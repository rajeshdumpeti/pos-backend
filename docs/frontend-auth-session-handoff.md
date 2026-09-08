# Frontend handoff: email sign-in and sessions

> Historical milestone handoff. The authoritative frozen v1.0.0 contract is
> [the consolidated authentication handoff](frontend-authentication-handoff.md).

This draft contract is for POS mobile coordination. Backend implementation is
present; frontend integration is not part of Milestone 07-04.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/sessions` | Email/password sign-in |
| `POST` | `/api/v1/auth/sessions/refresh` | Rotate refresh credentials |
| `GET` | `/api/v1/auth/session` | Read current identity/session |
| `DELETE` | `/api/v1/auth/session` | Log out current device |
| `DELETE` | `/api/v1/auth/sessions` | Log out all devices |

Sign-in accepts `email`, `password`, and `remember_me`. Successful sign-in and
refresh return `access_token`, `refresh_token`, `token_type`,
`access_expires_at`, the absolute `refresh_expires_at`, `account_id`,
`session_id`, and `onboarding_required`. Token responses are non-cacheable.

```json
{
  "email": "person@example.com",
  "password": "<user-entered-password>",
  "remember_me": false
}
```

```json
{
  "access_token": "<opaque-to-the-mobile-client>",
  "refresh_token": "<opaque-refresh-token>",
  "token_type": "Bearer",
  "access_expires_at": "2026-09-02T12:10:00Z",
  "refresh_expires_at": "2026-09-09T12:00:00Z",
  "account_id": "00000000-0000-4000-8000-000000000001",
  "session_id": "00000000-0000-4000-8000-000000000002",
  "onboarding_required": true
}
```

Refresh accepts only `{"refresh_token":"<opaque-refresh-token>"}`. Current
session returns `account_id`, `session_id`, `display_name`, and
`onboarding_required`. Logout operations have no response body.

Refresh must send its opaque refresh token in the body and a stable,
client-generated `Idempotency-Key` header (1–64 characters) for one logical
rotation attempt. A network retry must reuse that key. A new intentional refresh
uses a new key. Never send this header on registration or unrelated operations.

Current-session and logout operations use `Authorization: Bearer <access-token>`.
Logout responses are `204` and safe to retry. On `session_invalid`, clear local
credentials and return to Sign In. On `refresh_session_replayed`, clear all local
credentials, explain that the session ended for safety, and require sign-in.

## Stable error mapping

| Code | Status | Mobile behavior |
| --- | --- | --- |
| `invalid_credentials` | `401` | Show one generic email/password error |
| `email_not_verified` | `403` | Offer the existing email-verification flow |
| `account_unavailable` | `403` | Show account unavailable; do not guess why |
| `authentication_required` | `401` | Return to Sign In |
| `access_token_invalid` / `access_token_expired` | `401` | Attempt one approved refresh, otherwise clear credentials |
| `refresh_session_invalid` / `refresh_session_expired` | `401` | Clear credentials and return to Sign In |
| `refresh_session_replayed` | `409` | Clear credentials; require sign-in |
| `dependency_unavailable` | `503` | Preserve form state and offer retry/offline UI |

## Mobile state and storage requirements

- Disable sign-in inputs and secondary actions during the request. Preserve
  typed email and Remember Me state on network failure; never persist password.
- Render one identical invalid-credentials state for unknown email and wrong
  password. `email_not_verified` navigates to the existing verification flow.
- Remember Me changes only the absolute refresh lifetime: 7 days normally or
  30 days when enabled. Access tokens always last 10 minutes.
- Keep the access token in memory where practical. Store the refresh token only
  through the approved secure-storage abstraction backed by iOS Keychain or
  Android Keystore. Never use AsyncStorage.
- Prohibit tokens and request bodies from logs, analytics, crash reports, URLs,
  screenshots, support tickets, and clipboard workflows.
- On app restoration, load the refresh credential securely, refresh if needed,
  then call `GET /api/v1/auth/session`. Backend timestamps are authoritative;
  device time is only a scheduling hint.
- After sign-in/current-session success, route by `onboarding_required`. This is
  the transition from 07-03 verification's `session: null`; verification itself
  still does not create a session.
- Offline before sign-in/refresh shows retry-safe network UI without converting
  the failure into invalid credentials. Do not discard a still-valid credential
  solely because the network is unavailable.

Do not parse human-readable messages or JWT claims for product behavior. Treat
both tokens and the refresh idempotency key as sensitive diagnostics. Retain only
the safe `X-Request-ID` for support. Google, Apple, password recovery, biometrics,
and lockout UX remain outside this handoff.

## Mocking, verification, and acceptance

Frontend unit tests should mock sanitized token placeholders, the identity facts
above, each stable error code, offline failure, and onboarding true/false. Never
copy locally issued real tokens into fixtures.

For Founder Postman or cURL verification, use a newly created dummy account:
sign in, inspect only status/field names and cache headers, call current session,
refresh once, retry with the same `Idempotency-Key`, confirm another key rejects
the old token, then sign in again and verify current/all logout. Keep secrets in
tool variables and disable console/history capture; never paste responses into a
report. iOS and Android use their platform secure stores behind one abstraction;
no other platform behavior differs in this backend contract.

Acceptance requires stable status/error mapping, exact 10-minute access expiry,
7/30-day refresh selection, same-key retry, replay logout, restoration,
idempotent logout, request-ID retention, and zero token leakage. This draft
becomes approved only after Founder manual verification.
