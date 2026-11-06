"""Job intent, orchestration, retry, cancellation and transaction contract tests."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4
import pytest
from app.application.jobs import JobService, JobWorker
from app.domain.jobs import (
    JOB_TYPE,
    JobConflictError,
    JobValidationError,
    PermanentJobFailure,
    TransientJobFailure,
    backoff,
    new_job,
)
from tests.jobs_support import Store

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
PAYLOAD = {"cutoff_at": (NOW - timedelta(days=7)).isoformat(), "limit": 10}


def setup():
    store = Store()
    clock = [NOW]
    service = JobService(store.unit, clock=lambda: clock[0])
    return store, clock, service


def schedule(service, key="synthetic-key", account=None, at=NOW, attempts=5):
    return service.schedule(JOB_TYPE, account, PAYLOAD, at, key, attempts)


@pytest.mark.parametrize(
    "mutate",
    [
        {"job_type": "unknown"},
        {"payload": {"message": "private"}},
        {"payload": {"cutoff_at": "private", "limit": 1}},
        {"scheduled_at": NOW.replace(tzinfo=None)},
        {"max_attempts": 6},
        {"max_attempts": True},
        {"key": "private value\n"},
        {"payload": {**PAYLOAD, "limit": 101}},
        {"payload": {**PAYLOAD, "limit": True}},
        {"payload": {**PAYLOAD, "cutoff_at": (NOW + timedelta(days=1)).isoformat()}},
    ],
)
def test_invalid_intent_fails_without_private_values(mutate):
    values = dict(
        job_type=JOB_TYPE,
        account_id=None,
        payload=PAYLOAD,
        scheduled_at=NOW,
        key="test",
        now=NOW,
        max_attempts=5,
    )
    values.update(mutate)
    with pytest.raises(JobValidationError) as error:
        new_job(**values)
    assert "private" not in str(error.value)


def test_exact_instants_across_both_dst_folds_and_no_timezone_rules():
    for offset in (-4, -5):
        instant = datetime(2026, 11, 1, 1, 30, tzinfo=timezone(timedelta(hours=offset)))
        job = new_job(JOB_TYPE, None, PAYLOAD, instant, "test", NOW)
        assert job.scheduled_at == instant.astimezone(UTC)
    assert backoff(1).total_seconds() == 30
    assert [backoff(i).total_seconds() for i in range(1, 6)] == [30, 60, 120, 240, 300]


def test_deduplication_binds_original_intent_and_account():
    store, clock, service = setup()
    owner, other = uuid4(), uuid4()
    first = schedule(service, account=owner)
    assert schedule(service, account=owner).id == first.id
    assert schedule(service, account=other).id != first.id
    with pytest.raises(JobConflictError):
        schedule(service, account=owner, at=NOW + timedelta(seconds=1))
    assert len(store.rows) == 2 and store.rollbacks == 1
    assert "cutoff_at" not in repr(first) and str(owner) not in repr(first)


def test_future_jobs_bounded_batch_success_and_no_reexecution():
    store, clock, service = setup()
    first = schedule(service)
    schedule(service, key="second")
    schedule(service, key="future", at=NOW + timedelta(days=1))
    seen = []
    worker = JobWorker(
        store.unit, {JOB_TYPE: lambda job: seen.append(job.id)}, clock=lambda: clock[0]
    )
    assert worker.process_pending(1).succeeded == 1
    assert worker.process_pending(1).succeeded == 1
    assert worker.process_pending().claimed == 0 and len(set(seen)) == 2
    assert store.rows[first.id].attempts == 1


def test_retryable_failures_back_off_then_exhaust_exactly_five_attempts():
    store, clock, service = setup()
    job = schedule(service)

    def handler(job):
        raise TransientJobFailure()

    worker = JobWorker(store.unit, {JOB_TYPE: handler}, clock=lambda: clock[0])
    for attempt in range(1, 6):
        summary = worker.process_pending()
        row = store.rows[job.id]
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


@pytest.mark.parametrize(
    "failure,category",
    [
        (PermanentJobFailure(), "permanent"),
        (ValueError("private"), "handler_failure"),
        (JobValidationError(), "invalid_payload"),
    ],
)
def test_only_recognized_transient_failures_retry(failure, category):
    store, clock, service = setup()
    job = schedule(service)

    def handler(job):
        raise failure

    result = JobWorker(
        store.unit, {JOB_TYPE: handler}, clock=lambda: clock[0]
    ).process_pending()
    assert result.dead == 1 and store.rows[job.id].failure_category == category
    assert "private" not in repr(store.rows[job.id])


def test_missing_handler_is_terminal_without_any_execution():
    store, clock, service = setup()
    job = schedule(service)
    assert JobWorker(store.unit, {}, clock=lambda: clock[0]).process_pending().dead == 1
    assert store.rows[job.id].failure_category == "unsupported_type"


def test_expired_leases_recover_and_stale_attempt_cannot_acknowledge():
    store, clock, service = setup()
    schedule(service)
    with store.unit() as unit:
        first = unit.jobs.claim(NOW, uuid4(), timedelta(seconds=30), 1).jobs[0]
        assert unit.jobs.claim(NOW, uuid4(), timedelta(seconds=30), 1).jobs == ()
        second = unit.jobs.claim(
            NOW + timedelta(seconds=30), uuid4(), timedelta(seconds=30), 1
        ).jobs[0]
        assert second.attempts == 2
        assert unit.jobs.finish(first, NOW + timedelta(seconds=31), None, None) is None
        assert (
            unit.jobs.finish(second, NOW + timedelta(seconds=31), None, None).state
            == "succeeded"
        )
        unit.commit()


def test_crashes_consume_attempts_and_final_expiry_is_terminal():
    store, clock, service = setup()
    job = schedule(service, attempts=1)
    with store.unit() as unit:
        unit.jobs.claim(NOW, uuid4(), timedelta(seconds=1), 1)
        result = unit.jobs.claim(
            NOW + timedelta(seconds=1), uuid4(), timedelta(seconds=1), 1
        )
        unit.commit()
    assert result.exhausted == 1 and store.rows[job.id].state == "dead"


def test_account_scoped_cancellation_and_atomic_supersession():
    store, clock, service = setup()
    owner = uuid4()
    job = schedule(service, account=owner)
    assert not service.cancel(job.id, uuid4())
    replacement = new_job(
        JOB_TYPE, owner, PAYLOAD, NOW + timedelta(hours=1), "replacement", NOW
    )
    service.supersede(job.id, replacement)
    assert (
        store.rows[job.id].state == "superseded"
        and store.rows[replacement.id].state == "pending"
    )
    assert service.cancel(replacement.id, owner)
    assert (
        JobWorker(store.unit, {}, clock=lambda: clock[0]).process_pending().claimed == 0
    )


def test_supersession_conflict_rolls_back_original_cancellation():
    store, clock, service = setup()
    job = schedule(service)
    same_key = new_job(JOB_TYPE, None, PAYLOAD, NOW, "synthetic-key", NOW)
    with pytest.raises(JobConflictError):
        service.supersede(job.id, same_key)
    assert store.rows[job.id].state == "pending"


def test_running_jobs_cannot_be_cancelled_and_slow_handlers_lose_lease():
    store, clock, service = setup()
    job = schedule(service)

    def handler(value):
        assert not service.cancel(value.id, None)
        clock[0] += timedelta(seconds=2)

    result = JobWorker(
        store.unit, {JOB_TYPE: handler}, clock=lambda: clock[0], lease_seconds=1
    ).process_pending()
    assert result.lease_lost == 1 and store.rows[job.id].state == "running"


def test_cleanup_is_bounded_account_safe_idempotent_and_never_removes_pending():
    store, clock, service = setup()
    owner = uuid4()
    first = schedule(service, account=owner)
    foreign = schedule(service, key="foreign", account=uuid4())
    pending = schedule(service, key="pending", account=owner)
    service.cancel(first.id, owner)
    service.cancel(foreign.id, foreign.user_account_id)
    clock[0] += timedelta(seconds=1)
    cleanup = new_job(
        JOB_TYPE,
        owner,
        {"cutoff_at": clock[0].isoformat(), "limit": 1},
        clock[0],
        "cleanup",
        clock[0],
    )
    service.cleanup(cleanup)
    service.cleanup(cleanup)
    assert (
        first.id not in store.rows
        and foreign.id in store.rows
        and pending.id in store.rows
    )


def test_dependency_failure_retries_and_diagnostic_failure_does_not_change_confirmation():
    from app.application.persistence import PersistenceUnavailableError

    store, clock, service = setup()
    job = schedule(service)

    def handler(value):
        raise PersistenceUnavailableError("synthetic-private-driver-text")

    class BrokenDiagnostic:
        def emit(self, value, worker=None):
            raise RuntimeError("synthetic-private-diagnostic")

    worker = JobWorker(
        store.unit,
        {JOB_TYPE: handler},
        clock=lambda: clock[0],
        diagnostic=BrokenDiagnostic(),
    )
    assert worker.process_pending().retryable == 1
    assert store.rows[job.id].failure_category == "dependency_unavailable"
