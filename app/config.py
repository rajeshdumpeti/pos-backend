from collections.abc import Mapping
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str | None
    redis_url: str | None
    jwt_secret: str | None
    cors_origins: tuple[str, ...]


def _optional_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ
    origins = tuple(
        origin.strip()
        for origin in source.get("CORS_ORIGINS", "").split(",")
        if origin.strip()
    )

    return Settings(
        app_env=source.get("APP_ENV", "local").strip() or "local",
        database_url=_optional_value(source, "DATABASE_URL"),
        redis_url=_optional_value(source, "REDIS_URL"),
        jwt_secret=_optional_value(source, "JWT_SECRET"),
        cors_origins=origins or ("*",),
    )


settings = load_settings()
