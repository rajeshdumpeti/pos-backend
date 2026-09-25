"""Thin authenticated HTTP routes for authoritative onboarding."""

from decimal import Decimal
from typing import Annotated, Any, Callable, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import documented_errors
from app.api.onboarding_schemas import (
    CONFIGURATION,
    CompleteOnboardingRequest,
    CurrencyPreferenceResponse,
    IntegrationPreferencesResponse,
    NotificationPreferencesResponse,
    OnboardingGoalsResponse,
    OnboardingPatchRequest,
    OnboardingResponse,
    RequirementsResponse,
    UnitPreferenceResponse,
)
from app.application.onboarding import OnboardingService, OnboardingUpdate
from app.application.session_lifecycle import (
    AuthenticationRequiredError,
    EmailSessionService,
)
from app.core import get_request_id
from app.domain.onboarding import OnboardingState, decimal_to_storage


def create_onboarding_router(
    service: OnboardingService | None, sessions: EmailSessionService | None
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/onboarding", tags=["Onboarding"])
    bearer = HTTPBearer(auto_error=False)

    def principal(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> UUID:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AuthenticationRequiredError("Authentication is required")
        if sessions is None or service is None:
            from app.application.onboarding import OnboardingDependencyUnavailableError

            raise OnboardingDependencyUnavailableError(
                "Onboarding dependencies are unavailable"
            )
        claims, _account = sessions.authenticate(
            credentials.credentials, get_request_id(request)
        )
        return claims.account_id

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    @router.get(
        "",
        operation_id="get_onboarding_state",
        response_model=OnboardingResponse,
        responses=documented_errors(401, 403, 422, 500, 503),
    )
    def get_state(
        request: Request,
        response: Response,
        account_id: Annotated[UUID, Depends(principal)],
    ) -> OnboardingResponse:
        no_store(response)
        assert service is not None
        return _response(service.get(account_id, get_request_id(request)))

    @router.patch(
        "",
        operation_id="update_onboarding_state",
        response_model=OnboardingResponse,
        responses=documented_errors(400, 401, 403, 409, 422, 500, 503),
    )
    def update_state(
        payload: OnboardingPatchRequest,
        request: Request,
        response: Response,
        account_id: Annotated[UUID, Depends(principal)],
    ) -> OnboardingResponse:
        no_store(response)
        assert service is not None
        changes = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
        changes = _storage_values(changes)
        state = service.update(
            account_id,
            OnboardingUpdate(payload.expected_version, changes),
            get_request_id(request),
        )
        return _response(state)

    @router.post(
        "/complete",
        operation_id="complete_onboarding",
        response_model=OnboardingResponse,
        responses=documented_errors(401, 403, 409, 422, 500, 503),
    )
    def complete(
        payload: CompleteOnboardingRequest,
        request: Request,
        response: Response,
        account_id: Annotated[UUID, Depends(principal)],
    ) -> OnboardingResponse:
        no_store(response)
        assert service is not None
        return _response(
            service.complete(
                account_id, payload.expected_version, get_request_id(request)
            )
        )

    return router


def require_completed_onboarding(
    service: OnboardingService, sessions: EmailSessionService
) -> Callable[..., OnboardingState]:
    """Reusable opt-in dependency; intentionally not installed globally."""
    bearer = HTTPBearer(auto_error=False)

    def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> OnboardingState:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AuthenticationRequiredError("Authentication is required")
        claims, _ = sessions.authenticate(
            credentials.credentials, get_request_id(request)
        )
        return service.require_completed(claims.account_id, get_request_id(request))

    return dependency


def _storage_values(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_to_storage(value)
    if isinstance(value, dict):
        return {
            key: _storage_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_storage_values(item) for item in value]
    return value


def _response(state: OnboardingState) -> OnboardingResponse:
    return OnboardingResponse(
        status=cast(Any, state.status),
        contract_version="1.0",
        version=state.version,
        priority_life_areas=list(state.priority_life_areas),
        unit_preference=UnitPreferenceResponse(
            value=state.unit_system, confirmed=state.unit_system_confirmed
        ),
        currency_preference=CurrencyPreferenceResponse(
            value=state.currency, confirmed=state.currency_confirmed
        ),
        goals=OnboardingGoalsResponse(**state.goals),
        integration_preferences=IntegrationPreferencesResponse(
            **state.integration_preferences
        ),
        notification_preferences=NotificationPreferencesResponse(
            **state.notification_preferences
        ),
        requirements=RequirementsResponse(
            priority_life_areas_satisfied=bool(state.priority_life_areas),
            unit_system_satisfied=bool(
                state.unit_system and state.unit_system_confirmed
            ),
            currency_satisfied=bool(state.currency and state.currency_confirmed),
            ready_to_complete=state.ready_to_complete,
        ),
        recommended_next_action=cast(Any, state.recommended_next_action),
        completed_at=state.completed_at,
        configuration=CONFIGURATION,
    )
