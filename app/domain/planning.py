"""Daily planning owns durable intent and completion; Today owns no facts."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PlanError(RuntimeError):
    pass


class PlanValidationError(PlanError):
    pass


class PlanConflictError(PlanError):
    pass


class PlanIdempotencyConflictError(PlanError):
    pass


class PlanNotFoundError(PlanError):
    pass


class PlanUnavailableError(PlanError):
    pass


def timezone_info(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise PlanValidationError("Invalid timezone")
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise PlanValidationError("Invalid timezone") from None


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PlanValidationError("An offset is required")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PlanIntent:
    title: str
    scheduled_for_date: date
    timezone: str
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    is_priority: bool = False

    def normalized(self, *, check_offset: bool = True) -> "PlanIntent":
        if not isinstance(self.title, str):
            raise PlanValidationError("Invalid title")
        title = self.title.strip()
        if not title or len(title) > 200:
            raise PlanValidationError("Invalid title")
        zone = timezone_info(self.timezone)
        if (
            type(self.scheduled_for_date) is not date
            or type(self.is_priority) is not bool
        ):
            raise PlanValidationError("Invalid assignment")
        start, end = self.scheduled_start_at, self.scheduled_end_at
        if end is not None and start is None:
            raise PlanValidationError("End requires start")
        for instant in (start, end):
            if instant is not None:
                absolute = utc(instant)
                local = absolute.astimezone(zone)
                if check_offset and instant.utcoffset() != local.utcoffset():
                    raise PlanValidationError("Offset does not match timezone")
        if (
            start is not None
            and utc(start).astimezone(zone).date() != self.scheduled_for_date
        ):
            raise PlanValidationError("Start does not match assigned date")
        if end is not None and utc(end) <= utc(start):
            raise PlanValidationError("End must follow start")
        return PlanIntent(
            title,
            self.scheduled_for_date,
            self.timezone,
            None if start is None else utc(start),
            None if end is None else utc(end),
            self.is_priority,
        )


@dataclass(frozen=True, slots=True)
class PlanItem:
    id: UUID
    user_account_id: UUID
    intent: PlanIntent
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
