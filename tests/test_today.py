from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import pytest
from app.domain.planning import PlanItem, PlanIntent
from app.domain.today import aggregate, RecommendationCode

NOW = datetime(2026, 10, 2, 14, tzinfo=UTC)
ACCOUNT = uuid4()


def item(index, *, start=None, priority=False, complete=False, day=None, owner=ACCOUNT):
    return PlanItem(
        UUID(int=index),
        owner,
        PlanIntent(
            f"Item {index}",
            day or NOW.date(),
            "America/New_York",
            start,
            None,
            priority,
        ),
        NOW if complete else None,
        NOW - timedelta(hours=5) + timedelta(seconds=index),
        NOW,
        1,
    )


def today(items, completions=None, now=NOW, zone="America/New_York"):
    return aggregate(
        ACCOUNT,
        now,
        zone,
        items,
        items if completions is None else completions,
        ("focus",),
        {"daily_steps": 8000},
    )


@pytest.mark.parametrize(
    "items,code",
    [
        ([], "getting_started"),
        ([item(1, complete=True)], "complete"),
        ([item(1, start=NOW - timedelta(minutes=1))], "needs_attention"),
        ([item(1, complete=True), item(2, start=NOW + timedelta(hours=1))], "on_track"),
        ([item(1, start=NOW + timedelta(hours=1))], "getting_started"),
    ],
)
def test_status(items, code):
    result = today(items).daily_status
    assert result.code == code
    assert result.eligible == result.completed + result.remaining


@pytest.mark.parametrize("tier", range(6))
def test_all_recommendation_precedence_tiers(tier):
    candidates = [
        item(1, start=NOW - timedelta(hours=1), priority=True),
        item(2, start=NOW - timedelta(hours=1)),
        item(3, start=NOW + timedelta(hours=1), priority=True),
        item(4, start=NOW + timedelta(hours=1)),
        item(5, priority=True),
        item(6),
    ]
    result = today(list(reversed(candidates[tier:]))).next_best_action
    assert result.plan_item_id == candidates[tier].id
    assert result.code == tuple(RecommendationCode)[tier]
    assert result.action == "plan_item.view"
    assert result.evidence.is_priority == candidates[tier].intent.is_priority
    assert (
        result.evidence.scheduled_start_at == candidates[tier].intent.scheduled_start_at
    )


def test_no_score_zero_equal_weights_rounding_and_deduplication():
    assert today([]).score.percentage is None
    assert today([item(1)]).score.percentage == 0
    items = [item(i, complete=i <= 1, priority=i == 2) for i in range(1, 9)]
    result = today(items + [items[0], item(10, owner=uuid4())]).score
    assert result.percentage == 13 and result.numerator == 1 and result.denominator == 8
    assert all(entry.weight == 1 for entry in result.breakdown)
    assert [entry.item_id for entry in result.breakdown] == sorted(
        [i.id for i in items]
    )
    assert today([item(1, complete=True), item(2), item(3)]).score.percentage == 33


def test_up_next_maximum_labels_ties_and_completion_exclusion():
    result = today(
        [
            item(3, start=NOW + timedelta(hours=1)),
            item(2, start=NOW + timedelta(hours=1)),
            item(1, start=NOW),
            item(4, start=NOW + timedelta(hours=2)),
            item(5, start=NOW + timedelta(hours=3)),
            item(6, start=NOW, complete=True),
        ]
    ).up_next
    assert [entry.item.plan_item_id.int for entry in result.items] == [1, 2, 3, 4]
    assert [entry.label for entry in result.items] == ["NOW", "NEXT", "LATER", "LATER"]
    assert (
        today([item(1, start=NOW + timedelta(seconds=1))]).up_next.items[0].label
        == "NEXT"
    )
    assert today([item(1)]).up_next.state == "empty"


def test_priorities_wins_and_unsupported_measurements():
    active = [item(i, priority=True) for i in range(1, 6)]
    completed = [
        replace(item(i, complete=True), completed_at=NOW - timedelta(seconds=i))
        for i in range(6, 11)
    ]
    result = today(active + completed, completed + completed)
    assert [entry.plan_item_id.int for entry in result.top_priorities.items] == [
        1,
        2,
        3,
    ]
    assert [entry.plan_item_id.int for entry in result.wins.items] == [6, 7, 8]
    assert result.progress.state == "unavailable" and result.progress.metrics == ()
    assert result.capabilities.configured_goals == ("daily_steps",)
    assert result.capabilities.measurement_sources == ()
    assert set(result.capabilities.actions) == {
        "plan_item.create",
        "plan_item.view",
        "plan_item.update",
        "plan_item.complete",
    }
    empty = today([])
    assert (
        empty.next_best_action.state == "new_user"
        and empty.next_best_action.action == "plan_item.create"
    )
    assert today([item(1, complete=True)]).next_best_action.state == "insufficient_data"


@pytest.mark.parametrize(
    "now,day",
    [
        (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), "2026-03-08"),
        (datetime(2026, 3, 8, 7, 1, tzinfo=UTC), "2026-03-08"),
        (datetime(2026, 11, 1, 5, 59, tzinfo=UTC), "2026-11-01"),
        (datetime(2026, 11, 1, 6, 1, tzinfo=UTC), "2026-11-01"),
        (datetime(2026, 10, 3, 3, 59, tzinfo=UTC), "2026-10-02"),
        (datetime(2026, 10, 3, 4, tzinfo=UTC), "2026-10-03"),
    ],
)
def test_local_day_dst_and_rollover(now, day):
    result = today([item(1)], now=now)
    assert result.local_date.isoformat() == day
    assert result.score.denominator == int(day == "2026-10-02")


def test_wins_from_prior_assignment_completed_today():
    prior = replace(
        item(1, day=NOW.date() - timedelta(days=1), complete=True), completed_at=NOW
    )
    result = today([prior], [prior])
    assert result.score.denominator == 0 and len(result.wins.items) == 1
    assert (
        today(
            [], [replace(prior, completed_at=datetime(2026, 10, 2, 3, 59, tzinfo=UTC))]
        ).wins.state
        == "empty"
    )


def test_priorities_order_overdue_scheduled_unscheduled_and_uuid():
    items = [
        item(8, priority=True),
        item(7, start=NOW + timedelta(hours=2), priority=True),
        item(6, start=NOW - timedelta(hours=1), priority=True),
        item(5, start=NOW - timedelta(hours=1), priority=True),
        item(4, start=NOW - timedelta(hours=2)),
        item(3, start=NOW, priority=True, complete=True),
    ]
    assert [entry.plan_item_id.int for entry in today(items).top_priorities.items] == [
        5,
        6,
        7,
    ]
    assert [
        entry.plan_item_id.int
        for entry in today([items[0], items[1]]).top_priorities.items
    ] == [7, 8]
