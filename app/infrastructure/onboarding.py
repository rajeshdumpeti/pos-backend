"""SQLAlchemy onboarding persistence and composition."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Never, cast
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.onboarding import (
    OnboardingDependencyUnavailableError,
    OnboardingStateConflictError,
    OnboardingService,
    OnboardingUnitOfWork,
)
from app.config import Settings
from app.domain.onboarding import (
    CorruptOnboardingStateError,
    OnboardingState,
)
from app.infrastructure.authentication.models import UserAccountModel
from app.infrastructure.authentication.repositories import (
    SqlAlchemyUserAccountRepository,
)
from app.infrastructure.database import (
    SessionFactory,
    create_database_engine,
    create_session_factory,
)
from app.infrastructure.database_diagnostics import (
    log_database_failure,
    raise_safe_database_error,
)
from app.infrastructure.onboarding_models import OnboardingStateModel
from app.infrastructure.onboarding_models import reconstruct_onboarding_state


LOGGER = logging.getLogger("pos.onboarding.persistence")


class SafeOnboardingDiagnostic:
    """Emit only fixed labels and bounded onboarding metadata."""

    _EVENTS = frozenset(
        {
            "onboarding_state_retrieved",
            "onboarding_state_created",
            "onboarding_state_updated",
            "onboarding_state_conflict",
            "onboarding_completed",
            "onboarding_completion_rejected",
            "onboarding_required",
        }
    )

    def emit(
        self,
        *,
        event: str,
        operation: str,
        outcome: str,
        request_id: str,
        account_id: UUID,
        version: int,
        priority_count: int,
        category: str | None = None,
    ) -> None:
        try:
            LOGGER.info(
                event if event in self._EVENTS else "onboarding_event",
                extra={
                    "operation": (
                        operation
                        if operation in {"get", "update", "complete", "gate"}
                        else "unknown"
                    ),
                    "outcome": (
                        outcome if outcome in {"succeeded", "rejected"} else "unknown"
                    ),
                    "category": (
                        category
                        if category in {None, "concurrency", "incomplete"}
                        else "unknown"
                    ),
                    "request_id": request_id if len(request_id) <= 64 else "redacted",
                    "account_id": str(account_id),
                    "contract_version": "1.0",
                    "version": max(0, version),
                    "priority_count": max(0, min(priority_count, 7)),
                },
            )
        except Exception:
            pass


def _record(model: OnboardingStateModel) -> OnboardingState:
    try:
        return reconstruct_onboarding_state(model)
    except CorruptOnboardingStateError as error:
        log_database_failure(
            LOGGER, operation="onboarding_reconstruct", category="corrupt_persistence"
        )
        raise_safe_database_error(
            OnboardingDependencyUnavailableError,
            "Persisted onboarding state is invalid",
            error,
        )


def _values(state: OnboardingState) -> dict[str, Any]:
    return {
        "user_account_id": state.user_account_id,
        "contract_version": state.contract_version,
        "version": state.version,
        "priority_life_areas": list(state.priority_life_areas),
        "unit_system": state.unit_system,
        "unit_system_confirmed": state.unit_system_confirmed,
        "currency": state.currency,
        "currency_confirmed": state.currency_confirmed,
        "goals": state.goals,
        "integration_preferences": state.integration_preferences,
        "notification_preferences": state.notification_preferences,
        "completed_at": state.completed_at,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


class SqlAlchemyOnboardingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, account_id: UUID) -> OnboardingState | None:
        try:
            model = self._session.get(OnboardingStateModel, account_id)
        except SQLAlchemyError as error:
            self._raise(error)
        return None if model is None else _record(model)

    def get_for_update(self, account_id: UUID) -> OnboardingState | None:
        try:
            model = self._session.execute(
                select(OnboardingStateModel)
                .where(OnboardingStateModel.user_account_id == account_id)
                .with_for_update()
            ).scalar_one_or_none()
        except SQLAlchemyError as error:
            self._raise(error)
        return None if model is None else _record(model)

    def add(self, state: OnboardingState) -> None:
        try:
            self._session.execute(insert(OnboardingStateModel).values(**_values(state)))
            self._session.flush()
        except IntegrityError as error:
            name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            if name == "pk_onboarding_states":
                raise_safe_database_error(
                    OnboardingStateConflictError,
                    "Onboarding state version conflicts",
                    error,
                )
            raise_safe_database_error(
                OnboardingDependencyUnavailableError,
                "Onboarding persistence constraint was rejected",
                error,
            )
        except SQLAlchemyError as error:
            self._raise(error)

    def update(self, state: OnboardingState, expected_version: int) -> bool:
        try:
            result = self._session.execute(
                update(OnboardingStateModel)
                .where(
                    OnboardingStateModel.user_account_id == state.user_account_id,
                    OnboardingStateModel.version == expected_version,
                )
                .values(
                    **{
                        key: value
                        for key, value in _values(state).items()
                        if key != "user_account_id"
                    }
                )
            )
            self._session.flush()
            return result.rowcount == 1
        except SQLAlchemyError as error:
            self._raise(error)

    @staticmethod
    def _raise(error: SQLAlchemyError) -> Never:
        raise_safe_database_error(
            OnboardingDependencyUnavailableError,
            "Onboarding persistence operation failed",
            error,
        )


class SqlAlchemyOnboardingAccountRepository(SqlAlchemyUserAccountRepository):
    def set_onboarding_required(
        self, account_id: UUID, required: bool, updated_at: datetime
    ) -> bool:
        try:
            result = self._session.execute(
                update(UserAccountModel)
                .where(UserAccountModel.id == account_id)
                .values(onboarding_required=required, updated_at=updated_at)
            )
            return result.rowcount == 1
        except SQLAlchemyError as error:
            raise_safe_database_error(
                OnboardingDependencyUnavailableError,
                "Account persistence operation failed",
                error,
            )


class SqlAlchemyOnboardingUnitOfWork:
    def __init__(
        self, session_factory: SessionFactory, *, request_id: str | None = None
    ) -> None:
        self._factory = session_factory
        self._request_id = request_id
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyOnboardingUnitOfWork":
        try:
            self._session = self._factory()
        except SQLAlchemyError as error:
            self._raise("onboarding_uow_start", error)
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        session = self._require()
        try:
            if exception is not None:
                session.rollback()
        except SQLAlchemyError as error:
            self._raise("onboarding_uow_rollback", error)
        finally:
            try:
                session.close()
            except SQLAlchemyError as error:
                self._raise("onboarding_uow_close", error)
            finally:
                self._session = None
        if isinstance(exception, SQLAlchemyError):
            self._raise("onboarding_uow_operation", exception)

    @property
    def onboarding(self) -> SqlAlchemyOnboardingRepository:
        return SqlAlchemyOnboardingRepository(self._require())

    @property
    def accounts(self) -> SqlAlchemyOnboardingAccountRepository:
        return SqlAlchemyOnboardingAccountRepository(self._require())

    def commit(self) -> None:
        try:
            self._require().commit()
        except SQLAlchemyError as error:
            self._raise("onboarding_uow_commit", error)

    def rollback(self) -> None:
        try:
            self._require().rollback()
        except SQLAlchemyError as error:
            self._raise("onboarding_uow_rollback", error)

    def _require(self) -> Session:
        if self._session is None:
            raise RuntimeError("Onboarding Unit of Work is not active")
        return self._session

    def _raise(self, operation: str, error: SQLAlchemyError) -> Never:
        log_database_failure(
            LOGGER,
            operation=operation,
            category="unavailable",
            request_id=self._request_id,
        )
        raise_safe_database_error(
            OnboardingDependencyUnavailableError,
            "Onboarding persistence transaction failed",
            error,
        )


@dataclass(frozen=True, slots=True)
class OnboardingComposition:
    service: OnboardingService
    engine: Engine


def compose_onboarding(
    settings: Settings, *, shared_engine: Engine | None = None
) -> OnboardingComposition | None:
    if settings.database_url is None:
        return None
    engine = shared_engine or create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    return OnboardingComposition(
        OnboardingService(
            cast(
                "Callable[[str], OnboardingUnitOfWork]",
                lambda request_id: SqlAlchemyOnboardingUnitOfWork(
                    factory, request_id=request_id
                ),
            ),
            diagnostic=SafeOnboardingDiagnostic(),
        ),
        engine,
    )
