"""Use cases for the existing local mock resource API."""

from collections.abc import Mapping
from typing import Any

from app.application.persistence import (
    ResourceRepository,
    ResourceUnitOfWorkFactory,
)
from app.domain import (
    UnknownResourceError,
    add_resource_item,
    delete_resource_item,
    update_resource_item,
)


class MockResourceService:
    """Coordinates resource rules without depending on FastAPI or configuration."""

    def __init__(self, unit_of_work_factory: ResourceUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get_resource(self, resource: str) -> Any:
        if resource == "search":
            return []
        with self._unit_of_work_factory() as unit_of_work:
            if not unit_of_work.resources.contains(resource):
                raise UnknownResourceError("Unknown resource")
            return unit_of_work.resources.read(resource)

    def add_item(self, resource: str, item: Mapping[str, Any]) -> dict[str, Any]:
        with self._unit_of_work_factory() as unit_of_work:
            value = self._read_for_mutation(unit_of_work.resources, resource)
            updated_resource, new_item = add_resource_item(value, item)
            unit_of_work.resources.write(resource, updated_resource)
            unit_of_work.commit()
            return new_item

    def update_item(
        self,
        resource: str,
        item_id: str,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._unit_of_work_factory() as unit_of_work:
            value = self._read_for_mutation(unit_of_work.resources, resource)
            updated_resource, updated_item = update_resource_item(
                value, item_id, patch
            )
            unit_of_work.resources.write(resource, updated_resource)
            unit_of_work.commit()
            return updated_item

    def delete_item(self, resource: str, item_id: str) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            value = self._read_for_mutation(unit_of_work.resources, resource)
            unit_of_work.resources.write(
                resource, delete_resource_item(value, item_id)
            )
            unit_of_work.commit()

    def reset_demo(self) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.resources.reset()
            unit_of_work.commit()

    @staticmethod
    def _read_for_mutation(repository: ResourceRepository, resource: str) -> Any:
        return repository.read(resource) if repository.contains(resource) else None
