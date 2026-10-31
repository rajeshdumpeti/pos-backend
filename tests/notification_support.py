"""Transactional shared-store adapters used only by Inbox tests."""

from copy import deepcopy
from dataclasses import replace
from app.application.notifications import (
    NotificationService,
    PlanNotificationIntegration,
)
from app.application.planning import PlanService
from app.domain.notifications import InboxUnavailableError
from tests.planning_support import Repository as Plans, fixture as planning_fixture
from tests.jobs_support import Repository as Jobs


class Inbox:
    def __init__(self, store):
        self.store = store

    def lock_account(self, owner):
        return owner in self.store.owners

    def preference(self, owner):
        return self.store.preferences.get(owner)

    def save_preference(self, value):
        self.store.preferences[value.user_account_id] = value

    def cancel_jobs(self, owner, kind, now, item_id=None, superseded=False):
        for key, row in list(self.store.rows.items()):
            if (
                row.user_account_id == owner
                and row.job_type == kind
                and row.state in {"pending", "retryable"}
                and (item_id is None or row.payload.get("plan_item_id") == str(item_id))
            ):
                self.store.rows[key] = replace(
                    row,
                    state="superseded" if superseded else "cancelled",
                    completed_at=now,
                    updated_at=now,
                )

    def insert(self, row):
        if any(
            old.user_account_id == row.user_account_id
            and old.occurrence_digest == row.occurrence_digest
            for old in self.store.notifications.values()
        ):
            return False
        self.store.notifications[row.id] = row
        return True

    def get(self, owner, identifier, now):
        row = self.store.notifications.get(identifier)
        return (
            row
            if row and row.user_account_id == owner and row.expires_at > now
            else None
        )

    def list(self, owner, source, now, boundary, limit):
        rows = [
            row
            for row in self.store.notifications.values()
            if row.user_account_id == owner
            and row.expires_at > now
            and (source == "all" or row.source == source)
            and (boundary is None or (row.created_at, row.id) < boundary)
        ]
        return sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)[
            :limit
        ]

    def count(self, owner, source, now):
        return sum(
            row.read_at is None for row in self.list(owner, source, now, None, 100000)
        )

    def mark(self, owner, identifier, now):
        row = self.get(owner, identifier, now)
        if row and row.read_at is None:
            row = replace(row, read_at=now, updated_at=now, version=row.version + 1)
            self.store.notifications[row.id] = row
        return row

    def mark_all(self, owner, source, now):
        rows = [
            r for r in self.list(owner, source, now, None, 100000) if r.read_at is None
        ]
        for row in rows:
            self.mark(owner, row.id, now)
        return len(rows)

    def cleanup(self, now, limit):
        rows = sorted(
            [r for r in self.store.notifications.values() if r.expires_at <= now],
            key=lambda r: (r.expires_at, r.id),
        )[:limit]
        for row in rows:
            del self.store.notifications[row.id]
        return len(rows)


class Unit:
    def __init__(self, store):
        self.store = store
        self.plans = Plans(store)
        self.jobs = Jobs(store)
        self.inbox = Inbox(store)

    def __enter__(self):
        self.store.lock.acquire()
        self.committed = False
        self.snapshot = deepcopy(
            (
                self.store.items,
                self.store.receipts,
                self.store.rows,
                self.store.notifications,
                self.store.preferences,
            )
        )
        return self

    def __exit__(self, kind, error, traceback):
        if error or not self.committed:
            self.rollback()
        self.store.lock.release()

    def commit(self):
        if self.store.fail_commit:
            raise InboxUnavailableError()
        self.committed = True

    def rollback(self):
        (
            self.store.items,
            self.store.receipts,
            self.store.rows,
            self.store.notifications,
            self.store.preferences,
        ) = self.snapshot


def fixture():
    owner, account, states, onboarding, store, _ = planning_fixture()
    from tests.planning_support import NOW

    store.rows = {}
    store.notifications = {}
    store.preferences = {}
    store.owners = {owner}
    clock = [NOW]

    def factory():
        return Unit(store)

    plans = PlanService(
        lambda _: factory(),
        clock=lambda: clock[0],
        integration=PlanNotificationIntegration(),
    )
    inbox = NotificationService(factory, clock=lambda: clock[0])
    return owner, account, onboarding, store, clock, plans, inbox
