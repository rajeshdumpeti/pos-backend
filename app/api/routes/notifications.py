"""Thin Inbox routes; no client-created notification or job endpoint."""

from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.api.errors import documented_errors
from app.api.routes.onboarding import require_completed_onboarding
from app.api.notification_schemas import (
    InboxTimezoneRequest,
    InboxTimezoneResponse,
    InboxSourceQuery,
    InboxListQuery,
    InboxListResponse,
    InboxUnreadResponse,
    InboxReadResponse,
    InboxReadAllRequest,
    InboxReadAllResponse,
    InboxEmptyQuery,
)
from app.application.notifications import NotificationService
from app.application.session_lifecycle import AuthenticationRequiredError
from app.core import get_request_id
from app.domain.notifications import InboxUnavailableError
from app.domain.onboarding import OnboardingState


def create_notification_router(
    inbox: NotificationService | None, onboarding, sessions
) -> APIRouter:
    router = APIRouter(tags=["Notification Inbox"])
    bearer = HTTPBearer(auto_error=False)

    def principal(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> UUID:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AuthenticationRequiredError("Authentication is required")
        if sessions is None or onboarding is None:
            raise InboxUnavailableError()
        claims, _ = sessions.authenticate(
            credentials.credentials, get_request_id(request)
        )
        onboarding.get(claims.account_id, get_request_id(request))
        return claims.account_id

    if sessions is not None and onboarding is not None:
        gate = require_completed_onboarding(onboarding, sessions)
    else:

        def gate(owner: Annotated[UUID, Depends(principal)]):
            raise InboxUnavailableError()

    def service() -> NotificationService:
        if inbox is None:
            raise InboxUnavailableError()
        return inbox

    def no_store(response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    headers = {
        "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
        "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
        "X-Request-ID": {"schema": {"type": "string"}},
    }
    responses = {
        code: {**value, "headers": headers}
        for code, value in documented_errors(401, 403, 404, 422, 500, 503).items()
    }
    responses[200] = {
        "description": "Authoritative account-owned state; no-store, no-cache",
        "headers": headers,
    }

    @router.put(
        "/api/v1/account/timezone",
        operation_id="put_account_timezone",
        response_model=InboxTimezoneResponse,
        responses=responses,
        description="Authenticated active verified account; onboarding completion is not required. Idempotent identical updates; changed versions atomically replace the pending 18:00 local evaluation. No OS permission dependency.",
    )
    def timezone(
        payload: InboxTimezoneRequest,
        response: Response,
        owner: Annotated[UUID, Depends(principal)],
        query: Annotated[InboxEmptyQuery, Query()],
    ):
        no_store(response)
        value = service().put_timezone(owner, payload.timezone)
        return InboxTimezoneResponse(
            contract_version="1.0",
            timezone=value.timezone,
            updated_at=value.updated_at,
            version=value.version,
        )

    @router.get(
        "/api/v1/notifications",
        operation_id="list_notification_inbox",
        response_model=InboxListResponse,
        responses=responses,
        description="Inbox contract 1.0. Account-owned unexpired snapshots; expiry is exactly 90 elapsed days. Descending created_at then UUID; opaque keyset cursor binds account/filter/timezone and retained boundary. No cross-page snapshot guarantee. Unread count spans the complete filtered retained set. Today/Earlier uses created_at in the requested IANA timezone. Unavailable navigation strips references; completed/changed owned Plan targets remain navigable. Requires completed onboarding; independent of OS permission.",
    )
    def listing(
        response: Response,
        query: Annotated[InboxListQuery, Query()],
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return service().list(
            state.user_account_id,
            query.source,
            query.timezone,
            query.limit,
            query.cursor,
        )

    @router.get(
        "/api/v1/notifications/unread-count",
        operation_id="get_notification_unread_count",
        response_model=InboxUnreadResponse,
        responses=responses,
    )
    def count(
        response: Response,
        query: Annotated[InboxSourceQuery, Query()],
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return service().count(state.user_account_id, query.source)

    @router.post(
        "/api/v1/notifications/read-all",
        operation_id="mark_all_notifications_read",
        response_model=InboxReadAllResponse,
        responses=responses,
        description="Atomically mark all retained unread rows in the explicit source scope, using one server timestamp. Repeated execution changes zero rows. Count covers the selected scope.",
    )
    def read_all(
        payload: InboxReadAllRequest,
        response: Response,
        query: Annotated[InboxEmptyQuery, Query()],
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return service().mark_all(state.user_account_id, payload.source)

    @router.post(
        "/api/v1/notifications/{notification_id}/read",
        operation_id="mark_notification_read",
        response_model=InboxReadResponse,
        responses=responses,
        description="Idempotent account-owned read transition preserves first read_at. Missing/expired/foreign records share one safe 404. Resulting count is account-wide across both sources.",
    )
    def read(
        notification_id: UUID,
        response: Response,
        query: Annotated[InboxEmptyQuery, Query()],
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return service().mark(state.user_account_id, notification_id)

    return router
