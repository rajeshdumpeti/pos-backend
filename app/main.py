"""FastAPI application composition and ASGI entry point."""

from fastapi import FastAPI

from app.api import create_api_router
from app.application import MockResourceService
from app.config import Settings, settings
from app.core import configure_cors
from app.infrastructure import InMemoryResourceStore


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Compose one isolated FastAPI application from concrete local dependencies."""
    resolved_settings = app_settings or settings
    application = FastAPI(title="POS Local Mock API", version="0.1.0")

    configure_cors(application, resolved_settings)

    resource_store = InMemoryResourceStore()
    resource_service = MockResourceService(resource_store)
    application.include_router(create_api_router(resource_service))

    return application


app = create_app()
