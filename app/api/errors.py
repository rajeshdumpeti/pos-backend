"""Typed API errors and centralized exception translation."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorDetail, ErrorResponse
from app.core import REQUEST_ID_HEADER, get_request_id
from app.domain import (
    ItemNotFoundError,
    ResourceMutationNotAllowedError,
    UnknownResourceError,
)


_ERROR_DESCRIPTIONS = {
    status.HTTP_400_BAD_REQUEST: "The operation is not supported for this resource.",
    status.HTTP_404_NOT_FOUND: "The requested route, resource, or item was not found.",
    status.HTTP_405_METHOD_NOT_ALLOWED: "The HTTP method is not supported.",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "The request could not be validated.",
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
    application.add_exception_handler(RequestValidationError, _handle_validation_error)
    application.add_exception_handler(StarletteHTTPException, _handle_http_error)
    application.add_exception_handler(Exception, _handle_unexpected_error)


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
    details = [_validation_detail(error_detail) for error_detail in error.errors()]
    return _error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "request_validation_failed",
        "Request validation failed",
        details,
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


async def _handle_unexpected_error(
    request: Request, _error: Exception
) -> JSONResponse:
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
) -> JSONResponse:
    request_id = get_request_id(request)
    headers = dict(extra_headers or {})
    headers[REQUEST_ID_HEADER] = request_id
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id,
        details=details or [],
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
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
