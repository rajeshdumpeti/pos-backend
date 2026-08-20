"""FastAPI application composition and ASGI entry point."""

from fastapi import FastAPI

from app.api import create_api_router, register_exception_handlers
from app.application import MockResourceService, ResourceUnitOfWorkFactory
from app.config import Settings, load_settings
from app.core import configure_cors, configure_request_ids
from app.infrastructure import (
    InMemoryResourceStore,
    InMemoryResourceUnitOfWorkFactory,
)


def create_app(
    app_settings: Settings | None = None,
    resource_unit_of_work_factory: ResourceUnitOfWorkFactory | None = None,
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
    application.include_router(create_api_router(resource_service))

    return application


app = create_app()
