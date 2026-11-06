# Staging database implementation and validation

Validated November 5, 2026. No Supabase connection, migration, or other remote
operation was performed. No Git commands, commits, pushes, or PR creation were
performed. Historical migration revisions remain unchanged.

## Files changed and actual behavior

Paths below are relative to `pos-backend/`.

| File | Change |
| --- | --- |
| `app/config.py` | Adds immutable database-only settings and loader; shared inherited runtime settings validate pool limits, synchronous URL schemes, verified TLS, trusted CA parsing and protected URL options. |
| `app/infrastructure/database.py` | Accepts database settings; configures pool size 2, overflow 0, pool timeout 10s and driver connect timeout 10s by default; passes configured TLS mode/CA; preserves pre-ping and parameter hiding. Rejects mismatched settings and engine URLs. |
| `app/infrastructure/database_readiness.py` | Reads repository heads, executes SELECT 1, and compares every deployed Alembic revision against the single repository head; performs no migration or stamp. |
| `app/api/routes/health.py` | Adds GET /health/ready with generic 503 on unavailable/incompatible database; preserves GET /health liveness behavior. |
| `app/main.py` | Provides readiness with the existing composed engine; owns/disposes a fallback engine only when services were injected; adds no startup migration or connection. |
| `app/infrastructure/authentication/composition.py` | Passes validated pool/TLS settings to registration engine creation. |
| `app/infrastructure/authentication/session_composition.py` | Passes settings when a session engine must be created; preserves engine sharing. |
| `app/infrastructure/authentication/password_recovery_composition.py` | Passes settings when a recovery engine must be created; preserves engine sharing. |
| `app/infrastructure/authentication/email_delivery.py` | Passes settings for the email worker's engine. |
| `app/infrastructure/authentication/local_email.py` | Passes settings for the local inspection command's engine. |
| `app/infrastructure/onboarding.py` | Passes settings when an onboarding engine must be created; preserves engine sharing. |
| `app/infrastructure/job_worker.py` | Passes settings for background worker engine creation. |
| `app/infrastructure/notification_operations.py` | Passes settings for notification maintenance engine creation. |
| `migrations/env.py` | Online migrations load only database settings and apply the shared secure engine options; offline mode still loads no settings or engine. |
| `migrations/runtime.py` | Accepts the database-only settings type while retaining the safe missing-URL check. |
| `.env.example` | Documents pool/timeout defaults and TLS variable names without credentials. |
| `docs/devops/staging-database.md` | Adds staging variables, Session Pooler configuration, CA requirements, separate migration procedure, readiness, rollback/recovery and connection budgeting. |
| `docs/devops/environment-variables.md` | Links the staging database settings/runbook and database-only migration path. |
| `docs/devops/environment-strategy.md` | Records the staging database target and links deployment instructions. |
| `docs/migrations.md` | Corrects 0004 duplicate receipt deletion, current metadata/head descriptions and database-only migration configuration. |
| `docs/milestone-07-09-validation.md` | Corrects the second claim that 0004 deletes nothing or fails on duplicates. |
| `tests/conftest.py` | Generates an isolated temporary test CA without relying on the host trust store. |
| `tests/test_staging_database.py` | Covers protected TLS modes, CA failures, URL overrides, async URL rejection, pool/timeout validation and propagation, isolated Alembic settings/offline mode, readiness schema states/failures, shared engine and startup behavior. Uses SQLite only to exercise real Alembic version-table inspection without a network connection. |
| `tests/test_config.py` | Supplies verified TLS/CA configuration in valid protected-environment fixtures. |
| `tests/test_api_contracts.py` | Adds the new readiness operation to the expected OpenAPI operation list; existing authentication operations remain unchanged. |
| `tests/test_authentication_hardening.py` | Supplies TLS/CA settings in the existing protected-auth fixture; removes its unused json import. |
| `tests/test_jobs_infrastructure.py` | Updates engine test doubles to accept the settings argument. |
| `tests/test_notifications_infrastructure.py` | Updates engine test doubles to accept the settings argument. |
| `docs/devops/staging-database-validation.md` | This implementation and validation record. |

## Validation results

- Full pytest with `DATABASE_URL` and `POS_TEST_DATABASE_URL` unset and
  `APP_ENV=test`: **612 passed, 71 skipped**, 17.09 seconds. Guarded integration
  coverage was not enabled; no live PostgreSQL/Redis verification is claimed.
- Ruff on all changed Python files: **passed**.
- Repository-wide `ruff check app migrations tests`: **23 existing findings in
  untouched files** (unused imports/variables, imported pytest fixture naming,
  lambda assignment and semicolon style). These remain outside this patch.
- Database configuration: a staging placeholder URL, valid CA bundle,
  `verify-full`, default pool limits, pre-ping and parameter hiding validated;
  engine constructed and disposed without opening a connection.
- `alembic upgrade head --sql` and `alembic downgrade head:base --sql`: **passed**
  even with deliberately invalid APP_ENV and DATABASE_URL, proving the offline
  path does not load database/application configuration.
- `alembic heads`: **0008_notification_inbox**, single head.
- `python -m compileall -q app migrations`: **passed**.
- `python -m pip check`: **no broken requirements**.

## Remaining deployment gates

The database configuration implementation is ready for a staging connection
attempt. Supabase deployment itself is **not verified or completed**. An operator
must inject the actual Session Pooler URL and an appropriate mounted CA bundle,
confirm the free-capacity connection budget, authorize and run the separate
migration procedure, then confirm /health/ready returns 200. Certificate chain,
hostname, credentials, permissions and real PostgreSQL 17 migration compatibility
have not been tested against Supabase. Guarded integration tests remain to be run
against an explicitly configured disposable local PostgreSQL/Redis environment.
The full API still requires all existing protected auth/email/Redis/legal
settings. No authentication contract, Supabase Data API, SDK, or infrastructure
change is introduced.

Stop here and obtain founder approval before Git operations or deployment work.
