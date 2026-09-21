"""Network-free email-provider, rendering, and configuration tests."""

from datetime import UTC, datetime, timedelta
import logging
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
from pydantic import SecretStr
import pytest

from app.application.email_delivery import (
    AuthenticationEmailDispatcher, DeliveryReceipt, EmailDeliveryError, OutboundEmail,
)
from app.application.email_registration import VerificationEmailMessage
from app.application.password_recovery import PasswordResetEmailMessage
from app.config import ConfigurationError, Settings, load_settings
from app.domain.authentication import (
    AuthenticationEmailOutboxRecord,
    ChallengePurpose,
    OutboxMessageKind,
    OutboxStatus,
)
from app.infrastructure.authentication.email_delivery import (
    AuthenticationEmailTemplateRenderer, ResendEmailProvider,
)
from app.infrastructure.authentication.registration import AesGcmOutboxCipher
from app.infrastructure.authentication import local_email


NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if self.error:
            raise self.error
        return self.response


def _message():
    return OutboundEmail(
        "person@example.test", "POS <auth@example.test>", "Subject",
        "Plain body", "<p>Body</p>", "outbox-id",
    )


def _encrypted_record(
    kind: OutboxMessageKind,
    expires_at: datetime,
    *,
    reset_url: str = "https://app.example.test/reset",
    token: str = "t" * 43,
) -> AuthenticationEmailOutboxRecord:
    cipher = AesGcmOutboxCipher(b"e" * 32, key_id="test", key_version=1)
    record_id = uuid4()
    dedupe = f"{kind.value}:{record_id}"
    if kind is OutboxMessageKind.PASSWORD_RESET:
        encrypted = cipher.encrypt_password_reset(
            PasswordResetEmailMessage(token, reset_url, expires_at),
            context=dedupe.encode("ascii"),
        )
    else:
        encrypted = cipher.encrypt(
            VerificationEmailMessage(
                "012345", expires_at, ChallengePurpose.REGISTRATION
            ),
            context=dedupe.encode("ascii"),
        )
    return AuthenticationEmailOutboxRecord(
        id=record_id,
        user_account_id=None,
        message_kind=kind,
        recipient_normalized_email="person@example.test",
        payload_ciphertext=encrypted.ciphertext,
        encryption_key_id=encrypted.key_id,
        encryption_key_version=encrypted.key_version,
        deduplication_key=dedupe,
        status=OutboxStatus.PENDING,
        created_at=NOW,
        available_at=NOW,
    )


def _dispatch_encrypted(record: AuthenticationEmailOutboxRecord):
    claim_repository = MagicMock()
    claim_repository.claim_available.return_value = [record]
    result_repository = MagicMock()
    units = iter([DispatchUnit(claim_repository), DispatchUnit(result_repository)])
    provider = MagicMock()
    provider.send.return_value = DeliveryReceipt("provider-id")
    renderer = AuthenticationEmailTemplateRenderer(
        AesGcmOutboxCipher(b"e" * 32, key_id="test", key_version=1),
        "POS <auth@example.test>",
        clock=lambda: NOW,
    )
    summary = AuthenticationEmailDispatcher(
        lambda _request_id: next(units), renderer, provider, clock=lambda: NOW
    ).process_pending(1)
    return summary, provider, claim_repository, result_repository


def test_resend_uses_bounded_contract_and_idempotency_without_live_network():
    client = Client(Response(200, {"id": "provider-id"}))
    provider = ResendEmailProvider(SecretStr("must_never_appear"), client=client)

    receipt = provider.send(_message())

    assert receipt.provider_message_id == "provider-id"
    path, request = client.calls[0]
    assert path == "/emails"
    assert request["headers"]["Idempotency-Key"] == "outbox-id"
    assert request["json"]["text"] == "Plain body"
    assert request["json"]["html"] == "<p>Body</p>"
    assert "must_never_appear" not in repr(provider)


@pytest.mark.parametrize(
    ("response", "retryable", "category"),
    [(Response(429), True, "provider_unavailable"),
     (Response(503), True, "provider_unavailable"),
     (Response(400), False, "provider_rejected")],
)
def test_resend_classifies_failures_without_provider_body(response, retryable, category):
    provider = ResendEmailProvider(SecretStr("must_never_appear"), client=Client(response))
    with pytest.raises(EmailDeliveryError) as captured:
        provider.send(_message())
    assert captured.value.retryable is retryable
    assert captured.value.category == category
    assert "must_never_appear" not in str(captured.value)


def test_resend_network_failure_is_safe(caplog):
    request = httpx.Request("POST", "https://api.resend.com/emails")
    provider = ResendEmailProvider(
        SecretStr("must_never_appear"),
        client=Client(error=httpx.ConnectError("must_never_appear", request=request)),
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(EmailDeliveryError) as captured:
        provider.send(_message())
    rendered = (
        str(captured.value)
        + caplog.text
        + "".join(traceback.format_exception(captured.value))
    )
    assert "must_never_appear" not in rendered


def test_reset_renderer_builds_text_and_accessible_html_from_encrypted_payload():
    cipher = AesGcmOutboxCipher(b"e" * 32, key_id="test", key_version=1)
    record_id = uuid4()
    dedupe = f"password-reset:{record_id}"
    encrypted = cipher.encrypt_password_reset(
        PasswordResetEmailMessage(
            "t" * 43, "https://app.example.test/reset", NOW + timedelta(minutes=1)
        ), context=dedupe.encode("ascii")
    )
    record = AuthenticationEmailOutboxRecord(
        id=record_id, user_account_id=None,
        message_kind=OutboxMessageKind.PASSWORD_RESET,
        recipient_normalized_email="person@example.test",
        payload_ciphertext=encrypted.ciphertext,
        encryption_key_id=encrypted.key_id,
        encryption_key_version=encrypted.key_version,
        deduplication_key=dedupe, status=OutboxStatus.PENDING,
        created_at=NOW, available_at=NOW,
    )
    rendered = AuthenticationEmailTemplateRenderer(
        cipher, "POS <auth@example.test>", clock=lambda: NOW
    ).render(record)
    assert "Reset your password" in rendered.text
    assert '<a href="https://app.example.test/reset?token=' in rendered.html
    assert rendered.idempotency_key == str(record_id)


def test_development_custom_scheme_is_complete_in_html_and_plaintext_fallback(
    caplog, capsys
):
    token = "fake_opaque_reset_token_value_123456789012345"
    record = _encrypted_record(
        OutboxMessageKind.PASSWORD_RESET,
        NOW + timedelta(minutes=1),
        reset_url="pos://reset-password",
        token=token,
    )

    with caplog.at_level(logging.DEBUG):
        rendered = AuthenticationEmailTemplateRenderer(
            AesGcmOutboxCipher(b"e" * 32, key_id="test", key_version=1),
            "POS <auth@example.test>",
            clock=lambda: NOW,
        ).render(record)

    link = f"pos://reset-password?token={token}"
    assert f"Reset your password: {link}" in rendered.text
    assert "Development app link (copy the complete URL" in rendered.text
    assert rendered.text.count(link) == 2
    assert f'<a href="{link}">Reset your password</a>' in rendered.html
    assert "If the button is not supported by your email client" in rendered.html
    assert f"<span>{link}</span>" in rendered.html
    assert token not in caplog.text
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err


def test_resend_preserves_custom_scheme_html_and_plaintext_representations():
    token = "fake_opaque_reset_token_value_123456789012345"
    record = _encrypted_record(
        OutboxMessageKind.PASSWORD_RESET,
        NOW + timedelta(minutes=1),
        reset_url="pos://reset-password",
        token=token,
    )
    rendered = AuthenticationEmailTemplateRenderer(
        AesGcmOutboxCipher(b"e" * 32, key_id="test", key_version=1),
        "POS <auth@example.test>",
        clock=lambda: NOW,
    ).render(record)
    client = Client(Response(200, {"id": "provider-id"}))

    ResendEmailProvider(SecretStr("fake-provider-key"), client=client).send(rendered)

    request_body = client.calls[0][1]["json"]
    link = f"pos://reset-password?token={token}"
    assert link in request_body["text"]
    assert link in request_body["html"]
    assert request_body["to"] == ["person@example.test"]


def test_local_inspection_command_never_prints_password_reset_token(monkeypatch, capsys):
    token = "fake_opaque_reset_token_value_123456789012345"
    record = SimpleNamespace(
        message_kind=OutboxMessageKind.PASSWORD_RESET.value,
        recipient_normalized_email="person@example.test",
        payload_ciphertext=b"ciphertext",
        encryption_key_id="test",
        encryption_key_version=1,
        deduplication_key="password-reset:fake-id",
    )
    database_session = MagicMock()
    database_session.scalars.return_value.one_or_none.return_value = record
    session_context = MagicMock()
    session_context.__enter__.return_value = database_session
    cipher = MagicMock()
    cipher.decrypt_password_reset.return_value = PasswordResetEmailMessage(
        token,
        "pos://reset-password",
        NOW + timedelta(minutes=1),
    )
    monkeypatch.setattr(
        local_email,
        "load_settings",
        lambda: SimpleNamespace(
            app_env=local_email.AppEnvironment.DEVELOPMENT,
            local_email_inspection_enabled=True,
            database_url=SecretStr("postgresql://localhost/pos"),
            auth_outbox_encryption_key=SecretStr("fake-encryption-key"),
            auth_outbox_encryption_key_id="test",
            auth_outbox_encryption_key_version=1,
        ),
    )
    monkeypatch.setattr(local_email, "create_database_engine", lambda _url: object())
    monkeypatch.setattr(
        local_email, "create_session_factory", lambda _engine: lambda: session_context
    )
    monkeypatch.setattr(local_email, "decode_authentication_key", lambda *_args: b"e" * 32)
    monkeypatch.setattr(local_email, "AesGcmOutboxCipher", lambda *_args, **_kwargs: cipher)
    monkeypatch.setattr(local_email, "dispose_database_engine", lambda _engine: None)

    assert local_email.inspect_latest() == 0

    output = capsys.readouterr()
    assert token not in output.out
    assert token not in output.err
    assert "restricted to recipient email delivery" in output.out


@pytest.mark.parametrize(
    "kind", [OutboxMessageKind.EMAIL_VERIFICATION, OutboxMessageKind.PASSWORD_RESET]
)
@pytest.mark.parametrize("offset", [timedelta(seconds=-1), timedelta(0)])
def test_expired_authentication_message_fails_permanently_before_provider(
    kind, offset
):
    record = _encrypted_record(kind, NOW + offset)

    summary, provider, claim_repository, repository = _dispatch_encrypted(record)

    assert summary.failed == 1
    assert summary.delivered == 0
    assert summary.retry_scheduled == 0
    provider.send.assert_not_called()
    repository.mark_failed.assert_called_once()
    claim_id = repository.mark_failed.call_args.args[1]
    assert repository.mark_failed.call_args.args[0] == record.id
    assert repository.mark_failed.call_args.args[2:] == (NOW, "message_expired")
    assert claim_id == claim_repository.claim_available.call_args.args[1]
    repository.release_for_retry.assert_not_called()


@pytest.mark.parametrize(
    "kind", [OutboxMessageKind.EMAIL_VERIFICATION, OutboxMessageKind.PASSWORD_RESET]
)
def test_authentication_message_immediately_before_expiry_reaches_provider(kind):
    record = _encrypted_record(kind, NOW + timedelta(microseconds=1))

    summary, provider, _claim_repository, repository = _dispatch_encrypted(record)

    assert summary.delivered == 1
    assert summary.failed == 0
    provider.send.assert_called_once()
    repository.mark_delivered.assert_called_once()


def test_email_configuration_defaults_are_safe_and_protected_values_are_validated():
    settings = load_settings({"APP_ENV": "local"})
    assert settings.auth_email_provider.value == "local"
    assert settings.resend_api_key is None
    assert settings.auth_password_reset_url is None
    with pytest.raises(ConfigurationError):
        load_settings({"APP_ENV": "production", "AUTH_PASSWORD_RESET_URL": "http://app.example/reset"})
    with pytest.raises(ValueError):
        load_settings({"APP_ENV": "local", "AUTH_EMAIL_PROVIDER": "resend"})

    values = dict(
        line.split("=", 1)
        for line in Path(".env.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["AUTH_EMAIL_PROVIDER"] == "local"
    assert values["RESEND_API_KEY"] == ""
    assert values["AUTH_EMAIL_FROM"] == ""
    assert values["AUTH_PASSWORD_RESET_URL"] == ""
    assert values["AUTH_LOCAL_EMAIL_INSPECTION_ENABLED"] == "false"


def test_protected_environments_require_https_reset_destinations():
    secret = SecretStr("eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg")
    valid = {
        "cors_origins": ("https://app.pos.com",),
        "database_url": SecretStr("postgresql://localhost/pos"),
        "jwt_secret": SecretStr("fake-jwt-secret-that-is-at-least-32-characters"),
        "auth_challenge_digest_key": secret,
        "auth_outbox_encryption_key": secret,
        "auth_refresh_receipt_encryption_key": secret,
        "current_terms_version": "terms-v1",
        "current_terms_url": "https://legal.pos.com/terms",
        "current_privacy_version": "privacy-v1",
        "current_privacy_url": "https://legal.pos.com/privacy",
        "auth_rate_limit_backend": "redis",
        "redis_url": SecretStr("redis://localhost/0"),
        "auth_rate_limit_key": secret,
        "auth_public_origin": "https://api.pos.com",
        "auth_email_provider": "resend",
        "resend_api_key": SecretStr("fake-provider-key"),
        "auth_email_from": "auth@pos.com",
        "auth_password_reset_url": "https://app.pos.com/reset-password",
    }

    for environment in ("staging", "production"):
        settings = Settings(app_env=environment, **valid)
        assert settings.auth_password_reset_url.startswith("https://")
        for reset_url in (
            "pos://reset-password",
            "http://127.0.0.1:8081/reset-password",
        ):
            with pytest.raises(ValueError, match="approved reset destination"):
                Settings(
                    app_env=environment,
                    **{**valid, "auth_password_reset_url": reset_url},
                )


class DispatchUnit:
    def __init__(self, outbox):
        self.email_outbox = outbox
        self.commit = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_dispatcher_claims_bounded_batch_and_marks_provider_acceptance():
    record = MagicMock()
    record.id = uuid4()
    record.attempt_count = 0
    claim_repository = MagicMock()
    claim_repository.claim_available.return_value = [record]
    result_repository = MagicMock()
    units = iter([DispatchUnit(claim_repository), DispatchUnit(result_repository)])
    renderer = MagicMock()
    renderer.render.return_value = _message()
    provider = MagicMock()
    provider.send.return_value = DeliveryReceipt("provider-id")
    dispatcher = AuthenticationEmailDispatcher(
        lambda _request_id: next(units), renderer, provider, clock=lambda: NOW
    )

    summary = dispatcher.process_pending(10)

    assert summary.delivered == 1
    claim_repository.claim_available.assert_called_once()
    result_repository.mark_delivered.assert_called_once()


def test_dispatcher_schedules_retryable_failure_without_leaking_payload():
    record = MagicMock()
    record.id = uuid4()
    record.attempt_count = 0
    claim_repository = MagicMock()
    claim_repository.claim_available.return_value = [record]
    result_repository = MagicMock()
    units = iter([DispatchUnit(claim_repository), DispatchUnit(result_repository)])
    renderer = MagicMock()
    renderer.render.return_value = _message()
    provider = MagicMock()
    provider.send.side_effect = EmailDeliveryError(
        "provider_unavailable", retryable=True
    )
    summary = AuthenticationEmailDispatcher(
        lambda _request_id: next(units), renderer, provider, clock=lambda: NOW
    ).process_pending(1)
    assert summary.retry_scheduled == 1
    result_repository.release_for_retry.assert_called_once()
