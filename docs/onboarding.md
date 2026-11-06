# Authoritative onboarding backend contract

## Scope and architecture

Milestone 2.A persists one authoritative onboarding resource per `user_accounts`
row. Delivery follows the repository dependency direction: FastAPI routes and
schemas call `OnboardingService`; the service applies domain policy through
repository and Unit-of-Work ports; SQLAlchemy implementations own PostgreSQL
details. Repositories never commit and routes contain no transaction policy.

The contract version is `1.0`. The API is `/api/v1/onboarding`; it does not alter
the established authentication response shapes. After sign-in, verification,
session restoration, or refresh, clients read onboarding separately.

## Eligibility and routing

Onboarding requires a valid access token for an active account whose email is
verified. Account ownership comes only from verified token claims; no onboarding
request accepts an account identifier. Product routing is:

1. unauthenticated → Authentication;
2. authenticated and unverified → Verification;
3. authenticated, verified, incomplete → Onboarding;
4. authenticated, verified, completed → Today.

`require_completed_onboarding` is an explicit, reusable FastAPI dependency for
future product routes. It reads the authoritative row and returns
`onboarding_required` when incomplete. It is deliberately not global and is not
attached to authentication, account, legal, verification, or onboarding routes.

## State and invariants

Completion requires exactly: one supported priority area; `metric` or
`imperial` explicitly confirmed; and an enabled currency explicitly confirmed.
Enabled currencies are `USD`, `INR`, `CAD`, `GBP`, `EUR`, and `AUD`. Priorities
are `health`, `nutrition`, `fitness`, `focus`, `money`, `habits`, and `sleep`.
Duplicate priorities are returned once in canonical order.

Goals, integration intent, notification preferences, and a first routine are
optional. Goal JSON is accepted only through strict typed API models and is
stored deterministically; decimal and money inputs are serialized as exact
decimal strings. A budget currency must equal the confirmed onboarding currency.
Integration values express only `interested`, `not_interested`, or `deferred`.
Notification values express only `enabled`, `disabled`, or `deferred`; they do
not represent native OS authorization.

No Routine domain/service exists, so `routine_creation_supported` is `false`.
Onboarding never creates a placeholder routine. Provider authorization, native
permissions, push delivery, analytics, and AI recommendations are excluded.

Status is derived, never client-set. A row is `completed` only when its non-null
server completion timestamp and every required completion fact pass strict
reconstruction. Meaningful saved data without valid completion is `in_progress`;
no confirmed or optional meaningful data is `not_started`. Missing rows are
virtual version-0 `not_started` resources. GET is read-only. Screen and carousel
numbers are not accepted or persisted.

`onboarding_states.completed_at` is authoritative only as part of the complete
validated invariant; a missing or corrupt row can never satisfy completion.
`user_accounts.onboarding_required` remains only as a compatibility/materialized
projection for the established authentication schema. Sign-in, restoration,
normal refresh, and refresh retries derive the returned boolean from the
authoritative reconstructed onboarding row in the existing authentication Unit
of Work; a missing row is always incomplete and corrupt state fails safely.
Completion updates the projection atomically, and repeated completion repairs
projection drift before returning. Refresh retries re-read current state instead
of replaying the receipt's historical routing value.

## API and resume behavior

- `GET /api/v1/onboarding` reads the complete resource and configuration.
- `PATCH /api/v1/onboarding` applies a non-empty partial update. Omitted fields
  remain unchanged. It cannot set status, ownership, or completion.
- `POST /api/v1/onboarding/complete` checks the invariant and atomically records
  server time. It issues no token and creates no optional records.

Every response is `no-store`, includes normal request-ID handling, and uses named,
closed OpenAPI schemas. Required nullable fields are always serialized, including
`completed_at` and both preference `value` fields. State is database-backed, so
another valid session for the same account observes the same resource.

## Concurrency and transactions

Virtual state uses version 0. The first write creates the unique account-owned
row at version 1. Every committed mutation compares `expected_version` and
increments once. Identical replay returns current state without another version;
stale differing input returns `onboarding_state_conflict`. Completion locks the
row, compares the version, updates the completion timestamp and account routing
flag in one Unit-of-Work commit, and is idempotent after completion.

PostgreSQL enforces one row per account through the primary/foreign key, cascades
account deletion, and checks version, confirmation/value consistency, and the
database-representable completed-state requirements.
Migration `0005_authoritative_onboarding` is reversible. Existing
accounts are not backfilled; no row means incomplete.

Live PostgreSQL locks, constraints, and migration behavior are claimed only
after the guarded `POS_TEST_DATABASE_URL` suite runs against an authorized test
database; offline SQL generation and database-independent tests are not a
substitute.

Every persisted row is strictly reconstructed before it enters application or
API code. Contract version, enums, confirmations, JSON object shapes, goal
boundaries, exact decimal strings, preference keys/values, canonical priority
ordering, and timezone-aware timestamp ordering are revalidated. Corrupt stored
state is rejected rather than normalized or repaired.
Corrupt state fails through a sanitized persistence error without exposing the
stored payload, SQL, or driver details.

## Errors, privacy, and availability

The centralized envelope maps missing authentication to 401, unverified or
ineligible access to 403, semantic update rejection to 400, incomplete or stale
completion/update to 409, schema validation to 422, persistence failure to 503,
and unexpected failure to 500. SQL and database details are never public.

Request bodies and detailed goal, budget, health, and finance values are not
logged. Existing database diagnostics allow only operation/category/request ID.
Sensitive responses are non-cacheable. Application import remains safe when
database or authentication secrets are absent; the endpoint then fails closed as
`dependency_unavailable` after authentication requirements are evaluated.

## Manual verification

1. Upgrade a guarded disposable PostgreSQL database to Alembic head.
2. Register, verify, and sign in through the existing authentication flow.
3. GET onboarding with the bearer token; confirm version 0 and `not_started`.
4. PATCH priorities, confirmed unit, and confirmed currency with version 0.
5. Retry a differing PATCH with version 0; confirm 409 conflict.
6. POST completion with the returned version; confirm a server timestamp and
   `completed`, with no tokens in the response.
7. Repeat completion and confirm the same completed resource.
8. Restore/refresh the session, GET onboarding, and route to Today only from the
   authoritative completed response.

PostgreSQL tests may run only with the guarded explicit `POS_TEST_DATABASE_URL`
pointing to a local database named `pos_test`.
