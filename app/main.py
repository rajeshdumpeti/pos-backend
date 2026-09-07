"""FastAPI application composition and ASGI entry point."""

from fastapi import FastAPI

from app.api import create_api_router, register_exception_handlers
from app.application import MockResourceService, ResourceUnitOfWorkFactory
from app.application.email_registration import EmailRegistrationService
from app.application.session_lifecycle import EmailSessionService
from app.application.password_recovery import PasswordRecoveryService
from app.config import Settings, load_settings
from app.core import configure_cors, configure_request_ids
from app.infrastructure import (
    InMemoryResourceStore,
    InMemoryResourceUnitOfWorkFactory,
)
from app.infrastructure.authentication.composition import compose_email_registration
from app.infrastructure.authentication.registration import (
    SafeAuthenticationAvailabilityDiagnostic,
)
from app.infrastructure.authentication.session_composition import compose_email_sessions
from app.infrastructure.authentication.password_recovery_composition import compose_password_recovery
from app.infrastructure.database import dispose_database_engine


def create_app(
    app_settings: Settings | None = None,
    resource_unit_of_work_factory: ResourceUnitOfWorkFactory | None = None,
    email_registration_service: EmailRegistrationService | None = None,
    email_session_service: EmailSessionService | None = None,
    password_recovery_service: PasswordRecoveryService | None = None,
) -> FastAPI:
    """Compose one isolated FastAPI application from concrete local dependencies."""
    resolved_settings = app_settings if app_settings is not None else load_settings()
    application = FastAPI(title="POS Local Mock API", version="0.1.0")

    configure_cors(application, resolved_settings)
    configure_request_ids(application)
    register_exception_handlers(application)

    if resource_unit_of_work_factory is None:
        resource_store = InMemoryResourceStore()
        resource_unit_of_work_factory = InMemoryResourceUnitOfWorkFactory(
            resource_store
        )

    resource_service = MockResourceService(resource_unit_of_work_factory)
    authentication_availability_diagnostic = (
        SafeAuthenticationAvailabilityDiagnostic()
    )
    authentication_composition = None
    session_composition = None
    password_recovery_composition = None
    if email_registration_service is None:
        authentication_composition = compose_email_registration(resolved_settings)
        if authentication_composition is not None:
            email_registration_service = authentication_composition.service
    if email_session_service is None:
        session_composition = compose_email_sessions(
            resolved_settings,
            shared_engine=(
                None
                if authentication_composition is None
                else authentication_composition.engine
            ),
        )
        if session_composition is not None:
            email_session_service = session_composition.service
    if password_recovery_service is None:
        shared_engine = (
            authentication_composition.engine
            if authentication_composition is not None
            else session_composition.engine if session_composition is not None else None
        )
        password_recovery_composition = compose_password_recovery(
            resolved_settings, shared_engine=shared_engine
        )
        if password_recovery_composition is not None:
            password_recovery_service = password_recovery_composition.service

    application.include_router(
        create_api_router(
            resource_service,
            email_registration_service,
            authentication_availability_diagnostic,
            email_session_service,
            password_recovery_service,
        )
    )
    if authentication_composition is not None:
        application.add_event_handler(
            "shutdown",
            lambda: dispose_database_engine(authentication_composition.engine),
        )
    if session_composition is not None and authentication_composition is None:
        application.add_event_handler(
            "shutdown", lambda: dispose_database_engine(session_composition.engine)
        )
    if (
        password_recovery_composition is not None
        and authentication_composition is None
        and session_composition is None
    ):
        application.add_event_handler(
            "shutdown",
            lambda: dispose_database_engine(password_recovery_composition.engine),
        )

    return application


app = create_app()
