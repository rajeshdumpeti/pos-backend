"""Regression coverage for PostgreSQL test/Alembic target isolation."""

import logging
import os
from unittest.mock import patch

import pytest
from sqlalchemy import URL
from sqlalchemy.engine import make_url

from tests.postgresql_support import (
    bind_alembic_to_test_database,
    require_safe_test_database,
    upgrade_test_database,
)


def _url(database: str, password: str = "fake-password") -> str:
    return URL.create(
        "postgresql+psycopg",
        username="pos_test",
        password=password,
        host="127.0.0.1",
        database=database,
    ).render_as_string(hide_password=False)


def test_alembic_receives_test_target_and_ambient_database_cannot_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambient = _url("pos_local")
    test_target = _url("pos_test")
    observed_database = None

    def observe_upgrade(_config, _revision) -> None:
        nonlocal observed_database
        observed_database = make_url(os.environ["DATABASE_URL"]).database

    monkeypatch.setenv("DATABASE_URL", ambient)
    with patch("tests.postgresql_support.command.upgrade", observe_upgrade):
        upgrade_test_database(test_target)

    assert observed_database == "pos_test"
    assert os.environ["DATABASE_URL"] == ambient


def test_environment_is_removed_after_success_when_initially_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with bind_alembic_to_test_database(_url("pos_test")):
        assert "DATABASE_URL" in os.environ
    assert "DATABASE_URL" not in os.environ


def test_environment_is_restored_after_migration_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ambient = _url("pos_local")
    monkeypatch.setenv("DATABASE_URL", ambient)
    caplog.set_level(logging.DEBUG)
    sentinel = "must_never_appear"

    with patch(
        "tests.postgresql_support.command.upgrade",
        side_effect=RuntimeError("controlled migration failure"),
    ):
        with pytest.raises(RuntimeError, match="controlled migration failure"):
            upgrade_test_database(_url("pos_test", sentinel))

    assert os.environ["DATABASE_URL"] == ambient
    assert sentinel not in caplog.text


@pytest.mark.parametrize(
    "database_url",
    (
        _url("pos_local"),
        URL.create(
            "postgresql+psycopg",
            username="pos_test",
            password="fake-password",
            host="database.example.test",
            database="pos_test",
        ).render_as_string(hide_password=False),
    ),
)
def test_safety_guard_rejects_non_disposable_targets_without_secret_output(
    database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "must_never_appear"
    candidate = database_url.replace("fake-password", sentinel)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError) as captured:
        require_safe_test_database(candidate)

    assert sentinel not in str(captured.value)
    assert sentinel not in caplog.text
