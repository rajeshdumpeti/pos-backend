# Frontend handoff: password recovery

> Historical milestone handoff. The authoritative frozen v1.0.0 contract is
> [the consolidated authentication handoff](frontend-authentication-handoff.md).

## Request instructions

`POST /api/v1/auth/password-resets`

```json
{"email":"person@example.com"}
```

Every syntactically valid request receives `202` with:

```json
{"status":"instructions_if_eligible"}
```

The UI must always show neutral copy such as: “If that address is eligible,
password reset instructions have been sent.” It must not reveal whether an
account exists, is verified, is disabled, or uses only an external provider.

## Confirm a reset

`POST /api/v1/auth/password-resets/confirm`

```json
{"token":"<opaque-token-from-app-link>","new_password":"<user-entered-password>"}
```

Success is `204` with an empty body. It creates no session. Navigate to Sign In
and explain that all prior sessions were revoked.

Stable errors:

| HTTP | `code` | UI action |
| --- | --- | --- |
| 400 | `reset_token_invalid` | Request a new link |
| 410 | `reset_token_expired` | Request a new link |
| 400 | `password_rejected` | Keep the form and explain password requirements |
| 422 | `request_validation_failed` | Correct highlighted fields |
| 503 | `dependency_unavailable` | Preserve input and retry later |
| 500 | `internal_server_error` | Show safe retry/support state |

Preserve and surface the response request ID in support affordances. Never log
the email, reset token, or password. The app-link/universal-link implementation
and mobile screens remain a separate frontend milestone.
