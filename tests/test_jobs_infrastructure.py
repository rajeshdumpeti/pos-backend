"""SQL/transaction, diagnostics and bounded CLI contract tests without PostgreSQL."""

from dataclasses import asdict, replace
from datetime import timedelta
import logging
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
import traceback
import pytest
from sqlalchemy import MetaData, CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from app.application.jobs import WorkerSummary
from app.application.persistence import PersistenceUnavailableError
from app.domain.jobs import JOB_TYPE, new_job
from app.infrastructure import job_worker
from app.infrastructure.jobs import (
    SqlAlchemyJobRepository,
    SqlAlchemyJobUnitOfWork,
    SafeJobDiagnostic,
    due_query,
    fence,
)
from app.infrastructure.job_models import BackgroundJobModel
from migrations.metadata import target_metadata
from tests.test_jobs import NOW, PAYLOAD


def job():
    return new_job(JOB_TYPE, uuid4(), PAYLOAD, NOW, "synthetic-key", NOW)


def test_claim_sql_is_due_bounded_deterministic_and_skip_locked():
    sql = str(due_query(NOW, 7).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql and "LIMIT" in sql
    assert "ORDER BY background_jobs.scheduled_at, background_jobs.id" in sql
    assert "lease_expires_at <=" in sql and "scheduled_at <=" in sql


def test_atomic_deduplication_returns_existing_and_checks_intent_without_commit():
    value = job()
    session = Mock()
    session.scalar.return_value = BackgroundJobModel(**asdict(value))
    repo = SqlAlchemyJobRepository(session)
    assert repo.schedule(replace(value, id=uuid4())).id == value.id
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql and "RETURNING" in sql
    session.commit.assert_not_called()
    from app.domain.jobs import JobConflictError

    with pytest.raises(JobConflictError):
        repo.schedule(replace(value, intent_digest="different"))


def test_claim_exhaustion_and_attempts_are_flushed_not_committed():
    value = job()
    exhausted = replace(
        value,
        id=uuid4(),
        state="running",
        attempts=5,
        lease_owner=uuid4(),
        lease_expires_at=NOW,
    )
    session = Mock()
    session.scalars.return_value = [
        BackgroundJobModel(**asdict(value)),
        BackgroundJobModel(**asdict(exhausted)),
    ]
    result = SqlAlchemyJobRepository(session).claim(
        NOW, uuid4(), timedelta(seconds=30), 10
    )
    assert (
        len(result.jobs) == 1 and result.jobs[0].attempts == 1 and result.exhausted == 1
    )
    session.flush.assert_called_once()
    session.commit.assert_not_called()


def test_acknowledgement_fence_contains_attempt_owner_state_and_live_lease():
    value = replace(job(), lease_owner=uuid4(), state="running", attempts=2)
    sql = str(fence(value, NOW).compile(dialect=postgresql.dialect()))
    for field in ("attempts", "lease_owner", "lease_expires_at", "state", "id"):
        assert f"background_jobs.{field}" in sql
    assert "lease_expires_at >" in sql


def test_uow_owns_isolated_sessions_commit_rollback_close_and_read_rollback():
    sessions = [Mock(), Mock()]
    factory = Mock(side_effect=sessions)
    unit = SqlAlchemyJobUnitOfWork(factory)
    with unit:
        assert unit.jobs is not None
        unit.commit()
    sessions[0].commit.assert_called_once()
    sessions[0].close.assert_called_once()
    with unit:
        assert unit.jobs is not None
    sessions[1].rollback.assert_called_once()
    sessions[1].close.assert_called_once()
    assert factory.call_count == 2
    with pytest.raises(RuntimeError):
        unit.commit()


@pytest.mark.parametrize("where", ["body", "commit", "rollback", "close", "factory"])
def test_uow_translates_database_failures_and_redacts_traceback(where):
    marker = "synthetic-private-diagnostic"
    failure = OperationalError(marker, {"private": marker}, RuntimeError(marker))
    session = Mock()
    factory = Mock(return_value=session)
    if where == "factory":
        factory.side_effect = failure
    elif where not in {"body"}:
        getattr(session, where).side_effect = failure
    with pytest.raises(PersistenceUnavailableError) as caught:
        with SqlAlchemyJobUnitOfWork(factory) as unit:
            if where == "body":
                raise failure
            if where == "commit":
                unit.commit()
    rendered = "".join(
        traceback.format_exception(
            type(caught.value), caught.value, caught.value.__traceback__
        )
    )
    assert marker not in str(caught.value)
    # Existing boundary keeps the raw driver cause privately, never in the public cause chain.
    assert marker not in str(caught.value.__cause__)
    assert marker not in rendered
    if where != "factory":
        session.close.assert_called_once()


def test_diagnostics_are_allowlisted_and_exclude_payload_account_and_intent(caplog):
    value = replace(job(), payload={"private": "synthetic-private-payload"})
    worker = uuid4()
    with caplog.at_level(logging.INFO, logger="pos.jobs"):
        SafeJobDiagnostic().emit(value, worker)
        SafeJobDiagnostic().emit(replace(value, state="private"))
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.job_id == str(value.id) and record.lease_owner == str(worker)
    for key in ("payload", "user_account_id", "intent_digest", "deduplication_digest"):
        assert key not in record.__dict__
    assert "synthetic-private-payload" not in str(record.__dict__)


@pytest.mark.parametrize(
    "args,code",
    [
        ([], 1),
        (["process-pending", "0"], 1),
        (["process-pending", "101"], 1),
        (["process-pending", "private"], 1),
        (["loop"], 1),
        (["process-pending"], 0),
        (["process-pending", "7"], 0),
    ],
)
def test_cli_is_single_run_bounded_and_does_not_echo_arguments(
    monkeypatch, capsys, args, code
):
    process = Mock(return_value=0)
    monkeypatch.setattr(job_worker, "process_pending", process)
    monkeypatch.setattr(job_worker.sys, "argv", ["job-worker", *args])
    assert job_worker.main() == code
    assert "private" not in capsys.readouterr().out
    if code == 0:
        process.assert_called_once_with(10 if len(args) == 1 else 7)
    else:
        process.assert_not_called()


def test_cli_outputs_counts_only_and_disposes_engine_on_success_and_failure(
    monkeypatch, capsys
):
    engine = Mock()
    monkeypatch.setattr(
        job_worker, "load_settings", lambda: Mock(database_url=object())
    )
    monkeypatch.setattr(job_worker, "create_database_engine", lambda _, **_kwargs: engine)
    monkeypatch.setattr(job_worker, "create_session_factory", lambda _: Mock())
    dispose = Mock()
    monkeypatch.setattr(job_worker, "dispose_database_engine", dispose)
    worker = Mock()
    worker.process_pending.return_value = WorkerSummary(claimed=1, succeeded=1)
    monkeypatch.setattr(job_worker, "JobWorker", Mock(return_value=worker))
    assert job_worker.process_pending() == 0
    assert (
        capsys.readouterr().out
        == "claimed=1 succeeded=1 retryable=0 dead=0 lease_lost=0\n"
    )
    worker.process_pending.side_effect = RuntimeError("synthetic-private")
    assert job_worker.process_pending() == 4
    assert (
        "synthetic-private" not in capsys.readouterr().out and dispose.call_count == 2
    )


def test_migration_and_model_metadata_match_without_database():
    import importlib.util

    path = Path("migrations/versions/0007_background_jobs.py")
    spec = importlib.util.spec_from_file_location("jobs_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    created = []
    indexes = []

    class Recorder:
        def create_table(self, name, *items):
            from sqlalchemy import Table

            created.append(
                Table(
                    name,
                    MetaData(naming_convention=target_metadata.naming_convention),
                    *items,
                )
            )

        def create_index(self, name, table, columns, **kwargs):
            if table == "background_jobs":
                indexes.append((name, columns, str(kwargs["postgresql_where"])))

        def f(self, name):
            return name

        def drop_constraint(self, name, table, **kwargs):
            target = next(t for t in created if t.name == table)
            target.constraints.remove(
                next(c for c in target.constraints if c.name == name)
            )

        def create_check_constraint(self, name, table, condition):
            target = next(t for t in created if t.name == table)
            target.append_constraint(CheckConstraint(condition, name=name))

    recorder = Recorder()
    module.op = recorder
    module.upgrade()
    extension_spec = importlib.util.spec_from_file_location(
        "inbox_extension", Path("migrations/versions/0008_notification_inbox.py")
    )
    extension = importlib.util.module_from_spec(extension_spec)
    extension_spec.loader.exec_module(extension)
    extension.op = recorder
    extension.upgrade()
    table = created[0]
    expected = BackgroundJobModel.__table__
    assert [(c.name, str(c.type), c.nullable) for c in table.columns] == [
        (c.name, str(c.type), c.nullable) for c in expected.columns
    ]
    assert {
        (c.name, str(c.sqltext))
        for c in table.constraints
        if isinstance(c, CheckConstraint)
    } == {
        (c.name, str(c.sqltext))
        for c in expected.constraints
        if isinstance(c, CheckConstraint)
    }
    assert {(name, tuple(columns), where) for name, columns, where in indexes} == {
        (
            i.name,
            tuple(c.name for c in i.columns),
            str(i.dialect_options["postgresql"]["where"]),
        )
        for i in expected.indexes
    }
    assert {
        (c.name, tuple(col.name for col in c.columns)) for c in table.constraints
    } == {(c.name, tuple(col.name for col in c.columns)) for c in expected.constraints}
    assert {(fk.target_fullname, fk.ondelete) for fk in table.foreign_keys} == {
        (fk.target_fullname, fk.ondelete) for fk in expected.foreign_keys
    }
    assert module.down_revision == "0006_daily_planning"


def test_standalone_cli_model_resolves_account_foreign_key_without_application_startup():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.infrastructure.job_models import BackgroundJobModel; "
            "from sqlalchemy.schema import CreateTable; "
            "from sqlalchemy.dialects.postgresql import dialect; "
            "str(CreateTable(BackgroundJobModel.__table__).compile(dialect=dialect()))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""
