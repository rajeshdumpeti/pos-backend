"""Read-only database and repository schema compatibility probe."""

from pathlib import Path

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text


MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"


def check_database_readiness(engine: Engine) -> bool:
    """Require connectivity and exact revision parity, including all version rows.

    No stamp, DDL, or migration execution occurs. Missing version tables return
    no current heads and therefore fail the compatibility check.
    """
    expected = set(ScriptDirectory(str(MIGRATIONS_DIRECTORY)).get_heads())
    if len(expected) != 1:
        return False
    with engine.connect() as connection:
        if connection.execute(text("SELECT 1")).scalar_one() != 1:
            return False
        current = set(MigrationContext.configure(connection).get_current_heads())
    return current == expected
