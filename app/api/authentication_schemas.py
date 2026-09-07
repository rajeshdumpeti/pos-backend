"""Versioned HTTP schemas for email registration and verification."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.schemas import ErrorDetail


class AuthenticationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RegistrationRequest(AuthenticationRequest):
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
    display_name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(
        min_length=15,
        max_length=1024,
        json_schema_extra={"writeOnly": True},
    )
    terms_accepted: bool
    privacy_accepted: bool
    terms_version: str = Field(min_length=1, max_length=64)
    privacy_version: str = Field(min_length=1, max_length=64)


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
    new_email: str = Field(min_length=3, max_length=320)


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
    flow_token: str
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
    actions: list[Literal["sign_in", "forgot_password", "verify_email"]] = Field(
        default_factory=list
    )


class SignInRequest(AuthenticationRequest):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024, json_schema_extra={"writeOnly": True})
    remember_me: bool = False


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
