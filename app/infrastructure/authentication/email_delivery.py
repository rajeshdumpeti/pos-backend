"""Authentication email rendering, Resend delivery, and bounded worker CLI."""

from html import escape
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import SecretStr

from app.application.email_delivery import (
    AuthenticationEmailDispatcher,
    DeliveryReceipt,
    EmailDeliveryError,
    OutboundEmail,
    raise_safe_delivery_error,
)
from app.application.email_registration import EncryptedOutboxPayload
from app.application.email_registration import AuthenticationCryptographyError
from app.config import (
    AuthenticationEmailProvider,
    decode_authentication_key,
    load_settings,
)
from app.domain.authentication import AuthenticationEmailOutboxRecord, OutboxMessageKind
from app.infrastructure.authentication.registration import AesGcmOutboxCipher
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.database import create_database_engine, create_session_factory, dispose_database_engine


class AuthenticationEmailTemplateRenderer:
    def __init__(self, cipher: AesGcmOutboxCipher, sender: str) -> None:
        self._cipher = cipher
        self._sender = sender

    def render(self, record: AuthenticationEmailOutboxRecord) -> OutboundEmail:
        payload = EncryptedOutboxPayload(
            ciphertext=record.payload_ciphertext,
            key_id=record.encryption_key_id,
            key_version=record.encryption_key_version,
        )
        context = record.deduplication_key.encode("ascii")
        if record.message_kind is OutboxMessageKind.PASSWORD_RESET:
            try:
                message = self._cipher.decrypt_password_reset(payload, context=context)
            except AuthenticationCryptographyError as error:
                raise_safe_delivery_error(
                    "payload_unavailable", retryable=True, internal_cause=error
                )
            link = _reset_link(message.reset_url, message.token)
            subject = "Reset your POS password"
            text = (
                "A password reset was requested for your POS account.\n\n"
                f"Reset your password: {link}\n\n"
                "This link expires in 30 minutes. If you did not request this, "
                "you can ignore this email."
            )
            html = (
                '<main><h1>Reset your POS password</h1>'
                '<p>A password reset was requested for your POS account.</p>'
                f'<p><a href="{escape(link, quote=True)}">Reset your password</a></p>'
                '<p>This link expires in 30 minutes. If you did not request this, '
                'you can ignore this email.</p></main>'
            )
        else:
            try:
                message = self._cipher.decrypt(payload, context=context)
            except AuthenticationCryptographyError as error:
                raise_safe_delivery_error(
                    "payload_unavailable", retryable=True, internal_cause=error
                )
            subject = "Verify your POS email"
            text = (
                f"Your POS verification code is {message.code}. "
                "It expires in 10 minutes."
            )
            html = (
                '<main><h1>Verify your POS email</h1>'
                f'<p>Your verification code is <strong>{escape(message.code)}</strong>.</p>'
                '<p>It expires in 10 minutes.</p></main>'
            )
        return OutboundEmail(
            recipient=record.recipient_normalized_email,
            sender=self._sender,
            subject=subject,
            text=text,
            html=html,
            idempotency_key=str(record.id),
        )


class ResendEmailProvider:
    """Narrow HTTPS adapter; provider bodies and credentials never enter errors."""

    def __init__(
        self, api_key: SecretStr, *, client: httpx.Client | None = None
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(
            base_url="https://api.resend.com", timeout=httpx.Timeout(10.0)
        )

    def send(self, message: OutboundEmail) -> DeliveryReceipt:
        try:
            response = self._client.post(
                "/emails",
                headers={
                    "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                    "Idempotency-Key": message.idempotency_key,
                    "User-Agent": "pos-backend/0.1.0",
                },
                json={
                    "from": message.sender,
                    "to": [message.recipient],
                    "subject": message.subject,
                    "text": message.text,
                    "html": message.html,
                },
            )
        except httpx.TransportError as error:
            raise_safe_delivery_error(
                "provider_unavailable", retryable=True, internal_cause=error
            )
        if 200 <= response.status_code < 300:
            try:
                provider_id = str(response.json()["id"])
            except (ValueError, KeyError, TypeError) as error:
                raise_safe_delivery_error(
                    "provider_response_invalid", retryable=True, internal_cause=error
                )
            return DeliveryReceipt(provider_id)
        if response.status_code == 429 or response.status_code >= 500:
            raise EmailDeliveryError("provider_unavailable", retryable=True)
        raise EmailDeliveryError("provider_rejected", retryable=False)


def _reset_link(base_url: str, token: str) -> str:
    parsed = urlsplit(base_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("token", token))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def process_pending(limit: int) -> int:
    engine = None
    try:
        settings = load_settings()
        if settings.auth_email_provider is AuthenticationEmailProvider.LOCAL:
            print("Authentication email delivery is in local inspection mode")
            return 2
        if (
            settings.database_url is None
            or settings.auth_outbox_encryption_key is None
            or settings.resend_api_key is None
            or settings.auth_email_from is None
        ):
            print("Authentication email delivery configuration is incomplete")
            return 2
        engine = create_database_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        cipher = AesGcmOutboxCipher(
            decode_authentication_key(
                settings.auth_outbox_encryption_key, "AUTH_OUTBOX_ENCRYPTION_KEY"
            ),
            key_id=settings.auth_outbox_encryption_key_id,
            key_version=settings.auth_outbox_encryption_key_version,
        )
        dispatcher = AuthenticationEmailDispatcher(
            lambda request_id: SqlAlchemyAuthenticationUnitOfWork(
                session_factory, request_id=request_id
            ),
            AuthenticationEmailTemplateRenderer(cipher, settings.auth_email_from),
            ResendEmailProvider(settings.resend_api_key),
        )
        result = dispatcher.process_pending(limit)
        print(
            f"claimed={result.claimed} delivered={result.delivered} "
            f"retry_scheduled={result.retry_scheduled} failed={result.failed}"
        )
        return 0 if result.failed == 0 else 3
    except BaseException:
        print("Authentication email delivery failed safely")
        return 4
    finally:
        if engine is not None:
            try:
                dispose_database_engine(engine)
            except BaseException:
                pass


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) != 2 or arguments[0] != "process-pending":
        print(
            "Usage: python -m app.infrastructure.authentication.email_delivery "
            "process-pending LIMIT"
        )
        return 1
    try:
        limit = int(arguments[1])
    except ValueError:
        print("LIMIT must be an integer")
        return 1
    return process_pending(limit)


if __name__ == "__main__":
    raise SystemExit(main())
