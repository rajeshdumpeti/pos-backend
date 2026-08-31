"""Project-wide pytest safety hooks."""

from typing import Any

from app.infrastructure.database_diagnostics import redact_database_diagnostic


def pytest_exception_interact(
    node: Any,
    call: Any,
    report: Any,
) -> None:
    """Redact database credentials before pytest renders a failing traceback."""
    del node, call
    if report.longrepr is not None:
        report.longrepr = redact_database_diagnostic(report.longreprtext)
