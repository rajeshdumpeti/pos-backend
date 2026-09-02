"""Offline-only tests for canonical metadata and Alembic migration governance."""

from ast import Constant, Expr, FunctionDef, Pass, parse
from io import StringIO
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from app.config import AppEnvironment, Settings
from app.infrastructure.relational import (
    POSTGRESQL_IDENTIFIER_MAX_BYTES,
    RELATIONAL_METADATA,
    RELATIONAL_NAMING_CONVENTION,
    RelationalBase,
    bounded_identifier,
)
from migrations.runtime import require_database_url
from migrations.metadata import target_metadata


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = ROOT / "alembic.ini"
BASELINE_PATH = ROOT / "migrations/versions/0001_migration_foundation.py"


def _config(output: StringIO | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG))
    if output is not None:
        config.output_buffer = output
    return config


def test_metadata_import_does_not_construct_an_engine():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from unittest.mock import patch; "
                "guard = patch('sqlalchemy.create_engine', "
                "side_effect=AssertionError('metadata import connected')); "
                "guard.start(); import app.infrastructure.relational; guard.stop()"
            ),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_declarative_base_uses_canonical_authentication_metadata():
    assert RelationalBase.metadata is RELATIONAL_METADATA
    assert set(RELATIONAL_METADATA.tables) == {
        "authentication_attempt_states",
        "authentication_email_outbox",
        "email_identities",
        "email_verification_challenges",
        "external_provider_identities",
        "password_reset_challenges",
        "refresh_rotation_retry_receipts",
        "refresh_sessions",
        "security_events",
        "user_accounts",
    }


def test_naming_convention_matches_relational_standards():
    metadata = MetaData(naming_convention=RELATIONAL_NAMING_CONVENTION)
    accounts = Table(
        "user_accounts",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email_normalized", String, nullable=False),
        UniqueConstraint("email_normalized"),
        CheckConstraint("id > 0", info={"purpose": "id_positive"}),
    )
    sessions = Table(
        "refresh_sessions",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_account_id", ForeignKey("user_accounts.id"), nullable=False),
        Column("expires_at", String, nullable=False),
    )
    expiration_index = Index(None, sessions.c.expires_at)
    exclusion = ExcludeConstraint(
        (sessions.c.id, "="),
        info={"purpose": "one_active_identifier"},
    )
    sessions.append_constraint(exclusion)

    assert accounts.primary_key.name == "pk_user_accounts"
    assert next(iter(accounts.constraints - {accounts.primary_key})).name in {
        "uq_user_accounts__email_normalized",
        "ck_user_accounts__id_positive",
    }
    names = {constraint.name for constraint in accounts.constraints}
    assert "uq_user_accounts__email_normalized" in names
    assert "ck_user_accounts__id_positive" in names
    assert next(iter(sessions.foreign_key_constraints)).name == (
        "fk_refresh_sessions__user_account_id__user_accounts"
    )
    assert expiration_index.name == "ix_refresh_sessions__expires_at"
    assert exclusion.name == "ex_refresh_sessions__one_active_identifier"


def test_long_names_use_stable_sha256_shortening():
    long_name = "ix_security_events__" + "request_user_time_lookup_" * 4

    first = bounded_identifier(long_name)
    second = bounded_identifier(long_name)

    assert first == second
    assert len(first.encode("ascii")) <= POSTGRESQL_IDENTIFIER_MAX_BYTES
    assert first.endswith("__b479741b06")


def test_alembic_configuration_and_single_baseline_head_load():
    config = _config()
    script = ScriptDirectory.from_config(config)

    assert Path(script.dir).resolve() == (ROOT / "migrations").resolve()
    assert script.get_heads() == ["0003_registration_flow"]
    revision = script.get_revision("0001_migration_foundation")
    assert revision is not None
    assert revision.down_revision is None


def test_baseline_upgrade_and_downgrade_are_no_ops():
    tree = parse(BASELINE_PATH.read_text())
    functions = {
        node.name: node for node in tree.body if isinstance(node, FunctionDef)
    }

    assert set(functions) >= {"upgrade", "downgrade"}
    for function_name in ("upgrade", "downgrade"):
        function = functions[function_name]
        executable = [
            node
            for node in function.body
            if not (
                isinstance(node, Expr)
                and isinstance(node.value, Constant)
                and isinstance(node.value.value, str)
            )
        ]
        assert len(executable) == 1
        assert isinstance(executable[0], Pass)


def test_offline_upgrade_and_downgrade_generate_without_engine():
    upgrade_output = StringIO()
    downgrade_output = StringIO()

    with patch(
        "app.infrastructure.database.create_database_engine",
        side_effect=AssertionError("offline migration attempted engine construction"),
    ):
        command.upgrade(_config(upgrade_output), "head", sql=True)
        command.downgrade(_config(downgrade_output), "head:base", sql=True)

    assert "0001_migration_foundation" in upgrade_output.getvalue()
    assert "0001_migration_foundation" in downgrade_output.getvalue()
    assert "0002_auth_persistence" in upgrade_output.getvalue()
    assert "0003_registration_flow" in upgrade_output.getvalue()


def test_online_configuration_requires_database_url_without_exposing_values():
    settings = Settings(
        app_env=AppEnvironment.LOCAL,
        database_url=None,
        redis_url=None,
        jwt_secret=None,
        cors_origins=("*",),
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        require_database_url(settings)


def test_online_configuration_preserves_masked_database_value():
    database_url = SecretStr("postgresql+psycopg://placeholder.invalid/example")
    settings = Settings(
        app_env=AppEnvironment.TEST,
        database_url=database_url,
        redis_url=None,
        jwt_secret=None,
        cors_origins=("*",),
    )

    assert require_database_url(settings) is database_url


def test_migration_environment_targets_canonical_metadata():
    assert target_metadata is RELATIONAL_METADATA
