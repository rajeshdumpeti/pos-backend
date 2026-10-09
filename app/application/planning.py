"""Account-scoped planning use cases and transaction ports."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import re
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID, uuid4

from app.domain.planning import (
    PlanConflictError,
    PlanIdempotencyConflictError,
    PlanIntent,
    PlanItem,
    PlanNotFoundError,
    PlanValidationError,
    utc,
    timezone_info,
)


@dataclass(frozen=True, slots=True)
class CreationReceipt:
    request_hash: str
    item: PlanItem


class PlanRepository(Protocol):
    def get(
        self, account_id: UUID, item_id: UUID, *, lock: bool = False
    ) -> PlanItem | None: ...
    def add(self, item: PlanItem) -> None: ...
    def update(self, item: PlanItem, expected_version: int) -> bool: ...
    def selected_date_facts(
        self, account_id: UUID, selected_date: date
    ) -> list[PlanItem]: ...
    def today_facts(
        self, account_id: UUID, local_date: date, start: datetime, end: datetime
    ) -> list[PlanItem]: ...
    def receipt(
        self, account_id: UUID, operation: str, key: str
    ) -> CreationReceipt | None: ...
    def save_receipt(
        self, account_id: UUID, operation: str, key: str, receipt: CreationReceipt
    ) -> None: ...


class PlanUnitOfWork(Protocol):
    @property
    def plans(self) -> PlanRepository: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class PlanDiagnostic(Protocol):
    def emit(
        self,
        operation: str,
        request_id: str,
        account_id: UUID,
        item_id: UUID | None,
        version: int,
    ) -> None: ...


class PlanService:
    def __init__(
        self,
        factory: Callable[[str], PlanUnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None,
        diagnostic: PlanDiagnostic | None = None
    ) -> None:
        self.factory = factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.diagnostic = diagnostic

    def _emit(self, operation: str, request_id: str, item: PlanItem) -> None:
        if self.diagnostic:
            self.diagnostic.emit(
                operation, request_id, item.user_account_id, item.id, item.version
            )

    def create(
        self, account_id: UUID, intent: PlanIntent, key: str, request_id: str
    ) -> PlanItem:
        if not isinstance(key, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", key
        ):
            raise PlanValidationError("Invalid idempotency key")
        intent = intent.normalized()
        canonical = {
            name: getattr(intent, name) for name in intent.__dataclass_fields__
        }
        digest = sha256(
            json.dumps(
                canonical, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()
        with self.factory(request_id) as unit:
            receipt = unit.plans.receipt(account_id, "plan_item.create", key)
            if receipt:
                if receipt.request_hash != digest:
                    raise PlanIdempotencyConflictError("Idempotency conflict")
                return receipt.item
            now = utc(self.clock())
            item = PlanItem(uuid4(), account_id, intent, None, now, now, 1)
            unit.plans.add(item)
            unit.plans.save_receipt(
                account_id, "plan_item.create", key, CreationReceipt(digest, item)
            )
            unit.commit()
            self._emit("plan_item_create", request_id, item)
            return item

    def get(self, account_id: UUID, item_id: UUID, request_id: str) -> PlanItem:
        with self.factory(request_id) as unit:
            return self._get(unit, account_id, item_id)

    @staticmethod
    def _get(
        unit: PlanUnitOfWork, account_id: UUID, item_id: UUID, *, lock: bool = False
    ) -> PlanItem:
        item = unit.plans.get(account_id, item_id, lock=lock)
        if item is None:
            raise PlanNotFoundError("Item not found")
        return item

    def update(
        self,
        account_id: UUID,
        item_id: UUID,
        expected_version: int,
        changes: dict[str, object],
        request_id: str,
    ) -> PlanItem:
        allowed = set(PlanIntent.__dataclass_fields__)
        if not changes or not set(changes) <= allowed:
            raise PlanValidationError("Invalid update")
        with self.factory(request_id) as unit:
            current = self._get(unit, account_id, item_id, lock=True)
            if current.version != expected_version or current.completed_at is not None:
                raise PlanConflictError("Plan item version conflicts")
            # Stored UTC timestamps have already passed offset validation. Validate
            # newly supplied timestamps against the resulting assignment only.
            intended = replace(current.intent, **changes)
            normalized = intended.normalized(check_offset=False)
            zone = timezone_info(intended.timezone)
            for name in ("scheduled_start_at", "scheduled_end_at"):
                supplied = changes.get(name)
                if (
                    supplied is not None
                    and supplied.utcoffset()
                    != utc(supplied).astimezone(zone).utcoffset()
                ):
                    raise PlanValidationError("Offset does not match timezone")
            item = replace(
                current,
                intent=normalized,
                updated_at=utc(self.clock()),
                version=current.version + 1,
            )
            if not unit.plans.update(item, current.version):
                raise PlanConflictError("Plan item version conflicts")
            unit.commit()
            self._emit("plan_item_update", request_id, item)
            return item

    def complete(
        self, account_id: UUID, item_id: UUID, expected_version: int, request_id: str
    ) -> PlanItem:
        with self.factory(request_id) as unit:
            current = self._get(unit, account_id, item_id, lock=True)
            if current.completed_at is not None:
                return current
            if current.version != expected_version:
                raise PlanConflictError("Plan item version conflicts")
            now = utc(self.clock())
            item = replace(
                current,
                completed_at=now,
                updated_at=now,
                version=current.version + 1,
                intent=replace(current.intent, is_priority=False),
            )
            if not unit.plans.update(item, current.version):
                raise PlanConflictError("Plan item version conflicts")
            unit.commit()
            self._emit("plan_item_complete", request_id, item)
            return item
