"""Central registration for all HTTP routes."""

from fastapi import APIRouter

from app.api.routes import create_resources_router, health_router
from app.application import MockResourceService


def create_api_router(resource_service: MockResourceService) -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(create_resources_router(resource_service))
    return router
