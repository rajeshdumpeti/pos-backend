import re

from fastapi import status
from fastapi.testclient import TestClient
import pytest

from app.main import create_app


CONTROLLED_REQUEST_ID = "test-api-contracts"
REQUEST_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")


def test_request_id_is_propagated_on_success_and_errors():
    client = TestClient(create_app())
    headers = {"X-Request-ID": CONTROLLED_REQUEST_ID}

    success = client.get("/health", headers=headers)
    error = client.get("/unknown", headers=headers)

    assert success.headers["x-request-id"] == CONTROLLED_REQUEST_ID
    assert error.headers["x-request-id"] == CONTROLLED_REQUEST_ID
    assert error.json()["request_id"] == CONTROLLED_REQUEST_ID


def test_missing_or_malformed_request_ids_are_safely_replaced():
    client = TestClient(create_app())

    generated = client.get("/health").headers["x-request-id"]
    invalid_request_ids = ["unsafe request id with spaces", "a" * 65]

    assert REQUEST_ID_PATTERN.fullmatch(generated)
    for invalid_request_id in invalid_request_ids:
        replaced = client.get(
            "/unknown",
            headers={"X-Request-ID": invalid_request_id},
        )
        assert REQUEST_ID_PATTERN.fullmatch(replaced.headers["x-request-id"])
        assert replaced.json()["request_id"] == replaced.headers["x-request-id"]


def test_request_validation_uses_safe_structured_details():
    client = TestClient(create_app())
    submitted_value = "fake-sensitive-submitted-value"

    response = client.post(
        "/tasks",
        json={"id": submitted_value},
        headers={"X-Request-ID": CONTROLLED_REQUEST_ID},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {
        "code": "request_validation_failed",
        "message": "Request validation failed",
        "request_id": CONTROLLED_REQUEST_ID,
        "details": [
            {
                "field": "body.title",
                "code": "missing",
                "message": "Field required",
            }
        ],
    }
    assert submitted_value not in response.text


def test_malformed_json_does_not_echo_submitted_content():
    client = TestClient(create_app())
    submitted_value = "fake-sensitive-malformed-value"

    response = client.post(
        "/tasks",
        content=f'{{"id":"{submitted_value}",',
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": CONTROLLED_REQUEST_ID,
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["code"] == "request_validation_failed"
    assert response.json()["details"][0]["code"] == "json_invalid"
    assert response.json()["details"][0]["message"] == (
        "Request body must be valid JSON"
    )
    assert submitted_value not in response.text


def test_framework_errors_use_the_standard_contract_and_preserve_headers():
    client = TestClient(create_app())
    headers = {"X-Request-ID": CONTROLLED_REQUEST_ID}

    not_found = client.get("/not/a/route", headers=headers)
    method_not_allowed = client.put("/health", headers=headers)

    assert not_found.status_code == status.HTTP_404_NOT_FOUND
    assert not_found.json()["code"] == "route_not_found"
    assert not_found.json()["request_id"] == CONTROLLED_REQUEST_ID
    assert method_not_allowed.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert method_not_allowed.json()["code"] == "method_not_allowed"
    assert method_not_allowed.headers["allow"] == "GET"


def test_unexpected_errors_are_safe_and_remain_http_failures():
    application = create_app()
    internal_detail = "fake-private-internal-detail"

    @application.get("/_test/internal-error", include_in_schema=False)
    def raise_unexpected_error() -> None:
        raise RuntimeError(internal_detail)

    client = TestClient(application, raise_server_exceptions=False)
    response = client.get(
        "/_test/internal-error",
        headers={"X-Request-ID": CONTROLLED_REQUEST_ID},
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json() == {
        "code": "internal_server_error",
        "message": "An unexpected error occurred",
        "request_id": CONTROLLED_REQUEST_ID,
        "details": [],
    }
    assert response.headers["x-request-id"] == CONTROLLED_REQUEST_ID
    assert internal_detail not in response.text
    assert "RuntimeError" not in response.text

    with pytest.raises(RuntimeError, match=internal_detail):
        TestClient(application).get("/_test/internal-error")


def test_success_content_types_and_delete_body_follow_http_semantics():
    client = TestClient(create_app())
    item = {"id": "content-type-item", "title": "Content type"}

    health = client.get("/health")
    created = client.post("/tasks", json=item)
    deleted = client.delete("/tasks/content-type-item")

    assert health.headers["content-type"] == "application/json"
    assert created.headers["content-type"] == "application/json"
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert deleted.content == b""
    assert "content-type" not in deleted.headers


def test_openapi_has_stable_unique_operations_and_contract_schemas():
    client = TestClient(create_app())
    schema = client.get("/openapi.json").json()
    operations = [
        operation
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "delete"}
    ]
    operation_ids = [operation["operationId"] for operation in operations]

    assert operation_ids == [
        "get_health",
        "get_authentication_registration_policy",
        "create_email_registration",
        "confirm_email_verification",
        "resend_email_verification",
            "change_unverified_email",
            "create_email_session",
            "delete_all_sessions",
            "refresh_email_session",
            "get_current_session",
            "delete_current_session",
            "request_password_reset",
            "confirm_password_reset",
            "get_mock_resource",
        "create_mock_resource_item",
        "update_mock_resource_item",
        "delete_mock_resource_item",
        "reset_mock_demo",
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert schema["paths"]["/health"]["get"]["tags"] == ["Health"]
    assert schema["paths"]["/{resource}"]["get"]["tags"] == ["Mock resources"]
    assert schema["paths"]["/demo/reset"]["post"]["tags"] == ["Demo"]
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "ItemPatch" in schema["components"]["schemas"]

    validation_schema = schema["paths"]["/{resource}"]["post"]["responses"]["422"]
    assert "X-Request-ID" in validation_schema["headers"]
    assert validation_schema["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert schema["paths"]["/{resource}"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"] == {"$ref": "#/components/schemas/Item"}
    assert "content" not in schema["paths"]["/{resource}/{item_id}"]["delete"][
        "responses"
    ]["204"]
