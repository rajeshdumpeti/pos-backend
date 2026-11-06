# Staging database deployment

Target: Supabase PostgreSQL 17, shared IPv4 **Session pooler**, port **5432**.
POS authentication and authorization stay in FastAPI. Keep the Supabase Data API
disabled. No Supabase SDK, API keys, paid IPv4 add-on, or new infrastructure is
required. This runbook describes later deployment operations; implementation
validation does not connect to Supabase.

## Database configuration

Inject these values through the deployment secret/configuration manager. Do not
commit populated environment files or print the URL in logs or shell commands.

| Variable | Staging value / behavior |
| --- | --- |
| `APP_ENV` | `staging` (production has the same TLS enforcement) |
| `DATABASE_URL` | Required secret: `postgresql+psycopg://postgres.PROJECT_REF:ENCODED_PASSWORD@POOLER_HOST:5432/postgres` |
| `DATABASE_SSLMODE` | Required `verify-full`; alternatively put `sslmode=verify-full` in the URL |
| `DATABASE_SSLROOTCERT` | Required absolute path to a deployed trusted CA PEM bundle |
| `DATABASE_POOL_SIZE` | Default `2`; positive integer |
| `DATABASE_MAX_OVERFLOW` | Default `0`; non-negative integer |
| `DATABASE_POOL_TIMEOUT` | Default `10` seconds; positive integer, waiting for a pool slot |
| `DATABASE_CONNECT_TIMEOUT` | Default `10` seconds; positive integer, Psycopg/libpq connection establishment |

Copy the host and username from the project's **Connect → Session pooler**
connection string. Shared pooler usernames include the project reference. Use the
pooler DNS hostname, with the actual cluster/region from that dialog, and
percent-encode password URL reserved characters. Do not substitute an IP address
or the direct database hostname. Port 6543 is transaction mode and is outside
this deployment configuration. Plain `postgresql://` is normalized to Psycopg 3;
asynchronous driver schemes are rejected.

Source: [Supabase connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres).
Session pooling supports session state and prepared statements. The shared pooler
is available over IPv4 without the paid direct-connection IPv4 add-on.

Mount the trusted CA bundle for the **pooler endpoint's certificate chain** in
both API and migration containers, readable by their runtime user. Obtain the
appropriate trust chain through Supabase's current connection/certificate
instructions or a maintained OS CA bundle; verify that it trusts the actual
pooler certificate, which may differ from the direct database certificate.
Do not download/trust the presented leaf certificate without authenticating its
source. Configuration validates that the CA file can be parsed. Libpq performs
chain and DNS hostname verification on connection. An untrusted, expired, or
hostname-mismatched certificate must fail; never downgrade to `require`,
`verify-ca`, `prefer`, `allow`, or `disable` to bypass an error.

Explicit TLS environment settings take precedence at the driver boundary.
Conflicting TLS URL options and unsupported protected URL connection options are
rejected. Keep the URL query empty except for `sslmode`, `sslrootcert`,
`connect_timeout`, and `application_name`. Use the dedicated timeout and CA
variables for those settings. Engine pre-ping and SQL parameter hiding remain on.
Never enable SQL echo or log settings/driver exceptions with secrets.

## Validate and migrate

Package the `migrations/` directory (including all revision scripts) with the
backend. Readiness derives repository head from these artifacts and fails closed
if they are missing. Application startup does **not** upgrade or stamp schemas.

From the backend directory, with database variables injected into the process:

```bash
# Configuration-only check: validates URL, pool values, TLS mode and CA file.
# This does not connect and requires no auth/email/legal settings.
.venv/bin/python -c 'from app.config import load_database_settings; from migrations.runtime import require_database_url; require_database_url(load_database_settings()); print("Database configuration valid")'

# Offline review only: no database variables or connection needed.
.venv/bin/alembic heads
.venv/bin/alembic upgrade head --sql > /tmp/pos-staging-upgrade.sql
```

Review generated SQL, confirm the deployment targets staging, and secure a tested
backup/export before authorizing an online migration. Avoid writes from older
application versions during incompatible schema changes. Run exactly one release
migration process, separate from Uvicorn and worker startup:

```bash
# ONLINE: deployment operator runs these only after migration authorization.
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

The expected head is `0008_notification_inbox`. Alembic's online environment
loads database settings only, shares the secure engine factory, and disposes its
pool on exit. It never reads `.env` automatically. Supabase normally recommends
direct connections for migrations; this IPv4 deployment uses the specified
Session pooler because it retains a session for the migration connection. Rehearse
the full history on disposable local PostgreSQL 17 before the first deployment.
Do not use the transaction pooler or stamp a non-matching schema as head.

After migration, start the application with its existing complete protected
authentication, Redis, email, and legal configuration. Those application
requirements have not changed. Route deployment readiness checks to
`GET /health/ready`: HTTP 200 requires connectivity and exact deployed revision
parity. Missing, older, newer, unknown, or multiple deployed heads return 503;
connection errors also return 503 with only `{"status":"unavailable"}`.
`GET /health` remains process liveness and makes no database request. A matching
revision is a compatibility gate, not detection of manual schema drift. Pool and
connect timeouts bound acquisition/connection waits separately; they are not an
end-to-end query deadline. Choose probe thresholds to allow those waits.

## Rollback and recovery

Prefer rolling back application code only when it supports the deployed schema,
or apply a reviewed forward fix. Readiness requires exact head parity, so an
older build may reject a newer schema. Stop traffic/workers before an incompatible
rollback. Review a bounded offline downgrade first, for example:

```bash
.venv/bin/alembic downgrade 0008_notification_inbox:0007_background_jobs --sql
```

Downgrades can delete data: 0008 removes Inbox/timezone tables and feature job
intents. Revision 0004 deletes duplicate non-null refresh request-ID receipts,
keeping the earliest `created_at`, then lowest `id`. Its downgrade only drops
uniqueness and cannot recover those deleted receipts. Preserve an export if
those receipts matter; see [migration history](../migrations.md). Never edit
historical revisions. On migration failure, inspect the actual current revision
and transaction outcome before retrying; do not blindly stamp, rerun concurrent
migrators, or assume the entire multi-revision upgrade rolled back. Migrations
use a transaction per revision.

If schema or data cannot be safely repaired forward, restore a verified backup
into a separate staging database, verify revision and application behavior, then
switch the injected connection secret. Do not assume paid point-in-time recovery
is available. Confirm the free-plan backup/export and restore procedure before
deploying valuable staging data. Disposable local databases may be recreated;
staging data loss requires an explicit decision.

## Connection budget

Pools are per process and lazy: defaults allow at most **2 connections per
engine/process**, with no overflow. A session-pooler client holds its server
session until disconnected, including idle SQLAlchemy connections. Budget
`replicas × API workers × (pool_size + max_overflow)` plus independently running
job/email/notification workers, migration processes, tools, and rollout overlap.
Normal API composition shares its engine between services and readiness probes.
Each separate CLI/worker process has its own pool. Reserve headroom for Supabase
services and administration; compare the resulting budget to the project's actual
pooler client and PostgreSQL connection limits. Start with one API worker and
conservative concurrency on free capacity; do not solve exhaustion by raising
limits without checking the budget. Pool exhaustion returns unavailable rather
than opening overflow connections. Stop unused workers and dispose old processes
during rollout.
