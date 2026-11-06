"""Secret-safe helpers shared by the Alembic environment and its tests."""

from pydantic import SecretStr

from app.config import Settings


def require_database_url(settings: Settings) -> SecretStr:
    """Return configured database material or fail without exposing its value."""
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is required for online migrations")
    return settings.database_url
