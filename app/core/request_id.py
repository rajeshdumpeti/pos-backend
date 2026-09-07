"""Minimal request correlation for local API responses and errors."""

import re
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = _new_request_id()
        request.state.request_id = request_id
    return request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        candidates = request.headers.getlist(REQUEST_ID_HEADER)
        request_id = _resolve_request_id(candidates[0] if len(candidates) == 1 else None)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def configure_request_ids(application: FastAPI) -> None:
    application.add_middleware(RequestIdMiddleware)


def _resolve_request_id(candidate: str | None) -> str:
    if candidate is not None and _SAFE_REQUEST_ID.fullmatch(candidate):
        return candidate
    return _new_request_id()


def _new_request_id() -> str:
    return uuid4().hex
