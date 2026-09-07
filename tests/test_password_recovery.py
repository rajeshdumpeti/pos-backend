"""Focused password-recovery use-case and HTTP contract coverage."""

from datetime import UTC, datetime, timedelta
import traceback
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.application.email_registration import EncryptedOutboxPayload
from app.application.password_recovery import (
    ConfirmPasswordResetCommand,
    IssuedPasswordResetMaterial,
    PasswordRecoveryPolicy,
    PasswordRecoveryService,
    PasswordResetAcceptedResult,
    RequestPasswordResetCommand,
    ResetTokenExpiredError,
    ResetTokenInvalidError,
    ResetPasswordRejectedError,
    PasswordRecoveryInternalError,
)
from app.config import AppEnvironment, Settings
from app.domain.authentication import (
    AccountId, AccountStatus, EmailIdentityRecord, IdentityId,
    PasswordResetChallengeRecord, UserAccountRecord,
)
from app.domain.registration import NormalizedEmail
from app.main import create_app


NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class ContextUnit:
    def __init__(self) -> None:
        self.accounts = MagicMock()
        self.email_identities = MagicMock()
        self.password_reset_challenges = MagicMock()
        self.refresh_sessions = MagicMock()
        self.security_events = MagicMock()
        self.email_outbox = MagicMock()
        self.commit = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Material:
    def issue(self, _challenge_id, _request_id):
        return IssuedPasswordResetMaterial("t" * 43, "digest", "request-digest")

    def lookup_digest(self, token):
        if token != "t" * 43:
            raise ValueError("invalid")
        return "digest"


class Cipher:
    def encrypt_password_reset(self, _message, *, context):
        assert context.startswith(b"password-reset:")
        return EncryptedOutboxPayload(b"ciphertext", "test-key", 1)


class Normalizer:
    def normalize(self, email):
        if "@" not in email:
            from app.domain.registration import EmailPolicyError
            raise EmailPolicyError("invalid")
        return NormalizedEmail(display=email, comparison=email.casefold())


class Hasher:
    def hash(self, _password):
        return "argon2-hash"


class Checker:
    def is_compromised(self, _password):
        return False


class Logger:
    def __init__(self):
        self.events = []

    def emit(self, **event):
        self.events.append(event)


def _account():
    account_id = AccountId(uuid4())
    return UserAccountRecord(
        id=account_id, status=AccountStatus.ACTIVE, display_name="Person",
        password_hash="old", email_verified_at=NOW, onboarding_required=False,
        authentication_epoch=2, terms_version=None, terms_accepted_at=None,
        privacy_version=None, privacy_accepted_at=None, created_at=NOW,
        updated_at=NOW,
    )


def _identity(account_id):
    return EmailIdentityRecord(
        id=IdentityId(uuid4()), user_account_id=account_id,
        display_email="person@example.test", normalized_email="person@example.test",
        is_verified=True, is_primary=True, created_at=NOW, updated_at=NOW,
        verified_at=NOW,
    )


def _service(unit):
    return PasswordRecoveryService(
        lambda _request_id: unit, Hasher(), Checker(), Normalizer(), Material(),
        Cipher(), Logger(), PasswordRecoveryPolicy("https://app.example.test/reset"),
        clock=lambda: NOW,
    )


def test_eligible_request_supersedes_and_writes_challenge_and_outbox_once():
    unit = ContextUnit()
    account = _account()
    unit.email_identities.get_by_normalized_email_for_update.return_value = _identity(account.id)
    unit.accounts.get_for_update.return_value = account

    result = _service(unit).request(
        RequestPasswordResetCommand("Person@Example.Test", "request-id")
    )

    assert result.status == "instructions_if_eligible"
    unit.password_reset_challenges.consume_active_for_account.assert_called_once()
    unit.password_reset_challenges.add.assert_called_once()
    unit.email_outbox.add.assert_called_once()
    unit.commit.assert_called_once()
    record = unit.password_reset_challenges.add.call_args.args[0]
    assert record.token_digest == "digest"
    assert "t" * 43 not in repr(record)


@pytest.mark.parametrize("identity", [None, "unverified", "provider_only", "disabled"])
def test_ineligible_requests_have_same_result_and_no_persistence(identity):
    unit = ContextUnit()
    account = _account()
    resolved_identity = None if identity is None else _identity(account.id)
    if identity == "unverified":
        resolved_identity = SimpleNamespace(**{
            **resolved_identity.__dict__, "is_verified": False
        }) if hasattr(resolved_identity, "__dict__") else resolved_identity
        # Frozen slot record: use a direct replacement.
        original = _identity(account.id)
        resolved_identity = EmailIdentityRecord(
            id=original.id, user_account_id=original.user_account_id,
            display_email=original.display_email,
            normalized_email=original.normalized_email, is_verified=False,
            is_primary=True, created_at=NOW, updated_at=NOW,
        )
    if identity == "provider_only":
        account = UserAccountRecord(
            id=account.id, status=account.status, display_name=account.display_name,
            password_hash=None, email_verified_at=NOW, onboarding_required=False,
            authentication_epoch=0, terms_version=None, terms_accepted_at=None,
            privacy_version=None, privacy_accepted_at=None, created_at=NOW, updated_at=NOW,
        )
    if identity == "disabled":
        account = UserAccountRecord(
            id=account.id, status=AccountStatus.DISABLED, display_name=account.display_name,
            password_hash="old", email_verified_at=NOW, onboarding_required=False,
            authentication_epoch=0, terms_version=None, terms_accepted_at=None,
            privacy_version=None, privacy_accepted_at=None, created_at=NOW, updated_at=NOW,
        )
    unit.email_identities.get_by_normalized_email_for_update.return_value = resolved_identity
    unit.accounts.get_for_update.return_value = account

    assert _service(unit).request(
        RequestPasswordResetCommand("person@example.test", "request-id")
    ) == PasswordResetAcceptedResult()
    unit.password_reset_challenges.add.assert_not_called()
    unit.email_outbox.add.assert_not_called()
    unit.commit.assert_not_called()


def test_confirm_atomically_replaces_password_revokes_sessions_and_consumes_token():
    unit = ContextUnit()
    account = _account()
    challenge = PasswordResetChallengeRecord(
        id=uuid4(), user_account_id=account.id, token_digest="digest",
        request_security_digest="request-digest", issued_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )
    unit.password_reset_challenges.get_by_digest_for_update.return_value = challenge
    unit.accounts.get_for_update.return_value = account
    unit.accounts.replace_password_and_increment_epoch.return_value = True
    unit.password_reset_challenges.consume.return_value = True

    _service(unit).confirm(
        ConfirmPasswordResetCommand("t" * 43, "a strong replacement password", "request-id")
    )

    unit.accounts.replace_password_and_increment_epoch.assert_called_once()
    unit.refresh_sessions.revoke_all_for_account.assert_called_once_with(
        account.id, NOW, "password_reset"
    )
    unit.password_reset_challenges.consume.assert_called_once()
    unit.commit.assert_called_once()


def test_consumed_and_expired_tokens_are_rejected():
    account = _account()
    unit = ContextUnit()
    unit.password_reset_challenges.get_by_digest_for_update.return_value = PasswordResetChallengeRecord(
        id=uuid4(), user_account_id=account.id, token_digest="digest",
        request_security_digest="request-digest", issued_at=NOW - timedelta(hours=1),
        expires_at=NOW - timedelta(minutes=1),
    )
    with pytest.raises(ResetTokenExpiredError):
        _service(unit).confirm(ConfirmPasswordResetCommand("t" * 43, "a strong replacement password", "id"))
    unit.password_reset_challenges.get_by_digest_for_update.return_value = None
    with pytest.raises(ResetTokenInvalidError):
        _service(unit).confirm(ConfirmPasswordResetCommand("t" * 43, "a strong replacement password", "id"))


def test_project_owned_password_recovery_traceback_redacts_internal_secrets():
    class FailingCipher:
        def encrypt_password_reset(self, _message, *, context):
            raise ValueError("must_never_appear")

    service = PasswordRecoveryService(
        lambda _request_id: ContextUnit(), Hasher(), Checker(), Normalizer(), Material(),
        FailingCipher(), Logger(), PasswordRecoveryPolicy("https://app.example.test/reset"),
        clock=lambda: NOW,
    )
    with pytest.raises(PasswordRecoveryInternalError) as captured:
        service.request(RequestPasswordResetCommand("person@example.test", "request"))
    rendered = "".join(traceback.format_exception(captured.value))
    assert "must_never_appear" not in rendered


class StubRecovery:
    def __init__(self, error=None):
        self.error = error

    def request(self, command):
        self.command = command
        if self.error:
            raise self.error
        return PasswordResetAcceptedResult()

    def confirm(self, command):
        self.command = command
        if self.error:
            raise self.error


def test_password_reset_http_contract_and_cache_prevention():
    service = StubRecovery()
    client = TestClient(create_app(
        app_settings=Settings(app_env=AppEnvironment.TEST, cors_origins=("*",)),
        password_recovery_service=service,
    ))
    response = client.post(
        "/api/v1/auth/password-resets", json={"email": "person@example.test"},
        headers={"X-Request-ID": "reset-request"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "instructions_if_eligible"}
    assert response.headers["Cache-Control"] == "no-store"
    assert service.command.request_id == "reset-request"

    response = client.post(
        "/api/v1/auth/password-resets/confirm",
        json={"token": "t" * 43, "new_password": "a strong replacement password"},
    )
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["Cache-Control"] == "no-store"


def test_malformed_email_is_validation_error_but_unknown_email_is_generic_accepted():
    client = TestClient(create_app(
        app_settings=Settings(app_env=AppEnvironment.TEST, cors_origins=("*",)),
        password_recovery_service=StubRecovery(),
    ))
    malformed = client.post("/api/v1/auth/password-resets", json={"email": "x"})
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "request_validation_failed"
    unknown = client.post(
        "/api/v1/auth/password-resets", json={"email": "unknown@example.com"}
    )
    assert unknown.status_code == 202
    assert unknown.json() == {"status": "instructions_if_eligible"}


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ResetTokenInvalidError("safe"), 400, "reset_token_invalid"),
        (ResetTokenExpiredError("safe"), 410, "reset_token_expired"),
        (ResetPasswordRejectedError("safe"), 400, "password_rejected"),
    ],
)
def test_password_reset_errors_are_stable_and_secret_free(error, status, code):
    service = StubRecovery(error)
    client = TestClient(create_app(
        app_settings=Settings(app_env=AppEnvironment.TEST, cors_origins=("*",)),
        password_recovery_service=service,
    ))
    sentinel = "must_never_appear_in_password_reset_output"
    response = client.post(
        "/api/v1/auth/password-resets/confirm",
        json={"token": "t" * 43, "new_password": sentinel},
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert sentinel not in response.text


def test_unconfigured_password_recovery_is_safe_503_with_request_id(caplog):
    client = TestClient(create_app(
        app_settings=Settings(app_env=AppEnvironment.TEST, cors_origins=("*",))
    ))
    with caplog.at_level("WARNING"):
        response = client.post(
            "/api/v1/auth/password-resets",
            json={"email": "must_never_appear@example.test"},
            headers={"X-Request-ID": "password-recovery-unavailable"},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.json()["request_id"] == "password-recovery-unavailable"
    assert any(
        getattr(record, "operation", None) == "password_reset_request"
        and getattr(record, "request_id", None) == "password-recovery-unavailable"
        for record in caplog.records
    )
    assert "must_never_appear" not in caplog.text + response.text


def test_complete_local_database_settings_compose_recovery_service_and_return_202(
    monkeypatch,
):
    import base64

    key = lambda byte: base64.urlsafe_b64encode(bytes([byte]) * 32).decode().rstrip("=")
    settings = Settings(
        app_env=AppEnvironment.TEST,
        cors_origins=("*",),
        database_url="postgresql://synthetic:synthetic@127.0.0.1:5432/pos_test",
        auth_challenge_digest_key=key(120),
        auth_outbox_encryption_key=key(121),
        auth_password_reset_url="https://app.example.test/reset",
        auth_email_provider="local",
    )

    def accepted(self, _command):
        return PasswordResetAcceptedResult()

    monkeypatch.setattr(PasswordRecoveryService, "request", accepted)
    application = create_app(app_settings=settings)
    route = next(
        route
        for route in application.routes
        if getattr(route, "path", None) == "/api/v1/auth/password-resets"
    )
    require_service = route.endpoint.__closure__[0].cell_contents
    captured = dict(
        zip(require_service.__code__.co_freevars, require_service.__closure__ or ())
    )
    assert captured["password_recovery_service"].cell_contents is not None

    response = TestClient(application).post(
        "/api/v1/auth/password-resets",
        json={"email": "unknown@example.com"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "instructions_if_eligible"}
