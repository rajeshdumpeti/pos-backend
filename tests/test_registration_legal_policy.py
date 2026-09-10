"""Environment-aware registration legal policy contract tests."""

from fastapi.testclient import TestClient
import pytest
from types import SimpleNamespace

from app.config import (
    AppEnvironment,
    AuthenticationLegalConsentMode,
    ConfigurationError,
    load_settings,
)
from app.application.email_registration import (
    LegalConsentNotApplicableError,
    LegalConsentRequiredError,
    LegalPolicyStatus,
    EmailRegistrationService,
    RegisterAccountCommand,
    RegistrationPolicy,
)
from app.main import create_app
from tests.test_registration_api import StubRegistrationService


def test_legal_consent_modes_parse_and_default_safely() -> None:
    assert load_settings({}).auth_legal_consent_mode is AuthenticationLegalConsentMode.REQUIRED
    assert (
        load_settings(
            {"APP_ENV": "test", "AUTH_LEGAL_CONSENT_MODE": "deferred_nonproduction"}
        ).auth_legal_consent_mode
        is AuthenticationLegalConsentMode.DEFERRED_NONPRODUCTION
    )
    with pytest.raises(ConfigurationError, match="AUTH_LEGAL_CONSENT_MODE"):
        load_settings({"AUTH_LEGAL_CONSENT_MODE": "automatic"})


@pytest.mark.parametrize("environment", ["local", "development", "test"])
def test_deferred_mode_is_explicitly_nonproduction(environment: str) -> None:
    settings = load_settings(
        {"APP_ENV": environment, "AUTH_LEGAL_CONSENT_MODE": "deferred_nonproduction"}
    )
    assert settings.app_env is AppEnvironment(environment)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_protected_environments_reject_deferred_mode(environment: str) -> None:
    with pytest.raises(ValueError, match="restricted to non-production"):
        load_settings(
            {
                "APP_ENV": environment,
                "CORS_ORIGINS": "https://app.pos.com",
                "AUTH_LEGAL_CONSENT_MODE": "deferred_nonproduction",
            }
        )


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("CURRENT_TERMS_URL", "http://legal.pos.com/terms"),
        ("CURRENT_PRIVACY_URL", "https://example.test/privacy"),
        ("CURRENT_TERMS_VERSION", "placeholder"),
        ("CURRENT_PRIVACY_VERSION", "change-me"),
    ],
)
def test_protected_legal_policy_rejects_insecure_and_placeholder_values(
    variable: str, value: str
) -> None:
    settings = {
        "APP_ENV": "production",
        "AUTH_LEGAL_CONSENT_MODE": "required",
        "CURRENT_TERMS_VERSION": "terms-2026-09",
        "CURRENT_TERMS_URL": "https://legal.pos.com/terms",
        "CURRENT_PRIVACY_VERSION": "privacy-2026-09",
        "CURRENT_PRIVACY_URL": "https://legal.pos.com/privacy",
    }
    settings[variable] = value
    with pytest.raises(ConfigurationError) as caught:
        load_settings(settings)
    assert value not in str(caught.value)


def test_deferred_policy_is_named_nullable_noncacheable_and_request_identified() -> None:
    settings = load_settings(
        {"APP_ENV": "test", "AUTH_LEGAL_CONSENT_MODE": "deferred_nonproduction"}
    )
    response = TestClient(create_app(settings)).get(
        "/api/v1/auth/registration-policy", headers={"X-Request-ID": "deferred-policy"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "legal_consent_required": False,
        "legal_policy_status": "deferred_nonproduction",
        "terms_version": None,
        "terms_url": None,
        "privacy_version": None,
        "privacy_url": None,
    }
    assert response.headers["X-Request-ID"] == "deferred-policy"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_required_policy_returns_backend_values() -> None:
    settings = load_settings(
        {
            "AUTH_LEGAL_CONSENT_MODE": "required",
            "CURRENT_TERMS_VERSION": "terms-v1",
            "CURRENT_TERMS_URL": "http://localhost/legal/terms",
            "CURRENT_PRIVACY_VERSION": "privacy-v1",
            "CURRENT_PRIVACY_URL": "http://localhost/legal/privacy",
        }
    )
    response = TestClient(create_app(settings)).get("/api/v1/auth/registration-policy")
    assert response.json() == {
        "legal_consent_required": True,
        "legal_policy_status": "active",
        "terms_version": "terms-v1",
        "terms_url": "http://localhost/legal/terms",
        "privacy_version": "privacy-v1",
        "privacy_url": "http://localhost/legal/privacy",
    }


def test_incomplete_required_policy_fails_without_fallback() -> None:
    response = TestClient(create_app(load_settings({}))).get(
        "/api/v1/auth/registration-policy",
        headers={"X-Request-ID": "policy-unavailable"},
    )
    assert response.status_code == 503
    assert response.json() == {
        "code": "authentication_configuration_unavailable",
        "message": "Authentication configuration is unavailable",
        "request_id": "policy-unavailable",
        "details": [],
    }


def test_deferred_registration_schema_omits_legal_fields() -> None:
    service = StubRegistrationService()
    settings = load_settings(
        {"APP_ENV": "test", "AUTH_LEGAL_CONSENT_MODE": "deferred_nonproduction"}
    )
    client = TestClient(create_app(settings, email_registration_service=service))
    payload = {
        "display_name": "POS Tester",
        "email": "tester@example.test",
        "password": "a private test password",
    }
    response = client.post("/api/v1/auth/registrations", json=payload)
    assert response.status_code == 202
    assert service.last_command.terms_accepted is None
    assert service.last_command.privacy_accepted is None
    assert service.last_command.terms_version is None
    assert service.last_command.privacy_version is None
    assert service.last_command.legal_fields_supplied == frozenset()

    response = client.post(
        "/api/v1/auth/registrations", json={**payload, "terms_accepted": None}
    )
    assert response.status_code == 202
    assert service.last_command.legal_fields_supplied == {"terms_accepted"}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LegalConsentRequiredError(), "legal_consent_required"),
        (LegalConsentNotApplicableError(), "legal_consent_not_applicable"),
    ],
)
def test_legal_policy_errors_are_safe(error: Exception, code: str) -> None:
    service = StubRegistrationService(error)
    client = TestClient(
        create_app(load_settings({}), email_registration_service=service)
    )
    response = client.post(
        "/api/v1/auth/registrations",
        json={
            "display_name": "POS Tester",
            "email": "private@example.test",
            "password": "a private test password",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert response.json()["details"] == []
    assert "private@example.test" not in response.text


def _policy_validation_service(policy: RegistrationPolicy) -> EmailRegistrationService:
    def unexpected_unit(_request_id: str):
        pytest.fail("invalid legal input must be rejected before persistence")

    return EmailRegistrationService(
        unexpected_unit,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(normalize=lambda value: SimpleNamespace(display=value, comparison=value)),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(emit=lambda **_fields: None),
        policy,
    )


def test_application_enforces_required_and_deferred_legal_shapes() -> None:
    base = dict(
        display_name="POS Tester",
        email="private@example.test",
        password="a private test password",
        request_id="legal-shape",
    )
    required = _policy_validation_service(
        RegistrationPolicy(terms_version="terms-v1", privacy_version="privacy-v1")
    )
    with pytest.raises(LegalConsentRequiredError):
        required.register(RegisterAccountCommand(**base))

    deferred = _policy_validation_service(
        RegistrationPolicy(
            legal_consent_required=False,
            legal_policy_status=LegalPolicyStatus.DEFERRED_NONPRODUCTION,
        )
    )
    with pytest.raises(LegalConsentNotApplicableError):
        deferred.register(
            RegisterAccountCommand(
                **base,
                terms_accepted=None,
                legal_fields_supplied=frozenset({"terms_accepted"}),
            )
        )
    command = RegisterAccountCommand(
        **base,
        terms_accepted=True,
        terms_version="must-never-appear",
    )
    assert "POS Tester" not in repr(command)
    assert "must-never-appear" not in repr(command)
    assert "private@example.test" not in repr(command)


def test_openapi_documents_conditional_named_contract_and_unique_operations() -> None:
    schema = create_app(load_settings({})).openapi()
    policy = schema["components"]["schemas"]["RegistrationPolicyResponse"]
    request = schema["components"]["schemas"]["RegistrationRequest"]
    assert set(policy["properties"]) >= {
        "legal_consent_required",
        "legal_policy_status",
        "terms_version",
        "terms_url",
        "privacy_version",
        "privacy_url",
    }
    assert not {"terms_accepted", "privacy_accepted", "terms_version", "privacy_version"} & set(
        request["required"]
    )
    operations = [
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operations) == len(set(operations))
