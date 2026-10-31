"""Protected strict Inbox/timezone API contracts and safe failure behavior."""

from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.config import load_settings
from app.main import create_app
from app.domain.notifications import InboxUnavailableError
from tests.notification_support import fixture
from tests.test_onboarding_api import Sessions
from tests.test_notifications import create

HEADERS = {"Authorization": "Bearer valid-token", "X-Request-ID": "inbox-test"}


def client_fixture():
    owner, account, onboarding, store, clock, plans, inbox = fixture()
    client = TestClient(
        create_app(
            load_settings({"APP_ENV": "test"}),
            email_session_service=Sessions(owner),
            onboarding_service=onboarding,
            plan_service=plans,
            notification_service=inbox,
        ),
        raise_server_exceptions=False,
    )
    return client, owner, store, clock, plans, inbox


def test_empty_timezone_owned_idempotent_and_enabled_inbox_without_os_contract():
    client, owner, store, clock, plans, inbox = client_fixture()
    for path in (
        "/api/v1/notifications?timezone=UTC",
        "/api/v1/notifications/unread-count",
    ):
        response = client.get(path, headers=HEADERS)
        assert response.status_code == 200 and response.json()["unread_count"] == 0
        assert (
            response.headers["cache-control"] == "no-store"
            and response.headers["pragma"] == "no-cache"
        )
        assert response.headers["x-request-id"] == "inbox-test"
    initial = client.put(
        "/api/v1/account/timezone",
        json={"timezone": "America/New_York"},
        headers=HEADERS,
    )
    assert initial.status_code == 200 and initial.json()["version"] == 1
    assert (
        client.put(
            "/api/v1/account/timezone",
            json={"timezone": "America/New_York"},
            headers=HEADERS,
        ).json()
        == initial.json()
    )
    assert (
        client.put(
            "/api/v1/account/timezone", json={"timezone": "UTC"}, headers=HEADERS
        ).json()["version"]
        == 2
    )
    assert not store.notifications


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "/api/v1/notifications?timezone=private-zone", None),
        ("get", "/api/v1/notifications?timezone=UTC&source=other", None),
        ("get", "/api/v1/notifications?timezone=UTC&limit=101", None),
        ("get", "/api/v1/notifications?timezone=UTC&cursor=private-cursor", None),
        ("get", "/api/v1/notifications?timezone=UTC&account_id=private", None),
        ("get", "/api/v1/notifications/unread-count?source=other", None),
        ("put", "/api/v1/account/timezone", {"timezone": "UTC-04:00"}),
        (
            "put",
            "/api/v1/account/timezone",
            {"timezone": "UTC", "account_id": "private"},
        ),
        ("post", "/api/v1/notifications/read-all", {"source": "other"}),
        ("post", "/api/v1/notifications/private-id/read", None),
    ],
)
def test_validation_safe_no_private_values_and_never_cached(method, path, payload):
    client, *_ = client_fixture()
    response = client.request(method, path, json=payload, headers=HEADERS)
    assert response.status_code == 422 and response.json()["details"] == []
    assert "private" not in response.text
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["pragma"] == "no-cache"
    )


def test_open_read_read_all_count_scope_and_missing_record_are_authoritative():
    client, owner, store, clock, plans, inbox = client_fixture()
    create(plans, owner, clock[0])
    job = next(iter(store.rows.values()))
    clock[0] = job.scheduled_at
    inbox.plan_reminder(job)
    listing = client.get("/api/v1/notifications?timezone=UTC", headers=HEADERS)
    assert listing.status_code == 200
    identifier = listing.json()["items"][0]["notification_id"]
    path = f"/api/v1/notifications/{identifier}/read"
    first = client.post(path, headers=HEADERS)
    second = client.post(path, headers=HEADERS)
    assert (
        first.status_code == 200
        and first.json() == second.json()
        and first.json()["source"] == "all"
    )
    assert (
        client.post(
            "/api/v1/notifications/read-all", json={"source": "all"}, headers=HEADERS
        ).json()["marked_read_count"]
        == 0
    )
    missing = client.post(f"/api/v1/notifications/{uuid4()}/read", headers=HEADERS)
    assert (
        missing.status_code == 404
        and missing.json()["code"] == "notification_unavailable"
    )


@pytest.mark.parametrize(
    "path", ["/api/v1/notifications?timezone=UTC", "/api/v1/notifications/unread-count"]
)
def test_bearer_is_mandatory(path):
    client, *_ = client_fixture()
    response = client.get(path)
    assert (
        response.status_code == 401 and response.headers["cache-control"] == "no-store"
    )


def test_dependency_failure_and_unexpected_errors_do_not_expose_native_details(
    monkeypatch,
):
    client, owner, store, clock, plans, inbox = client_fixture()

    def fail(*args, **kwargs):
        raise InboxUnavailableError()

    monkeypatch.setattr(inbox, "list", fail)
    response = client.get("/api/v1/notifications?timezone=UTC", headers=HEADERS)
    assert (
        response.status_code == 503
        and response.json()["code"] == "dependency_unavailable"
    )

    def crash(*args, **kwargs):
        raise RuntimeError("private-diagnostic")

    monkeypatch.setattr(inbox, "list", crash)
    response = client.get("/api/v1/notifications?timezone=UTC", headers=HEADERS)
    assert (
        response.status_code == 500
        and "private" not in response.text
        and response.headers["cache-control"] == "no-store"
    )


def test_openapi_named_closed_schemas_unique_ids_and_no_arbitrary_create():
    client, *_ = client_fixture()
    spec = client.get("/openapi.json").json()
    ids = [
        op["operationId"]
        for methods in spec["paths"].values()
        for op in methods.values()
        if isinstance(op, dict) and "operationId" in op
    ]
    assert (
        len(ids) == len(set(ids))
        and "post" not in spec["paths"]["/api/v1/notifications"]
    )
    for name, schema in spec["components"]["schemas"].items():
        if name.startswith("Inbox"):
            assert schema.get("additionalProperties") is False
    assert (
        spec["x-pos-inbox-contract-version"] == "1.0"
        and spec["x-pos-today-contract-version"] == "1.0"
        and spec["x-pos-plan-contract-version"] == "1.0"
    )


def test_inbox_requires_completed_onboarding_but_timezone_does_not_expand_it():
    from tests.test_onboarding import fixture_service

    owner, _, _, _, onboarding = fixture_service()
    _, _, _, store, clock, plans, inbox = fixture()
    store.owners.add(owner)
    client = TestClient(
        create_app(
            load_settings({"APP_ENV": "test"}),
            email_session_service=Sessions(owner),
            onboarding_service=onboarding,
            plan_service=plans,
            notification_service=inbox,
        ),
        raise_server_exceptions=False,
    )
    response = client.get("/api/v1/notifications?timezone=UTC", headers=HEADERS)
    assert (
        response.status_code == 403 and response.json()["code"] == "onboarding_required"
    )
    assert (
        client.put(
            "/api/v1/account/timezone", json={"timezone": "UTC"}, headers=HEADERS
        ).status_code
        == 200
    )
    assert onboarding.get(owner, "test").status == "not_started"
    assert (
        client.put(
            "/api/v1/account/timezone?account_id=private",
            json={"timezone": "UTC"},
            headers=HEADERS,
        ).status_code
        == 422
    )
