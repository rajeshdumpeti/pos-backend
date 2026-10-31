"""Approved producer, timezone, Inbox history and atomicity contracts."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app.application.jobs import JobWorker
from app.domain.jobs import new_job
from app.domain.notifications import (
    PLAN_EVENT,
    TODAY_EVENT,
    CLEANUP_TYPE,
    InboxValidationError,
    InboxNotFoundError,
    InboxUnavailableError,
    next_evaluation,
    local_evaluation,
    plan_snapshot,
    today_snapshot,
    AccountTimezone,
    RETENTION,
)
from app.domain.planning import PlanIntent
from tests.notification_support import fixture, Unit


def create(plans, owner, now, key="first", minutes=30):
    start = now + timedelta(minutes=minutes) if minutes is not None else None
    return plans.create(
        owner, PlanIntent("Synthetic item", now.date(), "UTC", start), key, "test"
    )


def worker(store, clock, inbox):
    return JobWorker(
        lambda: Unit(store),
        {
            PLAN_EVENT: inbox.plan_reminder,
            TODAY_EVENT: inbox.today_reminder,
            CLEANUP_TYPE: inbox.cleanup,
        },
        clock=lambda: clock[0],
    )


def test_timezone_atomic_create_identical_and_changed_version_supersedes():
    owner, _, _, store, clock, _, inbox = fixture()
    first = inbox.put_timezone(owner, "America/New_York")
    assert first.version == 1
    assert (
        inbox.put_timezone(owner, "America/New_York") == first and len(store.rows) == 1
    )
    second = inbox.put_timezone(owner, "Europe/London")
    assert second.version == 2
    assert sorted(job.state for job in store.rows.values()) == ["pending", "superseded"]
    store.fail_commit = True
    with pytest.raises(InboxUnavailableError):
        inbox.put_timezone(owner, "Asia/Kolkata")
    assert store.preferences[owner] == second and len(store.rows) == 2


@pytest.mark.parametrize(
    "zone", ["UTC-04:00", "+04:00", "not/a-zone", "../private", "", None]
)
def test_invalid_timezone_does_not_schedule(zone):
    owner, _, _, store, _, _, inbox = fixture()
    with pytest.raises(InboxValidationError):
        inbox.put_timezone(owner, zone)
    assert not store.rows and not store.preferences


@pytest.mark.parametrize(
    "before,expected_hours",
    [
        (datetime(2026, 3, 7, 23, tzinfo=UTC), 23),
        (datetime(2026, 10, 31, 22, tzinfo=UTC), 25),
    ],
)
def test_daily_calendar_occurrences_cross_dst_without_fixed_24_hours(
    before, expected_hours
):
    day, instant = next_evaluation(before, "America/New_York")
    next_day, next_instant = next_evaluation(instant, "America/New_York", day)
    assert (next_instant - instant).total_seconds() == expected_hours * 3600
    assert next_day == day + timedelta(days=1)


def test_exact_eighteen_and_after_eighteen_choose_next_valid_local_occurrence():
    exact = datetime(2026, 10, 30, 18, tzinfo=UTC)
    assert next_evaluation(exact, "UTC")[1] == exact
    assert next_evaluation(exact + timedelta(microseconds=1), "UTC")[0] == date(
        2026, 10, 31
    )
    assert local_evaluation(date(2011, 12, 30), "Pacific/Apia") is None


@pytest.mark.parametrize(
    "minutes,delta", [(30, 15), (7, 0), (0, None), (-1, None), (None, None)]
)
def test_plan_schedules_minus_fifteen_or_immediately_and_never_past_or_unscheduled(
    minutes, delta
):
    owner, _, _, store, clock, plans, inbox = fixture()
    now = clock[0]
    create(plans, owner, now, minutes=minutes)
    assert len(store.rows) == (0 if delta is None else 1)
    if delta is not None:
        assert next(iter(store.rows.values())).scheduled_at == now + timedelta(
            minutes=delta
        )
    assert not store.notifications and not store.preferences


def test_plan_retry_snapshot_immutable_completed_navigation_and_rescheduling():
    owner, _, _, store, clock, plans, inbox = fixture()
    item = create(plans, owner, clock[0])
    clock[0] += timedelta(minutes=15)
    work = worker(store, clock, inbox)
    assert work.process_pending().succeeded == 1
    row = next(iter(store.notifications.values()))
    job = next(iter(store.rows.values()))
    inbox.plan_reminder(job)
    assert len(store.notifications) == 1
    changed = plans.update(
        owner,
        item.id,
        1,
        {
            "scheduled_start_at": clock[0] + timedelta(minutes=40),
            "title": "Changed synthetic",
        },
        "test",
    )
    assert row == store.notifications[row.id]
    plans.complete(owner, item.id, changed.version, "test")
    result = inbox.list(owner, "plan", "UTC")["items"][0]
    assert result["navigation"]["state"] == "available"
    assert all(job.state != "pending" for job in store.rows.values())
    assert store.notifications[row.id].message == row.message


def test_pending_reschedule_supersedes_and_running_handler_revalidates_version_completion_and_deletion():
    owner, _, _, store, clock, plans, inbox = fixture()
    item = create(plans, owner, clock[0])
    old = next(iter(store.rows.values()))
    updated = plans.update(
        owner, item.id, 1, {"scheduled_start_at": clock[0] + timedelta(hours=1)}, "test"
    )
    assert store.rows[old.id].state == "superseded"
    clock[0] += timedelta(minutes=20)
    inbox.plan_reminder(old)
    assert not store.notifications
    new = [r for r in store.rows.values() if r.state == "pending"][0]
    plans.complete(owner, item.id, updated.version, "test")
    inbox.plan_reminder(new)
    assert not store.notifications
    del store.items[item.id]
    inbox.plan_reminder(new)
    assert not store.notifications


def test_plan_commit_failure_rolls_back_item_receipt_and_job():
    owner, _, _, store, clock, plans, _ = fixture()
    store.fail_commit = True
    with pytest.raises(InboxUnavailableError):
        create(plans, owner, clock[0])
    assert not store.items and not store.receipts and not store.rows


@pytest.mark.parametrize("count", [0, 1, 2])
def test_today_exact_eighteen_eligibility_copy_and_next_occurrence(count):
    owner, _, _, store, clock, plans, inbox = fixture()
    inbox.put_timezone(owner, "UTC")
    for i in range(count):
        create(plans, owner, clock[0], key=str(i), minutes=None)
    job = next(iter(store.rows.values()))
    clock[0] = job.scheduled_at
    inbox.today_reminder(job)
    inbox.today_reminder(job)
    assert len(store.notifications) == (1 if count else 0)
    if count:
        row = next(iter(store.notifications.values()))
        assert (
            row.message
            == f'You have {count} item{"" if count==1 else "s"} remaining today.'
        )
        assert (
            row.selected_date == clock[0].date()
            and row.expires_at - row.created_at == RETENTION
        )
    following = [
        r
        for r in store.rows.values()
        if r.payload.get("date") == (clock[0].date() + timedelta(days=1)).isoformat()
    ]
    assert len(following) == 1 and following[0].scheduled_at.hour == 18


def test_stale_today_timezone_generation_and_prior_day_never_emit_old_copy():
    owner, _, _, store, clock, plans, inbox = fixture()
    create(plans, owner, clock[0], minutes=None)
    inbox.put_timezone(owner, "UTC")
    old = next(iter(store.rows.values()))
    inbox.put_timezone(owner, "America/New_York")
    clock[0] = old.scheduled_at
    inbox.today_reminder(old)
    assert not store.notifications
    current = [r for r in store.rows.values() if r.state == "pending"][0]
    clock[0] = current.scheduled_at + timedelta(days=2)
    inbox.today_reminder(current)
    assert not store.notifications
    assert any(
        r.payload.get("date") == clock[0].date().isoformat()
        for r in store.rows.values()
    )


def populated():
    data = fixture()
    owner, _, _, store, clock, plans, inbox = data
    item = create(plans, owner, clock[0])
    pref = AccountTimezone(owner, "UTC", clock[0], 1)
    rows = [
        plan_snapshot(item, clock[0]),
        today_snapshot(owner, pref, clock[0].date(), 1, clock[0]),
        replace(
            today_snapshot(
                owner,
                pref,
                clock[0].date() - timedelta(days=1),
                2,
                clock[0] - timedelta(days=1),
            ),
            id=uuid4(),
        ),
    ]
    for row in rows:
        store.notifications[row.id] = row
    return data, rows


def test_empty_and_full_scope_count_order_ties_pagination_filters_and_ownership():
    data, rows = populated()
    owner, _, _, store, clock, _, inbox = data
    foreign = uuid4()
    store.owners.add(foreign)
    assert inbox.list(foreign, "all", "UTC")["items"] == []
    first = inbox.list(owner, "all", "UTC", 1)
    assert first["unread_count"] == 3 and first["has_more"]
    seen = [first["items"][0]["notification_id"]]
    cursor = first["next_cursor"]
    while cursor:
        page = inbox.list(owner, "all", "UTC", 1, cursor)
        seen.extend(r["notification_id"] for r in page["items"])
        cursor = page["next_cursor"]
    assert seen == [
        r.id for r in sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)
    ]
    assert (
        inbox.count(owner, "plan")["unread_count"] == 1
        and inbox.count(owner, "today")["unread_count"] == 2
    )
    for other, source, timezone in [
        (foreign, "all", "UTC"),
        (owner, "plan", "UTC"),
        (owner, "all", "Asia/Kolkata"),
    ]:
        with pytest.raises(InboxValidationError):
            inbox.list(other, source, timezone, 1, first["next_cursor"])
    with pytest.raises(InboxValidationError):
        inbox.list(owner, "all", "UTC", 1, "private-cursor")


def test_read_idempotence_mark_all_scope_expiry_and_safe_unavailable_navigation():
    data, rows = populated()
    owner, _, _, store, clock, plans, inbox = data
    first = inbox.mark(owner, rows[0].id)
    clock[0] += timedelta(minutes=1)
    assert inbox.mark(owner, rows[0].id)["read_at"] == first["read_at"]
    assert inbox.mark_all(owner, "today")["marked_read_count"] == 2
    assert inbox.mark_all(owner, "all")["marked_read_count"] == 0
    del store.items[rows[0].plan_item_id]
    navigation = next(r for r in inbox.list(owner, "plan", "UTC")["items"])[
        "navigation"
    ]
    assert navigation == {
        "state": "unavailable",
        "action": None,
        "selected_date": None,
        "plan_item_id": None,
    }
    clock[0] = rows[0].expires_at
    assert (
        inbox.count(owner)["unread_count"] == 0
        and inbox.list(owner, "all", "UTC")["items"] == []
    )
    with pytest.raises(InboxNotFoundError):
        inbox.mark(owner, rows[0].id)
    job = new_job(CLEANUP_TYPE, None, {"limit": 1}, clock[0], "cleanup", clock[0])
    inbox.cleanup(job)
    assert len(store.notifications) == 2


def test_grouping_uses_created_time_in_requested_timezone_not_notification_source():
    data, rows = populated()
    owner, _, _, store, clock, _, inbox = data
    instant = datetime(2026, 11, 1, 1, tzinfo=UTC)
    clock[0] = instant + timedelta(hours=4)
    row = replace(
        rows[0], created_at=instant, expires_at=instant + RETENTION, updated_at=instant
    )
    store.notifications = {row.id: row}
    assert inbox.list(owner, "all", "UTC")["items"][0]["group"] == "today"
    assert (
        inbox.list(owner, "all", "America/New_York")["items"][0]["group"] == "earlier"
    )


def test_concurrent_producer_retry_and_mark_read_are_logically_idempotent():
    owner, _, _, store, clock, plans, inbox = fixture()
    create(plans, owner, clock[0])
    job = next(iter(store.rows.values()))
    clock[0] = job.scheduled_at
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: inbox.plan_reminder(job), range(2)))
    assert len(store.notifications) == 1
    identifier = next(iter(store.notifications))
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: inbox.mark(owner, identifier), range(2)))
    assert (
        values[0]["read_at"] == values[1]["read_at"]
        and inbox.count(owner)["unread_count"] == 0
    )


def test_cursor_wrong_boundary_types_safe_and_foreign_notification_is_indistinguishable():
    import base64
    import json
    from app.domain.notifications import digest

    data, rows = populated()
    owner, _, _, store, clock, _, inbox = data
    value = base64.urlsafe_b64encode(
        json.dumps([digest(owner, "all", "UTC"), clock[0].isoformat(), 17]).encode()
    ).decode()
    with pytest.raises(InboxValidationError):
        inbox.list(owner, "all", "UTC", 1, value)
    foreign = uuid4()
    store.owners.add(foreign)
    for identifier in (rows[0].id, uuid4()):
        with pytest.raises(InboxNotFoundError):
            inbox.mark(foreign, identifier)


def test_mark_all_is_account_and_source_scoped_under_concurrent_calls():
    data, rows = populated()
    owner, _, _, store, clock, _, inbox = data
    foreign = uuid4()
    store.owners.add(foreign)
    other = replace(rows[0], id=uuid4(), user_account_id=foreign)
    store.notifications[other.id] = other
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: inbox.mark_all(owner, "today"), range(2)))
    assert sorted(r["marked_read_count"] for r in results) == [0, 2]
    assert (
        inbox.count(owner, "plan")["unread_count"] == 1
        and inbox.count(foreign)["unread_count"] == 1
    )


def test_claimed_plan_job_cannot_emit_after_completion_and_disabled_owner_is_ignored():
    owner, _, _, store, clock, plans, inbox = fixture()
    item = create(plans, owner, clock[0])
    job = next(iter(store.rows.values()))
    clock[0] = job.scheduled_at
    plans.complete(owner, item.id, 1, "test")
    inbox.plan_reminder(job)
    assert not store.notifications
    store.owners.remove(owner)
    inbox.plan_reminder(job)
    assert not store.notifications
