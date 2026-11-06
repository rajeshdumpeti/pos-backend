# Milestone 2.C implementation and validation report

Final status: **READY WITH DEFERRED LIVE CHECKS**.

## 1–4. Starting state, ancestry, evidence and baseline

Repository: pos-backend. Starting and ending branch feat/backend-plan-v1.
HEAD remains 6bde40e0800ef36a9edefec9c6acbf5cc16d1e74; starting working tree
clean. origin/develop...HEAD: 0 behind, 0 ahead. No merge/rebase/cherry-pick/
revert/bisect marker was active. No Git branch/history operation was performed.
The earlier develop-branch blocker was resolved before this request began.

Inspected supplied implementation request; governance AGENTS.md, complete manifest,
charter and product register; backend README/CONTRIBUTING, architecture, API,
data-access, relational and migration guides; Today documentation/handoff and
2.B report; domain/application Today and planning, repository/UoW, planning HTTP
schemas/routes, completed-onboarding gate, session test boundary, safe errors,
request IDs, cache handling, schema/indexes, migration 0006 and metadata, existing
planning/Today/API/architecture/migration tests and PostgreSQL safeguards.
No standalone approved Plan specification, roadmap or cursor utility was found
in backend/governance. The supplied detailed request defines approved scope;
old UI-first restrictions are superseded by explicit backend authorization.
No frontend repository was modified, and no .env values were read.

Pre-implementation baseline: `.venv/bin/pytest -q` → **353 passed, 56 skipped**,
8.83s. Today contract 1.0, authentication/onboarding, daily planning persistence,
create/get/update/complete and migration 0006 all existed at that baseline.

## 5–7. Implemented architecture and file inventory

Thin GET route → PlanSelectedDateService → existing planning repository/UoW port
← SQLAlchemy adapter → domain-owned plan_items. Pure domain functions construct
summary/order/timing/insight; named immutable application projection and named
closed HTTP schemas expose the read. Repositories do not commit/create sessions;
reads close without commit. One SQL statement supplies full-date facts.

Created:

- `app/api/plan_read_schemas.py`
- `app/application/plan_selected_date.py`
- `app/domain/plan_read.py`
- `app/domain/plan_selection.py`
- `docs/examples/plan-v1-fixtures.json`
- `docs/frontend-plan-v1-handoff.md`
- `docs/plan-v1.md`
- `tests/test_plan_selected_date.py`
- `tests/test_plan_selected_date_api.py`
- `tests/test_plan_selected_date_infrastructure.py`
- `tests/test_plan_selected_date_postgresql.py`
- `docs/milestone-2c-validation.md`

Modified:

- `README.md`
- `app/api/routes/planning.py`
- `app/application/planning.py`
- `app/domain/today.py`
- `app/infrastructure/planning.py`
- `app/main.py`
- `docs/api-conventions.md`
- `docs/backend-architecture.md`
- `docs/migrations.md`
- `tests/planning_support.py`
- `tests/test_api_contracts.py`

## 8–9. API inventory and exact selected-date contract

| Method | Path | Result / operation ID |
| --- | --- | --- |
| GET | /api/v1/plan | 200 PlanSelectedDateResponse / get_plan_selected_date (new) |
| POST | /api/v1/plan/items | 201 PlanItemResponse / create_plan_item (preserved) |
| GET | /api/v1/plan/items/{plan_item_id} | 200 PlanItemResponse / get_plan_item (preserved) |
| PATCH | /api/v1/plan/items/{plan_item_id} | 200 PlanItemResponse / update_plan_item (preserved) |
| POST | /api/v1/plan/items/{plan_item_id}/complete | 200 PlanItemResponse / complete_plan_item (preserved) |
| GET | /api/v1/today | 200 TodayResponse / get_today_dashboard (preserved) |

Selected-date query requires selected_date (YYYY-MM-DD) and IANA timezone;
limit defaults 50, 1–100, optional opaque cursor. Extra fields reject. Plan contract
version 1.0; Today contract 1.0 remains separate. Response contains contract_version,
generated_at, timezone, selected_date, date_relation, freshness, summary, insight,
items, page. Exact named closed schemas and stable enums are in
[frontend-plan-v1-handoff.md](frontend-plan-v1-handoff.md), sourced from
[plan_read_schemas.py](../app/api/plan_read_schemas.py). No duplicate route exists.

## 10–17. Date, summary, ordering, pagination, timing and insight

Ownership is authenticated account + durable scheduled_for_date. Requested timezone
classifies selected date versus server-local today. Stored timezone/absolute start/
end preserve scheduling intent across travel; no date inference from converted
start. Existing DST gaps/folds, offset, assigned date and end-after-start validation
remain unchanged.

Past dates describe current authoritative state only. Later completion is currently
completed, with visible completed_at; unfinished past assignments are still
incomplete. No historical snapshot exists or is claimed.

Summary counts distinct full-date IDs, current completed/remaining and scheduled/
unscheduled remaining. Both invariants hold. Equal-weight Decimal ROUND_HALF_UP
completion: empty null, 0/2=0, 1/3=33, 1/8=13, 2/2=100. No priority/goal weighting.

Global sections: scheduled incomplete (start ASC, UUID ASC), unscheduled incomplete
(priority DESC, creation ASC, UUID ASC), completed (completion DESC, UUID ASC).
Priority never changes timeline chronology; completed exits active sections.

Cursor keyset preserves this global order, using integer microseconds for exact
instants. Versioned canonical URL-safe base64 JSON has only a query fingerprint
and sorting fields, no credential/private text. Context is derived from account/
date/timezone/order version; a fingerprint is not a signature or authorization.
Strict decoding rejects invalid bounds/types/UUID/form/context. SQL always uses
principal predicates; a manipulated valid position cannot expose foreign data.
Unchanged traversal has every UUID once. Summary/insight cover the entire date;
final next_cursor is null. Mutations can move results: no inter-request snapshot
guarantee. Successful mutations require page-one refetch (both dates if reassigned).

Timing: completed overrides all; no start = unscheduled; start > now = upcoming;
start <= now < end = ongoing; end <= now or point start <= now = past. Exact
interval start is ongoing; exact point start/end is past. No grace/commute logic.

Current insight: ongoing priority/other → past priority/other → upcoming priority/
other → unscheduled priority/other → complete → empty. Past: incomplete scheduled
priority/other → incomplete unscheduled priority/other → currently complete → empty.
Future: earliest scheduled priority/other → unscheduled priority/other → already
complete → empty. Start/creation/UUID resolve ties. Stable 17-code enum, one factual
recommendation/reason, supported create/view CTA, authoritative optional UUID and
allow-listed matching evidence. No prediction/confidence/benefit/personalization.

Modeled InsightComputationError only → unavailable/insight_unavailable with generic
copy, null action/UUID and full-date count evidence; items/summary remain usable.
Persistence fails globally with safe 503; unexpected computation/application error
remains safe 500. No catch-all obscures retrieval failure.

## 18–21. Isolation, compatibility, persistence and diagnostics

Existing bearer, active-account, verified-email and completed-onboarding gates
remain. Tested missing/invalid/expired session 401 and inactive/unverified/incomplete
403. No account ID input. Repository and projection exclude other accounts/dates;
context misuse rejects and forged position does not expose foreign records.

Create hashing/idempotent original-response replay, retrieve-one, update/version
conflict, completed edit restriction, version increments and replay-safe completion
are preserved. Shared pure ranking retains Today's frozen six past-start tiers;
Plan opts into an ongoing split. Existing Today response/score/priorities/Up Next/
Wins remain unchanged; regression includes byte-equivalent JSON before/after a
Plan read and a fixture demonstrating the intentional Plan/Today timing distinction.

No migration or persistence model change. Existing account/date/start index supports
the new equality predicates. Full-date materialization/sort is application-owned:
O(n) facts, O(n log n) ordering, bounded output, no bounded SQL-work claim. Alembic
head remains 0006_daily_planning. Offline model/migration parity passes; live proof
is deferred. Existing cascade/constraint tests remain intact.

Safe allow-listed structured diagnostics add selected-date read and insight-unavailable
operations/outcomes. Only bounded request ID, account UUID, null item UUID and
version 0 are recorded for reads/fallback. No cursor/title/body/email/token/URL/SQL/
exception text. Server-generated UTC generated_at equals freshness.as_of. Current-
only freshness, no-store/no-cache and X-Request-ID are preserved. Sensitive additions
scan found no credential URL, private-key block, access-key or credential assignment.

## 22–25. Validation evidence

| Check | Result |
| --- | --- |
| Baseline complete database-independent suite | 353 passed, 56 skipped; 8.83s |
| Focused new Plan suite, final | 111 passed, 2 guarded PostgreSQL skipped; 2.70s |
| Plan + Today/planning regression intermediate | 174 passed, 6 guarded skips; 4.47s |
| Today/planning/API/infrastructure/architecture/migration regression final | 87 passed; 94.25s |
| Complete suite | 464 passed, 58 skipped; 10.67s |
| Changed Python Ruff | Pass, 15 files |
| Changed Python Black check | Pass, 15 files |
| Repository Ruff | 24 pre-existing findings in 9 unchanged files; no changed-file findings |
| Repository Black | 41 unchanged files need formatting; no changed-file failures |
| Python compilation app/migrations/tests | Pass |
| Dependency/layer and UoW safety checks | Pass |
| Alembic heads/history | Single linear head 0006_daily_planning |
| Offline upgrade head / downgrade head:base SQL | Pass; no database/config access in offline path |
| Offline planning migration/model metadata parity | Pass |
| Guarded PostgreSQL/live migration/parity | NOT RUN: POS_TEST_DATABASE_URL unavailable |
| OpenAPI | Generated; 27 unique operation IDs, 13 named Plan response components checked closed |
| Documentation links | 90 local links, 0 missing after report |
| Sensitive additions scan | Pass |
| git diff --check | Pass |

Generated validation artifacts outside the repository:
/private/tmp/pos-plan-openapi.json, pos-plan-upgrade.sql, pos-plan-downgrade.sql,
pos-plan-ruff.txt, pos-plan-black.txt, pos-plan-lint-evidence.json.
No artifact contains live credentials or payloads. Offline SQL is not live proof.
Two new guarded PostgreSQL tests cover account/date isolation, nullable schedule,
current past-date completion, stable global ordering/cursor traversal, full summary
and cursor misuse. Existing guarded PostgreSQL tests cover migration upgrade/
downgrade, metadata parity, constraints and cascade. Safety guard is unchanged.

Repository lint locations (all verified outside the changed-file set):

- `app/application/email_delivery.py:7 F401`
- `app/application/password_recovery.py:216 F841`
- `app/infrastructure/authentication/abuse_control.py:116 E731`
- `app/infrastructure/authentication/session_security.py:185 F841`
- `tests/test_authentication_hardening.py:4 F401`
- `tests/test_authentication_hardening_postgresql.py:20 F401`
- `tests/test_authentication_hardening_postgresql.py:20 F401`
- `tests/test_authentication_hardening_postgresql.py:20 F401`
- `tests/test_authentication_hardening_postgresql.py:34 F811`
- `tests/test_authentication_hardening_postgresql.py:44 F811`
- `tests/test_authentication_hardening_postgresql.py:44 F811`
- `tests/test_authentication_hardening_postgresql.py:60 F811`
- `tests/test_authentication_hardening_postgresql.py:60 F811`
- `tests/test_authentication_hardening_postgresql.py:74 F811`
- `tests/test_authentication_hardening_postgresql.py:74 F811`
- `tests/test_authentication_hardening_postgresql.py:87 F811`
- `tests/test_authentication_hardening_postgresql.py:87 F811`
- `tests/test_authentication_postgresql.py:81 E702`
- `tests/test_authentication_postgresql.py:83 E702`
- `tests/test_authentication_postgresql.py:83 E702`
- `tests/test_authentication_postgresql.py:90 E702`
- `tests/test_password_recovery.py:347 E731`
- `tests/test_registration_postgresql.py:20 F401`
- `tests/test_registration_postgresql.py:23 F401`

Black failures (all unchanged):

- `app/api/authentication_schemas.py`
- `app/api/routes/authentication.py`
- `app/application/authentication_security.py`
- `app/application/email_registration.py`
- `app/application/password_recovery.py`
- `app/application/resources.py`
- `app/config.py`
- `app/core/authentication_security.py`
- `app/core/request_id.py`
- `app/domain/authentication.py`
- `app/infrastructure/authentication/__init__.py`
- `app/infrastructure/authentication/abuse_control.py`
- `app/infrastructure/authentication/composition.py`
- `app/infrastructure/authentication/email_delivery.py`
- `app/infrastructure/authentication/local_email.py`
- `app/infrastructure/authentication/models.py`
- `app/infrastructure/authentication/password_recovery_composition.py`
- `app/infrastructure/authentication/registration.py`
- `app/infrastructure/authentication/session_composition.py`
- `app/infrastructure/authentication/session_security.py`
- `migrations/env.py`
- `migrations/versions/0002_authentication_persistence_foundation.py`
- `migrations/versions/0003_registration_verification_flow.py`
- `tests/test_api.py`
- `tests/test_architecture.py`
- `tests/test_authentication_contract_v1.py`
- `tests/test_authentication_email_delivery.py`
- `tests/test_authentication_hardening.py`
- `tests/test_authentication_hardening_postgresql.py`
- `tests/test_authentication_postgresql.py`
- `tests/test_authentication_redis.py`
- `tests/test_config.py`
- `tests/test_database_redaction.py`
- `tests/test_password_recovery.py`
- `tests/test_password_recovery_postgresql.py`
- `tests/test_persistence_contracts.py`
- `tests/test_registration_legal_policy.py`
- `tests/test_registration_postgresql.py`
- `tests/test_registration_security.py`
- `tests/test_session_api.py`
- `tests/test_session_security.py`

## 26–27. Deferred scope and founder prerequisites

Deferred: recurrence/repeats/subtasks/manual ordering/optimization; provider/calendar
sync; health/nutrition/workout/money/focus and all other measurements; offline writes/
sync; historical snapshots/event sourcing; AI/predictions/confidence/benefits/
fabricated personalization; frontend implementation; deployment.

Founder must review the diff, record delivery SHA after separately authorized Git
delivery, configure existing auth/session/onboarding securely, maintain IANA data,
and use migration 0006 in an authorized test environment. Live destructive tests
require guarded local pos_test via POS_TEST_DATABASE_URL, never the development
DB or .env credential lookup. Follow the ten-step
[Swagger/manual verification](plan-v1.md#swagger-and-founder-manual-verification)
and frontend acceptance fixtures. Real UI/Swagger clicks and live PostgreSQL proof
remain operator checks; automatic schema generation is not a claim of manual execution.

## 28–31. Final Git state and delivery boundary

Tracked `git diff --stat` (new untracked files are listed separately above):

```text
 README.md                      |  8 ++++++++
 app/api/routes/planning.py     | 43 ++++++++++++++++++++++++++++++++++++++++++
 app/application/planning.py    |  3 +++
 app/domain/today.py            | 10 ++--------
 app/infrastructure/planning.py | 25 ++++++++++++++++++++++--
 app/main.py                    |  1 +
 docs/api-conventions.md        |  8 ++++++++
 docs/backend-architecture.md   |  8 ++++++++
 docs/migrations.md             |  5 +++++
 tests/planning_support.py      |  8 ++++++++
 tests/test_api_contracts.py    |  1 +
 11 files changed, 110 insertions(+), 10 deletions(-)
```

`git status --short`:

```text
 M README.md
 M app/api/routes/planning.py
 M app/application/planning.py
 M app/domain/today.py
 M app/infrastructure/planning.py
 M app/main.py
 M docs/api-conventions.md
 M docs/backend-architecture.md
 M docs/migrations.md
 M tests/planning_support.py
 M tests/test_api_contracts.py
?? app/api/plan_read_schemas.py
?? app/application/plan_selected_date.py
?? app/domain/plan_read.py
?? app/domain/plan_selection.py
?? docs/examples/
?? docs/frontend-plan-v1-handoff.md
?? docs/plan-v1.md
?? tests/test_plan_selected_date.py
?? tests/test_plan_selected_date_api.py
?? tests/test_plan_selected_date_infrastructure.py
?? tests/test_plan_selected_date_postgresql.py
?? docs/milestone-2c-validation.md
```

Nothing was staged, committed, pushed, merged or deployed. No branch was created,
switched, renamed or deleted; HEAD/history were unchanged. No live database
migration/cleanup, provider call or frontend/mobile edit occurred. No unrelated
work was cleaned, reverted or stashed. Delivery stops at reviewable backend code,
tests, documentation and this report.

Final status: **READY WITH DEFERRED LIVE CHECKS**. Implementation and database-
independent checks are complete; authorized live PostgreSQL/migration/parity and
founder manual Swagger checks remain deferred. Unrelated lint/format debt remains.
