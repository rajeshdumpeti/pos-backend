"""Database-independent Authentication Contract v1.0.0 freeze tests."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

from fastapi.testclient import TestClient
import pytest

from app.api.authentication_schemas import EmailNotVerifiedResponse
from app.application.email_registration import (
    EmailRegistrationService,
    LegalAcceptanceOutdatedError,
    RegistrationPolicy,
    VerificationContinuationResult,
)
from app.application.session_lifecycle import (
    EmailNotVerifiedError,
    InvalidCredentialsError,
    RefreshCommand,
    RefreshIdempotencyConflictError,
    SignInCommand,
)
from app.config import load_settings
from app.infrastructure.authentication.email_delivery import (
    EmailDeliveryError,
    _reset_link,
)
from app.infrastructure.authentication.registration import (
    EmailValidatorNormalizer,
    HmacVerificationMaterialProvider,
)
from app.main import AUTHENTICATION_CONTRACT_VERSION, create_app
from tests.test_registration_api import StubRegistrationService, _registration_payload
from tests.test_session_api import StubSessionService
from tests.test_session_lifecycle import NOW, State, service


class Continuation:
    def __init__(self) -> None:
        self.calls = 0

    def issue_sign_in_continuation(self, *_args):
        self.calls += 1
        return VerificationContinuationResult(
            flow_token="v1." + "f" * 43,
            masked_email="p***@example.test",
            expires_at=NOW + timedelta(minutes=30),
            resend_available_at=NOW + timedelta(seconds=60),
        )


def _pending_state() -> State:
    state = State()
    state.account = replace(
        state.account,
        status=__import__(
            "app.domain.authentication", fromlist=["AccountStatus"]
        ).AccountStatus.PENDING_VERIFICATION,
        email_verified_at=None,
    )
    state.accounts.account = state.account
    state.identity = replace(state.identity, is_verified=False, verified_at=None)
    state.identities.identity = state.identity
    return state


def test_unverified_sign_in_requires_correct_password_and_creates_no_session():
    state = _pending_state()
    continuation = Continuation()
    sessions = service(state, verification_continuation=continuation)

    with pytest.raises(InvalidCredentialsError) as wrong_password:
        sessions.sign_in(SignInCommand("person@example.test", "wrong", "invalid"))
    with pytest.raises(InvalidCredentialsError) as unknown_email:
        sessions.sign_in(SignInCommand("unknown@example.test", "wrong", "unknown"))
    assert str(wrong_password.value) == str(unknown_email.value)
    assert continuation.calls == 0
    assert state.commits == 0
    assert not state.sessions.records

    with pytest.raises(EmailNotVerifiedError) as caught:
        sessions.sign_in(SignInCommand("person@example.test", "correct", "valid"))
    assert caught.value.continuation.flow_token.startswith("v1.")
    assert continuation.calls == 1
    assert state.commits == 1
    assert not state.sessions.records


def test_named_unverified_response_contains_only_masked_continuation():
    error = EmailNotVerifiedError(Continuation().issue_sign_in_continuation())
    assert error.continuation.flow_token not in repr(error.continuation)
    assert error.continuation.flow_token not in str(error)
    api = TestClient(
        create_app(
            load_settings({}),
            email_session_service=StubSessionService(error),
        )
    )
    response = api.post(
        "/api/v1/auth/sessions",
        json={"email": "private@example.test", "password": "private-value"},
        headers={"X-Request-ID": "contract-v1"},
    )
    assert response.status_code == 403
    body = EmailNotVerifiedResponse.model_validate(response.json())
    assert body.actions == ["verify_email"]
    assert body.verification_flow.masked_email == "p***@example.test"
    assert body.verification_flow.flow_token.startswith("v1.")
    assert "private@example.test" not in response.text
    assert "private-value" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_active_challenge_rotates_only_flow_token_without_duplicate_email():
    material = HmacVerificationMaterialProvider(
        b"d" * 32, key_id="digest-test", key_version=1
    )
    original = material.issue(__import__("uuid").uuid4(), __import__(
        "app.domain.authentication", fromlist=["ChallengePurpose"]
    ).ChallengePurpose.REGISTRATION)
    state = _pending_state()
    challenge = SimpleNamespace(
        id=__import__("uuid").uuid4(),
        purpose=__import__(
            "app.domain.authentication", fromlist=["ChallengePurpose"]
        ).ChallengePurpose.REGISTRATION,
        flow_expires_at=NOW + timedelta(minutes=30),
        resend_available_at=NOW + timedelta(seconds=60),
        status_version=1,
    )
    rotated: list[tuple[object, ...]] = []
    unit = SimpleNamespace(
        verification_challenges=SimpleNamespace(
            get_current_for_update=lambda *_args: challenge,
            rotate_flow_token=lambda *args: rotated.append(args) is None or True,
        ),
        email_outbox=SimpleNamespace(add=lambda _record: pytest.fail("duplicate email")),
        security_events=SimpleNamespace(append=lambda _record: None),
    )
    registration = EmailRegistrationService(
        lambda _request_id: unit,
        SimpleNamespace(),
        SimpleNamespace(),
        EmailValidatorNormalizer(),
        material,
        SimpleNamespace(),
        SimpleNamespace(emit=lambda **_fields: None),
        RegistrationPolicy(terms_version="terms-v1", privacy_version="privacy-v1"),
        clock=lambda: NOW,
    )

    continuation = registration.issue_sign_in_continuation(
        unit, state.account, state.identity, "request", NOW
    )
    lookup = material.flow_lookup(continuation.flow_token)
    assert lookup.digest != original.flow_token_digest
    assert rotated[0][2] == lookup.digest
    assert continuation.expires_at == challenge.flow_expires_at
    assert continuation.resend_available_at == challenge.resend_available_at


def test_registration_policy_is_public_named_and_stale_versions_are_conflict():
    settings = load_settings(
        {
            "CURRENT_TERMS_VERSION": "terms-v1",
            "CURRENT_PRIVACY_VERSION": "privacy-v1",
        }
    )
    api = TestClient(create_app(settings))
    response = api.get(
        "/api/v1/auth/registration-policy",
        headers={"X-Request-ID": "policy-v1"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "terms_version": "terms-v1",
        "privacy_version": "privacy-v1",
    }
    assert response.headers["X-Request-ID"] == "policy-v1"
    assert response.headers["Cache-Control"] == "no-store"

    stale = StubRegistrationService(LegalAcceptanceOutdatedError())
    response = TestClient(
        create_app(settings, email_registration_service=stale)
    ).post("/api/v1/auth/registrations", json=_registration_payload())
    assert response.status_code == 409
    assert response.json()["code"] == "legal_acceptance_outdated"
    assert response.json()["actions"] == ["refresh_registration_policy"]
    assert response.json()["details"] == []
    assert "terms-test-v1" not in response.text


def test_remember_me_is_absent_and_true_is_not_accepted():
    api = TestClient(create_app(load_settings({}), email_session_service=StubSessionService()))
    schema = api.get("/openapi.json").json()
    assert "remember_me" not in schema["components"]["schemas"]["SignInRequest"]["properties"]
    response = api.post(
        "/api/v1/auth/sessions",
        json={"email": "person@example.test", "password": "private", "remember_me": True},
    )
    assert response.status_code == 422


def test_reset_link_has_exactly_one_token_and_preserves_opaque_value():
    token = "Opaque_Value-123"
    link = _reset_link("pos-dev://reset?source=app", token)
    query = parse_qsl(urlsplit(link).query, keep_blank_values=True)
    assert query == [("source", "app"), ("token", token)]
    for base in (
        "https://app.example.test/reset?token=existing",
        "https://app.example.test/reset?token=one&token=two",
        "https://app.example.test/reset?to%6ben=encoded",
    ):
        with pytest.raises(EmailDeliveryError) as caught:
            _reset_link(base, token)
        assert token not in str(caught.value)


def test_reset_destination_configuration_is_environment_safe():
    assert load_settings(
        {"APP_ENV": "development", "AUTH_PASSWORD_RESET_URL": "pos-dev://reset"}
    ).auth_password_reset_url == "pos-dev://reset"
    for value in (
        "https://app.example.test/reset?token=placeholder",
        "https://app.example.test/reset?token=one&token=two",
    ):
        with pytest.raises(ValueError) as caught:
            load_settings({"AUTH_PASSWORD_RESET_URL": value})
        assert "placeholder" not in str(caught.value)


def test_refresh_same_key_with_different_material_is_conflict():
    state = State()
    sessions = service(state)
    signed_in = sessions.sign_in(
        SignInCommand("person@example.test", "correct", "sign-in")
    )
    rotated = sessions.refresh(
        RefreshCommand(signed_in.refresh_token, "logical-refresh", "first")
    )
    with pytest.raises(RefreshIdempotencyConflictError):
        sessions.refresh(
            RefreshCommand(rotated.refresh_token, "logical-refresh", "second")
        )
    command = RefreshCommand("private-refresh", "private-idempotency", "request")
    assert "private-refresh" not in repr(command)
    assert "private-idempotency" not in repr(command)

    class ConflictSession(StubSessionService):
        def refresh(self, _command):
            raise RefreshIdempotencyConflictError()

    response = TestClient(
        create_app(
            load_settings({}),
            email_session_service=ConflictSession(),
        )
    ).post(
        "/api/v1/auth/sessions/refresh",
        json={"refresh_token": "r" * 40},
        headers={"Idempotency-Key": "different-material"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "refresh_idempotency_conflict"


def test_openapi_freezes_contract_version_and_named_schemas():
    schema = create_app(load_settings({})).openapi()
    assert AUTHENTICATION_CONTRACT_VERSION == "1.0.0"
    assert schema["x-pos-authentication-contract-version"] == "1.0.0"
    names = schema["components"]["schemas"]
    for name in (
        "RegistrationPolicyResponse",
        "RegistrationRequest",
        "PendingVerificationResponse",
        "ConfirmEmailRequest",
        "ResendVerificationRequest",
        "ChangeUnverifiedEmailRequest",
        "EmailVerifiedResponse",
        "SignInRequest",
        "EmailNotVerifiedResponse",
        "VerificationFlowContinuation",
        "RefreshSessionRequest",
        "SessionTokenResponse",
        "CurrentSessionResponse",
        "PasswordResetRequest",
        "PasswordResetAcceptedResponse",
        "ConfirmPasswordResetRequest",
        "AuthenticationErrorResponse",
    ):
        assert name in names
    assert set(
        names["AuthenticationErrorResponse"]["properties"]["actions"]["items"][
            "enum"
        ]
    ) == {
        "sign_in",
        "forgot_password",
        "verify_email",
        "refresh_registration_policy",
    }
    operation_ids = [
        operation["operationId"]
        for paths in schema["paths"].values()
        for operation in paths.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_openapi_authentication_endpoint_and_status_inventory_cannot_drift():
    schema = create_app(load_settings({})).openapi()
    expected = {
        ("GET", "/api/v1/auth/registration-policy"): {200, 400, 401, 413, 415, 422, 429, 500, 503},
        ("POST", "/api/v1/auth/registrations"): {202, 400, 401, 409, 413, 415, 422, 429, 500, 503},
        ("POST", "/api/v1/auth/email-verifications/confirm"): {201, 400, 401, 409, 410, 413, 415, 422, 429, 500, 503},
        ("POST", "/api/v1/auth/email-verifications/resend"): {202, 400, 401, 410, 413, 415, 422, 429, 500, 503},
        ("PATCH", "/api/v1/auth/email-verifications/email"): {202, 400, 401, 410, 413, 415, 422, 429, 500, 503},
        ("POST", "/api/v1/auth/sessions"): {200, 400, 401, 403, 413, 415, 422, 429, 500, 503},
        ("POST", "/api/v1/auth/sessions/refresh"): {200, 400, 401, 409, 413, 415, 422, 429, 500, 503},
        ("GET", "/api/v1/auth/session"): {200, 400, 401, 413, 415, 422, 429, 500, 503},
        ("DELETE", "/api/v1/auth/session"): {204, 400, 401, 413, 415, 422, 429, 500, 503},
        ("DELETE", "/api/v1/auth/sessions"): {204, 400, 401, 413, 415, 422, 429, 500, 503},
        ("POST", "/api/v1/auth/password-resets"): {202, 400, 401, 413, 415, 422, 500, 503},
        ("POST", "/api/v1/auth/password-resets/confirm"): {204, 400, 401, 410, 413, 415, 422, 429, 500, 503},
    }
    actual = {
        (method.upper(), path): {int(code) for code in operation["responses"]}
        for path, path_item in schema["paths"].items()
        if path.startswith("/api/v1/auth")
        for method, operation in path_item.items()
    }
    assert actual == expected

    sign_in_forbidden = schema["paths"]["/api/v1/auth/sessions"]["post"]["responses"]["403"]["content"]["application/json"]["schema"]
    refs = {item.get("$ref") for item in sign_in_forbidden["anyOf"]}
    assert "#/components/schemas/EmailNotVerifiedResponse" in refs
    assert all(
        "Retry-After" in operation["responses"]["429"]["headers"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "429" in operation.get("responses", {})
    )
