"""Explicit password-recovery dependency composition."""

from dataclasses import dataclass

from sqlalchemy import Engine

from app.application.password_recovery import PasswordRecoveryPolicy, PasswordRecoveryService
from app.config import Settings, decode_authentication_key
from app.infrastructure.authentication.registration import (
    AesGcmOutboxCipher,
    Argon2idPasswordHasher,
    EmailValidatorNormalizer,
    HmacPasswordResetMaterialProvider,
    LocalCompromisedPasswordChecker,
    SafeAuthenticationEventLogger,
)
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.database import create_database_engine, create_session_factory


@dataclass(frozen=True, slots=True)
class PasswordRecoveryComposition:
    service: PasswordRecoveryService
    engine: Engine


def compose_password_recovery(
    settings: Settings, *, shared_engine: Engine | None = None
) -> PasswordRecoveryComposition | None:
    if (
        settings.database_url is None
        or settings.auth_challenge_digest_key is None
        or settings.auth_outbox_encryption_key is None
        or settings.auth_password_reset_url is None
    ):
        return None
    engine = shared_engine or create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    service = PasswordRecoveryService(
        lambda request_id: SqlAlchemyAuthenticationUnitOfWork(
            session_factory, request_id=request_id
        ),
        Argon2idPasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost_kib=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
        ),
        LocalCompromisedPasswordChecker(),
        EmailValidatorNormalizer(),
        HmacPasswordResetMaterialProvider(
            decode_authentication_key(
                settings.auth_challenge_digest_key, "AUTH_CHALLENGE_DIGEST_KEY"
            )
        ),
        AesGcmOutboxCipher(
            decode_authentication_key(
                settings.auth_outbox_encryption_key, "AUTH_OUTBOX_ENCRYPTION_KEY"
            ),
            key_id=settings.auth_outbox_encryption_key_id,
            key_version=settings.auth_outbox_encryption_key_version,
        ),
        SafeAuthenticationEventLogger(),
        PasswordRecoveryPolicy(reset_url=settings.auth_password_reset_url),
    )
    return PasswordRecoveryComposition(service=service, engine=engine)
