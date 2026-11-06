# Milestone 17A — Implementation validation

## Starting state and inspected evidence

Branch `feat/backend-notification-inbox`; clean HEAD `367e1cc4952dc385dcd2f5815955ef09b5af122e`, equal to local `origin/develop` (0 ahead/0 behind). Required Plan delivery `146df660844019ff0052ccd05201e860deb48360` is an ancestor. Milestone 16 and one linear Alembic head `0007_background_jobs` were present. Baseline database-independent suite: **510 passed, 64 skipped in 11.65s**. No unrelated changes or branch operation were needed.

Reviewed the founder-approved replacement Milestone 17A brief (including exact producer matrix, account timezone and 90-day retention), governance manifest/charter, existing architecture and data access, authentication/account/onboarding gate, private-route error/request-ID/cache handling, Today/Plan contracts and mutations, job ports/worker/diagnostics/CLI, central metadata/migrations and guarded PostgreSQL isolation helpers. The brief resolves the previously missing product decisions; no screenshot-derived producer policy was invented.

## Implementation and contracts

Canonical architecture, policies, producer matrix, operations and verification:
[notification-inbox.md](notification-inbox.md). Exact TypeScript-style HTTP
contracts, enums and integration semantics:
[frontend-notification-inbox-handoff.md](frontend-notification-inbox-handoff.md).
Synthetic UI/test-only states:
[notification-inbox-v1-fixtures.json](examples/notification-inbox-v1-fixtures.json).
These are not runtime seeds or production fallbacks.

Delivered five endpoints: account timezone PUT; Inbox list; full-scope unread count; idempotent read-one; scoped atomic read-all. Inbox contract is 1.0 and Today/Plan remain 1.0. Named closed schemas and unique operation IDs are generated from the application. No arbitrary-create or job admin endpoint exists. Timezone synchronization requires authenticated active verified account access without onboarding completion; Inbox additionally reuses the completed-onboarding product boundary. Requests never accept an owning account ID.

Account timezone is stored separately from credentials/onboarding. Literal-identical writes preserve version/timestamp; changed values increment version and atomically replace pending Today intent. Daily occurrences use IANA local calendar 18:00, skipping invalid local dates and respecting DST, with no guessed timezone. Delayed same-day evaluations may run after 18:00; stale old-date jobs advance without creating yesterday's snapshot, and old timezone versions no-op. Plan reminders schedule minus 15 minutes or immediately within the window, revalidate owned source/version/occurrence and cancel/supersede pending work through the Plan transaction. Both producers use approved deterministic historical copy and database-backed occurrence uniqueness. Only source references, dates, timezone and versions enter job payloads.

History, grouping and unread counts are independent of OS permission. Grouping uses created_at in requested IANA timezone; source filters are distinct from Today/Earlier groups. SQL keyset pagination orders creation time/UUID descending with a default 50/max 100, account/filter/timezone context and an owned retained boundary. Counts cover all retained matching unread records. Read-one preserves first read_at and returns account-wide count; read-all returns scope count and number changed. Source navigation uses allow-listed actions and current owned Plan retrieval; missing targets strip references while retaining readable history. Logical expiration is exactly 90 elapsed UTC days, and global maintenance deletes only a bounded expired candidate set with account-before-row SKIP LOCKED order. No cleanup occurs in requests.

No source deletion/cancellation API exists, so none was added. Missing/deleted sources are revalidated safely and old history remains; future deletion/cancellation mutations must use the shared cancellation boundary. No Details/snooze/skip/completion action, push/token/OS permission, new queue, frontend implementation or arbitrary production seeds were introduced. Existing Plan diagnostic reference identifiers were removed to meet the stricter producer privacy boundary; operation/request/version diagnostics and API behavior remain.

## Persistence, concurrency and transactions

One new linear migration, `0008_notification_inbox`, follows 0007. It adds account_timezones, notifications, account/Plan FKs, approved source/event/category, exact-retention/read/audit constraints, occurrence uniqueness, page/unread/expiry indexes and the job producer-scope index/allow-list extension. No backfill. Existing applied migrations were not rewritten. Downgrade removes feature jobs and tables before restoring the old job allow-list. Central metadata contains both new tables.

Production composition shares one Plan UoW/session for source mutation, receipt, cancellation and job staging. Handler snapshot insertion and next Today intent commit together. Account row serialization, immutable occurrence uniqueness and conditional read SQL protect concurrent behavior; worker execution remains at least once with existing leases/fencing/retries. Retention takes bounded ordered account locks before its bounded candidate row locks to match request/producer lock order and skip busy accounts. Driver errors use existing redaction and recognized transient job failures; CLI/diagnostics never expose copy, private references, cursors, SQL, credentials or payloads.

## Validation evidence and limitations

- Baseline: **510 passed, 64 skipped**.
- Complete database-independent suite: **565 passed, 70 skipped**.
- Focused Inbox/domain/application/infrastructure/API tests: **55 passed, 6 skipped**.
- New tests cover timezone create/idempotency/versioning, DST, mutation rollback, correct minus-15/inside-window behavior, no past/unscheduled reminder, source generation/completion/deletion checks, deterministic count copy, producer/read concurrency through transactional doubles, groups/filters/keyset ties/counts, expiry, safe navigation, auth/gates, cache/errors/request IDs, schema/operation inventory, diagnostics, maintenance scheduling and offline migration parity.
- Six guarded PostgreSQL tests cover real concurrent producer/read deduplication, timezone serialization, source/job rollback, keyset ties/full counts/read-all/expiry cleanup, timezone rollback/stale generations, and live migration/catalog parity. `POS_TEST_DATABASE_URL` was unavailable: they were **not run against PostgreSQL**. Existing live tests also remain skipped. No `.env` was read, online migration applied, development database contacted or destructive test cleanup performed.
- Live tests reuse the established guard requiring an explicitly authorized local `pos_test` before upgrades/cleanup. Test doubles, SQL compilation and offline structural parity are not live PostgreSQL proof.
- Offline full upgrade/downgrade and single-head history passed. Compilation of 27 changed/new Python files, changed-file Ruff and Black, 32 unique OpenAPI operations with Today/Plan/Inbox contract 1.0, all 10 synthetic response contracts, documentation links, dependency-boundary checks, sensitive diagnostic scans and `git diff --check` passed.

## Founder verification and delivery boundary

Review the producer/timing/replay/rollback semantics and migration first. Supply the guarded disposable PostgreSQL configuration and run the new PostgreSQL suite plus existing live regressions. In an authorized environment, synchronize a verified user's timezone, create real future Plan intent and invoke bounded job batches deliberately. Verify reschedules/completion/stale leases, concurrent read counts, all filters/groups/navigation states, both DST transitions and expiry independent of device authorization. Schedule bounded retention jobs through the explicit operations CLI. No daemon or job bootstrap runs at API startup. Backend delivery SHA remains a handoff placeholder until separately approved Git delivery.

Inbox/OS authorization/push are separate. This milestone does not change mobile code, device permissions, token registration, providers or notification UI. No files were staged/committed/pushed, no branch was created/switched/merged, and nothing was deployed. Final status: **READY WITH DEFERRED LIVE CHECKS**.

## Exact delivery Git state

`git diff --stat` (tracked changes only; new files below remain untracked):

```text
 app/api/errors.py                        | 40 ++++++++++++++++++++++++++++++--
 app/application/planning.py              | 27 ++++++++++++++++++++-
 app/domain/jobs.py                       | 17 +++++++++++++-
 app/infrastructure/job_models.py         |  9 ++++++-
 app/infrastructure/job_worker.py         | 14 ++++++++++-
 app/infrastructure/jobs.py               |  4 ++--
 app/infrastructure/planning.py           | 18 ++++++++++----
 app/main.py                              | 13 +++++++++++
 docs/backend-architecture.md             |  9 +++++++
 docs/migrations.md                       |  9 +++++++
 migrations/metadata.py                   | 10 +++++++-
 tests/test_api_contracts.py              |  7 +++++-
 tests/test_authentication_persistence.py |  2 +-
 tests/test_jobs_infrastructure.py        | 26 +++++++++++++++++++--
 tests/test_migrations.py                 |  4 +++-
 15 files changed, 191 insertions(+), 18 deletions(-)
```

`git status --short`:

```text
 M app/api/errors.py
 M app/application/planning.py
 M app/domain/jobs.py
 M app/infrastructure/job_models.py
 M app/infrastructure/job_worker.py
 M app/infrastructure/jobs.py
 M app/infrastructure/planning.py
 M app/main.py
 M docs/backend-architecture.md
 M docs/migrations.md
 M migrations/metadata.py
 M tests/test_api_contracts.py
 M tests/test_authentication_persistence.py
 M tests/test_jobs_infrastructure.py
 M tests/test_migrations.py
?? app/api/notification_schemas.py
?? app/api/routes/notifications.py
?? app/application/notifications.py
?? app/domain/notification_jobs.py
?? app/domain/notifications.py
?? app/infrastructure/notification_models.py
?? app/infrastructure/notification_operations.py
?? app/infrastructure/notifications.py
?? docs/examples/notification-inbox-v1-fixtures.json
?? docs/frontend-notification-inbox-handoff.md
?? docs/milestone-17a-validation.md
?? docs/notification-inbox.md
?? migrations/versions/0008_notification_inbox.py
?? tests/notification_support.py
?? tests/test_notifications.py
?? tests/test_notifications_api.py
?? tests/test_notifications_infrastructure.py
?? tests/test_notifications_postgresql.py
```
