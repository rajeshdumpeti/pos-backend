"""Inbox use cases, producer policy and shared-transaction integration ports."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID
from app.application.jobs import JobRepository, JobUnitOfWork
from app.application.planning import PlanRepository, PlanUnitOfWork
from app.application.account_eligibility import AccountUnavailableError
from app.domain.jobs import Job, new_job, payload_for
from app.domain.planning import PlanItem, utc
from app.domain.notifications import (
    AccountTimezone,
    Notification,
    InboxNotFoundError,
    InboxValidationError,
    PLAN_EVENT,
    TODAY_EVENT,
    CLEANUP_TYPE,
    cursor_encode,
    cursor_decode,
    digest,
    zone,
    source_filter,
    local_evaluation,
    next_evaluation,
    plan_snapshot,
    today_snapshot,
)


class InboxRepository(Protocol):
    def lock_account(self, owner: UUID) -> bool: ...
    def preference(self, owner: UUID) -> AccountTimezone | None: ...
    def save_preference(self, preference: AccountTimezone) -> None: ...
    def cancel_jobs(
        self,
        owner: UUID,
        kind: str,
        now: datetime,
        item_id: UUID | None = None,
        superseded: bool = False,
    ) -> None: ...
    def insert(self, row: Notification) -> bool: ...
    def get(
        self, owner: UUID, identifier: UUID, now: datetime
    ) -> Notification | None: ...
    def list(
        self,
        owner: UUID,
        source: str,
        now: datetime,
        boundary: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Notification]: ...
    def count(self, owner: UUID, source: str, now: datetime) -> int: ...
    def mark(
        self, owner: UUID, identifier: UUID, now: datetime
    ) -> Notification | None: ...
    def mark_all(self, owner: UUID, source: str, now: datetime) -> int: ...
    def cleanup(self, now: datetime, limit: int) -> int: ...


class InboxUnitOfWork(JobUnitOfWork, Protocol):
    @property
    def inbox(self) -> InboxRepository: ...
    @property
    def plans(self) -> PlanRepository: ...
    @property
    def jobs(self) -> JobRepository: ...


def account(unit: InboxUnitOfWork, owner: UUID) -> None:
    if not unit.inbox.lock_account(owner):
        raise AccountUnavailableError("Account is unavailable")


def stage_daily(
    unit: InboxUnitOfWork,
    preference: AccountTimezone,
    now: datetime,
    after: date | None = None,
) -> None:
    day, instant = next_evaluation(now, preference.timezone, after)
    payload = {
        "date": day.isoformat(),
        "timezone": preference.timezone,
        "version": preference.version,
    }
    unit.jobs.schedule(
        new_job(
            TODAY_EVENT,
            preference.user_account_id,
            payload,
            instant,
            digest(TODAY_EVENT, day, preference.version),
            now,
        )
    )


class PlanNotificationIntegration:
    def prepare(self, unit: PlanUnitOfWork, owner: UUID) -> None:
        account(cast(InboxUnitOfWork, unit), owner)

    def changed(
        self,
        unit: PlanUnitOfWork,
        previous: PlanItem | None,
        item: PlanItem,
        now: datetime,
    ) -> None:
        unit = cast(InboxUnitOfWork, unit)
        unit.inbox.cancel_jobs(
            item.user_account_id, PLAN_EVENT, now, item.id, item.completed_at is None
        )
        start = item.intent.scheduled_start_at
        if item.completed_at is not None or start is None or start <= now:
            return
        payload = {
            "plan_item_id": str(item.id),
            "start_at": start.isoformat(),
            "version": item.version,
        }
        unit.jobs.schedule(
            new_job(
                PLAN_EVENT,
                item.user_account_id,
                payload,
                max(now, start - timedelta(minutes=15)),
                digest(PLAN_EVENT, item.id, start, item.version),
                now,
            )
        )


class NotificationService:
    def __init__(
        self,
        factory: Callable[[], InboxUnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None
    ):
        self.factory = factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def put_timezone(self, owner: UUID, timezone: str) -> AccountTimezone:
        zone(timezone)
        now = utc(self.clock())
        with self.factory() as unit:
            account(unit, owner)
            old = unit.inbox.preference(owner)
            if old is not None and old.timezone == timezone:
                return old
            preference = AccountTimezone(
                owner, timezone, now, 1 if old is None else old.version + 1
            )
            unit.inbox.save_preference(preference)
            unit.inbox.cancel_jobs(owner, TODAY_EVENT, now, superseded=True)
            stage_daily(unit, preference, now)
            unit.commit()
            return preference

    def list(
        self,
        owner: UUID,
        source: str,
        timezone: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        source_filter(source)
        zone(timezone)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InboxValidationError()
        now = utc(self.clock())
        boundary = (
            cursor_decode(cursor, owner, source, timezone)
            if cursor is not None
            else None
        )
        with self.factory() as unit:
            account(unit, owner)
            if boundary:
                row = unit.inbox.get(owner, boundary[1], now)
                if (
                    row is None
                    or row.created_at != boundary[0]
                    or (source != "all" and row.source != source)
                ):
                    raise InboxValidationError()
            rows = unit.inbox.list(owner, source, now, boundary, limit + 1)
            more = len(rows) > limit
            rows = rows[:limit]
            return {
                "contract_version": "1.0",
                "generated_at": now,
                "timezone": timezone,
                "filter": source,
                "unread_count": unit.inbox.count(owner, source, now),
                "items": [
                    self._present(unit, owner, row, timezone, now) for row in rows
                ],
                "next_cursor": (
                    cursor_encode(owner, source, timezone, rows[-1]) if more else None
                ),
                "has_more": more,
            }

    def count(self, owner: UUID, source: str = "all") -> dict:
        source_filter(source)
        now = utc(self.clock())
        with self.factory() as unit:
            account(unit, owner)
            return {
                "contract_version": "1.0",
                "source": source,
                "generated_at": now,
                "unread_count": unit.inbox.count(owner, source, now),
            }

    def mark(self, owner: UUID, identifier: UUID) -> dict:
        now = utc(self.clock())
        with self.factory() as unit:
            account(unit, owner)
            row = unit.inbox.mark(owner, identifier, now)
            if row is None:
                raise InboxNotFoundError()
            count = unit.inbox.count(owner, "all", now)
            unit.commit()
            return {
                "contract_version": "1.0",
                "notification_id": row.id,
                "is_read": True,
                "read_at": row.read_at,
                "unread_count": count,
                "source": "all",
            }

    def mark_all(self, owner: UUID, source: str) -> dict:
        source_filter(source)
        now = utc(self.clock())
        with self.factory() as unit:
            account(unit, owner)
            count = unit.inbox.mark_all(owner, source, now)
            remaining = unit.inbox.count(owner, source, now)
            unit.commit()
            return {
                "contract_version": "1.0",
                "source": source,
                "marked_read_count": count,
                "unread_count": remaining,
                "read_at": now,
            }

    @staticmethod
    def _present(
        unit: InboxUnitOfWork,
        owner: UUID,
        row: Notification,
        timezone: str,
        now: datetime,
    ) -> dict:
        navigation = {
            "state": "available",
            "action": "today.view",
            "selected_date": row.selected_date,
            "plan_item_id": None,
        }
        if row.source == "plan":
            item = unit.plans.get(owner, row.plan_item_id) if row.plan_item_id else None
            navigation = (
                {
                    "state": "unavailable",
                    "action": None,
                    "selected_date": None,
                    "plan_item_id": None,
                }
                if item is None
                else {
                    "state": "available",
                    "action": "plan_item.view",
                    "selected_date": item.intent.scheduled_for_date,
                    "plan_item_id": item.id,
                }
            )
        return {
            "notification_id": row.id,
            "source": row.source,
            "category": row.category,
            "event_type": row.event_type,
            "title": row.title,
            "message": row.message,
            "created_at": row.created_at,
            "group": (
                "today"
                if row.created_at.astimezone(zone(timezone)).date()
                == now.astimezone(zone(timezone)).date()
                else "earlier"
            ),
            "is_read": row.read_at is not None,
            "read_at": row.read_at,
            "source_timezone": row.source_timezone,
            "navigation": navigation,
        }

    def plan_reminder(self, job: Job) -> None:
        payload = payload_for(PLAN_EVENT, job.payload)
        now = utc(self.clock())
        with self.factory() as unit:
            if job.user_account_id is None or not unit.inbox.lock_account(
                job.user_account_id
            ):
                return
            item = unit.plans.get(job.user_account_id, UUID(payload["plan_item_id"]))
            if (
                item is None
                or item.completed_at is not None
                or item.version != payload["version"]
                or item.intent.scheduled_start_at is None
                or item.intent.scheduled_start_at
                != datetime.fromisoformat(payload["start_at"])
                or item.intent.scheduled_start_at <= now
                or now < item.intent.scheduled_start_at - timedelta(minutes=15)
            ):
                return
            unit.inbox.insert(plan_snapshot(item, now))
            unit.commit()

    def today_reminder(self, job: Job) -> None:
        payload = payload_for(TODAY_EVENT, job.payload)
        now = utc(self.clock())
        with self.factory() as unit:
            if job.user_account_id is None or not unit.inbox.lock_account(
                job.user_account_id
            ):
                return
            preference = unit.inbox.preference(job.user_account_id)
            if (
                preference is None
                or preference.timezone != payload["timezone"]
                or preference.version != payload["version"]
            ):
                return
            day = date.fromisoformat(payload["date"])
            expected = local_evaluation(day, preference.timezone)
            if expected is None or now < expected:
                return
            if now.astimezone(zone(preference.timezone)).date() == day:
                count = sum(
                    item.completed_at is None
                    for item in unit.plans.selected_date_facts(job.user_account_id, day)
                )
                if count:
                    unit.inbox.insert(
                        today_snapshot(job.user_account_id, preference, day, count, now)
                    )
            stage_daily(unit, preference, now, day)
            unit.commit()

    def cleanup(self, job: Job) -> None:
        payload = payload_for(CLEANUP_TYPE, job.payload)
        with self.factory() as unit:
            unit.inbox.cleanup(utc(self.clock()), payload["limit"])
            unit.commit()
