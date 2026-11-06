"""Secret-safe database exception and diagnostic formatting boundaries."""

import logging
import re
import traceback
from typing import Never, TypeVar


DatabaseError = TypeVar("DatabaseError", bound=RuntimeError)

_DATABASE_URL = re.compile(
    r"(?P<scheme>postgres(?:ql)?(?:\+[a-z0-9_]+)?://)(?P<userinfo>[^\s/]+)@",
    re.IGNORECASE,
)
_PASSWORD_FIELD = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd)[\"']?\s*[=:]\s*[\"']?)"
    r"(?P<value>[^\"'\s,;}]+)",
    re.IGNORECASE,
)


class RedactedDatabaseCause(RuntimeError):
    """Public-safe chain node standing in for a private driver exception."""


def redact_database_diagnostic(value: object) -> str:
    """Remove database URL userinfo and password fields from diagnostic text."""
    rendered = str(value)
    rendered = _DATABASE_URL.sub(r"\g<scheme>***:***@", rendered)
    return _PASSWORD_FIELD.sub(r"\g<prefix>***", rendered)


def raise_safe_database_error(
    error_type: type[DatabaseError],
    message: str,
    internal_cause: BaseException,
) -> Never:
    """Raise a safe exception while retaining the original cause privately.

    The explicit public cause prevents Python's default traceback formatter from
    rendering the raw exception context. Incident tooling may inspect the
    private cause only inside a controlled, non-rendering diagnostic boundary.
    """
    error = error_type(message)
    error._internal_cause = internal_cause  # type: ignore[attr-defined]
    public_cause = RedactedDatabaseCause(
        f"{type(internal_cause).__name__} details redacted"
    )
    raise error from public_cause


def format_safe_database_traceback(error: BaseException) -> str:
    """Format a traceback defensively for project-owned diagnostics."""
    return redact_database_diagnostic("".join(traceback.format_exception(error)))


def log_database_failure(
    logger: logging.Logger,
    *,
    operation: str,
    category: str,
    request_id: str | None = None,
) -> None:
    """Log only allow-listed database failure fields, never exception text."""
    logger.error(
        "database_operation_failed",
        extra={
            "operation": operation,
            "error_category": category,
            "request_id": request_id,
            "outcome": "failed",
        },
    )
