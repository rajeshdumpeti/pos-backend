"""Repository scoping, transaction safety and layer boundaries without a DB."""

import ast
from pathlib import Path
from unittest.mock import patch
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
import pytest
from app.application.plan_selected_date import PlanSelectedDateService
from app.application.planning import PlanService
from app.domain.planning import PlanUnavailableError
from app.infrastructure.planning import (
    SqlAlchemyPlanRepository,
    SqlAlchemyPlanUnitOfWork,
)
from app.infrastructure.planning_models import PlanItemModel
from tests.test_today import ACCOUNT, NOW


def test_selected_date_repository_predicates_and_existing_index():
    captured = []

    class Session:
        def scalars(self, query):
            captured.append(query)
            return []

    repository = SqlAlchemyPlanRepository(Session())
    assert repository.selected_date_facts(ACCOUNT, NOW.date()) == []
    query = captured[0].compile(dialect=postgresql.dialect())
    assert set(query.params.values()) == {ACCOUNT, NOW.date()}
    statement = str(query)
    assert "WHERE plan_items.user_account_id =" in statement
    assert "AND plan_items.scheduled_for_date =" in statement
    assert "scheduled_start_at =" not in statement
    assert any(
        tuple(index.columns.keys())[:2] == ("user_account_id", "scheduled_for_date")
        for index in PlanItemModel.__table__.indexes
    )


def test_driver_read_failure_rollback_close_and_safe_translation():
    class Session:
        def __init__(self):
            self.calls = []

        def scalars(self, query):
            raise OperationalError("private SQL", {}, Exception("private-driver-text"))

        def rollback(self):
            self.calls.append("rollback")

        def close(self):
            self.calls.append("close")

    session = Session()
    plans = PlanService(
        lambda request_id: SqlAlchemyPlanUnitOfWork(
            lambda: session, request_id=request_id
        ),
        clock=lambda: NOW,
    )
    with pytest.raises(PlanUnavailableError) as error:
        PlanSelectedDateService(plans).get(ACCOUNT, NOW.date(), "UTC", "safe-id")
    assert session.calls == ["rollback", "close"]
    assert "private" not in str(error.value)


def test_successful_read_closes_without_commit():
    class Session:
        def __init__(self):
            self.calls = []

        def scalars(self, query):
            return []

        def close(self):
            self.calls.append("close")

    session = Session()
    plans = PlanService(
        lambda request_id: SqlAlchemyPlanUnitOfWork(lambda: session), clock=lambda: NOW
    )
    result = PlanSelectedDateService(plans).get(ACCOUNT, NOW.date(), "UTC", "test")
    assert result.summary.total == 0 and session.calls == ["close"]


def test_selected_date_layers_remain_framework_independent():
    for filename in (
        "app/domain/plan_selection.py",
        "app/domain/plan_read.py",
        "app/application/plan_selected_date.py",
    ):
        for node in ast.walk(ast.parse(Path(filename).read_text())):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )
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
    with patch(
        "sqlalchemy.create_engine", side_effect=AssertionError("connection attempted")
    ):
        from migrations.metadata import target_metadata

        assert "plan_items" in target_metadata.tables
