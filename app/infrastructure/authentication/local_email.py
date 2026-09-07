"""Explicit local-only inspection of encrypted authentication email payloads."""

import sys

from sqlalchemy import select

from app.application.email_registration import EncryptedOutboxPayload
from app.config import AppEnvironment, decode_authentication_key, load_settings
from app.domain.registration import mask_email
from app.infrastructure.authentication.models import AuthenticationEmailOutboxModel
from app.infrastructure.authentication.registration import AesGcmOutboxCipher
from app.domain.authentication import OutboxMessageKind
from app.infrastructure.authentication.email_delivery import _reset_link
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    dispose_database_engine,
)


def inspect_latest() -> int:
    engine = None
    try:
        settings = load_settings()
        if (
            settings.app_env not in {
                AppEnvironment.LOCAL, AppEnvironment.DEVELOPMENT}
            or not settings.local_email_inspection_enabled
            or settings.database_url is None
            or settings.auth_outbox_encryption_key is None
        ):
            print("Local email inspection is not enabled")
            return 2
        engine = create_database_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.scalars(
                select(AuthenticationEmailOutboxModel)
                .where(AuthenticationEmailOutboxModel.status == "pending")
                .order_by(AuthenticationEmailOutboxModel.created_at.desc())
                .limit(1)
            ).one_or_none()
        if record is None:
            print("No pending authentication email is available")
            return 3
        cipher = AesGcmOutboxCipher(
            decode_authentication_key(
                settings.auth_outbox_encryption_key,
                "AUTH_OUTBOX_ENCRYPTION_KEY",
            ),
            key_id=settings.auth_outbox_encryption_key_id,
            key_version=settings.auth_outbox_encryption_key_version,
        )
        payload = EncryptedOutboxPayload(
            ciphertext=record.payload_ciphertext,
            key_id=record.encryption_key_id,
            key_version=record.encryption_key_version,
        )
        print(f"Destination: {mask_email(record.recipient_normalized_email)}")
        if record.message_kind == OutboxMessageKind.PASSWORD_RESET.value:
            message = cipher.decrypt_password_reset(
                payload, context=record.deduplication_key.encode("ascii")
            )
            print(f"Password reset link: {_reset_link(message.reset_url, message.token)}")
            print(f"Expires at: {message.expires_at.isoformat()}")
        else:
            message = cipher.decrypt(
                payload, context=record.deduplication_key.encode("ascii")
            )
            print(f"Verification code: {message.code}")
            print(f"Expires at: {message.expires_at.isoformat()}")
        return 0
    except BaseException:
        print("Local email inspection failed safely")
        return 4
    finally:
        if engine is not None:
            try:
                dispose_database_engine(engine)
            except BaseException:
                pass


def main() -> int:
    if sys.argv[1:] != ["inspect-latest"]:
        print(
            "Usage: python -m app.infrastructure.authentication.local_email "
            "inspect-latest"
        )
        return 1
    return inspect_latest()


if __name__ == "__main__":
    raise SystemExit(main())
