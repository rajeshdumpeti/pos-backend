"""Authentication Unit of Work with explicit transaction and repository ownership."""

import logging
from types import TracebackType

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.authentication_persistence import (
    AuthenticationPersistenceUnavailableError,
)
from app.infrastructure.authentication.repositories import (
    SqlAlchemyAuthenticationAttemptStateRepository,
    SqlAlchemyAuthenticationEmailOutboxRepository,
    SqlAlchemyEmailIdentityRepository,
    SqlAlchemyEmailVerificationChallengeRepository,
    SqlAlchemyExternalProviderIdentityRepository,
    SqlAlchemyPasswordResetChallengeRepository,
    SqlAlchemyRefreshRotationReceiptRepository,
    SqlAlchemyRefreshSessionRepository,
    SqlAlchemySecurityEventRepository,
    SqlAlchemyUserAccountRepository,
)
from app.infrastructure.database import SessionFactory
from app.infrastructure.database_diagnostics import (
    log_database_failure,
    raise_safe_database_error,
)


LOGGER = logging.getLogger("pos.authentication.persistence")


class SqlAlchemyAuthenticationUnitOfWork:
    """Own one session, transaction, and cohesive authentication repositories."""

    def __init__(
        self, session_factory: SessionFactory, *, request_id: str | None = None
    ) -> None:
        self._session_factory = session_factory
        self._request_id = request_id
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyAuthenticationUnitOfWork":
        if self._session is not None:
            raise RuntimeError("Authentication Unit of Work is already active")
        try:
            self._session = self._session_factory()
        except SQLAlchemyError as error:
            self._log_failure("authentication_uow_start", "unavailable")
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence transaction could not start",
                error,
            )
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        session = self._require_session()
        try:
            if exception is not None:
                session.rollback()
        except SQLAlchemyError as error:
            self._log_failure("authentication_uow_rollback", "unavailable")
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence rollback failed",
                error,
            )
        finally:
            try:
                session.close()
            except SQLAlchemyError as error:
                self._log_failure("authentication_uow_close", "unavailable")
                raise_safe_database_error(
                    AuthenticationPersistenceUnavailableError,
                    "Authentication persistence cleanup failed",
                    error,
                )
            finally:
                self._session = None

        if exception_type is not None and issubclass(exception_type, SQLAlchemyError):
            self._log_failure("authentication_uow_operation", "unexpected")
            assert exception is not None
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence operation failed",
                exception,
            )

    def commit(self) -> None:
        try:
            self._require_session().commit()
        except SQLAlchemyError as error:
            self._log_failure("authentication_uow_commit", "unavailable")
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence commit failed",
                error,
            )

    def rollback(self) -> None:
        try:
            self._require_session().rollback()
        except SQLAlchemyError as error:
            self._log_failure("authentication_uow_rollback", "unavailable")
            raise_safe_database_error(
                AuthenticationPersistenceUnavailableError,
                "Authentication persistence rollback failed",
                error,
            )

    @property
    def accounts(self) -> SqlAlchemyUserAccountRepository:
        return SqlAlchemyUserAccountRepository(self._require_session())

    @property
    def email_identities(self) -> SqlAlchemyEmailIdentityRepository:
        return SqlAlchemyEmailIdentityRepository(self._require_session())

    @property
    def provider_identities(self) -> SqlAlchemyExternalProviderIdentityRepository:
        return SqlAlchemyExternalProviderIdentityRepository(self._require_session())

    @property
    def verification_challenges(self) -> SqlAlchemyEmailVerificationChallengeRepository:
        return SqlAlchemyEmailVerificationChallengeRepository(self._require_session())

    @property
    def password_reset_challenges(self) -> SqlAlchemyPasswordResetChallengeRepository:
        return SqlAlchemyPasswordResetChallengeRepository(self._require_session())

    @property
    def refresh_sessions(self) -> SqlAlchemyRefreshSessionRepository:
        return SqlAlchemyRefreshSessionRepository(self._require_session())

    @property
    def refresh_receipts(self) -> SqlAlchemyRefreshRotationReceiptRepository:
        return SqlAlchemyRefreshRotationReceiptRepository(self._require_session())

    @property
    def attempt_states(self) -> SqlAlchemyAuthenticationAttemptStateRepository:
        return SqlAlchemyAuthenticationAttemptStateRepository(self._require_session())

    @property
    def security_events(self) -> SqlAlchemySecurityEventRepository:
        return SqlAlchemySecurityEventRepository(self._require_session())

    @property
    def email_outbox(self) -> SqlAlchemyAuthenticationEmailOutboxRepository:
        return SqlAlchemyAuthenticationEmailOutboxRepository(self._require_session())

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Authentication Unit of Work is not active")
        return self._session

    def _log_failure(self, operation: str, category: str) -> None:
        log_database_failure(
            LOGGER,
            operation=operation,
            category=category,
            request_id=self._request_id,
        )
