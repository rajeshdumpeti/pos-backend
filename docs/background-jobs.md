# Milestone 16 — Background Job Foundation

This is an internal PostgreSQL-backed foundation, not a notification engine or a continuously running queue service. API/domain operations depend on application repository/UoW ports; infrastructure supplies PostgreSQL and a deliberate single-run CLI. No API routes, Redis requirement, provider integration, startup task, polling loop, Inbox record or notification producer is added. Authentication email processing remains unchanged.

## Contract and lifecycle

`app/domain/jobs.py` defines validated intent, safe failures and the bounded retry policy. `app/application/jobs.py` defines repository/UoW/diagnostic ports, scheduling, cancellation, supersession and finite batch orchestration. SQLAlchemy models, repositories, session ownership and CLI composition remain in infrastructure. Domain/application import no FastAPI, SQLAlchemy, Psycopg, Redis or provider SDK.

Each durable job has UUID identity, an allow-listed type, optional owning account UUID, strict minimal JSONB payload, hashed deduplication scope and intent, UTC scheduled time, lifecycle state, attempt/max-attempt counts, worker UUID/lease expiry, safe failure category, and creation/update/first-start/completion timestamps. Payload, account and digests are excluded from the domain record repr.

States are `pending`, `running`, `retryable`, `succeeded`, `dead`, `cancelled`, `superseded`. Terminal states have completion timestamps and no lease. Only due pending/retryable rows or expired running leases are claim candidates. Success is terminal and cannot be claimed again. Attempts count leases, including leases lost to crashes, and are capped at five; callers may select a smaller positive maximum. First-start time is retained across attempts.

The only initial allow-listed type is `maintenance.jobs.cleanup`. Its exact payload is:

```text
{ cutoff_at: string /* aware ISO timestamp, normalized to UTC */, limit: integer /* 1..100 */ }
```

Unknown fields/types, naive instants, invalid keys and unbounded limits are rejected with fixed safe errors. The cutoff cannot be later than the scheduled execution time or the cleanup execution clock. There is deliberately no arbitrary payload or arbitrary job creation endpoint. No jobs are seeded/backfilled. Future job types need explicit domain validation, handler registration, persistence allow-list migration and tests before they can be scheduled.

## Scheduling, ownership and deduplication

`JobService.schedule` schedules one validated intent in an explicitly committed UoW. For future domain+job atomicity, construct `new_job(...)` and call `unit.jobs.schedule(intent)` inside the producer's own **composite UoW sharing the domain session**, then commit once. `SqlAlchemyJobRepository` accepts the existing session and never creates sessions or commits. Do not call the standalone scheduling service to open a nested independent transaction around an uncommitted domain change. A future producer-specific composite UoW is not implemented by this milestone.

The deduplication digest hashes owner scope (including a distinct system scope), type and caller-supplied stable key. The key uses a bounded ASCII grammar and is never stored or logged in plaintext. Intent digest includes canonical payload, original UTC schedule and maximum attempts. A unique database constraint and no-op upsert atomically return the existing logical job, including concurrent scheduling/cleanup races. Reusing a key for a changed intent raises a safe conflict and rolls back. Duplicate intent does not reset attempts, revive success/cancellation or rewrite history.

Account-owned reads/cancellations/cleanup always predicate the supplied trusted account UUID; another account sees no matching row. Null ownership means **system-owned jobs only**, not a wildcard. The trusted internal worker can claim across scopes, while handlers receive the original owning scope. Account deletion cascades owned jobs; cancellation of a running handler or reversal of committed work is not promised.

Deduplication lasts while the job row is retained. Explicit terminal cleanup ends that deduplication window. Producers must choose keys/cleanup horizons compatible with their own replay window and use domain-level uniqueness/idempotence for effects that require longer protection. There is no indefinite exactly-once claim or hidden deduplication receipt store.

## UTC and DST

Aware input timestamps are normalized to UTC and stored as timezone-aware PostgreSQL timestamps. Scheduling accepts exact instants; retries use elapsed UTC seconds. The foundation never chooses an account timezone, local reminder hour, ambiguous-time fold or DST-gap policy. Future domain producers must resolve IANA local-time intent before calling it. Both fall-back folds retain their distinct instants. It does not add fixed 24-hour local-day arithmetic.

## Claiming, leases and recovery

Claims use bounded PostgreSQL row locking with `FOR UPDATE SKIP LOCKED`, ordered by scheduled time then UUID. Default batch is 10, maximum 100, and exhausted expired rows count against the inspected batch bound. Claim changes, attempt increments and lease ownership commit atomically before handler execution. The default lease is 300 seconds; application construction permits 1..3600 seconds. Each handler's lease is renewed immediately before execution only if its prior lease remains live.

A live claim excludes other workers. Expired leases can be claimed with a new worker UUID and incremented attempt. Acknowledgements/renewals require matching job UUID, running state, worker UUID, attempt number and a still-live lease. Thus a previous attempt cannot acknowledge or alter a recovered lease, even if its handler finishes late. At the final attempt's expired lease, recovery marks the row dead with `lease_expired`; jobs cannot be stranded indefinitely or retried without a bound.

Execution is **at least once**. Fencing protects job-state acknowledgement, not arbitrary external side effects. A handler can finish its effect and crash before acknowledgement, or continue briefly after losing a lease. Every future handler must be idempotent and revalidate its domain facts; choose a batch size/lease duration appropriate to bounded handler runtime. No heartbeat thread, unsafe forced cancellation, exactly-once side-effect claim or distributed worker platform is introduced.

## Retries and failure categories

Only recognized `TransientJobFailure` and safe `PersistenceUnavailableError` handler failures retry, with categories `transient` and `dependency_unavailable`. Exponential backoff begins at 30 seconds and is capped at 300 seconds. With five total attempts, retry waits are 30, 60, 120 and 240 seconds; attempt five cannot schedule a sixth attempt. A database failure while claiming or confirming aborts that transaction; a retained lease is recovered later.

Validation, missing handlers, permanent failures and unexpected handler exceptions become terminal categories `invalid_payload`, `unsupported_type`, `permanent`, `handler_failure`. Raw exception text is neither persisted nor logged. Unexpected errors are not assumed transient. Invalid scheduling intent fails before insertion. Diagnostic failures cannot undo already confirmed state.

## Cancellation and supersession

Cancellation atomically affects only pending/retryable jobs in the owning scope. Repeated cancellation returns false without modifying terminal/running state. Supersession cancels the old pending/retryable row as `superseded` and schedules a validated replacement with a new dedupe key in one UoW. A conflict, missing/foreign old row, or an already leased/terminal old job aborts the entire operation, leaving no partial cancellation/replacement. Running handlers are not forcibly interrupted. Future notification invalidation must also check current source state; those producer rules remain deferred.

## Cleanup

There is **no invented automatic retention period**. A trusted internal maintenance intent supplies an explicit UTC cutoff and batch bound. Cleanup selects only terminal rows strictly older than that cutoff in the same owning scope, locks a bounded ordered set with SKIP LOCKED, then deletes those rows in the same transaction. Due, pending, retryable and running jobs are never deleted by this operation. The cleanup index supports owner/completion ordering; pending claiming, lease recovery and deduplication have separate required indexes.

Cleanup is repeat-safe per record. A repeated bounded batch may remove additional eligible old terminal rows, always within the same scope/cutoff and per-invocation limit. A crash before commit rolls it back; a crash after cleanup commit but before job acknowledgement can repeat it safely. Deduplication protection ends for deleted rows, as documented above. No request-time cleanup, unbounded scan/loop, Inbox retention duration or production cleanup jobs are fabricated.

## CLI and diagnostics

After an operator configures the existing approved database setting and applies migrations separately:

```sh
.venv/bin/python -m app.infrastructure.job_worker process-pending
.venv/bin/python -m app.infrastructure.job_worker process-pending 25
```

Every invocation processes one bounded batch and exits. No server startup drains the queue. A future deployment scheduler can invoke this command deliberately without changing application architecture. No local Redis, provider secrets or new environment variables are required. CLI imports do not connect; a valid database is required only when the command runs.

Successful execution prints only numeric `claimed`, `succeeded`, `retryable`, `dead`, `lease_lost` counts. Exit codes: 0 batch completed (possibly with recognized failures rescheduled); 1 invalid command/limit; 2 missing database configuration; 3 dead jobs or lost leases; 4 safe batch failure. Errors never echo arguments, SQL, connection details or raw exceptions. Engine disposal occurs in finally. Diagnostics allow only job UUID/type/state, bounded attempt, safe failure category, worker UUID and scheduled/completion timestamps. Account values, full payloads, keys, intent digests, emails, tokens and private references are absent.

PostgreSQL/driver failures use the established redacted persistence boundary. The UoW owns fresh session acquisition, commit, rollback and close; an uncommitted operation rolls back on exit. PostgreSQL engine construction retains hidden parameters. Repository code contains no session creation or commit.

## Persistence and validation

Migration `0007_background_jobs` follows `0006_daily_planning` with one linear head. It adds only `background_jobs`, its account FK, uniqueness/check constraints and three required partial indexes (due claim, expired lease recovery, scoped terminal cleanup). Downgrade removes the new table and its indexes. There is no backfill. Central Alembic metadata includes the new model. Existing Authentication metadata assertions are updated for the additional table; Authentication, Today and Plan behavior/contracts are unchanged.

The validation record and exact delivery status are in [milestone-16-validation.md](milestone-16-validation.md). Guarded tests in `tests/test_jobs_postgresql.py` reuse the existing `POS_TEST_DATABASE_URL` guard, which rejects anything except an explicitly authorized local `pos_test` database **before** migrations or cleanup. No `.env` file is read to discover it. Never point these tests at the development database. Migration/model parity also has an offline structural test; live catalog parity, concurrent claims/deduplication and live upgrade/downgrade require the guarded database.

## Founder verification and deferred work

1. Review the diff and the explicit replay/cleanup/at-least-once limits. Inspect offline migration output and the single Alembic head before authorizing a database migration.
2. Supply only the guarded local disposable PostgreSQL test configuration, then run `.venv/bin/pytest -q tests/test_jobs_postgresql.py` and the existing PostgreSQL regression suite. Confirm parallel scheduling, disjoint open claims, recovery fencing, retries, rollback, account boundaries and live metadata parity.
3. In an approved non-production verification environment, schedule an internal maintenance intent through the application port with an explicit cleanup cutoff, run one CLI batch, and inspect sanitized counts/state. Scheduling itself must be an explicit operator action; the CLI never seeds jobs.
4. Terminate a worker after claim and invoke a later batch after lease expiry. Confirm retry limits and stale-acknowledgement fencing. Verify cancellation/supersession before execution and terminal-only bounded cleanup.

Notification Inbox producer events, exact copy, domain preferences, local-time eligibility, occurrence keys, source invalidation and Inbox retention remain separate approval gates for Milestone 17A. Inbox records/APIs, notification producers, push delivery, device tokens, OS permission, frontend changes and a permanent scheduler are not implemented here.
