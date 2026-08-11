"""HTTP translation for the existing local mock resource use cases."""

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException

from app.api.schemas import Item
from app.application import MockResourceService
from app.domain import (
    ItemNotFoundError,
    ResourceMutationNotAllowedError,
    UnknownResourceError,
)


def create_resources_router(service: MockResourceService) -> APIRouter:
    router = APIRouter()

    @router.get("/{resource}")
    def get_resource(resource: str) -> Any:
        try:
            return service.get_resource(resource)
        except UnknownResourceError as error:
            _raise_http_error(error)

    @router.post("/{resource}")
    def add_item(resource: str, item: Item) -> dict[str, Any]:
        try:
            return service.add_item(resource, item.model_dump())
        except ResourceMutationNotAllowedError as error:
            _raise_http_error(error)

    @router.patch("/{resource}/{item_id}")
    def update_item(
        resource: str,
        item_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return service.update_item(resource, item_id, patch)
        except (ItemNotFoundError, ResourceMutationNotAllowedError) as error:
            _raise_http_error(error)

    @router.delete("/{resource}/{item_id}", status_code=204)
    def delete_item(resource: str, item_id: str) -> None:
        try:
            service.delete_item(resource, item_id)
        except ResourceMutationNotAllowedError as error:
            _raise_http_error(error)

    @router.post("/demo/reset")
    def reset_demo() -> dict[str, str]:
        service.reset_demo()
        return {"status": "reset"}

    return router


def _raise_http_error(error: Exception) -> NoReturn:
    status_code = 404 if isinstance(error, (UnknownResourceError, ItemNotFoundError)) else 400
    raise HTTPException(status_code=status_code, detail=str(error)) from error
