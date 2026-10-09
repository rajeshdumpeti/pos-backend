"""Authoritative selected-date orchestration and bounded opaque keyset pages."""

import base64
import binascii
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
from uuid import UUID

from app.application.planning import PlanService
from app.domain.planning import PlanItem, PlanValidationError, timezone_info, utc
from app.domain.plan_read import (
    InsightComputationError,
    PlanDateRelation,
    PlanInsight,
    PlanInsightCode,
    PlanInsightState,
    PlanSummary,
    PlanItemSection,
    PlanTimingState,
    PlanInsightAction,
    build_insight,
    evidence,
    ordering_key,
    section,
    summarize,
    timing_state,
)


def _context(account_id: UUID, day: date, timezone: str) -> str:
    # Public context fingerprint, not a signature or authorization credential.
    return sha256(
        json.dumps([str(account_id), day.isoformat(), timezone, 1]).encode()
    ).hexdigest()


def _encode(context: str, key: tuple[int, int, int, str]) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                {"v": 1, "context": context, "key": key}, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def _decode(cursor: str, context: str) -> tuple[int, int, int, str]:
    try:
        if not isinstance(cursor, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,1024}", cursor
        ):
            raise ValueError
        raw = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
        )
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"v", "context", "key"}:
            raise ValueError
        key = value["key"]
        if (
            type(value["v"]) is not int
            or value["v"] != 1
            or value["context"] != context
        ):
            raise ValueError
        if not isinstance(key, list) or len(key) != 4:
            raise ValueError
        if any(type(part) is not int for part in key[:3]):
            raise ValueError
        if (
            key[0] not in (0, 1, 2)
            or key[1] not in (0, 1)
            or (key[0] != 1 and key[1] != 0)
        ):
            raise ValueError
        if (
            abs(key[2]) > 10**18
            or not isinstance(key[3], str)
            or str(UUID(key[3])) != key[3]
        ):
            raise ValueError
        result = tuple(key)
        # Canonical encoding also rejects duplicate keys, whitespace and extra syntax.
        if _encode(context, result) != cursor:
            raise ValueError
        return result
    except (ValueError, TypeError, binascii.Error, UnicodeError, RecursionError):
        raise PlanValidationError("Invalid cursor") from None


@dataclass(frozen=True)
class PlanFreshness:
    state: str
    as_of: datetime


@dataclass(frozen=True)
class PlanPage:
    limit: int
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class PlanDayItem:
    id: UUID
    title: str
    scheduled_for_date: date
    timezone: str
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    is_priority: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    section: PlanItemSection
    timing_state: PlanTimingState
    action: PlanInsightAction


@dataclass(frozen=True)
class PlanSelectedDate:
    contract_version: str
    generated_at: datetime
    timezone: str
    selected_date: date
    date_relation: PlanDateRelation
    freshness: PlanFreshness
    summary: PlanSummary
    insight: PlanInsight
    items: tuple[PlanDayItem, ...]
    page: PlanPage


class PlanSelectedDateService:
    def __init__(
        self,
        plans: PlanService,
        *,
        insight_builder: Callable[..., PlanInsight] = build_insight
    ) -> None:
        self.plans = plans
        self.insight_builder = insight_builder

    def get(
        self,
        account_id: UUID,
        selected_date: date,
        timezone: str,
        request_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PlanSelectedDate:
        zone = timezone_info(timezone)
        if (
            type(selected_date) is not date
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise PlanValidationError("Invalid selected-date query")
        context = _context(account_id, selected_date, timezone)
        anchor = _decode(cursor, context) if cursor is not None else None
        now = utc(self.plans.clock())
        current_date = now.astimezone(zone).date()
        relation = (
            PlanDateRelation.PAST
            if selected_date < current_date
            else (
                PlanDateRelation.FUTURE
                if selected_date > current_date
                else PlanDateRelation.CURRENT
            )
        )
        with self.plans.factory(request_id) as unit:
            facts = unit.plans.selected_date_facts(account_id, selected_date)
        # Defense in depth at the projection boundary, including UUID deduplication.
        items = sorted(
            {
                item.id: item
                for item in facts
                if item.user_account_id == account_id
                and item.intent.scheduled_for_date == selected_date
            }.values(),
            key=ordering_key,
        )
        summary = summarize(items)
        remaining = [
            item for item in items if anchor is None or ordering_key(item) > anchor
        ]
        page_items = remaining[:limit]
        has_more = len(remaining) > limit
        next_cursor = (
            _encode(context, ordering_key(page_items[-1])) if has_more else None
        )
        projected_items = tuple(self._item(item, now) for item in page_items)
        failed = False
        try:
            insight = self.insight_builder(selected_date, relation, items, summary, now)
        except InsightComputationError:
            failed = True
            insight = PlanInsight(
                PlanInsightState.UNAVAILABLE,
                PlanInsightCode.INSIGHT_UNAVAILABLE,
                "Insight is unavailable",
                "The selected-date items remain available.",
                None,
                None,
                evidence(selected_date, relation, summary, now),
            )
        result = PlanSelectedDate(
            "1.0",
            now,
            timezone,
            selected_date,
            relation,
            PlanFreshness("current", now),
            summary,
            insight,
            projected_items,
            PlanPage(limit, next_cursor, has_more),
        )
        if self.plans.diagnostic:
            self.plans.diagnostic.emit(
                "plan_insight_unavailable" if failed else "plan_selected_date_read",
                request_id,
                account_id,
                None,
                0,
            )
        return result

    @staticmethod
    def _item(item: PlanItem, now: datetime) -> PlanDayItem:
        return PlanDayItem(
            **{
                "id": item.id,
                **asdict(item.intent),
                "completed_at": item.completed_at,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "version": item.version,
                "section": section(item),
                "timing_state": timing_state(item, now),
                "action": PlanInsightAction.VIEW,
            }
        )
