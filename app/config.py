"""Central, secret-safe runtime configuration for POS Backend."""

from collections.abc import Mapping
import base64
import binascii
from enum import StrEnum
from email.utils import parseaddr
import os
import re
from ipaddress import ip_network

from app.application.authentication_security import AbusePolicy, DEFAULT_POLICIES
from typing import Self
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ConfigurationError(ValueError):
    """Raised when a runtime configuration variable is invalid."""


class AppEnvironment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class AuthenticationEmailProvider(StrEnum):
    LOCAL = "local"
    RESEND = "resend"


class AuthenticationLegalConsentMode(StrEnum):
    REQUIRED = "required"
    DEFERRED_NONPRODUCTION = "deferred_nonproduction"


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
    current_terms_url: str | None = None
    current_privacy_url: str | None = None
    auth_legal_consent_mode: AuthenticationLegalConsentMode = (
        AuthenticationLegalConsentMode.REQUIRED
    )
    local_email_inspection_enabled: bool = False
    auth_email_provider: AuthenticationEmailProvider = AuthenticationEmailProvider.LOCAL
    resend_api_key: SecretStr | None = Field(default=None, repr=False)
    auth_email_from: str | None = None
    auth_password_reset_url: str | None = Field(default=None, repr=False)

    auth_rate_limit_backend: str = "memory"
    auth_rate_limit_key: SecretStr | None = Field(default=None, repr=False)
    auth_rate_limit_policies: dict[str, AbusePolicy] = Field(default_factory=lambda: dict(DEFAULT_POLICIES))
    auth_rate_limit_global: int = 10000
    auth_rate_limit_max_entries: int = 100000
    auth_redis_timeout_ms: int = 200
    auth_trusted_proxy_networks: tuple[str, ...] = ()
    auth_public_origin: str | None = None
    auth_max_body_bytes: int = 16384
    auth_max_authorization_bytes: int = 4096

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
        _validate_security_settings(self)
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
    if (
        settings.auth_legal_consent_mode
        is AuthenticationLegalConsentMode.DEFERRED_NONPRODUCTION
        and settings.app_env in _PROTECTED_ENVIRONMENTS
    ):
        raise ConfigurationError(
            "AUTH_LEGAL_CONSENT_MODE deferred_nonproduction is restricted to non-production environments"
        )
    legal_values = (
        settings.current_terms_version,
        settings.current_terms_url,
        settings.current_privacy_version,
        settings.current_privacy_url,
    )
    if settings.auth_legal_consent_mode is AuthenticationLegalConsentMode.REQUIRED:
        configured = tuple(value is not None for value in legal_values)
        if any(configured) and not all(configured):
            raise ConfigurationError(
                "Required legal policy configuration must include both versions and both URLs"
            )
        if all(configured):
            _validate_legal_policy(settings)
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
                ("CURRENT_TERMS_URL", settings.current_terms_url),
                ("CURRENT_PRIVACY_URL", settings.current_privacy_url),
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
    if settings.auth_password_reset_url is not None:
        try:
            reset_url = urlsplit(settings.auth_password_reset_url)
        except ValueError:
            raise ConfigurationError("AUTH_PASSWORD_RESET_URL must be a valid URL") from None
        allowed_reset_schemes = (
            {"http", "https", "pos", "pos-dev"}
            if settings.app_env in {AppEnvironment.LOCAL, AppEnvironment.DEVELOPMENT}
            else {"https"}
        )
        try:
            reset_query = parse_qsl(
                reset_url.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=20,
            )
        except ValueError:
            raise ConfigurationError(
                "AUTH_PASSWORD_RESET_URL query must be valid"
            ) from None
        if (
            reset_url.scheme not in allowed_reset_schemes
            or not reset_url.hostname
            or reset_url.username is not None
            or reset_url.password is not None
            or reset_url.fragment
        ):
            raise ConfigurationError(
                "AUTH_PASSWORD_RESET_URL must be an approved reset destination"
            )
        if any(name == "token" for name, _value in reset_query):
            raise ConfigurationError(
                "AUTH_PASSWORD_RESET_URL must not contain a token parameter"
            )
    if settings.auth_email_provider is AuthenticationEmailProvider.RESEND:
        missing_email = [
            name for name, value in (
                ("RESEND_API_KEY", settings.resend_api_key),
                ("AUTH_EMAIL_FROM", settings.auth_email_from),
            ) if value is None
        ]
        if missing_email:
            raise ConfigurationError(
                "Resend email delivery requires: " + ", ".join(missing_email)
            )
        assert settings.auth_email_from is not None
        _display_name, sender_address = parseaddr(settings.auth_email_from)
        if (
            not sender_address
            or "@" not in sender_address
            or "\r" in settings.auth_email_from
            or "\n" in settings.auth_email_from
            or len(settings.auth_email_from) > 320
        ):
            raise ConfigurationError("AUTH_EMAIL_FROM must be a valid sender address")
        if (
            settings.app_env in _PROTECTED_ENVIRONMENTS
            and sender_address.casefold().endswith("@resend.dev")
        ):
            raise ConfigurationError("AUTH_EMAIL_FROM must use a verified sender domain")


def _validate_legal_policy(settings: Settings) -> None:
    values = {
        "CURRENT_TERMS_VERSION": settings.current_terms_version,
        "CURRENT_PRIVACY_VERSION": settings.current_privacy_version,
    }
    placeholder = re.compile(
        r"(?:placeholder|change[-_ ]?me|replace[-_ ]?me|todo|example)", re.I
    )
    for name, value in values.items():
        assert value is not None
        if placeholder.search(value) or "<" in value or ">" in value:
            raise ConfigurationError(f"{name} must not be a placeholder")
    for name, value in (
        ("CURRENT_TERMS_URL", settings.current_terms_url),
        ("CURRENT_PRIVACY_URL", settings.current_privacy_url),
    ):
        assert value is not None
        try:
            parsed = urlsplit(value)
            parsed.port
        except ValueError:
            raise ConfigurationError(f"{name} must be a valid reviewable URL") from None
        allowed_schemes = (
            {"http", "https"}
            if settings.app_env not in _PROTECTED_ENVIRONMENTS
            else {"https"}
        )
        if (
            parsed.scheme.lower() not in allowed_schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError(f"{name} must be an approved reviewable URL")
        if settings.app_env in _PROTECTED_ENVIRONMENTS and (
            placeholder.search(parsed.hostname)
            or parsed.hostname.endswith((".example", ".test", ".invalid"))
            or parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ConfigurationError(f"{name} must not be a placeholder")


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
        auth_rate_limit_backend=_optional_value(source, "AUTH_RATE_LIMIT_BACKEND") or "memory",
        auth_rate_limit_key=_parse_auth_secret(source, "AUTH_RATE_LIMIT_KEY"),
        auth_rate_limit_policies={operation: AbusePolicy(
            _parse_int(source, "AUTH_LIMIT_" + operation.upper() + "_SOURCE", policy.source_limit),
            _parse_int(source, "AUTH_LIMIT_" + operation.upper() + "_IDENTITY", policy.identity_limit, minimum=0 if policy.identity_limit == 0 else 1),
            _parse_int(source, "AUTH_LIMIT_" + operation.upper() + "_WINDOW_SECONDS", policy.window_seconds),
        ) for operation, policy in DEFAULT_POLICIES.items()},
        auth_rate_limit_global=_parse_int(source, "AUTH_RATE_LIMIT_GLOBAL", 10000),
        auth_rate_limit_max_entries=_parse_int(source, "AUTH_RATE_LIMIT_MAX_ENTRIES", 100000),
        auth_redis_timeout_ms=_parse_int(source, "AUTH_REDIS_TIMEOUT_MS", 200),
        auth_max_body_bytes=_parse_int(source, "AUTH_MAX_BODY_BYTES", 16384),
        auth_max_authorization_bytes=_parse_int(source, "AUTH_MAX_AUTHORIZATION_BYTES", 4096),
        auth_trusted_proxy_networks=tuple(v.strip() for v in source.get("AUTH_TRUSTED_PROXY_NETWORKS", "").split(",") if v.strip()),
        auth_public_origin=_optional_value(source, "AUTH_PUBLIC_ORIGIN"),
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
        current_terms_url=_optional_value(source, "CURRENT_TERMS_URL"),
        current_privacy_url=_optional_value(source, "CURRENT_PRIVACY_URL"),
        auth_legal_consent_mode=_parse_legal_consent_mode(source),
        local_email_inspection_enabled=_parse_bool(
            source, "AUTH_LOCAL_EMAIL_INSPECTION_ENABLED", False
        ),
        auth_email_provider=_parse_email_provider(source),
        resend_api_key=(
            None
            if _optional_value(source, "RESEND_API_KEY") is None
            else SecretStr(_optional_value(source, "RESEND_API_KEY") or "")
        ),
        auth_email_from=_optional_value(source, "AUTH_EMAIL_FROM"),
        auth_password_reset_url=_optional_value(source, "AUTH_PASSWORD_RESET_URL"),
    )


def _parse_email_provider(environ: Mapping[str, str]) -> AuthenticationEmailProvider:
    raw = (_optional_value(environ, "AUTH_EMAIL_PROVIDER") or "local").lower()
    try:
        return AuthenticationEmailProvider(raw)
    except ValueError:
        raise ConfigurationError("AUTH_EMAIL_PROVIDER must be local or resend") from None


def _parse_legal_consent_mode(
    environ: Mapping[str, str],
) -> AuthenticationLegalConsentMode:
    raw = (_optional_value(environ, "AUTH_LEGAL_CONSENT_MODE") or "required").lower()
    try:
        return AuthenticationLegalConsentMode(raw)
    except ValueError:
        raise ConfigurationError(
            "AUTH_LEGAL_CONSENT_MODE must be required or deferred_nonproduction"
        ) from None


def _validate_security_settings(settings: Settings) -> None:
    if settings.auth_rate_limit_backend not in {"memory", "redis"}:
        raise ConfigurationError("AUTH_RATE_LIMIT_BACKEND must be memory or redis")
    if settings.auth_rate_limit_key is not None:
        _decode_256_bit_key(settings.auth_rate_limit_key.get_secret_value(), "AUTH_RATE_LIMIT_KEY")
    if settings.auth_rate_limit_backend == "redis":
        if settings.redis_url is None or settings.auth_rate_limit_key is None:
            raise ConfigurationError("Redis admission requires REDIS_URL and AUTH_RATE_LIMIT_KEY")
        target = urlsplit(settings.redis_url.get_secret_value())
        if target.query or target.fragment or not re.fullmatch(r"/[0-9]{1,2}|", target.path):
            raise ConfigurationError("REDIS_URL requires a numeric database path and no query or fragment")
    for name, value, minimum, maximum in (
        ("AUTH_RATE_LIMIT_GLOBAL", settings.auth_rate_limit_global, 1, 1000000),
        ("AUTH_RATE_LIMIT_MAX_ENTRIES", settings.auth_rate_limit_max_entries, 100, 1000000),
        ("AUTH_REDIS_TIMEOUT_MS", settings.auth_redis_timeout_ms, 10, 2000),
        ("AUTH_MAX_BODY_BYTES", settings.auth_max_body_bytes, 8192, 65536),
        ("AUTH_MAX_AUTHORIZATION_BYTES", settings.auth_max_authorization_bytes, 1024, 4096),
    ):
        if not minimum <= value <= maximum:
            raise ConfigurationError(f"{name} is outside the supported range")
    if set(settings.auth_rate_limit_policies) != set(DEFAULT_POLICIES):
        raise ConfigurationError("Authentication policies must cover every operation")
    for operation, policy in settings.auth_rate_limit_policies.items():
        minimum = 1 if DEFAULT_POLICIES[operation].identity_limit else 0
        if not (1 <= policy.source_limit <= 100000 and minimum <= policy.identity_limit <= 100000
                and 1 <= policy.window_seconds <= 3600):
            raise ConfigurationError("Authentication policy is outside the supported range")
    try:
        networks = [ip_network(value, strict=True) for value in settings.auth_trusted_proxy_networks]
    except ValueError:
        raise ConfigurationError("AUTH_TRUSTED_PROXY_NETWORKS must contain canonical CIDRs") from None
    if any(n.prefixlen == 0 for n in networks):
        raise ConfigurationError("AUTH_TRUSTED_PROXY_NETWORKS must not trust every address")
    if settings.auth_public_origin is not None:
        _validate_cors_origins((settings.auth_public_origin,), AppEnvironment.PRODUCTION)
    if settings.app_env in _PROTECTED_ENVIRONMENTS:
        if settings.auth_rate_limit_backend != "redis":
            raise ConfigurationError("Protected authentication requires Redis admission")
        if settings.auth_public_origin is None or not settings.auth_public_origin.startswith("https://"):
            raise ConfigurationError("AUTH_PUBLIC_ORIGIN must use HTTPS in protected environments")
        if any(not origin.startswith("https://") for origin in settings.cors_origins):
            raise ConfigurationError("Protected CORS origins must use HTTPS")
        if settings.auth_email_provider is AuthenticationEmailProvider.LOCAL:
            raise ConfigurationError("Protected authentication requires a delivery provider")
        if settings.auth_password_reset_url is None:
            raise ConfigurationError("Protected authentication requires AUTH_PASSWORD_RESET_URL")
