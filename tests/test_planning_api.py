import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.config import load_settings
from app.main import create_app
from app.domain.authentication import AccountStatus
from app.domain.planning import PlanIntent
from app.infrastructure.planning import SafePlanDiagnostic
from tests.planning_support import fixture, NOW
from tests.test_onboarding_api import Sessions

HEADERS = {
    "Authorization": "Bearer valid-token",
    "Idempotency-Key": "safe-create",
    "X-Request-ID": "today-test",
}
PAYLOAD = {
    "title": "Synthetic plan item",
    "scheduled_for_date": "2026-09-23",
    "timezone": "America/New_York",
    "is_priority": True,
}


def client_fixture():
    account, account_state, states, onboarding, store, plans = fixture()
    client = TestClient(
        create_app(
            load_settings({"APP_ENV": "test"}),
            email_session_service=Sessions(account),
            onboarding_service=onboarding,
            plan_service=plans,
        ),
        raise_server_exceptions=False,
    )
    return client, account, account_state, states, store, plans


def test_api_create_get_update_complete_today_and_cache():
    client, *_ = client_fixture()
    created = client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD)
    assert created.status_code == 201
    value = created.json()
    assert "user_account_id" not in value
    replay = client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD)
    assert replay.json() == value
    assert (
        client.post(
            "/api/v1/plan/items",
            headers=HEADERS,
            json={**PAYLOAD, "title": "Different"},
        ).status_code
        == 409
    )
    path = "/api/v1/plan/items/" + value["id"]
    assert client.get(path, headers=HEADERS).json() == value
    updated = client.patch(
        path, headers=HEADERS, json={"expected_version": 1, "title": "Updated"}
    )
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert (
        client.patch(
            path, headers=HEADERS, json={"expected_version": 1, "title": "Stale"}
        ).status_code
        == 409
    )
    completed = client.post(
        path + "/complete", headers=HEADERS, json={"expected_version": 2}
    )
    assert completed.status_code == 200 and completed.json()[
        "completed_at"
    ] == NOW.isoformat().replace("+00:00", "Z")
    assert (
        client.post(
            path + "/complete", headers=HEADERS, json={"expected_version": 1}
        ).json()
        == completed.json()
    )
    today = client.get("/api/v1/today?timezone=America/New_York", headers=HEADERS)
    assert today.status_code == 200
    body = today.json()
    assert (
        body["daily_status"]["code"] == "complete"
        and body["score"]["percentage"] == 100
    )
    assert body["top_priorities"]["items"] == [] and len(body["wins"]["items"]) == 1
    assert body["progress"]["metrics"] == []
    for response in (created, replay, updated, completed, today):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-request-id"] == "today-test"


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": "x" * 201},
        {"timezone": "Private/Secret"},
        {"account_id": str(uuid4())},
        {"user_account_id": str(uuid4())},
        {"unexpected-secret": "Private"},
        {"scheduled_start_at": "2026-09-23T10:00:00-05:00"},
        {
            "scheduled_start_at": "2026-09-23T10:00:00-04:00",
            "scheduled_end_at": "2026-09-23T09:00:00-04:00",
        },
    ],
)
def test_validation_never_echoes_payload(changes):
    client, *_ = client_fixture()
    response = client.post(
        "/api/v1/plan/items", headers=HEADERS, json={**PAYLOAD, **changes}
    )
    assert response.status_code == 422
    assert response.json()["details"] == []
    assert "Private" not in response.text and "Synthetic plan item" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_product_gate_and_isolation():
    client, account, state, states, store, plans = client_fixture()
    created = client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD).json()
    other = plans.create(
        uuid4(),
        PlanIntent("Other", NOW.date(), "UTC"),
        "other",
        "test",
    )
    path = "/api/v1/plan/items/" + str(other.id)
    for response in (
        client.get(path, headers=HEADERS),
        client.patch(
            path, headers=HEADERS, json={"expected_version": 1, "title": "Change"}
        ),
        client.post(path + "/complete", headers=HEADERS, json={"expected_version": 1}),
    ):
        assert (
            response.status_code == 404 and response.json()["code"] == "item_not_found"
        )
    assert client.get("/api/v1/today?timezone=UTC").status_code == 401
    state.email_verified_at = None
    assert (
        client.get("/api/v1/today?timezone=UTC", headers=HEADERS).json()["code"]
        == "email_verification_required"
    )
    state.email_verified_at = NOW
    state.status = AccountStatus.DISABLED
    assert (
        client.get("/api/v1/today?timezone=UTC", headers=HEADERS).json()["code"]
        == "account_unavailable"
    )
    state.status = AccountStatus.ACTIVE
    states.clear()
    for method, path, payload in [
        ("get", "/api/v1/today?timezone=UTC", None),
        ("post", "/api/v1/plan/items", PAYLOAD),
        ("get", "/api/v1/plan/items/" + created["id"], None),
        (
            "patch",
            "/api/v1/plan/items/" + created["id"],
            {"expected_version": 1, "title": "Update"},
        ),
        (
            "post",
            "/api/v1/plan/items/" + created["id"] + "/complete",
            {"expected_version": 1},
        ),
    ]:
        response = client.request(method, path, headers=HEADERS, json=payload)
        assert (
            response.status_code == 403
            and response.json()["code"] == "onboarding_required"
        )


def test_query_validation_dependency_outage_and_unexpected_errors():
    client, _, _, _, store, plans = client_fixture()
    for query in (
        "timezone=Unknown/Place",
        "timezone=-04:00",
        "timezone=UTC&today=2026-09-23",
        "timezone=UTC&account_id=private",
    ):
        response = client.get("/api/v1/today?" + query, headers=HEADERS)
        assert response.status_code == 422 and response.json()["details"] == []
    store.fail_commit = True
    assert (
        client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD).json()["code"]
        == "dependency_unavailable"
    )
    plans.factory = lambda _: (_ for _ in ()).throw(RuntimeError("Sensitive exception"))
    response = client.get("/api/v1/today?timezone=UTC", headers=HEADERS)
    assert response.status_code == 500 and "Sensitive" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_openapi_named_closed_schemas_and_operation_ids():
    client, *_ = client_fixture()
    schema = client.get("/openapi.json").json()
    assert schema["x-pos-today-contract-version"] == "1.0"
    operations = [
        operation
        for path in schema["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "patch", "delete"}
    ]
    assert len({operation["operationId"] for operation in operations}) == len(
        operations
    )
    for operation in operations:
        if operation["operationId"] in {
            "create_plan_item",
            "get_plan_item",
            "update_plan_item",
            "complete_plan_item",
            "get_today_dashboard",
        }:
            assert operation["security"] == [{"HTTPBearer": []}]
            assert {"401", "403", "409", "422", "500", "503"} <= set(
                operation["responses"]
            )
            assert all(
                "account_id" not in parameter["name"]
                for parameter in operation.get("parameters", [])
            )
    components = schema["components"]["schemas"]
    seen = set()

    def inspect(name):
        if name in seen:
            return
        seen.add(name)
        value = components[name]
        if value.get("type") == "object":
            assert value["additionalProperties"] is False

        def visit(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    inspect(value["$ref"].split("/")[-1])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(value)

    for name in (
        "TodayResponse",
        "CreatePlanItemRequest",
        "UpdatePlanItemRequest",
        "CompletePlanItemRequest",
        "PlanItemResponse",
    ):
        inspect(name)
    assert "departure" not in json.dumps(components["TodayResponse"])


def test_diagnostics_allowlist_does_not_accept_private_values(caplog):
    caplog.set_level("INFO", logger="pos.planning.persistence")
    diagnostic = SafePlanDiagnostic()
    diagnostic.emit("plan_item_create", "safe-id", uuid4(), uuid4(), 1)
    diagnostic.emit("Private title", "safe-id", uuid4(), uuid4(), 1)
    assert len(caplog.records) == 1
    assert "title" not in caplog.records[0].__dict__
    assert "Private" not in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("scheduled_start_at", 1790949600),
        ("scheduled_start_at", "1790949600"),
        ("scheduled_start_at", "2026-09-23T10:00:00"),
        ("scheduled_for_date", 1790949600),
    ],
)
def test_timestamp_and_date_input_require_explicit_wire_formats(field, value):
    client, *_ = client_fixture()
    response = client.post(
        "/api/v1/plan/items", headers=HEADERS, json={**PAYLOAD, field: value}
    )
    assert response.status_code == 422 and response.json()["details"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": None},
        {"title": None},
        {"is_priority": None},
        {"scheduled_for_date": None},
        {"scheduled_start_at": "2026-09-23T10:00:00-05:00"},
        {
            "scheduled_start_at": "2026-09-23T10:00:00-04:00",
            "scheduled_end_at": "2026-09-23T10:00:00-04:00",
        },
        {"scheduled_start_at": "2026-09-24T10:00:00-04:00"},
    ],
)
def test_update_validates_resulting_intent(changes):
    client, *_ = client_fixture()
    value = client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD).json()
    response = client.patch(
        "/api/v1/plan/items/" + value["id"],
        headers=HEADERS,
        json={"expected_version": 1, **changes},
    )
    assert response.status_code == 422 and response.json()["details"] == []


def test_scheduled_update_clear_schedule_and_completed_edit_conflict():
    client, *_ = client_fixture()
    value = client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD).json()
    path = "/api/v1/plan/items/" + value["id"]
    response = client.patch(
        path,
        headers=HEADERS,
        json={
            "expected_version": 1,
            "scheduled_start_at": "2026-09-23T10:00:00-04:00",
            "scheduled_end_at": "2026-09-23T11:00:00-04:00",
        },
    )
    assert (
        response.status_code == 200
        and response.json()["scheduled_start_at"] == "2026-09-23T14:00:00Z"
    )
    clear = client.patch(
        path,
        headers=HEADERS,
        json={
            "expected_version": 2,
            "scheduled_start_at": None,
            "scheduled_end_at": None,
        },
    )
    assert clear.status_code == 200 and clear.json()["scheduled_start_at"] is None
    assert (
        client.post(
            path + "/complete", headers=HEADERS, json={"expected_version": 3}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            path, headers=HEADERS, json={"expected_version": 4, "title": "Reopen"}
        ).status_code
        == 409
    )
