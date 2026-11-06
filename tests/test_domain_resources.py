import pytest

from app.domain import (
    ItemNotFoundError,
    ResourceMutationNotAllowedError,
    add_resource_item,
    update_resource_item,
)


def test_resource_rules_return_updated_collections_without_mutating_input():
    existing = [{"id": "one", "title": "Existing", "done": False}]

    items, added = add_resource_item(existing, {"id": "two", "title": "Added"})
    updated_items, updated = update_resource_item(items, "two", {"done": True})

    assert existing == [{"id": "one", "title": "Existing", "done": False}]
    assert added == {"id": "two", "title": "Added"}
    assert updated == {"id": "two", "title": "Added", "done": True}
    assert updated_items[0] == updated


def test_resource_rules_raise_framework_independent_errors():
    with pytest.raises(ResourceMutationNotAllowedError, match="does not accept items"):
        add_resource_item({"not": "a list"}, {"id": "two", "title": "Added"})

    with pytest.raises(ItemNotFoundError, match="Item not found"):
        update_resource_item([], "missing", {"done": True})
