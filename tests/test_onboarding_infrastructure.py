from unittest.mock import Mock
from sqlalchemy.exc import IntegrityError, OperationalError
from types import SimpleNamespace

import pytest

from app.infrastructure.onboarding import (
    OnboardingStateModel,
    SqlAlchemyOnboardingRepository,
    SqlAlchemyOnboardingUnitOfWork,
)
from app.application.onboarding import (
    OnboardingDependencyUnavailableError,
    OnboardingStateConflictError,
)
from app.application.authentication_persistence import (
    AuthenticationPersistenceUnavailableError,
)
from app.infrastructure.authentication.repositories import (
    SqlAlchemyUserAccountRepository,
)
from app.infrastructure.database_diagnostics import format_safe_database_traceback
from app.domain.onboarding import OnboardingState
from datetime import UTC, datetime
from uuid import uuid4


def test_onboarding_model_enforces_account_ownership_and_core_checks():
    table = OnboardingStateModel.__table__
    assert list(table.primary_key.columns.keys()) == ["user_account_id"]
    foreign_key = next(iter(table.foreign_key_constraints))
    assert foreign_key.ondelete == "CASCADE"
    purposes = {constraint.info.get("purpose") for constraint in table.constraints}
    assert {
        "version_positive",
        "valid_unit_system",
        "valid_currency",
        "unit_confirmation_has_value",
        "currency_confirmation_has_value",
        "completed_state_has_required_facts",
    } <= purposes


def test_onboarding_unit_of_work_owns_commit_rollback_and_close():
    session = Mock()
    unit = SqlAlchemyOnboardingUnitOfWork(lambda: session)
    with unit:
        unit.commit()
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()

    failing_session = Mock()
    with pytest.raises(RuntimeError, match="failure"):
        with SqlAlchemyOnboardingUnitOfWork(lambda: failing_session):
            raise RuntimeError("failure")
    failing_session.rollback.assert_called_once_with()
    failing_session.close.assert_called_once_with()


def test_expected_first_write_conflict_redacts_private_driver_details():
    private = "SQL SELECT private_table password=sentinel-secret"
    original = RuntimeError(private)
    original.diag = Mock(constraint_name="pk_onboarding_states")
    session = Mock()
    session.execute.side_effect = IntegrityError("statement", {}, original)
    repository = SqlAlchemyOnboardingRepository(session)
    state = OnboardingState(
        uuid4(), 1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    with pytest.raises(OnboardingStateConflictError) as caught:
        repository.add(state)
    rendered = format_safe_database_traceback(caught.value)
    assert private not in rendered
    assert "sentinel-secret" not in rendered


def test_unrelated_integrity_failure_is_safe_and_not_a_concurrency_conflict():
    private = "postgresql://private:credential@host/db SQL sentinel"
    original = RuntimeError(private)
    original.diag = Mock(
        constraint_name="fk_onboarding_states__user_account_id__user_accounts"
    )
    session = Mock()
    session.execute.side_effect = IntegrityError(
        "statement", {"private": True}, original
    )
    repository = SqlAlchemyOnboardingRepository(session)
    state = OnboardingState(
        uuid4(), 1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    with pytest.raises(OnboardingDependencyUnavailableError) as caught:
        repository.add(state)
    rendered = format_safe_database_traceback(caught.value)
    assert private not in str(caught.value)
    assert private not in repr(caught.value)
    assert private not in rendered
    assert "credential" not in rendered


def test_unexpected_database_failure_is_translated_safely():
    private = "password=sentinel-secret SQL SELECT private"
    session = Mock()
    session.get.side_effect = OperationalError("statement", {}, RuntimeError(private))
    repository = SqlAlchemyOnboardingRepository(session)
    with pytest.raises(OnboardingDependencyUnavailableError) as caught:
        repository.get(uuid4())
    assert private not in format_safe_database_traceback(caught.value)
    assert "sentinel-secret" not in repr(caught.value)


def test_non_object_jsonb_and_invalid_completion_reconstruct_as_safe_failure(caplog):
    account_id = uuid4()
    base = {
        "user_account_id": account_id,
        "contract_version": "1.0",
        "version": 1,
        "priority_life_areas": ["health"],
        "unit_system": "metric",
        "unit_system_confirmed": True,
        "currency": "USD",
        "currency_confirmed": True,
        "goals": [],
        "integration_preferences": {},
        "notification_preferences": {},
        "completed_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    for changes in (
        {},
        {
            "goals": {},
            "priority_life_areas": [],
            "completed_at": datetime.now(UTC),
        },
    ):
        session = Mock()
        session.get.return_value = SimpleNamespace(**{**base, **changes})
        repository = SqlAlchemyOnboardingRepository(session)
        with pytest.raises(OnboardingDependencyUnavailableError) as caught:
            repository.get(account_id)
        assert str(caught.value) == "Persisted onboarding state is invalid"
    assert "health" not in caplog.text


def test_authentication_reader_rejects_logically_invalid_completion():
    account_id = uuid4()
    session = Mock()
    session.get.return_value = SimpleNamespace(
        user_account_id=account_id,
        contract_version="1.0",
        version=1,
        priority_life_areas=[],
        unit_system=None,
        unit_system_confirmed=False,
        currency=None,
        currency_confirmed=False,
        goals={},
        integration_preferences={},
        notification_preferences={},
        completed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repository = SqlAlchemyUserAccountRepository(session)
    with pytest.raises(AuthenticationPersistenceUnavailableError) as caught:
        repository.get_onboarding_state(account_id)
    assert str(caught.value) == "Authentication persistence operation failed"
    assert "priority_life_areas" not in format_safe_database_traceback(caught.value)
