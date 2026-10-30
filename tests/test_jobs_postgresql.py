"""Live concurrency proof only on the explicitly authorized local pos_test database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.application.jobs import JobService, JobWorker
from app.domain.jobs import JOB_TYPE, JobConflictError, new_job
from app.infrastructure.database import create_session_factory
from app.infrastructure.jobs import SqlAlchemyJobUnitOfWork
from migrations.metadata import target_metadata
from tests import test_onboarding_postgresql as shared_postgresql
from tests.postgresql_support import bind_alembic_to_test_database
from tests.test_jobs import NOW, PAYLOAD

engine = shared_postgresql.engine
empty_tables = shared_postgresql.empty_tables
TEST_URL = shared_postgresql.TEST_URL
_active_account = shared_postgresql._active_account

pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)


def services(engine):
    def factory():
        return SqlAlchemyJobUnitOfWork(create_session_factory(engine))

    return factory, JobService(factory, clock=lambda: NOW)


def test_concurrent_scheduling_is_one_logical_job_and_account_isolated(engine):
    factory, service = services(engine)
    owner, other = _active_account(engine), _active_account(engine)

    def schedule(_):
        return service.schedule(JOB_TYPE, owner, PAYLOAD, NOW, "same-key").id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(schedule, range(2)))
    assert ids[0] == ids[1]
    with factory() as unit:
        assert unit.jobs.get(ids[0], other) is None
        assert not unit.jobs.cancel(ids[0], other, NOW)
    with pytest.raises(JobConflictError):
        service.schedule(
            JOB_TYPE, owner, PAYLOAD, NOW + timedelta(seconds=1), "same-key"
        )


def test_disjoint_claims_while_transactions_are_simultaneously_open(engine):
    factory, service = services(engine)
    for key in ("first", "second"):
        service.schedule(JOB_TYPE, None, PAYLOAD, NOW, key)
    barrier = Barrier(2)

    def claim(_):
        with factory() as unit:
            jobs = unit.jobs.claim(NOW, uuid4(), timedelta(seconds=30), 1).jobs
            barrier.wait(timeout=10)
            unit.commit()
            return {job.id for job in jobs}

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert len(claims[0]) == len(claims[1]) == 1 and claims[0].isdisjoint(claims[1])


def test_lease_recovery_fences_old_ack_and_success_is_not_claimed_again(engine):
    factory, service = services(engine)
    service.schedule(JOB_TYPE, None, PAYLOAD, NOW, "lease")
    with factory() as unit:
        first = unit.jobs.claim(NOW, uuid4(), timedelta(seconds=1), 1).jobs[0]
        unit.commit()
    with factory() as unit:
        second = unit.jobs.claim(
            NOW + timedelta(seconds=1), uuid4(), timedelta(seconds=30), 1
        ).jobs[0]
        unit.commit()
    with factory() as unit:
        assert unit.jobs.finish(first, NOW + timedelta(seconds=2), None, None) is None
        assert (
            unit.jobs.finish(second, NOW + timedelta(seconds=2), None, None).state
            == "succeeded"
        )
        unit.commit()
    assert (
        JobWorker(factory, {}, clock=lambda: NOW + timedelta(minutes=1))
        .process_pending()
        .claimed
        == 0
    )


def test_future_cancel_supersede_rollback_and_cleanup(engine):
    factory, service = services(engine)
    future = service.schedule(
        JOB_TYPE, None, PAYLOAD, NOW + timedelta(days=1), "future"
    )
    assert JobWorker(factory, {}, clock=lambda: NOW).process_pending().claimed == 0
    replacement = new_job(
        JOB_TYPE, None, PAYLOAD, NOW + timedelta(days=2), "replacement", NOW
    )
    service.supersede(future.id, replacement)
    assert service.cancel(replacement.id, None)
    with factory() as unit:
        assert unit.jobs.cleanup(None, NOW + timedelta(seconds=1), 1) == 1
        unit.commit()
    doomed = new_job(JOB_TYPE, None, PAYLOAD, NOW, "rollback", NOW)
    with pytest.raises(RuntimeError):
        with factory() as unit:
            unit.jobs.schedule(doomed)
            raise RuntimeError("synthetic rollback")
    with factory() as unit:
        assert unit.jobs.get(doomed.id, None) is None


def test_live_migration_metadata_parity_and_reversible_jobs_revision(engine):
    assert TEST_URL is not None
    with bind_alembic_to_test_database(TEST_URL):
        try:
            command.downgrade(Config("alembic.ini"), "0006_daily_planning")
        finally:
            command.upgrade(Config("alembic.ini"), "head")
    with engine.connect() as connection:
        assert (
            compare_metadata(
                MigrationContext.configure(
                    connection,
                    opts={"compare_type": True, "compare_server_default": True},
                ),
                target_metadata,
            )
            == []
        )


def test_persisted_retries_exhaust_and_dead_jobs_are_not_claimed(engine):
    from app.domain.jobs import TransientJobFailure, backoff

    clock = [NOW]
    factory, service = services(engine)
    job = service.schedule(JOB_TYPE, None, PAYLOAD, NOW, "retry")

    def handler(value):
        raise TransientJobFailure()

    worker = JobWorker(factory, {JOB_TYPE: handler}, clock=lambda: clock[0])
    for attempt in range(1, 6):
        summary = worker.process_pending()
        with factory() as unit:
            row = unit.jobs.get(job.id, None)
        assert row.attempts == attempt
        if attempt < 5:
            assert summary.retryable == 1 and row.scheduled_at == clock[0] + backoff(
                attempt
            )
            assert worker.process_pending().claimed == 0
            clock[0] = row.scheduled_at
        else:
            assert summary.dead == 1 and row.state == "dead"
    assert worker.process_pending().claimed == 0
