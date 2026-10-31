"""Inbox SQL adapters. Account serialization protects counts and producer revalidation."""

from dataclasses import asdict
from sqlalchemy import and_, case, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from app.domain.planning import utc
from app.domain.notifications import (
    AccountTimezone,
    Notification,
    InboxUnavailableError,
)
from app.infrastructure.authentication.models import UserAccountModel
from app.infrastructure.notification_models import (
    AccountTimezoneModel as Preference,
    NotificationModel as Model,
)
from app.infrastructure.job_models import BackgroundJobModel
from app.infrastructure.jobs import SqlAlchemyJobUnitOfWork
from app.infrastructure.planning import SqlAlchemyPlanRepository
from app.infrastructure.database_diagnostics import raise_safe_database_error


def record(model) -> Notification:
    values = {
        column.name: getattr(model, column.name) for column in Model.__table__.columns
    }
    for name in ("created_at", "expires_at", "read_at", "updated_at"):
        if values[name] is not None:
            values[name] = utc(values[name])
    return Notification(**values)


class SqlAlchemyInboxRepository:
    def __init__(self, session):
        self._session = session

    def lock_account(self, owner):
        row = self._session.scalar(
            select(UserAccountModel)
            .where(UserAccountModel.id == owner)
            .with_for_update()
        )
        return (
            row is not None
            and row.status == "active"
            and row.email_verified_at is not None
        )

    def preference(self, owner):
        row = self._session.get(Preference, owner)
        return (
            None
            if row is None
            else AccountTimezone(
                row.user_account_id, row.timezone, utc(row.updated_at), row.version
            )
        )

    def save_preference(self, value):
        self._session.execute(
            insert(Preference)
            .values(**asdict(value))
            .on_conflict_do_update(
                index_elements=[Preference.user_account_id],
                set_={
                    "timezone": value.timezone,
                    "version": value.version,
                    "updated_at": value.updated_at,
                },
            )
        )

    def cancel_jobs(self, owner, kind, now, item_id=None, superseded=False):
        query = update(BackgroundJobModel).where(
            BackgroundJobModel.user_account_id == owner,
            BackgroundJobModel.job_type == kind,
            BackgroundJobModel.state.in_(("pending", "retryable")),
        )
        if item_id is not None:
            query = query.where(
                BackgroundJobModel.payload["plan_item_id"].astext == str(item_id)
            )
        self._session.execute(
            query.values(
                state="superseded" if superseded else "cancelled",
                updated_at=now,
                completed_at=now,
            )
        )

    def insert(self, row):
        result = self._session.execute(
            insert(Model)
            .values(**asdict(row))
            .on_conflict_do_nothing(
                index_elements=[Model.user_account_id, Model.occurrence_digest]
            )
            .returning(Model.id)
        )
        return result.scalar_one_or_none() is not None

    def scope(self, owner, source, now):
        terms = [Model.user_account_id == owner, Model.expires_at > now]
        if source != "all":
            terms.append(Model.source == source)
        return and_(*terms)

    def get(self, owner, identifier, now):
        row = self._session.scalar(
            select(Model).where(self.scope(owner, "all", now), Model.id == identifier)
        )
        return record(row) if row else None

    def list(self, owner, source, now, boundary, limit):
        query = select(Model).where(self.scope(owner, source, now))
        if boundary:
            query = query.where(tuple_(Model.created_at, Model.id) < tuple_(*boundary))
        return [
            record(row)
            for row in self._session.scalars(
                query.order_by(Model.created_at.desc(), Model.id.desc()).limit(limit)
            )
        ]

    def count(self, owner, source, now):
        return self._session.scalar(
            select(func.count())
            .select_from(Model)
            .where(self.scope(owner, source, now), Model.read_at.is_(None))
        )

    def mark(self, owner, identifier, now):
        first = Model.read_at.is_(None)
        row = self._session.scalar(
            update(Model)
            .where(self.scope(owner, "all", now), Model.id == identifier)
            .values(
                read_at=func.coalesce(Model.read_at, now),
                updated_at=case((first, now), else_=Model.updated_at),
                version=Model.version + case((first, 1), else_=0),
            )
            .returning(Model)
            .execution_options(populate_existing=True)
        )
        return record(row) if row else None

    def mark_all(self, owner, source, now):
        return self._session.execute(
            update(Model)
            .where(self.scope(owner, source, now), Model.read_at.is_(None))
            .values(read_at=now, updated_at=now, version=Model.version + 1)
        ).rowcount

    def cleanup(self, now, limit):
        from app.domain.jobs import bounded

        bounded(limit)
        # Observe a bounded candidate set, then take account locks before row locks,
        # matching every Inbox/producer transaction. Busy accounts are skipped.
        candidates = self._session.execute(
            select(Model.id, Model.user_account_id)
            .where(Model.expires_at <= now)
            .order_by(Model.expires_at, Model.id)
            .limit(limit)
        ).all()
        if not candidates:
            return 0
        owners = list(
            self._session.scalars(
                select(UserAccountModel.id)
                .where(UserAccountModel.id.in_({owner for _, owner in candidates}))
                .order_by(UserAccountModel.id)
                .with_for_update(skip_locked=True)
            )
        )
        if not owners:
            return 0
        ids = (
            select(Model.id)
            .where(
                Model.id.in_([identifier for identifier, _ in candidates]),
                Model.user_account_id.in_(owners),
                Model.expires_at <= now,
            )
            .order_by(Model.expires_at, Model.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return self._session.execute(delete(Model).where(Model.id.in_(ids))).rowcount


class SqlAlchemyInboxUnitOfWork(SqlAlchemyJobUnitOfWork):
    @property
    def inbox(self):
        return SqlAlchemyInboxRepository(self._require())

    @property
    def plans(self):
        return SqlAlchemyPlanRepository(self._require())

    def _raise(self, error):
        raise_safe_database_error(
            InboxUnavailableError, "Notification persistence is unavailable", error
        )


def compose_notifications(engine):
    from app.application.notifications import NotificationService
    from app.infrastructure.database import create_session_factory

    sessions = create_session_factory(engine)
    return NotificationService(lambda: SqlAlchemyInboxUnitOfWork(sessions))
