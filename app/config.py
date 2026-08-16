"""Central, secret-safe runtime configuration for POS Backend."""

from collections.abc import Mapping
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
    cors_origins: tuple[str, ...]

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


def _validate_jwt_secret(value: str) -> None:
    if len(value) < 32:
        raise ConfigurationError(
            "JWT_SECRET must contain at least 32 characters when configured"
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
        cors_origins=_parse_cors_origins(source, app_environment),
    )
