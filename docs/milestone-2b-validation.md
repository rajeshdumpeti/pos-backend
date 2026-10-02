# Milestone 2.B implementation and validation report

Final status: **READY WITH DEFERRED LIVE CHECKS**.

## Starting state and inspected evidence (1–5)

Repository: `pos-backend`. Starting and ending branch:
`feat/backend-today-dashboard-v1`. Starting HEAD and unchanged ending HEAD:
`f6ae152085b0d2ee699f12b7798b328b436d69aa`. Starting working tree was clean;
no merge/rebase/cherry-pick/revert was active. `git merge-base --is-ancestor
origin/develop HEAD` exited 0. No branch/history change occurred.

Read the supplied locked implementation request, governance AGENTS.md, complete
manifest, engineering charter and product-document register; backend README,
CONTRIBUTING, architecture, data access, API conventions, relational standards,
onboarding and migration documentation; authentication/recovery/onboarding
application and API contracts, session principal and active/verified gate,
central error/request-ID contracts, repositories/UoW, metadata and migration
history, migration tests and guarded PostgreSQL helpers. No roadmap or separate
Today specification file was present in the inspected repositories; the supplied
locked request provides this milestone's approved scope. Governance's old UI-first
phase is superseded by the explicit backend implementation authorization.

Authentication, password recovery, authoritative onboarding, migration 0005,
onboarding PostgreSQL concurrency/rollback/constraint tests and the migration's
explicit non-null completed-preference checks are present. No suitable durable
planning table exists; unversioned tasks are mock resources.

Pre-edit complete baseline: `.venv/bin/pytest -q` -> **283 passed, 52 skipped,
0 failed**, 6.04s. Reused authenticated sessions, narrow authoritative onboarding
gate, safe errors/request IDs/cache conventions, SQLAlchemy metadata/naming,
engine/session factories, safe persistence translation and UoW lifecycle.

Narrow adjustments: item GET added to implement `plan_item.view`; a new scoped
creation receipt is necessary because refresh receipts are authentication-specific.
Existing static global inventory assertions gained five operations, two table names,
table count 13 and the new migration head. Authentication/recovery/session/onboarding
behavior and their behavioral tests were preserved.

## Architecture and file inventory (6–8)

API -> application -> domain/repository/UoW ports <- infrastructure -> PostgreSQL.
Planning owns intent and completion. Today owns no persistence. Domain aggregation
is independently testable and deterministic; services use an injected UTC clock.
Today retrieves date assignments/completion-window facts in one account-scoped SQL
statement so counts and wins share one PostgreSQL statement snapshot. No cache or
new dependency was added.

Created:

- `app/api/planning_schemas.py`
- `app/api/routes/planning.py`
- `app/application/planning.py`
- `app/application/today.py`
- `app/domain/planning.py`
- `app/domain/today.py`
- `app/infrastructure/planning.py`
- `app/infrastructure/planning_models.py`
- `docs/frontend-today-dashboard-handoff.md`
- `docs/milestone-2b-validation.md`
- `docs/today-dashboard.md`
- `migrations/versions/0006_daily_planning.py`
- `tests/planning_support.py`
- `tests/test_planning.py`
- `tests/test_planning_api.py`
- `tests/test_planning_infrastructure.py`
- `tests/test_planning_postgresql.py`
- `tests/test_today.py`

Modified:

- `README.md`
- `app/api/errors.py`
- `app/main.py`
- `docs/api-conventions.md`
- `docs/backend-architecture.md`
- `docs/migrations.md`
- `migrations/metadata.py`
- `tests/test_api_contracts.py`
- `tests/test_authentication_persistence.py`
- `tests/test_migrations.py`

## API and exact contracts (9–10)

| Method | Endpoint | Success | Operation ID |
| --- | --- | --- | --- |
| POST | `/api/v1/plan/items` | 201 | create_plan_item |
| GET | `/api/v1/plan/items/{plan_item_id}` | 200 | get_plan_item |
| PATCH | `/api/v1/plan/items/{plan_item_id}` | 200 | update_plan_item |
| POST | `/api/v1/plan/items/{plan_item_id}/complete` | 200 | complete_plan_item |
| GET | `/api/v1/today?timezone=America/New_York` | 200 | get_today_dashboard |

Exact TypeScript-style declarations, all stable enums, field meanings, safe full
response examples, mutation requests and error contracts are in
[frontend-today-dashboard-handoff.md](frontend-today-dashboard-handoff.md).
Authoritative schema source: `app/api/planning_schemas.py`;
OpenAPI source: `app/main.py:create_app().openapi()`.
API version `/api/v1`; Today version `1.0`.

## Planning and Today rules (11–19)

Plan items contain stable UUID, authenticated owner, stripped required title (<=200),
assigned date/IANA zone, optional UTC start/end, explicit priority, authoritative
completion timestamp, creation/update timestamps and positive concurrency version.
End requires start and must be later. PATCH supports only milestone fields; omission
preserves values, only schedule fields may be null. No reopening/deletion exists.
Completion uses server time, clears priority, increments once and is replay-safe.

Current Today resolves server UTC time through the requested validated IANA zone.
No client today-date is accepted. Scheduled input requires explicit offset consistent
with the named zone and an assigned date matching local start. Spring-forward gaps
fail; both fall-back folds work with matching offsets. Scheduled instants remain
absolute; unscheduled assignment dates and stored zones remain explicit intent.
Travel changes the current boundary, not persistence. Local midnight removes prior
assignments; wins use separately converted local-midnight boundaries (23/25-hour
DST days). Start and end remain separate; no departure guidance exists.

Daily Status precedence: no eligible -> getting_started; all complete -> complete;
any incomplete start <= now -> needs_attention; some complete/no overdue -> on_track;
otherwise getting_started. Counts explain only plan facts.

Next Best Action ranks: overdue priority, other overdue, upcoming priority, other
upcoming, unscheduled priority, other unscheduled. Starts/creation and UUID break ties.
Exactly one supported action; selected item uses view and safe evidence. No items
uses new_user/create-first/create; all complete uses insufficient_data/all-complete/
create. No predictions, confidence, health/financial claims or unsupported domains.

Score: owned and assigned-current-date items, deduplicated UUID, weight 1 regardless
of priority; completed/total*100. Decimal ROUND_HALF_UP to whole integer (1/8 -> 13,
1/3 -> 33). No eligible -> no_score/null; eligible without completion -> available/0.
Breakdown preserves item ID, domain, eligibility reason, weight, earned weight and
completion state, ordered by UUID. Goals without measurements never count.

Progress: unavailable/no_supported_measurement_sources, empty metrics. Confirmed
priority/configured goal identifiers remain truthful capability metadata, never fake
measurements or zero values. Capabilities are partial; measurement sources are empty.

Up Next: max four incomplete scheduled eligible items, start/UUID order; start <= now
NOW, first future NEXT, later future LATER. Priorities: max three incomplete explicit
priorities, past-start then scheduled start then unscheduled creation then UUID.
Wins: max three current-local-day completions, newest then UUID, independently of
assignment day; replay never duplicates. Completion immediately exits active sections.

Supported actions exactly: plan_item.create, plan_item.view, plan_item.update,
plan_item.complete. Unsupported Quick Log identifiers are absent. Section-state
available/empty/no_data/unavailable/partial/stale/error meanings are documented;
backend reads are uncached current, with no speculative stale threshold. Shared
persistence outage returns global safe 503, never successful fake empty sections.

## Authorization, transactions, persistence and safety (20–24)

Every route uses valid session + active account + verified email + authoritative
completed onboarding, narrowly scoped to product routes. Ownership comes from claims;
account-ID input is rejected. Every repository lookup/mutation is account-scoped.
Foreign IDs are indistinguishable from missing (404). Existing auth errors remain.

Create canonical hash scopes account/operation/key/request. Account-row lock closes
the absent-receipt concurrency race; item and immutable original-response snapshot
commit atomically. Raw keys are hashed. Same request replays original 201 response;
different request conflicts. Per-account creation serialization is intentionally
conservative. PATCH uses item lock plus expected-version SQL predicate. Completion
locks/advances once and replay makes no commit. UoW owns rollback/close; repositories
never commit. Failed transactions fully roll back. Receipt retention follows item/
account lifecycle; no speculative cleanup/expiration policy was introduced.

Migration 0006_daily_planning follows 0005 in one linear head. Adds plan_items and
plan_creation_receipts only, no seeds/backfills, reversible downgrade; account deletion
cascades. Constraints protect title/version/schedule/completion/audit-time invariants.
Indexes support account/date/start and completion-day reads; scoped receipt uniqueness.
0004 and 0005 are unchanged. Offline model/migration parity passes; live parity is
unexecuted and is not inferred from offline checks.

OpenAPI generates with named closed nested schemas, explicit state enums, unique IDs,
Bearer authentication, success/error request-ID and cache headers, no account-ID input
and no unrestricted response maps. Generated validation artifact:
`/private/tmp/pos-today-openapi.json` (not staged; regenerate from source).

Safe diagnostics reuse fixed structured events, allow-listed operations, request ID,
UUIDs and version. No title/body/free-form preference/credential/SQL/exception text is
emitted. Validation details are empty for product routes. Success/failure responses
set no-store/no-cache. Sensitive-additions scan found no database URL, credential
pattern or private-key block. No .env values were read.

## Executed validation and deferred live checks (25–27)

| Check | Exact result |
| --- | --- |
| Pre-edit complete suite | 283 passed, 52 skipped, 0 failed |
| Focused final new suite | 70 passed, 4 guarded PostgreSQL skipped, 0 failed |
| Final complete suite | 353 passed, 56 skipped, 0 failed; 8.24s |
| Changed Python Ruff | Pass |
| Changed Python Black check | Pass |
| Repository Ruff | 24 findings, all in unchanged files |
| Repository Black | 41 formatting failures, all in unchanged files |
| Compile app/migrations/tests | Pass |
| Architecture/dependency tests | Pass, included in suites |
| OpenAPI generation/schema/ID assertions | Pass, included in suites |
| Offline migration/model metadata parity | Pass, included in suites |
| Alembic history/single head | Pass, head 0006_daily_planning |
| Offline upgrade head / downgrade head:base SQL | Pass |
| Documentation links | 69 local links checked, 0 missing before this report; rechecked afterward |
| Sensitive-additions scan | Pass |
| git diff --check | Pass |

Full suite initially exposed two additive static-inventory failures; corrected only
those inventory expectations (no behavior weakened) and reran to the final clean
result above. Existing unrelated Ruff/Black findings were preserved, not refactored.

POS_TEST_DATABASE_URL was absent. No live PostgreSQL connection, automated cleanup,
live upgrade/downgrade or live metadata comparison was performed. Four new guarded
tests cover PostgreSQL concurrent creation/completion, isolation/version/cascade,
rollback, constraints and upgrade/downgrade parity when the authorized local pos_test
configuration is supplied. Other existing PostgreSQL/optional integration skips remain.
Offline SQL is in `/private/tmp/pos-today-upgrade.sql` and
`/private/tmp/pos-today-downgrade.sql`; neither substitutes for live proof.

Canonical [Today documentation](today-dashboard.md) and the
[frontend handoff](frontend-today-dashboard-handoff.md) are complete, including exact
contracts, missing-data/client-state guidance, all ordering/score/time rules, action
mapping, safe examples, acceptance scenarios and manual sequence. Existing README,
architecture, API and migration guides link the capability.

## Deferred work and founder prerequisites (28–29)

Deferred: nutrition/protein/calories/water/workout/budget/focus/routine logging and
measurements; calendar/reminders/wearables/providers; commute/departure; push;
recurrence/subtasks/automatic scheduling; AI/predictions/confidence/impact/learned
ranking; offline queue/sync; frontend implementation; staging/production deployment.
Unsupported actions must stay hidden. Frontend must never fabricate completions,
measurements, wins, connections or recommendations and owns destination mapping.

Founder/reviewer must approve the diff, supply guarded local pos_test configuration
for live evidence, securely configure existing auth/onboarding dependencies, maintain
IANA data, apply the reviewed migration in an authorized test environment and complete
real registration/email verification/onboarding. Follow the canonical manual sequence,
including stale mutations, cross-account IDs, DST/travel/rollover and persistence
outage. Record the eventual reviewed backend SHA; this uncommitted work has no delivery
SHA yet. No frontend implementation prompt was created.

## Final Git evidence and delivery boundary (30–33)

`git diff --stat` reports tracked changes only (new files remain untracked):

```text
 README.md                                |  7 +++++
 app/api/errors.py                        | 47 ++++++++++++++++++++++++++++++--
 app/main.py                              | 11 ++++++++
 docs/api-conventions.md                  |  7 +++++
 docs/backend-architecture.md             |  4 +++
 docs/migrations.md                       |  7 ++++-
 migrations/metadata.py                   |  5 +++-
 tests/test_api_contracts.py              |  5 ++++
 tests/test_authentication_persistence.py |  2 +-
 tests/test_migrations.py                 |  4 ++-
 10 files changed, 93 insertions(+), 6 deletions(-)
```

New files are listed above and included in the final status below; they are not
omitted from the delivered workspace, only from Git's unstaged tracked diff stat.

`git status --short`:

```text
 M README.md
 M app/api/errors.py
 M app/main.py
 M docs/api-conventions.md
 M docs/backend-architecture.md
 M docs/migrations.md
 M migrations/metadata.py
 M tests/test_api_contracts.py
 M tests/test_authentication_persistence.py
 M tests/test_migrations.py
?? app/api/planning_schemas.py
?? app/api/routes/planning.py
?? app/application/planning.py
?? app/application/today.py
?? app/domain/planning.py
?? app/domain/today.py
?? app/infrastructure/planning.py
?? app/infrastructure/planning_models.py
?? docs/frontend-today-dashboard-handoff.md
?? docs/milestone-2b-validation.md
?? docs/today-dashboard.md
?? migrations/versions/0006_daily_planning.py
?? tests/planning_support.py
?? tests/test_planning.py
?? tests/test_planning_api.py
?? tests/test_planning_infrastructure.py
?? tests/test_planning_postgresql.py
?? tests/test_today.py
```

Nothing was staged, committed, pushed, merged, deployed, applied to a live database,
or changed in the frontend/mobile repository. No branch was created/switched/deleted
and no Git history was modified. Implementation stops at backend code, validation,
documentation and this report.

Final status: **READY WITH DEFERRED LIVE CHECKS**. Live PostgreSQL proof remains
pending; repository-wide formatting/lint debt is pre-existing and isolated from this
diff.
