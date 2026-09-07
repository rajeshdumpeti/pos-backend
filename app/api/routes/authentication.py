"""Thin HTTP delivery for email registration and verification."""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.authentication_schemas import (
    AuthenticationErrorResponse,
    ChangeUnverifiedEmailRequest,
    ConfirmEmailRequest,
    EmailVerifiedResponse,
    PendingVerificationResponse,
    RegistrationRequest,
    ResendVerificationRequest,
    SignInRequest,
    RefreshSessionRequest,
    SessionTokenResponse,
    CurrentSessionResponse,
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
from app.application.session_lifecycle import (
    EmailSessionService,
    RefreshCommand,
    SignInCommand,
)


def _responses(*status_codes: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        400: "The password, flow, or verification code was rejected.",
        401: "Authentication credentials are invalid or expired.",
        403: "The account cannot establish a session.",
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
    session_service: EmailSessionService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
    bearer = HTTPBearer(auto_error=False)

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

    def require_session_service(*, operation: str, request: Request) -> EmailSessionService:
        if session_service is None:
            availability_diagnostic.emit_configuration_incomplete(
                operation=operation, request_id=get_request_id(request)
            )
            raise RegistrationDependencyUnavailableError(
                "Session dependencies are unavailable"
            )
        return session_service

    def access_token(
        credentials: HTTPAuthorizationCredentials | None,
    ) -> str:
        from app.application.session_lifecycle import AuthenticationRequiredError

        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AuthenticationRequiredError("Authentication is required")
        return credentials.credentials

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

    @router.post(
        "/sessions",
        operation_id="create_email_session",
        summary="Sign in with a verified email account",
        description="Verifies email and password, then creates one rotating refresh session.",
        response_model=SessionTokenResponse,
        responses=_responses(401, 403, 500, 503),
    )
    def sign_in(payload: SignInRequest, request: Request, response: Response) -> SessionTokenResponse:
        result = require_session_service(operation="email_sign_in", request=request).sign_in(
            SignInCommand(
                email=payload.email,
                password=payload.password.get_secret_value(),
                remember_me=payload.remember_me,
                request_id=get_request_id(request),
            )
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return SessionTokenResponse(**asdict(result))

    @router.post(
        "/sessions/refresh",
        operation_id="refresh_email_session",
        summary="Rotate a refresh token",
        description="Atomically rotates one refresh token; retries must reuse Idempotency-Key.",
        response_model=SessionTokenResponse,
        responses=_responses(400, 401, 409, 500, 503),
    )
    def refresh(
        payload: RefreshSessionRequest,
        request: Request,
        response: Response,
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=64)
        ],
    ) -> SessionTokenResponse:
        result = require_session_service(operation="refresh_session", request=request).refresh(
            RefreshCommand(
                refresh_token=payload.refresh_token.get_secret_value(),
                refresh_request_id=idempotency_key,
                request_id=get_request_id(request),
            )
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return SessionTokenResponse(**asdict(result))

    @router.get(
        "/session",
        operation_id="get_current_session",
        summary="Get the current authenticated identity and session",
        description="Validates the access token and persisted session before restoring identity facts.",
        response_model=CurrentSessionResponse,
        responses=_responses(401, 500, 503),
    )
    def current_session(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> CurrentSessionResponse:
        result = require_session_service(operation="current_session", request=request).current(
            access_token(credentials), get_request_id(request)
        )
        return CurrentSessionResponse(**asdict(result))

    @router.delete(
        "/session",
        operation_id="delete_current_session",
        summary="Log out the current session",
        description="Idempotently revokes the current refresh session.",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_responses(401, 500, 503),
    )
    def logout_current(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> Response:
        require_session_service(operation="logout_current", request=request).logout_current(
            access_token(credentials), get_request_id(request)
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete(
        "/sessions",
        operation_id="delete_all_sessions",
        summary="Log out every session for the current account",
        description="Idempotently revokes every active refresh session for the account.",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_responses(401, 500, 503),
    )
    def logout_all(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> Response:
        require_session_service(operation="logout_all", request=request).logout_all(
            access_token(credentials), get_request_id(request)
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
