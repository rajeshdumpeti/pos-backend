"""HTTP contract tests for sign-in and session lifecycle routes."""

from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

from fastapi.testclient import TestClient

from app.application.session_lifecycle import (
    CurrentSessionResult,
    InvalidCredentialsError,
    SessionTokenResult,
)
from app.config import AppEnvironment, Settings
from app.main import create_app


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class StubSessionService:
    def __init__(self, error=None) -> None:
        self.error = error
        self.last = None

    def sign_in(self, command):
        self.last = command
        if self.error:
            raise self.error
        return self.tokens()

    def refresh(self, command):
        self.last = command
        return self.tokens()

    def current(self, access_token, request_id):
        self.last = (access_token, request_id)
        return CurrentSessionResult(
            UUID("00000000-0000-4000-8000-000000000001"),
            UUID("00000000-0000-4000-8000-000000000002"),
            "POS Tester",
            True,
        )

    def logout_current(self, access_token, request_id):
        self.last = (access_token, request_id)

    def logout_all(self, access_token, request_id):
        self.last = (access_token, request_id)

    @staticmethod
    def tokens():
        return SessionTokenResult(
            "access-value",
            "refresh-value",
            "Bearer",
            NOW + timedelta(minutes=10),
            NOW + timedelta(days=7),
            UUID("00000000-0000-4000-8000-000000000001"),
            UUID("00000000-0000-4000-8000-000000000002"),
            True,
        )


def client(service=None):
    return TestClient(
        create_app(
            app_settings=Settings(app_env=AppEnvironment.TEST, cors_origins=("*",)),
            email_session_service=service,
        )
    )


def test_sign_in_contract_is_no_store_and_enumeration_safe():
    service = StubSessionService()
    response = client(service).post(
        "/api/v1/auth/sessions",
        json={"email": "person@example.test", "password": "private", "remember_me": True},
        headers={"X-Request-ID": "sign-in-request"},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert service.last.remember_me is True
    assert service.last.request_id == "sign-in-request"

    failed = client(StubSessionService(InvalidCredentialsError())).post(
        "/api/v1/auth/sessions",
        json={"email": "unknown@example.test", "password": "must_never_appear"},
    )
    assert failed.status_code == 401
    assert failed.json()["code"] == "invalid_credentials"
    assert "must_never_appear" not in failed.text


def test_refresh_requires_narrow_idempotency_key_and_returns_no_store():
    service = StubSessionService()
    missing = client(service).post(
        "/api/v1/auth/sessions/refresh", json={"refresh_token": "r" * 40}
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "request_validation_failed"
    response = client(service).post(
        "/api/v1/auth/sessions/refresh",
        json={"refresh_token": "r" * 40},
        headers={"Idempotency-Key": "rotation-1"},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert service.last.refresh_request_id == "rotation-1"


def test_current_and_logout_contracts_use_bearer_authentication():
    service = StubSessionService()
    api = client(service)
    current = api.get("/api/v1/auth/session", headers={"Authorization": "Bearer access-value"})
    assert current.status_code == 200
    assert current.json()["display_name"] == "POS Tester"
    assert api.delete("/api/v1/auth/session", headers={"Authorization": "Bearer access-value"}).status_code == 204
    assert api.delete("/api/v1/auth/sessions", headers={"Authorization": "Bearer access-value"}).status_code == 204


def test_unconfigured_session_service_is_safe_503():
    response = client().post(
        "/api/v1/auth/sessions",
        json={"email": "person@example.test", "password": "must_never_appear"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert "must_never_appear" not in response.text


def test_unconfigured_session_emits_one_safe_diagnostic(caplog):
    caplog.set_level(logging.WARNING, logger="pos.authentication.registration")
    response = client().get(
        "/api/v1/auth/session",
        headers={"Authorization": "Bearer must_never_appear", "X-Request-ID": "safe-session-request"},
    )

    assert response.status_code == 503
    records = [record for record in caplog.records if record.message == "authentication_configuration_unavailable"]
    assert len(records) == 1
    assert records[0].operation == "current_session"
    assert records[0].request_id == "safe-session-request"
    assert "must_never_appear" not in caplog.text
