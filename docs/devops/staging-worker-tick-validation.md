# Staging worker tick implementation handoff

Validated November 11, 2026. Final status: **READY WITH DEFERRED LIVE CHECKS**.

## Starting state and inspected evidence

- Branch: `develop`.
- HEAD: `368ad8608d9e905756ae44811d63eb7db88162a0`.
- Starting worktree: clean; nothing staged or untracked.
- Relationship to the local `origin/develop` reference: 0 ahead, 0 behind.
  No fetch was performed; this describes the available remote-tracking reference.
- Required email and background-job implementations were present. No unexplained
  changes were found before editing.
- Read governance: `pos-governance/AGENTS.md`, `01_PROJECT_MANIFEST.md`, `CODEX.md`,
  and backend `CONTRIBUTING.md`. Only the manifest is present among the numbered
  product inputs. The explicit task authorizes this backend work beyond the
  governance's older local-MVP phase; it does not authorize branch changes.
- Inspected the complete existing email provider/renderer/CLI, dispatcher,
  background-job service/domain/repository/UoW/CLI, outbox claim/result repository
  methods, configuration and redaction boundaries, Redis admission composition,
  existing CLI/diagnostics tests and the relevant operations documentation.
- Inspected notification handler revalidation and occurrence insertion,
  existing PostgreSQL disjoint-claim/recovery/uniqueness tests, and the local
  database isolation guard. No missing concurrency-safe claim implementation was
  found.
- Inspected installed pinned redis-py source: URL parsing/pool composition,
  SSL connection configuration and socket wrapping, bounded pool checkout,
  client ownership/close behavior and default representations. Compatibility
  conclusions are based on this repository's composed client, not just docs.

## Architecture and files changed

Application/domain business rules, transactions, retry policies, notification
handlers, migrations and API contracts are unchanged. Infrastructure exposes the
existing service compositions as resource-owning context managers. The new
orchestrator calls those services, not their legacy CLI exit-code adapters.

| File | Actual change |
| --- | --- |
| `app/infrastructure/worker_tick.py` | Finite sequential orchestration, strict positive limits, per-processor results, safe aggregate JSON, deterministic combined exit code and private/redacted failures; injectable processor factories. |
| `app/infrastructure/authentication/email_delivery.py` | Extracts the existing dispatcher composition, registers engine and HTTP-client cleanup, and adds provider close; preserves legacy CLI command and item-status mapping. |
| `app/infrastructure/job_worker.py` | Extracts the existing job/notification handler composition and engine cleanup; preserves standalone CLI command/default and item-status mapping. |
| `app/infrastructure/authentication/redis_client.py` | Builds a lazy maintained redis-py client with verified TLS for TLS URLs, bounded pool/socket/connect waits, safe pool repr, owned pool close and safe composition/close exceptions. |
| `app/infrastructure/authentication/abuse_control.py` | Uses that client composition and close boundary; retains Redis admission semantics and private causes behind redacted public failures. Black formatting applied to the touched module. |
| `tests/test_worker_tick.py` | Covers independent attempts, all exit classes, item retry/permanent behavior through the real dispatcher, invalid limits, exact command/defaults, resource cleanup, finite execution and sentinel-safe outputs/reprs/tracebacks. |
| `tests/test_redis_tls_composition.py` | Exercises actual repository/redis-py composition with forbidden sockets, TLS context verification, URL pass-through, bounded exhausted-pool waits, repr safety, failure privacy and optional/local Redis compatibility. |
| `tests/test_worker_tick_claims.py` | Verifies bounded PostgreSQL SKIP LOCKED claim contracts, expired email lease selection, claim-ID acknowledgement predicates and full general-job fencing, without connection or SQL output. |
| `tests/test_worker_tick_postgresql.py` | Adds guarded real-database tests for overlapping combined ticks with fake delivery and stale email-result rejection after lease recovery. Deferred in this environment. |
| `docs/devops/staging-worker-tick.md` | Canonical command, configuration names, limits, output/exit contract, operational handoff, overlap/TLS evidence and deferred checks. |
| `docs/background-jobs.md` | Links the combined staging command without changing the standalone contract. |
| `docs/password-recovery-and-email-delivery.md` | Links the combined staging command without changing email policy. |
| `docs/devops/staging-worker-tick-validation.md` | This validation and review handoff. |

## Exact command and contracts

```bash
python -m app.infrastructure.worker_tick process-pending --email-limit 25 --job-limit 25
```

This exact argument vector is tested with injected processors: each receives
25 exactly once and the result is exit 0. Live staging execution was not run.
Defaults are email 25 and jobs 10; each argument accepts integers 1–25. The
command runs email then jobs and exits after one pass of each. An email failure
never short-circuits the job attempt. The final exit is the maximum of both
statuses: 0 completed passes, 1 operational/cleanup failure, 2 invalid arguments
or configuration, 3 composition/startup failure. Handled per-item retries,
terminal/dead outcomes and lease-loss counters do not become execution failures.
Shared settings failure prevents processing; invalid arguments prevent settings
loading. No scheduler, workflow, persistent process or process-local mutex exists.

Final JSON contains only processor, outcome and five aggregate count fields.
Unknown counts are null. Email lease-loss count is unavailable from the existing
dispatcher and is never fabricated. Existing allow-listed job diagnostics remain.
Raw exceptions, payloads, content, recipients, tokens, private links, credentials,
connection URLs, provider bodies and SQL are never added to summaries.

## Environment names and timeout behavior

See [the complete operations runbook](staging-worker-tick.md) for the full
protected configuration list, local/staging invocation and no-value handoff.
Core names: `APP_ENV`, `DATABASE_URL`, `AUTH_EMAIL_PROVIDER`,
`AUTH_OUTBOX_ENCRYPTION_KEY`, `RESEND_API_KEY`, `AUTH_EMAIL_FROM`.
Staging additionally retains the existing TLS, auth, Redis, email/reset and
legal settings requirements. No new environment variable is added and no `.env`
values were read, printed or copied.

Provider phase timeout remains 10 seconds; database connect and pool timeouts
remain the configured settings, defaulting to 10 seconds. Neither worker uses
Redis. API admission still accepts the username/password TLS URL shape and
passes it unchanged to redis-py. TLS requires certificate verification and
hostname matching. Pool, connect and socket waits use `AUTH_REDIS_TIMEOUT_MS`,
default 200 ms; the pool is capped at 20 and automatic client retries remain off.
Query/fragment options cannot disable verification or replace those bounds.
Credential-bearing pool repr is replaced; exceptions have safe public chains.
No live Upstash, Resend or email call was made.

## Overlap evidence and DevOps responsibility

Both processors retain transactional PostgreSQL SKIP LOCKED claiming. Email
result transitions require the matching claim ID and claimed state; recovered
claims reject stale results. General jobs additionally fence owner, attempt and
live lease. Stable provider idempotency keys and notification occurrence
uniqueness retain their existing scope; arbitrary external effects are still
at least once. No exactly-once promise or new failure classification is added.

DevOps should use the same staging GitHub Actions concurrency group for manual
and scheduled ticks, with in-progress cancellation disabled, plus a suitable
job timeout. Database correctness must not depend solely on that setting.
GitHub Actions is temporary staging scheduling, not the production worker
platform. No workflow was created and no deployment was performed.

## Exact validation results

| Check | Result |
| --- | --- |
| Before-edit database-independent baseline | 612 passed, 71 skipped; 18.40 s |
| Focused new orchestration/Redis/claim tests, including guarded test collection | 72 passed, 2 skipped; 0.90 s |
| Existing authentication email tests | 15 passed; 0.12 s |
| Existing background-job/notification unit, infrastructure and API tests | 104 passed; 2.04 s |
| Full final database-independent suite | 684 passed, 73 skipped; 19.13 s |
| Architecture tests | 6 passed; 0.80 s |
| Ruff on all nine changed/new Python files | Passed |
| Black check on all nine changed/new Python files | Passed |
| Python compilation | Passed |
| `pip check` | No broken requirements |
| Production dependency/configuration scan | No web-startup imports, dotenv loading or logging configuration introduced |
| Secret-literal scan of changed production code/docs | No credential URLs, private keys or API token literals found |
| Sentinel stdout/stderr/log/repr/public traceback tests and validation-output scan | Passed; no sentinel sensitive output emitted |
| Local documentation-link scan | Passed |
| `git diff --check` | Passed |
| `git diff --cached --quiet` | Passed; nothing staged |

A custom dependency scan initially included tests and flagged the intentional
FastAPI import in the API compatibility test. Restricting the production import
rule to production modules passed; existing architecture tests also passed.
No production dependency exception was waived.

No authorized `POS_TEST_DATABASE_URL` was present. Guarded PostgreSQL and Redis
integration tests were skipped; no configuration was searched for in `.env`.
New and existing claim-query tests passed without connecting. Live PostgreSQL
17 overlapping execution, database connectivity/certificate/ACL behavior,
Upstash account connectivity and provider delivery remain deferred authorized
checks. They are not claimed as verified.

## Git review state

HEAD and branch remain unchanged. `git diff --stat` for tracked modifications:

```text
 app/infrastructure/authentication/abuse_control.py | 104 +++++++++++++++------
 .../authentication/email_delivery.py               |  96 ++++++++++++-------
 app/infrastructure/job_worker.py                   |  41 ++++----
 docs/background-jobs.md                            |   3 +
 docs/password-recovery-and-email-delivery.md       |   3 +
 5 files changed, 166 insertions(+), 81 deletions(-)
```

Untracked files are intentionally not staged, so that stat excludes the eight
new files shown by `git status --short`:

```text
 M app/infrastructure/authentication/abuse_control.py
 M app/infrastructure/authentication/email_delivery.py
 M app/infrastructure/job_worker.py
 M docs/background-jobs.md
 M docs/password-recovery-and-email-delivery.md
?? app/infrastructure/authentication/redis_client.py
?? app/infrastructure/worker_tick.py
?? docs/devops/staging-worker-tick-validation.md
?? docs/devops/staging-worker-tick.md
?? tests/test_redis_tls_composition.py
?? tests/test_worker_tick.py
?? tests/test_worker_tick_claims.py
?? tests/test_worker_tick_postgresql.py
```

Nothing was staged, committed, pushed, merged, deployed or changed in the
frontend/mobile repository. No branch was created, switched, merged or deleted.
No GitHub Actions workflow, production infrastructure, database revision or
paid infrastructure was added or modified. Work stops at review.
