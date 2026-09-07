from pydantic import ValidationError
import pytest

from app.config import AppEnvironment, ConfigurationError, Settings, load_settings


def test_local_settings_do_not_require_future_secrets():
    settings = load_settings({})

    assert settings.app_env is AppEnvironment.LOCAL
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.jwt_secret is None
    assert settings.cors_origins == ("*",)


@pytest.mark.parametrize(
    ("environment_name", "expected_environment"),
    [
        ("LOCAL", AppEnvironment.LOCAL),
        ("development", AppEnvironment.DEVELOPMENT),
        ("Test", AppEnvironment.TEST),
        ("staging", AppEnvironment.STAGING),
        ("production", AppEnvironment.PRODUCTION),
    ],
)
def test_supported_environment_names_are_normalized(
    environment_name: str, expected_environment: AppEnvironment
):
    environment = {
        "APP_ENV": f"  {environment_name}  ",
        "CORS_ORIGINS": "http://localhost:8081",
    }
    if expected_environment in {AppEnvironment.STAGING, AppEnvironment.PRODUCTION}:
        environment.update(
            {
                "DATABASE_URL": "postgresql://fake-user:fake-password@localhost/pos",
                "AUTH_CHALLENGE_DIGEST_KEY": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",
                "AUTH_OUTBOX_ENCRYPTION_KEY": "eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXk",
                "JWT_SECRET": "fake-jwt-secret-that-is-at-least-32-characters",
                "AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY": "enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6eno",
                "CORS_ORIGINS": "https://app.example.test",
                "AUTH_PUBLIC_ORIGIN": "https://api.example.test",
                "AUTH_RATE_LIMIT_BACKEND": "redis",
                "REDIS_URL": "redis://localhost:6379/0",
                "AUTH_RATE_LIMIT_KEY": __import__("base64").urlsafe_b64encode(bytes(range(32))).decode(),
                "AUTH_EMAIL_PROVIDER": "resend",
                "RESEND_API_KEY": "test-placeholder",
                "AUTH_EMAIL_FROM": "POS <auth@example.test>",
                "AUTH_PASSWORD_RESET_URL": "https://app.example.test/reset",
                "CURRENT_TERMS_VERSION": "terms-test-v1",
                "CURRENT_PRIVACY_VERSION": "privacy-test-v1",
            }
        )
    settings = load_settings(environment)

    assert settings.app_env is expected_environment


def test_invalid_environment_name_fails_with_actionable_error():
    with pytest.raises(ConfigurationError, match="APP_ENV must be one of") as error:
        load_settings({"APP_ENV": "preview"})

    assert "preview" not in str(error.value)


def test_cors_origins_are_parsed_trimmed_and_deduplicated():
    settings = load_settings(
        {
            "APP_ENV": "development",
            "CORS_ORIGINS": (
                "http://localhost:8081, http://127.0.0.1:8081,"
                " http://localhost:8081"
            ),
        }
    )

    assert settings.cors_origins == (
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    )


@pytest.mark.parametrize(
    "origins",
    [
        "localhost:8081",
        "http://localhost:8081/path",
        "https://user:password@example.test",
        "*,http://localhost:8081",
    ],
)
def test_malformed_cors_origins_fail_clearly(origins: str):
    with pytest.raises(ConfigurationError, match="CORS_ORIGINS"):
        load_settings({"APP_ENV": "local", "CORS_ORIGINS": origins})


@pytest.mark.parametrize("app_environment", ["staging", "production"])
def test_protected_environments_reject_wildcard_cors(app_environment: str):
    with pytest.raises(ConfigurationError, match="explicit origins"):
        load_settings({"APP_ENV": app_environment})


def test_optional_service_urls_and_jwt_secret_are_validated():
    database_url = "postgresql://fake-user:fake-password@localhost/pos"
    redis_url = "redis://:fake-password@localhost:6379/0"
    jwt_secret = "fake-jwt-secret-that-is-at-least-32-characters"
    settings = load_settings(
        {
            "APP_ENV": "test",
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            "JWT_SECRET": jwt_secret,
        }
    )

    assert settings.database_url is not None
    assert settings.redis_url is not None
    assert settings.jwt_secret is not None
    assert settings.database_url.get_secret_value() == database_url
    assert settings.redis_url.get_secret_value() == redis_url
    assert settings.jwt_secret.get_secret_value() == jwt_secret


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DATABASE_URL", "mysql://fake-password@localhost/pos"),
        ("REDIS_URL", "http://fake-password@localhost:6379"),
        ("JWT_SECRET", "too-short-fake-secret"),
    ],
)
def test_invalid_sensitive_configuration_does_not_echo_value(
    variable: str, value: str
):
    with pytest.raises(ConfigurationError) as error:
        load_settings({"APP_ENV": "test", variable: value})

    assert variable in str(error.value)
    assert value not in str(error.value)


def test_secrets_are_masked_in_settings_representations_and_serialization():
    secrets = {
        "DATABASE_URL": "postgresql://fake-user:fake-db-password@localhost/pos",
        "REDIS_URL": "redis://:fake-redis-password@localhost:6379/0",
        "JWT_SECRET": "fake-jwt-secret-that-is-at-least-32-characters",
    }
    settings = load_settings({"APP_ENV": "test", **secrets})

    rendered_settings = (
        repr(settings),
        str(settings),
        repr(settings.model_dump()),
        settings.model_dump_json(),
    )

    for secret in secrets.values():
        assert all(secret not in rendered for rendered in rendered_settings)


def test_direct_settings_construction_cannot_bypass_secret_validation():
    invalid_database_url = "mysql://fake-direct-password@localhost/pos"

    with pytest.raises(ValidationError) as error:
        Settings(
            app_env=AppEnvironment.TEST,
            database_url=invalid_database_url,
            cors_origins=("*",),
        )

    assert "DATABASE_URL" in str(error.value)
    assert invalid_database_url not in str(error.value)


def test_load_settings_calls_are_isolated(monkeypatch: pytest.MonkeyPatch):
    # The test exercises process reloads, not whichever optional integration
    # variables a full PostgreSQL-enabled invocation exported.
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
    first_settings = load_settings()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:8081")
    second_settings = load_settings()

    assert first_settings.app_env is AppEnvironment.TEST
    assert first_settings.cors_origins == ("*",)
    assert second_settings.app_env is AppEnvironment.DEVELOPMENT
    assert second_settings.cors_origins == ("http://localhost:8081",)
