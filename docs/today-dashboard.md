# Today Dashboard Core v1 — daily planning

Milestone 2.B implements API `/api/v1` and Today contract `1.0`. Planning owns
intent and completion. Today is a read-only deterministic projection; it stores
no dashboard facts, scores, wins, or measurements. The frontend contract is
[frontend-today-dashboard-handoff.md](frontend-today-dashboard-handoff.md).

## Architecture and composition

Routes in `app/api/routes/planning.py` validate HTTP contracts and invoke
`PlanService` / `TodayService`. Framework-independent domain policy lives in
`app/domain/planning.py` and `app/domain/today.py`. Application repository and
Unit-of-Work ports live in `app/application/planning.py`; SQLAlchemy adapters
live in `app/infrastructure/planning.py`. Existing canonical relational metadata,
engine/session creation, safe database translation and Unit-of-Work lifecycle
are reused. No provider, cache, or new runtime dependency is introduced.

Runtime composition reuses the onboarding engine. Missing configuration never
connects on import; authenticated product requests fail closed with 503 when
required services are unavailable. Generic unversioned `/tasks` remains a mock,
not an authoritative planning source. No suitable durable planning table existed.

Today reads eligible date assignments and current-day completion facts in one
account-scoped PostgreSQL statement. This gives counts, score and wins the same
statement snapshot. Onboarding preferences come through the existing authoritative
product gate. Confirmed priorities and configured goal identifiers are inspected,
but targets without measurements do not become progress.

## API inventory and contracts

| Method | Endpoint | Success | Operation ID |
| --- | --- | --- | --- |
| POST | `/api/v1/plan/items` | 201 | `create_plan_item` |
| GET | `/api/v1/plan/items/{plan_item_id}` | 200 | `get_plan_item` |
| PATCH | `/api/v1/plan/items/{plan_item_id}` | 200 | `update_plan_item` |
| POST | `/api/v1/plan/items/{plan_item_id}/complete` | 200 | `complete_plan_item` |
| GET | `/api/v1/today?timezone=America/New_York` | 200 | `get_today_dashboard` |

GET item implements the advertised `plan_item.view` capability. No deletion,
reopening, recurrence, categories, descriptions, subtasks, tags or reminders exist.
Create requires `Idempotency-Key`: 1–128 ASCII letters/numbers/dot/underscore/hyphen,
starting with a letter or number. All inputs are closed; account IDs and unknown
fields are rejected. PATCH requires `expected_version >= 1` and at least one
changed field. Omitted fields are preserved. Only start/end can be explicitly
cleared with null. Both must be cleared together when clearing a scheduled start
that has an end. Completed items cannot be edited or reopened (409).

Titles are stripped of surrounding whitespace, required, and limited to 200
Unicode characters. No additional normalization convention for product titles
existed. Blank titles, invalid zones, naive instants, offset mismatches, assigned
start-date mismatches, and end <= start are rejected with safe 422 errors.
Completion uses the injected server UTC clock, sets `completed_at`, clears
`is_priority`, advances version once, and commits once. Replay returns the current
completed resource, including when the replay version is old.

Exact named TypeScript-style request/response declarations and all enum values
appear in the handoff. Pydantic source is `app/api/planning_schemas.py`. OpenAPI is
generated from `create_app().openapi()` in `app/main.py`, and exposed at
`/openapi.json`; top-level `x-pos-today-contract-version` is `1.0`. API operation IDs
are unique; schemas forbid additional object properties. UUIDs are opaque strings.
Dates are ISO dates; instants use RFC 3339 with explicit offsets, normally UTC Z.

## Eligibility, authorization, errors and cache

All five routes opt into `require_completed_onboarding`: valid session, active
account, verified email, authoritative completed onboarding. Identity comes from
authenticated claims. Missing credentials return 401 `authentication_required`;
existing invalid/expired token semantics remain. Disabled accounts return 403
`account_unavailable`; unverified email returns 403 `email_verification_required`;
incomplete onboarding returns 403 `onboarding_required`. The gate is not global.

Every item lookup/update and receipt/query is account-scoped. Foreign-owned IDs
return the same 404 `item_not_found` as absent IDs. Stale updates/completion return
409 `plan_item_state_conflict`; changed creation payload under the same key returns
409 `plan_item_idempotency_conflict`. Validation returns 422
`request_validation_failed` with empty details so even malicious field names cannot
reflect sensitive values. Shared persistence failure returns 503
`dependency_unavailable`; unexpected failure returns safe 500
`internal_server_error`. Existing error envelopes and `X-Request-ID` are reused.
Success and failure responses set `Cache-Control: no-store` and `Pragma: no-cache`.

## Time, DST, rollover and travel

Current Today accepts a required IANA zone, never a client date or raw offset.
Python `zoneinfo` and maintained system timezone data resolve server UTC time into
the authoritative local day. Unknown/malformed zones fail safely. Keep the runtime
OS timezone database maintained; no fixed-offset arithmetic replaces timezone rules.

Unscheduled items require an explicit date and zone. Scheduled input additionally
requires an aware start instant; its offset must match the named zone at that
instant, and its local start date must match the explicit assignment. Optional end
must also match the zone offset at its own instant and be later than start.
Nonexistent spring-forward wall times fail the offset consistency check. Both
fall-back folds are valid when their offsets identify the intended instant.
End may cross midnight or a DST boundary; comparison uses absolute UTC instants.
Stored start/end remain separate. There is no departure-time field.

The requested current zone defines the Today boundary. Eligibility still uses
stored explicit assigned date: travel changes the current day, never stored user
intent. Absolute scheduled instants remain absolute; unscheduled date assignments
remain attached to their explicitly chosen date. Stored timezone is returned for
presentation and interpretation. Changing assignment timezone via PATCH preserves
existing absolute instants; the resulting start must still match the assigned date.
To change date for a scheduled item, supply a consistent new start or clear schedule.
Previous-day assigned items leave active Today after local midnight. Wins use
completion-day boundaries independently of assignment date. Local start and next
local midnight are converted separately to UTC; a DST day can be 23 or 25 hours.

## Daily Status

Counts are distinct eligible IDs, completed, remaining, and overdue. Overdue means
incomplete with start <= now (exact start is actionable NOW). Apply in order:

1. No eligible items: `getting_started`.
2. All eligible items complete: `complete`.
3. At least one overdue incomplete item: `needs_attention`.
4. Some completion and no overdue remaining items: `on_track`.
5. Otherwise: `getting_started`.

These describe plan facts, never health, mood, worth or general productivity.

## Next Best Action

Exactly one action is returned. Incomplete eligible items rank in this order:

1. Explicit priority whose start is at/before now.
2. Other start at/before now.
3. Upcoming scheduled priority.
4. Other upcoming scheduled item.
5. Unscheduled priority.
6. Other unscheduled item.

Within scheduled tiers use start then stable ID; unscheduled tiers use creation
then stable ID. Returned code explains the tier. Evidence contains only eligible
count, remaining count, selected explicit priority and start. The selected item
uses `plan_item.view`; its title is obtained from the real item resource. No items
uses `new_user` / `create_first_plan_item` / `plan_item.create`. All complete uses
`insufficient_data` / `all_plan_items_complete` / `plan_item.create`: there is no
remaining item to recommend. No health/financial claims, predictions, confidence,
expected impact or unsupported domain action is returned.

## Score

Eligible means authenticated owner plus explicitly assigned current local date.
No cancellation/archive rule exists in this domain. Count each UUID once; every
item has weight 1, regardless of explicit priority. Completed earns 1, remaining
earns 0. Percentage = completed / eligible * 100, rounded with exact decimal
`ROUND_HALF_UP` to an integer (12.5 -> 13). Empty returns `no_score`, percentage
null, numerator 0 and denominator 0. One incomplete item is a legitimate available
0. One of three complete is 33; one of eight complete is 13; all complete is 100.
Breakdown is ordered by stable UUID and contains item ID, domain, fixed eligibility
reason `assigned_current_local_date`, weight, earned weight and completion state.
Onboarding targets and absent measurements never enter the denominator.

## Progress, Up Next, priorities and wins

Progress is `unavailable`, reason `no_supported_measurement_sources`, metrics [].
No target-only goal becomes zero or a visible metric. Capabilities are `partial`
because planning works while measurement sources do not. Metadata exposes confirmed
priority identifiers and configured goal identifiers, never invented measurements.
A future populated measurement contract requires authoritative current values and
targets and remains limited to five visible metrics; this milestone defines no
future populated metric schema.

Up Next includes at most four incomplete eligible scheduled items, ordered by start
then UUID. Past-start items come first. Every past-start/exact-start incomplete item
is NOW; first future is NEXT; subsequent future is LATER. No scheduled items means
empty, never a fabricated NOW. Start, optional end, assigned date, stored timezone,
domain, completion state, stable ID and supported view action remain explicit.

Top Priorities includes at most three incomplete explicitly marked items. Past-start
items come first, then scheduled by start, then unscheduled by creation, then UUID.
Onboarding areas do not imply priority. Completion immediately removes the item.

Wins includes at most three authoritative completions within the requested local
day, irrespective of assigned date. Order newest completion then UUID. Deduplicate
by stable ID. Partial/incomplete items never count. Wins derive from `completed_at`;
no separate events/wins table or duplicate replay event exists.

## State and freshness semantics

`available`: authoritative supported data; `empty`: applicable collection without
records; `no_data`: supported concept with no current value; `unavailable`: source
cannot provide data/is unsupported; `partial`: some capabilities present, others
absent; `stale`: documented older authoritative value; `error`: safe section error.
Numeric zero is returned only when authoritative facts make it zero. Score uses
its own `available | no_score`; recommendation uses
`populated | new_user | insufficient_data`. Today is synchronous, uncached and
returns freshness `current` with `as_of == generated_at`. No stale threshold or
stale backend success is claimed. A shared database failure fails the whole request,
never produces successful empty sections. Unsupported measurements do not fail the
aggregate. Reserved empty arrays have maxItems 0 in v1 OpenAPI.

## Persistence, transactions and idempotency

Revision `0006_daily_planning` follows `0005_authoritative_onboarding`, adds only
`plan_items` and `plan_creation_receipts`, and has a reversible destructive downgrade.
It does not alter 0004/0005 or seed/backfill data. Account deletion cascades to items
and receipts. Database CHECKs protect title bounds, positive versions, schedule
ordering, audit-time ordering, and completed-not-priority. IANA validation remains
at the domain boundary because database timezone catalogs are not portable contracts.

Indexes support account/date/start lookup and account/completed-at range lookup.
Receipt uniqueness scopes account, fixed operation and hashed key. No raw
idempotency key is stored. Canonical hash covers normalized title, date, IANA zone,
UTC start/end and explicit priority. Equivalent normalized input replays the
original successful response, even if the item later changes. Different hashes
conflict. An immutable strict response snapshot is kept in the domain-owned receipt
so replay needs no current-item substitution. Receipts live with their account/item;
no speculative expiration/cleanup policy is introduced.

Creation locks the owning account row before receipt lookup, avoiding the absent
receipt race; item and receipt commit atomically. This conservatively serializes
creates per account; finer locking is deferred pending measured contention. Edits
and completion lock the account-scoped item and compare expected version in SQL.
Concurrent identical completions produce one state transition; replay makes no
commit. Repositories never commit/create sessions. UoW owns commit, rollback and
close; errors roll back the full transaction. Read operations never commit.

## Diagnostics and deferred scope

Allow-listed successful events use `planning_operation_succeeded` with operations
`today_read`, `plan_item_create`, `plan_item_update`, `plan_item_complete`, outcome,
validated request ID, account UUID, optional item UUID and version. Persistence
failures reuse fixed safe database categories. No titles, bodies, free-form
onboarding values, email, tokens, URLs, SQL, credentials or raw exception text are
logged. Private titles may be returned to the authenticated owner for presentation.

Deferred: nutrition/calories/protein/water/workout/budget/focus/routine logging and
measurements; calendar/reminders/wearables/providers; commute/departure; push;
recurrence/subtasks/reminders/automatic scheduling; AI/prediction/confidence/impact;
learned ranking; offline mutation queue/sync; frontend implementation; deployment.
Only `plan_item.create`, `plan_item.view`, `plan_item.update`, `plan_item.complete`
are advertised. Unsupported Quick Log actions must remain hidden.

## Manual verification and founder prerequisites

Use a reviewed backend checkout, maintained IANA timezone data, configured existing
authentication/session/onboarding dependencies and a disposable authorized PostgreSQL
test target for destructive checks. Never paste secrets or token values into reports.
No live validation is authorized through the development database. Automated
PostgreSQL checks require `POS_TEST_DATABASE_URL` pointing to local `pos_test`, passed
through `tests/postgresql_support.py`; no `.env` lookup is needed.

1. On the guarded disposable target, upgrade Alembic to head and verify metadata.
2. Register, verify email, sign in and complete onboarding through existing flows.
3. GET Today with the current IANA zone: null score, empty collections, unavailable
   progress, create-first recommendation and supported planning actions only.
4. Create an unscheduled priority using an idempotency key; retry identically and
   differently to verify same resource versus safe conflict.
5. Create a scheduled item using the actual named-zone offset; inspect item GET,
   Up Next and the explicit start/end fields.
6. PATCH with current version; retry a stale differing update and verify 409.
7. Complete with current version; verify server timestamp, one win, score update,
   and immediate removal from active priorities/Up Next. Replay completion.
8. Use a second account to probe the item ID; all lookups/mutations return 404.
9. Verify unverified, disabled and incomplete-onboarding product gates.
10. Exercise local midnight, spring-forward invalid time, both fall-back offsets,
    and another current timezone. Confirm stored intent and absolute instants remain.
11. Simulate persistence failure; verify safe 503, request ID and no-store headers.
12. On a disposable database only, downgrade to 0005, upgrade to head and compare
    metadata. Never treat offline SQL as proof of live PostgreSQL behavior.
