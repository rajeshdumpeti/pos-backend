"""In-memory integration used by the local mock API."""

from copy import deepcopy
from typing import Any

from app.infrastructure.demo_data import SEED


class InMemoryResourceStore:
    """Owns mutable demo state and can restore a clean seed snapshot."""

    def __init__(self) -> None:
        self._resources: dict[str, Any] = deepcopy(SEED)

    def contains(self, resource: str) -> bool:
        return resource in self._resources

    def read(self, resource: str) -> Any:
        return self._resources[resource]

    def write(self, resource: str, value: Any) -> None:
        self._resources[resource] = value

    def reset(self) -> None:
        self._resources = deepcopy(SEED)
