"""Fast tests for authentication domain, contracts, metadata, and transactions."""

from datetime import UTC, datetime
import inspect
import logging
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.application.authentication_persistence import (
    AuthenticationAttemptStateRepository,
    AuthenticationEmailOutboxRepository,
    AuthenticationUnitOfWork,
    EmailIdentityRepository,
    EmailVerificationChallengeRepository,
    ExternalProviderIdentityRepository,
    PasswordResetChallengeRepository,
    RefreshRotationReceiptRepository,
    RefreshSessionRepository,
    SecurityEventRepository,
    UserAccountRepository,
)
from app.domain.authentication import (
    AccountId,
    AccountStatus,
    EmailIdentityRecord,
    InvalidAccountTransitionError,
    UserAccountRecord,
    require_account_transition,
)
from app.infrastructure.authentication.models import UserAccountModel
from app.infrastructure.authentication.repositories import SqlAlchemyUserAccountRepository
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.relational import RELATIONAL_METADATA


NOW = datetime(2026, 8, 30, tzinfo=UTC)


class RecordingSession:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        if self.fail_commit:
            raise SQLAlchemyError("password=do-not-log token=do-not-log")

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1


def _account(status: AccountStatus = AccountStatus.PENDING_VERIFICATION) -> UserAccountRecord:
    return UserAccountRecord(
        id=AccountId(uuid4()), status=status, display_name="Example User",
        password_hash="$argon2id$never-log", email_verified_at=None,
        onboarding_required=True, authentication_epoch=0, terms_version="v1",
        terms_accepted_at=NOW, privacy_version="v1", privacy_accepted_at=NOW,
        created_at=NOW, updated_at=NOW,
    )


def test_account_lifecycle_allows_verification_and_keeps_deleted_terminal():
    assert require_account_transition(AccountStatus.PENDING_VERIFICATION, AccountStatus.ACTIVE) is AccountStatus.ACTIVE
    with pytest.raises(InvalidAccountTransitionError):
        require_account_transition(AccountStatus.DELETED, AccountStatus.ACTIVE)
    with pytest.raises(InvalidAccountTransitionError):
        require_account_transition(AccountStatus.PENDING_VERIFICATION, AccountStatus.DISABLED)


def test_sensitive_record_fields_are_absent_from_repr():
    account = _account()
    identity = EmailIdentityRecord(
        id=uuid4(), user_account_id=account.id, display_email="private@example.test",
        normalized_email="private@example.test", is_verified=False, is_primary=True,
        created_at=NOW, updated_at=NOW,
    )
    assert "$argon2id$never-log" not in repr(account)
    assert "private@example.test" not in repr(identity)


def test_domain_and_application_contracts_are_framework_independent():
    domain_source = inspect.getsource(inspect.getmodule(AccountStatus))
    contract_source = inspect.getsource(inspect.getmodule(AuthenticationUnitOfWork))
    prohibited = ("fastapi", "sqlalchemy", "alembic", "psycopg", "pydantic")
    assert not any(name in domain_source.lower() for name in prohibited)
    assert not any(name in contract_source.lower() for name in prohibited)


def test_metadata_has_expected_constraints_indexes_and_no_raw_secret_columns():
    assert len(RELATIONAL_METADATA.tables) == 10
    assert UserAccountModel.__table__.c.id.type.__class__.__name__ == "UUID"
    forbidden = {"password", "verification_code", "reset_token", "refresh_token", "provider_token"}
    columns = {column.name for table in RELATIONAL_METADATA.tables.values() for column in table.columns}
    assert forbidden.isdisjoint(columns)
    assert "password_hash" in columns
    assert "payload_ciphertext" in columns
    assert "response_ciphertext" in columns
    assert all(constraint.name for table in RELATIONAL_METADATA.tables.values() for constraint in table.constraints)
    assert all(index.name for table in RELATIONAL_METADATA.tables.values() for index in table.indexes)
    receipt_constraints = {
        constraint.name
        for constraint in RELATIONAL_METADATA.tables[
            "refresh_rotation_retry_receipts"
        ].constraints
    }
    assert "uq_refresh_rotation_retry_receipts__refresh_request_id" in receipt_constraints


def test_repositories_never_commit_or_create_sessions():
    source = inspect.getsource(inspect.getmodule(SqlAlchemyUserAccountRepository))
    assert ".commit(" not in source
    assert "sessionmaker(" not in source
    assert "create_engine(" not in source


def test_authentication_uow_commits_once_and_closes():
    session = RecordingSession()
    unit = SqlAlchemyAuthenticationUnitOfWork(lambda: session)  # type: ignore[arg-type]
    with unit as active:
        assert isinstance(active, AuthenticationUnitOfWork)
        active.commit()
    assert (session.commit_count, session.rollback_count, session.close_count) == (1, 0, 1)


def test_authentication_uow_exposes_every_focused_repository_contract():
    session = RecordingSession()
    with SqlAlchemyAuthenticationUnitOfWork(lambda: session) as unit:  # type: ignore[arg-type]
        repositories = (
            (unit.accounts, UserAccountRepository),
            (unit.email_identities, EmailIdentityRepository),
            (unit.provider_identities, ExternalProviderIdentityRepository),
            (unit.verification_challenges, EmailVerificationChallengeRepository),
            (unit.password_reset_challenges, PasswordResetChallengeRepository),
            (unit.refresh_sessions, RefreshSessionRepository),
            (unit.refresh_receipts, RefreshRotationReceiptRepository),
            (unit.attempt_states, AuthenticationAttemptStateRepository),
            (unit.security_events, SecurityEventRepository),
            (unit.email_outbox, AuthenticationEmailOutboxRepository),
        )
        assert all(isinstance(repository, contract) for repository, contract in repositories)


def test_authentication_uow_rolls_back_and_closes_on_failure():
    session = RecordingSession()
    with pytest.raises(ValueError):
        with SqlAlchemyAuthenticationUnitOfWork(lambda: session):  # type: ignore[arg-type]
            raise ValueError("use-case failed")
    assert (session.commit_count, session.rollback_count, session.close_count) == (0, 1, 1)


def test_commit_failure_is_chained_logged_and_secret_safe(caplog: pytest.LogCaptureFixture):
    session = RecordingSession(fail_commit=True)
    caplog.set_level(logging.ERROR, logger="pos.authentication.persistence")
    with pytest.raises(Exception) as captured:
        with SqlAlchemyAuthenticationUnitOfWork(lambda: session, request_id="request-safe") as unit:  # type: ignore[arg-type]
            unit.commit()
    combined = str(captured.value) + caplog.text
    assert "do-not-log" not in combined
    assert "request-safe" not in caplog.text  # structured LogRecord field, not message text
    assert captured.value.__cause__ is not None
    assert (session.rollback_count, session.close_count) == (1, 1)
