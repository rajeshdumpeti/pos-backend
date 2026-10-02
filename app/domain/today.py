"""Pure deterministic Today projection of domain-owned planning facts."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from uuid import UUID

from app.domain.onboarding import PriorityLifeArea, GOAL_FIELDS
from app.domain.planning import PlanItem, timezone_info, utc


class SectionState(StrEnum):
    AVAILABLE = "available"
    EMPTY = "empty"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"
    STALE = "stale"
    ERROR = "error"


class DailyStatusCode(StrEnum):
    GETTING_STARTED = "getting_started"
    COMPLETE = "complete"
    NEEDS_ATTENTION = "needs_attention"
    ON_TRACK = "on_track"


class RecommendationState(StrEnum):
    POPULATED = "populated"
    NEW_USER = "new_user"
    INSUFFICIENT_DATA = "insufficient_data"


class RecommendationCode(StrEnum):
    PRIORITY_PAST_START = "priority_past_start"
    PAST_START = "past_start"
    PRIORITY_UPCOMING = "priority_upcoming"
    UPCOMING = "upcoming"
    PRIORITY_UNSCHEDULED = "priority_unscheduled"
    UNSCHEDULED = "unscheduled"
    CREATE_FIRST = "create_first_plan_item"
    ALL_COMPLETE = "all_plan_items_complete"


class ActionIdentifier(StrEnum):
    CREATE = "plan_item.create"
    VIEW = "plan_item.view"
    UPDATE = "plan_item.update"
    COMPLETE = "plan_item.complete"


class UpNextLabel(StrEnum):
    NOW = "NOW"
    NEXT = "NEXT"
    LATER = "LATER"


class ScoreState(StrEnum):
    AVAILABLE = "available"
    NO_SCORE = "no_score"


class DomainType(StrEnum):
    PLAN_ITEM = "plan_item"


class CompletionState(StrEnum):
    INCOMPLETE = "incomplete"
    COMPLETED = "completed"


class GoalIdentifier(StrEnum):
    TARGET_WEIGHT = "target_weight"
    PROTEIN = "daily_protein_grams"
    WATER = "daily_water"
    CALORIES = "daily_calories_kcal"
    STEPS = "daily_steps"
    WORKOUTS = "workouts_per_week"
    FOCUS = "focus_minutes_per_day"
    SLEEP = "target_sleep_minutes"
    BUDGET = "budget_target"


@dataclass(frozen=True)
class DailyStatus:
    state: SectionState
    code: DailyStatusCode
    eligible: int
    completed: int
    remaining: int
    overdue: int


@dataclass(frozen=True)
class RecommendationEvidence:
    eligible: int
    remaining: int
    is_priority: bool | None
    scheduled_start_at: datetime | None


@dataclass(frozen=True)
class Recommendation:
    state: RecommendationState
    code: RecommendationCode
    domain: DomainType
    recommendation: str
    reason: str
    action: ActionIdentifier
    plan_item_id: UUID | None
    evidence: RecommendationEvidence


@dataclass(frozen=True)
class ScoreContribution:
    item_id: UUID
    domain: DomainType
    eligibility_reason: str
    weight: int
    earned_weight: int
    completion_state: CompletionState


@dataclass(frozen=True)
class Score:
    state: ScoreState
    percentage: int | None
    numerator: int
    denominator: int
    breakdown: tuple[ScoreContribution, ...]


@dataclass(frozen=True)
class ItemSummary:
    plan_item_id: UUID
    title: str
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    scheduled_for_date: date
    timezone: str
    domain: DomainType
    state: CompletionState
    action: ActionIdentifier
    completed_at: datetime | None


@dataclass(frozen=True)
class UpNextItem:
    item: ItemSummary
    label: UpNextLabel


@dataclass(frozen=True)
class ItemSection:
    state: SectionState
    items: tuple[ItemSummary, ...]
    action: ActionIdentifier


@dataclass(frozen=True)
class UpNextSection:
    state: SectionState
    items: tuple[UpNextItem, ...]
    action: ActionIdentifier


@dataclass(frozen=True)
class Progress:
    state: SectionState
    reason: str
    metrics: tuple[()] = ()


@dataclass(frozen=True)
class Capabilities:
    state: SectionState
    actions: tuple[ActionIdentifier, ...]
    priority_life_areas: tuple[PriorityLifeArea, ...]
    configured_goals: tuple[GoalIdentifier, ...]
    measurement_sources: tuple[()] = ()


@dataclass(frozen=True)
class Freshness:
    state: str
    as_of: datetime


@dataclass(frozen=True)
class Today:
    contract_version: str
    generated_at: datetime
    timezone: str
    local_date: date
    freshness: Freshness
    daily_status: DailyStatus
    next_best_action: Recommendation
    score: Score
    progress: Progress
    up_next: UpNextSection
    top_priorities: ItemSection
    wins: ItemSection
    capabilities: Capabilities


def summary(item: PlanItem) -> ItemSummary:
    return ItemSummary(
        item.id,
        item.intent.title,
        item.intent.scheduled_start_at,
        item.intent.scheduled_end_at,
        item.intent.scheduled_for_date,
        item.intent.timezone,
        DomainType.PLAN_ITEM,
        CompletionState.COMPLETED if item.completed_at else CompletionState.INCOMPLETE,
        ActionIdentifier.VIEW,
        item.completed_at,
    )


def aggregate(
    account_id: UUID,
    now: datetime,
    timezone: str,
    items: list[PlanItem],
    completions: list[PlanItem],
    priorities: tuple[str, ...],
    goals: dict,
) -> Today:
    now = utc(now)
    local_date = now.astimezone(timezone_info(timezone)).date()
    eligible = sorted(
        {
            item.id: item
            for item in items
            if item.user_account_id == account_id
            and item.intent.scheduled_for_date == local_date
        }.values(),
        key=lambda item: str(item.id),
    )
    active = [item for item in eligible if item.completed_at is None]
    overdue = [
        item
        for item in active
        if item.intent.scheduled_start_at is not None
        and item.intent.scheduled_start_at <= now
    ]
    total, completed = len(eligible), len(eligible) - len(active)
    code = (
        DailyStatusCode.GETTING_STARTED
        if not total
        else (
            DailyStatusCode.COMPLETE
            if not active
            else (
                DailyStatusCode.NEEDS_ATTENTION
                if overdue
                else (
                    DailyStatusCode.ON_TRACK
                    if completed
                    else DailyStatusCode.GETTING_STARTED
                )
            )
        )
    )

    def recommendation_key(item: PlanItem):
        start = item.intent.scheduled_start_at
        priority = item.intent.is_priority
        tier = (
            (0 if priority else 1)
            if start is not None and start <= now
            else (2 if priority else 3) if start is not None else (4 if priority else 5)
        )
        return tier, start or item.created_at, str(item.id)

    selected = min(active, key=recommendation_key) if active else None
    if selected:
        tier = recommendation_key(selected)[0]
        rec_code = tuple(RecommendationCode)[:6][tier]
        recommendation = Recommendation(
            RecommendationState.POPULATED,
            rec_code,
            DomainType.PLAN_ITEM,
            "View this plan item",
            (
                "An explicit priority has reached its scheduled start",
                "An incomplete item has reached its scheduled start",
                "This is the next scheduled explicit priority",
                "This is the next scheduled item",
                "This explicit priority is assigned to today without a start time",
                "This item is assigned to today without a start time",
            )[tier],
            ActionIdentifier.VIEW,
            selected.id,
            RecommendationEvidence(
                total,
                len(active),
                selected.intent.is_priority,
                selected.intent.scheduled_start_at,
            ),
        )
    else:
        recommendation = Recommendation(
            (
                RecommendationState.NEW_USER
                if not total
                else RecommendationState.INSUFFICIENT_DATA
            ),
            (
                RecommendationCode.CREATE_FIRST
                if not total
                else RecommendationCode.ALL_COMPLETE
            ),
            DomainType.PLAN_ITEM,
            "Create a plan item",
            "No incomplete eligible plan items",
            ActionIdentifier.CREATE,
            None,
            RecommendationEvidence(total, 0, None, None),
        )
    percentage = (
        None
        if not total
        else int(
            (Decimal(completed) * 100 / Decimal(total)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    )
    score = Score(
        ScoreState.AVAILABLE if total else ScoreState.NO_SCORE,
        percentage,
        completed,
        total,
        tuple(
            ScoreContribution(
                item.id,
                DomainType.PLAN_ITEM,
                "assigned_current_local_date",
                1,
                int(item.completed_at is not None),
                (
                    CompletionState.COMPLETED
                    if item.completed_at
                    else CompletionState.INCOMPLETE
                ),
            )
            for item in eligible
        ),
    )
    scheduled = sorted(
        (item for item in active if item.intent.scheduled_start_at is not None),
        key=lambda item: (item.intent.scheduled_start_at, str(item.id)),
    )[:4]
    next_seen = False
    upcoming = []
    for item in scheduled:
        if item.intent.scheduled_start_at <= now:
            label = UpNextLabel.NOW
        else:
            label = UpNextLabel.LATER if next_seen else UpNextLabel.NEXT
            next_seen = True
        upcoming.append(UpNextItem(summary(item), label))
    top = sorted(
        (item for item in active if item.intent.is_priority), key=recommendation_key
    )[:3]
    wins = sorted(
        {
            item.id: item
            for item in completions
            if item.user_account_id == account_id
            and item.completed_at is not None
            and item.completed_at.astimezone(timezone_info(timezone)).date()
            == local_date
        }.values(),
        key=lambda item: (-item.completed_at.timestamp(), str(item.id)),
    )[:3]
    return Today(
        "1.0",
        now,
        timezone,
        local_date,
        Freshness("current", now),
        DailyStatus(
            SectionState.AVAILABLE, code, total, completed, len(active), len(overdue)
        ),
        recommendation,
        score,
        Progress(SectionState.UNAVAILABLE, "no_supported_measurement_sources"),
        UpNextSection(
            SectionState.AVAILABLE if upcoming else SectionState.EMPTY,
            tuple(upcoming),
            ActionIdentifier.CREATE,
        ),
        ItemSection(
            SectionState.AVAILABLE if top else SectionState.EMPTY,
            tuple(map(summary, top)),
            ActionIdentifier.CREATE,
        ),
        ItemSection(
            SectionState.AVAILABLE if wins else SectionState.EMPTY,
            tuple(map(summary, wins)),
            ActionIdentifier.CREATE,
        ),
        Capabilities(
            SectionState.PARTIAL,
            tuple(ActionIdentifier),
            tuple(PriorityLifeArea(p) for p in priorities),
            tuple(
                GoalIdentifier(g)
                for g in GOAL_FIELDS
                if g in goals and goals[g] is not None
            ),
        ),
    )
