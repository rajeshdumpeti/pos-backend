# Frontend Today Dashboard handoff

Backend SHA: record `git rev-parse HEAD` after review/commit; this implementation
is intentionally uncommitted and the starting SHA is not its delivery SHA.
API `/api/v1`; Today contract `1.0`. Pin the reviewed backend SHA and contract
version. Canonical behavior: [today-dashboard.md](today-dashboard.md).
OpenAPI generation source: `app/main.py:create_app().openapi()`;
request/response source: `app/api/planning_schemas.py`. GET `/openapi.json` is
available without secrets. All object schemas are closed. No account-ID inputs.

## Endpoints

| Method | Endpoint | Request | Response |
| --- | --- | --- | --- |
| POST | `/api/v1/plan/items` | CreatePlanItemRequest + required Idempotency-Key header | 201 PlanItemResponse |
| GET | `/api/v1/plan/items/{plan_item_id}` | UUID path | 200 PlanItemResponse |
| PATCH | `/api/v1/plan/items/{plan_item_id}` | UpdatePlanItemRequest | 200 PlanItemResponse |
| POST | `/api/v1/plan/items/{plan_item_id}/complete` | CompletePlanItemRequest | 200 PlanItemResponse |
| GET | `/api/v1/today?timezone=America/New_York` | required IANA timezone; no client date | 200 TodayResponse |

Every request uses Bearer authentication. Every success/failure is no-store/no-cache
and carries X-Request-ID. Optional X-Request-ID must follow the existing bounded
safe ASCII contract. Idempotency-Key is required only on create, 1–128 safe ASCII
characters, starting with a letter/number. JSON null and omission are distinct.
PATCH only allows explicit null for start/end; other optional fields, when supplied,
must have values. Expected versions are integers >= 1. Titles are stripped and
bounded to 200 characters. Instants must include explicit offsets; integer epoch
inputs are rejected. Date strings use YYYY-MM-DD; UUID strings remain opaque.

## Exact TypeScript-style contracts and stable enums

Derived from named OpenAPI components. `never[]` means always empty in v1. Dates,
UUIDs and instants serialize as strings; displayed timestamps normally end in Z.
PATCH non-schedule fields are non-null when supplied, as reflected in OpenAPI.

```ts
export type ActionIdentifier = "plan_item.create" | "plan_item.view" | "plan_item.update" | "plan_item.complete";

export interface CapabilitiesResponse {
  state: SectionState;
  actions: Array<ActionIdentifier>;
  priority_life_areas: Array<PriorityLifeArea>;
  configured_goals: Array<GoalIdentifier>;
  measurement_sources: never[];
}

export interface CompletePlanItemRequest {
  expected_version: number;
}

export type CompletionState = "incomplete" | "completed";

export interface CreatePlanItemRequest {
  title: string;
  scheduled_for_date: string;
  timezone: string;
  scheduled_start_at?: string | null;
  scheduled_end_at?: string | null;
  is_priority?: boolean;
}

export type DailyStatusCode = "getting_started" | "complete" | "needs_attention" | "on_track";

export interface DailyStatusResponse {
  state: SectionState;
  code: DailyStatusCode;
  eligible: number;
  completed: number;
  remaining: number;
  overdue: number;
}

export type DomainType = "plan_item";

export interface FreshnessResponse {
  state: "current";
  as_of: string;
}

export type GoalIdentifier = "target_weight" | "daily_protein_grams" | "daily_water" | "daily_calories_kcal" | "daily_steps" | "workouts_per_week" | "focus_minutes_per_day" | "target_sleep_minutes" | "budget_target";

export interface ItemSummaryResponse {
  plan_item_id: string;
  title: string;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  scheduled_for_date: string;
  timezone: string;
  domain: DomainType;
  state: CompletionState;
  action: ActionIdentifier;
  completed_at: string | null;
}

export interface PlanItemResponse {
  id: string;
  title: string;
  scheduled_for_date: string;
  timezone: string;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  is_priority: boolean;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export type PriorityLifeArea = "health" | "nutrition" | "fitness" | "focus" | "money" | "habits" | "sleep";

export interface PrioritySectionResponse {
  state: SectionState;
  items: Array<ItemSummaryResponse>;
  action: ActionIdentifier;
}

export interface ProgressResponse {
  state: SectionState;
  reason: "no_supported_measurement_sources";
  metrics: never[];
}

export type RecommendationCode = "priority_past_start" | "past_start" | "priority_upcoming" | "upcoming" | "priority_unscheduled" | "unscheduled" | "create_first_plan_item" | "all_plan_items_complete";

export interface RecommendationEvidenceResponse {
  eligible: number;
  remaining: number;
  is_priority: boolean | null;
  scheduled_start_at: string | null;
}

export interface RecommendationResponse {
  state: RecommendationState;
  code: RecommendationCode;
  domain: DomainType;
  recommendation: string;
  reason: string;
  action: ActionIdentifier;
  plan_item_id: string | null;
  evidence: RecommendationEvidenceResponse;
}

export type RecommendationState = "populated" | "new_user" | "insufficient_data";

export interface ScoreContributionResponse {
  item_id: string;
  domain: DomainType;
  eligibility_reason: "assigned_current_local_date";
  weight: 1;
  earned_weight: 0 | 1;
  completion_state: CompletionState;
}

export interface ScoreResponse {
  state: ScoreState;
  percentage: number | null;
  numerator: number;
  denominator: number;
  breakdown: Array<ScoreContributionResponse>;
}

export type ScoreState = "available" | "no_score";

export type SectionState = "available" | "empty" | "no_data" | "unavailable" | "partial" | "stale" | "error";

export interface TodayResponse {
  contract_version: "1.0";
  generated_at: string;
  timezone: string;
  local_date: string;
  freshness: FreshnessResponse;
  daily_status: DailyStatusResponse;
  next_best_action: RecommendationResponse;
  score: ScoreResponse;
  progress: ProgressResponse;
  up_next: UpNextSectionResponse;
  top_priorities: PrioritySectionResponse;
  wins: WinsSectionResponse;
  capabilities: CapabilitiesResponse;
}

export interface UpNextItemResponse {
  item: ItemSummaryResponse;
  label: UpNextLabel;
}

export type UpNextLabel = "NOW" | "NEXT" | "LATER";

export interface UpNextSectionResponse {
  state: SectionState;
  items: Array<UpNextItemResponse>;
  action: ActionIdentifier;
}

export interface UpdatePlanItemRequest {
  expected_version: number;
  title?: string;
  scheduled_for_date?: string;
  timezone?: string;
  scheduled_start_at?: string | null;
  scheduled_end_at?: string | null;
  is_priority?: boolean;
}

export interface WinsSectionResponse {
  state: SectionState;
  items: Array<ItemSummaryResponse>;
  action: ActionIdentifier;
}
```

## Field meanings, state and frontend behavior

`generated_at`/`freshness.as_of` are server UTC instants from the same read;
`timezone`/`local_date` are the authoritative current-day boundary. `daily_status`
counts and code explain only eligible plan items. `next_best_action` contains one
code/action, optional authoritative item UUID, factual reason and allow-listed
evidence. Resolve selected text through item GET or supplied summary sections.
`score` is daily plan completion; denominator is total distinct eligible IDs,
numerator is completed IDs. Breakdown reproduces the score without priority weights.
`progress` has no populated metrics. `up_next` wraps each real summary with a label;
`top_priorities` and `wins` contain at most three summaries each. Summary `timezone`
is the stored assignment zone; `completed_at` is nullable. `capabilities` lists
implemented actions, confirmed priority areas and configured goal identifiers;
configured does not mean measured. No target/current-value numbers are inferred.

Section states: available = authoritative data; empty = collection with no records;
no_data = supported concept without current value; unavailable = unsupported source;
partial = some capability facts missing; stale = documented older authoritative data;
error = sanitized failure. Do not render absence as zero. Score `no_score` has null
percentage, including denominator zero. Available 0 is legitimate. Backend v1
freshness is current only; it claims no stale threshold or successful offline data.

Loading: use calm placeholders. Refreshing: preserve previously confirmed UI with a
visible refresh state until the response arrives. Offline: indicate unavailable/
previously fetched data as appropriate to approved client storage policy; respect
no-store and do not create a backend freshness claim. Stale: frontend presentation
must explicitly identify an older response, not change its server freshness fields.
Partial/unavailable: show only supported planning facts; hide unsupported measurement
cards and actions. Empty: show supported plan creation CTA. Error: map safe codes,
preserve request ID, allow deliberate retry; never replace authoritative failures
with fabricated successful zero/empty data. No offline mutation queue is implemented.

## Daily Status, recommendation, score and collections

Daily Status precedence: no eligible -> getting_started; all completed -> complete;
any incomplete start <= now -> needs_attention; some complete/no overdue -> on_track;
otherwise getting_started. Never infer emotional state, health or self-worth.

Recommendation precedence: past-start priority; other past-start; upcoming priority;
other upcoming; unscheduled priority; other unscheduled. Order starts (or unscheduled
creation time), then stable UUID. Selected item -> populated/view. No items ->
new_user/create-first/create. All complete -> insufficient_data/all-complete/create.
Evidence must match the returned resource; exactly one supported action is present.
No confidence, future impact, prediction, nutrition/financial/health guidance exists.

Score: owner + explicit current-date assignment, distinct UUIDs, all weights 1;
completed / total * 100, Decimal ROUND_HALF_UP. Examples: no items -> null; 0/2 -> 0;
1/3 -> 33; 1/8 -> 13; 2/2 -> 100. No goal-only contribution, inferred weighting or
optimistic predicted percentage. Breakdown preserves numerator and denominator.

Up Next: max four, incomplete eligible scheduled items by start then UUID. Every
start <= now -> NOW; first future -> NEXT; later future -> LATER. No fabricated NOW.
Top Priorities: max three explicit active priorities, past-start then scheduled start,
then unscheduled creation, then UUID. Wins: max three actual completions in current
local day, newest then UUID, including prior-date assignments completed today.
Partial progress never becomes a win. Completion replay never duplicates wins.

## Dates, authorization and mutations

Current timezone is a required validated IANA identifier. Backend determines today
from server UTC time, never a supplied client date. Start offset must match the named
zone and assigned date. End offset must match its own instant and end > start. Spring
DST gaps are rejected; both fall folds are accepted with the appropriate offset.
Travel changes the requested current-day boundary, not stored assignment or absolute
instants. Prior assigned date leaves Today at local midnight. Display stored timezone
and absolute start/end correctly; never derive commute/departure time. Unscheduled
items retain explicitly assigned dates. See canonical documentation for PATCH date/
zone consistency and 23/25-hour completion-day windows.

Authentication missing/invalid/expired -> existing 401 flow. Active-account/verified
email/onboarding gates use existing 403 codes. Only authoritative completed onboarding
routes to Today. Foreign-account UUIDs are indistinguishable from missing (404).
Validation is safe 422 with empty details; database outage is 503 dependency_unavailable;
unexpected error is sanitized 500. No auth success shapes or unrelated gates change.

Create receipt scopes account + operation + key + normalized request hash. Retrying
the same key/payload returns the original 201 resource even after later edits; fetch
the current item/Today after a replay when needed. Changed payload under that key ->
409 plan_item_idempotency_conflict. PATCH requires current expected_version; committed
update increments once. Stale or completed edit -> 409 plan_item_state_conflict. On
conflict fetch current item and ask the user to reconcile; do not silently overwrite.
Completion uses server time and advances once; repeated completion returns authoritative
completed state even with an old expected_version. Do not optimistically claim success
before the backend mutation succeeds. Failed transactions leave no partial state.

Safe error envelope:

```ts
interface SafeError {
  code: string;
  message: string;
  request_id: string;
  details: Array<{ field: string; code: string; message: string }>;
}
```

## Synthetic examples and acceptance scenarios

Creation (Idempotency-Key header chosen once per logical creation):

```json
{"title":"Prepare tomorrow's priorities","scheduled_for_date":"2026-10-02","timezone":"America/New_York","scheduled_start_at":"2026-10-02T18:00:00-04:00","scheduled_end_at":"2026-10-02T18:30:00-04:00","is_priority":true}
```

PATCH: `{"expected_version":1,"title":"Prepare priorities"}`.
Completion: `{"expected_version":2}`. Error example:
`{"code":"plan_item_state_conflict","message":"Plan item version conflicts","request_id":"today-test","details":[]}`.

Acceptance: completed-onboarding user sees first-plan empty state; creates/replays a
priority; sees legitimate zero; creates a scheduled item with separate start/end;
updates with concurrency; completes and sees one win and changed score; completion
leaves priorities/Up Next; stale edits safely conflict; cross-account IDs return 404;
unsupported goals never show zero measurements; exact-start label is NOW; future-only
list has NEXT and no NOW; rollover/travel changes eligibility without rewriting
stored intent; DST gaps/folds validate correctly; outage never becomes fake empty data.

## Implemented, deferred, destination mapping and founder prerequisites

Implemented actions are exactly plan_item.create, plan_item.view, plan_item.update,
plan_item.complete. Frontend owns mapping these domain/resource identifiers to screens
or modals; backend identifiers are not route names. Unsupported actions must remain
hidden. Frontend must not fabricate completion, measurements, wins, connected
integrations, or recommendations. No configured integration preference is a connection.

Nutrition, water, workouts, money, focus measurements, routine logging, calendar,
reminders, wearables, commute, push, recurring tasks, subtasks, AI/predictions/confidence/
expected impact, learned ranking, offline queue/synchronization, frontend implementation
and deployment are deferred. Progress is deliberately unavailable; maximum future
visible measurements remains five and requires a later contract.

Founder prerequisites: review this backend diff, record delivery SHA after approved
Git delivery, configure existing authentication/database dependencies securely,
maintain system timezone data, apply the reviewed migration in an authorized test
environment, complete real registration/verification/onboarding, and run the canonical
manual sequence. Automated destructive PostgreSQL tests require guarded local pos_test
via POS_TEST_DATABASE_URL. Review live upgrade/downgrade and parity evidence before
claiming PostgreSQL readiness. No production/development cleanup is authorized.

## Complete safe response examples

Creation response (synthetic UUID):

```json
{
  "id": "00000000-0000-4000-8000-000000000002",
  "title": "Prepare tomorrow's priorities",
  "scheduled_for_date": "2026-10-02",
  "timezone": "America/New_York",
  "scheduled_start_at": "2026-10-02T22:00:00Z",
  "scheduled_end_at": "2026-10-02T22:30:00Z",
  "is_priority": true,
  "completed_at": null,
  "created_at": "2026-10-02T14:00:00Z",
  "updated_at": "2026-10-02T14:00:00Z",
  "version": 1
}
```

Empty Today response (synthetic onboarding priority):

```json
{
  "contract_version": "1.0",
  "generated_at": "2026-10-02T14:00:00Z",
  "timezone": "America/New_York",
  "local_date": "2026-10-02",
  "freshness": {
    "state": "current",
    "as_of": "2026-10-02T14:00:00Z"
  },
  "daily_status": {
    "state": "available",
    "code": "getting_started",
    "eligible": 0,
    "completed": 0,
    "remaining": 0,
    "overdue": 0
  },
  "next_best_action": {
    "state": "new_user",
    "code": "create_first_plan_item",
    "domain": "plan_item",
    "recommendation": "Create a plan item",
    "reason": "No incomplete eligible plan items",
    "action": "plan_item.create",
    "plan_item_id": null,
    "evidence": {
      "eligible": 0,
      "remaining": 0,
      "is_priority": null,
      "scheduled_start_at": null
    }
  },
  "score": {
    "state": "no_score",
    "percentage": null,
    "numerator": 0,
    "denominator": 0,
    "breakdown": []
  },
  "progress": {
    "state": "unavailable",
    "reason": "no_supported_measurement_sources",
    "metrics": []
  },
  "up_next": {
    "state": "empty",
    "items": [],
    "action": "plan_item.create"
  },
  "top_priorities": {
    "state": "empty",
    "items": [],
    "action": "plan_item.create"
  },
  "wins": {
    "state": "empty",
    "items": [],
    "action": "plan_item.create"
  },
  "capabilities": {
    "state": "partial",
    "actions": [
      "plan_item.create",
      "plan_item.view",
      "plan_item.update",
      "plan_item.complete"
    ],
    "priority_life_areas": [
      "focus"
    ],
    "configured_goals": [],
    "measurement_sources": []
  }
}
```
