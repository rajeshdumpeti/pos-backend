from types import SimpleNamespace

from fastapi import Depends
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_onboarding import NOW, Repository, Unit, fixture_service
from app.api.routes.onboarding import require_completed_onboarding
from app.application.onboarding import (
    OnboardingDependencyUnavailableError,
    OnboardingService,
    OnboardingUpdate,
)
from app.api.onboarding_schemas import OnboardingResponse
from pydantic import ValidationError
import pytest


class Sessions:
    def __init__(self, account_id):
        self.account_id = account_id

    def authenticate(self, token, request_id):
        if token != "valid-token":
            from app.application.session_lifecycle import AccessTokenInvalidError

            raise AccessTokenInvalidError("invalid")
        return SimpleNamespace(account_id=self.account_id), SimpleNamespace()


def client_fixture():
    account_id, _, _, _, service = fixture_service()
    return TestClient(
        create_app(
            email_session_service=Sessions(account_id), onboarding_service=service
        )
    )


def test_onboarding_requires_authentication_and_returns_virtual_state():
    client = client_fixture()
    unauthorized = client.get("/api/v1/onboarding")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "authentication_required"
    assert unauthorized.headers["cache-control"] == "no-store"
    assert unauthorized.json()["request_id"] == unauthorized.headers["x-request-id"]
    response = client.get(
        "/api/v1/onboarding", headers={"Authorization": "Bearer valid-token"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_started"
    assert response.json()["version"] == 0
    assert response.json()["completed_at"] is None
    assert response.json()["unit_preference"] == {
        "value": None,
        "confirmed": False,
    }
    assert response.json()["currency_preference"] == {
        "value": None,
        "confirmed": False,
    }
    schema = client.get("/openapi.json").json()
    required = schema["components"]["schemas"]["OnboardingResponse"]["required"]
    assert set(response.json()) == set(required)
    assert response.json()["configuration"]["routine_creation_supported"] is False
    assert response.headers["cache-control"] == "no-store"


def test_onboarding_api_rejects_unverified_and_disabled_accounts():
    account_id, account, _, _, service = fixture_service()
    client = TestClient(
        create_app(
            email_session_service=Sessions(account_id), onboarding_service=service
        )
    )
    headers = {"Authorization": "Bearer valid-token"}
    account.email_verified_at = None
    unverified = client.get("/api/v1/onboarding", headers=headers)
    assert unverified.status_code == 403
    assert unverified.json()["code"] == "email_verification_required"
    from app.domain.authentication import AccountStatus

    account.email_verified_at = NOW
    account.status = AccountStatus.DISABLED
    disabled = client.get("/api/v1/onboarding", headers=headers)
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "account_unavailable"


def test_patch_complete_conflict_and_no_tokens():
    client = client_fixture()
    headers = {"Authorization": "Bearer valid-token"}
    initial = client.get("/api/v1/onboarding", headers=headers)
    updated = client.patch(
        "/api/v1/onboarding",
        headers=headers,
        json={
            "expected_version": 0,
            "priority_life_areas": ["focus", "health", "focus"],
            "unit_system": "metric",
            "unit_system_confirmed": True,
            "currency": "USD",
            "currency_confirmed": True,
            "goals": {
                "daily_steps": 8000,
                "budget_target": {"amount": "20.00", "currency": "USD"},
            },
        },
    )
    assert updated.status_code == 200
    assert updated.json()["priority_life_areas"] == ["health", "focus"]
    conflict = client.patch(
        "/api/v1/onboarding",
        headers=headers,
        json={"expected_version": 0, "priority_life_areas": ["money"]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "onboarding_state_conflict"
    completed = client.post(
        "/api/v1/onboarding/complete", headers=headers, json={"expected_version": 1}
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert (
        "access_token" not in completed.text and "refresh_token" not in completed.text
    )
    assert set(initial.json()) == set(updated.json()) == set(completed.json())


def test_strict_validation_rejects_unknowns_non_finite_and_empty_patch():
    client = client_fixture()
    headers = {"Authorization": "Bearer valid-token"}
    cases = [
        {"expected_version": 0},
        {"expected_version": 0, "priority_life_areas": ["career"]},
        {"expected_version": 0, "currency": "usd"},
        {"expected_version": 0, "goals": {"unknown": 1}},
        {"expected_version": 0, "goals": {"workouts_per_week": 8}},
        {"expected_version": 0, "screen_2_seen": True},
        {"expected_version": 0, "account_id": "00000000-0000-0000-0000-000000000000"},
    ]
    for payload in cases:
        response = client.patch("/api/v1/onboarding", headers=headers, json=payload)
        assert response.status_code == 422
        assert response.json()["code"] == "request_validation_failed"

    private_value = "private-health-value-must-not-echo"
    response = client.patch(
        "/api/v1/onboarding",
        headers=headers,
        json={"expected_version": 0, "goals": {"unknown": private_value}},
    )
    assert private_value not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_explicit_route_gate_rejects_incomplete_and_allows_completed():
    account_id, _, _, _, service = fixture_service()
    sessions = Sessions(account_id)
    application = create_app(email_session_service=sessions, onboarding_service=service)

    @application.get("/_test/requires-onboarding", include_in_schema=False)
    def gated(_state=Depends(require_completed_onboarding(service, sessions))):
        return {"allowed": True}

    client = TestClient(application)
    headers = {"Authorization": "Bearer valid-token"}
    rejected = client.get("/_test/requires-onboarding", headers=headers)
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "onboarding_required"
    service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["health"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
            },
        ),
        "request",
    )
    service.complete(account_id, 1, "request")
    assert client.get("/_test/requires-onboarding", headers=headers).json() == {
        "allowed": True
    }


def test_route_gate_fails_safely_for_corrupt_persisted_state():
    account_id, account, states, _, _ = fixture_service()

    class CorruptRepository(Repository):
        def get(self, account_id):
            raise OnboardingDependencyUnavailableError(
                "Persisted onboarding state is invalid"
            )

    class CorruptUnit(Unit):
        def __init__(self):
            super().__init__(states, account)
            self.onboarding = CorruptRepository(states)

    service = OnboardingService(lambda _request_id: CorruptUnit(), clock=lambda: NOW)
    sessions = Sessions(account_id)
    application = create_app(email_session_service=sessions, onboarding_service=service)

    @application.get("/_test/requires-valid-onboarding", include_in_schema=False)
    def gated(_state=Depends(require_completed_onboarding(service, sessions))):
        return {"allowed": True}

    response = TestClient(application).get(
        "/_test/requires-valid-onboarding",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert "Persisted" not in response.text


def test_onboarding_response_models_reject_unexpected_internal_properties():
    with pytest.raises(ValidationError):
        OnboardingResponse.model_validate(
            {
                "status": "not_started",
                "contract_version": "1.0",
                "version": 0,
                "priority_life_areas": [],
                "unit_preference": {"value": None, "confirmed": False},
                "currency_preference": {"value": None, "confirmed": False},
                "goals": {},
                "integration_preferences": {},
                "notification_preferences": {},
                "requirements": {
                    "priority_life_areas_satisfied": False,
                    "unit_system_satisfied": False,
                    "currency_satisfied": False,
                    "ready_to_complete": False,
                },
                "recommended_next_action": "select_priority_life_areas",
                "completed_at": None,
                "configuration": {},
                "unexpected": True,
            }
        )
