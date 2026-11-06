"""Shared pure schedule/priority selection, including Today's frozen policy."""

from datetime import datetime

from app.domain.planning import PlanItem


def recommendation_key(
    item: PlanItem, now: datetime, *, distinguish_ongoing: bool = False
) -> tuple[int, datetime, str]:
    start, end = item.intent.scheduled_start_at, item.intent.scheduled_end_at
    priority_rank = int(not item.intent.is_priority)
    if start is None:
        tier = 6 if distinguish_ongoing else 4
    elif start > now:
        tier = 4 if distinguish_ongoing else 2
    elif distinguish_ongoing and end is not None and now < end:
        tier = 0
    else:
        tier = 2 if distinguish_ongoing else 0
    return tier + priority_rank, start or item.created_at, str(item.id)


def assigned_date_key(item: PlanItem) -> tuple[int, datetime, str]:
    """Past/future insight: scheduled then unscheduled, priority within each."""
    start = item.intent.scheduled_start_at
    return (
        (0 if start is not None else 2) + int(not item.intent.is_priority),
        start or item.created_at,
        str(item.id),
    )
