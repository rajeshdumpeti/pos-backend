"""Thin product routes reuse the authoritative onboarding boundary."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.api.errors import documented_errors
from app.api.routes.onboarding import require_completed_onboarding
from app.api.planning_schemas import (
    CreatePlanItemRequest,
    UpdatePlanItemRequest,
    CompletePlanItemRequest,
    PlanItemResponse,
    TodayResponse,
    TodayTimezoneQuery,
)
from app.application.planning import PlanService
from app.application.today import TodayService
from app.application.onboarding import OnboardingDependencyUnavailableError
from app.application.session_lifecycle import AuthenticationRequiredError
from app.core import get_request_id
from app.domain.onboarding import OnboardingState
from app.domain.planning import PlanIntent, PlanItem, PlanUnavailableError


def _response(item: PlanItem) -> PlanItemResponse:
    return PlanItemResponse(
        id=item.id,
        **asdict(item.intent),
        completed_at=item.completed_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version
    )


def create_planning_router(
    plans: PlanService | None, onboarding, sessions
) -> APIRouter:
    router = APIRouter(tags=["Daily planning and Today"])
    if onboarding is not None and sessions is not None:
        gate = require_completed_onboarding(onboarding, sessions)
    else:
        bearer = HTTPBearer(auto_error=False)

        def gate(
            request: Request,
            credentials: HTTPAuthorizationCredentials | None = Security(bearer),
        ) -> OnboardingState:
            if credentials is None:
                raise AuthenticationRequiredError("Authentication is required")
            raise OnboardingDependencyUnavailableError(
                "Product dependencies are unavailable"
            )

    def service() -> PlanService:
        if plans is None:
            raise PlanUnavailableError("Planning dependencies are unavailable")
        return plans

    def no_store(response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    errors = documented_errors(401, 403, 404, 409, 422, 500, 503)

    def responses(success_code: int):
        headers = {
            "X-Request-ID": {
                "description": "Validated caller correlation ID or generated server ID",
                "schema": {"type": "string"},
            },
            "Cache-Control": {
                "description": "Sensitive responses are not stored",
                "schema": {"type": "string", "const": "no-store"},
            },
            "Pragma": {
                "description": "Legacy cache prevention",
                "schema": {"type": "string", "const": "no-cache"},
            },
        }
        return {
            **{code: {**value, "headers": headers} for code, value in errors.items()},
            success_code: {
                "description": "Authoritative planning response",
                "headers": headers,
            },
        }

    @router.post(
        "/api/v1/plan/items",
        status_code=201,
        operation_id="create_plan_item",
        response_model=PlanItemResponse,
        responses=responses(201),
    )
    def create(
        payload: CreatePlanItemRequest,
        request: Request,
        response: Response,
        state: Annotated[OnboardingState, Depends(gate)],
        key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
            ),
        ],
    ):
        no_store(response)
        return _response(
            service().create(
                state.user_account_id,
                PlanIntent(**payload.model_dump()),
                key,
                get_request_id(request),
            )
        )

    @router.get(
        "/api/v1/plan/items/{plan_item_id}",
        operation_id="get_plan_item",
        response_model=PlanItemResponse,
        responses=responses(200),
    )
    def get(
        plan_item_id: UUID,
        request: Request,
        response: Response,
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return _response(
            service().get(state.user_account_id, plan_item_id, get_request_id(request))
        )

    @router.patch(
        "/api/v1/plan/items/{plan_item_id}",
        operation_id="update_plan_item",
        response_model=PlanItemResponse,
        responses=responses(200),
    )
    def update(
        plan_item_id: UUID,
        payload: UpdatePlanItemRequest,
        request: Request,
        response: Response,
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return _response(
            service().update(
                state.user_account_id,
                plan_item_id,
                payload.expected_version,
                payload.model_dump(exclude={"expected_version"}, exclude_unset=True),
                get_request_id(request),
            )
        )

    @router.post(
        "/api/v1/plan/items/{plan_item_id}/complete",
        operation_id="complete_plan_item",
        response_model=PlanItemResponse,
        responses=responses(200),
    )
    def complete(
        plan_item_id: UUID,
        payload: CompletePlanItemRequest,
        request: Request,
        response: Response,
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return _response(
            service().complete(
                state.user_account_id,
                plan_item_id,
                payload.expected_version,
                get_request_id(request),
            )
        )

    @router.get(
        "/api/v1/today",
        operation_id="get_today_dashboard",
        response_model=TodayResponse,
        responses=responses(200),
    )
    def today(
        request: Request,
        response: Response,
        query: Annotated[TodayTimezoneQuery, Query()],
        state: Annotated[OnboardingState, Depends(gate)],
    ):
        no_store(response)
        return TodayResponse.model_validate(
            TodayService(service()).get(
                state.user_account_id, query.timezone, state, get_request_id(request)
            )
        )

    return router
