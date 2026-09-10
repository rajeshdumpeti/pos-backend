"""Versioned HTTP schemas for email registration and verification."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.schemas import ErrorDetail


class AuthenticationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RegistrationRequest(AuthenticationRequest):
    """Registration input; legal fields are conditional on the current policy."""

    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra={
            "example": {
                "display_name": "POS Tester",
                "email": "tester@example.com",
                "password": "<user-entered-password>",
                "terms_accepted": True,
                "privacy_accepted": True,
                "terms_version": "terms-v1",
                "privacy_version": "privacy-v1",
            }
        },
    )
    display_name: str = Field(min_length=1, max_length=160, repr=False)
    email: str = Field(min_length=3, max_length=320, repr=False)
    password: SecretStr = Field(
        min_length=15,
        max_length=1024,
        json_schema_extra={"writeOnly": True},
    )
    terms_accepted: bool | None = Field(
        default=None,
        description="Required and true only when legal_consent_required is true; otherwise omit.",
    )
    privacy_accepted: bool | None = Field(
        default=None,
        description="Required and true only when legal_consent_required is true; otherwise omit.",
    )
    terms_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Current backend-provided Terms version when consent is required; otherwise omit.",
    )
    privacy_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Current backend-provided Privacy version when consent is required; otherwise omit.",
    )


class ConfirmEmailRequest(AuthenticationRequest):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra={
            "example": {
                "flow_token": "<opaque-flow-token>",
                "code": "<six-digit-code>",
            }
        },
    )
    flow_token: SecretStr = Field(
        min_length=45,
        max_length=128,
        json_schema_extra={"writeOnly": True},
    )
    code: SecretStr = Field(
        min_length=6,
        max_length=6,
        json_schema_extra={"writeOnly": True},
    )


class ResendVerificationRequest(AuthenticationRequest):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra={
            "example": {"flow_token": "<opaque-flow-token>"}
        },
    )
    flow_token: SecretStr = Field(
        min_length=45,
        max_length=128,
        json_schema_extra={"writeOnly": True},
    )


class ChangeUnverifiedEmailRequest(AuthenticationRequest):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
        json_schema_extra={
            "example": {
                "flow_token": "<opaque-flow-token>",
                "new_email": "new-address@example.com",
            }
        },
    )
    flow_token: SecretStr = Field(
        min_length=45,
        max_length=128,
        json_schema_extra={"writeOnly": True},
    )
    new_email: str = Field(min_length=3, max_length=320, repr=False)


class PendingVerificationResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "registration_pending",
                "flow_token": "<opaque-flow-token>",
                "masked_email": "p***@example.com",
                "verification_expires_at": "2026-09-01T12:10:00Z",
                "flow_expires_at": "2026-09-01T12:30:00Z",
                "resend_available_at": "2026-09-01T12:01:00Z",
            }
        }
    )
    status: Literal["registration_pending"]
    flow_token: str = Field(repr=False)
    masked_email: str
    verification_expires_at: datetime
    flow_expires_at: datetime
    resend_available_at: datetime


class EmailVerifiedResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "email_verified",
                "account_id": "00000000-0000-4000-8000-000000000001",
                "onboarding_required": True,
                "session": None,
                "next_action": "await_session_establishment",
            }
        }
    )
    status: Literal["email_verified"]
    account_id: UUID
    onboarding_required: bool
    session: None = None
    next_action: Literal["await_session_establishment"]


class AuthenticationErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[ErrorDetail] = Field(default_factory=list)
    retry_at: datetime | None = None
    retry_after_seconds: int | None = None
    actions: list[
        Literal[
            "sign_in",
            "forgot_password",
            "verify_email",
            "refresh_registration_policy",
        ]
    ] = Field(
        default_factory=list
    )


class VerificationFlowContinuation(BaseModel):
    flow_token: str = Field(repr=False)
    masked_email: str
    expires_at: datetime
    resend_available_at: datetime


class EmailNotVerifiedResponse(BaseModel):
    code: Literal["email_not_verified"]
    message: Literal["Email verification is required"]
    request_id: str
    details: list[ErrorDetail] = Field(default_factory=list)
    actions: list[Literal["verify_email"]]
    verification_flow: VerificationFlowContinuation


class RegistrationPolicyResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "legal_consent_required": True,
                    "legal_policy_status": "active",
                    "terms_version": "approved-terms-version",
                    "terms_url": "https://legal.pos.example/terms",
                    "privacy_version": "approved-privacy-version",
                    "privacy_url": "https://legal.pos.example/privacy",
                },
                {
                    "legal_consent_required": False,
                    "legal_policy_status": "deferred_nonproduction",
                    "terms_version": None,
                    "terms_url": None,
                    "privacy_version": None,
                    "privacy_url": None,
                },
            ]
        }
    )
    legal_consent_required: bool
    legal_policy_status: Literal["active", "deferred_nonproduction"]
    terms_version: str | None
    terms_url: str | None
    privacy_version: str | None
    privacy_url: str | None


class SignInRequest(AuthenticationRequest):
    email: str = Field(min_length=3, max_length=320, repr=False)
    password: SecretStr = Field(min_length=1, max_length=1024, json_schema_extra={"writeOnly": True})


class RefreshSessionRequest(AuthenticationRequest):
    refresh_token: SecretStr = Field(min_length=32, max_length=256, json_schema_extra={"writeOnly": True})


class SessionTokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    token_type: Literal["Bearer"]
    access_expires_at: datetime
    refresh_expires_at: datetime
    account_id: UUID
    session_id: UUID
    onboarding_required: bool


class CurrentSessionResponse(BaseModel):
    account_id: UUID
    session_id: UUID
    display_name: str
    onboarding_required: bool


class PasswordResetRequest(AuthenticationRequest):
    email: str = Field(min_length=3, max_length=320, repr=False)


class PasswordResetAcceptedResponse(BaseModel):
    status: Literal["instructions_if_eligible"]


class ConfirmPasswordResetRequest(AuthenticationRequest):
    token: SecretStr = Field(
        min_length=43, max_length=128, json_schema_extra={"writeOnly": True}
    )
    new_password: SecretStr = Field(
        min_length=15, max_length=1024, json_schema_extra={"writeOnly": True}
    )
