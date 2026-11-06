"""Service health delivery endpoint."""

from fastapi import APIRouter, status

from app.api.errors import documented_errors
from app.api.schemas import HealthResponse


router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    operation_id="get_health",
    summary="Check service health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    responses=documented_errors(status.HTTP_500_INTERNAL_SERVER_ERROR),
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")
