"""HTTP delivery for the existing local mock resource use cases."""

from typing import Any

from fastapi import APIRouter, Response, status

from app.api.errors import documented_errors
from app.api.schemas import Item, ItemPatch, ResetResponse
from app.application import MockResourceService


def create_resources_router(service: MockResourceService) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/{resource}",
        operation_id="get_mock_resource",
        summary="Get a local mock resource",
        tags=["Mock resources"],
        status_code=status.HTTP_200_OK,
        responses=documented_errors(
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    )
    def get_resource(resource: str) -> Any:
        return service.get_resource(resource)

    @router.post(
        "/{resource}",
        operation_id="create_mock_resource_item",
        summary="Add an item to a local mock collection",
        tags=["Mock resources"],
        response_model=Item,
        status_code=status.HTTP_200_OK,
        responses=documented_errors(
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    )
    def add_item(resource: str, item: Item) -> dict[str, Any]:
        return service.add_item(resource, item.model_dump())

    @router.patch(
        "/{resource}/{item_id}",
        operation_id="update_mock_resource_item",
        summary="Update an item in a local mock collection",
        tags=["Mock resources"],
        response_model=dict[str, Any],
        status_code=status.HTTP_200_OK,
        responses=documented_errors(
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    )
    def update_item(
        resource: str,
        item_id: str,
        patch: ItemPatch,
    ) -> dict[str, Any]:
        return service.update_item(resource, item_id, patch.root)

    @router.delete(
        "/{resource}/{item_id}",
        operation_id="delete_mock_resource_item",
        summary="Delete an item from a local mock collection",
        tags=["Mock resources"],
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
        responses=documented_errors(
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    )
    def delete_item(resource: str, item_id: str) -> Response:
        service.delete_item(resource, item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/demo/reset",
        operation_id="reset_mock_demo",
        summary="Reset all local mock data",
        response_model=ResetResponse,
        status_code=status.HTTP_200_OK,
        tags=["Demo"],
        responses=documented_errors(status.HTTP_500_INTERNAL_SERVER_ERROR),
    )
    def reset_demo() -> ResetResponse:
        service.reset_demo()
        return ResetResponse(status="reset")

    return router
