"""Service health delivery endpoint."""

from fastapi import APIRouter, Request, Response, status

from app.infrastructure.database_readiness import check_database_readiness

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


@router.get(
    "/health/ready",
    operation_id="get_database_readiness",
    summary="Check database connectivity and schema compatibility",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
def readiness(request: Request, response: Response) -> HealthResponse:
    engine = getattr(request.app.state, "database_engine", None)
    try:
        ready = engine is not None and check_database_readiness(engine)
    except Exception:
        # Readiness must fail closed, including missing migration artifacts.
        # Never render driver exceptions or connection configuration.
        ready = False
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="unavailable")
    return HealthResponse(status="ok")
