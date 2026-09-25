"""Guarded PostgreSQL persistence and first-write concurrency coverage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.application.onboarding import (
    OnboardingDependencyUnavailableError,
    OnboardingService,
    OnboardingStateConflictError,
    OnboardingUpdate,
)
from app.infrastructure.authentication.models import UserAccountModel
from app.infrastructure.database import create_database_engine, dispose_database_engine
from app.infrastructure.onboarding import (
    OnboardingStateModel,
    SqlAlchemyOnboardingAccountRepository,
    SqlAlchemyOnboardingRepository,
    SqlAlchemyOnboardingUnitOfWork,
)
from app.domain.onboarding import OnboardingState
from app.infrastructure.relational import RELATIONAL_METADATA
from tests.postgresql_support import upgrade_test_database


TEST_URL = os.environ.get("POS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="authorized PostgreSQL test URL not configured"
)
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


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


def _active_account(engine):
    account_id = uuid4()
    with Session(engine) as session:
        session.add(
            UserAccountModel(
                id=account_id,
                status="active",
                display_name="Onboarding Test",
                password_hash=None,
                email_verified_at=NOW,
                onboarding_required=True,
                authentication_epoch=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
    return account_id


def _service(engine) -> OnboardingService:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return OnboardingService(
        lambda request_id: SqlAlchemyOnboardingUnitOfWork(
            factory, request_id=request_id
        ),
        clock=lambda: NOW,
    )


def test_onboarding_persists_completes_and_updates_account_atomically(engine):
    account_id = _active_account(engine)
    service = _service(engine)
    updated = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["health"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
            },
        ),
        "postgres-onboarding",
    )
    completed = service.complete(
        account_id, updated.version, "postgres-onboarding-complete"
    )

    with Session(engine) as session:
        persisted = session.get(OnboardingStateModel, account_id)
        account = session.get(UserAccountModel, account_id)
        assert persisted is not None and persisted.completed_at == NOW
        assert account is not None and account.onboarding_required is False
    assert completed.status == "completed"


def test_concurrent_first_insert_preserves_one_row_per_account(engine):
    account_id = _active_account(engine)

    def insert_once(index: int) -> str:
        try:
            _service(engine).update(
                account_id,
                OnboardingUpdate(
                    0,
                    {"priority_life_areas": ["health" if index == 0 else "focus"]},
                ),
                f"postgres-first-write-{index}",
            )
            return "created"
        except OnboardingStateConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(insert_once, range(2)))
    assert sorted(outcomes) == ["conflict", "created"]
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(OnboardingStateModel)) == 1
        )


def test_jsonb_decimal_round_trip_and_account_delete_cascade(engine):
    account_id = _active_account(engine)
    state = _service(engine).update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["health", "money"],
                "goals": {
                    "target_weight": {"value": "72.50", "unit": "kg"},
                    "budget_target": {"amount": "1250.25", "currency": "USD"},
                },
                "currency": "USD",
                "currency_confirmed": True,
                "integration_preferences": {"calendar": "deferred"},
                "notification_preferences": {"water": "enabled"},
            },
        ),
        "postgres-round-trip",
    )
    persisted = _service(engine).get(account_id, "postgres-round-trip-get")
    assert persisted.goals == state.goals
    assert persisted.integration_preferences == {"calendar": "deferred"}
    assert persisted.notification_preferences == {"water": "enabled"}

    with Session(engine) as session:
        account = session.get(UserAccountModel, account_id)
        assert account is not None
        session.delete(account)
        session.commit()
    with Session(engine) as session:
        assert session.get(OnboardingStateModel, account_id) is None


def test_concurrent_updates_allow_exactly_one_version_advance(engine):
    account_id = _active_account(engine)
    service = _service(engine)
    initial = service.update(
        account_id,
        OnboardingUpdate(0, {"priority_life_areas": ["health"]}),
        "postgres-initial",
    )

    def update_once(index: int) -> str:
        try:
            _service(engine).update(
                account_id,
                OnboardingUpdate(
                    initial.version,
                    {"priority_life_areas": ["health", "focus", "money"][: index + 2]},
                ),
                f"postgres-update-{index}",
            )
            return "updated"
        except OnboardingStateConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update_once, range(2)))
    assert sorted(outcomes) == ["conflict", "updated"]
    assert _service(engine).get(account_id, "postgres-final").version == 2


def test_concurrent_completion_is_idempotent(engine):
    account_id = _active_account(engine)
    service = _service(engine)
    ready = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["health"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
            },
        ),
        "postgres-ready",
    )

    def complete_once(index: int):
        return _service(engine).complete(
            account_id, ready.version, f"postgres-complete-{index}"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = list(pool.map(complete_once, range(2)))
    assert {state.status for state in completed} == {"completed"}
    assert {state.version for state in completed} == {ready.version + 1}


def test_completion_projection_failure_rolls_back_both_changes(engine):
    account_id = _active_account(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    class FailingProjectionRepository(SqlAlchemyOnboardingAccountRepository):
        def set_onboarding_required(self, account_id, required, updated_at):
            return False

    class FailingProjectionUnitOfWork(SqlAlchemyOnboardingUnitOfWork):
        @property
        def accounts(self):
            return FailingProjectionRepository(self._require())

    service = OnboardingService(
        lambda request_id: FailingProjectionUnitOfWork(factory, request_id=request_id),
        clock=lambda: NOW,
    )
    ready = service.update(
        account_id,
        OnboardingUpdate(
            0,
            {
                "priority_life_areas": ["health"],
                "unit_system": "metric",
                "unit_system_confirmed": True,
                "currency": "USD",
                "currency_confirmed": True,
            },
        ),
        "postgres-rollback-ready",
    )
    with pytest.raises(OnboardingDependencyUnavailableError):
        service.complete(account_id, ready.version, "postgres-rollback")

    with Session(engine) as session:
        persisted = session.get(OnboardingStateModel, account_id)
        account = session.get(UserAccountModel, account_id)
        assert persisted is not None and persisted.completed_at is None
        assert persisted.version == ready.version
        assert account is not None and account.onboarding_required is True


@pytest.mark.parametrize(
    "changes",
    [
        {"priority_life_areas": []},
        {"unit_system": None},
        {"unit_system_confirmed": False},
        {"currency": None},
        {"currency_confirmed": False},
    ],
)
def test_database_rejects_completed_state_without_required_facts(engine, changes):
    account_id = _active_account(engine)
    values = {
        "user_account_id": account_id,
        "contract_version": "1.0",
        "version": 1,
        "priority_life_areas": ["health"],
        "unit_system": "metric",
        "unit_system_confirmed": True,
        "currency": "USD",
        "currency_confirmed": True,
        "goals": {},
        "integration_preferences": {},
        "notification_preferences": {},
        "completed_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(OnboardingStateModel.__table__.insert().values(**values))


def test_corrupt_jsonb_reconstruction_fails_safely(engine):
    account_id = _active_account(engine)
    state = _service(engine).update(
        account_id,
        OnboardingUpdate(0, {"priority_life_areas": ["health"]}),
        "postgres-corrupt-seed",
    )
    with engine.begin() as connection:
        connection.execute(
            OnboardingStateModel.__table__.update()
            .where(OnboardingStateModel.user_account_id == account_id)
            .values(goals=[])
        )
    with pytest.raises(OnboardingDependencyUnavailableError):
        _service(engine).get(account_id, "postgres-corrupt-read")
    assert state.version == 1


def test_foreign_key_integrity_failure_is_not_misclassified_as_conflict(engine):
    state = OnboardingState(
        uuid4(),
        1,
        priority_life_areas=("health",),
        created_at=NOW,
        updated_at=NOW,
    )
    with Session(engine) as session:
        repository = SqlAlchemyOnboardingRepository(session)
        with pytest.raises(OnboardingDependencyUnavailableError):
            repository.add(state)
        session.rollback()
