"""Explicit bounded retention scheduling; never run by application startup."""

from datetime import datetime
import sys
from app.application.jobs import JobService
from app.config import load_settings
from app.domain.jobs import bounded, utc
from app.domain.notifications import CLEANUP_TYPE
from app.infrastructure.database import (
    create_database_engine,
    create_session_factory,
    dispose_database_engine,
)
from app.infrastructure.jobs import SqlAlchemyJobUnitOfWork


def main() -> int:
    engine = None
    try:
        args = sys.argv[1:]
        if len(args) != 4 or args[0] != "schedule-cleanup":
            raise ValueError()
        limit, instant, key = (
            bounded(int(args[1])),
            utc(datetime.fromisoformat(args[2])),
            args[3],
        )
    except Exception:
        print(
            "Usage: notification_operations schedule-cleanup LIMIT AWARE_TIMESTAMP OPERATION_KEY"
        )
        return 1
    try:
        settings = load_settings()
        if settings.database_url is None:
            print("Notification maintenance configuration is unavailable")
            return 2
        engine = create_database_engine(settings.database_url)
        sessions = create_session_factory(engine)
        service = JobService(lambda: SqlAlchemyJobUnitOfWork(sessions))
        service.schedule(CLEANUP_TYPE, None, {"limit": limit}, instant, key)
        print("scheduled=1")
        return 0
    except Exception:
        print("Notification maintenance scheduling failed safely")
        return 4
    finally:
        if engine is not None:
            try:
                dispose_database_engine(engine)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
