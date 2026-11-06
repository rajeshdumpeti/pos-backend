# Backend to frontend authentication handoff

This is the authoritative frontend contract for **Authentication Contract
v1.1.0**. Earlier milestone handoffs are historical evidence; when they differ,
this document and generated OpenAPI govern. The backend baseline must be pinned
by the integration change to the reviewed commit containing Milestone 07-09.
Until that commit exists, use branch `feat/backend-auth-contract-freeze` and do
not invent a hash.

The MVP supports email/password only. Google, Apple, account linking,
biometrics, MFA, and passkeys are deferred. The API base path is
`/api/v1/auth`; deployment supplies the HTTPS origin. Backend responses include
`X-Request-ID`, and all authentication responses use `Cache-Control: no-store`
and `Pragma: no-cache`.

## Endpoint inventory

| Operation ID | Method and path | Request | Success |
|---|---|---|---|
| `get_authentication_registration_policy` | `GET /registration-policy` | none | `200 RegistrationPolicyResponse` |
| `create_email_registration` | `POST /registrations` | `RegistrationRequest` | `202 PendingVerificationResponse` |
| `confirm_email_verification` | `POST /email-verifications/confirm` | `ConfirmEmailRequest` | `201 EmailVerifiedResponse` |
| `resend_email_verification` | `POST /email-verifications/resend` | `ResendVerificationRequest` | `202 PendingVerificationResponse` |
| `change_unverified_email` | `PATCH /email-verifications/email` | `ChangeUnverifiedEmailRequest` | `202 PendingVerificationResponse` |
| `create_email_session` | `POST /sessions` | `SignInRequest` | `200 SessionTokenResponse` |
| `refresh_email_session` | `POST /sessions/refresh` | `RefreshSessionRequest` plus `Idempotency-Key` | `200 SessionTokenResponse` |
| `get_current_session` | `GET /session` | Bearer access token | `200 CurrentSessionResponse` |
| `delete_current_session` | `DELETE /session` | Bearer access token | `204`, empty, no content type |
| `delete_all_sessions` | `DELETE /sessions` | Bearer access token | `204`, empty, no content type |
| `request_password_reset` | `POST /password-resets` | `PasswordResetRequest` | `202 PasswordResetAcceptedResponse` |
| `confirm_password_reset` | `POST /password-resets/confirm` | `ConfirmPasswordResetRequest` | `204`, empty, no content type |

## Named contracts

```ts
type RegistrationPolicyResponse = {
  legal_consent_required: boolean;
  legal_policy_status: "active" | "deferred_nonproduction";
  terms_version: string | null;
  terms_url: string | null;
  privacy_version: string | null;
  privacy_url: string | null;
};

type RegistrationRequest = {
  display_name: string;
  email: string;
  password: string;
  terms_accepted?: boolean;
  privacy_accepted?: boolean;
  terms_version?: string;
  privacy_version?: string;
};

type PendingVerificationResponse = {
  status: "registration_pending";
  flow_token: string;
  masked_email: string;
  verification_expires_at: string;
  flow_expires_at: string;
  resend_available_at: string;
};

type ConfirmEmailRequest = { flow_token: string; code: string };
type ResendVerificationRequest = { flow_token: string };
type ChangeUnverifiedEmailRequest = {
  flow_token: string;
  new_email: string;
};

type EmailVerifiedResponse = {
  status: "email_verified";
  account_id: string;
  onboarding_required: boolean;
  session: null;
  next_action: "await_session_establishment";
};

type SignInRequest = { email: string; password: string };

type VerificationFlowContinuation = {
  flow_token: string;
  masked_email: string;
  expires_at: string;
  resend_available_at: string;
};

type EmailNotVerifiedResponse = {
  code: "email_not_verified";
  message: "Email verification is required";
  request_id: string;
  details: [];
  actions: ["verify_email"];
  verification_flow: VerificationFlowContinuation;
};

type SessionTokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: "Bearer";
  access_expires_at: string;
  refresh_expires_at: string;
  account_id: string;
  session_id: string;
  onboarding_required: boolean;
};

type RefreshSessionRequest = { refresh_token: string };
type CurrentSessionResponse = {
  account_id: string;
  session_id: string;
  display_name: string;
  onboarding_required: boolean;
};
type PasswordResetRequest = { email: string };
type PasswordResetAcceptedResponse = {
  status: "instructions_if_eligible";
};
type ConfirmPasswordResetRequest = {
  token: string;
  new_password: string;
};
```

JSON examples use only placeholders:

```json
{"email":"person@example.test","password":"<user-entered-password>"}
```

```json
{"refresh_token":"<opaque-refresh-token>"}
```

## Registration and verification

Fetch `GET /registration-policy` before registration and again after
`legal_acceptance_outdated`. When required, show both backend-provided document
URLs, require explicit acceptance, and submit their identifiers unchanged. When
deferred, omit the consent UI and all four legal request fields. Never invent
acceptance, versions, URLs, or whether consent applies. Deferred policy is
backend-controlled and forbidden in staging and production.
Do not permanently hardcode version identifiers in the app.

Registration returns a transient flow token and masked address. Keep the token
in memory, then use it for confirmation, resend, or change-email. A successful
resend/change returns a replacement; discard the superseded token immediately.
Backend timestamps govern code expiry, flow expiry, and resend availability.

Correct credentials for a pending unverified account return the named
`EmailNotVerifiedResponse`. The backend rotates the current flow credential
without duplicating its active verification email. If no usable flow exists, it
creates one challenge and encrypted email atomically. After this response:

1. Clear the password.
2. Keep `verification_flow.flow_token` only in transient memory.
3. Navigate to Verify Email and use the returned timing/address fields.
4. Use that token for confirmation, resend, or permitted change-email.
5. Return to Sign In if transient flow state is lost.

Invalid email and password share `invalid_credentials`; they never return or
create a flow. Concurrent correct sign-ins may rotate the transient credential;
the last committed response is current, one challenge remains current, and no
duplicate email is sent during the existing cooldown. A client that receives a
superseded flow restarts at Sign In. Verification never creates a session.

Remember Me is absent from v1.0.0. Do not render it or submit it. Extra fields,
including `remember_me`, are rejected. Refresh sessions have a seven-day absolute
lifetime; access tokens last ten minutes. `onboarding_required` is the only
backend routing signal. Backend does not choose a mobile screen; Product and
Frontend own the exact onboarding or authenticated-home destination.

## Refresh, restoration, and forced sign-out

Use one single-flight refresh coordinator for every frontend caller. Generate an
unpredictable key (a cryptographically random UUID is suitable) per logical
refresh. An uncertain network retry reuses the same key and same old refresh
token. After success, atomically replace the stored refresh token and never use
the old one. The next independent refresh uses a new key.

| Refresh case | Frozen server behavior | Frontend action |
|---|---|---|
| Same old token and same key within five minutes after commit | Exact encrypted prior response, `200`; no second rotation | Accept once and store returned replacement |
| Same key with different refresh material | `409 refresh_idempotency_conflict`; no mutation | Sign out; investigate client coordinator bug |
| Same old token with different key | `409 refresh_session_replayed`; family revoked | Clear everything and force Sign In |
| Concurrent same token/same key | At most one rotation; peer receives exact retry result | Coalesce frontend callers |
| Concurrent same token/different keys | One rotation; competing use is replay and contains family | Clear everything and force Sign In |
| Ambiguous transport failure | Commit may have happened | Retry once with the same token/key; never invent a new key |
| Persistence/encryption failure before commit | Full rollback; safe `503` or `500` | Keep old credential, manual/network retry with same key |
| Confirmed invalid, expired, revoked, replayed, or conflict | Session cannot continue safely | Complete local sign-out |

Idempotency keys and tokens are sensitive diagnostics. Never log or persist the
key outside the in-flight refresh record. Do not parse JWT claims for product
state. Restore by loading the refresh credential from approved secure storage,
refreshing when needed, then calling `GET /session`. Backend validation of the
JWT, account, session, verification, revocation, and authentication epoch is
authoritative.

Platform secure storage is required during frontend integration: Keychain-backed
storage on iOS and Keystore-backed storage on Android through one abstraction.
Keep access tokens in memory where practical. Never use AsyncStorage, plaintext
files, analytics, crash payloads, URLs, screenshots, clipboard workflows, or
support tickets for passwords, codes, flow/reset tokens, Bearer tokens, or
idempotency keys. Secure-storage implementation is frontend work, not part of
this backend milestone.

## Logout and local cleanup

Current-session logout is `DELETE /session`; all-session logout is
`DELETE /sessions`. Both require the current access token and return an empty
`204` on successful revocation. Do not refresh solely to perform logout. The
server may return `401` for invalid/expired credentials or `503` when revocation
cannot be confirmed; it does not turn a failed remote security mutation into a
false success.

Frontend signed-out state is idempotent: immediately clear access/refresh tokens,
account-specific caches, authentication state, pending protected requests, and
protected navigation history whether remote logout succeeds, fails, or the app
is offline. Offline logout means remote revocation is unconfirmed. Repeated local
logout must never restore or duplicate a session. Password reset and forced
session invalidation use the same complete cleanup.

## Password recovery and deep links

Forgot Password always shows neutral text: “If that address is eligible,
instructions will be sent.” A `202` does not prove account existence or delivery.
The reset destination contract is exactly:

```text
<configured-reset-base-url>?token=<opaque-reset-token>
```

Existing query fields are preserved and `token` is appended exactly once. The
configured base must not contain `token`, including encoded or duplicate forms.
Local/development may use HTTP(S), `pos://`, or `pos-dev://`; protected
environments require the configured HTTPS universal/app-link destination.
Founder-owned domain and Apple/Android association files remain staging/deployment
prerequisites.

Extract the single `token` query value once and pass it exactly as received to
`POST /password-resets/confirm`. Do not trim, case-fold, normalize Unicode, or
decode it repeatedly. Backend confirmation is authoritative. Reset success
returns empty `204`, creates no session, revokes every session, and requires
complete local cleanup followed by Sign In.

## Error and action matrix

Authentication validation `details` are always empty. The action vocabulary is
`verify_email`, `refresh_registration_policy`, `sign_in`, and
`forgot_password`. Timing fields occur only for resend cooldown
(`retry_at`, `retry_after_seconds`) and generic schemas that explicitly declare
them. Every public `429` has integer `Retry-After` from 1 through 3,600 seconds.

| Operation, method, and path | HTTP | Code and exact message | Action/timing | Class | Frontend transition |
|---|---:|---|---|---|---|
| registration, `POST /registrations` | 400 | `legal_consent_required` — “Legal consent is required” | none | correctable | Display both current documents and require explicit acceptance |
| registration, `POST /registrations` | 400 | `legal_consent_not_applicable` — “Legal consent is not applicable” | none | policy refresh | Refetch policy and omit all legal fields when deferred |
| registration, `POST /registrations` | 400 | `password_rejected` — “Password was rejected” | none | correctable | Focus password; preserve nonsecret fields |
| registration, `POST /registrations` | 409 | `legal_acceptance_outdated` — “Registration policy has changed” | `refresh_registration_policy` | correctable | Refetch policy and require acceptance of current copy |
| verification confirm, `POST /email-verifications/confirm` | 400 | `verification_flow_invalid` — “Verification flow is invalid” | none | flow restart | Clear flow and return to Sign In/registration |
| verification confirm, `POST /email-verifications/confirm` | 400 | `verification_code_invalid` — “Verification code is invalid” | none | correctable | Clear code and focus code entry |
| verification confirm, `POST /email-verifications/confirm` | 409 | `account_already_exists` — “Account ownership was confirmed” | `sign_in`, `forgot_password` | terminal flow | Clear flow and present only returned actions |
| verification confirm, `POST /email-verifications/confirm` | 410 | `verification_flow_expired` — “Verification flow expired” | none | flow restart | Clear flow and restart |
| verification confirm, `POST /email-verifications/confirm` | 410 | `verification_code_expired` — “Verification code expired” | none | correctable | Offer resend |
| verification confirm, `POST /email-verifications/confirm` | 429 | `verification_attempts_exceeded` — “Verification attempts were exceeded” | bounded `Retry-After` | terminal flow | Stop confirming; resend/restart |
| verification resend, `POST /email-verifications/resend` | 400/410 | flow invalid/expired, messages above | none | flow restart | Clear flow and restart |
| verification resend, `POST /email-verifications/resend` | 429 | `resend_cooldown_active` — “Verification resend cooldown is active” | header plus `retry_at`, `retry_after_seconds` | retryable | Use authoritative countdown, then manual resend |
| change email, `PATCH /email-verifications/email` | 400/410 | flow invalid/expired, messages above | none | flow restart | Clear flow and restart |
| sign-in, `POST /sessions` | 401 | `invalid_credentials` — “Email or password is invalid” | none | correctable/privacy-safe | One generic error; never infer account existence |
| sign-in, `POST /sessions` | 403 | `email_not_verified` — “Email verification is required” | `verify_email`, typed continuation | correctable | Clear password and navigate to Verify Email |
| sign-in, `POST /sessions` | 403 | `account_unavailable` — “Account is unavailable” | none | terminal | Clear password; generic unavailable state |
| refresh, `POST /sessions/refresh` | 401 | `refresh_session_invalid` — “Refresh session is invalid” | none | terminal | Complete local sign-out |
| refresh, `POST /sessions/refresh` | 401 | `refresh_session_expired` — “Refresh session expired” | none | terminal | Complete local sign-out |
| refresh, `POST /sessions/refresh` | 409 | `refresh_session_replayed` — “Refresh session replay was detected” | none | terminal | Complete local sign-out and safety explanation |
| refresh, `POST /sessions/refresh` | 409 | `refresh_idempotency_conflict` — “Refresh idempotency key conflicts with another request” | none | terminal/client defect | Complete local sign-out |
| restore/logout, `GET /session`, `DELETE /session`, `DELETE /sessions` | 401 | `authentication_required` — “Authentication is required” | none | terminal | Complete local sign-out |
| restore/logout, `GET /session`, `DELETE /session`, `DELETE /sessions` | 401 | `access_token_invalid` — “Access token is invalid” | none | refreshable for restore | One single-flight refresh or sign out |
| restore/logout, `GET /session`, `DELETE /session`, `DELETE /sessions` | 401 | `access_token_expired` — “Access token expired” | none | refreshable for restore | One single-flight refresh; do not refresh solely for logout |
| reset confirm, `POST /password-resets/confirm` | 400 | `reset_token_invalid` — “Reset token is invalid” | none | flow restart | Clear token and request another link |
| reset confirm, `POST /password-resets/confirm` | 410 | `reset_token_expired` — “Reset token expired” | none | flow restart | Clear token and request another link |
| reset confirm, `POST /password-resets/confirm` | 400 | `password_rejected` — “Password was rejected” | none | correctable | Focus new password |
| every listed endpoint with malformed Authorization, see endpoint inventory | 401 | `authentication_required` — “Authentication is required” | none | terminal request | Remove malformed credentials; protected flows sign out |
| every listed endpoint, see endpoint inventory | 400/422 | `request_validation_failed` — “Request validation failed” | empty details | correctable | Fix client/form input without echoing values |
| every listed endpoint, see endpoint inventory | 413 | `request_too_large` — “Request body is too large” | none | terminal request | Fix client payload; do not resubmit unchanged |
| every listed endpoint, see endpoint inventory | 415 | `unsupported_media_type` — “JSON content is required” | none | terminal request | Send one uncompressed application/json body |
| every listed endpoint except `POST /password-resets` | 429 | `rate_limited` — “Please try again later” | bounded `Retry-After` only | retryable | End loading; monotonic/manual retry countdown |
| forgot password, `POST /password-resets` | 202 | `instructions_if_eligible` response, no error code | no Retry-After | privacy-safe generic | Show the same neutral acknowledgement |
| every listed endpoint, see endpoint inventory | 503 | `dependency_unavailable` — “A required dependency is unavailable” | none | retryable | Preserve safe state and allow manual retry |
| registration policy, `GET /registration-policy` | 503 | `authentication_configuration_unavailable` — “Authentication configuration is unavailable” | none | retryable/configuration | Do not use fallback policy; allow explicit retry |
| every listed endpoint, see endpoint inventory | 500 | `internal_server_error` — “An unexpected error occurred” | none | retryable/support | Generic state with request ID only |

Malformed extra Authorization headers can produce `authentication_required` on
otherwise public operations. Query strings are rejected on every authentication
endpoint. OpenAPI is the authoritative per-operation status inventory; contract
tests compare it with the endpoint list and required named schemas.

## UI state, accessibility, and offline behavior

Disable the initiating control and conflicting fields while each request is in
flight. Coalesce refresh calls and block duplicate submissions. End loading on
every success, HTTP failure, timeout, cancellation, and offline failure. Preserve
nonsecret fields after retryable failures; clear passwords on successful
submission, navigation away, unverified continuation, and sign-out. Clear codes
after rejection, and clear flow/reset tokens when used, superseded, expired, or
cancelled.

Treat network failure as offline/ambiguous state, never bad credentials. Do not
discard a potentially valid session solely due to a network outage, except when
the user explicitly logs out locally. Protected screens must not continue with
a confirmed invalid session.

Expose busy state and announce the first status/error through the existing live
region. Associate errors with fields or a form summary and move focus once to the
first actionable target. Do not announce cooldown changes every second. After
forced sign-out, focus the Sign In heading and announce the safe reason. Never
announce credentials, raw request IDs, or UUIDs as primary content. Preserve
large text, keyboard-safe layout, minimum touch targets, contrast, and platform
screen-reader expectations on both iOS and Android. Authentication behavior is
otherwise platform-equivalent; only secure storage and universal/app-link setup
are platform-specific.

## Fixtures, manual verification, and acceptance

Use placeholders and controlled clocks for: policy/current-policy conflict,
pending registration, invalid/expired/exhausted verification, resend cooldown,
unverified sign-in continuation, valid session, onboarding true/false, access
expiry with one refresh, exact refresh retry, idempotency conflict, replay,
generic reset acknowledgement, reset invalid/expired/success, rate limit,
dependency unavailable, internal error, offline, and ambiguous refresh/logout.
Never copy issued credentials or token-bearing links into fixtures.

Manual verification requires the reviewed backend commit, disposable accounts,
safe HTTP-client history/logging, the guarded PostgreSQL/Redis configurations in
[security hardening](authentication-security-hardening.md), and later a verified
sender plus approved HTTPS universal/app-link destination. Verify policy fetch,
registration, continuation through confirm/resend/change, seven-day session,
single-flight refresh cases, both logout endpoints, reset cleanup, cache headers,
request IDs, and secret-free diagnostics. Do not paste credentials or complete
reset links into evidence.

Acceptance requires every named schema and stable code above, v1.0.0 in OpenAPI,
generic invalid credentials/reset acknowledgement, transient continuation,
single-flight refresh, complete forced-sign-out cleanup, no Remember Me, secure
storage, accessible transitions, deterministic fixtures, and no secret logging.
Backend owns validation, tokens, challenge/session state, throttling, transactions,
outbox, stable codes, and timestamps. Frontend owns form UX, navigation, local
state/cleanup, single-flight coordination, secure-storage integration, deep-link
extraction, accessibility, and offline presentation. Product owns legal content
and the precise onboarding/home route.
