"""Central registration for all HTTP routes."""

from fastapi import APIRouter

from app.api.routes import (
    create_authentication_router,
    create_resources_router,
    health_router,
)
from app.application.email_registration import (
    AuthenticationAvailabilityDiagnostic,
    EmailRegistrationService,
)
from app.application import MockResourceService
from app.application.session_lifecycle import EmailSessionService


def create_api_router(
    resource_service: MockResourceService,
    email_registration_service: EmailRegistrationService | None = None,
    authentication_availability_diagnostic: AuthenticationAvailabilityDiagnostic | None = None,
    email_session_service: EmailSessionService | None = None,
) -> APIRouter:
    if authentication_availability_diagnostic is None:
        raise ValueError("Authentication availability diagnostic is required")
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(
        create_authentication_router(
            email_registration_service,
            authentication_availability_diagnostic,
            email_session_service,
        )
    )
    router.include_router(create_resources_router(resource_service))
    return router
