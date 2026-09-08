# Frontend handoff: email registration and verification

> Historical milestone handoff. The authoritative frozen v1.0.0 contract is
> [the consolidated authentication handoff](frontend-authentication-handoff.md).

This is the Milestone 07-03 contract for the POS mobile Authentication UI. The
backend supplies state and authoritative timing; the frontend owns token-driven
visual design, focus, keyboard behavior, accessibility, and offline presentation.
Session establishment and final authenticated routing arrive in 07-04.

## Shared contract

Base path: `/api/v1/auth`. Send `Content-Type: application/json` and optionally
`X-Request-ID`; the backend returns `X-Request-ID`. Never log request bodies,
passwords, codes, or flow tokens. Store the flow token only in transient flow
state pending the approved 07-04 storage design.

```ts
type PendingVerification = {
  status: "registration_pending";
  flow_token: string;
  masked_email: string;
  verification_expires_at: string;
  flow_expires_at: string;
  resend_available_at: string;
};

type ApiError = {
  code: string;
  message: string;
  request_id: string;
  details: Array<{ field: string; code: string; message: string }>;
  retry_at?: string;
  retry_after_seconds?: number;
  actions?: Array<"sign_in" | "forgot_password">;
};
```

All timestamps are ISO 8601. Render countdowns from backend timestamps and
reconcile on app resume or another response; the device timer is presentation,
not authority.

## Create account

`POST /registrations` creates or privacy-safely continues a registration.

```ts
type RegistrationRequest = {
  display_name: string;
  email: string;
  password: string;
  terms_accepted: boolean;
  privacy_accepted: boolean;
  terms_version: string;
  privacy_version: string;
};
```

Sanitized example: name `POS Tester`, email `tester@example.com`, a locally
generated password of 15+ characters, and the current legal versions. Password
confirmation is frontend-only equality validation and is not sent.

Success is HTTP 202 with `PendingVerification`. New, pending, and existing
accounts intentionally have the same initial status and schema. Navigate to
Email Verification using `masked_email`; retain `flow_token` without displaying
it.

## Confirm email

`POST /email-verifications/confirm`

```ts
type ConfirmRequest = { flow_token: string; code: string };
type EmailVerified = {
  status: "email_verified";
  account_id: string;
  onboarding_required: boolean;
  session: null;
  next_action: "await_session_establishment";
};
```

Success is HTTP 201. Announce “Email verified.” Because `session` is null, do
not enter authenticated POS routes or persist an authenticated state. Hold the
success screen/final routing until 07-04.

## Resend

`POST /email-verifications/resend` accepts `{ flow_token }` and returns HTTP
202 with a replacement `PendingVerification`. Replace the old transient flow
token immediately. Disable resend until `resend_available_at`; while the request
is active, keep the button disabled and announce progress and completion.

## Change unverified email

`PATCH /email-verifications/email` accepts:

```ts
type ChangeEmailRequest = { flow_token: string; new_email: string };
```

HTTP 202 returns a replacement `PendingVerification`. Replace the old token,
update the displayed masked destination, clear code input, and return focus to
the first verification-code cell. The old flow is unusable.

## Error and UX mapping

| Code | HTTP | UX behavior |
|---|---:|---|
| `request_validation_failed` | 422 | Map safe field details inline; never echo sensitive input. |
| `password_rejected` | 400 | Announce password requirements/rejection and focus password. |
| `verification_flow_invalid` | 400 | Clear unusable flow and offer restart/back. |
| `verification_flow_expired` | 410 | Show flow-expired state and restart registration. |
| `verification_code_invalid` | 400 | Inline invalid-code state; clear code and refocus. |
| `verification_code_expired` | 410 | Show Code Expired and offer resend. |
| `verification_attempts_exceeded` | 429 | Show Too Many Attempts; do not retry confirmation. |
| `resend_cooldown_active` | 429 | Continue approved countdown from `retry_at`/`retry_after_seconds`. |
| `account_already_exists` | 409 | Offer only actions returned: Sign In or Forgot Password. |
| `dependency_unavailable` | 503 | Retry-safe unavailable/offline presentation; retain form locally. |
| `internal_server_error` | 500 | Generic retry/support message with request ID. |

A transport/network failure maps to the approved Offline state and is not an
authentication error. Do not discard user-entered non-secret fields. Passwords
and codes should not be persisted.

## Loading and accessibility

- Disable the initiating button and fields/actions that can alter the current
  transaction while each request is pending; prevent duplicate submits.
- Expose busy state and announce success/errors through the existing accessible
  status region.
- Verification inputs must accept and announce leading zeroes as six digits.
- Do not read the flow token, account UUID, or raw request ID as primary user
  content; the request ID may be available in support details.
- Keep error text visible, programmatically associated with its field, scalable,
  and usable in light/dark themes.
- Preserve keyboard-safe layout and 44-point actions at large text sizes.

## Mock fixtures and acceptance

Use fixed fake timestamps and placeholder strings in UI tests, never real codes
or tokens. Required fixtures: pending, invalid code, expired code/flow, exhausted
attempts, cooldown with retry metadata, existing account actions, offline, 503,
and verified-with-null-session.

Acceptance requires all four requests, status/error mapping, authoritative
countdown behavior, replacement of superseded flow tokens, no sensitive
logging/persistence, accessible announcements, and no authenticated navigation.
Swagger at `/docs` is the source for schemas. Local curl verification should use
developer-generated values and the deliberate encrypted-outbox inspection CLI;
never commit or paste those values.
