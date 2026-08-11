"""Use cases for the existing local mock resource API."""

from collections.abc import Mapping
from typing import Any, Protocol

from app.domain import (
    UnknownResourceError,
    add_resource_item,
    delete_resource_item,
    update_resource_item,
)


class ResourceStore(Protocol):
    """Storage capability required by the current resource use cases."""

    def contains(self, resource: str) -> bool: ...

    def read(self, resource: str) -> Any: ...

    def write(self, resource: str, value: Any) -> None: ...

    def reset(self) -> None: ...


class MockResourceService:
    """Coordinates resource rules without depending on FastAPI or configuration."""

    def __init__(self, store: ResourceStore) -> None:
        self._store = store

    def get_resource(self, resource: str) -> Any:
        if resource == "search":
            return []
        if not self._store.contains(resource):
            raise UnknownResourceError("Unknown resource")
        return self._store.read(resource)

    def add_item(self, resource: str, item: Mapping[str, Any]) -> dict[str, Any]:
        value = self._read_for_mutation(resource)
        updated_resource, new_item = add_resource_item(value, item)
        self._store.write(resource, updated_resource)
        return new_item

    def update_item(
        self,
        resource: str,
        item_id: str,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = self._read_for_mutation(resource)
        updated_resource, updated_item = update_resource_item(value, item_id, patch)
        self._store.write(resource, updated_resource)
        return updated_item

    def delete_item(self, resource: str, item_id: str) -> None:
        value = self._read_for_mutation(resource)
        self._store.write(resource, delete_resource_item(value, item_id))

    def reset_demo(self) -> None:
        self._store.reset()

    def _read_for_mutation(self, resource: str) -> Any:
        return self._store.read(resource) if self._store.contains(resource) else None
