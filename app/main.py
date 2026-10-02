"""FastAPI application composition and ASGI entry point."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api import create_api_router, register_exception_handlers
from app.application import MockResourceService, ResourceUnitOfWorkFactory
from app.application.email_registration import (
    EmailRegistrationService,
    LegalPolicyStatus,
    RegistrationPolicyResult,
)
from app.application.session_lifecycle import EmailSessionService
from app.application.password_recovery import PasswordRecoveryService
from app.config import AuthenticationLegalConsentMode, Settings, load_settings
from app.core.authentication_security import AuthenticationSecurityMiddleware
from app.infrastructure.authentication.abuse_control import compose_admission
from app.infrastructure.authentication.registration import SafeAuthenticationEventLogger
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
from app.infrastructure.authentication.password_recovery_composition import (
    compose_password_recovery,
)
from app.infrastructure.database import dispose_database_engine
from app.application.onboarding import OnboardingService
from app.infrastructure.onboarding import compose_onboarding
from app.application.planning import PlanService
from app.infrastructure.planning import compose_planning
from app.api.routes.planning import create_planning_router


AUTHENTICATION_CONTRACT_VERSION = "1.1.0"


def create_app(
    app_settings: Settings | None = None,
    resource_unit_of_work_factory: ResourceUnitOfWorkFactory | None = None,
    email_registration_service: EmailRegistrationService | None = None,
    email_session_service: EmailSessionService | None = None,
    password_recovery_service: PasswordRecoveryService | None = None,
    onboarding_service: OnboardingService | None = None,
    plan_service: PlanService | None = None,
) -> FastAPI:
    """Compose one isolated FastAPI application from concrete local dependencies."""
    resolved_settings = app_settings if app_settings is not None else load_settings()
    application = FastAPI(title="POS Local Mock API", version="0.1.0")

    admission, close_admission = compose_admission(resolved_settings)
    application.add_event_handler("shutdown", close_admission)
    application.add_middleware(
        AuthenticationSecurityMiddleware,
        admission=admission,
        settings=resolved_settings,
        diagnostic=SafeAuthenticationEventLogger(),
    )
    configure_cors(application, resolved_settings)
    configure_request_ids(application)
    register_exception_handlers(application)

    if resource_unit_of_work_factory is None:
        resource_store = InMemoryResourceStore()
        resource_unit_of_work_factory = InMemoryResourceUnitOfWorkFactory(
            resource_store
        )

    resource_service = MockResourceService(resource_unit_of_work_factory)
    authentication_availability_diagnostic = SafeAuthenticationAvailabilityDiagnostic()
    authentication_composition = None
    session_composition = None
    password_recovery_composition = None
    onboarding_composition = None
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
            verification_continuation=email_registration_service,
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

    if onboarding_service is None:
        shared_engine = (
            authentication_composition.engine
            if authentication_composition is not None
            else (
                session_composition.engine
                if session_composition is not None
                else (
                    password_recovery_composition.engine
                    if password_recovery_composition is not None
                    else None
                )
            )
        )
        onboarding_composition = compose_onboarding(
            resolved_settings, shared_engine=shared_engine
        )
        if onboarding_composition is not None:
            onboarding_service = onboarding_composition.service

    application.include_router(
        create_api_router(
            resource_service,
            email_registration_service,
            authentication_availability_diagnostic,
            email_session_service,
            password_recovery_service,
            (
                RegistrationPolicyResult(
                    legal_consent_required=(
                        resolved_settings.auth_legal_consent_mode
                        is AuthenticationLegalConsentMode.REQUIRED
                    ),
                    legal_policy_status=(
                        LegalPolicyStatus.ACTIVE
                        if resolved_settings.auth_legal_consent_mode
                        is AuthenticationLegalConsentMode.REQUIRED
                        else LegalPolicyStatus.DEFERRED_NONPRODUCTION
                    ),
                    terms_version=(
                        resolved_settings.current_terms_version
                        if resolved_settings.auth_legal_consent_mode
                        is AuthenticationLegalConsentMode.REQUIRED
                        else None
                    ),
                    terms_url=(
                        resolved_settings.current_terms_url
                        if resolved_settings.auth_legal_consent_mode
                        is AuthenticationLegalConsentMode.REQUIRED
                        else None
                    ),
                    privacy_version=(
                        resolved_settings.current_privacy_version
                        if resolved_settings.auth_legal_consent_mode
                        is AuthenticationLegalConsentMode.REQUIRED
                        else None
                    ),
                    privacy_url=(
                        resolved_settings.current_privacy_url
                        if resolved_settings.auth_legal_consent_mode
                        is AuthenticationLegalConsentMode.REQUIRED
                        else None
                    ),
                )
                if (
                    resolved_settings.auth_legal_consent_mode
                    is AuthenticationLegalConsentMode.DEFERRED_NONPRODUCTION
                    or (
                        resolved_settings.current_terms_version is not None
                        and resolved_settings.current_terms_url is not None
                        and resolved_settings.current_privacy_version is not None
                        and resolved_settings.current_privacy_url is not None
                    )
                )
                else None
            ),
            onboarding_service,
        )
    )

    if plan_service is None and onboarding_composition is not None:
        plan_service = compose_planning(onboarding_composition.engine)
    application.include_router(
        create_planning_router(plan_service, onboarding_service, email_session_service)
    )

    def authentication_contract_openapi() -> dict[str, object]:
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
            )
            schema["x-pos-authentication-contract-version"] = (
                AUTHENTICATION_CONTRACT_VERSION
            )
            schema["x-pos-today-contract-version"] = "1.0"
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = authentication_contract_openapi
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
    if (
        onboarding_composition is not None
        and authentication_composition is None
        and session_composition is None
        and password_recovery_composition is None
    ):
        application.add_event_handler(
            "shutdown", lambda: dispose_database_engine(onboarding_composition.engine)
        )

    return application


app = create_app()
