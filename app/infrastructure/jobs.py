"""PostgreSQL jobs: repositories never construct sessions or commit transactions."""

from dataclasses import asdict
from datetime import datetime, timedelta
import logging
from uuid import UUID
from types import TracebackType
from typing import Self, Never
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.application.jobs import Claim
from app.application.persistence import PersistenceUnavailableError
from app.domain.jobs import (
    CATEGORIES,
    JOB_TYPES,
    STATES,
    TERMINAL,
    Job,
    JobConflictError,
    bounded,
    utc,
)
from app.infrastructure.database import SessionFactory
from app.infrastructure.database_diagnostics import raise_safe_database_error
from app.infrastructure.job_models import BackgroundJobModel as Model


def record(model: Model) -> Job:
    return Job(
        **{
            column.name: getattr(model, column.name)
            for column in Model.__table__.columns
        }
    )


def due_query(now: datetime, limit: int):
    now = utc(now)
    bounded(limit)
    return (
        select(Model)
        .where(
            or_(
                and_(
                    Model.state.in_(("pending", "retryable")), Model.scheduled_at <= now
                ),
                and_(Model.state == "running", Model.lease_expires_at <= now),
            )
        )
        .order_by(Model.scheduled_at, Model.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def fence(job: Job, now: datetime):
    return and_(
        Model.id == job.id,
        Model.state == "running",
        Model.lease_owner == job.lease_owner,
        Model.attempts == job.attempts,
        Model.lease_expires_at > now,
    )


class SqlAlchemyJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def schedule(self, job: Job) -> Job:
        # A no-op upsert locks/returns the existing row atomically, including races
        # with terminal cleanup; a changed intent is rejected before commit.
        existing = record(
            self._session.scalar(
                insert(Model)
                .values(**asdict(job))
                .on_conflict_do_update(
                    index_elements=[Model.deduplication_digest],
                    set_={"deduplication_digest": job.deduplication_digest},
                )
                .returning(Model)
                .execution_options(populate_existing=True)
            )
        )
        if (
            existing.intent_digest != job.intent_digest
            or existing.user_account_id != job.user_account_id
            or existing.job_type != job.job_type
        ):
            raise JobConflictError()
        return existing

    def get(self, job_id: UUID, account_id: UUID | None) -> Job | None:
        model = self._session.scalar(
            select(Model).where(Model.id == job_id, Model.user_account_id == account_id)
        )
        return record(model) if model is not None else None

    def claim(self, now: datetime, owner: UUID, lease: timedelta, limit: int) -> Claim:
        jobs = []
        exhausted = 0
        for model in self._session.scalars(due_query(now, limit)):
            if model.attempts >= model.max_attempts:
                model.state, model.completed_at, model.updated_at = "dead", now, now
                model.failure_category = "lease_expired"
                model.lease_owner = model.lease_expires_at = None
                exhausted += 1
                continue
            model.state, model.updated_at = "running", now
            model.attempts += 1
            model.started_at = model.started_at or now
            model.lease_owner, model.lease_expires_at = owner, now + lease
            jobs.append(record(model))
        self._session.flush()
        return Claim(tuple(jobs), exhausted)

    def renew(self, job: Job, now: datetime, lease: timedelta) -> bool:
        result = self._session.execute(
            update(Model)
            .where(fence(job, now))
            .values(lease_expires_at=now + lease, updated_at=now)
        )
        return result.rowcount == 1

    def finish(
        self, job: Job, now: datetime, category: str | None, retry_at: datetime | None
    ) -> Job | None:
        state = (
            "succeeded"
            if category is None
            else "retryable" if retry_at is not None else "dead"
        )
        values = dict(
            state=state,
            failure_category=category,
            lease_owner=None,
            lease_expires_at=None,
            completed_at=None if state == "retryable" else now,
            updated_at=now,
        )
        if retry_at is not None:
            values["scheduled_at"] = retry_at
        model = self._session.scalar(
            update(Model).where(fence(job, now)).values(**values).returning(Model)
        )
        return record(model) if model is not None else None

    def cancel(
        self,
        job_id: UUID,
        account_id: UUID | None,
        now: datetime,
        superseded: bool = False,
    ) -> bool:
        result = self._session.execute(
            update(Model)
            .where(
                Model.id == job_id,
                Model.user_account_id == account_id,
                Model.state.in_(("pending", "retryable")),
            )
            .values(
                state="superseded" if superseded else "cancelled",
                completed_at=now,
                updated_at=now,
            )
        )
        return result.rowcount == 1

    def cleanup(self, account_id: UUID | None, before: datetime, limit: int) -> int:
        bounded(limit)
        before = utc(before)
        ids = (
            select(Model.id)
            .where(
                Model.user_account_id == account_id,
                Model.state.in_(TERMINAL),
                Model.completed_at < before,
            )
            .order_by(Model.completed_at, Model.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = self._session.execute(delete(Model).where(Model.id.in_(ids)))
        return result.rowcount


class SqlAlchemyJobUnitOfWork:
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._session: Session | None = None
        self._committed = False

    @property
    def jobs(self) -> SqlAlchemyJobRepository:
        return SqlAlchemyJobRepository(self._require())

    def _require(self) -> Session:
        if self._session is None:
            raise RuntimeError("Background job transaction is not active")
        return self._session

    def __enter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Background job transaction is already active")
        self._committed = False
        try:
            self._session = self._factory()
        except SQLAlchemyError as error:
            self._raise(error)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        error = exception if isinstance(exception, SQLAlchemyError) else None
        try:
            if exception is not None or not self._committed:
                self._require().rollback()
        except SQLAlchemyError as failure:
            error = failure
        finally:
            try:
                self._require().close()
            except SQLAlchemyError as failure:
                error = failure
            self._session = None
        if error is not None:
            self._raise(error)

    def _raise(self, error: SQLAlchemyError) -> Never:
        raise_safe_database_error(
            PersistenceUnavailableError,
            "Background job persistence is unavailable",
            error,
        )

    def commit(self) -> None:
        try:
            self._require().commit()
            self._committed = True
        except SQLAlchemyError as error:
            self._raise(error)

    def rollback(self) -> None:
        try:
            self._require().rollback()
        except SQLAlchemyError as error:
            self._raise(error)


class SafeJobDiagnostic:
    def emit(self, job: Job, worker: UUID | None = None) -> None:
        if (
            not isinstance(job.id, UUID)
            or type(job.attempts) is not int
            or not 0 <= job.attempts <= 5
            or job.job_type not in JOB_TYPES
            or job.state not in STATES
            or job.failure_category not in CATEGORIES | {None}
        ):
            return
        logging.getLogger("pos.jobs").info(
            "background_job_transition",
            extra={
                "job_id": str(job.id),
                "job_type": job.job_type,
                "state": job.state,
                "attempt": job.attempts,
                "failure_category": job.failure_category,
                "lease_owner": str(worker) if isinstance(worker, UUID) else None,
                "scheduled_at": job.scheduled_at.isoformat(),
                "completed_at": (
                    job.completed_at.isoformat() if job.completed_at else None
                ),
            },
        )
