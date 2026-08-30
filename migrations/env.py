"""Alembic environment for POS Backend."""

from logging.config import fileConfig

from alembic import context

from app.config import load_settings
from app.infrastructure.database import (
    create_database_engine,
    dispose_database_engine,
)
from migrations.metadata import target_metadata
from migrations.runtime import require_database_url


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

def _common_configuration() -> dict[str, object]:
    return {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "transaction_per_migration": True,
    }


def run_migrations_offline() -> None:
    """Generate PostgreSQL SQL without reading configuration or connecting."""
    context.configure(
        dialect_name="postgresql",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_configuration(),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through the existing secret-safe engine boundary."""
    settings = load_settings()
    database_url = require_database_url(settings)
    engine = create_database_engine(database_url)

    try:
        with engine.connect() as connection:
            context.configure(connection=connection, **_common_configuration())
            with context.begin_transaction():
                context.run_migrations()
    finally:
        dispose_database_engine(engine)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
