"""Route modules composed by app.api.router."""

from app.api.routes.health import router as health_router
from app.api.routes.resources import create_resources_router

__all__ = ["create_resources_router", "health_router"]
