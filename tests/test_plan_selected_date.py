"""Pure facts, service pagination, and deterministic full-date insights."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
import base64
import json
import pytest

from app.application.plan_selected_date import (
    PlanSelectedDateService,
    _context,
    _encode,
)
from app.domain.plan_read import (
    InsightComputationError,
    PlanDateRelation,
    PlanInsightCode,
    build_insight,
    ordering_key,
    section,
    summarize,
    timing_state,
)
from app.domain.planning import (
    PlanIntent,
    PlanValidationError,
    PlanUnavailableError,
)
from app.domain.today import aggregate
from tests.planning_support import fixture
from tests.test_today import ACCOUNT, NOW, item


def scheduled(index, *, start=NOW, end=None, priority=False):
    value = item(index, start=start, priority=priority)
    return replace(value, intent=replace(value.intent, scheduled_end_at=end))


@pytest.mark.parametrize(
    "start,end,complete,expected",
    [
        (NOW - timedelta(hours=1), NOW + timedelta(hours=1), False, "ongoing"),
        (NOW, NOW + timedelta(seconds=1), False, "ongoing"),
        (NOW, None, False, "past"),
        (NOW - timedelta(hours=1), NOW, False, "past"),
        (NOW - timedelta(hours=2), NOW - timedelta(hours=1), False, "past"),
        (NOW + timedelta(microseconds=1), None, False, "upcoming"),
        (None, None, False, "unscheduled"),
        (NOW + timedelta(hours=1), None, True, "completed"),
        (None, None, True, "completed"),
    ],
)
def test_exact_timing(start, end, complete, expected):
    value = scheduled(1, start=start, end=end)
    if complete:
        value = replace(value, completed_at=NOW)
    assert timing_state(value, NOW) == expected
    assert section(value) == (
        "completed"
        if complete
        else "unscheduled_incomplete" if start is None else "scheduled_incomplete"
    )


@pytest.mark.parametrize(
    "total,completed,percentage",
    [(0, 0, None), (2, 0, 0), (3, 1, 33), (8, 1, 13), (2, 2, 100)],
)
def test_summary_decimal_rounding_distinct_and_invariants(total, completed, percentage):
    values = [
        item(i + 1, complete=i < completed, start=NOW if i % 2 else None)
        for i in range(total)
    ]
    result = summarize(values + values)
    assert result.completion_percentage == percentage
    assert result.total == total and result.completed == completed
    assert result.completed + result.remaining == total
    assert result.scheduled_remaining + result.unscheduled_remaining == result.remaining


def test_global_order_all_buckets_chronology_priority_ties_and_completion():
    values = [
        item(8, complete=True),
        replace(item(9, complete=True), completed_at=NOW - timedelta(seconds=1)),
        item(7),
        item(6, priority=True),
        item(5, start=NOW + timedelta(hours=1), priority=True),
        item(4, start=NOW),
        item(3, start=NOW),
        replace(item(2, priority=True), created_at=item(6).created_at),
        replace(item(1, complete=True), completed_at=NOW),
    ]
    assert [value.id.int for value in sorted(values, key=ordering_key)] == [
        3,
        4,
        5,
        2,
        6,
        7,
        1,
        8,
        9,
    ]
    assert ordering_key(
        replace(values[0], completed_at=NOW + timedelta(microseconds=1))
    ) < ordering_key(values[0])


def current_candidates():
    return [
        scheduled(
            1,
            start=NOW - timedelta(hours=1),
            end=NOW + timedelta(hours=1),
            priority=True,
        ),
        scheduled(2, start=NOW - timedelta(hours=2), end=NOW + timedelta(hours=1)),
        scheduled(3, start=NOW - timedelta(hours=4), priority=True),
        scheduled(4, start=NOW - timedelta(hours=5)),
        scheduled(5, start=NOW + timedelta(hours=2), priority=True),
        scheduled(6, start=NOW + timedelta(hours=1)),
        item(7, priority=True),
        item(8),
    ]


@pytest.mark.parametrize("tier", range(8))
def test_current_full_precedence_and_evidence(tier):
    values = current_candidates()[tier:]
    result = build_insight(
        NOW.date(),
        PlanDateRelation.CURRENT,
        list(reversed(values)),
        summarize(values),
        NOW,
    )
    selected = values[0]
    assert result.code == tuple(PlanInsightCode)[tier]
    assert result.plan_item_id == selected.id and result.action == "plan_item.view"
    assert result.evidence.is_priority == selected.intent.is_priority
    assert result.evidence.timing_state == timing_state(selected, NOW)
    assert result.evidence.scheduled_start_at == selected.intent.scheduled_start_at
    assert result.evidence.total == len(values)
    assert result.state == "available"
    assert not {"confidence", "benefit", "prediction", "provider"} & set(asdict(result))


@pytest.mark.parametrize("relation", [PlanDateRelation.PAST, PlanDateRelation.FUTURE])
@pytest.mark.parametrize("tier", range(4))
def test_past_future_full_precedence(relation, tier):
    values = [
        scheduled(1, start=NOW + timedelta(hours=3), priority=True),
        scheduled(2),
        item(3, priority=True),
        item(4),
    ][tier:]
    result = build_insight(
        NOW.date(), relation, list(reversed(values)), summarize(values), NOW
    )
    assert result.plan_item_id == values[0].id
    if relation == "past":
        assert result.code == (
            "past_remaining_priority" if tier % 2 == 0 else "past_remaining"
        )
        assert "still incomplete" in result.reason
    else:
        assert (
            result.code
            == (
                "future_scheduled_priority",
                "future_scheduled",
                "future_unscheduled_priority",
                "future_unscheduled",
            )[tier]
        )
    assert (
        result.evidence.date_relation == relation and result.action == "plan_item.view"
    )


@pytest.mark.parametrize("relation", list(PlanDateRelation))
@pytest.mark.parametrize("complete", [False, True])
def test_complete_and_empty_all_dates_current_state_copy(relation, complete):
    values = (
        [item(1, complete=True, day=NOW.date() - timedelta(days=1))] if complete else []
    )
    result = build_insight(
        NOW.date() - timedelta(days=1), relation, values, summarize(values), NOW
    )
    assert result.code == (
        "selected_date_complete" if complete else "selected_date_empty"
    )
    assert result.state == ("available" if complete else "empty")
    assert result.action == "plan_item.create" and result.plan_item_id is None
    assert "currently assigned to this date" in result.reason
    assert result.evidence.timing_state is None


@pytest.mark.parametrize("relation", list(PlanDateRelation))
def test_insight_schedule_creation_uuid_ties(relation):
    first = scheduled(1, start=NOW + timedelta(hours=1), priority=True)
    second = replace(first, id=UUID(int=2))
    result = build_insight(
        NOW.date(), relation, [second, first], summarize([first, second]), NOW
    )
    assert result.plan_item_id == first.id
    first = item(1, priority=True)
    second = replace(first, id=UUID(int=2))
    result = build_insight(
        NOW.date(), relation, [second, first], summarize([first, second]), NOW
    )
    assert result.plan_item_id == first.id


def service_fixture(values=()):
    account, _, _, _, store, plans = fixture()
    plans.clock = lambda: NOW
    store.items = {
        value.id: replace(value, user_account_id=account) for value in values
    }
    return account, store, plans, PlanSelectedDateService(plans)


def test_pagination_full_date_summary_insight_filter_dedup_no_commits():
    account, store, plans, service = service_fixture(
        current_candidates() + [item(i, complete=i % 2 == 0) for i in range(10, 118)]
    )
    other_day = plans.create(
        account,
        PlanIntent("Other date", NOW.date() - timedelta(days=1), "UTC"),
        "other-day",
        "test",
    )
    foreign = plans.create(
        uuid4(), PlanIntent("Foreign", NOW.date(), "UTC"), "foreign", "test"
    )
    # Even a defective adapter returning duplicates/unscoped facts cannot affect the projection.
    from tests.planning_support import Repository

    original = Repository.selected_date_facts
    from unittest.mock import patch

    with patch.object(
        Repository,
        "selected_date_facts",
        lambda repo, owner, day: list(store.items.values()) * 2,
    ):
        first = service.get(account, NOW.date(), "UTC", "test")
    assert first.page.limit == 50 and len(first.items) == 50
    assert first.summary.total == 116 and first.summary.completed == 54
    assert first.insight.plan_item_id == UUID(int=1)
    seen = []
    cursor = None
    while True:
        result = service.get(account, NOW.date(), "UTC", "test", limit=7, cursor=cursor)
        seen.extend(value.id for value in result.items)
        assert result.summary == first.summary and result.insight == first.insight
        assert result.generated_at == result.freshness.as_of == NOW
        if not result.page.has_more:
            assert result.page.next_cursor is None
            break
        assert result.page.next_cursor is not None
        cursor = result.page.next_cursor
    expected = sorted(
        original(type("Repo", (), {"store": store})(), account, NOW.date()),
        key=ordering_key,
    )
    assert seen == [value.id for value in expected] and len(seen) == len(set(seen))
    assert other_day.id not in seen and foreign.id not in seen
    assert all(unit.commits == 0 for unit in store.units[2:])
    assert len(service.get(account, NOW.date(), "UTC", "test", limit=100).items) == 100


@pytest.mark.parametrize(
    "cursor", ["", "private-cursor", "e30", "a" * 1025, "!!!!", "bnVsbA", "W10", "eA"]
)
def test_malformed_cursors_safe(cursor):
    account, _, _, service = service_fixture()
    with pytest.raises(PlanValidationError, match="Invalid cursor"):
        service.get(account, NOW.date(), "UTC", "test", cursor=cursor)


@pytest.mark.parametrize("change", ["account", "date", "timezone"])
def test_incompatible_cursor_context(change):
    account, _, _, service = service_fixture([item(1), item(2)])
    cursor = service.get(account, NOW.date(), "UTC", "test", limit=1).page.next_cursor
    with pytest.raises(PlanValidationError):
        service.get(
            uuid4() if change == "account" else account,
            NOW.date() + timedelta(days=1) if change == "date" else NOW.date(),
            "America/New_York" if change == "timezone" else "UTC",
            "test",
            cursor=cursor,
        )


@pytest.mark.parametrize(
    "key",
    [
        [False, 0, 0, str(UUID(int=1))],
        [3, 0, 0, str(UUID(int=1))],
        [0, 1, 0, str(UUID(int=1))],
        [0, 0, 10**19, str(UUID(int=1))],
        [0, 0, 0, "bad"],
        [0, 0],
    ],
)
def test_cursor_key_shape(key):
    account, _, _, service = service_fixture()
    cursor = _encode(_context(account, NOW.date(), "UTC"), key)
    with pytest.raises(PlanValidationError):
        service.get(account, NOW.date(), "UTC", "test", cursor=cursor)


def test_forged_cursor_only_changes_position_never_authorization():
    account, store, _, service = service_fixture([item(1), item(2)])
    foreign = replace(item(3), user_account_id=uuid4())
    store.items[foreign.id] = foreign
    context = _context(account, NOW.date(), "UTC")
    cursor = _encode(context, (0, 0, -(10**18), str(UUID(int=0))))
    result = service.get(account, NOW.date(), "UTC", "test", cursor=cursor)
    assert {value.id for value in result.items} == {UUID(int=1), UUID(int=2)}
    payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert set(payload) == {"v", "context", "key"} and "Foreign" not in json.dumps(
        payload
    )


@pytest.mark.parametrize(
    "zone,relation",
    [
        ("America/New_York", "current"),
        ("UTC", "past"),
        ("America/Los_Angeles", "current"),
    ],
)
def test_requested_timezone_travel_does_not_reassign(zone, relation):
    account, _, plans, service = service_fixture([item(1)])
    plans.clock = lambda: datetime(2026, 10, 3, 3, 59, tzinfo=UTC)
    result = service.get(account, NOW.date(), zone, "test")
    assert result.date_relation == relation and result.summary.total == 1
    assert result.items[0].timezone == "America/New_York"
    assert result.items[0].scheduled_for_date == NOW.date()


@pytest.mark.parametrize(
    "delta,relation", [(-1, "past"), (0, "current"), (1, "future")]
)
def test_empty_date_relations(delta, relation):
    account, _, _, service = service_fixture()
    result = service.get(account, NOW.date() + timedelta(days=delta), "UTC", "test")
    assert result.date_relation == relation and result.summary.total == 0
    assert result.summary.completion_percentage is None and result.items == ()


def test_modeled_insight_failure_keeps_items_summary_and_safe_diagnostics(caplog):
    account, _, plans, _ = service_fixture([item(1)])
    from app.infrastructure.planning import SafePlanDiagnostic

    plans.diagnostic = SafePlanDiagnostic()
    caplog.set_level("INFO", logger="pos.planning.persistence")

    def fail(*args):
        raise InsightComputationError("private-internal-text")

    result = PlanSelectedDateService(plans, insight_builder=fail).get(
        account, NOW.date(), "UTC", "safe-test"
    )
    assert len(result.items) == 1 and result.summary.total == 1
    assert (
        result.insight.state == "unavailable"
        and result.insight.code == "insight_unavailable"
    )
    assert result.insight.action is None and result.insight.plan_item_id is None
    assert result.insight.evidence.total == 1
    assert "private-internal-text" not in str(asdict(result)) + caplog.text
    record = caplog.records[0]
    assert (
        record.operation == "plan_insight_unavailable"
        and record.outcome == "unavailable"
    )
    assert not {"cursor", "title", "exception", "sql"} & record.__dict__.keys()


def test_persistence_and_unexpected_insight_errors_are_not_hidden():
    account, _, plans, _ = service_fixture([item(1)])

    def fail(*args):
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        PlanSelectedDateService(plans, insight_builder=fail).get(
            account, NOW.date(), "UTC", "test"
        )
    plans.factory = lambda _: (_ for _ in ()).throw(PlanUnavailableError("unavailable"))
    with pytest.raises(PlanUnavailableError):
        PlanSelectedDateService(plans).get(account, NOW.date(), "UTC", "test")


def test_today_frozen_recommendation_for_ongoing_vs_past_is_preserved():
    ongoing = scheduled(
        1, start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=1), priority=True
    )
    past = scheduled(2, start=NOW - timedelta(hours=2), priority=True)
    today = aggregate(ACCOUNT, NOW, "UTC", [ongoing, past], [], (), {})
    insight = build_insight(
        NOW.date(),
        PlanDateRelation.CURRENT,
        [ongoing, past],
        summarize([ongoing, past]),
        NOW,
    )
    assert (
        today.contract_version == "1.0"
        and today.next_best_action.plan_item_id == past.id
    )
    assert today.next_best_action.code == "priority_past_start"
    assert (
        insight.plan_item_id == ongoing.id
        and insight.code == "current_ongoing_priority"
    )


def test_assigned_date_is_not_inferred_from_absolute_start():
    account, _, plans, service = service_fixture()
    # Late New York assignment has a UTC instant on the following date.
    value = plans.create(
        account,
        PlanIntent(
            "Synthetic late item",
            NOW.date(),
            "America/New_York",
            datetime(
                2026,
                10,
                2,
                23,
                30,
                tzinfo=ZoneInfo("America/New_York"),
            ),
        ),
        "late",
        "test",
    )
    assert value.intent.scheduled_start_at.date() != NOW.date()
    current = service.get(account, NOW.date(), "UTC", "test")
    next_day = service.get(account, NOW.date() + timedelta(days=1), "UTC", "test")
    assert current.summary.total == 1 and current.items[0].id == value.id
    assert next_day.summary.total == 0


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.5])
def test_application_limit_bounds(limit):
    account, _, _, service = service_fixture()
    with pytest.raises(PlanValidationError):
        service.get(account, NOW.date(), "UTC", "test", limit=limit)
