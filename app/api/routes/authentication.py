"""Thin HTTP delivery for email registration and verification."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request, status

from app.api.authentication_schemas import (
    AuthenticationErrorResponse,
    ChangeUnverifiedEmailRequest,
    ConfirmEmailRequest,
    EmailVerifiedResponse,
    PendingVerificationResponse,
    RegistrationRequest,
    ResendVerificationRequest,
)
from app.application.email_registration import (
    AuthenticationAvailabilityDiagnostic,
    ChangeUnverifiedEmailCommand,
    ConfirmEmailCommand,
    EmailRegistrationService,
    RegisterAccountCommand,
    RegistrationDependencyUnavailableError,
    ResendVerificationCommand,
)
from app.core import REQUEST_ID_HEADER, get_request_id


def _responses(*status_codes: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        400: "The password, flow, or verification code was rejected.",
        409: "Email ownership was proved for an existing account.",
        410: "The verification code or flow expired.",
        422: "The request or legal acceptance could not be validated.",
        429: "Verification attempts or the resend cooldown were exceeded.",
        500: "An unexpected safe internal failure occurred.",
        503: "A required persistence or cryptographic dependency is unavailable.",
    }
    return {
        code: {
            "description": descriptions[code],
            "model": AuthenticationErrorResponse,
            "headers": {
                REQUEST_ID_HEADER: {
                    "description": "Request identifier for support and diagnostics.",
                    "schema": {"type": "string"},
                }
            },
        }
        for code in status_codes
    }


def create_authentication_router(
    service: EmailRegistrationService | None,
    availability_diagnostic: AuthenticationAvailabilityDiagnostic,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

    def require_service(
        *, operation: str, request: Request
    ) -> EmailRegistrationService:
        if service is None:
            availability_diagnostic.emit_configuration_incomplete(
                operation=operation,
                request_id=get_request_id(request),
            )
            raise RegistrationDependencyUnavailableError(
                "Registration dependencies are unavailable"
            )
        return service

    @router.post(
        "/registrations",
        operation_id="create_email_registration",
        summary="Create an email registration",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=PendingVerificationResponse,
        responses=_responses(400, 422, 429, 500, 503),
    )
    def register(
        payload: RegistrationRequest, request: Request
    ) -> PendingVerificationResponse:
        result = require_service(operation="registration", request=request).register(
            RegisterAccountCommand(
                display_name=payload.display_name,
                email=payload.email,
                password=payload.password.get_secret_value(),
                terms_accepted=payload.terms_accepted,
                privacy_accepted=payload.privacy_accepted,
                terms_version=payload.terms_version,
                privacy_version=payload.privacy_version,
                request_id=get_request_id(request),
            )
        )
        return PendingVerificationResponse(**asdict(result))

    @router.post(
        "/email-verifications/confirm",
        operation_id="confirm_email_verification",
        summary="Confirm an email verification code",
        status_code=status.HTTP_201_CREATED,
        response_model=EmailVerifiedResponse,
        responses=_responses(400, 409, 410, 422, 429, 500, 503),
    )
    def confirm(
        payload: ConfirmEmailRequest, request: Request
    ) -> EmailVerifiedResponse:
        result = require_service(
            operation="email_verification", request=request
        ).confirm(
            ConfirmEmailCommand(
                flow_token=payload.flow_token.get_secret_value(),
                code=payload.code.get_secret_value(),
                request_id=get_request_id(request),
            )
        )
        return EmailVerifiedResponse(**asdict(result))

    @router.post(
        "/email-verifications/resend",
        operation_id="resend_email_verification",
        summary="Resend an email verification code",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=PendingVerificationResponse,
        responses=_responses(400, 410, 422, 429, 500, 503),
    )
    def resend(
        payload: ResendVerificationRequest, request: Request
    ) -> PendingVerificationResponse:
        result = require_service(
            operation="verification_resend", request=request
        ).resend(
            ResendVerificationCommand(
                flow_token=payload.flow_token.get_secret_value(),
                request_id=get_request_id(request),
            )
        )
        return PendingVerificationResponse(**asdict(result))

    @router.patch(
        "/email-verifications/email",
        operation_id="change_unverified_email",
        summary="Change an unverified registration email",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=PendingVerificationResponse,
        responses=_responses(400, 410, 422, 429, 500, 503),
    )
    def change_email(
        payload: ChangeUnverifiedEmailRequest, request: Request
    ) -> PendingVerificationResponse:
        result = require_service(
            operation="change_unverified_email", request=request
        ).change_email(
            ChangeUnverifiedEmailCommand(
                flow_token=payload.flow_token.get_secret_value(),
                new_email=payload.new_email,
                request_id=get_request_id(request),
            )
        )
        return PendingVerificationResponse(**asdict(result))

    return router
