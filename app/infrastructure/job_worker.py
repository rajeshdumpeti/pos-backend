"""Deliberate, single-run CLI. No web integration or perpetual worker loop."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import sys
from app.application.jobs import JobService, JobWorker
from app.config import ConfigurationError, Settings, load_settings
from app.domain.jobs import JOB_TYPE, bounded
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    dispose_database_engine,
)
from app.infrastructure.jobs import SafeJobDiagnostic, SqlAlchemyJobUnitOfWork


@contextmanager
def job_processor(settings: Settings) -> Iterator[JobWorker]:
    """Reuse the established handlers and UoWs; own only their engine lifetime."""
    if settings.database_url is None:
        raise ConfigurationError("Background job database configuration is unavailable")
    engine = create_database_engine(settings.database_url, settings=settings)
    try:
        sessions = create_session_factory(engine)

        def factory():
            return SqlAlchemyJobUnitOfWork(sessions)

        service = JobService(factory)
        from app.application.notifications import NotificationService
        from app.domain.notifications import PLAN_EVENT, TODAY_EVENT, CLEANUP_TYPE
        from app.infrastructure.notifications import SqlAlchemyInboxUnitOfWork

        notifications = NotificationService(lambda: SqlAlchemyInboxUnitOfWork(sessions))
        yield JobWorker(
            factory,
            {
                JOB_TYPE: service.cleanup,
                PLAN_EVENT: notifications.plan_reminder,
                TODAY_EVENT: notifications.today_reminder,
                CLEANUP_TYPE: notifications.cleanup,
            },
            diagnostic=SafeJobDiagnostic(),
        )
    finally:
        dispose_database_engine(engine)


def process_pending(limit: int = 10) -> int:
    """Keep the standalone CLI's existing output and exit-code contract."""
    try:
        bounded(limit)
        settings = load_settings()
        if settings.database_url is None:
            print("Background job database configuration is unavailable")
            return 2
        with job_processor(settings) as worker:
            summary = worker.process_pending(limit)
        print(" ".join(f"{key}={value}" for key, value in asdict(summary).items()))
        return 3 if summary.dead or summary.lease_lost else 0
    except Exception:
        print("Background job batch failed safely")
        return 4


def main() -> int:
    args = sys.argv[1:]
    try:
        if not args or args[0] != "process-pending" or len(args) > 2:
            raise ValueError()
        limit = int(args[1]) if len(args) == 2 else 10
        bounded(limit)
    except ValueError:
        print(
            "Usage: python -m app.infrastructure.job_worker process-pending [LIMIT:1..100]"
        )
        return 1
    return process_pending(limit)


if __name__ == "__main__":
    raise SystemExit(main())
