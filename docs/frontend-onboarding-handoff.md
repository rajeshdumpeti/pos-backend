# Frontend onboarding handoff — contract 1.0

## Transport, eligibility, and routing

Base URL is the backend origin. All three endpoints require
`Authorization: Bearer <access-token>` and a valid active, email-verified
account:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/onboarding` | Read authoritative state and configuration |
| `PATCH` | `/api/v1/onboarding` | Save a non-empty partial update |
| `POST` | `/api/v1/onboarding/complete` | Explicitly complete after requirements pass |

Retrieve onboarding after sign-in, verification/session establishment, session
restoration, and refresh. Route unauthenticated users to Authentication,
unverified users to Verification, verified incomplete users to Onboarding, and
verified completed users to Today. The approved transition is Onboarding → Today
only after the completion response says `completed`.

Screen 1 is pre-auth presentation. Screen 4 priority selection cannot be
skipped. Screens 2, 3, and 9 are presentation only and are not backend
milestones. Never use carousel or screen numbers as authoritative progress.

## TypeScript-style contract

```ts
type Status = 'not_started' | 'in_progress' | 'completed';
type Priority = 'health' | 'nutrition' | 'fitness' | 'focus' | 'money' | 'habits' | 'sleep';
type UnitSystem = 'metric' | 'imperial';
type Currency = 'USD' | 'INR' | 'CAD' | 'GBP' | 'EUR' | 'AUD';
type IntegrationId = 'apple_health' | 'health_connect' | 'apple_watch' | 'calendar' | 'reminders' | 'wearables';
type IntegrationIntent = 'interested' | 'not_interested' | 'deferred';
type NotificationId = 'workout' | 'protein' | 'water' | 'finance' | 'deep_work' | 'weekly_reflection';
type NotificationValue = 'enabled' | 'disabled' | 'deferred';

interface Goals {
  target_weight?: { value: string; unit: 'kg' | 'lb' } | null;
  daily_protein_grams?: string | null;
  daily_water?: { value: string; unit: 'liters' | 'fluid_ounces' } | null;
  daily_calories_kcal?: number | null;
  daily_steps?: number | null;
  workouts_per_week?: number | null;          // 0..7
  focus_minutes_per_day?: number | null;      // 0..1440
  target_sleep_minutes?: number | null;       // 1..1440
  budget_target?: { amount: string; currency: Currency } | null;
}

interface PatchOnboarding {
  expected_version: number;
  priority_life_areas?: Priority[];
  unit_system?: UnitSystem;
  unit_system_confirmed?: boolean;
  currency?: Currency;
  currency_confirmed?: boolean;
  goals?: Goals;
  integration_preferences?: Partial<Record<IntegrationId, IntegrationIntent>>;
  notification_preferences?: Partial<Record<NotificationId, NotificationValue>>;
}

interface CompleteOnboarding { expected_version: number }

interface OnboardingResource {
  status: Status;
  contract_version: '1.0';
  version: number;
  priority_life_areas: Priority[];
  unit_preference: { value: UnitSystem | null; confirmed: boolean };
  currency_preference: { value: Currency | null; confirmed: boolean };
  goals: Goals;
  integration_preferences: Partial<Record<IntegrationId, IntegrationIntent | null>>;
  notification_preferences: Partial<Record<NotificationId, NotificationValue | null>>;
  requirements: {
    priority_life_areas_satisfied: boolean;
    unit_system_satisfied: boolean;
    currency_satisfied: boolean;
    ready_to_complete: boolean;
  };
  recommended_next_action: 'select_priority_life_areas' | 'confirm_unit_system' |
    'confirm_currency' | 'complete_onboarding' | 'onboarding_completed';
  completed_at: string | null;
  configuration: {
    priority_life_areas: Priority[];
    unit_systems: UnitSystem[];
    currencies: Currency[];
    optional_goal_fields: (keyof Goals)[];
    integration_identifiers: IntegrationId[];
    integration_preference_values: IntegrationIntent[];
    notification_identifiers: NotificationId[];
    notification_preference_values: NotificationValue[];
    routine_creation_supported: false;
  };
}
```

Decimal JSON inputs may be number or decimal string; treat returned stored
decimal values as strings and never round money through binary floating point.
Budget currency must match the selected, confirmed currency. Unknown keys and
enum values are rejected. Numeric values must be finite. Positive measurements,
protein, water, calories, steps, and sleep must be greater than zero; budget may
be zero; workouts and focus use the bounds shown above. These are technical, not
medical or financial-advice limits.

## Required, optional, and status behavior

The only completion requirements are at least one priority, explicit unit
confirmation, and explicit currency confirmation. A locale suggestion is not a
confirmation. Goals, routine, integrations, and notifications can all be
skipped. Priority duplicates are removed in canonical order.

Missing state is a virtual read-only `not_started` resource at version 0. Saved
meaningful data is `in_progress`; only successful explicit completion with a
server timestamp is `completed`. The frontend must not persist or fabricate
authoritative completion. It may keep safe unsaved same-device drafts, but the
backend remains authoritative across sessions and devices.

`completed_at` is authoritative only when the strictly reconstructed row also
satisfies every required completion fact. The authentication response field
`onboarding_required` is retained for compatibility but is derived from the
current reconstructed row, including refresh retries; missing state always
returns `true` and corrupt state fails safely. Continue to GET onboarding after
every authentication lifecycle resolution rather than persisting the
compatibility field.

Routine creation is deferred because no backend Routine service exists. Do not
create a placeholder. Integration preferences are intent only and never mean a
provider is connected. Notification preferences are backend preferences only
and never mean iOS/Android permission was requested or granted.

## Concurrency, offline, loading, and retry

Send the last received `version` as `expected_version`. A save increments once.
An identical replay may return current state without incrementing. On
`onboarding_state_conflict`, fetch GET, preserve the user's unsaved intent, show
that newer state exists, and require a deliberate user decision before another
save. Never silently overwrite another device.

Show a stable loading state while GET is unresolved; do not route to Today from
cached UI progress. Offline, safe drafts may remain local and clearly unsaved.
The frontend must not automatically repeat failed mutations after reconnection;
retry must be deliberate. After a network/dependency failure, GET authoritative
state before deciding whether a prior mutation succeeded.

## Errors and accessible announcements

Errors use `{ code, message, request_id, details }` and the response header
`X-Request-ID`. Map codes as follows:

| HTTP/code | UI behavior and announcement |
| --- | --- |
| 401 `authentication_required` / token errors | Resolve auth; “Your session needs attention.” |
| 403 `email_verification_required` | Route Verification; “Verify your email to continue.” |
| 403 `onboarding_required` | Route Onboarding for explicitly gated product routes |
| 400 `onboarding_update_rejected` | Keep inputs; announce “We couldn’t save those choices.” |
| 409 `onboarding_incomplete` | Focus first unmet requirement |
| 409 `onboarding_state_conflict` | Announce “Your setup changed on another session. Review the latest choices.” |
| 422 `request_validation_failed` | Show safe field errors; do not echo raw payloads |
| 503 `dependency_unavailable` | Announce “Setup is temporarily unavailable. Try again when ready.” |
| offline | Announce “You’re offline. Changes have not been saved.” |

On successful save announce “Setup choices saved.” On completion announce
“Setup complete. Opening Today.” Announcements must not rely on color alone.

## Sanitized examples and fixtures

PATCH ready-to-complete:

```json
{"expected_version":0,"priority_life_areas":["health","focus"],"unit_system":"metric","unit_system_confirmed":true,"currency":"USD","currency_confirmed":true}
```

Compact mock fixtures (configuration omitted where unchanged):

```ts
const notStarted = { status: 'not_started', version: 0, priority_life_areas: [],
  unit_preference: {value:null,confirmed:false}, currency_preference:{value:null,confirmed:false},
  goals:{}, integration_preferences:{}, notification_preferences:{}, completed_at:null,
  requirements:{priority_life_areas_satisfied:false,unit_system_satisfied:false,currency_satisfied:false,ready_to_complete:false},
  recommended_next_action:'select_priority_life_areas' };
const inProgress = { ...notStarted, status:'in_progress', version:1,
  priority_life_areas:['health'], requirements:{...notStarted.requirements,priority_life_areas_satisfied:true},
  recommended_next_action:'confirm_unit_system' };
const ready = { ...inProgress, version:3, unit_preference:{value:'metric',confirmed:true},
  currency_preference:{value:'USD',confirmed:true},
  requirements:{priority_life_areas_satisfied:true,unit_system_satisfied:true,currency_satisfied:true,ready_to_complete:true},
  recommended_next_action:'complete_onboarding' };
const completed = { ...ready, status:'completed', version:4,
  completed_at:'2026-09-23T12:00:00Z', recommended_next_action:'onboarding_completed' };
const validationFailure = { status:422, body:{code:'request_validation_failed',message:'Request validation failed',request_id:'fixture-request',details:[]} };
const conflict = { status:409, body:{code:'onboarding_state_conflict',message:'Onboarding state conflicts with a newer update',request_id:'fixture-request',details:[]} };
const offline = { kind:'offline', saved:false };
const dependencyFailure = { status:503, body:{code:'dependency_unavailable',message:'A required dependency is unavailable',request_id:'fixture-request',details:[]} };
```

All actual responses also include deterministic configuration and contract
version `1.0`.

Generated OpenAPI uses the same named, closed goal, measured-value, budget,
integration-preference, notification-preference, requirements, and configuration
types shown above. Persisted data is revalidated on every read; corrupt backend
state returns only a safe dependency error and must never be rendered as setup
state.

## Acceptance and manual/Postman sequence

Acceptance requires that state resumes after a new request/session, another
session sees the same state, stale writes do not overwrite, optional omissions
do not block readiness, completion returns no authentication token, and only an
authoritative completed response permits Today routing.

In Postman: obtain a verified user's access token through normal auth; GET and
retain version; PATCH one or more fields with that version; GET again; submit a
stale differing PATCH and confirm 409; finish required fields; POST `/complete`;
repeat POST to confirm idempotency; restore or refresh auth and GET again. Also
verify no-store headers and `X-Request-ID`. A local guarded PostgreSQL instance
at the migration head is a prerequisite for persistence testing.

Assumptions/deferred work: there is no Routine service, provider authorization,
native notification permission boundary, push delivery, or AI recommendation
system in this milestone. Frontend screens and navigation are outside this
backend change.
