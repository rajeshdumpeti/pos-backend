# Frontend handoff — Notification Inbox 1.0

Backend delivery SHA: **TO_BE_FILLED_AFTER_REVIEW_AND_COMMIT**. Implementation-only work is not a committed delivery SHA. Inbox contract **1.0**, `/api/v1`; local service base URL `http://127.0.0.1:8000` (use the existing environment configuration elsewhere). Authoritative producer/lifecycle rules: [notification-inbox.md](notification-inbox.md). OpenAPI extension: `x-pos-inbox-contract-version: 1.0`; Today/Plan remain 1.0.

Use the existing shared bearer client, authentication recovery, completed-onboarding gates and safe request-ID handling. Never log/persist response bodies, private navigation references or cursors. **Frontend must never fabricate notifications, unread counts or navigation destinations.** The showcase is a visual reference and contains deferred Details/snooze/skip/completion/settings behavior.

## Endpoints and exact TypeScript-style contracts

```ts
type UUID = string;
type UTCInstant = string; // RFC 3339; server returns UTC Z
type ISODate = string; // YYYY-MM-DD
type IANATimezone = string;
type InboxSource = 'today' | 'plan';
type InboxFilter = 'all' | InboxSource;
type InboxCategory = 'reminder' | 'attention';
type InboxEvent = 'plan.item_starting_soon' | 'today.items_remaining';
type InboxNavigation = {
  state: 'available' | 'unavailable';
  action: 'today.view' | 'plan_item.view' | null;
  selected_date: ISODate | null;
  plan_item_id: UUID | null;
};
type InboxItem = {
  notification_id: UUID;
  source: InboxSource;
  category: InboxCategory;
  event_type: InboxEvent;
  title: string;
  message: string;
  created_at: UTCInstant;
  group: 'today' | 'earlier';
  is_read: boolean;
  read_at: UTCInstant | null;
  source_timezone: IANATimezone; // historical source intent, independent of grouping zone
  navigation: InboxNavigation;
};

// PUT /api/v1/account/timezone
// Authenticated active verified account; completion of onboarding not required.
type PutAccountTimezoneRequest = { timezone: IANATimezone };
type AccountTimezoneResponse = {
  contract_version: '1.0'; timezone: IANATimezone;
  updated_at: UTCInstant; version: number;
};

// GET /api/v1/notifications
// query: source?: InboxFilter (default all), timezone: IANATimezone,
//        limit?: integer 1..100 (default 50), cursor?: opaque string
type InboxListResponse = {
  contract_version: '1.0'; generated_at: UTCInstant;
  timezone: IANATimezone; filter: InboxFilter; unread_count: number;
  items: InboxItem[]; next_cursor: string | null; has_more: boolean;
};

// GET /api/v1/notifications/unread-count?source=all|today|plan
// source defaults all; count is independent of pagination/timezone grouping.
type InboxUnreadResponse = {
  contract_version: '1.0'; source: InboxFilter;
  unread_count: number; generated_at: UTCInstant;
};

// POST /api/v1/notifications/{notification_id}/read; no body or query
type InboxReadResponse = {
  contract_version: '1.0'; notification_id: UUID;
  is_read: true; read_at: UTCInstant;
  source: 'all'; unread_count: number; // account-wide across both sources
};

// POST /api/v1/notifications/read-all
type InboxReadAllRequest = { source: InboxFilter }; // explicit source is required
type InboxReadAllResponse = {
  contract_version: '1.0'; source: InboxFilter;
  marked_read_count: number; unread_count: number; read_at: UTCInstant;
};
```

All objects are closed; unknown fields/query parameters and account IDs are rejected. Available Today navigation has action `today.view`, non-null evaluation date and null item ID. Available Plan navigation has action `plan_item.view` with non-null current assigned date/item UUID. Unavailable navigation has null action, date and item ID. There are no external URLs or invented destinations. Use existing authoritative detail retrieval; completed owned items remain supported. Today selected_date is historical notification context, not authorization for a nonexistent historical Today API.

## Producer behavior and timezone synchronization

Inbox is enabled by default and remains available when OS push permission is denied. Onboarding notification preferences and device permission do not gate it. There is no new push or token API.

Plan mutation and job scheduling/cancellation commit together. A future incomplete scheduled item queues minus 15 minutes, or immediately if already inside that window. No starting-soon record is created once the start is no longer future. Revalidation checks item existence, ownership, completion, version and exact occurrence. Reschedules supersede pending intent; completed items cancel it. Delivered history remains unchanged and deduplicates by account/event/item/exact start. Existing Plan deletion/cancellation APIs do not exist and are not added here; missing-source handling is supported.

Today evaluation occurs at 18:00 in the stored account IANA timezone, only with incomplete assigned items. Zero remaining still advances the next local-calendar evaluation. It uses approved singular/plural deterministic copy, never client/AI-generated copy. No timezone means no Today evaluation. Changed timezone increments a generation/version and supersedes old pending work; stale versions safely no-op. Delayed same-day evaluations are allowed after 18:00; old-date jobs do not create yesterday's record. Both DST transitions use local dates, not +24 UTC hours.

After authenticated startup, PUT the device's valid IANA timezone when it differs from the last confirmed account-scoped synchronized value, or the value is unknown. Store confirmation only after success using existing account lifecycle rules. Identical PUT preserves timestamp/version. Do not guess offsets or depend on permission state; no registration/onboarding timezone requirement or full Settings module is introduced. The preference is account-wide and last-writer-wins across devices.

## List/count, grouping and read behavior

Order is `created_at DESC, notification UUID DESC`. Opaque cursors bind account, source, requested timezone and a retained owned boundary. Never decode/log them; restart from page one on filter/timezone/account change or cursor validation failure. No offset pagination or cross-page snapshot promise exists. Expiry and concurrent writes can change pages; preserve backend order and deduplicate by notification ID defensively.

Counts always cover all retained unexpired unread records in scope. A page's loaded length is not the unread count. A `today` source filter can include Earlier groups. Group is based on notification creation time in the requested timezone and server current time, not scheduled start or item assignment. source_timezone is the event's preserved intent timezone. Retention is exactly 90 elapsed UTC days; expired rows disappear logically even before physical cleanup.

Opening a row may explicitly mark it read and then use its supported navigation. This is not Notification Details. Do not fabricate a destination when unavailable; retain the readable row and allow marking read. Plan navigation reflects the current owned target's assignment, while message/source timezone remain historical. Read-one preserves first read_at on retry and returns an account-wide count. Read-all applies its explicit source scope with one timestamp; repeat changes zero, and its read_at is operation time rather than a rewritten per-row read timestamp. Refetch/invalidate affected lists/counts after confirmed changes; never infer read success from a tap or elapsed time.

## States, failures and privacy

Empty success is authoritative `items=[]`, not a fallback for failure. Loading/refresh/pagination UI derives from requests. Preserve confirmed same-account/filter content with an honest stale/offline notice when refresh fails; backend offers no persisted offline queue or stale snapshot. Retrying read mutations is safe and deliberate; do not replay them automatically after account changes.

Responses and errors use no-store/no-cache and existing safe X-Request-ID. Map 401 to shared session recovery, 403 to existing account/email/onboarding handling, 404 `notification_unavailable` to a safe unavailable row action, 422 `request_validation_failed` to corrected input or explicit pagination reset, 503 `dependency_unavailable` to retry guidance, and 500 `internal_server_error` to generic retry. Existing central error envelope is unchanged, validation details are empty, and no raw SQL/provider/cursor/private body is returned. Do not convert 503/500 into empty success or zero badge counts.

No new in-app notification settings destination exists. OS authorization/settings and Inbox history are independent. Do not hide history because permission is off, promise push delivery after granting permission, or implement the showcase's unsupported actions.

## Synthetic fixtures and acceptance

[notification-inbox-v1-fixtures.json](examples/notification-inbox-v1-fixtures.json) contains explicit synthetic empty, Today, Earlier, mixed All, source-filtered, unread, all-read, unavailable and paginated responses. They are UI/test examples only, never backend seeds or a fallback in production; example cursor IDs do not identify actual server rows.

Verify authentication/onboarding gates; timezone synchronization/version behavior; empty/loading/error/stale/offline states; both filters versus both groups; tied-order paging and full counts; invalid cursor reset; idempotent reads and scoped read-all; source rescheduling/completion/deletion; unavailable identifiers; source timezone versus grouping timezone; expiry across DST; and OS-permission independence. Backend worker batches must be invoked deliberately in an authorized test environment to generate real approved events. Physical push delivery, native OS permission, Details/actions, snooze/skip, notification-based completion and frontend code remain deferred.
