from app.config import load_settings


def test_local_settings_do_not_require_future_secrets():
    settings = load_settings({"APP_ENV": "local"})

    assert settings.app_env == "local"
    assert settings.database_url is None
    assert settings.redis_url is None
    assert settings.jwt_secret is None
    assert settings.cors_origins == ("*",)


def test_cors_origins_are_parsed_from_public_configuration():
    settings = load_settings(
        {
            "APP_ENV": "development",
            "CORS_ORIGINS": "http://localhost:8081, http://127.0.0.1:8081",
        }
    )

    assert settings.app_env == "development"
    assert settings.cors_origins == (
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    )
