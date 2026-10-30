"""Deliberate, single-run CLI. No web integration or perpetual worker loop."""

from dataclasses import asdict
import sys
from app.application.jobs import JobService, JobWorker
from app.config import load_settings
from app.domain.jobs import JOB_TYPE, bounded
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    dispose_database_engine,
)
from app.infrastructure.jobs import SafeJobDiagnostic, SqlAlchemyJobUnitOfWork


def process_pending(limit: int = 10) -> int:
    engine = None
    try:
        bounded(limit)
        settings = load_settings()
        if settings.database_url is None:
            print("Background job database configuration is unavailable")
            return 2
        engine = create_database_engine(settings.database_url)
        sessions = create_session_factory(engine)

        def factory():
            return SqlAlchemyJobUnitOfWork(sessions)

        service = JobService(factory)
        worker = JobWorker(
            factory, {JOB_TYPE: service.cleanup}, diagnostic=SafeJobDiagnostic()
        )
        summary = worker.process_pending(limit)
        print(" ".join(f"{key}={value}" for key, value in asdict(summary).items()))
        return 3 if summary.dead or summary.lease_lost else 0
    except Exception:
        print("Background job batch failed safely")
        return 4
    finally:
        if engine is not None:
            try:
                dispose_database_engine(engine)
            except Exception:
                pass


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
