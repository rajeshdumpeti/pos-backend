"""Internal job scheduling and bounded worker orchestration through UoW ports."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID, uuid4

from app.application.persistence import PersistenceUnavailableError
from app.domain.jobs import (
    Job,
    JobConflictError,
    JobValidationError,
    PermanentJobFailure,
    TransientJobFailure,
    backoff,
    bounded,
    new_job,
    utc,
)


@dataclass(frozen=True)
class Claim:
    jobs: tuple[Job, ...]
    exhausted: int = 0


class JobRepository(Protocol):
    def schedule(self, job: Job) -> Job: ...
    def get(self, job_id: UUID, account_id: UUID | None) -> Job | None: ...
    def claim(
        self, now: datetime, owner: UUID, lease: timedelta, limit: int
    ) -> Claim: ...
    def renew(self, job: Job, now: datetime, lease: timedelta) -> bool: ...
    def finish(
        self, job: Job, now: datetime, category: str | None, retry_at: datetime | None
    ) -> Job | None: ...
    def cancel(
        self,
        job_id: UUID,
        account_id: UUID | None,
        now: datetime,
        superseded: bool = False,
    ) -> bool: ...
    def cleanup(self, account_id: UUID | None, before: datetime, limit: int) -> int: ...


class JobUnitOfWork(Protocol):
    @property
    def jobs(self) -> JobRepository: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class JobDiagnostic(Protocol):
    def emit(self, job: Job, worker: UUID | None = None) -> None: ...


class JobService:
    def __init__(
        self,
        factory: Callable[[], JobUnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None
    ) -> None:
        self.factory = factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def schedule(
        self,
        job_type: str,
        account_id: UUID | None,
        payload: dict,
        scheduled_at: datetime,
        key: str,
        max_attempts: int = 5,
    ) -> Job:
        job = new_job(
            job_type, account_id, payload, scheduled_at, key, self.clock(), max_attempts
        )
        with self.factory() as unit:
            result = unit.jobs.schedule(job)
            unit.commit()
            return result

    def cancel(self, job_id: UUID, account_id: UUID | None) -> bool:
        with self.factory() as unit:
            result = unit.jobs.cancel(job_id, account_id, utc(self.clock()))
            unit.commit()
            return result

    def supersede(self, old_id: UUID, replacement: Job) -> Job:
        # Replacement is a validated internal intent; both writes share one transaction.
        checked = new_job(
            replacement.job_type,
            replacement.user_account_id,
            replacement.payload,
            replacement.scheduled_at,
            "validation",
            self.clock(),
            replacement.max_attempts,
        )
        if checked.intent_digest != replacement.intent_digest:
            raise JobValidationError()
        with self.factory() as unit:
            if not unit.jobs.cancel(
                old_id, replacement.user_account_id, utc(self.clock()), True
            ):
                raise JobConflictError()
            result = unit.jobs.schedule(replacement)
            if result.id == old_id or result.state != "pending":
                raise JobConflictError()
            unit.commit()
            return result

    def cleanup(self, job: Job) -> None:
        from app.domain.jobs import payload_for

        payload = payload_for(job.job_type, job.payload)
        cutoff = datetime.fromisoformat(payload["cutoff_at"])
        if cutoff > utc(self.clock()):
            raise JobValidationError()
        with self.factory() as unit:
            unit.jobs.cleanup(job.user_account_id, cutoff, payload["limit"])
            unit.commit()


@dataclass
class WorkerSummary:
    claimed: int = 0
    succeeded: int = 0
    retryable: int = 0
    dead: int = 0
    lease_lost: int = 0


class JobWorker:
    def __init__(
        self,
        factory: Callable[[], JobUnitOfWork],
        handlers: Mapping[str, Callable[[Job], None]],
        *,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 300,
        diagnostic: JobDiagnostic | None = None
    ) -> None:
        bounded(lease_seconds, 3600)
        self.factory, self.handlers = factory, dict(handlers)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lease = timedelta(seconds=lease_seconds)
        self.diagnostic = diagnostic

    def process_pending(self, limit: int = 10) -> WorkerSummary:
        bounded(limit)
        owner = uuid4()
        with self.factory() as unit:
            claim = unit.jobs.claim(utc(self.clock()), owner, self.lease, limit)
            unit.commit()
        summary = WorkerSummary(claimed=len(claim.jobs), dead=claim.exhausted)
        for job in claim.jobs:
            with self.factory() as unit:
                live = unit.jobs.renew(job, utc(self.clock()), self.lease)
                unit.commit()
            if not live:
                summary.lease_lost += 1
                continue
            category = None
            try:
                handler = self.handlers.get(job.job_type)
                if handler is None:
                    category = "unsupported_type"
                else:
                    handler(job)
            except TransientJobFailure:
                category = "transient"
            except PersistenceUnavailableError:
                category = "dependency_unavailable"
            except JobValidationError:
                category = "invalid_payload"
            except PermanentJobFailure:
                category = "permanent"
            except Exception:
                category = "handler_failure"
            now = utc(self.clock())
            retry_at = (
                now + backoff(job.attempts)
                if category in {"transient", "dependency_unavailable"}
                and job.attempts < job.max_attempts
                else None
            )
            with self.factory() as unit:
                finished = unit.jobs.finish(job, now, category, retry_at)
                unit.commit()
            if finished is None:
                summary.lease_lost += 1
                continue
            if finished.state == "succeeded":
                summary.succeeded += 1
            elif finished.state == "retryable":
                summary.retryable += 1
            else:
                summary.dead += 1
            if self.diagnostic:
                try:
                    self.diagnostic.emit(finished, owner)
                except Exception:
                    pass
        return summary
