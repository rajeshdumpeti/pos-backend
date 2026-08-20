from collections.abc import Callable
from typing import Any, cast

from fastapi import status
from fastapi.testclient import TestClient
from pydantic import SecretStr
import psycopg
import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.application import PersistenceConfigurationError, PersistenceUnavailableError
from app.config import load_settings
from app.infrastructure import (
    InMemoryResourceStore,
    SqlAlchemyUnitOfWork,
    check_database_connectivity,
    create_database_engine,
    create_session_factory,
    dispose_database_engine,
)
from app.main import create_app


FAKE_DATABASE_URL = (
    "postgresql+psycopg://fake-user:fake-password@localhost:5432/fake-pos"
)


class RecordingSession:
    def __init__(self, *, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        if self.fail_commit:
            raise SQLAlchemyError("driver detail with fake-password")

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1


class ControlledResult:
    def scalar_one(self) -> int:
        return 1


class ControlledConnection:
    def __init__(self, error: SQLAlchemyError | None = None) -> None:
        self.error = error
        self.statement = ""
        self.closed = False

    def __enter__(self) -> "ControlledConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def execute(self, statement: object) -> ControlledResult:
        self.statement = str(statement)
        if self.error is not None:
            raise self.error
        return ControlledResult()


class ControlledEngine:
    def __init__(self, connection: ControlledConnection) -> None:
        self.connection = connection
        self.connect_count = 0
        self.dispose_count = 0

    def connect(self) -> ControlledConnection:
        self.connect_count += 1
        return self.connection

    def dispose(self) -> None:
        self.dispose_count += 1


def _sqlalchemy_unit_of_work(
    session_factory: Callable[[], RecordingSession],
) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(
        cast(Any, session_factory),
        lambda _session: InMemoryResourceStore(),
    )


def test_engine_construction_uses_psycopg_without_connecting_and_masks_secrets(
    monkeypatch: pytest.MonkeyPatch,
):
    def reject_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Engine construction attempted a connection")

    monkeypatch.setattr(psycopg, "connect", reject_connection)

    engine = create_database_engine(SecretStr(FAKE_DATABASE_URL))
    rendered = (repr(engine), repr(engine.url), str(engine.url))

    assert engine.url.drivername == "postgresql+psycopg"
    assert all("fake-password" not in value for value in rendered)
    assert all(FAKE_DATABASE_URL not in value for value in rendered)
    dispose_database_engine(engine)


def test_plain_postgresql_url_is_normalized_to_the_approved_sync_driver():
    engine = create_database_engine(
        SecretStr("postgresql://fake-user:fake-password@localhost/fake-pos")
    )

    assert engine.url.drivername == "postgresql+psycopg"
    dispose_database_engine(engine)


def test_async_driver_is_rejected_without_exposing_configuration():
    configured_value = "postgresql+asyncpg://fake-user:fake-password@localhost/fake"

    with pytest.raises(PersistenceConfigurationError) as error:
        create_database_engine(SecretStr(configured_value))

    assert configured_value not in str(error.value)
    assert "fake-password" not in str(error.value)


def test_session_factory_creates_isolated_sessions_without_connecting():
    engine = create_database_engine(SecretStr(FAKE_DATABASE_URL))
    factory = create_session_factory(engine)

    first_session = factory()
    second_session = factory()

    assert first_session is not second_session
    assert first_session.get_bind() is engine
    assert second_session.get_bind() is engine
    first_session.close()
    second_session.close()
    dispose_database_engine(engine)


def test_sqlalchemy_unit_of_work_commits_once_and_closes_on_success():
    session = RecordingSession()
    unit_of_work = _sqlalchemy_unit_of_work(lambda: session)

    with unit_of_work as active_unit_of_work:
        assert isinstance(active_unit_of_work.resources, InMemoryResourceStore)
        active_unit_of_work.commit()

    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert session.close_count == 1


def test_sqlalchemy_unit_of_work_rolls_back_and_closes_on_failure():
    session = RecordingSession()
    unit_of_work = _sqlalchemy_unit_of_work(lambda: session)

    with pytest.raises(ValueError, match="application failure"):
        with unit_of_work:
            raise ValueError("application failure")

    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert session.close_count == 1


def test_failed_commit_is_translated_rolled_back_closed_and_secret_safe():
    session = RecordingSession(fail_commit=True)
    unit_of_work = _sqlalchemy_unit_of_work(lambda: session)

    with pytest.raises(PersistenceUnavailableError) as error:
        with unit_of_work:
            unit_of_work.commit()

    assert session.commit_count == 1
    assert session.rollback_count == 1
    assert session.close_count == 1
    assert "fake-password" not in str(error.value)
    assert "SQLAlchemy" not in str(error.value)


def test_each_sqlalchemy_unit_of_work_acquires_a_new_session():
    sessions: list[RecordingSession] = []

    def session_factory() -> RecordingSession:
        session = RecordingSession()
        sessions.append(session)
        return session

    for _index in range(2):
        with _sqlalchemy_unit_of_work(session_factory) as unit_of_work:
            unit_of_work.commit()

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert all(session.close_count == 1 for session in sessions)


def test_connectivity_check_executes_select_one_and_closes_the_connection():
    connection = ControlledConnection()
    engine = ControlledEngine(connection)

    check_database_connectivity(cast(Engine, engine))

    assert engine.connect_count == 1
    assert connection.statement == "SELECT 1"
    assert connection.closed is True


def test_connectivity_failure_is_actionable_and_does_not_leak_driver_details():
    connection = ControlledConnection(
        SQLAlchemyError("host=fake-host password=fake-password")
    )
    engine = ControlledEngine(connection)

    with pytest.raises(PersistenceUnavailableError) as error:
        check_database_connectivity(cast(Engine, engine))

    assert connection.closed is True
    assert "connectivity check failed" in str(error.value).lower()
    assert "fake-host" not in str(error.value)
    assert "fake-password" not in str(error.value)


def test_engine_disposal_is_explicit_and_testable():
    engine = ControlledEngine(ControlledConnection())

    dispose_database_engine(cast(Engine, engine))

    assert engine.dispose_count == 1


def test_default_app_ignores_optional_database_configuration_and_does_not_connect():
    settings = load_settings(
        {
            "APP_ENV": "test",
            "DATABASE_URL": FAKE_DATABASE_URL,
        }
    )

    response = TestClient(create_app(settings)).get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


def test_injected_persistence_failure_uses_safe_existing_api_error_contract():
    internal_detail = "SQL SELECT secret_table password=fake-password"

    class FailingUnitOfWork:
        def __enter__(self) -> Any:
            raise PersistenceUnavailableError(internal_detail)

    application = create_app(
        resource_unit_of_work_factory=lambda: cast(Any, FailingUnitOfWork())
    )
    client = TestClient(application, raise_server_exceptions=False)

    response = client.get(
        "/dashboard",
        headers={"X-Request-ID": "data-access-test"},
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json() == {
        "code": "internal_server_error",
        "message": "An unexpected error occurred",
        "request_id": "data-access-test",
        "details": [],
    }
    assert response.headers["x-request-id"] == "data-access-test"
    assert internal_detail not in response.text
    assert "fake-password" not in response.text
