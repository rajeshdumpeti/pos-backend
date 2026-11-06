"""Explicit composition for email sign-in and session lifecycle."""

from dataclasses import dataclass

from sqlalchemy import Engine

from app.application.session_lifecycle import (
    EmailSessionService,
    SessionPolicy,
    VerificationContinuationIssuer,
)
from app.config import Settings, decode_authentication_key
from app.infrastructure.authentication.registration import (
    Argon2idPasswordHasher,
    EmailValidatorNormalizer,
    SafeAuthenticationEventLogger,
)
from app.infrastructure.authentication.session_security import (
    AesGcmRefreshReceiptCipher,
    JwtAccessTokenCodec,
    OpaqueRefreshTokenGenerator,
)
from app.infrastructure.authentication.unit_of_work import SqlAlchemyAuthenticationUnitOfWork
from app.infrastructure.database import create_database_engine, create_session_factory


@dataclass(frozen=True, slots=True)
class SessionComposition:
    service: EmailSessionService
    engine: Engine


def compose_email_sessions(
    settings: Settings,
    *,
    shared_engine: Engine | None = None,
    verification_continuation: VerificationContinuationIssuer | None = None,
) -> SessionComposition | None:
    if (
        settings.database_url is None
        or settings.jwt_secret is None
        or settings.auth_refresh_receipt_encryption_key is None
    ):
        return None
    engine = shared_engine or create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    hasher = Argon2idPasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost_kib=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
    )
    policy = SessionPolicy()
    service = EmailSessionService(
        lambda request_id: SqlAlchemyAuthenticationUnitOfWork(session_factory, request_id=request_id),
        hasher,
        EmailValidatorNormalizer(),
        JwtAccessTokenCodec(settings.jwt_secret.get_secret_value(), policy),
        OpaqueRefreshTokenGenerator(),
        AesGcmRefreshReceiptCipher(
            decode_authentication_key(
                settings.auth_refresh_receipt_encryption_key,
                "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY",
            ),
            key_id=settings.auth_refresh_receipt_encryption_key_id,
            key_version=settings.auth_refresh_receipt_encryption_key_version,
        ),
        hasher.hash("POS constant-time dummy password"),
        policy,
        verification_continuation=verification_continuation,
        event_logger=SafeAuthenticationEventLogger(),
    )
    return SessionComposition(service=service, engine=engine)
