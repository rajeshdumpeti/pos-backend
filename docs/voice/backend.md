# Voice v1 backend (VOICE-M3)

Voice input is on-device speech on mobile. Speech, audio and recognized words never reach the
backend. The backend only does two small things: it tells the app whether voice is enabled for
the signed-in account, and it records a content-free origin for plan-item creation.

Approved spec: POS-VOICE-PRD-001 v1.0 (D13, D14). Founder amendment, 2026-11-25: the flag is
exposed through a new endpoint, `GET /api/v1/features`, not through Today
`capabilities.actions`. The mobile app validates Today strictly, so adding an action value would
break installed builds. The Today and Plan responses are unchanged.

## Feature flags

`GET /api/v1/features` (operation ID `get_feature_flags`)

```json
{ "contract_version": "1.0", "voice_entry": { "enabled": true } }
```

- Same authentication as Today and Plan: a Bearer access token for an account that has completed
  onboarding. The same 401 and 403 error envelope applies.
- `Cache-Control: no-store` and `Pragma: no-cache` on every response, including errors.
- The response is a closed schema. It never reveals the mode, the allowlist or other accounts.
- The decision is a pure function of configuration and the account ID. There is no database
  table and no migration.

### Modes

Set with environment variables (typed and validated at startup):

| Variable                | Values                         | Default | Meaning                                     |
| ----------------------- | ------------------------------ | ------- | ------------------------------------------- |
| `VOICE_ENTRY_MODE`      | `off`, `allowlist`, `on`       | `off`   | `off`: nobody. `on`: everybody. `allowlist`: only listed accounts. |
| `VOICE_ENTRY_ALLOWLIST` | comma-separated account UUIDs  | empty   | Used only when the mode is `allowlist`.     |

- An invalid mode stops the service at startup. The mode is case-insensitive.
- An invalid UUID anywhere in the allowlist stops the service at startup, even when the mode is
  `off`. Whitespace, empty entries and duplicates are tolerated. Error messages never echo the
  value.
- `allowlist` with an empty list is valid and enables voice for nobody.

### Rollout and rollback

1. Start with `VOICE_ENTRY_MODE=off`.
2. To try it with chosen accounts, set `VOICE_ENTRY_MODE=allowlist` and list their account IDs.
3. To open it to everyone, set `VOICE_ENTRY_MODE=on`.
4. **Rollback:** set `VOICE_ENTRY_MODE=off` and restart the service. No app release is needed.
   The app reads the flag from this endpoint, so the mic disappears on its next flag refresh.

Changing the variables needs a service restart, because they are read at startup.

## Entry-source header on create

`POST /api/v1/plan/items` accepts an optional header `X-POS-Entry-Source`.

- Accepted values: `manual`, `quick_log`, `voice` (case-sensitive).
- A missing, empty, unknown or over-32-character value is treated as `unspecified`. The header
  can never cause a 4xx.
- It is not part of the idempotency comparison. The same `Idempotency-Key` and body with a
  different or missing header returns the original 201, never 409.
- It does not change the stored item or the response body.
- It is recorded only in the existing `planning_operation_succeeded` log event for
  `plan_item_create`, as the field `entry_source` (`manual`, `quick_log`, `voice` or
  `unspecified`). The log carries no title, date, email or account ID.
- A replayed request is not a new creation, so it does not write a second event.
