"""Transactional in-memory test double, never used by production composition."""

from copy import deepcopy
from dataclasses import replace
from app.application.jobs import Claim
from app.domain.jobs import JobConflictError, TERMINAL


class Store:
    def __init__(self):
        self.rows = {}
        self.commits = 0
        self.rollbacks = 0

    def unit(self):
        return Unit(self)


class Unit:
    def __init__(self, store):
        self.store = store
        self.jobs = Repository(store)

    def __enter__(self):
        self.before = deepcopy(self.store.rows)
        self.committed = False
        return self

    def __exit__(self, kind, error, traceback):
        if error or not self.committed:
            self.store.rows = self.before
            self.store.rollbacks += 1

    def commit(self):
        self.committed = True
        self.store.commits += 1

    def rollback(self):
        self.store.rows = self.before
        self.store.rollbacks += 1


class Repository:
    def __init__(self, store):
        self.store = store

    def schedule(self, job):
        for row in self.store.rows.values():
            if row.deduplication_digest == job.deduplication_digest:
                if row.intent_digest != job.intent_digest:
                    raise JobConflictError()
                return row
        self.store.rows[job.id] = job
        return job

    def get(self, job_id, account_id):
        row = self.store.rows.get(job_id)
        return row if row and row.user_account_id == account_id else None

    def claim(self, now, owner, lease, limit):
        jobs, exhausted = [], 0
        due = [
            row
            for row in self.store.rows.values()
            if (row.state in {"pending", "retryable"} and row.scheduled_at <= now)
            or (row.state == "running" and row.lease_expires_at <= now)
        ]
        for row in sorted(due, key=lambda row: (row.scheduled_at, row.id))[:limit]:
            if row.attempts >= row.max_attempts:
                self.store.rows[row.id] = replace(
                    row,
                    state="dead",
                    completed_at=now,
                    updated_at=now,
                    failure_category="lease_expired",
                    lease_owner=None,
                    lease_expires_at=None,
                )
                exhausted += 1
                continue
            row = replace(
                row,
                state="running",
                attempts=row.attempts + 1,
                started_at=row.started_at or now,
                updated_at=now,
                lease_owner=owner,
                lease_expires_at=now + lease,
            )
            self.store.rows[row.id] = row
            jobs.append(row)
        return Claim(tuple(jobs), exhausted)

    def current(self, job, now):
        row = self.store.rows[job.id]
        return (
            row.state == "running"
            and row.lease_owner == job.lease_owner
            and row.attempts == job.attempts
            and row.lease_expires_at > now
        )

    def renew(self, job, now, lease):
        if not self.current(job, now):
            return False
        self.store.rows[job.id] = replace(
            self.store.rows[job.id], lease_expires_at=now + lease
        )
        return True

    def finish(self, job, now, category, retry_at):
        if not self.current(job, now):
            return None
        row = replace(
            self.store.rows[job.id],
            state=(
                "succeeded" if category is None else "retryable" if retry_at else "dead"
            ),
            failure_category=category,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=now,
            completed_at=None if retry_at else now,
            scheduled_at=retry_at or job.scheduled_at,
        )
        self.store.rows[job.id] = row
        return row

    def cancel(self, job_id, account_id, now, superseded=False):
        row = self.get(job_id, account_id)
        if not row or row.state not in {"pending", "retryable"}:
            return False
        self.store.rows[job_id] = replace(
            row,
            state="superseded" if superseded else "cancelled",
            updated_at=now,
            completed_at=now,
        )
        return True

    def cleanup(self, account_id, before, limit):
        rows = [
            row
            for row in self.store.rows.values()
            if row.user_account_id == account_id
            and row.state in TERMINAL
            and row.completed_at < before
        ]
        for row in sorted(rows, key=lambda row: (row.completed_at, row.id))[:limit]:
            del self.store.rows[row.id]
        return min(len(rows), limit)
