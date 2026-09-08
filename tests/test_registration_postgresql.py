"""PostgreSQL registration, verification, rollback, and concurrency tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import os

from pydantic import SecretStr
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.application.email_registration import (
    AccountAlreadyExistsError,
    AuthenticationCryptographyError,
    ChangeUnverifiedEmailCommand,
    ConfirmEmailCommand,
    EmailRegistrationService,
    EncryptedOutboxPayload,
    PasswordHasher,
    RegisterAccountCommand,
    RegistrationPolicy,
    RegistrationInputRejectedError,
    LegalAcceptanceOutdatedError,
    ResendCooldownActiveError,
    ResendVerificationCommand,
    VerificationAttemptsExceededError,
    VerificationCodeExpiredError,
    VerificationCodeInvalidError,
    VerificationFlowExpiredError,
    VerificationFlowInvalidError,
)
from app.infrastructure.authentication.models import (
    AuthenticationEmailOutboxModel,
    EmailIdentityModel,
    EmailVerificationChallengeModel,
    RefreshSessionModel,
    UserAccountModel,
)
from app.infrastructure.authentication.registration import (
    AesGcmOutboxCipher,
    EmailValidatorNormalizer,
    HmacVerificationMaterialProvider,
    LocalCompromisedPasswordChecker,
    SafeAuthenticationEventLogger,
)
from app.infrastructure.authentication.unit_of_work import (
    SqlAlchemyAuthenticationUnitOfWork,
)
from app.application.session_lifecycle import (
    EmailNotVerifiedError,
    EmailSessionService,
    SessionPolicy,
    SignInCommand,
)
from app.infrastructure.authentication.session_security import (
    AesGcmRefreshReceiptCipher,
    JwtAccessTokenCodec,
    OpaqueRefreshTokenGenerator,
)
from app.infrastructure.database import create_database_engine, dispose_database_engine
from app.infrastructure.relational import RELATIONAL_METADATA
from tests.postgresql_support import upgrade_test_database


TEST_URL = os.environ.get("POS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class FastTestPasswordHasher:
    def hash(self, password: str) -> str:
        return "$argon2id$test$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == self.hash(password)

    def needs_rehash(self, password_hash: str) -> bool:
        return False


class FailingCipher:
    def encrypt(self, message, *, context):
        raise AuthenticationCryptographyError("Authentication email encryption failed")


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
def empty_authentication_tables(engine):
    names = ", ".join(f'"{name}"' for name in RELATIONAL_METADATA.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))


@pytest.fixture
def clock():
    return MutableClock()


@pytest.fixture
def cipher():
    return AesGcmOutboxCipher(b"e" * 32, key_id="email-test", key_version=1)


@pytest.fixture
def verification_material():
    return HmacVerificationMaterialProvider(
        b"d" * 32, key_id="digest-test", key_version=1
    )


@pytest.fixture
def service(engine, clock, cipher, verification_material):
    return _service(engine, clock, cipher, verification_material)


def _service(engine, clock, cipher, verification_material=None) -> EmailRegistrationService:
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return EmailRegistrationService(
        lambda request_id: SqlAlchemyAuthenticationUnitOfWork(
            sessions, request_id=request_id
        ),
        FastTestPasswordHasher(),
        LocalCompromisedPasswordChecker(),
        EmailValidatorNormalizer(),
        verification_material
        or HmacVerificationMaterialProvider(
            b"d" * 32, key_id="digest-test", key_version=1
        ),
        cipher,
        SafeAuthenticationEventLogger(),
        RegistrationPolicy(
            terms_version="terms-test-v1",
            privacy_version="privacy-test-v1",
        ),
        clock=clock,
    )


def _register(service, email="person@example.com", password="a private passphrase 9472"):
    return service.register(
        RegisterAccountCommand(
            display_name="POS Person",
            email=email,
            password=password,
            terms_accepted=True,
            privacy_accepted=True,
            terms_version="terms-test-v1",
            privacy_version="privacy-test-v1",
            request_id="registration-test",
        )
    )


def _message_for_flow(engine, cipher, verification_material, flow_token):
    lookup = verification_material.flow_lookup(flow_token)
    with Session(engine) as session:
        challenge = session.scalars(
            select(EmailVerificationChallengeModel).where(
                EmailVerificationChallengeModel.flow_token_digest == lookup.digest
            )
        ).one()
        outbox = session.scalars(
            select(AuthenticationEmailOutboxModel).where(
                AuthenticationEmailOutboxModel.deduplication_key
                == f"email-verification:{challenge.id}"
            )
        ).one()
    message = cipher.decrypt(
        EncryptedOutboxPayload(
            ciphertext=outbox.payload_ciphertext,
            key_id=outbox.encryption_key_id,
            key_version=outbox.encryption_key_version,
        ),
        context=outbox.deduplication_key.encode("ascii"),
    )
    return outbox, message


def test_registration_persists_hash_digest_and_encrypted_outbox_atomically(
    engine, service, cipher, verification_material
):
    password = "a private passphrase 9472"
    result = _register(service, password=password)
    outbox, message = _message_for_flow(
        engine, cipher, verification_material, result.flow_token
    )

    with Session(engine) as session:
        account = session.scalars(select(UserAccountModel)).one()
        challenge = session.scalars(select(EmailVerificationChallengeModel)).one()
    assert result.status == "registration_pending"
    assert account.password_hash.startswith("$argon2id$test$")
    assert password not in account.password_hash
    assert message.code not in challenge.code_digest
    assert message.code.encode() not in outbox.payload_ciphertext
    assert result.flow_token not in challenge.flow_token_digest


def test_registration_requires_current_legal_acceptance(service):
    base = {
        "display_name": "POS Person",
        "email": "legal@example.com",
        "password": "a private passphrase 9472",
        "privacy_accepted": True,
        "terms_version": "terms-test-v1",
        "privacy_version": "privacy-test-v1",
        "request_id": "legal-test",
    }
    with pytest.raises(RegistrationInputRejectedError):
        service.register(RegisterAccountCommand(terms_accepted=False, **base))
    with pytest.raises(LegalAcceptanceOutdatedError):
        service.register(
            RegisterAccountCommand(
                terms_accepted=True,
                terms_version="stale-terms",
                **{key: value for key, value in base.items() if key != "terms_version"},
            )
        )


def _session_service(engine, registration, clock):
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    policy = SessionPolicy()
    hasher = FastTestPasswordHasher()
    return EmailSessionService(
        lambda request_id: SqlAlchemyAuthenticationUnitOfWork(
            sessions, request_id=request_id
        ),
        hasher,
        EmailValidatorNormalizer(),
        JwtAccessTokenCodec("k" * 64, policy),
        OpaqueRefreshTokenGenerator(),
        AesGcmRefreshReceiptCipher(
            b"r" * 32, key_id="receipt-test", key_version=1
        ),
        hasher.hash("constant dummy password"),
        policy,
        verification_continuation=registration,
        clock=clock,
    )


def _unverified_sign_in(sessions, email="person@example.com"):
    with pytest.raises(EmailNotVerifiedError) as caught:
        sessions.sign_in(
            SignInCommand(email, "a private passphrase 9472", "unverified-sign-in")
        )
    return caught.value.continuation


def test_unverified_sign_in_continuation_confirms_with_existing_code(
    engine, service, cipher, verification_material, clock
):
    pending = _register(service)
    _, message = _message_for_flow(
        engine, cipher, verification_material, pending.flow_token
    )
    continuation = _unverified_sign_in(
        _session_service(engine, service, clock)
    )

    assert service.confirm(
        ConfirmEmailCommand(
            continuation.flow_token,
            message.code,
            "continuation-confirm",
        )
    ).status == "email_verified"
    with Session(engine) as session:
        assert session.scalars(select(RefreshSessionModel)).all() == []
        assert len(session.scalars(select(AuthenticationEmailOutboxModel)).all()) == 1


def test_unverified_sign_in_continuation_supports_resend_and_change_email(
    engine, service, clock
):
    _register(service)
    sessions = _session_service(engine, service, clock)
    resend_continuation = _unverified_sign_in(sessions)
    clock.now += timedelta(seconds=61)
    resent = service.resend(
        ResendVerificationCommand(
            resend_continuation.flow_token, "continuation-resend"
        )
    )
    changed = service.change_email(
        ChangeUnverifiedEmailCommand(
            resent.flow_token,
            "replacement@example.com",
            "continuation-change",
        )
    )
    assert resent.flow_token != resend_continuation.flow_token
    assert changed.masked_email == "r********@example.com"


def test_concurrent_unverified_sign_in_leaves_one_current_flow_and_one_email(
    engine, service, verification_material, clock
):
    _register(service)
    sessions = _session_service(engine, service, clock)

    def attempt(index):
        with pytest.raises(EmailNotVerifiedError) as caught:
            sessions.sign_in(
                SignInCommand(
                    "person@example.com",
                    "a private passphrase 9472",
                    f"concurrent-sign-in-{index}",
                )
            )
        return caught.value.continuation.flow_token

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(attempt, range(2)))
    with Session(engine) as session:
        current = session.scalars(
            select(EmailVerificationChallengeModel).where(
                EmailVerificationChallengeModel.consumed_at.is_(None),
                EmailVerificationChallengeModel.superseded_by_id.is_(None),
            )
        ).one()
        assert len(session.scalars(select(AuthenticationEmailOutboxModel)).all()) == 1
        assert len(session.scalars(select(RefreshSessionModel)).all()) == 0
    matching = [
        token
        for token in tokens
        if verification_material.flow_lookup(token).digest
        == current.flow_token_digest
    ]
    assert len(matching) == 1


def test_unverified_continuation_outbox_failure_rolls_back(
    engine, service, clock
):
    _register(service)
    with engine.begin() as connection:
        connection.execute(EmailVerificationChallengeModel.__table__.delete())
        connection.execute(AuthenticationEmailOutboxModel.__table__.delete())
    broken_registration = _service(engine, clock, FailingCipher())
    sessions = _session_service(engine, broken_registration, clock)

    with pytest.raises(AuthenticationCryptographyError):
        sessions.sign_in(
            SignInCommand(
                "person@example.com",
                "a private passphrase 9472",
                "failed-continuation",
            )
        )
    with Session(engine) as session:
        assert session.scalars(select(EmailVerificationChallengeModel)).all() == []
        assert session.scalars(select(AuthenticationEmailOutboxModel)).all() == []
        assert session.scalars(select(RefreshSessionModel)).all() == []


def test_pending_and_verified_duplicates_have_equivalent_initial_public_results(
    engine, service, cipher, verification_material
):
    first = _register(service)
    pending_duplicate = _register(service, password="another private phrase 5521")
    _, message = _message_for_flow(
        engine, cipher, verification_material, pending_duplicate.flow_token
    )
    verified = service.confirm(
        ConfirmEmailCommand(
            flow_token=pending_duplicate.flow_token,
            code=message.code,
            request_id="confirm-pending",
        )
    )
    verified_duplicate = _register(service, password="third private phrase 6632")

    assert first.status == pending_duplicate.status == verified_duplicate.status
    assert first.masked_email == pending_duplicate.masked_email == verified_duplicate.masked_email
    assert verified.status == "email_verified"
    _, existing_message = _message_for_flow(
        engine, cipher, verification_material, verified_duplicate.flow_token
    )
    with pytest.raises(AccountAlreadyExistsError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=verified_duplicate.flow_token,
                code=existing_message.code,
                request_id="confirm-existing",
            )
        )
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(UserAccountModel)) == 1


def test_invalid_code_attempts_are_atomic_and_exhaust_at_five(engine, service):
    result = _register(service)
    for _ in range(4):
        with pytest.raises(VerificationCodeInvalidError):
            service.confirm(
                ConfirmEmailCommand(
                    flow_token=result.flow_token,
                    code="999999",
                    request_id="invalid-code",
                )
            )
    with pytest.raises(VerificationAttemptsExceededError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=result.flow_token,
                code="999999",
                request_id="fifth-invalid-code",
            )
        )
    with Session(engine) as session:
        challenge = session.scalars(select(EmailVerificationChallengeModel)).one()
    assert challenge.attempts == 5


def test_resend_enforces_cooldown_supersedes_old_flow_and_verifies(
    engine, service, clock, cipher, verification_material
):
    original = _register(service)
    with pytest.raises(ResendCooldownActiveError):
        service.resend(
            ResendVerificationCommand(
                flow_token=original.flow_token, request_id="resend-too-soon"
            )
        )
    clock.now += timedelta(seconds=61)
    replacement = service.resend(
        ResendVerificationCommand(
            flow_token=original.flow_token, request_id="resend-accepted"
        )
    )
    with pytest.raises(VerificationFlowInvalidError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=original.flow_token,
                code="000000",
                request_id="old-flow",
            )
        )
    _, message = _message_for_flow(
        engine, cipher, verification_material, replacement.flow_token
    )
    assert service.confirm(
        ConfirmEmailCommand(
            flow_token=replacement.flow_token,
            code=message.code,
            request_id="new-flow",
        )
    ).status == "email_verified"


def test_change_email_invalidates_old_flow_and_activates_only_new_identity(
    engine, service, cipher, verification_material
):
    original = _register(service, "old@example.com")
    changed = service.change_email(
        ChangeUnverifiedEmailCommand(
            flow_token=original.flow_token,
            new_email="New.Address+tag@BÜCHER.de",
            request_id="change-email",
        )
    )
    with pytest.raises(VerificationFlowInvalidError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=original.flow_token,
                code="000000",
                request_id="old-email-flow",
            )
        )
    _, message = _message_for_flow(
        engine, cipher, verification_material, changed.flow_token
    )
    service.confirm(
        ConfirmEmailCommand(
            flow_token=changed.flow_token,
            code=message.code,
            request_id="confirm-new-email",
        )
    )
    with Session(engine) as session:
        identity = session.scalars(select(EmailIdentityModel)).one()
    assert identity.normalized_email == "new.address+tag@xn--bcher-kva.de"
    assert identity.is_verified is True


def test_change_email_collision_proves_ownership_without_altering_either_account(
    engine, service, cipher, verification_material
):
    first = _register(service, "first@example.com")
    _register(service, "second@example.com")
    collision = service.change_email(
        ChangeUnverifiedEmailCommand(
            flow_token=first.flow_token,
            new_email="SECOND@example.com",
            request_id="collision",
        )
    )
    _, message = _message_for_flow(
        engine, cipher, verification_material, collision.flow_token
    )
    with pytest.raises(AccountAlreadyExistsError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=collision.flow_token,
                code=message.code,
                request_id="collision-proof",
            )
        )
    with engine.connect() as connection:
        identities = connection.scalars(
            select(EmailIdentityModel.normalized_email).order_by(
                EmailIdentityModel.normalized_email
            )
        ).all()
    assert identities == ["first@example.com", "second@example.com"]


def test_encryption_failure_rolls_back_account_challenge_and_outbox(engine, clock):
    failing = _service(engine, clock, FailingCipher())
    with pytest.raises(AuthenticationCryptographyError):
        _register(failing)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(UserAccountModel)) == 0
        assert connection.scalar(
            select(func.count()).select_from(EmailVerificationChallengeModel)
        ) == 0
        assert connection.scalar(
            select(func.count()).select_from(AuthenticationEmailOutboxModel)
        ) == 0


def test_concurrent_duplicate_registration_creates_one_account_and_one_current_challenge(
    engine, service
):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _register(service), range(2)))
    assert [result.status for result in results] == [
        "registration_pending",
        "registration_pending",
    ]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(UserAccountModel)) == 1
        current = connection.scalar(
            select(func.count())
            .select_from(EmailVerificationChallengeModel)
            .where(
                EmailVerificationChallengeModel.consumed_at.is_(None),
                EmailVerificationChallengeModel.superseded_by_id.is_(None),
            )
        )
    assert current == 1


def test_code_and_flow_expiration_are_distinct(service, clock):
    code_expiring = _register(service, "code-expiry@example.com")
    clock.now += timedelta(seconds=601)
    with pytest.raises(VerificationCodeExpiredError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=code_expiring.flow_token,
                code="000000",
                request_id="expired-code",
            )
        )

    flow_expiring = _register(service, "flow-expiry@example.com")
    clock.now += timedelta(seconds=1801)
    with pytest.raises(VerificationFlowExpiredError):
        service.confirm(
            ConfirmEmailCommand(
                flow_token=flow_expiring.flow_token,
                code="000000",
                request_id="expired-flow",
            )
        )


def test_consumed_challenge_is_single_use(
    engine, service, cipher, verification_material
):
    pending = _register(service, "single-use@example.com")
    _, message = _message_for_flow(
        engine, cipher, verification_material, pending.flow_token
    )
    command = ConfirmEmailCommand(
        flow_token=pending.flow_token,
        code=message.code,
        request_id="single-use",
    )
    assert service.confirm(command).status == "email_verified"
    with pytest.raises(VerificationFlowInvalidError):
        service.confirm(command)


def test_concurrent_confirmation_activates_once(
    engine, service, cipher, verification_material
):
    pending = _register(service, "concurrent-confirm@example.com")
    _, message = _message_for_flow(
        engine, cipher, verification_material, pending.flow_token
    )

    def confirm_once(index):
        try:
            return service.confirm(
                ConfirmEmailCommand(
                    flow_token=pending.flow_token,
                    code=message.code,
                    request_id=f"concurrent-confirm-{index}",
                )
            ).status
        except VerificationFlowInvalidError:
            return "flow_invalid"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(confirm_once, range(2)))
    assert outcomes == ["email_verified", "flow_invalid"]
    with Session(engine) as session:
        account = session.scalars(select(UserAccountModel)).one()
    assert account.status == "active"


def test_concurrent_resend_leaves_one_current_challenge(engine, service, clock):
    pending = _register(service, "concurrent-resend@example.com")
    clock.now += timedelta(seconds=61)

    def resend_once(index):
        try:
            return service.resend(
                ResendVerificationCommand(
                    flow_token=pending.flow_token,
                    request_id=f"concurrent-resend-{index}",
                )
            ).status
        except VerificationFlowInvalidError:
            return "flow_invalid"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(resend_once, range(2)))
    assert outcomes == ["flow_invalid", "registration_pending"]
    with Session(engine) as session:
        current = session.scalar(
            select(func.count())
            .select_from(EmailVerificationChallengeModel)
            .where(
                EmailVerificationChallengeModel.consumed_at.is_(None),
                EmailVerificationChallengeModel.superseded_by_id.is_(None),
            )
        )
    assert current == 1


def test_concurrent_change_email_applies_once(engine, service):
    pending = _register(service, "concurrent-change@example.com")

    def change_once(index):
        try:
            result = service.change_email(
                ChangeUnverifiedEmailCommand(
                    flow_token=pending.flow_token,
                    new_email=f"replacement-{index}@example.com",
                    request_id=f"concurrent-change-{index}",
                )
            )
            return result.masked_email
        except VerificationFlowInvalidError:
            return "flow_invalid"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(change_once, range(2)))
    assert outcomes.count("flow_invalid") == 1
    with Session(engine) as session:
        identities = session.scalars(select(EmailIdentityModel)).all()
        current = session.scalar(
            select(func.count())
            .select_from(EmailVerificationChallengeModel)
            .where(
                EmailVerificationChallengeModel.consumed_at.is_(None),
                EmailVerificationChallengeModel.superseded_by_id.is_(None),
            )
        )
    assert len(identities) == 1
    assert identities[0].normalized_email.startswith("replacement-")
    assert current == 1
