"""Closed reference-only payloads; rendered copy is never queued."""

from datetime import date, datetime
from uuid import UUID
from app.domain.jobs import JobValidationError, bounded, utc
from app.domain.notifications import PLAN_EVENT, TODAY_EVENT, CLEANUP_TYPE, zone


def notification_payload(kind: str, payload: dict) -> dict:
    try:
        if not isinstance(payload, dict):
            raise ValueError()
        if kind == CLEANUP_TYPE and set(payload) == {"limit"}:
            return {"limit": bounded(payload["limit"])}
        if kind == PLAN_EVENT and set(payload) == {
            "plan_item_id",
            "start_at",
            "version",
        }:
            identifier = UUID(payload["plan_item_id"])
            start = utc(datetime.fromisoformat(payload["start_at"]))
            if type(payload["version"]) is not int or payload["version"] < 1:
                raise ValueError()
            return {
                "plan_item_id": str(identifier),
                "start_at": start.isoformat(),
                "version": payload["version"],
            }
        if kind == TODAY_EVENT and set(payload) == {"date", "timezone", "version"}:
            day = date.fromisoformat(payload["date"])
            if (
                day.isoformat() != payload["date"]
                or type(payload["version"]) is not int
                or payload["version"] < 1
            ):
                raise ValueError()
            zone(payload["timezone"])
            return {
                "date": day.isoformat(),
                "timezone": payload["timezone"],
                "version": payload["version"],
            }
        raise ValueError()
    except Exception:
        raise JobValidationError() from None
