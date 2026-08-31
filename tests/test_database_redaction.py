"""Regression coverage for database credential redaction boundaries."""

import logging
import traceback

import pytest
from sqlalchemy.exc import OperationalError

from app.application import PersistenceUnavailableError
from app.infrastructure.database_diagnostics import (
    format_safe_database_traceback,
    log_database_failure,
    raise_safe_database_error,
    redact_database_diagnostic,
)
from tests.conftest import pytest_exception_interact


SENTINEL = "must_never_appear"
SENSITIVE_URL = (
    f"postgresql+psycopg://sentinel-user:{SENTINEL}@localhost:5432/pos_test"
)


def _driver_error() -> OperationalError:
    return OperationalError(
        "connection failed",
        {},
        RuntimeError(f"password={SENTINEL} url={SENSITIVE_URL}"),
    )


def test_safe_database_exception_preserves_private_cause_without_rendering_secret():
    internal = _driver_error()

    with pytest.raises(PersistenceUnavailableError) as captured:
        try:
            raise internal
        except OperationalError as error:
            raise_safe_database_error(
                PersistenceUnavailableError,
                "Database operation failed",
                error,
            )

    public_error = captured.value
    rendered = "".join(traceback.format_exception(public_error))
    assert public_error._internal_cause is internal  # type: ignore[attr-defined]
    assert public_error.__cause__ is not internal
    assert SENTINEL not in str(public_error)
    assert SENTINEL not in rendered
    assert SENSITIVE_URL not in rendered


def test_project_traceback_formatter_redacts_urls_and_password_fields():
    internal = _driver_error()
    try:
        raise internal
    except OperationalError as error:
        with pytest.raises(PersistenceUnavailableError) as captured:
            raise_safe_database_error(
                PersistenceUnavailableError,
                "Database operation failed",
                error,
            )

    rendered = format_safe_database_traceback(captured.value)
    assert SENTINEL not in rendered
    assert SENSITIVE_URL not in rendered
    assert "OperationalError details redacted" in rendered


def test_structured_database_failure_log_never_renders_exception_details(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.ERROR, logger="pos.database.test")
    logger = logging.getLogger("pos.database.test")

    log_database_failure(
        logger,
        operation="database_connect",
        category="unavailable",
        request_id="redaction-test",
    )

    assert SENTINEL not in caplog.text
    assert SENSITIVE_URL not in caplog.text
    record = caplog.records[-1]
    assert record.operation == "database_connect"
    assert record.request_id == "redaction-test"


def test_redaction_helper_masks_direct_diagnostic_text():
    rendered = redact_database_diagnostic(
        f"connection failed password={SENTINEL} "
        f"kwargs={{'password': '{SENTINEL}'}} url={SENSITIVE_URL}"
    )
    assert SENTINEL not in rendered
    assert SENSITIVE_URL not in rendered
    assert "postgresql+psycopg://***:***@localhost:5432/pos_test" in rendered


def test_redaction_masks_malformed_url_with_unescaped_at_in_password():
    diagnostic = (
        "postgresql://sentinel-user:must_never_appear@fragment@localhost/pos_test"
    )
    rendered = redact_database_diagnostic(diagnostic)
    assert SENTINEL not in rendered
    assert "fragment" not in rendered
    assert rendered == "postgresql://***:***@localhost/pos_test"


def test_pytest_failure_hook_redacts_database_credentials():
    class Report:
        longrepr = f"password={SENTINEL} url={SENSITIVE_URL}"

        @property
        def longreprtext(self) -> str:
            return str(self.longrepr)

    report = Report()
    pytest_exception_interact(None, None, report)

    assert SENTINEL not in report.longrepr
    assert SENSITIVE_URL not in report.longrepr
