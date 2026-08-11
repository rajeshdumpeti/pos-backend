"""Framework-independent rules for mutable local resource collections."""

from collections.abc import Mapping
from typing import Any

from app.domain.errors import ItemNotFoundError, ResourceMutationNotAllowedError


def add_resource_item(
    resource: Any,
    item: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = _require_item_list(resource, "Resource does not accept items")
    new_item = dict(item)
    return [new_item, *items], new_item


def update_resource_item(
    resource: Any,
    item_id: str,
    patch: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = _require_item_list(resource, "Resource is not a list")
    updated_items: list[dict[str, Any]] = []
    updated_item: dict[str, Any] | None = None

    for item in items:
        if item.get("id") == item_id:
            updated_item = {**item, **patch}
            updated_items.append(updated_item)
        else:
            updated_items.append(item)

    if updated_item is None:
        raise ItemNotFoundError("Item not found")

    return updated_items, updated_item


def delete_resource_item(resource: Any, item_id: str) -> list[dict[str, Any]]:
    items = _require_item_list(resource, "Resource is not a list")
    return [item for item in items if item.get("id") != item_id]


def _require_item_list(resource: Any, error_message: str) -> list[dict[str, Any]]:
    if not isinstance(resource, list):
        raise ResourceMutationNotAllowedError(error_message)
    return resource
