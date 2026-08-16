from importlib import import_module

from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app


def test_layer_packages_are_importable():
    modules = [
        "app.api",
        "app.application",
        "app.core",
        "app.domain",
        "app.infrastructure",
    ]

    for module in modules:
        assert import_module(module) is not None


def test_application_composition_creates_isolated_state():
    first_client = TestClient(create_app())
    second_client = TestClient(create_app())
    item = {"id": "isolated", "title": "Only in one app"}

    first_client.post("/tasks", json=item)

    assert first_client.get("/tasks").json()[0]["id"] == "isolated"
    assert all(task["id"] != "isolated" for task in second_client.get("/tasks").json())


def test_composition_preserves_configured_cors_behavior():
    settings = load_settings(
        {
            "APP_ENV": "development",
            "CORS_ORIGINS": "http://localhost:8081",
        }
    )
    client = TestClient(create_app(settings))

    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:8081",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8081"
    assert "access-control-allow-credentials" not in response.headers


def test_default_composition_reloads_process_settings(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    first_app = create_app()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:8081")
    second_app = create_app()

    first_response = TestClient(first_app).options(
        "/health",
        headers={
            "Origin": "http://unconfigured.example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    second_response = TestClient(second_app).options(
        "/health",
        headers={
            "Origin": "http://localhost:8081",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert first_response.headers["access-control-allow-origin"] == "*"
    assert second_response.headers["access-control-allow-origin"] == (
        "http://localhost:8081"
    )


def test_secret_values_are_not_exposed_through_http_or_openapi():
    fake_secret = "fake-jwt-secret-that-is-at-least-32-characters"
    settings = load_settings({"APP_ENV": "test", "JWT_SECRET": fake_secret})
    client = TestClient(create_app(settings))

    responses = [
        client.get("/health"),
        client.get("/openapi.json"),
        client.get("/dashboard"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(fake_secret not in response.text for response in responses)
