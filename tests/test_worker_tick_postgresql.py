"""Guarded overlap and stale email claim proofs; no Redis or provider calls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Barrier, Lock
from uuid import uuid4

import pytest

from app.application.email_delivery import (
    AuthenticationEmailDispatcher,
    DeliveryReceipt,
    OutboundEmail,
)
from app.application.jobs import JobService, JobWorker
from app.config import load_settings
from app.domain.jobs import JOB_TYPE
from app.infrastructure.authentication.models import AuthenticationEmailOutboxModel
from app.infrastructure.authentication.unit_of_work import (
    SqlAlchemyAuthenticationUnitOfWork,
)
from app.infrastructure.database import create_session_factory
from app.infrastructure.jobs import SqlAlchemyJobUnitOfWork
from app.infrastructure.worker_tick import run_tick
from tests import test_onboarding_postgresql as shared
from tests.test_jobs import PAYLOAD


engine = shared.engine
empty_tables = shared.empty_tables
pytestmark = pytest.mark.skipif(
    shared.TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)
NOW = shared.NOW


def seed_email(engine, count):
    identifiers = [uuid4() for _ in range(count)]
    with engine.begin() as connection:
        connection.execute(
            AuthenticationEmailOutboxModel.__table__.insert(),
            [
                dict(
                    id=identifier,
                    message_kind="email_verification",
                    recipient_normalized_email="synthetic@example.invalid",
                    payload_ciphertext=b"fixture",
                    encryption_key_id="fixture",
                    encryption_key_version=1,
                    deduplication_key=str(identifier),
                    status="pending",
                    created_at=NOW,
                    available_at=NOW,
                    attempt_count=0,
                )
                for identifier in identifiers
            ],
        )
    return identifiers


def test_two_overlapping_ticks_claim_disjoint_email_and_jobs_and_finish(engine):
    identifiers = seed_email(engine, 2)
    sessions = create_session_factory(engine)

    def job_factory():
        return SqlAlchemyJobUnitOfWork(sessions)

    service = JobService(job_factory, clock=lambda: NOW)
    # Use the established cleanup payload with an execution instant after its cutoff.
    clock = max(NOW, datetime.fromisoformat(PAYLOAD["cutoff_at"]))
    jobs = [
        service.schedule(JOB_TYPE, None, PAYLOAD, clock, str(uuid4())) for _ in range(2)
    ]
    email_barrier, job_barrier = Barrier(2), Barrier(2)
    emails_seen, jobs_seen = [], []
    lock = Lock()

    class Renderer:
        def render(self, record):
            return OutboundEmail(
                "synthetic@example.invalid",
                "synthetic@example.invalid",
                "fixture",
                "fixture",
                "fixture",
                str(record.id),
            )

    class Provider:
        def send(self, message):
            with lock:
                emails_seen.append(message.idempotency_key)
            email_barrier.wait(timeout=10)
            return DeliveryReceipt("fixture")

    @contextmanager
    def emails(settings):
        yield AuthenticationEmailDispatcher(
            lambda request: SqlAlchemyAuthenticationUnitOfWork(
                sessions, request_id=request
            ),
            Renderer(),
            Provider(),
            clock=lambda: clock,
        )

    def handler(job):
        with lock:
            jobs_seen.append(job.id)
        job_barrier.wait(timeout=10)

    @contextmanager
    def general_jobs(settings):
        yield JobWorker(job_factory, {JOB_TYPE: handler}, clock=lambda: clock)

    def execute(_):
        return run_tick(
            load_settings({"APP_ENV": "test"}),
            email_limit=1,
            job_limit=1,
            email_factory=emails,
            job_factory=general_jobs,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert all(result.exit_code == 0 for pair in results for result in pair)
    assert set(emails_seen) == {str(identifier) for identifier in identifiers}
    assert len(emails_seen) == len(set(emails_seen)) == 2
    assert set(jobs_seen) == {job.id for job in jobs}
    assert len(jobs_seen) == len(set(jobs_seen)) == 2


def test_reclaimed_email_lease_rejects_all_stale_acknowledgements(engine):
    identifier = seed_email(engine, 1)[0]
    sessions = create_session_factory(engine)
    first, second = uuid4(), uuid4()
    with SqlAlchemyAuthenticationUnitOfWork(sessions) as unit:
        assert len(unit.email_outbox.claim_available(NOW, first, 1)) == 1
        unit.commit()
    later = NOW + timedelta(minutes=5)
    with SqlAlchemyAuthenticationUnitOfWork(sessions) as unit:
        assert len(unit.email_outbox.claim_available(later, second, 1)) == 1
        unit.commit()
    with SqlAlchemyAuthenticationUnitOfWork(sessions) as unit:
        assert not unit.email_outbox.mark_delivered(identifier, first, later, "fixture")
        assert not unit.email_outbox.mark_failed(
            identifier, first, later, "message_expired"
        )
        assert not unit.email_outbox.release_for_retry(
            identifier, first, later, "provider_unavailable"
        )
        assert unit.email_outbox.mark_delivered(identifier, second, later, "fixture")
        unit.commit()
