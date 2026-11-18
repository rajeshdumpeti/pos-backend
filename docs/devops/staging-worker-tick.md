# Temporary staging worker tick

The backend provides one finite combined pass for the temporary GitHub Actions
staging scheduler. It reuses `AuthenticationEmailDispatcher` and `JobWorker`,
including the existing notification and maintenance handlers. It does not
implement scheduling, poll for work, sleep, create a daemon, seed jobs, migrate
the database, or run on FastAPI startup. GitHub Actions remains a temporary
staging scheduler, not the final production worker platform.

## Exact command and limits

From the backend repository, with its existing dependencies installed and
configuration injected into the process:

```bash
python -m app.infrastructure.worker_tick process-pending --email-limit 25 --job-limit 25
```

The equivalent local virtual-environment command is:

```bash
.venv/bin/python -m app.infrastructure.worker_tick process-pending --email-limit 25 --job-limit 25
```

The default invocation is:

```bash
.venv/bin/python -m app.infrastructure.worker_tick process-pending
```

| Argument | Default | Accepted range |
| --- | --- | --- |
| `--email-limit` | 25 | Integer 1–25 |
| `--job-limit` | 10 | Integer 1–25 |

Both limits are validated before configuration loading or processing. Zero,
negative, malformed and larger values fail without echoing the arguments.
Each processor receives its selected limit exactly once. Empty queues are
successful. Exhausted job claims count against the existing inspected batch
bound; retry scheduling does not process the retried item again in this tick.
Existing standalone email/job commands retain their commands and item policies;
the combined command has its own exit-code contract below.

## Independence, resources and exit codes

Email runs first, then general jobs, sequentially in one process. Each owns its
own composition and cleanup scope. An operational failure, configuration failure,
or composition failure in email does not prevent the job attempt. Both results
are retained. HTTP clients and database engines close even after processing or
partial composition failures; cleanup failure is reported rather than silently
turning a failed pass into success.

| Exit | Meaning |
| --- | --- |
| 0 | Both passes completed; handled per-item retries, terminal/dead outcomes and lease-loss counts are permitted. |
| 1 | At least one processor had an operational execution or cleanup failure. |
| 2 | Invalid arguments or invalid/missing configuration. |
| 3 | Startup/composition failure before that processor could begin processing. |

After both attempts, the command returns the numerically highest exit code,
so startup failure takes precedence over configuration failure, then operational
failure. Shared settings validation fails before either processor is constructed
and returns 2; an unexpected shared startup failure returns 3. Interrupts and
process termination are not safely handled operational item failures; a killed
process cannot promise a final summary or cleanup. Existing lease recovery
handles abandoned claims.

The command does not translate the legacy CLIs' item-based exit statuses. It
reads their shared application-service summaries directly. An existing dispatcher
retry or permanent rejection therefore stays a handled item outcome, not a new
orchestrator failure. Processor exceptions produce fixed outcome categories;
raw exceptions are retained only behind private error attributes for controlled
internal inspection, with a redacted public exception chain.

## Environment configuration

No new environment variables are introduced. Neither processor loads `.env`
files. Inject configuration using the approved deployment secret mechanism;
never use shell tracing, print environment values, or copy credentials into
commands or workflow YAML.

The email pass requires these existing names:

- `DATABASE_URL`
- `AUTH_EMAIL_PROVIDER` (select the existing sending provider; inspection mode
  does not send and is a configuration failure for this command)
- `AUTH_OUTBOX_ENCRYPTION_KEY`
- `RESEND_API_KEY`
- `AUTH_EMAIL_FROM`

The general-job pass requires `DATABASE_URL`. Both use `load_settings()`, so all
existing protected-environment requirements also apply when invoking staging:

- `APP_ENV`, `CORS_ORIGINS`
- `DATABASE_SSLMODE`, `DATABASE_SSLROOTCERT`
- `JWT_SECRET`, `AUTH_CHALLENGE_DIGEST_KEY`,
  `AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY`
- `CURRENT_TERMS_VERSION`, `CURRENT_TERMS_URL`,
  `CURRENT_PRIVACY_VERSION`, `CURRENT_PRIVACY_URL`
- `AUTH_RATE_LIMIT_BACKEND`, `REDIS_URL`, `AUTH_RATE_LIMIT_KEY`,
  `AUTH_PUBLIC_ORIGIN`, `AUTH_PASSWORD_RESET_URL`

Existing optional tuning/metadata names include `DATABASE_POOL_SIZE`,
`DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT`, `DATABASE_CONNECT_TIMEOUT`,
`AUTH_REDIS_TIMEOUT_MS`, `AUTH_OUTBOX_ENCRYPTION_KEY_ID`, and
`AUTH_OUTBOX_ENCRYPTION_KEY_VERSION`. Existing policy defaults and authentication,
legal and delivery validations are unchanged. See
[environment variables](environment-variables.md) and
[database deployment](staging-database.md).

For local invocation, use only an authorized local database and deliberately
configured test delivery account. Automated tests replace delivery with doubles
and never contact a live provider. Local inspection remains a separate command;
there is no insecure or simulated-delivery fallback in this tick. For staging,
DevOps injects the existing protected settings and CA file, applies migrations
in the separate approved step, then invokes the exact command above.

## Timeouts and capacity

Existing SQLAlchemy defaults are pool size 2, overflow 0, pool checkout wait 10
seconds, and PostgreSQL connect timeout 10 seconds, with pre-ping and parameter
hiding. Each pass creates/disposes its engine sequentially. The existing Resend
adapter uses a 10-second HTTPX timeout for each connect/read/write/pool phase,
and this change also closes its HTTP pool. No retry/lease or provider failure
classification is changed.

The command is bounded by item count, not a guaranteed wall-clock deadline.
There is no newly introduced PostgreSQL statement timeout or forced transaction
cancellation. Network operations and individual handler execution can take
multiple timeout phases. DevOps must also set a suitable workflow job timeout;
a killed batch can leave leases for later recovery. Do not increase limits to
catch up without reviewing lease duration and connection capacity.

## Overlap and PostgreSQL correctness

DevOps should give scheduled and manually dispatched ticks the **same GitHub
Actions concurrency group** for the staging environment and avoid cancelling an
in-progress tick merely because another schedule arrives. Use `cancel-in-progress`
disabled and a sensible job timeout. DevOps owns that workflow; this change
creates no workflow or deployed infrastructure. Workflow concurrency reduces
overlap; database correctness does not depend on it. There is no machine-local
mutex.

Email claims select a bounded due/expired batch with PostgreSQL `FOR UPDATE
SKIP LOCKED`, then commit claim IDs before delivery. A five-minute abandoned
claim can be recovered. Delivery/retry/failure updates predicate record ID,
claimed state and the matching claim ID, so a recovered claim rejects the old
owner. The outbox UUID remains the provider idempotency key across recovery.
The existing dispatcher does not report failed acknowledgement counts separately;
its aggregate counters are not a proof of provider inbox placement or current
lease ownership. The email summary's `lease_lost` field is therefore null.

General-job claims retain due-time ordering, bounded `SKIP LOCKED` row locks,
atomic attempt/owner/lease changes and the existing five-minute default lease.
Renewal/completion require matching job ID, running state, owner, attempt and a
still-live lease. Retries/backoff, exhausted-attempt recovery and terminal
categories remain owned by `JobWorker`. Notification handlers retain account
locks, source revalidation, and unique occurrence insertion; job scheduling
retains database deduplication constraints.

Execution is at least once. Fencing protects persisted acknowledgements, not
arbitrary external effects. Provider deduplication and notification uniqueness
retain their existing scopes/windows; no perpetual exactly-once promise is made.
Do not alter leases, keys, retries or handler rules in a scheduler wrapper.

Evidence: the network-free `test_worker_tick_claims.py` verifies generated
PostgreSQL claim/fence contracts without printing SQL. Existing guarded tests in
`test_jobs_postgresql.py`, `test_authentication_postgresql.py` and
`test_notifications_postgresql.py` cover disjoint claims, recovery fences and
occurrence uniqueness. New `test_worker_tick_postgresql.py` covers two overlapping
combined ticks (fake email provider) and rejection of all stale email result
updates after reclaim. These tests run only when the existing explicitly
configured, guarded local `POS_TEST_DATABASE_URL` is present; no `.env` is read
and no Supabase database is used for these tests.

## Redis / Upstash TLS boundary

Redis is used by API authentication admission, not by either worker processor.
The tick loads existing settings but does not construct admission or contact Redis.
`REDIS_URL` is still required by the existing protected settings policy.

The repository accepts the Upstash username/password `rediss://` URL shape with
an empty path (default Redis database), through the existing secret setting.
`compose_admission()` calls `create_redis_client()`, which passes the unchanged URL
to the pinned redis-py dependency's `BlockingConnectionPool.from_url()` and
`Redis.from_pool()`. The scheme selects redis-py's `SSLConnection`. Certificate
requirements are explicitly required and hostname checking is explicitly enabled;
there is no downgrade or plaintext fallback. The maintained OS trust store
validates the service certificate. Use a valid trusted runtime CA store.

Connect, socket read/write, and pool-wait timeouts all use the existing
`AUTH_REDIS_TIMEOUT_MS` policy (default 200 ms, validated range 10–2000 ms).
The pool remains capped at 20 and client retries remain disabled. URL query
options/fragments cannot override TLS or timeouts. Existing local plaintext
Redis configuration remains available for local development only by explicitly
choosing that scheme; a configured TLS URL is never rewritten.

The private pool repr prevents redis-py's default connection-kwargs repr from
rendering credentials. Composition, admission and cleanup failures have fixed
public exceptions and redacted public causes while retaining private causes.
`test_redis_tls_composition.py` exercises the actual repository composition and
real redis-py pool/SSL connection classes, verifies the SSL context's certificate
and hostname checks, verifies pool exhaustion bounds, and checks secret-safe
representations/tracebacks. Sockets are forbidden. This proves repository
configuration compatibility, not a live Upstash account/network/ACL test.

## Logging and interpretation

A completed tick prints exactly two JSON summaries, one per processor, restricted
to `processor`, `outcome`, `claimed`, `succeeded`, `retryable`, `dead`, and
`lease_lost`. Counts are non-negative integers; unknown counters are null. Email
maps delivered to succeeded, retry_scheduled to retryable and failed to dead.
No arbitrary summary fields or exception strings are serialized. Invalid CLI
arguments print only a fixed invalid-arguments outcome. Shared startup failure
prints fixed summaries with unknown counts.

Do not enable SQL echo, raw driver tracebacks, HTTP wire logging, verbose provider
or Redis connection logging, shell tracing, or environment dumps in the scheduler.
Existing job/persistence diagnostics remain their established allow-listed events.
Never print individual payloads, recipients, content, titles, tokens, private
links, connection URLs, credentials, API keys or provider response bodies. Do
not render private exception attributes or traceback locals. Alert on execution
exit codes separately from item retry/dead counts; a zero exit is not proof that
all items were delivered or that no terminal items need investigation.

## Validation and deferred live checks

Run the network-free regression suite, formatting, architecture and dependency
checks before review. Run guarded PostgreSQL concurrency tests only when the
authorized disposable local test configuration is already available. No live
Upstash, Resend or email-provider calls belong in automated validation. Live
staging invocation, certificate/ACL verification and provider delivery checks
require a separate authorized operations step. No frontend changes, migrations,
scheduling workflow, deployment, Git staging or commits are part of this task.
