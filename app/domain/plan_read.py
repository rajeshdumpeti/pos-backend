"""Selected-date read facts and deterministic insight; no historical snapshots."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from uuid import UUID

from app.domain.planning import PlanItem
from app.domain.plan_selection import assigned_date_key, recommendation_key


class PlanDateRelation(StrEnum):
    PAST = "past"
    CURRENT = "current"
    FUTURE = "future"


class PlanItemSection(StrEnum):
    SCHEDULED_INCOMPLETE = "scheduled_incomplete"
    UNSCHEDULED_INCOMPLETE = "unscheduled_incomplete"
    COMPLETED = "completed"


class PlanTimingState(StrEnum):
    ONGOING = "ongoing"
    PAST = "past"
    UPCOMING = "upcoming"
    UNSCHEDULED = "unscheduled"
    COMPLETED = "completed"


class PlanInsightState(StrEnum):
    AVAILABLE = "available"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class PlanInsightCode(StrEnum):
    CURRENT_ONGOING_PRIORITY = "current_ongoing_priority"
    CURRENT_ONGOING = "current_ongoing"
    CURRENT_PAST_PRIORITY = "current_past_priority"
    CURRENT_PAST = "current_past"
    CURRENT_UPCOMING_PRIORITY = "current_upcoming_priority"
    CURRENT_UPCOMING = "current_upcoming"
    CURRENT_UNSCHEDULED_PRIORITY = "current_unscheduled_priority"
    CURRENT_UNSCHEDULED = "current_unscheduled"
    PAST_REMAINING_PRIORITY = "past_remaining_priority"
    PAST_REMAINING = "past_remaining"
    FUTURE_SCHEDULED_PRIORITY = "future_scheduled_priority"
    FUTURE_SCHEDULED = "future_scheduled"
    FUTURE_UNSCHEDULED_PRIORITY = "future_unscheduled_priority"
    FUTURE_UNSCHEDULED = "future_unscheduled"
    SELECTED_DATE_COMPLETE = "selected_date_complete"
    SELECTED_DATE_EMPTY = "selected_date_empty"
    INSIGHT_UNAVAILABLE = "insight_unavailable"


class PlanInsightAction(StrEnum):
    CREATE = "plan_item.create"
    VIEW = "plan_item.view"


class InsightComputationError(RuntimeError):
    """Explicitly modeled insight-only failure; persistence errors are excluded."""


@dataclass(frozen=True)
class PlanSummary:
    total: int
    completed: int
    remaining: int
    scheduled_remaining: int
    unscheduled_remaining: int
    completion_percentage: int | None


@dataclass(frozen=True)
class PlanInsightEvidence:
    selected_date: date
    date_relation: PlanDateRelation
    total: int
    completed: int
    remaining: int
    is_priority: bool | None
    timing_state: PlanTimingState | None
    scheduled_start_at: datetime | None


@dataclass(frozen=True)
class PlanInsight:
    state: PlanInsightState
    code: PlanInsightCode
    recommendation: str
    reason: str
    action: PlanInsightAction | None
    plan_item_id: UUID | None
    evidence: PlanInsightEvidence


def timing_state(item: PlanItem, now: datetime) -> PlanTimingState:
    if item.completed_at is not None:
        return PlanTimingState.COMPLETED
    start, end = item.intent.scheduled_start_at, item.intent.scheduled_end_at
    if start is None:
        return PlanTimingState.UNSCHEDULED
    if start > now:
        return PlanTimingState.UPCOMING
    if end is not None and now < end:
        return PlanTimingState.ONGOING
    return PlanTimingState.PAST


def section(item: PlanItem) -> PlanItemSection:
    if item.completed_at is not None:
        return PlanItemSection.COMPLETED
    if item.intent.scheduled_start_at is not None:
        return PlanItemSection.SCHEDULED_INCOMPLETE
    return PlanItemSection.UNSCHEDULED_INCOMPLETE


def _microseconds(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def ordering_key(item: PlanItem) -> tuple[int, int, int, str]:
    if item.completed_at is not None:
        return 2, 0, -_microseconds(item.completed_at), str(item.id)
    if item.intent.scheduled_start_at is not None:
        return 0, 0, _microseconds(item.intent.scheduled_start_at), str(item.id)
    return (
        1,
        int(not item.intent.is_priority),
        _microseconds(item.created_at),
        str(item.id),
    )


def summarize(items: list[PlanItem]) -> PlanSummary:
    distinct = {item.id: item for item in items}.values()
    total = len(distinct)
    completed = sum(item.completed_at is not None for item in distinct)
    scheduled = sum(
        item.completed_at is None and item.intent.scheduled_start_at is not None
        for item in distinct
    )
    remaining = total - completed
    percentage = (
        None
        if total == 0
        else int(
            (Decimal(completed) * 100 / Decimal(total)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    )
    return PlanSummary(
        total, completed, remaining, scheduled, remaining - scheduled, percentage
    )


def evidence(
    day: date,
    relation: PlanDateRelation,
    summary: PlanSummary,
    now: datetime,
    item: PlanItem | None = None,
) -> PlanInsightEvidence:
    return PlanInsightEvidence(
        day,
        relation,
        summary.total,
        summary.completed,
        summary.remaining,
        item.intent.is_priority if item else None,
        timing_state(item, now) if item else None,
        item.intent.scheduled_start_at if item else None,
    )


def build_insight(
    day: date,
    relation: PlanDateRelation,
    items: list[PlanItem],
    summary: PlanSummary,
    now: datetime,
) -> PlanInsight:
    active = [item for item in items if item.completed_at is None]
    if not active:
        empty = summary.total == 0
        return PlanInsight(
            PlanInsightState.EMPTY if empty else PlanInsightState.AVAILABLE,
            (
                PlanInsightCode.SELECTED_DATE_EMPTY
                if empty
                else PlanInsightCode.SELECTED_DATE_COMPLETE
            ),
            "Create a plan item",
            (
                "No items are currently assigned to this date."
                if empty
                else "All items currently assigned to this date are completed."
            ),
            PlanInsightAction.CREATE,
            None,
            evidence(day, relation, summary, now),
        )
    if relation == PlanDateRelation.CURRENT:
        selected = min(
            active,
            key=lambda item: recommendation_key(item, now, distinguish_ongoing=True),
        )
        state = timing_state(selected, now)
        suffix = "_priority" if selected.intent.is_priority else ""
        code = PlanInsightCode(f"current_{state}{suffix}")
        reason = {
            PlanTimingState.ONGOING: "This incomplete item is within its scheduled interval.",
            PlanTimingState.PAST: "This item is still incomplete and its scheduled time has passed.",
            PlanTimingState.UPCOMING: "This incomplete item has an upcoming scheduled start.",
            PlanTimingState.UNSCHEDULED: "This incomplete item is assigned to today without a start time.",
        }[state]
        if selected.intent.is_priority:
            reason = "This is an explicit priority. " + reason
    else:
        selected = min(active, key=assigned_date_key)
        priority = selected.intent.is_priority
        suffix = "_priority" if priority else ""
        if relation == PlanDateRelation.PAST:
            code = PlanInsightCode(f"past_remaining{suffix}")
            reason = (
                "This priority is still incomplete."
                if priority
                else "This item assigned to this date is still incomplete."
            )
        else:
            scheduled = selected.intent.scheduled_start_at is not None
            code = PlanInsightCode(
                f"future_{'scheduled' if scheduled else 'unscheduled'}{suffix}"
            )
            reason = (
                (
                    "This is the earliest scheduled incomplete priority assigned to this date."
                    if priority
                    else "This is the earliest scheduled incomplete item assigned to this date."
                )
                if scheduled
                else (
                    "This incomplete priority is assigned to this date without a start time."
                    if priority
                    else "This incomplete item is assigned to this date without a start time."
                )
            )
    return PlanInsight(
        PlanInsightState.AVAILABLE,
        code,
        "View this plan item",
        reason,
        PlanInsightAction.VIEW,
        selected.id,
        evidence(day, relation, summary, now, selected),
    )
