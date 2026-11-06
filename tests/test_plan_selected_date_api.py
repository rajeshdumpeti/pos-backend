"""HTTP, gates, safe errors and closed OpenAPI selected-date contract."""

from datetime import timedelta
from uuid import uuid4
import pytest
from app.domain.authentication import AccountStatus
from app.domain.planning import PlanIntent, PlanUnavailableError
from app.domain.plan_read import InsightComputationError
from app.application.plan_selected_date import PlanSelectedDateService
from app.application.session_lifecycle import AccessTokenExpiredError
from tests.test_planning_api import client_fixture, HEADERS, PAYLOAD
from tests.planning_support import NOW

QUERY = {"selected_date": NOW.date().isoformat(), "timezone": "America/New_York"}


def test_authorized_empty_one_multiple_and_multipage_contract():
    client, account, _, _, _, plans = client_fixture()
    empty = client.get("/api/v1/plan", params=QUERY, headers=HEADERS)
    assert empty.status_code == 200
    body = empty.json()
    assert body["summary"] == {
        "total": 0,
        "completed": 0,
        "remaining": 0,
        "scheduled_remaining": 0,
        "unscheduled_remaining": 0,
        "completion_percentage": None,
    }
    assert body["page"] == {"limit": 50, "next_cursor": None, "has_more": False}
    assert body["insight"]["code"] == "selected_date_empty" and body["items"] == []
    assert body["generated_at"] == body["freshness"]["as_of"]
    values = [
        plans.create(
            account,
            PlanIntent(
                "Synthetic item", NOW.date(), "America/New_York", is_priority=i == 2
            ),
            str(i),
            "test",
        )
        for i in range(3)
    ]
    first = client.get("/api/v1/plan", params={**QUERY, "limit": 1}, headers=HEADERS)
    value = first.json()
    assert first.status_code == 200 and len(value["items"]) == 1
    assert (
        value["summary"]["total"] == 3
        and value["summary"]["completion_percentage"] == 0
    )
    assert value["insight"]["plan_item_id"] == str(values[2].id)
    assert value["items"][0]["id"] == str(values[2].id)
    assert (
        value["items"][0]["section"] == "unscheduled_incomplete"
        and value["items"][0]["timing_state"] == "unscheduled"
    )
    all_ids = [value["items"][0]["id"]]
    while value["page"]["has_more"]:
        response = client.get(
            "/api/v1/plan",
            params={**QUERY, "limit": 1, "cursor": value["page"]["next_cursor"]},
            headers=HEADERS,
        )
        assert response.status_code == 200
        value = response.json()
        all_ids.extend(entry["id"] for entry in value["items"])
        assert value["summary"]["total"] == 3
    assert value["page"]["next_cursor"] is None and len(set(all_ids)) == 3
    for response in (empty, first, response):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-request-id"] == HEADERS["X-Request-ID"]
    assert (
        client.get(
            "/api/v1/plan", params={**QUERY, "limit": 100}, headers=HEADERS
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"selected_date": "2026-09-23"},
        {"timezone": "UTC"},
        {**QUERY, "selected_date": "2026-02-30"},
        {**QUERY, "selected_date": "20260923"},
        {**QUERY, "selected_date": "1790949600"},
        {**QUERY, "selected_date": "2026-09-23T00:00:00Z"},
        {**QUERY, "timezone": "Unknown/Private"},
        {**QUERY, "timezone": "-04:00"},
        {**QUERY, "limit": 0},
        {**QUERY, "limit": 101},
        {**QUERY, "limit": -1},
        {**QUERY, "limit": "1.5"},
        {**QUERY, "limit": "true"},
        {**QUERY, "cursor": ""},
        {**QUERY, "cursor": "private-cursor"},
        {**QUERY, "cursor": "a" * 1025},
        {**QUERY, "account_id": "private"},
        {**QUERY, "user_account_id": str(uuid4())},
        {**QUERY, "today": "private"},
    ],
)
def test_safe_query_validation(params):
    client, *_ = client_fixture()
    response = client.get("/api/v1/plan", params=params, headers=HEADERS)
    assert response.status_code == 422
    assert (
        response.json()["code"] == "request_validation_failed"
        and response.json()["details"] == []
    )
    assert "private" not in response.text.lower()
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["pragma"] == "no-cache"
    )
    assert response.json()["request_id"] == HEADERS["X-Request-ID"]


@pytest.mark.parametrize(
    "kind", ["missing", "invalid", "expired", "inactive", "unverified", "onboarding"]
)
def test_all_authentication_and_eligibility_gates(kind, monkeypatch):
    client, _, account, states, _, _ = client_fixture()
    headers = dict(HEADERS)
    status, code = 403, None
    if kind == "missing":
        headers.pop("Authorization")
        status, code = 401, "authentication_required"
    elif kind == "invalid":
        headers["Authorization"] = "Bearer invalid"
        status, code = 401, "access_token_invalid"
    elif kind == "expired":
        from tests.test_onboarding_api import Sessions

        def fail(*args):
            raise AccessTokenExpiredError("private")

        monkeypatch.setattr(Sessions, "authenticate", fail)
        status, code = 401, "access_token_expired"
    elif kind == "inactive":
        account.status = AccountStatus.DISABLED
        code = "account_unavailable"
    elif kind == "unverified":
        account.email_verified_at = None
        code = "email_verification_required"
    else:
        states.clear()
        code = "onboarding_required"
    response = client.get("/api/v1/plan", params=QUERY, headers=headers)
    assert response.status_code == status and response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"


def test_other_account_other_date_and_incompatible_cursors():
    client, account, _, _, _, plans = client_fixture()
    for i in range(2):
        plans.create(account, PlanIntent("Owned", NOW.date(), "UTC"), str(i), "test")
    foreign = plans.create(
        uuid4(), PlanIntent("Foreign", NOW.date(), "UTC"), "foreign", "test"
    )
    plans.create(
        account,
        PlanIntent("Other day", NOW.date() + timedelta(days=1), "UTC"),
        "next",
        "test",
    )
    first = client.get(
        "/api/v1/plan", params={**QUERY, "limit": 1}, headers=HEADERS
    ).json()
    assert first["summary"]["total"] == 2
    assert str(foreign.id) not in str(first)
    for params in (
        {**QUERY, "timezone": "UTC"},
        {**QUERY, "selected_date": (NOW.date() + timedelta(days=1)).isoformat()},
    ):
        response = client.get(
            "/api/v1/plan",
            params={**params, "cursor": first["page"]["next_cursor"]},
            headers=HEADERS,
        )
        assert (
            response.status_code == 422
            and first["page"]["next_cursor"] not in response.text
        )


def test_historical_completion_current_state_and_mutation_refetch():
    client, account, _, _, _, plans = client_fixture()
    yesterday = NOW.date() - timedelta(days=1)
    created = plans.create(
        account, PlanIntent("Synthetic past", yesterday, "UTC"), "past", "test"
    )
    query = {**QUERY, "selected_date": yesterday.isoformat()}
    before = client.get("/api/v1/plan", params=query, headers=HEADERS).json()
    assert before["date_relation"] == "past" and before["summary"]["remaining"] == 1
    assert "still incomplete" in before["insight"]["reason"]
    result = client.post(
        "/api/v1/plan/items/" + str(created.id) + "/complete",
        json={"expected_version": 1},
        headers=HEADERS,
    )
    assert result.status_code == 200
    after = client.get("/api/v1/plan", params=query, headers=HEADERS).json()
    assert after["summary"]["completed"] == 1 and after["items"][0][
        "completed_at"
    ].startswith(NOW.date().isoformat())
    assert (
        after["items"][0]["section"] == after["items"][0]["timing_state"] == "completed"
    )
    assert (
        "currently assigned" in after["insight"]["reason"]
        and after["summary"]["completion_percentage"] == 100
    )


def test_insight_failure_isolated_and_persistence_or_general_failure_remains_global(
    monkeypatch,
):
    client, account, _, _, _, plans = client_fixture()
    plans.create(account, PlanIntent("Synthetic", NOW.date(), "UTC"), "create", "test")

    def fail(*args):
        raise InsightComputationError("private-internal-detail")

    monkeypatch.setattr(
        "app.api.routes.planning.PlanSelectedDateService",
        lambda plans: PlanSelectedDateService(plans, insight_builder=fail),
    )
    response = client.get("/api/v1/plan", params=QUERY, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == len(body["items"]) == 1
    assert (
        body["insight"]["state"] == "unavailable" and body["insight"]["action"] is None
    )
    assert (
        body["insight"]["code"] == "insight_unavailable"
        and "private" not in response.text
    )
    plans.factory = lambda _: (_ for _ in ()).throw(
        PlanUnavailableError("private-internal-detail")
    )
    response = client.get("/api/v1/plan", params=QUERY, headers=HEADERS)
    assert (
        response.status_code == 503
        and response.json()["code"] == "dependency_unavailable"
    )
    plans.factory = lambda _: (_ for _ in ()).throw(
        RuntimeError("private-internal-detail")
    )
    response = client.get("/api/v1/plan", params=QUERY, headers=HEADERS)
    assert (
        response.status_code == 500
        and response.json()["code"] == "internal_server_error"
    )
    assert (
        "private" not in response.text
        and response.headers["cache-control"] == "no-store"
    )


def test_openapi_named_closed_contract_unique_ids_headers_and_enums():
    client, *_ = client_fixture()
    schema = client.get("/openapi.json").json()
    assert (
        schema["x-pos-plan-contract-version"]
        == schema["x-pos-today-contract-version"]
        == "1.0"
    )
    operation = schema["paths"]["/api/v1/plan"]["get"]
    assert operation["operationId"] == "get_plan_selected_date" and operation[
        "security"
    ] == [{"HTTPBearer": []}]
    assert set(operation["responses"]) == {"200", "401", "403", "422", "500", "503"}
    for response in operation["responses"].values():
        assert {"X-Request-ID", "Cache-Control", "Pragma"} <= response["headers"].keys()
    parameters = {p["name"]: p for p in operation["parameters"]}
    assert set(parameters) == {"selected_date", "timezone", "limit", "cursor"}
    assert (
        parameters["selected_date"]["required"] and parameters["timezone"]["required"]
    )
    assert (
        parameters["limit"]["schema"]["default"] == 50
        and parameters["limit"]["schema"]["maximum"] == 100
    )
    components = schema["components"]["schemas"]
    seen = set()

    def inspect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].split("/")[-1]
                if name not in seen:
                    seen.add(name)
                    inspect(components[name])
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(components["PlanSelectedDateResponse"])
    assert components["PlanTimingState"]["enum"] == [
        "ongoing",
        "past",
        "upcoming",
        "unscheduled",
        "completed",
    ]
    assert components["PlanInsightAction"]["enum"] == [
        "plan_item.create",
        "plan_item.view",
    ]
    assert len(components["PlanInsightCode"]["enum"]) == 17
    ids = [
        op["operationId"]
        for path in schema["paths"].values()
        for method, op in path.items()
        if method in {"get", "post", "patch", "delete"}
    ]
    assert len(ids) == len(set(ids))


def test_today_response_unchanged_by_plan_read():
    client, *_ = client_fixture()
    client.post("/api/v1/plan/items", headers=HEADERS, json=PAYLOAD)
    before = client.get(
        "/api/v1/today", params={"timezone": "America/New_York"}, headers=HEADERS
    ).json()
    client.get("/api/v1/plan", params=QUERY, headers=HEADERS)
    after = client.get(
        "/api/v1/today", params={"timezone": "America/New_York"}, headers=HEADERS
    ).json()
    assert before == after


def test_documented_mock_fixtures_are_closed_valid_and_current_state_only():
    import json
    from pathlib import Path
    from app.api.plan_read_schemas import PlanSelectedDateResponse

    fixtures = json.loads(Path("docs/examples/plan-v1-fixtures.json").read_text())
    assert set(fixtures) == {
        "empty_current",
        "current_ongoing_priority_page_one",
        "past_still_incomplete",
        "past_completed_later",
        "future_scheduled_priority",
        "insight_unavailable",
    }
    for body in fixtures.values():
        response = PlanSelectedDateResponse.model_validate(body)
        assert response.generated_at == response.freshness.as_of
        assert (
            response.summary.completed + response.summary.remaining
            == response.summary.total
        )
        assert (
            response.summary.scheduled_remaining
            + response.summary.unscheduled_remaining
            == response.summary.remaining
        )
    past = fixtures["past_completed_later"]
    assert past["items"][0]["completed_at"][:10] > past["selected_date"]
    assert "currently assigned" in past["insight"]["reason"]
    for filename in ("docs/plan-v1.md", "docs/frontend-plan-v1-handoff.md"):
        text = Path(filename).read_text().lower()
        assert "refetch" in text and "snapshot" in text and "currently" in text
        assert "you finished everything that day" not in text
        assert "was complete at the end" not in text
