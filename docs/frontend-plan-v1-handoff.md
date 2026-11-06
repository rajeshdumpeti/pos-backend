# Frontend Plan v1 handoff

Delivery SHA: `<reviewed-backend-delivery-sha>` (record after approved Git delivery;
this implementation is intentionally uncommitted). API `/api/v1`; Plan read
contract `1.0`, separate from unchanged Today contract `1.0`. Canonical backend
behavior: [plan-v1.md](plan-v1.md). OpenAPI generation source:
`app/main.py:create_app().openapi()`, exposed at `/openapi.json`. HTTP schema source:
[plan_read_schemas.py](../app/api/plan_read_schemas.py). This is a contract handoff,
not a frontend implementation prompt.

## Endpoint and query

`GET /api/v1/plan?selected_date=YYYY-MM-DD&timezone=America/New_York&limit=50&cursor=<opaque>`
Operation ID `get_plan_selected_date`. Required Bearer session and completed
onboarding. Unknown query fields are rejected. No account-ID query/body field.
Date is an explicit valid YYYY-MM-DD; timezone is an IANA name. Limit is integer
1–100, default 50. Cursor comes from the preceding page and is opaque. Response
200 is PlanSelectedDateResponse. No duplicate selected-date route exists.

## Exact TypeScript-style contract and enums

All dates, instants and UUIDs serialize as strings. UTC response instants use Z.
Nullable fields are required response keys; only query limit/cursor are optional.
Object schemas are named and closed. No unrestricted response dictionaries.

```ts
export interface PlanSelectedDateQuery {
  selected_date: string;
  timezone: string;
  limit?: number;
  cursor?: string;
}
export type PlanDateRelation = "past" | "current" | "future";
export type PlanItemSection = "scheduled_incomplete" | "unscheduled_incomplete" | "completed";
export type PlanTimingState = "ongoing" | "past" | "upcoming" | "unscheduled" | "completed";
export type PlanInsightState = "available" | "empty" | "unavailable" | "error";
export type PlanInsightAction = "plan_item.create" | "plan_item.view";
export type PlanInsightCode =
  | "current_ongoing_priority" | "current_ongoing"
  | "current_past_priority" | "current_past"
  | "current_upcoming_priority" | "current_upcoming"
  | "current_unscheduled_priority" | "current_unscheduled"
  | "past_remaining_priority" | "past_remaining"
  | "future_scheduled_priority" | "future_scheduled"
  | "future_unscheduled_priority" | "future_unscheduled"
  | "selected_date_complete" | "selected_date_empty" | "insight_unavailable";
export interface PlanFreshnessResponse {
  state: "current";
  as_of: string;
}
export interface PlanSummaryResponse {
  total: number;
  completed: number;
  remaining: number;
  scheduled_remaining: number;
  unscheduled_remaining: number;
  completion_percentage: number | null;
}
export interface PlanDayItemResponse {
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
  section: PlanItemSection;
  timing_state: PlanTimingState;
  action: "plan_item.view";
}
export interface PlanPageResponse {
  limit: number;
  next_cursor: string | null;
  has_more: boolean;
}
export interface PlanInsightEvidenceResponse {
  selected_date: string;
  date_relation: PlanDateRelation;
  total: number;
  completed: number;
  remaining: number;
  is_priority: boolean | null;
  timing_state: PlanTimingState | null;
  scheduled_start_at: string | null;
}
export interface PlanInsightResponse {
  state: PlanInsightState;
  code: PlanInsightCode;
  recommendation: string;
  reason: string;
  action: PlanInsightAction | null;
  plan_item_id: string | null;
  evidence: PlanInsightEvidenceResponse;
}
export interface PlanSelectedDateResponse {
  contract_version: "1.0";
  generated_at: string;
  timezone: string;
  selected_date: string;
  date_relation: PlanDateRelation;
  freshness: PlanFreshnessResponse;
  summary: PlanSummaryResponse;
  insight: PlanInsightResponse;
  items: PlanDayItemResponse[];
  page: PlanPageResponse;
}
export interface SafeError {
  code: string;
  message: string;
  request_id: string;
  details: Array<{ field: string; code: string; message: string }>;
}
```

## Summary, sections, ordering and timing

Summary/insight cover **all items currently assigned to this date**, never just
loaded pages. Completed and remaining sum to total; scheduled_remaining and
unscheduled_remaining sum to remaining. Distinct UUIDs have equal weight. Decimal
ROUND_HALF_UP gives 0 items → null, 0/2 → 0, 1/3 → 33, 1/8 → 13, 2/2 → 100.
Null is absent percentage; zero is genuine completion data. No goals/measurement
or priority weighting. Never derive authoritative counts from optimistic UI.

Render global sections in order: scheduled_incomplete, unscheduled_incomplete,
completed. Scheduled items sort by start ASC then UUID ASC, regardless of priority.
Unscheduled sort priority DESC then created_at ASC then UUID ASC. Completed sort
completed_at DESC then UUID ASC. Pages continue that single global order; do not
locally regroup in a way that changes chronology. Completion removes an item from
incomplete sections. Priority is an inline indicator, not drag/persisted order.

Use timing_state from the server. Completed always means completed; no start means
unscheduled. Future start means upcoming. Start <= now < end means ongoing when
end exists. End <= now means past; point start <= now means past. Exact interval
start is ongoing, exact point start/exact end are past. No grace or commute logic.

## Date/timezone ownership and historical correction

Select by scheduled_for_date, not by converting scheduled_start_at. The request
zone determines relation to the server's current local date. Item timezone is its
stored scheduling intent; show authoritative instants in the intended presentation.
Travel/request-zone changes never rewrite assignment. Existing mutation validation
retains DST gap rejection, both fall folds with correct offsets, assigned start
date and end-after-start rules. Never send client-authoritative today.

Past responses show current state. An old assignment completed today is currently
completed; an unfinished old assignment is still incomplete. Completed_at remains
visible. Never label these counts as a historical snapshot or infer completion
at a prior cutoff. Use “Currently completed,” “Still incomplete,” and “Items
currently assigned to this date.”

## Insight and supported destinations

Current selection: ongoing priority → ongoing other → past incomplete priority →
past incomplete other → upcoming priority → upcoming other → unscheduled priority →
unscheduled other → all complete → empty. Schedule/creation then UUID breaks ties.

Past: incomplete scheduled priority → other incomplete scheduled → incomplete
unscheduled priority → other incomplete unscheduled → all currently completed →
empty. Past reasons explicitly describe current incompleteness/assignment.

Future: earliest incomplete scheduled priority → earliest other scheduled →
unscheduled priority → other unscheduled → all already completed → empty. No
prediction of completion/benefit is supplied.

Available insight with a selected item has one plan_item.view and real item ID.
Empty uses selected_date_empty/empty/create; complete uses
selected_date_complete/available/create. Those branches have no item ID. The
frontend owns destinations: create opens the existing item creator with selected
date intent; view opens the existing item detail via its UUID. If insight selects
an item outside the page, use existing GET `/api/v1/plan/items/{plan_item_id}`.
Action identifiers are domain intents, not backend navigation routes.

Modeled insight failure returns unavailable/insight_unavailable, generic text,
null action/item ID and truthful full-date count evidence while items/summary stay
usable. Hide the insight CTA then; do not fabricate a replacement recommendation.
The schema reserves error as an explicit state; this backend fallback emits
unavailable. Persistence failure stays global 503; unexpected failure stays 500.

Today retains its frozen past-start selector and public output; Plan's ongoing
split is explicit. Do not silently reinterpret Today fields using Plan rules.

## Pagination, mutation, loading, refresh and offline

Limit defaults 50/max 100. has_more is authoritative for the current response;
next_cursor null means traversal ends. Treat cursor as opaque and scope local
continuation to the exact selected date/timezone/account session. Incompatible
cursor reuse fails safely; changing limit is supported. Unchanged data traversal
has no duplicates/gaps. Intervening mutations can alter current ordering: no
snapshot guarantee. Avoid merging older responses over newer selected-date state.

After confirmed create/update/complete, discard cursor and refetch selected date
from page one. Date reassignment requires refetching old and new dates; refresh
Today where relevant. Preserve current expected_version/idempotency/completion
contracts from [Today handoff](frontend-today-dashboard-handoff.md). Do not claim
mutation success before an authoritative success response. On conflict retrieve
current item and reconcile using existing behavior.

Loading: calm placeholders. Refresh: keep previously confirmed visible state with
an explicit refresh indicator. Offline: show an honest inability to refresh;
respect no-store and any separately approved client storage policy. Previously
visible data must be identified as older; never change its backend freshness into
a successful current read. Failed refresh should retain an explicit error/retry,
not synthetic empty/zero data. No offline write queue or sync exists.

Generated_at/freshness.as_of are the same server UTC instant. Freshness state is
current only, without stale threshold. Successful/error product responses carry
Cache-Control no-store, Pragma no-cache and X-Request-ID. Retain safe request ID
for support, without logging raw cursor, title/body, auth token or private payload.

## Errors and account isolation

| HTTP | Safe behavior |
| --- | --- |
| 401 | Existing missing/invalid/expired session flow |
| 403 | Existing account_unavailable/email_verification_required/onboarding_required gates |
| 422 | request_validation_failed; correct query or restart pagination; no raw input in details |
| 503 | dependency_unavailable; deliberate retry, never fake empty data |
| 500 | internal_server_error; safe retry/support with request ID |

Authorization derives ownership solely from the principal. Unknown account-ID
query fields fail. Every repository query includes that account and assigned date.
Cursor manipulation cannot grant foreign data. Existing item GET/mutations retain
safe 404 for foreign UUIDs. No account information is accepted from the frontend.

## Safe fixtures and acceptance scenarios

[plan-v1-fixtures.json](examples/plan-v1-fixtures.json) contains complete synthetic
responses, schema-validated in backend tests. Mock keys: empty_current,
current_ongoing_priority_page_one, past_still_incomplete, past_completed_later,
future_scheduled_priority, insight_unavailable. Titles/UUIDs are synthetic; fixtures
are not live user facts or product-state fallback data. The paginated fixture has
full-date summary/insight and an opaque cursor generated by the implementation.

Safe empty example (complete item/count fixture file contains the remaining cases):

```json
{
  "contract_version": "1.0",
  "generated_at": "2026-10-02T14:00:00Z",
  "timezone": "America/New_York",
  "selected_date": "2026-10-02",
  "date_relation": "current",
  "freshness": {"state": "current", "as_of": "2026-10-02T14:00:00Z"},
  "summary": {"total": 0, "completed": 0, "remaining": 0, "scheduled_remaining": 0, "unscheduled_remaining": 0, "completion_percentage": null},
  "insight": {
    "state": "empty", "code": "selected_date_empty", "recommendation": "Create a plan item",
    "reason": "No items are currently assigned to this date.",
    "action": "plan_item.create", "plan_item_id": null,
    "evidence": {"selected_date": "2026-10-02", "date_relation": "current", "total": 0, "completed": 0, "remaining": 0, "is_priority": null, "timing_state": null, "scheduled_start_at": null}
  },
  "items": [], "page": {"limit": 50, "next_cursor": null, "has_more": false}
}
```

Acceptance scenarios: empty null percentage; real zero for incomplete items; 1/8
rounds to 13; scheduled chronology beats priority; tied UUID order is stable;
unscheduled priorities lead; completed items are newest-first; all unchanged-date
pages contain every owned UUID once; summary/recommendation describe the full date;
selected-date/account filtering excludes other facts; current ongoing wins before
past; past copy says still incomplete/currently completed; future copy has no
prediction; exact interval/point/end boundaries match definitions; travel preserves
assignment; DST validation remains; modeled insight outage preserves list/counts;
repository outage is 503; gates and mutation/Today regression behavior remain.

Founder verification: review diff, record the eventual delivery SHA, configure
existing auth/onboarding securely, maintain IANA data, and use migration 0006 in
an authorized test environment. Follow the ten-step [Swagger/manual sequence](plan-v1.md#swagger-and-founder-manual-verification),
including pagination, invalid queries, historical completion, mutations/refetch,
travel/DST, second-account isolation and separated insight/persistence failures.
Guarded PostgreSQL live execution requires local pos_test via POS_TEST_DATABASE_URL;
offline SQL generation does not replace live migration/parity evidence.

Delivered: authoritative date read, deterministic section/order/timing, bounded
cursor response, full-date summary/insight and modeled insight isolation.
Deferred: repeating/recurring items, subtasks, drag/manual ordering, schedule
optimization, providers/calendar sync, measurements in all life domains, offline
queues/sync, snapshots/event sourcing, predictive/generative AI, confidence/benefit/
fabricated personalization, frontend implementation and deployment.

Frontend must not fabricate historical snapshots, insights, measurements, provider
state, or mutation success. No integration preference proves provider connection,
and no configured goal proves a measured value. Only supported actions may appear.
