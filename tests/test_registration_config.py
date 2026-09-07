"""Typed registration configuration and secret-isolation tests."""

from pathlib import Path

import pytest

from app.config import ConfigurationError, load_settings


DIGEST_KEY = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
OUTBOX_KEY = "eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXk"


def test_example_environment_keeps_registration_secrets_and_local_access_safe():
    values = dict(
        line.split("=", 1)
        for line in Path(".env.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    assert values["AUTH_CHALLENGE_DIGEST_KEY"] == ""
    assert values["AUTH_OUTBOX_ENCRYPTION_KEY"] == ""
    assert values["JWT_SECRET"] == ""
    assert values["AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY"] == ""
    assert values["CURRENT_TERMS_VERSION"] == ""
    assert values["CURRENT_PRIVACY_VERSION"] == ""
    assert values["AUTH_LOCAL_EMAIL_INSPECTION_ENABLED"] == "false"


def test_registration_secrets_are_masked_and_policy_is_frozen():
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_CHALLENGE_DIGEST_KEY": DIGEST_KEY,
            "AUTH_OUTBOX_ENCRYPTION_KEY": OUTBOX_KEY,
            "CURRENT_TERMS_VERSION": "terms-test-v1",
            "CURRENT_PRIVACY_VERSION": "privacy-test-v1",
        }
    )
    rendered = repr(settings)
    assert DIGEST_KEY not in rendered
    assert OUTBOX_KEY not in rendered
    assert settings.verification_code_lifetime_seconds == 600
    assert settings.verification_max_attempts == 5
    assert settings.verification_resend_cooldown_seconds == 60


def test_protected_environment_requires_authentication_secrets_without_echoing_values():
    with pytest.raises(Exception) as captured:
        load_settings(
            {
                "APP_ENV": "production",
                "CORS_ORIGINS": "https://pos.example",
            }
        )
    rendered = str(captured.value)
    assert "AUTH_CHALLENGE_DIGEST_KEY" in rendered
    assert "AUTH_OUTBOX_ENCRYPTION_KEY" in rendered

    sentinel = "must_never_appear"
    with pytest.raises(ConfigurationError) as captured:
        load_settings(
            {
                "APP_ENV": "test",
                "AUTH_CHALLENGE_DIGEST_KEY": sentinel,
            }
        )
    assert sentinel not in str(captured.value)


def test_local_email_inspection_is_rejected_outside_local_and_development():
    with pytest.raises(Exception, match="restricted to local and development"):
        load_settings(
            {
                "APP_ENV": "test",
                "AUTH_LOCAL_EMAIL_INSPECTION_ENABLED": "true",
            }
        )
