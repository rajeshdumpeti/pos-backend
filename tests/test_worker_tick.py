"""Finite orchestration and secret-safe diagnostics; all network calls forbidden."""

from contextlib import contextmanager
from dataclasses import replace
import json
import logging
import os
import subprocess
import sys
import traceback
from unittest.mock import Mock, patch

import pytest

from app.application.email_delivery import DispatchSummary
from app.application.jobs import WorkerSummary
from app.config import ConfigurationError, load_settings
from app.infrastructure import worker_tick as tick
from app.infrastructure.authentication import email_delivery
from app.infrastructure import job_worker
from app.infrastructure.database_diagnostics import format_safe_database_traceback


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live network access forbidden")

    monkeypatch.setattr("psycopg.connect", forbidden)
    monkeypatch.setattr("redis.connection.Connection.connect", forbidden)
    monkeypatch.setattr("httpx.HTTPTransport.handle_request", forbidden)


@pytest.fixture
def settings():
    return load_settings({"APP_ENV": "test"})


def processor_factory(summary, *, failure=None, startup=None, cleanup=None):
    processor = Mock()
    processor.process_pending.return_value = summary
    processor.process_pending.side_effect = failure
    closed = Mock(side_effect=cleanup)

    @contextmanager
    def compose(settings):
        try:
            if startup:
                raise startup
            yield processor
        finally:
            closed()

    return compose, processor, closed


def factories(email_failure=None, job_failure=None):
    return (
        processor_factory(DispatchSummary(1, 1, 0, 0), failure=email_failure),
        processor_factory(WorkerSummary(claimed=1, succeeded=1), failure=job_failure),
    )


def run(settings, email, jobs):
    return tick.run_tick(
        settings,
        email_factory=email[0],
        job_factory=jobs[0],
        email_limit=7,
        job_limit=9,
    )


@pytest.mark.parametrize(
    "email_fails,job_fails,code",
    [
        (False, False, 0),
        (True, False, 1),
        (False, True, 1),
        (True, True, 1),
    ],
)
def test_independent_finite_passes_close_and_keep_both_outcomes(
    settings, email_fails, job_fails, code
):
    email, jobs = factories(
        RuntimeError() if email_fails else None, RuntimeError() if job_fails else None
    )
    results = run(settings, email, jobs)
    assert max(result.exit_code for result in results) == code
    for factory, limit in ((email, 7), (jobs, 9)):
        factory[1].process_pending.assert_called_once_with(limit)
        factory[2].assert_called_once_with()
    assert results[0].exit_code == int(email_fails)
    assert results[1].exit_code == int(job_fails)


@pytest.mark.parametrize(
    "email_summary,job_summary",
    [
        (DispatchSummary(1, 0, 1, 0), WorkerSummary(retryable=1)),
        (DispatchSummary(1, 0, 0, 1), WorkerSummary(dead=1)),
        (DispatchSummary(0, 0, 0, 0), WorkerSummary(lease_lost=1)),
    ],
)
def test_handled_item_results_do_not_fail_tick(settings, email_summary, job_summary):
    results = run(
        settings, processor_factory(email_summary), processor_factory(job_summary)
    )
    assert all(result.exit_code == 0 for result in results)
    assert results[0].retryable == email_summary.retry_scheduled
    assert results[0].dead == email_summary.failed
    assert (
        results[0].lease_lost is None
    )  # dispatcher does not expose a lease-lost counter


@pytest.mark.parametrize("argument", ["--email-limit", "--job-limit"])
@pytest.mark.parametrize("value", ["0", "-1", "26", "1000000000", "bad", "1.5"])
def test_invalid_limit_returns_two_without_loading_or_processing(
    argument, value, capsys
):
    with patch.object(tick, "load_settings") as load, patch.object(
        tick, "run_tick"
    ) as process:
        assert tick.main(["process-pending", argument, value]) == 2
    load.assert_not_called()
    process.assert_not_called()
    assert capsys.readouterr().out == '{"outcome": "invalid_arguments"}\n'


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["loop"],
        ["process-pending", "--unknown"],
        ["process-pending", "--email-limit"],
    ],
)
def test_cli_shape_errors_are_sanitized(args, capsys):
    assert tick.main(args) == 2
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("field", ["email_limit", "job_limit"])
@pytest.mark.parametrize("value", [0, -1, 26, True, "5"])
def test_direct_orchestration_validates_both_limits_before_composing(
    settings, field, value
):
    factory = Mock()
    with pytest.raises(ConfigurationError):
        tick.run_tick(
            settings, email_factory=factory, job_factory=factory, **{field: value}
        )
    factory.assert_not_called()


@pytest.mark.parametrize(
    "first_error,second_error,expected",
    [
        (RuntimeError(), None, 3),
        (ConfigurationError(), None, 2),
        (RuntimeError(), RuntimeError(), 3),
        (RuntimeError(), ConfigurationError(), 3),
    ],
)
def test_startup_failure_still_attempts_other_processor_and_keeps_severity(
    settings, first_error, second_error, expected
):
    email = processor_factory(DispatchSummary(0, 0, 0, 0), startup=first_error)
    jobs = processor_factory(WorkerSummary(), startup=second_error)
    results = run(settings, email, jobs)
    assert max(result.exit_code for result in results) == expected
    email[1].process_pending.assert_not_called()
    if second_error is None:
        jobs[1].process_pending.assert_called_once_with(9)
    for factory in (email, jobs):
        factory[2].assert_called_once()


def test_cleanup_failure_is_operational_and_other_processor_runs(settings):
    email = processor_factory(DispatchSummary(1, 1, 0, 0), cleanup=RuntimeError())
    jobs = processor_factory(WorkerSummary())
    results = run(settings, email, jobs)
    assert results[0].exit_code == 1 and results[0].succeeded == 1
    assert results[1].exit_code == 0
    jobs[1].process_pending.assert_called_once()


@pytest.mark.parametrize(
    "failure,code", [(ConfigurationError(), 2), (ValueError(), 2), (RuntimeError(), 3)]
)
def test_shared_settings_failure_prevents_processing_and_is_safe(failure, code, capsys):
    with patch.object(tick, "load_settings", side_effect=failure), patch.object(
        tick, "run_tick"
    ) as run:
        assert tick.main(["process-pending"]) == code
    run.assert_not_called()
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_main_defaults_and_allowlisted_aggregate_summary(settings, capsys):
    email, jobs = factories()
    with patch.object(tick, "load_settings", return_value=settings), patch.object(
        tick, "email_processor", email[0]
    ), patch.object(tick, "job_processor", jobs[0]):
        assert tick.main(["process-pending"]) == 0
    email[1].process_pending.assert_called_once_with(25)
    jobs[1].process_pending.assert_called_once_with(10)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["processor"] for row in records] == ["email", "jobs"]
    assert all(
        set(row)
        == {
            "processor",
            "outcome",
            "claimed",
            "succeeded",
            "retryable",
            "dead",
            "lease_lost",
        }
        for row in records
    )


def test_sentinel_private_values_never_enter_outputs_reprs_or_public_tracebacks(
    settings, capsys, caplog
):
    markers = (
        "sentinel-person@example.invalid",
        "sentinel-token",
        "sentinel-password",
        "sentinel-api-key",
        "rediss://sentinel-user:sentinel-redis-secret@private.invalid:6379",
        "https://private.invalid/reset",
        "sentinel-payload",
    )
    raw = RuntimeError(" ".join(markers))
    email, jobs = factories(raw, raw)
    with caplog.at_level(logging.DEBUG):
        results = run(settings, email, jobs)
        with patch.object(tick, "load_settings", return_value=settings), patch.object(
            tick, "run_tick", return_value=results
        ):
            assert tick.main(["process-pending"]) == 1
    captured = capsys.readouterr()
    rendered = captured.out + captured.err + caplog.text + repr(results)
    for result in results:
        assert result._error._internal_cause is raw
        rendered += str(result._error) + repr(result._error)
        rendered += "".join(traceback.format_exception(result._error))
        rendered += format_safe_database_traceback(result._error)
    assert all(marker not in rendered for marker in markers)


@pytest.mark.parametrize(
    "summary",
    [
        DispatchSummary("secret", 0, 0, 0),
        replace(WorkerSummary(), dead=-1),
        {"payload": "secret"},
    ],
)
def test_invalid_summary_is_not_serialized(settings, summary):
    results = run(
        settings, processor_factory(summary), processor_factory(WorkerSummary())
    )
    assert results[0].exit_code == 1
    assert "secret" not in repr(results[0].public_summary())


def configured_settings():
    return load_settings(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://placeholder.invalid/pos",
            "AUTH_EMAIL_PROVIDER": "resend",
            "RESEND_API_KEY": "sentinel-api-key",
            "AUTH_EMAIL_FROM": "sender@example.invalid",
            "AUTH_OUTBOX_ENCRYPTION_KEY": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",
        }
    )


@pytest.mark.parametrize("fail_at", [None, "cipher", "dispatch", "provider_close"])
def test_real_email_composition_closes_http_and_engine_on_all_paths(fail_at):
    engine, provider, dispatcher = Mock(), Mock(), Mock()
    if fail_at == "dispatch":
        dispatcher.process_pending.side_effect = RuntimeError()
    if fail_at == "provider_close":
        provider.close.side_effect = RuntimeError()
    with patch.object(
        email_delivery, "create_database_engine", return_value=engine
    ), patch.object(email_delivery, "create_session_factory"), patch.object(
        email_delivery, "dispose_database_engine"
    ) as dispose, patch.object(
        email_delivery,
        "AesGcmOutboxCipher",
        side_effect=RuntimeError() if fail_at == "cipher" else None,
    ), patch.object(
        email_delivery, "ResendEmailProvider", return_value=provider
    ) as build, patch.object(
        email_delivery, "AuthenticationEmailDispatcher", return_value=dispatcher
    ):
        try:
            with email_delivery.email_processor(configured_settings()) as process:
                process.process_pending(1)
        except RuntimeError:
            assert fail_at is not None
        dispose.assert_called_once_with(engine)
        if build.called:
            provider.close.assert_called_once()


@pytest.mark.parametrize("failure", [None, "compose", "process"])
def test_real_job_composition_closes_engine_on_all_paths(settings, failure):
    engine, worker = Mock(), Mock()
    if failure == "process":
        worker.process_pending.side_effect = RuntimeError()
    settings = configured_settings()
    with patch.object(
        job_worker, "create_database_engine", return_value=engine
    ), patch.object(
        job_worker,
        "create_session_factory",
        side_effect=RuntimeError() if failure == "compose" else None,
    ), patch.object(
        job_worker, "dispose_database_engine"
    ) as dispose, patch.object(
        job_worker, "JobWorker", return_value=worker
    ):
        try:
            with job_worker.job_processor(settings) as process:
                process.process_pending(1)
        except RuntimeError:
            assert failure is not None
        dispose.assert_called_once_with(engine)


def test_missing_email_configuration_still_processes_jobs(settings):
    jobs = processor_factory(WorkerSummary())
    results = tick.run_tick(settings, job_factory=jobs[0])
    assert results[0].exit_code == 2 and results[1].exit_code == 0
    jobs[1].process_pending.assert_called_once()


def test_import_and_invalid_module_cli_are_network_free_without_optional_settings():
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("AUTH_", "DATABASE_", "REDIS_", "CURRENT_", "RESEND_", "JWT_")
        )
    }
    environment["APP_ENV"] = "test"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.infrastructure.worker_tick",
            "process-pending",
            "--job-limit",
            "0",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stderr == ""


def test_exact_handoff_command_runs_both_maximum_passes(settings, capsys):
    email, jobs = factories()
    with patch.object(tick, "load_settings", return_value=settings), patch.object(
        tick, "email_processor", email[0]
    ), patch.object(tick, "job_processor", jobs[0]):
        assert (
            tick.main(["process-pending", "--email-limit", "25", "--job-limit", "25"])
            == 0
        )
    email[1].process_pending.assert_called_once_with(25)
    jobs[1].process_pending.assert_called_once_with(25)
    assert len(capsys.readouterr().out.splitlines()) == 2


@pytest.mark.parametrize("retryable", [True, False])
def test_real_email_dispatcher_preserves_handled_item_outcome(settings, retryable):
    from app.application.email_delivery import (
        AuthenticationEmailDispatcher,
        EmailDeliveryError,
    )
    from tests.test_authentication_email_delivery import DispatchUnit

    record = Mock(attempt_count=0)
    claim = Mock()
    claim.claim_available.return_value = [record]
    acknowledgement = Mock()
    units = iter([DispatchUnit(claim), DispatchUnit(acknowledgement)])
    provider = Mock()
    provider.send.side_effect = EmailDeliveryError(
        "provider_unavailable", retryable=retryable
    )
    dispatcher = AuthenticationEmailDispatcher(lambda _: next(units), Mock(), provider)

    @contextmanager
    def compose(settings):
        yield dispatcher

    jobs = processor_factory(WorkerSummary())
    results = tick.run_tick(settings, email_factory=compose, job_factory=jobs[0])
    assert all(result.exit_code == 0 for result in results)
    assert results[0].retryable == int(retryable)
    assert results[0].dead == int(not retryable)
    claim.claim_available.assert_called_once()
    if retryable:
        acknowledgement.release_for_retry.assert_called_once()
    else:
        acknowledgement.mark_failed.assert_called_once()
