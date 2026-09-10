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
    # Keep this process-settings assertion independent of optional integration
    # credentials exported by a PostgreSQL-enabled test invocation.
    for name in (
        "DATABASE_URL",
        "POS_TEST_DATABASE_URL",
        "JWT_SECRET",
        "AUTH_CHALLENGE_DIGEST_KEY",
        "AUTH_OUTBOX_ENCRYPTION_KEY",
        "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY",
        "CURRENT_TERMS_VERSION",
        "CURRENT_PRIVACY_VERSION",
        "AUTH_EMAIL_PROVIDER",
        "RESEND_API_KEY",
        "AUTH_EMAIL_FROM",
        "AUTH_PASSWORD_RESET_URL",
    ):
        monkeypatch.delenv(name, raising=False)
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


def test_database_enabled_composition_does_not_leak_into_later_settings(monkeypatch):
    """Regression for full PostgreSQL invocations contaminating process settings."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-user:fake-password@127.0.0.1/pos_test")
    monkeypatch.setenv(
        "AUTH_CHALLENGE_DIGEST_KEY",
        "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",
    )
    monkeypatch.setenv(
        "AUTH_OUTBOX_ENCRYPTION_KEY",
        "eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXk",
    )
    monkeypatch.setenv("CURRENT_TERMS_VERSION", "terms-test-v1")
    monkeypatch.setenv("CURRENT_TERMS_URL", "http://localhost/legal/terms")
    monkeypatch.setenv("CURRENT_PRIVACY_VERSION", "privacy-test-v1")
    monkeypatch.setenv("CURRENT_PRIVACY_URL", "http://localhost/legal/privacy")
    monkeypatch.setenv("AUTH_PASSWORD_RESET_URL", "https://app.example.test/reset")
    create_app()

    for name in (
        "DATABASE_URL", "AUTH_CHALLENGE_DIGEST_KEY", "AUTH_OUTBOX_ENCRYPTION_KEY",
        "CURRENT_TERMS_VERSION", "CURRENT_TERMS_URL", "CURRENT_PRIVACY_VERSION",
        "CURRENT_PRIVACY_URL", "AUTH_LEGAL_CONSENT_MODE", "AUTH_PASSWORD_RESET_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = load_settings()
    assert settings.database_url is None
    assert settings.auth_password_reset_url is None
    assert settings.cors_origins == ("*",)


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
