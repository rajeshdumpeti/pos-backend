"""Framework-independent, bounded background-job intent and failure policy."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

JOB_TYPE = "maintenance.jobs.cleanup"
STATES = frozenset(
    {"pending", "running", "succeeded", "retryable", "dead", "cancelled", "superseded"}
)
TERMINAL = frozenset({"succeeded", "dead", "cancelled", "superseded"})
CATEGORIES = frozenset(
    {
        "transient",
        "dependency_unavailable",
        "permanent",
        "invalid_payload",
        "unsupported_type",
        "handler_failure",
        "lease_expired",
    }
)


class JobValidationError(ValueError):
    def __init__(self) -> None:
        super().__init__("Background job input is invalid")


class JobConflictError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Background job intent conflicts with existing state")


class TransientJobFailure(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Background job dependency is temporarily unavailable")


class PermanentJobFailure(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Background job cannot be completed")


def utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise JobValidationError()
    return value.astimezone(UTC)


def bounded(value: int, maximum: int = 100) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise JobValidationError()
    return value


def payload_for(job_type: str, payload: dict) -> dict:
    if (
        job_type != JOB_TYPE
        or not isinstance(payload, dict)
        or set(payload) != {"cutoff_at", "limit"}
    ):
        raise JobValidationError()
    try:
        if not isinstance(payload["cutoff_at"], str) or len(payload["cutoff_at"]) > 64:
            raise JobValidationError()
        cutoff = utc(datetime.fromisoformat(payload["cutoff_at"]))
        return {"cutoff_at": cutoff.isoformat(), "limit": bounded(payload["limit"])}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise JobValidationError() from None


def backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(300, 30 * 2 ** (bounded(attempt, 5) - 1)))


@dataclass(frozen=True, slots=True)
class Job:
    id: UUID
    job_type: str
    user_account_id: UUID | None = field(repr=False)
    payload: dict = field(repr=False)
    deduplication_digest: str = field(repr=False)
    intent_digest: str = field(repr=False)
    scheduled_at: datetime
    state: str
    attempts: int
    max_attempts: int
    lease_owner: UUID | None
    lease_expires_at: datetime | None
    failure_category: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


def new_job(
    job_type: str,
    account_id: UUID | None,
    payload: dict,
    scheduled_at: datetime,
    key: str,
    now: datetime,
    max_attempts: int = 5,
) -> Job:
    if account_id is not None and not isinstance(account_id, UUID):
        raise JobValidationError()
    if (
        not isinstance(key, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key) is None
    ):
        raise JobValidationError()
    scheduled_at, now = utc(scheduled_at), utc(now)
    payload = payload_for(job_type, payload)
    if datetime.fromisoformat(payload["cutoff_at"]) > scheduled_at:
        raise JobValidationError()
    bounded(max_attempts, 5)
    intent = json.dumps(
        [payload, scheduled_at.isoformat(), max_attempts],
        sort_keys=True,
        separators=(",", ":"),
    )
    scope = json.dumps([str(account_id) if account_id else None, job_type, key])
    return Job(
        uuid4(),
        job_type,
        account_id,
        payload,
        sha256(scope.encode()).hexdigest(),
        sha256(intent.encode()).hexdigest(),
        scheduled_at,
        "pending",
        0,
        max_attempts,
        None,
        None,
        None,
        now,
        now,
        None,
        None,
    )
