"""Central, secret-safe runtime configuration for POS Backend."""

from collections.abc import Mapping
import base64
import binascii
from enum import StrEnum
import os
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ConfigurationError(ValueError):
    """Raised when a runtime configuration variable is invalid."""


class AppEnvironment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseModel):
    """Validated settings with sensitive values excluded from representations."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    app_env: AppEnvironment
    database_url: SecretStr | None = Field(default=None, repr=False)
    redis_url: SecretStr | None = Field(default=None, repr=False)
    jwt_secret: SecretStr | None = Field(default=None, repr=False)
    auth_refresh_receipt_encryption_key: SecretStr | None = Field(default=None, repr=False)
    auth_refresh_receipt_encryption_key_id: str = "auth-refresh-receipt"
    auth_refresh_receipt_encryption_key_version: int = 1
    cors_origins: tuple[str, ...]
    verification_code_lifetime_seconds: int = 600
    verification_max_attempts: int = 5
    verification_resend_cooldown_seconds: int = 60
    verification_flow_lifetime_seconds: int = 1800
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4
    auth_challenge_digest_key: SecretStr | None = Field(default=None, repr=False)
    auth_challenge_digest_key_id: str = "auth-challenge"
    auth_challenge_digest_key_version: int = 1
    auth_outbox_encryption_key: SecretStr | None = Field(default=None, repr=False)
    auth_outbox_encryption_key_id: str = "auth-email"
    auth_outbox_encryption_key_version: int = 1
    current_terms_version: str | None = None
    current_privacy_version: str | None = None
    local_email_inspection_enabled: bool = False

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        if self.database_url is not None:
            _validate_secret_url(
                self.database_url.get_secret_value(),
                "DATABASE_URL",
                _DATABASE_SCHEMES,
            )
        if self.redis_url is not None:
            _validate_secret_url(
                self.redis_url.get_secret_value(),
                "REDIS_URL",
                _REDIS_SCHEMES,
            )
        if self.jwt_secret is not None:
            _validate_jwt_secret(self.jwt_secret.get_secret_value())
        _validate_cors_origins(self.cors_origins, self.app_env)
        _validate_authentication_settings(self)
        return self


_PROTECTED_ENVIRONMENTS = {
    AppEnvironment.STAGING,
    AppEnvironment.PRODUCTION,
}
_DATABASE_SCHEMES = {"postgresql", "postgresql+asyncpg", "postgresql+psycopg"}
_REDIS_SCHEMES = {"redis", "rediss"}


def _optional_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _parse_environment(environ: Mapping[str, str]) -> AppEnvironment:
    raw_environment = environ.get("APP_ENV", "local").strip().lower() or "local"

    try:
        return AppEnvironment(raw_environment)
    except ValueError:
        supported = ", ".join(environment.value for environment in AppEnvironment)
        raise ConfigurationError(f"APP_ENV must be one of: {supported}") from None


def _parse_secret_url(
    environ: Mapping[str, str],
    name: str,
    allowed_schemes: set[str],
) -> SecretStr | None:
    value = _optional_value(environ, name)
    if value is None:
        return None

    _validate_secret_url(value, name, allowed_schemes)
    return SecretStr(value)


def _validate_secret_url(value: str, name: str, allowed_schemes: set[str]) -> None:
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ConfigurationError(f"{name} must be a valid URL") from None

    if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
        schemes = ", ".join(sorted(allowed_schemes))
        raise ConfigurationError(f"{name} must use one of these URL schemes: {schemes}")


def _parse_jwt_secret(environ: Mapping[str, str]) -> SecretStr | None:
    value = _optional_value(environ, "JWT_SECRET")
    if value is None:
        return None
    _validate_jwt_secret(value)
    return SecretStr(value)


def _parse_int(
    environ: Mapping[str, str], name: str, default: int, *, minimum: int = 1
) -> int:
    raw_value = _optional_value(environ, name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer") from None
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _parse_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = _optional_value(environ, name)
    if raw_value is None:
        return default
    normalized = raw_value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _parse_auth_secret(environ: Mapping[str, str], name: str) -> SecretStr | None:
    value = _optional_value(environ, name)
    if value is None:
        return None
    _decode_256_bit_key(value, name)
    return SecretStr(value)


def _decode_256_bit_key(value: str, name: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, binascii.Error):
        raise ConfigurationError(
            f"{name} must be a URL-safe base64 encoded 256-bit key"
        ) from None
    if len(decoded) != 32:
        raise ConfigurationError(
            f"{name} must be a URL-safe base64 encoded 256-bit key"
        )
    return decoded


def decode_authentication_key(value: SecretStr, name: str) -> bytes:
    """Decode one validated key without exposing it outside composition."""
    return _decode_256_bit_key(value.get_secret_value(), name)


def _validate_jwt_secret(value: str) -> None:
    if len(value) < 32:
        raise ConfigurationError(
            "JWT_SECRET must contain at least 32 characters when configured"
        )


def _validate_authentication_settings(settings: Settings) -> None:
    if settings.verification_flow_lifetime_seconds < settings.verification_code_lifetime_seconds:
        raise ConfigurationError(
            "AUTH_VERIFICATION_FLOW_LIFETIME_SECONDS must be at least "
            "AUTH_VERIFICATION_CODE_LIFETIME_SECONDS"
        )
    if settings.verification_max_attempts != 5:
        raise ConfigurationError("AUTH_VERIFICATION_MAX_ATTEMPTS must be 5")
    if settings.verification_resend_cooldown_seconds != 60:
        raise ConfigurationError("AUTH_RESEND_COOLDOWN_SECONDS must be 60")
    if settings.verification_code_lifetime_seconds != 600:
        raise ConfigurationError("AUTH_VERIFICATION_CODE_LIFETIME_SECONDS must be 600")
    if settings.argon2_memory_cost_kib < 19456:
        raise ConfigurationError("AUTH_ARGON2_MEMORY_COST_KIB must be at least 19456")
    if settings.argon2_time_cost < 2:
        raise ConfigurationError("AUTH_ARGON2_TIME_COST must be at least 2")
    for name, value in (
        ("AUTH_CHALLENGE_DIGEST_KEY", settings.auth_challenge_digest_key),
        ("AUTH_OUTBOX_ENCRYPTION_KEY", settings.auth_outbox_encryption_key),
        (
            "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY",
            settings.auth_refresh_receipt_encryption_key,
        ),
    ):
        if value is not None:
            _decode_256_bit_key(value.get_secret_value(), name)
    for name, value in (
        ("AUTH_CHALLENGE_DIGEST_KEY_ID", settings.auth_challenge_digest_key_id),
        ("AUTH_OUTBOX_ENCRYPTION_KEY_ID", settings.auth_outbox_encryption_key_id),
        (
            "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY_ID",
            settings.auth_refresh_receipt_encryption_key_id,
        ),
    ):
        if not value or len(value) > 64:
            raise ConfigurationError(f"{name} must contain 1 to 64 characters")
    for name, value in (
        ("CURRENT_TERMS_VERSION", settings.current_terms_version),
        ("CURRENT_PRIVACY_VERSION", settings.current_privacy_version),
    ):
        if value is not None and (not value.strip() or len(value) > 64):
            raise ConfigurationError(f"{name} must contain 1 to 64 characters")
    if settings.app_env in _PROTECTED_ENVIRONMENTS:
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", settings.database_url),
                ("AUTH_CHALLENGE_DIGEST_KEY", settings.auth_challenge_digest_key),
                ("AUTH_OUTBOX_ENCRYPTION_KEY", settings.auth_outbox_encryption_key),
                ("JWT_SECRET", settings.jwt_secret),
                (
                    "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY",
                    settings.auth_refresh_receipt_encryption_key,
                ),
                ("CURRENT_TERMS_VERSION", settings.current_terms_version),
                ("CURRENT_PRIVACY_VERSION", settings.current_privacy_version),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                "Protected authentication environments require: "
                + ", ".join(missing)
            )
    if (
        settings.local_email_inspection_enabled
        and settings.app_env not in {AppEnvironment.LOCAL, AppEnvironment.DEVELOPMENT}
    ):
        raise ConfigurationError(
            "AUTH_LOCAL_EMAIL_INSPECTION_ENABLED is restricted to local and development"
        )


def _parse_cors_origins(
    environ: Mapping[str, str], app_environment: AppEnvironment
) -> tuple[str, ...]:
    raw_origins = environ.get("CORS_ORIGINS", "")
    origins = tuple(
        dict.fromkeys(
            origin.strip() for origin in raw_origins.split(",") if origin.strip()
        )
    ) or ("*",)

    _validate_cors_origins(origins, app_environment)
    return origins


def _validate_cors_origins(
    origins: tuple[str, ...], app_environment: AppEnvironment
) -> None:
    if "*" in origins and len(origins) > 1:
        raise ConfigurationError(
            "CORS_ORIGINS cannot combine the wildcard with explicit origins"
        )
    if origins == ("*",):
        if app_environment in _PROTECTED_ENVIRONMENTS:
            raise ConfigurationError(
                "CORS_ORIGINS must contain explicit origins for staging and production"
            )
        return

    for origin in origins:
        try:
            parsed = urlsplit(origin)
            parsed.port
        except ValueError:
            raise ConfigurationError(
                "CORS_ORIGINS must contain valid HTTP or HTTPS origins"
            ) from None

        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError(
                "CORS_ORIGINS must contain origin-only HTTP or HTTPS URLs"
            )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load isolated settings from a mapping or the process environment."""

    source = os.environ if environ is None else environ
    app_environment = _parse_environment(source)

    return Settings(
        app_env=app_environment,
        database_url=_parse_secret_url(source, "DATABASE_URL", _DATABASE_SCHEMES),
        redis_url=_parse_secret_url(source, "REDIS_URL", _REDIS_SCHEMES),
        jwt_secret=_parse_jwt_secret(source),
        auth_refresh_receipt_encryption_key=_parse_auth_secret(
            source, "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY"
        ),
        auth_refresh_receipt_encryption_key_id=(
            _optional_value(source, "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY_ID")
            or "auth-refresh-receipt"
        ),
        auth_refresh_receipt_encryption_key_version=_parse_int(
            source, "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY_VERSION", 1
        ),
        cors_origins=_parse_cors_origins(source, app_environment),
        verification_code_lifetime_seconds=_parse_int(
            source, "AUTH_VERIFICATION_CODE_LIFETIME_SECONDS", 600
        ),
        verification_max_attempts=_parse_int(
            source, "AUTH_VERIFICATION_MAX_ATTEMPTS", 5
        ),
        verification_resend_cooldown_seconds=_parse_int(
            source, "AUTH_RESEND_COOLDOWN_SECONDS", 60
        ),
        verification_flow_lifetime_seconds=_parse_int(
            source, "AUTH_VERIFICATION_FLOW_LIFETIME_SECONDS", 1800
        ),
        argon2_time_cost=_parse_int(source, "AUTH_ARGON2_TIME_COST", 3),
        argon2_memory_cost_kib=_parse_int(
            source, "AUTH_ARGON2_MEMORY_COST_KIB", 65536
        ),
        argon2_parallelism=_parse_int(source, "AUTH_ARGON2_PARALLELISM", 4),
        auth_challenge_digest_key=_parse_auth_secret(
            source, "AUTH_CHALLENGE_DIGEST_KEY"
        ),
        auth_challenge_digest_key_id=(
            _optional_value(source, "AUTH_CHALLENGE_DIGEST_KEY_ID")
            or "auth-challenge"
        ),
        auth_challenge_digest_key_version=_parse_int(
            source, "AUTH_CHALLENGE_DIGEST_KEY_VERSION", 1
        ),
        auth_outbox_encryption_key=_parse_auth_secret(
            source, "AUTH_OUTBOX_ENCRYPTION_KEY"
        ),
        auth_outbox_encryption_key_id=(
            _optional_value(source, "AUTH_OUTBOX_ENCRYPTION_KEY_ID")
            or "auth-email"
        ),
        auth_outbox_encryption_key_version=_parse_int(
            source, "AUTH_OUTBOX_ENCRYPTION_KEY_VERSION", 1
        ),
        current_terms_version=_optional_value(source, "CURRENT_TERMS_VERSION"),
        current_privacy_version=_optional_value(source, "CURRENT_PRIVACY_VERSION"),
        local_email_inspection_enabled=_parse_bool(
            source, "AUTH_LOCAL_EMAIL_INSPECTION_ENABLED", False
        ),
    )
