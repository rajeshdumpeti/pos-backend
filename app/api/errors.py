"""Typed API errors and centralized exception translation."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorDetail, ErrorResponse
from app.domain.notifications import (
    InboxError,
    InboxValidationError,
    InboxNotFoundError,
    InboxUnavailableError,
)
from app.domain.planning import (
    PlanError,
    PlanValidationError,
    PlanConflictError,
    PlanIdempotencyConflictError,
    PlanNotFoundError,
    PlanUnavailableError,
)
from app.application.email_registration import (
    AccountAlreadyExistsError,
    AuthenticationConfigurationUnavailableError,
    AuthenticationCryptographyError,
    LegalAcceptanceOutdatedError,
    LegalConsentNotApplicableError,
    LegalConsentRequiredError,
    PasswordRejectedError,
    RegistrationDependencyUnavailableError,
    RegistrationInputRejectedError,
    RegistrationInternalError,
    RegistrationVerificationError,
    ResendCooldownActiveError,
    VerificationAttemptsExceededError,
    VerificationCodeExpiredError,
    VerificationCodeInvalidError,
    VerificationFlowExpiredError,
    VerificationFlowInvalidError,
)
from app.core import REQUEST_ID_HEADER, get_request_id
from app.application.session_lifecycle import (
    AccessTokenInvalidError,
    AccessTokenExpiredError,
    AuthenticationRequiredError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    RefreshSessionInvalidError,
    RefreshSessionExpiredError,
    RefreshSessionReplayedError,
    RefreshIdempotencyConflictError,
    SessionDependencyUnavailableError,
    SessionInternalError,
    SessionLifecycleError,
)
from app.domain import (
    ItemNotFoundError,
    ResourceMutationNotAllowedError,
    UnknownResourceError,
)
from app.application.password_recovery import (
    PasswordRecoveryDependencyUnavailableError,
    PasswordRecoveryError,
    PasswordRecoveryInternalError,
    ResetPasswordRejectedError,
    ResetTokenExpiredError,
    ResetTokenInvalidError,
)
from app.application.onboarding import (
    OnboardingDependencyUnavailableError,
    OnboardingEmailVerificationRequiredError,
    OnboardingError,
    OnboardingIncompleteError,
    OnboardingRequiredError,
    OnboardingStateConflictError,
    OnboardingUpdateRejectedError,
)
from app.application.account_eligibility import AccountUnavailableError


_ERROR_DESCRIPTIONS = {
    status.HTTP_401_UNAUTHORIZED: "Authentication is required or invalid.",
    status.HTTP_403_FORBIDDEN: "The authenticated account is not eligible for this operation.",
    status.HTTP_400_BAD_REQUEST: "The operation is not supported for this resource.",
    status.HTTP_404_NOT_FOUND: "The requested route, resource, or item was not found.",
    status.HTTP_405_METHOD_NOT_ALLOWED: "The HTTP method is not supported.",
    status.HTTP_409_CONFLICT: "The request conflicts with existing state.",
    status.HTTP_410_GONE: "The requested authentication flow has expired.",
    status.HTTP_429_TOO_MANY_REQUESTS: "The operation is temporarily unavailable.",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "The request could not be validated.",
    status.HTTP_503_SERVICE_UNAVAILABLE: "A required dependency is unavailable.",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "An unexpected server error occurred.",
}
_VALIDATION_MESSAGES = {
    "bool_type": "Value must be a boolean",
    "dict_type": "Value must be an object",
    "extra_forbidden": "Field is not allowed",
    "json_invalid": "Request body must be valid JSON",
    "list_type": "Value must be an array",
    "missing": "Field required",
    "string_too_long": "Value is too long",
    "string_too_short": "Value is too short",
    "string_type": "Value must be a string",
}


def documented_errors(*status_codes: int) -> dict[int, dict[str, Any]]:
    return {
        status_code: {
            "description": _ERROR_DESCRIPTIONS[status_code],
            "model": ErrorResponse,
            "headers": {
                REQUEST_ID_HEADER: {
                    "description": "Request identifier for support and diagnostics.",
                    "schema": {"type": "string"},
                }
            },
        }
        for status_code in status_codes
    }


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(UnknownResourceError, _handle_domain_error)
    application.add_exception_handler(ItemNotFoundError, _handle_domain_error)
    application.add_exception_handler(
        ResourceMutationNotAllowedError, _handle_domain_error
    )
    application.add_exception_handler(
        RegistrationVerificationError, _handle_registration_error
    )
    application.add_exception_handler(SessionLifecycleError, _handle_session_error)
    application.add_exception_handler(AccountUnavailableError, _handle_session_error)
    application.add_exception_handler(
        PasswordRecoveryError, _handle_password_recovery_error
    )
    application.add_exception_handler(OnboardingError, _handle_onboarding_error)
    application.add_exception_handler(PlanError, _handle_plan_error)
    application.add_exception_handler(InboxError, _handle_inbox_error)
    application.add_exception_handler(RequestValidationError, _handle_validation_error)
    application.add_exception_handler(StarletteHTTPException, _handle_http_error)
    application.add_exception_handler(Exception, _handle_unexpected_error)


async def _handle_inbox_error(request: Request, error: InboxError) -> JSONResponse:
    code, name, message = (
        (422, "request_validation_failed", "The request could not be validated")
        if isinstance(error, InboxValidationError)
        else (
            (404, "notification_unavailable", "Notification is unavailable")
            if isinstance(error, InboxNotFoundError)
            else (
                (503, "dependency_unavailable", "A required dependency is unavailable")
                if isinstance(error, InboxUnavailableError)
                else (500, "internal_server_error", "An unexpected error occurred")
            )
        )
    )
    return _error_response(request, code, name, message)


async def _handle_plan_error(request: Request, error: PlanError) -> JSONResponse:
    mappings = {
        PlanValidationError: (
            422,
            "request_validation_failed",
            "The request could not be validated",
        ),
        PlanConflictError: (
            409,
            "plan_item_state_conflict",
            "Plan item version conflicts",
        ),
        PlanIdempotencyConflictError: (
            409,
            "plan_item_idempotency_conflict",
            "Idempotency key conflicts",
        ),
        PlanNotFoundError: (404, "item_not_found", "Item not found"),
        PlanUnavailableError: (
            503,
            "dependency_unavailable",
            "A required dependency is unavailable",
        ),
    }
    code, name, message = mappings.get(
        type(error), (500, "internal_server_error", "An unexpected error occurred")
    )
    return _error_response(request, code, name, message)


async def _handle_onboarding_error(
    request: Request, error: OnboardingError
) -> JSONResponse:
    if isinstance(error, OnboardingEmailVerificationRequiredError):
        return _onboarding_error_response(
            request,
            403,
            "email_verification_required",
            "Email verification is required",
        )
    if isinstance(error, OnboardingRequiredError):
        return _onboarding_error_response(
            request, 403, "onboarding_required", "Onboarding completion is required"
        )
    if isinstance(error, OnboardingUpdateRejectedError):
        return _onboarding_error_response(
            request, 400, "onboarding_update_rejected", "Onboarding update was rejected"
        )
    if isinstance(error, OnboardingIncompleteError):
        return _onboarding_error_response(
            request,
            409,
            "onboarding_incomplete",
            "Onboarding requirements are incomplete",
        )
    if isinstance(error, OnboardingStateConflictError):
        return _onboarding_error_response(
            request,
            409,
            "onboarding_state_conflict",
            "Onboarding state conflicts with a newer update",
        )
    if isinstance(error, OnboardingDependencyUnavailableError):
        return _onboarding_error_response(
            request,
            503,
            "dependency_unavailable",
            "A required dependency is unavailable",
        )
    return _onboarding_error_response(
        request, 500, "internal_server_error", "An unexpected error occurred"
    )


def _onboarding_error_response(
    request: Request, status_code: int, code: str, message: str
) -> JSONResponse:
    return _error_response(
        request,
        status_code,
        code,
        message,
        extra_headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


async def _handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, UnknownResourceError):
        return _error_response(
            request,
            status.HTTP_404_NOT_FOUND,
            "resource_not_found",
            "Unknown resource",
        )
    if isinstance(error, ItemNotFoundError):
        return _error_response(
            request,
            status.HTTP_404_NOT_FOUND,
            "item_not_found",
            "Item not found",
        )
    return _error_response(
        request,
        status.HTTP_400_BAD_REQUEST,
        "resource_operation_not_supported",
        str(error),
    )


async def _handle_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    details = (
        []
        if request.url.path.startswith(
            (
                "/api/v1/auth",
                "/api/v1/plan",
                "/api/v1/today",
                "/api/v1/notifications",
                "/api/v1/account",
            )
        )
        else [_validation_detail(error_detail) for error_detail in error.errors()]
    )
    return _error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "request_validation_failed",
        "Request validation failed",
        details,
    )


async def _handle_registration_error(
    request: Request, error: RegistrationVerificationError
) -> JSONResponse:
    if isinstance(error, RegistrationInputRejectedError):
        return _error_response(
            request, 422, "request_validation_failed", "Request validation failed"
        )
    if isinstance(error, LegalConsentRequiredError):
        return _error_response(
            request, 400, "legal_consent_required", "Legal consent is required"
        )
    if isinstance(error, LegalConsentNotApplicableError):
        return _error_response(
            request,
            400,
            "legal_consent_not_applicable",
            "Legal consent is not applicable",
        )
    if isinstance(error, LegalAcceptanceOutdatedError):
        return _error_response(
            request,
            409,
            "legal_acceptance_outdated",
            "Registration policy has changed",
            actions=["refresh_registration_policy"],
        )
    if isinstance(error, PasswordRejectedError):
        return _error_response(
            request, 400, "password_rejected", "Password was rejected"
        )
    if isinstance(error, VerificationFlowInvalidError):
        return _error_response(
            request, 400, "verification_flow_invalid", "Verification flow is invalid"
        )
    if isinstance(error, VerificationFlowExpiredError):
        return _error_response(
            request, 410, "verification_flow_expired", "Verification flow expired"
        )
    if isinstance(error, VerificationCodeInvalidError):
        return _error_response(
            request, 400, "verification_code_invalid", "Verification code is invalid"
        )
    if isinstance(error, VerificationCodeExpiredError):
        return _error_response(
            request, 410, "verification_code_expired", "Verification code expired"
        )
    if isinstance(error, VerificationAttemptsExceededError):
        return _error_response(
            request,
            429,
            "verification_attempts_exceeded",
            "Verification attempts were exceeded",
            retry_after_seconds=error.retry_after_seconds,
        )
    if isinstance(error, ResendCooldownActiveError):
        return _error_response(
            request,
            429,
            "resend_cooldown_active",
            "Verification resend cooldown is active",
            retry_at=error.retry_at.isoformat(),
            retry_after_seconds=error.retry_after_seconds,
        )
    if isinstance(error, AccountAlreadyExistsError):
        return _error_response(
            request,
            409,
            "account_already_exists",
            "Account ownership was confirmed",
            actions=["sign_in", "forgot_password"],
        )
    if isinstance(error, AuthenticationConfigurationUnavailableError):
        return _error_response(
            request,
            503,
            "authentication_configuration_unavailable",
            "Authentication configuration is unavailable",
        )
    if isinstance(error, RegistrationDependencyUnavailableError):
        return _error_response(
            request,
            503,
            "dependency_unavailable",
            "A required dependency is unavailable",
        )
    if isinstance(error, (AuthenticationCryptographyError, RegistrationInternalError)):
        return _error_response(
            request,
            500,
            "internal_server_error",
            "An unexpected error occurred",
        )
    return _error_response(
        request, 500, "internal_server_error", "An unexpected error occurred"
    )


async def _handle_session_error(
    request: Request, error: SessionLifecycleError
) -> JSONResponse:
    if isinstance(error, InvalidCredentialsError):
        return _error_response(
            request, 401, "invalid_credentials", "Email or password is invalid"
        )
    if isinstance(error, EmailNotVerifiedError):
        continuation = error.continuation
        return _error_response(
            request,
            403,
            "email_not_verified",
            "Email verification is required",
            actions=["verify_email"],
            verification_flow={
                "flow_token": continuation.flow_token,
                "masked_email": continuation.masked_email,
                "expires_at": continuation.expires_at.isoformat(),
                "resend_available_at": continuation.resend_available_at.isoformat(),
            },
        )
    if isinstance(error, AccountUnavailableError):
        return _error_response(
            request, 403, "account_unavailable", "Account is unavailable"
        )
    if isinstance(error, AuthenticationRequiredError):
        return _error_response(
            request, 401, "authentication_required", "Authentication is required"
        )
    if isinstance(error, AccessTokenExpiredError):
        return _error_response(
            request, 401, "access_token_expired", "Access token expired"
        )
    if isinstance(error, AccessTokenInvalidError):
        return _error_response(
            request, 401, "access_token_invalid", "Access token is invalid"
        )
    if isinstance(error, RefreshSessionExpiredError):
        return _error_response(
            request, 401, "refresh_session_expired", "Refresh session expired"
        )
    if isinstance(error, RefreshSessionInvalidError):
        return _error_response(
            request, 401, "refresh_session_invalid", "Refresh session is invalid"
        )
    if isinstance(error, RefreshSessionReplayedError):
        return _error_response(
            request,
            409,
            "refresh_session_replayed",
            "Refresh session replay was detected",
        )
    if isinstance(error, RefreshIdempotencyConflictError):
        return _error_response(
            request,
            409,
            "refresh_idempotency_conflict",
            "Refresh idempotency key conflicts with another request",
        )
    if isinstance(error, SessionDependencyUnavailableError):
        return _error_response(
            request,
            503,
            "dependency_unavailable",
            "A required dependency is unavailable",
        )
    if isinstance(error, SessionInternalError):
        return _error_response(
            request, 500, "internal_server_error", "An unexpected error occurred"
        )
    return _error_response(
        request, 500, "internal_server_error", "An unexpected error occurred"
    )


async def _handle_password_recovery_error(
    request: Request, error: PasswordRecoveryError
) -> JSONResponse:
    if isinstance(error, ResetTokenInvalidError):
        return _error_response(
            request, 400, "reset_token_invalid", "Reset token is invalid"
        )
    if isinstance(error, ResetTokenExpiredError):
        return _error_response(
            request, 410, "reset_token_expired", "Reset token expired"
        )
    if isinstance(error, ResetPasswordRejectedError):
        return _error_response(
            request, 400, "password_rejected", "Password was rejected"
        )
    if isinstance(error, PasswordRecoveryDependencyUnavailableError):
        return _error_response(
            request,
            503,
            "dependency_unavailable",
            "A required dependency is unavailable",
        )
    if isinstance(error, PasswordRecoveryInternalError):
        return _error_response(
            request, 500, "internal_server_error", "An unexpected error occurred"
        )
    return _error_response(
        request, 500, "internal_server_error", "An unexpected error occurred"
    )


async def _handle_http_error(
    request: Request, error: StarletteHTTPException
) -> JSONResponse:
    if error.status_code == status.HTTP_404_NOT_FOUND:
        error_code = "route_not_found"
    elif error.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        error_code = "method_not_allowed"
    else:
        error_code = "http_error"

    try:
        message = HTTPStatus(error.status_code).phrase
    except ValueError:
        message = "Request failed"

    return _error_response(
        request,
        error.status_code,
        error_code,
        message,
        extra_headers=error.headers,
    )


async def _handle_unexpected_error(request: Request, _error: Exception) -> JSONResponse:
    return _error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_server_error",
        "An unexpected error occurred",
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    extra_headers: dict[str, str] | None = None,
    retry_at: str | None = None,
    retry_after_seconds: int | None = None,
    actions: list[str] | None = None,
    verification_flow: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    headers = dict(extra_headers or {})
    if request.url.path.startswith(
        (
            "/api/v1/onboarding",
            "/api/v1/plan",
            "/api/v1/today",
            "/api/v1/notifications",
            "/api/v1/account",
        )
    ):
        headers["Cache-Control"] = "no-store"
        headers["Pragma"] = "no-cache"
    headers[REQUEST_ID_HEADER] = request_id
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id,
        details=details or [],
    )
    content: dict[str, Any] = body.model_dump()
    if retry_at is not None:
        content["retry_at"] = retry_at
    if retry_after_seconds is not None:
        content["retry_after_seconds"] = retry_after_seconds
        headers["Retry-After"] = str(retry_after_seconds)
    if actions:
        content["actions"] = actions
    if verification_flow is not None:
        content["verification_flow"] = verification_flow
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
    )


def _format_location(location: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in location) or "request"


def _validation_detail(error: dict[str, Any]) -> ErrorDetail:
    validation_code = str(error.get("type", "invalid"))
    return ErrorDetail(
        field=_format_location(error.get("loc", ())),
        code=validation_code,
        message=_VALIDATION_MESSAGES.get(validation_code, "Invalid value"),
    )
