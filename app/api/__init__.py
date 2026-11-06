"""FastAPI delivery layer for POS Backend."""

from app.api.errors import register_exception_handlers
from app.api.router import create_api_router

__all__ = ["create_api_router", "register_exception_handlers"]
