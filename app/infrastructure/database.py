"""SQLAlchemy engine, session, transaction, and connectivity infrastructure."""

from collections.abc import Callable
from types import TracebackType

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.application import (
    PersistenceConfigurationError,
    PersistenceUnavailableError,
    ResourceRepository,
)


SessionFactory = Callable[[], Session]
RepositoryFactory = Callable[[Session], ResourceRepository]


def create_database_engine(database_url: SecretStr) -> Engine:
    """Build a synchronous PostgreSQL engine without opening a connection."""
    try:
        url = make_url(database_url.get_secret_value())
    except ArgumentError:
        raise PersistenceConfigurationError(
            "Database engine configuration is invalid"
        ) from None

    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif url.drivername != "postgresql+psycopg":
        raise PersistenceConfigurationError(
            "Database engine requires the synchronous PostgreSQL driver"
        )

    try:
        return create_engine(
            url,
            hide_parameters=True,
            pool_pre_ping=True,
        )
    except (ArgumentError, SQLAlchemyError):
        raise PersistenceConfigurationError(
            "Database engine configuration is invalid"
        ) from None


def dispose_database_engine(engine: Engine) -> None:
    """Release all pooled connections owned by an engine."""
    try:
        engine.dispose()
    except SQLAlchemyError:
        raise PersistenceUnavailableError(
            "Database engine disposal failed"
        ) from None


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the sole factory for isolated synchronous sessions."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def check_database_connectivity(engine: Engine) -> None:
    """Run one read-only connectivity probe without inspecting any schema."""
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        raise PersistenceUnavailableError(
            "Database connectivity check failed"
        ) from None

    if result != 1:
        raise PersistenceUnavailableError("Database connectivity check failed")


class SqlAlchemyUnitOfWork:
    """Own one SQLAlchemy session and its transaction mechanics."""

    def __init__(
        self,
        session_factory: SessionFactory,
        repository_factory: RepositoryFactory,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._session: Session | None = None
        self._repository: ResourceRepository | None = None

    @property
    def resources(self) -> ResourceRepository:
        if self._repository is None:
            raise RuntimeError("Unit of work is not active")
        return self._repository

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        if self._session is not None:
            raise RuntimeError("Unit of work is already active")

        try:
            self._session = self._session_factory()
            self._repository = self._repository_factory(self._session)
        except SQLAlchemyError:
            self._close_after_failed_entry()
            raise PersistenceUnavailableError(
                "Database session could not be started"
            ) from None
        except BaseException:
            self._close_after_failed_entry()
            raise

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        session = self._require_session()
        cleanup_failed = False

        try:
            if exception is not None:
                session.rollback()
        except SQLAlchemyError:
            cleanup_failed = True
        finally:
            try:
                session.close()
            except SQLAlchemyError:
                cleanup_failed = True
            self._session = None
            self._repository = None

        if cleanup_failed or (
            exception_type is not None
            and issubclass(exception_type, SQLAlchemyError)
        ):
            raise PersistenceUnavailableError(
                "Database operation could not be completed"
            ) from None

    def commit(self) -> None:
        try:
            self._require_session().commit()
        except SQLAlchemyError:
            raise PersistenceUnavailableError(
                "Database operation could not be completed"
            ) from None

    def rollback(self) -> None:
        try:
            self._require_session().rollback()
        except SQLAlchemyError:
            raise PersistenceUnavailableError(
                "Database operation could not be completed"
            ) from None

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        return self._session

    def _close_after_failed_entry(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except SQLAlchemyError:
                pass
        self._session = None
        self._repository = None
