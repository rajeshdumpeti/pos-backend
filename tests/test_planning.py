from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from app.application.today import TodayService
from app.domain.planning import (
    PlanIntent,
    PlanValidationError,
    PlanConflictError,
    PlanIdempotencyConflictError,
    PlanNotFoundError,
    PlanUnavailableError,
)
from tests.planning_support import fixture, NOW


def intent(**changes):
    return PlanIntent(
        **{
            "title": "  Test item  ",
            "scheduled_for_date": NOW.date(),
            "timezone": "America/New_York",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": "x" * 201},
        {"timezone": "-04:00"},
        {"timezone": "Unknown/Place"},
        {"scheduled_start_at": datetime(2026, 9, 23, 10)},
        {"scheduled_start_at": datetime.fromisoformat("2026-09-23T10:00:00-05:00")},
        {"scheduled_end_at": NOW},
        {
            "scheduled_start_at": NOW.astimezone(ZoneInfo("America/New_York")),
            "scheduled_end_at": NOW.astimezone(ZoneInfo("America/New_York")),
        },
    ],
)
def test_invalid_intent(changes):
    with pytest.raises(PlanValidationError):
        intent(**changes).normalized()


def test_create_idempotency_original_response_and_atomic_failure():
    account, _, _, _, store, service = fixture()
    item = service.create(account, intent(), "create-1", "test")
    assert item.intent.title == "Test item"
    assert item.version == 1 and len(store.items) == len(store.receipts) == 1
    completed = service.complete(account, item.id, 1, "test")
    assert completed.completed_at == NOW
    assert service.create(account, intent(), "create-1", "test") == item
    with pytest.raises(PlanIdempotencyConflictError):
        service.create(account, intent(title="Different"), "create-1", "test")
    store.fail_commit = True
    with pytest.raises(PlanUnavailableError):
        service.create(account, intent(), "create-2", "test")
    assert len(store.items) == len(store.receipts) == 1


def test_update_concurrency_completion_replay_and_rollback():
    account, _, _, _, store, service = fixture()
    item = service.create(account, intent(is_priority=True), "create", "test")
    updated = service.update(account, item.id, 1, {"title": "Updated"}, "test")
    assert updated.version == 2
    with pytest.raises(PlanConflictError):
        service.update(account, item.id, 1, {"title": "Stale"}, "test")
    store.fail_commit = True
    with pytest.raises(PlanUnavailableError):
        service.complete(account, item.id, 2, "test")
    assert store.items[item.id] == updated
    store.fail_commit = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: service.complete(account, item.id, 2, "test"), range(2))
        )
    assert results[0] == results[1]
    assert results[0].version == 3 and not results[0].intent.is_priority
    assert sum(unit.commits for unit in store.units) == 3


def test_isolation_and_completed_item_leaves_active_sections():
    account, _, states, _, _, service = fixture()
    item = service.create(account, intent(is_priority=True), "create", "test")
    for operation in (
        lambda: service.get(uuid4(), item.id, "test"),
        lambda: service.update(uuid4(), item.id, 1, {"title": "Other"}, "test"),
        lambda: service.complete(uuid4(), item.id, 1, "test"),
    ):
        with pytest.raises(PlanNotFoundError):
            operation()
    service.complete(account, item.id, 1, "test")
    today = TodayService(service).get(
        account, "America/New_York", states[account], "test"
    )
    assert today.score.percentage == 100
    assert not today.top_priorities.items and not today.up_next.items
    assert len(today.wins.items) == 1
    service.complete(account, item.id, 1, "test")
    assert (
        len(
            TodayService(service)
            .get(account, "America/New_York", states[account], "test")
            .wins.items
        )
        == 1
    )


@pytest.mark.parametrize(
    "value,valid",
    [
        ("2026-03-08T02:30:00-05:00", False),
        ("2026-03-08T03:30:00-04:00", True),
        ("2026-11-01T01:30:00-04:00", True),
        ("2026-11-01T01:30:00-05:00", True),
    ],
)
def test_dst_assignments(value, valid):
    instant = datetime.fromisoformat(value)
    data = intent(scheduled_for_date=instant.date(), scheduled_start_at=instant)
    if valid:
        assert data.normalized().scheduled_start_at == instant.astimezone(UTC)
    else:
        with pytest.raises(PlanValidationError):
            data.normalized()


def test_timezone_change_retains_stored_intent():
    account, _, states, _, store, service = fixture()
    service.clock = lambda: datetime(2026, 9, 24, 2, tzinfo=UTC)
    item = service.create(account, intent(), "create", "test")
    ny = TodayService(service).get(account, "America/New_York", states[account], "test")
    tokyo = TodayService(service).get(account, "Asia/Tokyo", states[account], "test")
    assert ny.local_date.isoformat() == "2026-09-23" and ny.score.denominator == 1
    assert tokyo.local_date.isoformat() == "2026-09-24" and tokyo.score.denominator == 0
    service.clock = lambda: datetime(2026, 9, 24, 4, tzinfo=UTC)
    assert (
        TodayService(service)
        .get(account, "America/New_York", states[account], "test")
        .score.denominator
        == 0
    )
    assert store.items[item.id] == item


@pytest.mark.parametrize(
    "instant,hours",
    [
        (datetime(2026, 3, 8, 12, tzinfo=UTC), 23),
        (datetime(2026, 11, 1, 12, tzinfo=UTC), 25),
    ],
)
def test_today_completion_window_tracks_dst_day_length(instant, hours):
    account, _, states, _, store, service = fixture()
    captured = []
    from tests.planning_support import Unit, Repository

    class CapturingRepository(Repository):
        def today_facts(self, account_id, day, start, end):
            captured.append((account_id, day, start, end))
            return super().today_facts(account_id, day, start, end)

    def factory(_):
        unit = Unit(store)
        unit.plans = CapturingRepository(store)
        return unit

    service.factory = factory
    service.clock = lambda: instant
    TodayService(service).get(account, "America/New_York", states[account], "test")
    assert len(captured) == 1
    assert (captured[0][3] - captured[0][2]).total_seconds() == hours * 3600
    assert captured[0][0] == account


def test_travel_preserves_absolute_scheduled_instant():
    account, _, states, _, store, service = fixture()
    start = datetime.fromisoformat("2026-09-23T10:00:00-04:00")
    created = service.create(
        account, intent(scheduled_start_at=start), "scheduled", "test"
    )
    TodayService(service).get(account, "Asia/Tokyo", states[account], "test")
    assert store.items[created.id].intent.scheduled_start_at == datetime(
        2026, 9, 23, 14, tzinfo=UTC
    )
    assert store.items[created.id].intent.timezone == "America/New_York"
