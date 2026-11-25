"""Authenticated feature flags for the current account."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import documented_errors
from app.api.feature_schemas import FeatureFlagsResponse, VoiceEntryFlagResponse
from app.api.routes.onboarding import require_completed_onboarding
from app.application.feature_flags import FeatureFlagService
from app.application.onboarding import OnboardingDependencyUnavailableError
from app.application.session_lifecycle import AuthenticationRequiredError
from app.domain.onboarding import OnboardingState


def create_features_router(flags: FeatureFlagService, onboarding, sessions) -> APIRouter:
    router = APIRouter(tags=["Feature flags"])
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

    def no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    headers = {
        "X-Request-ID": {
            "description": "Validated caller correlation ID or generated server ID",
            "schema": {"type": "string"},
        },
        "Cache-Control": {
            "description": "Account-specific responses are not stored",
            "schema": {"type": "string", "const": "no-store"},
        },
        "Pragma": {
            "description": "Legacy cache prevention",
            "schema": {"type": "string", "const": "no-cache"},
        },
    }
    errors = documented_errors(401, 403, 500, 503)

    @router.get(
        "/api/v1/features",
        operation_id="get_feature_flags",
        response_model=FeatureFlagsResponse,
        responses={
            **{code: {**value, "headers": headers} for code, value in errors.items()},
            200: {"description": "Feature flags for the authenticated account", "headers": headers},
        },
        summary="Read feature flags for the authenticated account",
        description=(
            "Feature flags contract 1.0. Requires the same authentication and completed "
            "onboarding as Today and Plan. The response states only whether each feature "
            "is enabled for the caller; it never reveals configuration, rollout mode or "
            "other accounts. Responses are never cached."
        ),
    )
    def get_feature_flags(
        request: Request,
        response: Response,
        state: Annotated[OnboardingState, Depends(gate)],
    ) -> FeatureFlagsResponse:
        no_store(response)
        result = flags.flags_for(state.user_account_id)
        return FeatureFlagsResponse(
            contract_version="1.0",
            voice_entry=VoiceEntryFlagResponse(enabled=result.voice_entry_enabled),
        )

    return router
