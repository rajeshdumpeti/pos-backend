"""Network-free staging database configuration and readiness regression tests."""

from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.config import ConfigurationError, DatabaseSettings, load_database_settings, load_settings
from app.infrastructure.database import create_database_engine
from app.infrastructure.database_readiness import check_database_readiness
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
URL = "postgresql+psycopg://test-user:test-secret@pooler.invalid:5432/postgres"
HEAD = "0008_notification_inbox"


@pytest.fixture
def database_environment(trusted_ca_file):
    return {
        "APP_ENV": "staging", "DATABASE_URL": URL,
        "DATABASE_SSLMODE": "verify-full", "DATABASE_SSLROOTCERT": str(trusted_ca_file),
    }


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("mode", [None, "disable", "allow", "prefer", "require", "verify-ca"])
def test_protected_tls_rejects_unverified_connections(database_environment, environment, mode):
    database_environment["APP_ENV"] = environment
    if mode is None:
        database_environment.pop("DATABASE_SSLMODE")
    else:
        database_environment["DATABASE_SSLMODE"] = mode
    with pytest.raises(ValueError, match="verify-full") as error:
        load_database_settings(database_environment)
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize("certificate", [None, "/missing/ca.pem", "invalid"])
def test_verified_tls_requires_valid_trusted_ca(database_environment, certificate, tmp_path):
    if certificate is None:
        database_environment.pop("DATABASE_SSLROOTCERT")
    elif certificate == "invalid":
        ca = tmp_path / "invalid.pem"
        ca.write_text("not a certificate")
        database_environment["DATABASE_SSLROOTCERT"] = str(ca)
    else:
        database_environment["DATABASE_SSLROOTCERT"] = certificate
    with pytest.raises(ValueError, match="DATABASE_SSLROOTCERT"):
        load_database_settings(database_environment)


@pytest.mark.parametrize("query", [
    "sslmode=disable", "host=other.invalid", "service=unsafe", "sslrootcert=/other.pem",
    "sslmode=verify-full&sslmode=disable",
])
def test_protected_url_cannot_override_tls(database_environment, query):
    database_environment["DATABASE_URL"] += "?" + query
    with pytest.raises(ValueError):
        load_database_settings(database_environment)


def test_tls_url_mode_is_supported(database_environment):
    database_environment.pop("DATABASE_SSLMODE")
    database_environment["DATABASE_URL"] += "?sslmode=verify-full"
    assert load_database_settings(database_environment).database_url is not None


@pytest.mark.parametrize("scheme", ["postgresql+asyncpg", "postgresql+psycopg_async"])
def test_async_urls_rejected_by_both_settings_loaders(scheme):
    for loader in (load_database_settings, load_settings):
        with pytest.raises(ConfigurationError) as error:
            loader({"DATABASE_URL": URL.replace("postgresql+psycopg", scheme)})
        assert "test-secret" not in str(error.value)


@pytest.mark.parametrize("name,value", [
    ("DATABASE_POOL_SIZE", "0"), ("DATABASE_MAX_OVERFLOW", "-1"),
    ("DATABASE_POOL_TIMEOUT", "0"), ("DATABASE_CONNECT_TIMEOUT", "0"),
    ("DATABASE_POOL_SIZE", "bad"),
])
def test_invalid_pool_configuration(name, value):
    with pytest.raises(ConfigurationError, match=name):
        load_database_settings({name: value})


def test_direct_settings_cannot_bypass_pool_validation():
    with pytest.raises(ValidationError):
        DatabaseSettings(app_env="local", database_pool_size=0)


@pytest.mark.parametrize("custom", [False, True])
def test_engine_uses_pool_and_driver_options_without_connecting(database_environment, custom):
    if custom:
        database_environment.update(DATABASE_POOL_SIZE="3", DATABASE_MAX_OVERFLOW="1",
                                    DATABASE_POOL_TIMEOUT="7", DATABASE_CONNECT_TIMEOUT="8")
    settings = load_database_settings(database_environment)
    with patch("psycopg.connect", side_effect=AssertionError("network forbidden")):
        engine = create_database_engine(settings.database_url, settings=settings)
        try:
            assert engine.pool.size() == (3 if custom else 2)
            assert engine.pool._max_overflow == (1 if custom else 0)
            assert engine.pool.timeout() == (7 if custom else 10)
            assert engine.pool._pre_ping is True
            assert engine.hide_parameters is True
            with patch.object(engine.dialect, "connect", side_effect=RuntimeError) as connect:
                with pytest.raises(RuntimeError):
                    engine.connect()
                assert connect.call_args.kwargs["connect_timeout"] == (8 if custom else 10)
                assert connect.call_args.kwargs["sslmode"] == "verify-full"
                assert connect.call_args.kwargs["sslrootcert"] == settings.database_sslrootcert
        finally:
            engine.dispose()


def test_online_alembic_uses_only_database_configuration(database_environment, monkeypatch):
    for name in tuple(__import__("os").environ):
        if name.startswith(("AUTH_", "CURRENT_")) or name in {"JWT_SECRET", "CORS_ORIGINS", "REDIS_URL"}:
            monkeypatch.delenv(name)
    for name, value in database_environment.items():
        monkeypatch.setenv(name, value)
    engine = Mock()
    engine.connect.side_effect = RuntimeError("stopped before network")
    with patch("app.config.load_settings", side_effect=AssertionError("full settings loaded")), patch(
        "app.infrastructure.database.create_database_engine", return_value=engine
    ) as create:
        with pytest.raises(RuntimeError, match="stopped before network"):
            command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    assert create.call_args.kwargs["settings"].app_env == "staging"
    engine.dispose.assert_called_once()


def test_offline_alembic_needs_no_database_configuration(monkeypatch):
    monkeypatch.setenv("APP_ENV", "invalid")
    monkeypatch.setenv("DATABASE_URL", "invalid")
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    with patch("app.config.load_database_settings", side_effect=AssertionError("settings loaded")):
        command.upgrade(config, "head", sql=True)
    assert HEAD in output.getvalue()


@pytest.mark.parametrize("revisions,ready", [
    (None, False), ([], False), (["0007_background_jobs"], False),
    (["unknown_revision"], False), ([HEAD, "unexpected_branch"], False), ([HEAD], True),
])
def test_readiness_revision_parity(revisions, ready):
    # SQLite exercises real Alembic version inspection without any network.
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    try:
        if revisions is not None:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
                for revision in revisions:
                    connection.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": revision})
        assert check_database_readiness(engine) is ready
        app = create_app(load_settings({"APP_ENV": "test"}))
        app.state.database_engine = engine
        response = TestClient(app).get("/health/ready")
        assert response.status_code == (200 if ready else 503)
        assert response.json() == {"status": "ok" if ready else "unavailable"}
    finally:
        engine.dispose()


def test_readiness_unavailable_and_liveness_independent():
    app = create_app(load_settings({"APP_ENV": "test"}))
    client = TestClient(app)
    assert client.get("/health/ready").status_code == 503
    engine = Mock()
    engine.connect.side_effect = RuntimeError("password=test-secret host=private-host")
    app.state.database_engine = engine
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("heads", [[], [HEAD, "another_head"]])
def test_readiness_rejects_invalid_repository_graph_without_connecting(heads):
    engine = Mock()
    with patch("app.infrastructure.database_readiness.ScriptDirectory") as scripts:
        scripts.return_value.get_heads.return_value = heads
        assert check_database_readiness(engine) is False
    engine.connect.assert_not_called()


def test_readiness_rejects_unexpected_connectivity_result():
    engine = Mock()
    engine.connect.return_value.__enter__ = Mock(return_value=Mock())
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = 0
    assert check_database_readiness(engine) is False


def test_app_shares_engine_and_never_migrates_or_connects_on_startup():
    settings = load_settings({"APP_ENV": "test", "DATABASE_URL": URL,
                              "DATABASE_POOL_SIZE": "3"})
    with patch("psycopg.connect", side_effect=AssertionError("network forbidden")), patch(
        "alembic.command.upgrade", side_effect=AssertionError("startup migration forbidden")
    ), patch("app.infrastructure.database.create_engine", wraps=create_engine) as create:
        app = create_app(settings)
        assert create.call_count == 1
        engine = app.state.database_engine
        assert engine.pool.size() == 3
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
