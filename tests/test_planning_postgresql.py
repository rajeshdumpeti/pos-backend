"""Destructive integration checks are guarded by the local pos_test mechanism."""

from concurrent.futures import ThreadPoolExecutor
import os
import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session, sessionmaker
from app.application.planning import PlanService
from app.domain.planning import PlanIntent, PlanNotFoundError, PlanUnavailableError
from app.infrastructure.planning import SqlAlchemyPlanUnitOfWork
from app.infrastructure.planning_models import PlanItemModel, PlanCreationReceiptModel
from app.infrastructure.database import create_database_engine, dispose_database_engine
from app.infrastructure.relational import RELATIONAL_METADATA
from tests.postgresql_support import (
    upgrade_test_database,
    bind_alembic_to_test_database,
)
from tests.test_onboarding_postgresql import _active_account, NOW

TEST_URL = os.environ.get("POS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)


@pytest.fixture(scope="module")
def engine():
    assert TEST_URL is not None
    upgrade_test_database(TEST_URL)
    value = create_database_engine(SecretStr(TEST_URL))
    try:
        yield value
    finally:
        dispose_database_engine(value)


@pytest.fixture(autouse=True)
def empty_tables(engine):
    names = ", ".join(f'"{name}"' for name in RELATIONAL_METADATA.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))


def service(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    return PlanService(
        lambda request_id: SqlAlchemyPlanUnitOfWork(factory, request_id=request_id),
        clock=lambda: NOW,
    )


def intent():
    return PlanIntent("Synthetic PostgreSQL item", NOW.date(), "UTC", is_priority=True)


def test_concurrent_creation_and_completion_single_transition(engine):
    account = _active_account(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(
            pool.map(
                lambda _: service(engine).create(account, intent(), "same-key", "test"),
                range(2),
            )
        )
    assert created[0] == created[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = list(
            pool.map(
                lambda _: service(engine).complete(account, created[0].id, 1, "test"),
                range(2),
            )
        )
    assert completed[0] == completed[1] and completed[0].version == 2
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PlanItemModel)) == 1
        assert (
            session.scalar(select(func.count()).select_from(PlanCreationReceiptModel))
            == 1
        )


def test_isolation_optimistic_conflict_and_cascade(engine):
    from app.domain.planning import PlanConflictError, PlanIdempotencyConflictError

    account, other = _active_account(engine), _active_account(engine)
    svc = service(engine)
    created = svc.create(account, intent(), "same-key", "test")
    other_created = svc.create(other, intent(), "same-key", "test")
    assert created.id != other_created.id
    for operation in (
        lambda: svc.get(other, created.id, "test"),
        lambda: svc.update(other, created.id, 1, {"title": "Other"}, "test"),
        lambda: svc.complete(other, created.id, 1, "test"),
    ):
        with pytest.raises(PlanNotFoundError):
            operation()
    updated = svc.update(account, created.id, 1, {"title": "Updated"}, "test")
    assert updated.version == 2
    with pytest.raises(PlanConflictError):
        svc.update(account, created.id, 1, {"title": "Stale"}, "test")
    with pytest.raises(PlanIdempotencyConflictError):
        svc.create(
            account, PlanIntent("Different", NOW.date(), "UTC"), "same-key", "test"
        )
    from app.infrastructure.authentication.models import UserAccountModel

    with Session(engine) as session:
        session.delete(session.get(UserAccountModel, account))
        session.commit()
    with Session(engine) as session:
        assert session.get(PlanItemModel, created.id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(PlanCreationReceiptModel)
                .where(PlanCreationReceiptModel.user_account_id == account)
            )
            == 0
        )


def test_failed_completion_fully_rolls_back(engine):
    account = _active_account(engine)
    created = service(engine).create(account, intent(), "create", "test")

    class FailingUnit(SqlAlchemyPlanUnitOfWork):
        def commit(self):
            raise PlanUnavailableError("Commit failed")

    factory = sessionmaker(engine, expire_on_commit=False)
    svc = PlanService(lambda request_id: FailingUnit(factory), clock=lambda: NOW)
    with pytest.raises(PlanUnavailableError):
        svc.complete(account, created.id, 1, "test")
    assert service(engine).get(account, created.id, "test") == created


def test_constraints_and_upgrade_downgrade_metadata_parity(engine):
    from sqlalchemy.exc import IntegrityError

    account = _active_account(engine)
    created = service(engine).create(account, intent(), "create", "test")
    for changes in (
        {"title": " "},
        {"version": 0},
        {"completed_at": NOW, "is_priority": True},
        {"scheduled_end_at": NOW},
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    PlanItemModel.__table__.update()
                    .where(PlanItemModel.id == created.id)
                    .values(**changes)
                )
    with engine.connect() as connection:
        assert (
            compare_metadata(
                MigrationContext.configure(connection), RELATIONAL_METADATA
            )
            == []
        )
    assert TEST_URL is not None
    with bind_alembic_to_test_database(TEST_URL):
        command.downgrade(Config("alembic.ini"), "0005_authoritative_onboarding")
        command.upgrade(Config("alembic.ini"), "head")
    with engine.connect() as connection:
        assert (
            compare_metadata(
                MigrationContext.configure(connection), RELATIONAL_METADATA
            )
            == []
        )
