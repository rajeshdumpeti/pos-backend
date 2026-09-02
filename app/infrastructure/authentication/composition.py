"""Explicit composition of registration dependencies."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine

from app.application.email_registration import EmailRegistrationService, RegistrationPolicy
from app.config import Settings, decode_authentication_key
from app.infrastructure.authentication.registration import (
    AesGcmOutboxCipher,
    Argon2idPasswordHasher,
    EmailValidatorNormalizer,
    HmacVerificationMaterialProvider,
    LocalCompromisedPasswordChecker,
    SafeAuthenticationEventLogger,
)
from app.infrastructure.authentication.unit_of_work import (
    SqlAlchemyAuthenticationUnitOfWork,
)
from app.infrastructure.database import create_database_engine, create_session_factory


@dataclass(frozen=True, slots=True)
class AuthenticationComposition:
    service: EmailRegistrationService
    engine: Engine


def compose_email_registration(
    settings: Settings,
) -> AuthenticationComposition | None:
    """Compose only when every required local/production dependency exists."""
    if (
        settings.database_url is None
        or settings.auth_challenge_digest_key is None
        or settings.auth_outbox_encryption_key is None
        or settings.current_terms_version is None
        or settings.current_privacy_version is None
    ):
        return None

    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    service = EmailRegistrationService(
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
        HmacVerificationMaterialProvider(
            decode_authentication_key(
                settings.auth_challenge_digest_key,
                "AUTH_CHALLENGE_DIGEST_KEY",
            ),
            key_id=settings.auth_challenge_digest_key_id,
            key_version=settings.auth_challenge_digest_key_version,
        ),
        AesGcmOutboxCipher(
            decode_authentication_key(
                settings.auth_outbox_encryption_key,
                "AUTH_OUTBOX_ENCRYPTION_KEY",
            ),
            key_id=settings.auth_outbox_encryption_key_id,
            key_version=settings.auth_outbox_encryption_key_version,
        ),
        SafeAuthenticationEventLogger(),
        RegistrationPolicy(
            code_lifetime=timedelta(
                seconds=settings.verification_code_lifetime_seconds
            ),
            flow_lifetime=timedelta(
                seconds=settings.verification_flow_lifetime_seconds
            ),
            resend_cooldown=timedelta(
                seconds=settings.verification_resend_cooldown_seconds
            ),
            maximum_attempts=settings.verification_max_attempts,
            terms_version=settings.current_terms_version,
            privacy_version=settings.current_privacy_version,
        ),
    )
    return AuthenticationComposition(service=service, engine=engine)
