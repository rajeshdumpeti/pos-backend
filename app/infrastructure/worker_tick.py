"""One finite staging worker tick; scheduling is owned by DevOps."""

import argparse
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json
from typing import Protocol

from app.application.email_delivery import DispatchSummary
from app.application.jobs import WorkerSummary
from app.config import ConfigurationError, Settings, load_settings
from app.infrastructure.authentication.email_delivery import email_processor
from app.infrastructure.job_worker import job_processor


MAX_LIMIT = 25
DEFAULT_EMAIL_LIMIT = 25
DEFAULT_JOB_LIMIT = 10


class Processor(Protocol):
    def process_pending(self, limit: int) -> DispatchSummary | WorkerSummary: ...


ProcessorFactory = Callable[[Settings], AbstractContextManager[Processor]]


class Outcome(StrEnum):
    SUCCESS = "success"
    OPERATIONAL_FAILURE = "operational_failure"
    CONFIGURATION_FAILURE = "configuration_failure"
    STARTUP_FAILURE = "startup_failure"


EXIT_CODES = {
    Outcome.SUCCESS: 0,
    Outcome.OPERATIONAL_FAILURE: 1,
    Outcome.CONFIGURATION_FAILURE: 2,
    Outcome.STARTUP_FAILURE: 3,
}


class WorkerTickError(RuntimeError):
    """Public-safe diagnostic with the original exception retained privately."""

    def __init__(self, cause: Exception) -> None:
        super().__init__("Worker processor failed safely")
        self._internal_cause = cause
        self.__cause__ = RuntimeError("Worker processor details redacted")
        self.__suppress_context__ = True


@dataclass(frozen=True)
class ProcessorResult:
    processor: str
    outcome: Outcome
    claimed: int | None = None
    succeeded: int | None = None
    retryable: int | None = None
    dead: int | None = None
    lease_lost: int | None = None
    _error: WorkerTickError | None = field(default=None, repr=False, compare=False)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.outcome]

    def public_summary(self) -> dict[str, str | int | None]:
        # Never serialize a service, exception, dataclass internals or payload.
        return {
            "processor": self.processor,
            "outcome": self.outcome.value,
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "retryable": self.retryable,
            "dead": self.dead,
            "lease_lost": self.lease_lost,
        }


def _counts(summary: DispatchSummary | WorkerSummary) -> dict[str, int]:
    if type(summary) is DispatchSummary:
        values = {
            "claimed": summary.claimed,
            "succeeded": summary.delivered,
            "retryable": summary.retry_scheduled,
            "dead": summary.failed,
        }
    elif type(summary) is WorkerSummary:
        values = asdict(summary)
    else:
        raise RuntimeError("Worker summary is invalid")
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise RuntimeError("Worker summary is invalid")
    return values


def _attempt(
    name: str, factory: ProcessorFactory, settings: Settings, limit: int
) -> ProcessorResult:
    started = False
    counts: dict[str, int] = {}
    try:
        with factory(settings) as processor:
            started = True
            counts = _counts(processor.process_pending(limit))
    except Exception as error:
        outcome = (
            Outcome.OPERATIONAL_FAILURE
            if started
            else (
                Outcome.CONFIGURATION_FAILURE
                if isinstance(error, ConfigurationError)
                else Outcome.STARTUP_FAILURE
            )
        )
        return ProcessorResult(name, outcome, **counts, _error=WorkerTickError(error))
    return ProcessorResult(name, Outcome.SUCCESS, **counts)


def _check_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise ConfigurationError("Worker limits must be integers between 1 and 25")
    return value


def run_tick(
    settings: Settings,
    *,
    email_limit: int = DEFAULT_EMAIL_LIMIT,
    job_limit: int = DEFAULT_JOB_LIMIT,
    email_factory: ProcessorFactory | None = None,
    job_factory: ProcessorFactory | None = None,
) -> tuple[ProcessorResult, ProcessorResult]:
    """Attempt both passes sequentially, with independent resource lifetimes."""
    _check_limit(email_limit)
    _check_limit(job_limit)
    email = _attempt("email", email_factory or email_processor, settings, email_limit)
    jobs = _attempt("jobs", job_factory or job_processor, settings, job_limit)
    return email, jobs


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default error includes the caller's raw arguments.
        raise ConfigurationError("Worker tick arguments are invalid")


def _parse_limit(raw: str) -> int:
    try:
        return _check_limit(int(raw))
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Worker limit must be between 1 and 25"
        ) from None


def main(arguments: Sequence[str] | None = None) -> int:
    parser = _Parser(prog="python -m app.infrastructure.worker_tick")
    parser.add_argument("command", choices=("process-pending",))
    parser.add_argument("--email-limit", type=_parse_limit, default=DEFAULT_EMAIL_LIMIT)
    parser.add_argument("--job-limit", type=_parse_limit, default=DEFAULT_JOB_LIMIT)
    try:
        args = parser.parse_args(arguments)
    except ConfigurationError:
        print(json.dumps({"outcome": "invalid_arguments"}))
        return 2
    try:
        settings = load_settings()
    except Exception as error:
        outcome = (
            Outcome.CONFIGURATION_FAILURE
            if isinstance(error, ValueError)
            else Outcome.STARTUP_FAILURE
        )
        results = tuple(ProcessorResult(name, outcome) for name in ("email", "jobs"))
    else:
        results = run_tick(
            settings, email_limit=args.email_limit, job_limit=args.job_limit
        )
    for result in results:
        print(json.dumps(result.public_summary(), sort_keys=True))
    return max(result.exit_code for result in results)


if __name__ == "__main__":
    raise SystemExit(main())
