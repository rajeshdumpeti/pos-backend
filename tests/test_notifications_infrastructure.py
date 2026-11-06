"""SQL safety, latest migration parity and deliberate operations entry point."""

from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
import importlib.util
import pytest
from sqlalchemy import CheckConstraint, MetaData, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from app.infrastructure.notifications import (
    SqlAlchemyInboxRepository,
    SqlAlchemyInboxUnitOfWork,
)
from app.infrastructure.notification_models import (
    AccountTimezoneModel,
    NotificationModel,
)
from app.infrastructure import notification_operations
from app.domain.notifications import InboxUnavailableError
from tests.test_notifications import populated


def test_inbox_error_boundary_is_transient_and_redacts_driver_details():
    from app.domain.jobs import TransientJobFailure

    session = Mock()
    session.execute.side_effect = OperationalError(
        "private-query", {}, RuntimeError("private-driver")
    )
    with pytest.raises(InboxUnavailableError) as caught:
        with SqlAlchemyInboxUnitOfWork(lambda: session) as unit:
            unit.inbox.cancel_jobs(uuid4(), "today.items_remaining", None)
    assert isinstance(caught.value, TransientJobFailure)
    assert "private" not in str(caught.value) and "private" not in str(
        caught.value.__cause__
    )
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_repository_uses_scoped_keyset_expiry_atomic_read_and_bounded_cleanup():
    data, rows = populated()
    owner, _, _, store, clock, _, _ = data
    session = Mock()
    session.scalars.return_value = []
    session.execute.return_value.rowcount = 1
    repo = SqlAlchemyInboxRepository(session)
    repo.list(owner, "plan", clock[0], (rows[0].created_at, rows[0].id), 3)
    sql = str(session.scalars.call_args.args[0].compile(dialect=postgresql.dialect()))
    for required in (
        "user_account_id",
        "expires_at",
        "source",
        "created_at DESC",
        "id DESC",
        "LIMIT",
    ):
        assert required in sql
    assert "OFFSET" not in sql
    session.execute.return_value.all.return_value = [(rows[0].id, owner)]
    session.scalars.return_value = [owner]
    repo.cleanup(clock[0], 2)
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "SKIP LOCKED" in sql and "LIMIT" in sql and "expires_at <=" in sql
    account_sql = str(
        session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    )
    assert "user_accounts" in account_sql and "SKIP LOCKED" in account_sql
    session.commit.assert_not_called()


def test_latest_inbox_migration_matches_columns_constraints_indexes_and_foreign_keys():
    spec = importlib.util.spec_from_file_location(
        "inbox_migration", Path("migrations/versions/0008_notification_inbox.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tables = {}
    indexes = []

    class Recorder:
        def create_table(self, name, *items):
            tables[name] = Table(name, MetaData(), *items)

        def create_index(self, name, table, columns, **kwargs):
            indexes.append((name, table, columns, kwargs))

        def f(self, name):
            return name

        def drop_constraint(self, *args, **kwargs):
            pass

        def create_check_constraint(self, *args, **kwargs):
            pass

    module.op = Recorder()
    module.upgrade()
    for expected in (AccountTimezoneModel.__table__, NotificationModel.__table__):
        actual = tables[expected.name]
        assert [(c.name, str(c.type), c.nullable) for c in actual.columns] == [
            (c.name, str(c.type), c.nullable) for c in expected.columns
        ]
        assert {
            (c.name, str(c.sqltext))
            for c in actual.constraints
            if isinstance(c, CheckConstraint)
        } == {
            (c.name, str(c.sqltext))
            for c in expected.constraints
            if isinstance(c, CheckConstraint)
        }
        assert {
            (c.name, tuple(col.name for col in c.columns)) for c in actual.constraints
        } == {
            (c.name, tuple(col.name for col in c.columns)) for c in expected.constraints
        }
        assert {(f.target_fullname, f.ondelete) for f in actual.foreign_keys} == {
            (f.target_fullname, f.ondelete) for f in expected.foreign_keys
        }
        actual_indexes = [
            (name, tuple(str(c) for c in columns), str(kwargs.get("postgresql_where")))
            for name, table, columns, kwargs in indexes
            if table == expected.name
        ]
        expected_indexes = [
            (
                i.name,
                tuple(c.name if hasattr(c, "name") else str(c) for c in i.expressions),
                str(i.dialect_options["postgresql"]["where"]),
            )
            for i in expected.indexes
        ]
        assert sorted(actual_indexes) == sorted(expected_indexes)
    assert module.down_revision == "0007_background_jobs"


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["loop"],
        ["schedule-cleanup", "101", "private", "private-key"],
        ["schedule-cleanup", "1", "2026-10-30", "private-key"],
    ],
)
def test_operations_invalid_arguments_never_echo_private_values(
    monkeypatch, capsys, args
):
    monkeypatch.setattr(notification_operations.sys, "argv", ["operations", *args])
    assert notification_operations.main() == 1
    assert "private" not in capsys.readouterr().out


def test_maintenance_scheduling_uses_only_approved_job_and_sanitized_summary(
    monkeypatch, capsys
):
    from datetime import datetime, UTC
    from app.domain.notifications import CLEANUP_TYPE

    engine = Mock()
    service = Mock()
    monkeypatch.setattr(
        notification_operations.sys,
        "argv",
        [
            "operations",
            "schedule-cleanup",
            "20",
            "2026-10-30T12:00:00+00:00",
            "synthetic-operation",
        ],
    )
    monkeypatch.setattr(
        notification_operations, "load_settings", lambda: Mock(database_url=object())
    )
    monkeypatch.setattr(
        notification_operations, "create_database_engine", lambda _: engine
    )
    monkeypatch.setattr(
        notification_operations, "create_session_factory", lambda _: Mock()
    )
    monkeypatch.setattr(
        notification_operations, "dispose_database_engine", lambda _: None
    )
    monkeypatch.setattr(notification_operations, "JobService", lambda _: service)
    assert notification_operations.main() == 0
    service.schedule.assert_called_once_with(
        CLEANUP_TYPE,
        None,
        {"limit": 20},
        datetime(2026, 10, 30, 12, tzinfo=UTC),
        "synthetic-operation",
    )
    assert capsys.readouterr().out == "scheduled=1\n"
    service.schedule.side_effect = RuntimeError("private-native-error")
    assert (
        notification_operations.main() == 4 and "private" not in capsys.readouterr().out
    )


def test_integrated_plan_producer_diagnostics_do_not_log_navigation_references(caplog):
    import logging
    from app.infrastructure.planning import SafePlanDiagnostic
    from tests.notification_support import fixture
    from tests.test_notifications import create

    owner, _, _, store, clock, plans, inbox = fixture()
    plans.diagnostic = SafePlanDiagnostic()
    with caplog.at_level(logging.INFO, logger="pos.planning.persistence"):
        item = create(plans, owner, clock[0])
    assert caplog.records
    for record in caplog.records:
        values = str(record.__dict__)
        assert str(item.id) not in values and str(owner) not in values
        assert "Synthetic item" not in values


@pytest.mark.parametrize("source", ["all", "today", "plan"])
def test_authoritative_count_sql_only_counts_unread_retained_owned_scope(source):
    from datetime import UTC, datetime

    owner = uuid4()
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    session = Mock()
    session.scalar.return_value = 7
    repository = SqlAlchemyInboxRepository(session)
    assert repository.count(owner, source, now) == 7
    statement = session.scalar.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "notifications.read_at IS NULL" in sql
    assert "notifications.user_account_id =" in sql
    assert "notifications.expires_at >" in sql
    assert owner in compiled.params.values() and now in compiled.params.values()
    assert ("notifications.source =" in sql) == (source != "all")
    if source != "all":
        assert source in compiled.params.values()
    assert "LIMIT" not in sql and "OFFSET" not in sql
    session.commit.assert_not_called()
