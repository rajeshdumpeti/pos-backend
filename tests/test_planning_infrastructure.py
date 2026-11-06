import ast
import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy import MetaData, Table, Index
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from app.infrastructure.planning import (
    _record,
    _values,
    _snapshot,
    _from_snapshot,
    SqlAlchemyPlanUnitOfWork,
)
from app.infrastructure.planning_models import PlanItemModel, PlanCreationReceiptModel
from app.domain.planning import PlanUnavailableError
from tests.test_today import item


def test_reconstruction_and_receipt_round_trip():
    value = item(1)
    assert _record(_values(value)) == value
    assert _from_snapshot(_snapshot(value)) == value
    for changes in (
        {"title": " "},
        {"timezone": "Unknown/Place"},
        {"version": 0},
        {"created_at": value.created_at.replace(tzinfo=None)},
    ):
        with pytest.raises(PlanUnavailableError):
            _record({**_values(value), **changes})
    with pytest.raises(PlanUnavailableError):
        _from_snapshot({"private": "invalid"})


def test_migration_metadata_parity_without_database():
    from app.infrastructure.relational import RELATIONAL_METADATA

    migration = Path("migrations/versions/0006_daily_planning.py")
    spec = importlib.util.spec_from_file_location("planning_revision", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = MetaData()
    # Provide referenced tables so PostgreSQL can resolve foreign-key types.
    RELATIONAL_METADATA.tables["user_accounts"].to_metadata(metadata)

    class Operations:
        def create_table(self, name, *columns):
            return Table(name, metadata, *columns)

        def create_index(self, name, table, columns):
            Index(name, *(metadata.tables[table].c[c] for c in columns))

        def f(self, name):
            return name

    with patch.object(module, "op", Operations()):
        module.upgrade()
    for model in (PlanItemModel, PlanCreationReceiptModel):
        expected = model.__table__
        actual = metadata.tables[expected.name]

        def ddl(table):
            return " ".join(
                str(CreateTable(table).compile(dialect=postgresql.dialect())).split()
            )

        # Constraint order is immaterial; compare columns and named constraints.
        assert [(c.name, str(c.type), c.nullable) for c in expected.columns] == [
            (c.name, str(c.type), c.nullable) for c in actual.columns
        ]

        def constraints(table):
            return {
                c.name: (
                    type(c).__name__,
                    str(getattr(c, "sqltext", "")),
                    tuple(c.columns.keys()),
                    tuple(
                        (e.target_fullname, e.ondelete)
                        for e in getattr(c, "elements", ())
                    ),
                )
                for c in table.constraints
            }

        assert constraints(expected) == constraints(actual)
        assert {(i.name, tuple(i.columns.keys())) for i in expected.indexes} == {
            (i.name, tuple(i.columns.keys())) for i in actual.indexes
        }
        assert ddl(actual)


def test_uow_rollback_close_and_safe_driver_translation():
    from sqlalchemy.exc import OperationalError

    class Session:
        def __init__(self):
            self.calls = []

        def rollback(self):
            self.calls.append("rollback")

        def close(self):
            self.calls.append("close")

        def commit(self):
            self.calls.append("commit")
            raise OperationalError("private SQL", {}, Exception("private driver"))

    session = Session()
    with pytest.raises(PlanUnavailableError) as error:
        with SqlAlchemyPlanUnitOfWork(lambda: session, request_id="test") as unit:
            unit.commit()
    assert session.calls == ["commit", "rollback", "close"]
    assert "private" not in str(error.value)


def test_new_layers_do_not_import_framework_or_infrastructure():
    for path in [
        Path("app/domain/planning.py"),
        Path("app/domain/today.py"),
        Path("app/application/planning.py"),
        Path("app/application/today.py"),
    ]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(
                name.startswith(
                    (
                        "fastapi",
                        "sqlalchemy",
                        "psycopg",
                        "app.infrastructure",
                        "app.config",
                    )
                )
                for name in names
            )
