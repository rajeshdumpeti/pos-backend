from fastapi.testclient import TestClient
from httpx import Response

from app.main import app, create_app


MOCK_RESOURCES = [
    "dashboard",
    "timeline",
    "routines",
    "nutrition",
    "weight",
    "workouts",
    "money",
    "tasks",
    "focus",
    "reports",
    "notifications",
    "profile",
    "settings",
]
REQUEST_ID = "test-api-errors"


def assert_error_response(
    response: Response, status_code: int, code: str, message: str
) -> None:
    assert response.status_code == status_code
    assert response.headers["x-request-id"] == REQUEST_ID
    assert response.json() == {
        "code": code,
        "message": message,
        "request_id": REQUEST_ID,
        "details": [],
    }


def test_application_import_exposes_existing_metadata():
    assert app.title == "POS Local Mock API"
    assert app.version == "0.1.0"


def test_health():
    client = TestClient(create_app())

    assert client.get("/health").json() == {"status": "ok"}


def test_all_mock_resources_and_search_behavior_are_preserved():
    client = TestClient(create_app())

    for resource in MOCK_RESOURCES:
        assert client.get(f"/{resource}").status_code == 200

    assert client.get("/search").json() == []
    assert_error_response(
        client.get("/unknown", headers={"X-Request-ID": REQUEST_ID}),
        404,
        "resource_not_found",
        "Unknown resource",
    )


def test_list_resource_mutations_and_reset_are_preserved():
    client = TestClient(create_app())
    item = {"id": "t-new", "title": "Review architecture", "meta": "Today", "done": False}

    assert client.post("/tasks", json=item).json() == item
    assert client.get("/tasks").json()[0] == item

    updated = client.patch("/tasks/t-new", json={"done": True})
    assert updated.status_code == 200
    assert updated.json() == {**item, "done": True}

    deleted = client.delete("/tasks/t-new")
    assert deleted.status_code == 204
    assert all(task["id"] != "t-new" for task in client.get("/tasks").json())

    client.post("/tasks", json=item)
    assert client.post("/demo/reset").json() == {"status": "reset"}
    assert all(task["id"] != "t-new" for task in client.get("/tasks").json())


def test_existing_mutation_errors_use_the_standard_contract():
    client = TestClient(create_app())
    headers = {"X-Request-ID": REQUEST_ID}

    item = {"id": "new", "title": "Not accepted"}
    assert_error_response(
        client.post("/dashboard", json=item, headers=headers),
        400,
        "resource_operation_not_supported",
        "Resource does not accept items",
    )
    assert_error_response(
        client.patch("/dashboard/new", json={"done": True}, headers=headers),
        400,
        "resource_operation_not_supported",
        "Resource is not a list",
    )
    assert_error_response(
        client.patch("/tasks/missing", json={"done": True}, headers=headers),
        404,
        "item_not_found",
        "Item not found",
    )
