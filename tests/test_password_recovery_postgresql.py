"""Guarded PostgreSQL password-recovery transaction and concurrency tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.application.email_registration import EncryptedOutboxPayload
from app.application.password_recovery import (
    ConfirmPasswordResetCommand, PasswordRecoveryPolicy, PasswordRecoveryService,
    RequestPasswordResetCommand, ResetTokenInvalidError,
)
from app.domain.authentication import AccountId, AccountStatus, EmailIdentityRecord, IdentityId, UserAccountRecord
from app.infrastructure.authentication.models import (
    AuthenticationEmailOutboxModel, PasswordResetChallengeModel,
    RefreshSessionModel, UserAccountModel,
)
from app.infrastructure.authentication.registration import (
    AesGcmOutboxCipher, EmailValidatorNormalizer, HmacPasswordResetMaterialProvider,
    LocalCompromisedPasswordChecker, SafeAuthenticationEventLogger,
)
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.database import create_database_engine, dispose_database_engine
from app.infrastructure.relational import RELATIONAL_METADATA
from tests.postgresql_support import upgrade_test_database


TEST_URL = os.environ.get("POS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(TEST_URL is None, reason="authorized PostgreSQL test URL not configured")
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class FastHasher:
    def hash(self, password):
        return "$argon2id$test$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, password_hash, password):
        return password_hash == self.hash(password)

    def needs_rehash(self, _password_hash):
        return False


@pytest.fixture(scope="module")
def engine():
    assert TEST_URL is not None
    upgrade_test_database(TEST_URL)
    value = create_database_engine(SecretStr(TEST_URL))
    try:
        yield value
    finally:
        dispose_database_engine(value)


@pytest.fixture(autouse=True)
def empty_tables(engine):
    names = ", ".join(f'"{name}"' for name in RELATIONAL_METADATA.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))


@pytest.fixture
def recovery(engine):
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    cipher = AesGcmOutboxCipher(b"e" * 32, key_id="email-test", key_version=1)
    service = PasswordRecoveryService(
        lambda request_id: SqlAlchemyAuthenticationUnitOfWork(sessions, request_id=request_id),
        FastHasher(), LocalCompromisedPasswordChecker(), EmailValidatorNormalizer(),
        HmacPasswordResetMaterialProvider(b"d" * 32), cipher,
        SafeAuthenticationEventLogger(),
        PasswordRecoveryPolicy("https://app.example.test/reset"), clock=lambda: NOW,
    )
    return service, cipher, sessions


def _create_account(sessions):
    account_id = AccountId(uuid4())
    with SqlAlchemyAuthenticationUnitOfWork(sessions, request_id="setup") as unit:
        unit.accounts.add(UserAccountRecord(
            id=account_id, status=AccountStatus.ACTIVE, display_name="Person",
            password_hash="old-hash", email_verified_at=NOW,
            onboarding_required=False, authentication_epoch=3,
            terms_version=None, terms_accepted_at=None,
            privacy_version=None, privacy_accepted_at=None,
            created_at=NOW, updated_at=NOW,
        ))
        unit.email_identities.add(EmailIdentityRecord(
            id=IdentityId(uuid4()), user_account_id=account_id,
            display_email="person@example.com",
            normalized_email="person@example.com", is_verified=True,
            is_primary=True, verified_at=NOW, created_at=NOW, updated_at=NOW,
        ))
        unit.commit()
    return account_id


def _latest_token(engine, cipher):
    with Session(engine) as session:
        outbox = session.scalars(
            select(AuthenticationEmailOutboxModel)
            .order_by(AuthenticationEmailOutboxModel.created_at.desc())
            .limit(1)
        ).one()
    message = cipher.decrypt_password_reset(
        EncryptedOutboxPayload(
            outbox.payload_ciphertext, outbox.encryption_key_id,
            outbox.encryption_key_version,
        ), context=outbox.deduplication_key.encode("ascii")
    )
    return message.token


def test_reset_confirmation_updates_epoch_consumes_token_and_revokes_sessions(engine, recovery):
    service, cipher, sessions = recovery
    account_id = _create_account(sessions)
    service.request(RequestPasswordResetCommand("person@example.com", "request"))
    token = _latest_token(engine, cipher)
    service.confirm(ConfirmPasswordResetCommand(token, "a strong replacement password", "confirm"))
    with Session(engine) as session:
        account = session.get(UserAccountModel, account_id)
        challenge = session.scalars(select(PasswordResetChallengeModel)).one()
        assert account is not None
        assert account.authentication_epoch == 4
        assert account.password_hash != "old-hash"
        assert challenge.consumed_at is not None
        assert session.scalars(select(RefreshSessionModel)).all() == []
    with pytest.raises(ResetTokenInvalidError):
        service.confirm(ConfirmPasswordResetCommand(token, "another strong replacement password", "replay"))


def test_latest_request_supersedes_previous_token(engine, recovery):
    service, cipher, sessions = recovery
    _create_account(sessions)
    service.request(RequestPasswordResetCommand("person@example.com", "first"))
    first = _latest_token(engine, cipher)
    service.request(RequestPasswordResetCommand("person@example.com", "second"))
    with pytest.raises(ResetTokenInvalidError):
        service.confirm(ConfirmPasswordResetCommand(first, "a strong replacement password", "old"))


def test_concurrent_confirmation_has_one_winner(engine, recovery):
    service, cipher, sessions = recovery
    _create_account(sessions)
    service.request(RequestPasswordResetCommand("person@example.com", "request"))
    token = _latest_token(engine, cipher)

    def confirm(index):
        try:
            service.confirm(ConfirmPasswordResetCommand(
                token, f"a strong replacement password {index}", f"confirm-{index}"
            ))
            return "success"
        except ResetTokenInvalidError:
            return "invalid"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(confirm, range(2)))
    assert sorted(outcomes) == ["invalid", "success"]
