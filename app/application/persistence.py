"""Application-facing persistence contracts and safe persistence errors."""

from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable


class PersistenceUnavailableError(RuntimeError):
    """Raised when persistence cannot safely complete an operation."""


class PersistenceConfigurationError(RuntimeError):
    """Raised when persistence cannot be constructed from approved settings."""


@runtime_checkable
class ResourceRepository(Protocol):
    """Persistence operations required by the current mock-resource use cases."""

    def contains(self, resource: str) -> bool: ...

    def read(self, resource: str) -> Any: ...

    def write(self, resource: str, value: Any) -> None: ...

    def reset(self) -> None: ...


@runtime_checkable
class ResourceUnitOfWork(Protocol):
    """One isolated repository and transaction boundary for an operation."""

    @property
    def resources(self) -> ResourceRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


ResourceUnitOfWorkFactory = Callable[[], ResourceUnitOfWork]
