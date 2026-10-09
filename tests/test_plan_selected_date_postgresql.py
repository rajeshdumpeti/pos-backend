"""Live selected-date tests reuse only the guarded local pos_test fixtures."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.application.plan_selected_date import (
    PlanSelectedDateService,
    _context,
    _encode,
)
from app.domain.planning import PlanIntent, PlanValidationError
from app.infrastructure.planning import _values
from app.infrastructure.planning_models import PlanItemModel
from tests import test_planning_postgresql as planning_postgresql
from tests.test_onboarding_postgresql import _active_account, NOW

engine = planning_postgresql.engine
empty_tables = planning_postgresql.empty_tables
service = planning_postgresql.service
TEST_URL = planning_postgresql.TEST_URL

pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)


def test_selected_date_filter_account_isolation_and_current_state(engine):
    account, other = _active_account(engine), _active_account(engine)
    plans = service(engine)
    day = NOW.date() - timedelta(days=1)
    past = plans.create(
        account, PlanIntent("Synthetic past", day, "UTC"), "past", "test"
    )
    incomplete = plans.create(
        account, PlanIntent("Synthetic remaining", day, "UTC"), "remaining", "test"
    )
    plans.create(other, PlanIntent("Synthetic foreign", day, "UTC"), "foreign", "test")
    plans.create(
        account,
        PlanIntent("Synthetic other date", NOW.date(), "UTC"),
        "other-date",
        "test",
    )
    plans.complete(account, past.id, 1, "test")
    reader = PlanSelectedDateService(plans)
    result = reader.get(account, day, "UTC", "test", limit=1)
    assert (
        result.summary.total == 2
        and result.summary.completed == 1
        and result.summary.unscheduled_remaining == 1
    )
    assert (
        result.items[0].id == incomplete.id
        and result.items[0].scheduled_start_at is None
    )
    assert (
        result.date_relation == "past" and "still incomplete" in result.insight.reason
    )
    second = reader.get(
        account, day, "UTC", "test", limit=1, cursor=result.page.next_cursor
    )
    assert (
        second.items[0].completed_at == NOW
        and second.items[0].timing_state == "completed"
    )
    assert second.summary == result.summary and second.page.next_cursor is None
    with pytest.raises(PlanValidationError):
        reader.get(other, day, "UTC", "test", cursor=result.page.next_cursor)
    forged = _encode(_context(other, day, "UTC"), (0, 0, -(10**18), str(UUID(int=0))))
    foreign = reader.get(other, day, "UTC", "test", cursor=forged)
    assert len(foreign.items) == 1 and foreign.items[0].id not in {
        past.id,
        incomplete.id,
    }


def test_stable_global_order_and_cursor_traversal_full_summary(engine):
    account = _active_account(engine)
    plans = service(engine)
    values = []
    for i in range(1, 9):
        start = NOW + timedelta(hours=1) if i <= 3 else None
        value = plans.create(
            account,
            PlanIntent(
                "Synthetic order", NOW.date(), "UTC", start, None, i in (3, 4, 5)
            ),
            str(i),
            "test",
        )
        # Controlled UUID and creation ties prove persisted reconstruction order.
        values.append(replace(value, id=UUID(int=i), created_at=NOW, updated_at=NOW))
    with engine.begin() as connection:
        connection.execute(
            PlanItemModel.__table__.delete().where(
                PlanItemModel.user_account_id == account
            )
        )
        connection.execute(
            PlanItemModel.__table__.insert(), [_values(value) for value in values]
        )
    plans.complete(account, UUID(int=7), 1, "test")
    plans.complete(account, UUID(int=8), 1, "test")
    with engine.begin() as connection:
        connection.execute(
            PlanItemModel.__table__.update()
            .where(PlanItemModel.id == UUID(int=8))
            .values(
                completed_at=NOW + timedelta(microseconds=1),
                updated_at=NOW + timedelta(microseconds=1),
            )
        )
    reader = PlanSelectedDateService(plans)
    seen = []
    cursor = None
    while True:
        result = reader.get(account, NOW.date(), "UTC", "test", limit=2, cursor=cursor)
        seen.extend(value.id.int for value in result.items)
        assert result.summary.total == 8 and result.summary.completed == 2
        assert (
            result.summary.scheduled_remaining
            == result.summary.unscheduled_remaining
            == 3
        )
        assert result.summary.completion_percentage == 25
        assert result.insight.plan_item_id == UUID(int=3)
        if not result.page.has_more:
            assert result.page.next_cursor is None
            break
        cursor = result.page.next_cursor
    assert seen == [1, 2, 3, 4, 5, 6, 8, 7]
    with Session(engine) as session:
        assert (
            len(
                session.scalars(
                    select(PlanItemModel).where(
                        PlanItemModel.user_account_id == account
                    )
                ).all()
            )
            == 8
        )
