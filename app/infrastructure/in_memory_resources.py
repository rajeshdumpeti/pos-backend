"""In-memory repository and unit of work used by the local mock API."""

from copy import deepcopy
from types import TracebackType
from typing import Any

from app.application import ResourceRepository
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

    def _snapshot(self) -> dict[str, Any]:
        return deepcopy(self._resources)

    def _restore(self, snapshot: dict[str, Any]) -> None:
        self._resources = deepcopy(snapshot)


class InMemoryResourceUnitOfWork:
    """Protect one in-memory operation with commit-or-restore semantics."""

    def __init__(self, repository: InMemoryResourceStore) -> None:
        self._repository = repository
        self._initial_state: dict[str, Any] | None = None
        self._committed = False

    @property
    def resources(self) -> ResourceRepository:
        return self._repository

    def __enter__(self) -> "InMemoryResourceUnitOfWork":
        if self._initial_state is not None:
            raise RuntimeError("Unit of work is already active")
        self._initial_state = self._repository._snapshot()
        self._committed = False
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        try:
            if exception_type is not None or not self._committed:
                self.rollback()
        finally:
            self._initial_state = None
            self._committed = False

    def commit(self) -> None:
        self._require_active()
        self._committed = True

    def rollback(self) -> None:
        self._require_active()
        assert self._initial_state is not None
        self._repository._restore(self._initial_state)
        self._committed = False

    def _require_active(self) -> None:
        if self._initial_state is None:
            raise RuntimeError("Unit of work is not active")


class InMemoryResourceUnitOfWorkFactory:
    """Create isolated units of work over one application-scoped mock store."""

    def __init__(self, repository: InMemoryResourceStore) -> None:
        self._repository = repository

    def __call__(self) -> InMemoryResourceUnitOfWork:
        return InMemoryResourceUnitOfWork(self._repository)
