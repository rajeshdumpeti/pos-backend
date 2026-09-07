"""Isolated, secret-safe PostgreSQL migration setup for integration tests."""

from contextlib import contextmanager
import os
from collections.abc import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


_ALLOWED_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TEST_DATABASE_NAME = "pos_test"
_MISSING = object()


def require_safe_test_database(test_database_url: str) -> None:
    """Fail closed unless the explicit test URL targets local ``pos_test``."""
    try:
        target = make_url(test_database_url)
    except (ArgumentError, ValueError):
        raise RuntimeError("POS test database configuration is invalid") from None

    if (
        not target.drivername.startswith("postgresql")
        or target.database != _TEST_DATABASE_NAME
        or target.host not in _ALLOWED_LOCAL_HOSTS
    ):
        raise RuntimeError(
            "PostgreSQL integration tests require the local pos_test database"
        )


@contextmanager
def bind_alembic_to_test_database(test_database_url: str) -> Iterator[None]:
    """Temporarily bind online Alembic to the already-authorized test target."""
    require_safe_test_database(test_database_url)
    previous = os.environ.get("DATABASE_URL", _MISSING)
    os.environ["DATABASE_URL"] = test_database_url
    try:
        yield
    finally:
        if previous is _MISSING:
            os.environ.pop("DATABASE_URL", None)
        else:
            assert isinstance(previous, str)
            os.environ["DATABASE_URL"] = previous


def upgrade_test_database(test_database_url: str) -> None:
    """Upgrade only the explicit disposable test database to Alembic head."""
    with bind_alembic_to_test_database(test_database_url):
        command.upgrade(Config("alembic.ini"), "head")
