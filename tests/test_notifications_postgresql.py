"""Guarded PostgreSQL transaction/concurrency and migration proofs for Inbox v1."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, select
from app.application.notifications import (
    NotificationService,
    PlanNotificationIntegration,
)
from app.application.planning import PlanService
from app.domain.notifications import InboxNotFoundError
from app.domain.planning import PlanIntent
from app.infrastructure.database import create_session_factory
from app.infrastructure.notifications import SqlAlchemyInboxUnitOfWork
from app.infrastructure.planning import SqlAlchemyPlanUnitOfWork
from app.infrastructure.job_models import BackgroundJobModel
from app.infrastructure.notification_models import NotificationModel
from app.infrastructure.planning_models import PlanItemModel
from migrations.metadata import target_metadata
from tests import test_onboarding_postgresql as shared
from tests.postgresql_support import bind_alembic_to_test_database

engine = shared.engine
empty_tables = shared.empty_tables
pytestmark = pytest.mark.skipif(
    shared.TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)


def services(engine):
    clock = [shared.NOW]
    sessions = create_session_factory(engine)

    def factory():
        return SqlAlchemyInboxUnitOfWork(sessions)

    plans = PlanService(
        lambda _: SqlAlchemyPlanUnitOfWork(sessions),
        clock=lambda: clock[0],
        integration=PlanNotificationIntegration(),
    )
    return clock, plans, NotificationService(factory, clock=lambda: clock[0]), factory


def create(plans, owner, now):
    return plans.create(
        owner,
        PlanIntent("Synthetic item", now.date(), "UTC", now + timedelta(minutes=30)),
        "create",
        "test",
    )


def test_atomic_plan_jobs_concurrent_producer_dedupe_read_and_account_isolation(engine):
    owner, other = shared._active_account(engine), shared._active_account(engine)
    clock, plans, inbox, factory = services(engine)
    item = create(plans, owner, clock[0])
    with factory() as unit:
        job = unit._require().scalar(
            select(BackgroundJobModel).where(
                BackgroundJobModel.user_account_id == owner
            )
        )
        from app.infrastructure.jobs import record

        intent = record(job)
    clock[0] += timedelta(minutes=15)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: inbox.plan_reminder(intent), range(2)))
    result = inbox.list(owner, "all", "UTC")
    assert len(result["items"]) == 1 and result["unread_count"] == 1
    identifier = result["items"][0]["notification_id"]
    assert inbox.list(other, "all", "UTC")["items"] == []
    with pytest.raises(InboxNotFoundError):
        inbox.mark(other, identifier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: inbox.mark(owner, identifier), range(2)))
    assert (
        states[0]["read_at"] == states[1]["read_at"]
        and inbox.count(owner)["unread_count"] == 0
    )
    assert all(state["unread_count"] == 0 for state in states)
    retained = inbox.list(owner, "all", "UTC")["items"]
    assert len(retained) == 1 and retained[0]["is_read"] is True
    plans.complete(owner, item.id, 1, "test")
    assert (
        inbox.list(owner, "all", "UTC")["items"][0]["navigation"]["state"]
        == "available"
    )
    with engine.begin() as connection:
        connection.execute(delete(PlanItemModel).where(PlanItemModel.id == item.id))
    nav = inbox.list(owner, "all", "UTC")["items"][0]["navigation"]
    assert nav["state"] == "unavailable" and nav["plan_item_id"] is None


def test_timezone_serialization_supersession_and_today_schedule_generation(engine):
    owner = shared._active_account(engine)
    clock, plans, inbox, factory = services(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        preferences = list(
            pool.map(lambda _: inbox.put_timezone(owner, "UTC"), range(2))
        )
    assert preferences[0].version == preferences[1].version == 1
    next_pref = inbox.put_timezone(owner, "America/New_York")
    assert next_pref.version == 2
    with factory() as unit:
        states = list(
            unit._require().scalars(
                select(BackgroundJobModel.state).where(
                    BackgroundJobModel.user_account_id == owner
                )
            )
        )
    assert states.count("pending") == 1 and states.count("superseded") == 1


def test_shared_transaction_failure_rolls_back_domain_and_job(engine):
    owner = shared._active_account(engine)
    clock, plans, inbox, factory = services(engine)
    from app.infrastructure.database import create_session_factory
    from app.domain.notifications import InboxUnavailableError

    class Failure(SqlAlchemyPlanUnitOfWork):
        def commit(self):
            raise InboxUnavailableError()

    failing = PlanService(
        lambda _: Failure(create_session_factory(engine)),
        clock=lambda: clock[0],
        integration=PlanNotificationIntegration(),
    )
    with pytest.raises(InboxUnavailableError):
        create(failing, owner, clock[0])
    with factory() as unit:
        assert list(unit.plans.selected_date_facts(owner, clock[0].date())) == []
        assert not list(
            unit._require().scalars(
                select(BackgroundJobModel).where(
                    BackgroundJobModel.user_account_id == owner
                )
            )
        )


def test_live_catalog_parity_and_feature_downgrade_upgrade(engine):
    assert shared.TEST_URL is not None
    with bind_alembic_to_test_database(shared.TEST_URL):
        try:
            command.downgrade(Config("alembic.ini"), "0007_background_jobs")
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


def test_keyset_ties_full_count_concurrent_read_all_and_expired_cleanup(engine):
    from app.domain.notifications import CLEANUP_TYPE
    from app.domain.jobs import new_job

    clock, plans, inbox, factory = services(engine)
    owner = shared._active_account(engine)
    for i in range(5):
        plans.create(
            owner,
            PlanIntent(
                "Synthetic item",
                clock[0].date(),
                "UTC",
                clock[0] + timedelta(minutes=30),
            ),
            str(i),
            "test",
        )
    from app.infrastructure.jobs import record

    with factory() as unit:
        jobs = [
            record(row) for row in unit._require().scalars(select(BackgroundJobModel))
        ]
    clock[0] += timedelta(minutes=15)
    for job in jobs:
        inbox.plan_reminder(job)
    seen = []
    cursor = None
    while True:
        page = inbox.list(owner, "plan", "UTC", 2, cursor)
        assert page["unread_count"] == 5
        seen.extend(row["notification_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5 and seen == sorted(seen, reverse=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: inbox.mark_all(owner, "plan"), range(2)))
    assert sorted(row["marked_read_count"] for row in results) == [0, 5]
    assert all(row["unread_count"] == 0 for row in results)
    retained = inbox.list(owner, "plan", "UTC")["items"]
    assert len(retained) == 5 and all(row["is_read"] for row in retained)
    assert inbox.count(owner)["unread_count"] == 0
    clock[0] += timedelta(days=90)
    assert inbox.list(owner, "all", "UTC")["items"] == []
    cleanup = new_job(CLEANUP_TYPE, None, {"limit": 2}, clock[0], "retention", clock[0])
    inbox.cleanup(cleanup)
    with factory() as unit:
        remaining = list(unit._require().scalars(select(NotificationModel.id)))
    assert len(remaining) == 3


def test_today_stale_generation_noop_and_timezone_job_transaction_rollback(engine):
    from app.domain.notifications import TODAY_EVENT, InboxUnavailableError
    from app.infrastructure.jobs import record

    owner = shared._active_account(engine)
    clock, plans, inbox, factory = services(engine)
    plans.create(
        owner, PlanIntent("Synthetic item", clock[0].date(), "UTC"), "item", "test"
    )
    initial = inbox.put_timezone(owner, "UTC")
    with factory() as unit:
        old = record(
            unit._require().scalar(
                select(BackgroundJobModel).where(
                    BackgroundJobModel.job_type == TODAY_EVENT
                )
            )
        )

    class Failure(SqlAlchemyInboxUnitOfWork):
        def commit(self):
            raise InboxUnavailableError()

    sessions = create_session_factory(engine)
    failing = NotificationService(lambda: Failure(sessions), clock=lambda: clock[0])
    with pytest.raises(InboxUnavailableError):
        failing.put_timezone(owner, "Asia/Kolkata")
    assert inbox.put_timezone(owner, "UTC") == initial
    changed = inbox.put_timezone(owner, "America/New_York")
    clock[0] = old.scheduled_at
    inbox.today_reminder(old)
    assert inbox.count(owner)["unread_count"] == 0
    with factory() as unit:
        new = record(
            unit._require().scalar(
                select(BackgroundJobModel).where(
                    BackgroundJobModel.state == "pending",
                    BackgroundJobModel.job_type == TODAY_EVENT,
                )
            )
        )
    clock[0] = new.scheduled_at
    inbox.today_reminder(new)
    inbox.today_reminder(new)
    assert inbox.count(owner)["unread_count"] == 1 and changed.version == 2


def test_authoritative_unread_count_mixed_sources_read_expired_and_foreign_rows(engine):
    from dataclasses import replace
    from app.domain.notifications import AccountTimezone, plan_snapshot, today_snapshot

    owner, other = shared._active_account(engine), shared._active_account(engine)
    clock, plans, inbox, factory = services(engine)
    now = clock[0]
    first = create(plans, owner, now)
    second = plans.create(
        owner,
        PlanIntent("Synthetic item", now.date(), "UTC", now + timedelta(minutes=30)),
        "second",
        "test",
    )
    preference = AccountTimezone(owner, "UTC", now, 1)
    unread_plan = plan_snapshot(first, now)
    read_plan = replace(plan_snapshot(second, now), read_at=now, version=2)
    unread_today = today_snapshot(owner, preference, now.date(), 1, now)
    expired_time = now - timedelta(days=90)
    expired_today = today_snapshot(
        owner, preference, expired_time.date(), 1, expired_time
    )
    foreign_today = today_snapshot(
        other, AccountTimezone(other, "UTC", now, 1), now.date(), 1, now
    )
    with factory() as unit:
        for row in (unread_plan, read_plan, unread_today, expired_today, foreign_today):
            assert unit.inbox.insert(row)
        unit.commit()

    assert inbox.count(owner)["unread_count"] == 2
    assert inbox.count(owner, "plan")["unread_count"] == 1
    assert inbox.count(owner, "today")["unread_count"] == 1
    assert inbox.count(other)["unread_count"] == 1
    page = inbox.list(owner, "all", "UTC", limit=1)
    assert len(page["items"]) == 1 and page["unread_count"] == 2
    retained = inbox.list(owner, "all", "UTC")["items"]
    assert len(retained) == 3
    assert next(row for row in retained if row["notification_id"] == read_plan.id)[
        "is_read"
    ]
    assert expired_today.id not in {row["notification_id"] for row in retained}

    first_read = inbox.mark(owner, unread_plan.id)
    assert first_read["unread_count"] == 1
    clock[0] += timedelta(seconds=1)
    assert inbox.mark(owner, unread_plan.id)["read_at"] == first_read["read_at"]
    assert inbox.count(owner, "plan")["unread_count"] == 0
    assert inbox.count(owner, "today")["unread_count"] == 1
    assert inbox.mark_all(owner, "today")["marked_read_count"] == 1
    assert inbox.mark_all(owner, "all")["marked_read_count"] == 0
    assert inbox.count(owner)["unread_count"] == 0
    assert inbox.count(other)["unread_count"] == 1
    retained = inbox.list(owner, "all", "UTC")["items"]
    assert len(retained) == 3 and all(row["is_read"] for row in retained)
