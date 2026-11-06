"""Account-owned Inbox facts and deterministic producer/time rules."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
import base64
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.domain.jobs import TransientJobFailure
from app.domain.planning import PlanItem, utc

PLAN_EVENT = "plan.item_starting_soon"
TODAY_EVENT = "today.items_remaining"
CLEANUP_TYPE = "maintenance.notifications.cleanup"
SOURCES = frozenset({"all", "today", "plan"})
RETENTION = timedelta(days=90)


class InboxError(RuntimeError):
    def __init__(self, message: str | None = None):
        RuntimeError.__init__(self, "Notification operation could not be completed")


class InboxValidationError(InboxError):
    pass


class InboxNotFoundError(InboxError):
    pass


class InboxUnavailableError(InboxError, TransientJobFailure):
    pass


def zone(value: str) -> ZoneInfo:
    try:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 128
            or value.startswith(("posix/", "right/"))
        ):
            raise ValueError()
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError, TypeError):
        raise InboxValidationError() from None


def source_filter(value: str) -> str:
    if not isinstance(value, str) or value not in SOURCES:
        raise InboxValidationError()
    return value


def digest(*parts) -> str:
    return sha256(
        json.dumps(parts, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def local_evaluation(day: date, timezone: str) -> datetime | None:
    tz = zone(timezone)
    wall = datetime.combine(day, time(18), tzinfo=tz)
    instant = wall.astimezone(UTC)
    return (
        instant
        if instant.astimezone(tz).replace(tzinfo=None) == wall.replace(tzinfo=None)
        else None
    )


def next_evaluation(
    now: datetime, timezone: str, after_day: date | None = None
) -> tuple[date, datetime]:
    now = utc(now)
    day = now.astimezone(zone(timezone)).date()
    if after_day is not None:
        day = max(day, after_day + timedelta(days=1))
    for _ in range(4):
        instant = local_evaluation(day, timezone)
        if instant is not None and (after_day is not None or instant >= now):
            return day, instant
        day += timedelta(days=1)
    raise InboxValidationError()


@dataclass(frozen=True, slots=True)
class AccountTimezone:
    user_account_id: UUID = field(repr=False)
    timezone: str
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class Notification:
    id: UUID
    user_account_id: UUID = field(repr=False)
    source: str
    category: str
    event_type: str
    title: str = field(repr=False)
    message: str = field(repr=False)
    created_at: datetime
    expires_at: datetime
    read_at: datetime | None
    plan_item_id: UUID | None = field(repr=False)
    selected_date: date = field(repr=False)
    source_timezone: str
    occurrence_digest: str = field(repr=False)
    updated_at: datetime
    version: int


def plan_snapshot(item: PlanItem, now: datetime) -> Notification:
    return Notification(
        uuid4(),
        item.user_account_id,
        "plan",
        "reminder",
        PLAN_EVENT,
        "Starting soon",
        f'"{item.intent.title}" starts in 15 minutes.',
        now,
        now + RETENTION,
        None,
        item.id,
        item.intent.scheduled_for_date,
        item.intent.timezone,
        digest(
            item.user_account_id,
            PLAN_EVENT,
            item.id,
            utc(item.intent.scheduled_start_at),
        ),
        now,
        1,
    )


def today_snapshot(
    owner: UUID, preference: AccountTimezone, day: date, count: int, now: datetime
) -> Notification:
    return Notification(
        uuid4(),
        owner,
        "today",
        "attention",
        TODAY_EVENT,
        "Today’s plan",
        f'You have {count} item{"" if count == 1 else "s"} remaining today.',
        now,
        now + RETENTION,
        None,
        None,
        day,
        preference.timezone,
        digest(owner, TODAY_EVENT, day, preference.version),
        now,
        1,
    )


def cursor_encode(owner: UUID, source: str, timezone: str, row: Notification) -> str:
    value = [digest(owner, source, timezone), row.created_at.isoformat(), str(row.id)]
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def cursor_decode(
    value: str, owner: UUID, source: str, timezone: str
) -> tuple[datetime, UUID]:
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 1024:
            raise ValueError()
        data = json.loads(
            base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
        )
        if (
            not isinstance(data, list)
            or len(data) != 3
            or data[0] != digest(owner, source, timezone)
            or not isinstance(data[1], str)
            or not isinstance(data[2], str)
        ):
            raise ValueError()
        instant, identifier = utc(datetime.fromisoformat(data[1])), UUID(data[2])
        if str(identifier) != data[2]:
            raise ValueError()
        return instant, identifier
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
        raise InboxValidationError() from None
