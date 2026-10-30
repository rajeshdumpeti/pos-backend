# Milestone 16 implementation validation

## Starting state and scope

- Branch: `feat/backend-background-jobs`.
- HEAD: `146df660844019ff0052ccd05201e860deb48360`, also the required Plan delivery ancestor and current local `origin/develop` tip; ahead/behind 0/0.
- Starting worktree: clean. No branch creation/switch, active delivery operation or unrelated changes were required.
- Baseline database-independent suite: **464 passed, 58 skipped in 10.36s**. Guarded PostgreSQL/Redis service variables were removed from the test subprocess environment to keep this baseline service-independent; no values were printed or discovered from `.env`.

Inspected governance manifest/charter, backend architecture, relational standards, data-access/UoW ownership, safe persistence diagnostics, authentication email dispatcher/CLI, Today/Plan contracts and planning persistence, migration metadata/history, PostgreSQL guard/isolation helpers and existing tests. Authentication email retry/outbox code is domain-specific, not a generic job platform; it was preserved.

## Delivered architecture and policy

[background-jobs.md](background-jobs.md) defines the contract, constraints, operator usage and limits. Domain/application ports depend on no framework/database/provider. PostgreSQL infrastructure owns persistence, bounded SKIP LOCKED claims, attempt fencing, safe acknowledgement, explicit UoW transaction completion and close. CLI execution is one bounded batch. No API/startup task or permanent worker was added.

The initial type `maintenance.jobs.cleanup` is a foundation maintenance operation, not a notification producer. Payload accepts only explicit UTC cutoff and bounded limit. No automatic retention period, production jobs or fake records are inserted. The 5-attempt policy uses 30-second exponential backoff capped at 300 seconds, and only recognized transient/dependency failures retry. Deduplication is database-backed and scoped by owner/type/key; changed intent conflicts. Deduplication ends when explicit terminal cleanup removes a row. Cancel/supersede operate only before leasing and share an explicit transaction for replacement. Execution is at least once; future handlers need idempotence and source-state checks, not an exactly-once assumption.

Migration `0007_background_jobs` follows `0006_daily_planning`, adds only the jobs table and required constraints/indexes, and downgrades by dropping it. There is no backfill. Central metadata includes the canonical model. Existing metadata-count/head assertions changed only as required for the new table/revision. Today/Plan/API behavior is unchanged.

## Validation record

- Complete database-independent suite: **510 passed, 64 skipped in 12.69s**.
- Focused job/application/infrastructure/metadata tests: **57 passed, 6 skipped in 1.09s**.
- Single linear Alembic head: **0007_background_jobs**. Offline full upgrade and downgrade SQL generated successfully without a database connection.
- Offline migration/model parity test compares columns/types/nullability, constraints, foreign keys, index names/columns/predicates. Live database catalog parity is not inferred from this test.
- Python compilation (13 changed/new Python files), changed-file Ruff, changed-file Black, documentation links, safe-output scan and `git diff --check` passed. OpenAPI generation passed with 27 unique operation IDs and no new job/notification endpoints.
- Full regression covers Authentication, Onboarding, Today, Plan, safe errors, API schemas, OpenAPI operation IDs and existing migration standards.

`POS_TEST_DATABASE_URL` was unavailable. No `.env` was read, live database was contacted, live migration was applied or destructive cleanup was performed. The six new live tests cover concurrent deduplication, simultaneously open disjoint claims, stale-ack fencing after lease recovery, future/cancel/supersede/rollback/cleanup, persisted retry exhaustion, and live upgrade/downgrade/catalog parity. They reuse the guard requiring an explicitly authorized local `pos_test` before any upgrade or table cleanup. These concurrency/catalog outcomes remain unverified until that suite runs against PostgreSQL.

## Files and deferred acceptance

Created domain intent/policy (`app/domain/jobs.py`), application ports/services/worker (`app/application/jobs.py`), canonical model (`app/infrastructure/job_models.py`), repository/UoW/diagnostics (`app/infrastructure/jobs.py`), bounded CLI (`app/infrastructure/job_worker.py`), migration `0007`, test-only transactional store and focused/guarded tests, and two canonical documents. Modified central migration metadata, two existing metadata assertions, and architecture/migration documentation links. No runtime dependency, settings variable, frontend file, provider or Inbox endpoint changed.

Follow [founder verification](background-jobs.md#founder-verification-and-deferred-work) in an authorized disposable environment. Notification Inbox records, producers, event/copy matrices, domain preferences, retention policy, source invalidation, push providers, device tokens and OS authorization remain deferred. Nothing was staged, committed, pushed, merged, deployed or changed in the frontend. Final status is **READY WITH DEFERRED LIVE CHECKS**.

## Exact delivery Git state

`git diff --stat` (tracked changes only; newly created files remain untracked):

```text
 docs/backend-architecture.md             | 8 ++++++++
 docs/migrations.md                       | 8 ++++++++
 migrations/metadata.py                   | 4 +++-
 tests/test_authentication_persistence.py | 2 +-
 tests/test_migrations.py                 | 3 ++-
 5 files changed, 22 insertions(+), 3 deletions(-)
```

`git status --short`:

```text
 M docs/backend-architecture.md
 M docs/migrations.md
 M migrations/metadata.py
 M tests/test_authentication_persistence.py
 M tests/test_migrations.py
?? app/application/jobs.py
?? app/domain/jobs.py
?? app/infrastructure/job_models.py
?? app/infrastructure/job_worker.py
?? app/infrastructure/jobs.py
?? docs/background-jobs.md
?? docs/milestone-16-validation.md
?? migrations/versions/0007_background_jobs.py
?? tests/jobs_support.py
?? tests/test_jobs.py
?? tests/test_jobs_infrastructure.py
?? tests/test_jobs_postgresql.py
```
