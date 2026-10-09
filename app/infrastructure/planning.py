"""PostgreSQL planning repositories, safe reconstruction and Unit of Work."""

from dataclasses import asdict
from typing import Never
from sqlalchemy.orm import Session
from app.infrastructure.database import SessionFactory
from datetime import date, datetime
from hashlib import sha256
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import select, update, or_, and_
from sqlalchemy.exc import SQLAlchemyError

from app.application.planning import CreationReceipt, PlanService
from app.domain.planning import PlanIntent, PlanItem, PlanUnavailableError, utc
from app.infrastructure.database_diagnostics import (
    log_database_failure,
    raise_safe_database_error,
)
from app.infrastructure.onboarding import SqlAlchemyOnboardingUnitOfWork
from app.infrastructure.planning_models import PlanItemModel, PlanCreationReceiptModel
from app.infrastructure.authentication.models import UserAccountModel

LOGGER = logging.getLogger("pos.planning.persistence")


def _values(item: PlanItem) -> dict:
    return {
        "id": item.id,
        "user_account_id": item.user_account_id,
        **asdict(item.intent),
        "completed_at": item.completed_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "version": item.version,
    }


def _record(values: dict) -> PlanItem:
    try:
        intent = PlanIntent(
            **{name: values[name] for name in PlanIntent.__dataclass_fields__}
        )
        normalized = intent.normalized(check_offset=False)
        if (
            intent != normalized
            or type(values["version"]) is not int
            or values["version"] < 1
            or not isinstance(values["id"], UUID)
            or not isinstance(values["user_account_id"], UUID)
        ):
            raise ValueError("Invalid persisted state")
        created, updated = utc(values["created_at"]), utc(values["updated_at"])
        completion = (
            None if values["completed_at"] is None else utc(values["completed_at"])
        )
        if updated < created or (
            completion is not None and (completion < created or intent.is_priority)
        ):
            raise ValueError("Invalid persisted state")
        return PlanItem(
            values["id"],
            values["user_account_id"],
            normalized,
            completion,
            created,
            updated,
            values["version"],
        )
    except (TypeError, ValueError, KeyError, RuntimeError) as error:
        raise_safe_database_error(
            PlanUnavailableError, "Persisted planning state is invalid", error
        )


def _snapshot(item: PlanItem) -> dict:
    return json.loads(json.dumps(_values(item), default=str))


def _from_snapshot(value: dict) -> PlanItem:
    try:
        values = dict(value)
        for name in ("id", "user_account_id"):
            values[name] = UUID(values[name])
        values["scheduled_for_date"] = date.fromisoformat(values["scheduled_for_date"])
        for name in (
            "scheduled_start_at",
            "scheduled_end_at",
            "completed_at",
            "created_at",
            "updated_at",
        ):
            values[name] = (
                None if values[name] is None else datetime.fromisoformat(values[name])
            )
        return _record(values)
    except (TypeError, ValueError, KeyError) as error:
        raise_safe_database_error(
            PlanUnavailableError, "Persisted planning receipt is invalid", error
        )


class SqlAlchemyPlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self, account_id: UUID, item_id: UUID, *, lock: bool = False
    ) -> PlanItem | None:
        query = select(PlanItemModel).where(
            PlanItemModel.user_account_id == account_id, PlanItemModel.id == item_id
        )
        if lock:
            query = query.with_for_update()
        model = self._session.execute(query).scalar_one_or_none()
        return (
            None
            if model is None
            else _record(
                {c.name: getattr(model, c.name) for c in model.__table__.columns}
            )
        )

    def add(self, item: PlanItem) -> None:
        self._session.add(PlanItemModel(**_values(item)))
        self._session.flush()

    def update(self, item: PlanItem, expected_version: int) -> bool:
        result = self._session.execute(
            update(PlanItemModel)
            .where(
                PlanItemModel.user_account_id == item.user_account_id,
                PlanItemModel.id == item.id,
                PlanItemModel.version == expected_version,
            )
            .values(**_values(item))
        )
        return result.rowcount == 1

    def _list(self, query) -> list[PlanItem]:
        return [
            _record({c.name: getattr(model, c.name) for c in model.__table__.columns})
            for model in self._session.scalars(query)
        ]

    def selected_date_facts(
        self, account_id: UUID, selected_date: date
    ) -> list[PlanItem]:
        # A single statement provides mutually consistent full-date facts.
        return self._list(
            select(PlanItemModel).where(
                PlanItemModel.user_account_id == account_id,
                PlanItemModel.scheduled_for_date == selected_date,
            )
        )

    def today_facts(
        self, account_id: UUID, local_date: date, start: datetime, end: datetime
    ) -> list[PlanItem]:
        # One statement yields one PostgreSQL snapshot for counts and wins.
        return self._list(
            select(PlanItemModel).where(
                PlanItemModel.user_account_id == account_id,
                or_(
                    PlanItemModel.scheduled_for_date == local_date,
                    and_(
                        PlanItemModel.completed_at >= start,
                        PlanItemModel.completed_at < end,
                    ),
                ),
            )
        )

    def receipt(
        self, account_id: UUID, operation: str, key: str
    ) -> CreationReceipt | None:
        # Serialize creation per owning account before checking the receipt. This
        # closes the absent-receipt race without catch/retry of failed transactions.
        self._session.execute(
            select(UserAccountModel.id)
            .where(UserAccountModel.id == account_id)
            .with_for_update()
        )
        model = self._session.scalar(
            select(PlanCreationReceiptModel).where(
                PlanCreationReceiptModel.user_account_id == account_id,
                PlanCreationReceiptModel.operation == operation,
                PlanCreationReceiptModel.key_digest == sha256(key.encode()).hexdigest(),
            )
        )
        if model is None:
            return None
        item = _from_snapshot(model.response_snapshot)
        if item.user_account_id != account_id or item.id != model.plan_item_id:
            raise PlanUnavailableError("Persisted planning receipt is invalid")
        return CreationReceipt(model.request_hash, item)

    def save_receipt(
        self, account_id: UUID, operation: str, key: str, receipt: CreationReceipt
    ) -> None:
        self._session.add(
            PlanCreationReceiptModel(
                id=uuid4(),
                user_account_id=account_id,
                operation=operation,
                key_digest=sha256(key.encode()).hexdigest(),
                request_hash=receipt.request_hash,
                plan_item_id=receipt.item.id,
                response_snapshot=_snapshot(receipt.item),
                created_at=receipt.item.created_at,
            )
        )
        self._session.flush()


class SqlAlchemyPlanUnitOfWork(SqlAlchemyOnboardingUnitOfWork):
    @property
    def plans(self) -> SqlAlchemyPlanRepository:
        return SqlAlchemyPlanRepository(self._require())

    def _raise(self, operation: str, error: SQLAlchemyError) -> Never:
        log_database_failure(
            LOGGER,
            operation="planning_transaction",
            category="unavailable",
            request_id=self._request_id,
        )
        raise_safe_database_error(
            PlanUnavailableError, "Planning persistence is unavailable", error
        )


class SafePlanDiagnostic:
    def emit(
        self,
        operation: str,
        request_id: str,
        account_id: UUID,
        item_id: UUID | None,
        version: int,
    ) -> None:
        if operation not in {
            "today_read",
            "plan_selected_date_read",
            "plan_insight_unavailable",
            "plan_item_create",
            "plan_item_update",
            "plan_item_complete",
        }:
            return
        try:
            LOGGER.info(
                (
                    "planning_insight_unavailable"
                    if operation == "plan_insight_unavailable"
                    else "planning_operation_succeeded"
                ),
                extra={
                    "operation": operation,
                    "outcome": (
                        "unavailable"
                        if operation == "plan_insight_unavailable"
                        else "succeeded"
                    ),
                    "request_id": request_id if len(request_id) <= 64 else "redacted",
                    "account_id": (
                        str(account_id) if isinstance(account_id, UUID) else "redacted"
                    ),
                    "plan_item_id": str(item_id) if isinstance(item_id, UUID) else None,
                    "version": max(0, version),
                },
            )
        except Exception:
            pass


def compose_planning(engine) -> PlanService:
    from app.infrastructure.database import create_session_factory

    factory: SessionFactory = create_session_factory(engine)
    return PlanService(
        lambda request_id: SqlAlchemyPlanUnitOfWork(factory, request_id=request_id),
        diagnostic=SafePlanDiagnostic(),
    )
