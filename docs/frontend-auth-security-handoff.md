# Frontend handoff — email/password security, 07-08

> Historical milestone handoff. The authoritative frozen v1.0.0 contract is
> [the consolidated authentication handoff](frontend-authentication-handoff.md).

This is a backend handoff, not a frontend implementation. Preserve the existing
[registration](frontend-auth-registration-handoff.md),
[session](frontend-auth-session-handoff.md), and
[password recovery](frontend-auth-password-recovery-handoff.md) success schemas.
The canonical [security policy](authentication-security-hardening.md) describes
exact backend limits and residual risk. Google, Apple, biometrics, OAuth/account
linking, MFA/passkeys and authorization are outside this MVP. 07-09 remains the
final contract and release-handoff milestone.

Send JSON bodies and one Bearer credential where required. Credentials must
never appear in API URLs. Send only one Idempotency-Key for refresh, using
1–64 ASCII letters/digits/dot/underscore/hyphen. Reuse that key for retries of
one uncertain rotation; use a new key for the next rotation. Serialize refresh
attempts. A same-key retry is permitted for five minutes; another-key replay
revokes the token family. Do not log the key or treat a refresh failure as proof
that the first attempt did not commit.

## Errors and state mapping

Branch on `code`, never message text. Every backend auth response prevents
caching. Retain only the safe response `X-Request-ID` for support.

| Code / status | Frontend state and action |
|---|---|
| `rate_limited` / 429 | End loading, show “Please wait before trying again”, honor Retry-After, prevent automatic retry loops. Do not describe the account as locked. |
| `resend_cooldown_active` / 429 | Preserve existing server-authoritative retry_at/retry_after_seconds countdown. |
| `verification_attempts_exceeded` / 429 | Stop code confirmation and offer the existing resend/restart flow. |
| `invalid_credentials` / 401 | One identical email/password error for known and unknown identities. |
| `email_not_verified` / 403 | Offer existing email verification after correct credentials. |
| `account_unavailable` / 403 | Generic unavailable state; do not guess the cause. |
| `authentication_required`, `access_token_invalid`, `access_token_expired` / 401 | Pause protected requests; perform at most one serialized refresh when a usable refresh credential exists. Otherwise clear session state and return to Sign In. |
| `refresh_session_invalid`, `refresh_session_expired` / 401 | Clear session credentials and protected identity state; force Sign In. |
| `refresh_session_replayed` / 409 | Clear credentials, force Sign In, explain that the session ended for safety. Never retry the replayed credential. |
| `verification_flow_invalid` / 400, `verification_flow_expired` / 410 | Clear transient flow/code state and restart registration. |
| `verification_code_invalid` / 400 | Clear code and focus code entry with a generic error. |
| `verification_code_expired` / 410 | Offer resend using the existing cooldown. |
| `account_already_exists` / 409 | Only after mailbox proof, offer the returned Sign In/Forgot Password actions. |
| `reset_token_invalid` / 400, `reset_token_expired` / 410 | Clear transient reset material and offer a new request. |
| `password_rejected` / 400 | Explain the existing password policy and focus the password field. |
| `request_validation_failed` / 400 or 422 | Safe form-level error; auth details may be empty. Never echo request values. |
| `request_too_large` / 413, `unsupported_media_type` / 415 | End loading and report a safe client request error; avoid resubmitting unchanged data. |
| `dependency_unavailable` / 503 | Temporary unavailable state, manual retry; do not infer bad credentials or account absence. |
| `internal_server_error` / 500 | Safe retry/support state using request ID only. |

For rate_limited, parse Retry-After as an integer duration in seconds, bounded to
1–3,600. Use a monotonic countdown and re-evaluate after app foregrounding. If
missing/invalid, use a conservative 60-second local wait and manual retry; never
a tight loop. Do not guess which limiter dimension fired. A 429 is not a signal
to erase a valid session. Resume only the user's intended action after the wait.

Forgot Password always uses neutral copy such as “If that address is eligible,
instructions will be sent.” Repeated or internally suppressed requests and
limiter outages receive the same 202 acknowledgement, with no Retry-After. It
does not guarantee delivery. Verification resend retains the replacement-flow
contract: replace the old transient flow value only after successful 202.

## Loading, retry and memory

Disable duplicate submits while a request is pending and expose accessible busy
state. Preserve non-secret form fields on failure. End loading for every success,
HTTP failure, timeout and offline outcome. Network failures are not invalid
credentials: show offline/manual retry and retain a still-valid session. Do not
allow protected requests to proceed with a revoked or expired session simply
because the device is offline.

Clear in-memory passwords after completion, cancellation, leaving the flow or
session teardown. Clear verification codes after submission/rejection and clear
flow/reset tokens when consumed, superseded, expired or cancelled. Reset success
creates no session: return to Sign In and explain that previous sessions ended.
After logout or definitive invalid/replayed refresh, clear protected navigation
history so Back cannot reopen authenticated screens.

Never log or persist passwords, verification codes or reset/flow tokens. Never
put bearer credentials in analytics, crash reports, screenshots, fixtures,
clipboard workflows, AsyncStorage or plaintext files. During frontend integration,
a refresh credential that must survive restart requires the approved platform
secure storage abstraction; keep access credentials in memory where practical.
SecureStore/Keychain/Keystore implementation and biometrics are explicitly
separate work and are not supplied by this backend milestone.

## Accessibility and fixtures

Announce busy, success, failure and the initial cooldown through the existing
accessible status region. Do not announce countdown changes every second. On
rejection, associate visible scalable error text with the relevant field or form;
move focus to the error summary or first actionable field once. After session
expiry navigation, focus the Sign In heading and announce the reason. Keep
resend/retry actions accessible when the timer expires, without unexpected focus
jumps. Never announce credential strings or technical UUIDs as primary content.

Mock fixtures use placeholders only: successful session, generic bad credentials,
unverified account, 429 with Retry-After 60, malformed/missing Retry-After, generic
recovery 202, existing verification cooldown, 503, offline, expired access with
one refresh success, refresh replay, reset success and forced Sign In. Use a
controlled clock to test cooldown/background transitions. Do not copy real
issued tokens or reset links into fixtures.

Acceptance requires unchanged success schemas, stable error mapping, generic
privacy-safe messages, serialized same-key refresh retries, bounded cooldowns,
no duplicate submissions, no credential leakage or insecure persistence,
accessible focus/announcements, complete offline handling and forced Sign In
on definitive session invalidation. Founder verification requires the guarded
backend dependencies and disposable accounts documented in the security policy;
mobile implementation/verification is a later task.
