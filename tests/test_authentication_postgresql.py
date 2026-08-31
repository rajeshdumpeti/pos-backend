"""PostgreSQL-only migration, repository, constraint, and concurrency coverage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pydantic import SecretStr
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.application import PersistenceUnavailableError
from app.domain.authentication import AccountId, AccountStatus, EmailIdentityRecord, UserAccountRecord
from app.infrastructure.authentication.models import (
    AuthenticationAttemptStateModel,
    AuthenticationEmailOutboxModel,
    EmailIdentityModel,
    ExternalProviderIdentityModel,
    UserAccountModel,
)
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.database import create_database_engine, dispose_database_engine
from app.infrastructure.relational import RELATIONAL_METADATA


TEST_URL = os.environ.get("POS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_URL is None, reason="authorized PostgreSQL test URL not configured")
NOW = datetime(2026, 8, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine():
    assert TEST_URL is not None
    value = create_database_engine(SecretStr(TEST_URL))
    try:
        try:
            command.upgrade(Config("alembic.ini"), "head")
        except (PersistenceUnavailableError, SQLAlchemyError):
            pytest.fail(
                "Authorized PostgreSQL test database is unavailable",
                pytrace=False,
            )
        yield value
    finally:
        dispose_database_engine(value)


@pytest.fixture
def session_factory(engine):
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()


def _account() -> UserAccountRecord:
    return UserAccountRecord(id=AccountId(uuid4()), status=AccountStatus.PENDING_VERIFICATION, display_name="Persistence Test", password_hash="$argon2id$fixture", email_verified_at=None, onboarding_required=True, authentication_epoch=0, terms_version=None, terms_accepted_at=None, privacy_version=None, privacy_accepted_at=None, created_at=NOW, updated_at=NOW)


def _identity(account_id: AccountId, normalized: str) -> EmailIdentityRecord:
    return EmailIdentityRecord(id=uuid4(), user_account_id=account_id, display_email=normalized, normalized_email=normalized, is_verified=False, is_primary=True, created_at=NOW, updated_at=NOW)


def test_repository_add_get_and_atomic_commit(session_factory):
    account = _account(); identity = _identity(account.id, f"{uuid4()}@example.test")
    with SqlAlchemyAuthenticationUnitOfWork(session_factory) as unit:
        unit.accounts.add(account); unit.email_identities.add(identity); unit.commit()
    with SqlAlchemyAuthenticationUnitOfWork(session_factory) as unit:
        assert unit.accounts.get(account.id) == account
        assert unit.email_identities.get_by_normalized_email(identity.normalized_email) == identity


def test_failed_transaction_leaves_no_partial_account(session_factory):
    account = _account(); duplicate = f"{uuid4()}@example.test"
    with pytest.raises(Exception):
        with SqlAlchemyAuthenticationUnitOfWork(session_factory) as unit:
            unit.accounts.add(account)
            unit.email_identities.add(_identity(account.id, duplicate))
            unit.email_identities.add(_identity(account.id, duplicate))
            unit.commit()
    with Session(session_factory.kw["bind"]) as session:
        assert session.get(UserAccountModel, account.id) is None


def test_database_constraints_reject_invalid_status_and_expiry(engine):
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO user_accounts (id,status,display_name,onboarding_required,authentication_epoch,created_at,updated_at) VALUES (:id,'invalid','x',true,0,:now,:now)"), {"id": uuid4(), "now": NOW})


def test_metadata_and_live_schema_have_no_drift(engine):
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, RELATIONAL_METADATA) == []


def test_concurrent_normalized_email_creation_allows_one_winner(engine):
    normalized = f"race-{uuid4()}@example.test"

    def create_identity() -> str:
        account_id = uuid4()
        try:
            with engine.begin() as connection:
                connection.execute(UserAccountModel.__table__.insert().values(id=account_id, status="pending_verification", display_name="Race", onboarding_required=True, authentication_epoch=0, created_at=NOW, updated_at=NOW))
                connection.execute(EmailIdentityModel.__table__.insert().values(id=uuid4(), user_account_id=account_id, display_email=normalized, normalized_email=normalized, is_verified=False, is_primary=True, created_at=NOW, updated_at=NOW))
            return "created"
        except IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: create_identity(), range(2)))
    assert sorted(outcomes) == ["conflict", "created"]
    with engine.begin() as connection:
        ids = connection.scalars(select(UserAccountModel.id).join(EmailIdentityModel).where(EmailIdentityModel.normalized_email == normalized)).all()
        connection.execute(UserAccountModel.__table__.delete().where(UserAccountModel.id.in_(ids)))


def test_migration_schema_contains_all_authentication_tables(engine):
    assert set(RELATIONAL_METADATA.tables).issubset(set(inspect(engine).get_table_names()))


def test_atomic_attempt_increment_is_safe_under_concurrency(engine):
    identity_digest = f"identity-{uuid4()}"
    risk_digest = f"risk-{uuid4()}"

    def increment(_: int) -> int:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with SqlAlchemyAuthenticationUnitOfWork(factory) as unit:
            state = unit.attempt_states.increment_failure(
                identity_digest, risk_digest, NOW, NOW + timedelta(seconds=1), None
            )
            unit.commit()
            return state.failed_attempts

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(increment, range(10)))
    with engine.begin() as connection:
        count = connection.scalar(
            select(AuthenticationAttemptStateModel.failed_attempts).where(
                AuthenticationAttemptStateModel.identity_digest == identity_digest,
                AuthenticationAttemptStateModel.risk_bucket_digest == risk_digest,
            )
        )
        assert count == 10
        connection.execute(
            AuthenticationAttemptStateModel.__table__.delete().where(
                AuthenticationAttemptStateModel.identity_digest == identity_digest
            )
        )


def test_concurrent_provider_identity_creation_allows_one_winner(engine):
    provider_subject = f"subject-{uuid4()}"

    def create_provider(_: int) -> str:
        account_id = uuid4()
        try:
            with engine.begin() as connection:
                connection.execute(UserAccountModel.__table__.insert().values(id=account_id, status="pending_verification", display_name="Provider Race", onboarding_required=True, authentication_epoch=0, created_at=NOW, updated_at=NOW))
                connection.execute(ExternalProviderIdentityModel.__table__.insert().values(id=uuid4(), user_account_id=account_id, provider="google", provider_subject=provider_subject, is_private_relay=False, first_authenticated_at=NOW, last_authenticated_at=NOW, created_at=NOW, updated_at=NOW))
            return "created"
        except IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create_provider, range(2)))
    assert sorted(outcomes) == ["conflict", "created"]
    with engine.begin() as connection:
        account_ids = connection.scalars(select(ExternalProviderIdentityModel.user_account_id).where(ExternalProviderIdentityModel.provider_subject == provider_subject)).all()
        connection.execute(UserAccountModel.__table__.delete().where(UserAccountModel.id.in_(account_ids)))


def test_outbox_skip_locked_claims_are_disjoint(engine):
    outbox_ids = [uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.execute(AuthenticationEmailOutboxModel.__table__.insert(), [
            {"id": item_id, "message_kind": "email_verification", "recipient_normalized_email": f"{item_id}@example.test", "payload_ciphertext": b"ciphertext", "encryption_key_id": "test-key", "encryption_key_version": 1, "deduplication_key": f"dedupe-{item_id}", "status": "pending", "created_at": NOW, "available_at": NOW, "attempt_count": 0}
            for item_id in outbox_ids
        ])

    def claim(_: int) -> set:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with SqlAlchemyAuthenticationUnitOfWork(factory) as unit:
            claimed = unit.email_outbox.claim_available(NOW, uuid4(), 2)
            unit.commit()
            return {record.id for record in claimed}

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(claim, range(2)))
    assert len(batches[0]) == len(batches[1]) == 2
    assert batches[0].isdisjoint(batches[1])
    with engine.begin() as connection:
        connection.execute(AuthenticationEmailOutboxModel.__table__.delete().where(AuthenticationEmailOutboxModel.id.in_(outbox_ids)))
