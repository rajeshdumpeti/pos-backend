"""Versioned registration API and OpenAPI contract tests."""

from datetime import UTC, datetime, timedelta
import logging
import traceback
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.application.email_registration import (
    AccountAlreadyExistsError,
    EmailVerifiedResult,
    PasswordRejectedError,
    PendingVerificationResult,
    RegistrationDependencyUnavailableError,
    ResendCooldownActiveError,
    VerificationAttemptsExceededError,
    VerificationCodeExpiredError,
    VerificationCodeInvalidError,
    VerificationFlowExpiredError,
    VerificationFlowInvalidError,
)
from app.config import AppEnvironment, Settings
from app.main import create_app


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class StubRegistrationService:
    def __init__(self, error=None) -> None:
        self.error = error
        self.last_command = None

    def _raise_or_record(self, command):
        self.last_command = command
        if self.error is not None:
            raise self.error

    def register(self, command):
        self._raise_or_record(command)
        return PendingVerificationResult(
            status="registration_pending",
            flow_token="opaque-flow-token",
            masked_email="r***@example.test",
            verification_expires_at=NOW + timedelta(minutes=10),
            flow_expires_at=NOW + timedelta(minutes=30),
            resend_available_at=NOW + timedelta(seconds=60),
        )

    def confirm(self, command):
        self._raise_or_record(command)
        return EmailVerifiedResult(
            status="email_verified",
            account_id=uuid4(),
            onboarding_required=True,
            session=None,
            next_action="await_session_establishment",
        )

    def resend(self, command):
        return self.register(command)

    def change_email(self, command):
        return self.register(command)


def _settings() -> Settings:
    return Settings(app_env=AppEnvironment.TEST, cors_origins=("*",))


def _registration_payload() -> dict:
    return {
        "display_name": "POS Tester",
        "email": "tester@example.test",
        "password": "a private test password",
        "terms_accepted": True,
        "privacy_accepted": True,
        "terms_version": "terms-test-v1",
        "privacy_version": "privacy-test-v1",
    }


def test_registration_returns_202_masked_destination_timing_and_request_id():
    service = StubRegistrationService()
    client = TestClient(
        create_app(app_settings=_settings(), email_registration_service=service)
    )

    response = client.post(
        "/api/v1/auth/registrations",
        json=_registration_payload(),
        headers={"X-Request-ID": "registration-contract"},
    )

    assert response.status_code == 202
    assert response.headers["X-Request-ID"] == "registration-contract"
    assert response.json()["status"] == "registration_pending"
    assert response.json()["masked_email"] == "r***@example.test"
    assert service.last_command.request_id == "registration-contract"


def test_confirmation_returns_201_without_a_session():
    client = TestClient(
        create_app(
            app_settings=_settings(),
            email_registration_service=StubRegistrationService(),
        )
    )
    response = client.post(
        "/api/v1/auth/email-verifications/confirm",
        json={"flow_token": "v1." + "f" * 43, "code": "123456"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "email_verified"
    assert response.json()["session"] is None
    assert response.json()["next_action"] == "await_session_establishment"


def test_password_rejection_and_validation_never_echo_sensitive_input():
    secret = "must_not_be_echoed_password"
    client = TestClient(
        create_app(
            app_settings=_settings(),
            email_registration_service=StubRegistrationService(
                PasswordRejectedError("Password was rejected")
            ),
        )
    )
    payload = _registration_payload()
    payload["password"] = secret
    response = client.post("/api/v1/auth/registrations", json=payload)

    assert response.status_code == 400
    assert response.json()["code"] == "password_rejected"
    assert secret not in response.text

    malformed = client.post(
        "/api/v1/auth/registrations",
        json={"password": secret, "unexpected": secret},
    )
    assert malformed.status_code == 422
    assert secret not in malformed.text


def test_cooldown_and_existing_account_errors_have_stable_action_metadata():
    cooldown = StubRegistrationService(
        ResendCooldownActiveError(NOW + timedelta(seconds=42), 42)
    )
    client = TestClient(
        create_app(app_settings=_settings(), email_registration_service=cooldown)
    )
    response = client.post(
        "/api/v1/auth/email-verifications/resend",
        json={"flow_token": "v1." + "f" * 43},
    )
    assert response.status_code == 429
    assert response.json()["code"] == "resend_cooldown_active"
    assert response.json()["retry_after_seconds"] == 42
    assert response.headers["Retry-After"] == "42"

    existing = StubRegistrationService(
        AccountAlreadyExistsError("Account ownership was confirmed")
    )
    client = TestClient(
        create_app(app_settings=_settings(), email_registration_service=existing)
    )
    response = client.post(
        "/api/v1/auth/email-verifications/confirm",
        json={"flow_token": "v1." + "f" * 43, "code": "123456"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "account_already_exists"
    assert response.json()["actions"] == ["sign_in", "forgot_password"]


def test_unconfigured_authentication_is_safe_503_and_openapi_is_secret_free():
    client = TestClient(create_app(app_settings=_settings()))
    response = client.post(
        "/api/v1/auth/email-verifications/resend",
        json={"flow_token": "v1." + "m" * 43},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert "v1." + "m" * 43 not in response.text

    schema_text = client.get("/openapi.json").text
    for path in (
        "/api/v1/auth/registrations",
        "/api/v1/auth/email-verifications/confirm",
        "/api/v1/auth/email-verifications/resend",
        "/api/v1/auth/email-verifications/email",
    ):
        assert path in schema_text
    assert "012345" not in schema_text
    assert "must_not_appear" not in schema_text


@pytest.mark.parametrize(
    ("operation", "method", "path", "payload"),
    (
        (
            "registration",
            "post",
            "/api/v1/auth/registrations",
            {
                "display_name": "Diagnostic User",
                "email": "must_never_appear@example.com",
                "password": "must_never_appear_password",
                "terms_accepted": True,
                "privacy_accepted": True,
                "terms_version": "terms-test-v1",
                "privacy_version": "privacy-test-v1",
            },
        ),
        (
            "email_verification",
            "post",
            "/api/v1/auth/email-verifications/confirm",
            {
                "flow_token": "v1." + "f" * 43 + "must_never_appear",
                "code": "123456",
            },
        ),
        (
            "verification_resend",
            "post",
            "/api/v1/auth/email-verifications/resend",
            {"flow_token": "v1." + "f" * 43 + "must_never_appear"},
        ),
        (
            "change_unverified_email",
            "patch",
            "/api/v1/auth/email-verifications/email",
            {
                "flow_token": "v1." + "f" * 43 + "must_never_appear",
                "new_email": "must_never_appear@example.com",
            },
        ),
    ),
)
def test_unconfigured_authentication_emits_one_safe_diagnostic_per_route(
    caplog, operation, method, path, payload
):
    request_id = f"configuration-unavailable-{operation}"
    caplog.set_level(logging.WARNING, logger="pos.authentication.registration")
    client = TestClient(create_app(app_settings=_settings()))

    response = client.request(
        method,
        path,
        json=payload,
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "dependency_unavailable",
        "message": "A required dependency is unavailable",
        "request_id": request_id,
        "details": [],
    }
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "authentication_configuration_unavailable"
    ]
    assert len(records) == 1
    record = records[0]
    assert {
        "operation": record.operation,
        "outcome": record.outcome,
        "category": record.category,
        "request_id": record.request_id,
    } == {
        "operation": operation,
        "outcome": "unavailable",
        "category": "configuration_incomplete",
        "request_id": request_id,
    }
    assert not hasattr(record, "error_category")
    assert not hasattr(record, "account_id")
    assert not hasattr(record, "challenge_id")
    assert "must_never_appear" not in caplog.text


def test_unconfigured_authentication_exception_traceback_is_secret_free():
    sentinel = "must_never_appear"
    try:
        raise RegistrationDependencyUnavailableError(
            "Registration dependencies are unavailable"
        )
    except RegistrationDependencyUnavailableError as error:
        rendered = "".join(traceback.format_exception(error))
    assert sentinel not in rendered


def test_service_level_dependency_failure_does_not_emit_configuration_event(caplog):
    caplog.set_level(logging.WARNING, logger="pos.authentication.registration")
    client = TestClient(
        create_app(
            app_settings=_settings(),
            email_registration_service=StubRegistrationService(
                RegistrationDependencyUnavailableError(
                    "Registration dependency is unavailable"
                )
            ),
        )
    )

    response = client.post(
        "/api/v1/auth/registrations",
        json=_registration_payload(),
        headers={"X-Request-ID": "service-dependency-failure"},
    )

    assert response.status_code == 503
    assert not any(
        record.getMessage() == "authentication_configuration_unavailable"
        for record in caplog.records
    )


def test_verification_error_catalog_maps_to_stable_status_and_codes():
    cases = (
        (VerificationFlowInvalidError("safe"), 400, "verification_flow_invalid"),
        (VerificationFlowExpiredError("safe"), 410, "verification_flow_expired"),
        (VerificationCodeInvalidError("safe"), 400, "verification_code_invalid"),
        (VerificationCodeExpiredError("safe"), 410, "verification_code_expired"),
        (
            VerificationAttemptsExceededError("safe"),
            429,
            "verification_attempts_exceeded",
        ),
    )
    for error, status_code, code in cases:
        client = TestClient(
            create_app(
                app_settings=_settings(),
                email_registration_service=StubRegistrationService(error),
            )
        )
        response = client.post(
            "/api/v1/auth/email-verifications/confirm",
            json={"flow_token": "v1." + "f" * 43, "code": "123456"},
        )
        assert response.status_code == status_code
        assert response.json()["code"] == code
        assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_secret_field_shape_validation_is_safe():
    sentinel = "must_never_appear"
    client = TestClient(
        create_app(
            app_settings=_settings(),
            email_registration_service=StubRegistrationService(),
        )
    )
    response = client.post(
        "/api/v1/auth/email-verifications/confirm",
        json={"flow_token": sentinel, "code": sentinel},
    )
    assert response.status_code == 422
    assert sentinel not in response.text
