# Milestone 17A — Notification Inbox Foundation v1

Inbox contract **1.0**, API `/api/v1`, implemented from the founder-approved Milestone 17A brief. The producer matrix and 90-day policy below are product decisions from that brief, not deductions from the showcase. Notification Details/actions, snooze/skip, push delivery, tokens, OS authorization, frontend implementation and generic recurrence are excluded.

## Ownership, architecture and composition

API routes → `NotificationService` / Plan mutation integration → framework-independent repository/UoW and existing job ports ← SQLAlchemy adapters → PostgreSQL. Pure domain rules own occurrence identity, deterministic copy, IANA calendar calculations and safe cursor validation. Existing authentication, verified/active account rules, product onboarding gate, request IDs and safe errors are reused. No new dependency or queue system is added.

`account_timezones` owns a nullable-in-existence account preference, separate from credentials and onboarding. `notifications` stores immutable event snapshots plus read/audit metadata. Both reference the canonical owning account with cascade deletion. Plan references use `SET NULL` so source deletion preserves history but invalidates navigation. No Plan/Today facts are duplicated; handlers read canonical planning rows. No records or jobs are backfilled merely to populate the Inbox.

Production `compose_planning` injects `PlanNotificationIntegration`. Its prepare hook locks the account before Plan row access; scheduling/cancellation occurs before the same Plan UoW commit as item and idempotency receipt changes. Custom production compositions must provide that integration and a composite UoW; test doubles may intentionally omit it. There is no nested transaction around uncommitted Plan state. Inbox handlers/preferences use a composite UoW exposing planning, jobs and Inbox repositories on one session. Repositories never commit/create sessions.

Account-row locking serializes mutations, timezone changes, read-state changes and producer revalidation. The job worker claims/renews in short committed transactions before acquiring account/domain locks in the handler. Handlers atomically insert an occurrence and its next job where relevant. Database uniqueness is a second deduplication barrier. Execution remains at least once: retries may execute a handler again, but cannot create a second retained occurrence snapshot.

## Approved producer matrix

| Event | Source/category | Trigger and eligibility | Schedule and occurrence identity | Lifecycle |
|---|---|---|---|---|
| `plan.item_starting_soon` | `plan` / `reminder` | Owned incomplete item with a future authoritative start; re-read active/verified owner and current Plan state/version at execution | Exact UTC start minus 15 minutes; mutations inside the window enqueue immediately. Inbox identity hashes account/event/item UUID/exact start. Job identity also includes item version to fence stale mutations | Any update replaces pending intent; completion cancels it. A stale leased handler checks current version/start/completion/existence and no-ops. History already delivered is unchanged |
| `today.items_remaining` | `today` / `attention` | Stored authoritative IANA timezone; current evaluated local date at or after its scheduled 18:00; at least one incomplete canonical Plan item assigned to that local date | 18:00 local converted to UTC; Inbox identity hashes account/event/local date/timezone version. Handler stages the following local-calendar evaluation in the same transaction | Changed timezone increments version and supersedes old pending daily work; leased old versions no-op. Zero remaining creates no record but still stages the following evaluation |

Copy is exactly the approved deterministic composition: Plan uses the fixed starting-soon title and quoted current item title with the approved 15-minute sentence. Today uses the approved title and singular/plural count sentence. The fixed Plan wording is preserved even for inside-window scheduling; it is not dynamically rephrased or AI generated. Copy becomes an immutable historical snapshot at insertion, never a live representation of the source afterward. Message/title fields never enter logs or job payloads. Integrated Plan diagnostics also omit account/item reference identifiers; operation, correlation and version metadata remain.

The existing Plan API has no delete/cancel-item operation, so none is added. If an item is removed directly or becomes inaccessible, a handler cannot create a record from it; an existing row becomes non-navigable. Future source deletion/cancellation operations must use the shared integration/cancellation boundary. Completed items remain retrievable and navigable under the existing Plan contract.

## Account timezone, calendar time and late jobs

`PUT /api/v1/account/timezone` accepts only `{timezone}` with a valid IANA identifier. Fixed-offset strings such as `UTC-04:00` are rejected. Identity comes only from the authenticated principal. Active verified users can synchronize before onboarding completion; this does not change onboarding state or make timezone mandatory during registration. Inbox endpoints additionally require completed onboarding, like Today/Plan.

First synchronization creates version 1. Identical literal IANA identifiers preserve both version and updated_at and schedule nothing extra. A changed identifier increments the version, stores one server timestamp and atomically supersedes the previous pending Today evaluation and stages the next valid local 18:00. Alias spellings are not silently canonicalized. This is account-wide, last-writer-wins preference, not per-device scheduling. No timezone means no Today job; Plan jobs still use their own authoritative schedules.

Frontend synchronization should use the existing authenticated client after startup when the device IANA identifier differs from the last confirmed account-scoped synchronized value, or that value is unknown. An identical PUT is safe. Do not derive timezone from raw offsets or OS notification permission. There is no full Settings module or new registration field.

All job timestamps persist exact UTC instants. Each next daily occurrence uses local calendar dates and IANA conversion, not UTC plus 24 hours. Across DST, consecutive occurrences can be 23/25 hours apart. Invalid skipped local dates are skipped using UTC roundtrip validation; zoneinfo's first fold is used if an 18:00 wall time repeats. No Plan assignment/date/timezone is rewritten by account travel.

A delayed Today job may evaluate after 18:00 **on the same local date**. It never creates yesterday's reminder after local midnight. A stale old-date job advances the chain to the current local date or later; if today's 18:00 is already due, that distinct current-date evaluation is queued for the next bounded worker invocation. Following jobs use deterministic local occurrence times, so retries schedule the same next intent. A stale timezone/version does not restart an obsolete chain. Recognized transient failures inherit Milestone 16 retries; exhausted/dead jobs require deliberate operational review rather than an infinite recovery loop.

## HTTP contracts and cache

Exact named TypeScript contracts and fixtures are in [frontend-notification-inbox-handoff.md](frontend-notification-inbox-handoff.md); generated OpenAPI comes from `create_app().openapi()`. Extension `x-pos-inbox-contract-version` is `1.0`; Today and Plan remain `1.0`.

| Method | Path | Scope |
|---|---|---|
| PUT | `/api/v1/account/timezone` | Authenticated principal's timezone/version |
| GET | `/api/v1/notifications` | Retained owned rows; source, required IANA timezone, limit and cursor |
| GET | `/api/v1/notifications/unread-count` | All retained unread rows in selected source |
| POST | `/api/v1/notifications/{notification_id}/read` | One retained owned row; resulting count is account-wide `all` |
| POST | `/api/v1/notifications/read-all` | Explicit body source scope; count is that scope |

Inputs and named response objects are closed. No arbitrary notification creation endpoint exists. Unknown account identifiers/query fields are rejected, not honored. All successes/errors use `Cache-Control: no-store`, `Pragma: no-cache` and existing safe `X-Request-ID`. Validation details are empty on these private routes, preventing field/cursor/body reflection. Missing bearer is 401; existing account/email/onboarding gates remain 403; missing/expired/foreign notification UUIDs share 404 `notification_unavailable`; invalid timezone/filter/cursor is 422 `request_validation_failed`; unavailable dependencies are 503 and unexpected failures safe 500. No error exposes raw SQL, job payloads, titles/messages or navigation references.

Rows sort `created_at DESC, UUID DESC`, using keyset comparisons and a default 50/max 100 limit. Cursors are opaque base64 continuations bound to account/source/requested timezone and a retained owned ordering boundary. They contain no account ID or Plan reference. They are not authentication tokens or a signed snapshot: account predicates are always re-applied, and the boundary must still exist and match. Foreign/filter/zone/expired-boundary cursors fail safely; restart page one. Under stable retained historical reads, ties paginate without duplicates/omissions. Concurrent mutations, expiry and cleanup can change subsequent pages; no cross-page snapshot is promised.

`unread_count` covers the complete retained filtered set, never just the page. The `today` source means Today-produced records, distinct from the `today` grouping label. Grouping converts record creation time and one server-authoritative generated_at into the requested IANA timezone; it does not use source schedule time. Source timezone is retained separately as snapshot presentation metadata.

One-record read is an atomic conditional update/coalesce preserving the first read_at and advancing audit version only once. Read-all atomically changes every retained unread row in scope using one server timestamp; repeated calls mark zero and never rewrite existing timestamps. Its response read_at is the operation timestamp even if no rows changed. Read metadata is the only API mutation of historical snapshots.

Navigation is allow-listed `today.view` or `plan_item.view`. Today selected_date describes the historical evaluation context, not an invented historical Today dashboard API. Plan navigation uses current authoritative owned item's assigned date/UUID when retrievable, even if its title/schedule/completion changed. Historical source_timezone/copy stay unchanged. Missing/deleted/inaccessible targets return `state=unavailable` and null action/date/item ID; the row remains readable and markable until expiry. No arbitrary URL is stored or returned.

## Exact retention and operations

expires_at is created_at plus exactly 90 elapsed UTC days (2160 hours); the database enforces this independently of session timezone. Every read/count/read-mutation excludes expires_at <= the sampled server clock, even before physical cleanup. No request handler scans/deletes expired data.

`maintenance.notifications.cleanup` is an allow-listed maintenance job with payload `{limit: integer 1..100}` and no copy/source references. It observes a bounded ordered candidate set, locks owning accounts in UUID order with SKIP LOCKED, and then locks/deletes only those expired candidates with SKIP LOCKED. Busy accounts are skipped, matching the account-before-row lock order used by reads and producers. Repeated batches are safe; no unexpired row is removed. A global maintenance job is a trusted operations capability, not an account-facing API. Operators can schedule one explicitly with a stable key and aware execution timestamp:

```sh
.venv/bin/python -m app.infrastructure.notification_operations schedule-cleanup 100 2026-10-30T12:00:00+00:00 retention-operation-001
.venv/bin/python -m app.infrastructure.job_worker process-pending 10
```

Use a timestamp appropriate to the authorized environment; the example is synthetic, not an instruction to seed records. Retrying the exact scheduling tuple/key is idempotent; changed scheduling intent under the same key safely conflicts. The CLI prints only a sanitized scheduling count or fixed safe failure. Worker output remains Milestone 16's numeric batch summary, not message bodies or deletion contents. An external deployment scheduler must invoke cleanup deliberately, and repeat bounded batches as backlog requires. API startup creates no loops, maintenance jobs, notification rows or producer backfill. Logical expiration remains effective if the operator delays physical cleanup.

## Persistence and verification

Migration `0008_notification_inbox` follows `0007_background_jobs`: two tables, ownership/Plan FKs, occurrence uniqueness, fixed retention/audit constraints, pagination/unread/expiry indexes, and the producer cancellation index/expanded explicit job allow-list. It performs no backfill. Downgrade removes feature jobs before restoring the old allow-list, then removes feature tables; review this destructive rollback before authorizing a live migration. Authentication credential tables are unchanged. Central metadata and offline migration parity tests cover the new shape.

Account locks and uniqueness provide durable producer/read concurrency protections, with guarded PostgreSQL tests for deduplication, serial timezone updates, read transitions, keyset ties, expiry/cleanup and rollback. Live catalog parity and migration upgrade/downgrade require only the guarded local `pos_test` configuration, never the development database. No `.env` is read for discovery. See the [validation record](milestone-17a-validation.md).

## Acceptance prerequisites

Apply the reviewed migration only in an authorized environment, configure existing authentication/product dependencies, and use verified completed-onboarding accounts for Inbox access. Synchronize a valid timezone, create real approved Plan intent, and invoke bounded worker batches deliberately. Check all filters, Today/Earlier across IANA zones/DST, full unread counts, first-read preservation, scoped read-all, account isolation, changed/completed/deleted navigation and logical expiry independently of device push permission. Test rescheduling/completion while jobs are leased and retries after uncertain acknowledgement. Run guarded PostgreSQL tests before claiming live concurrency or catalog verification. Push delivery, Notification Details/actions and frontend changes remain deferred.
