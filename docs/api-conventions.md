# POS Backend API conventions

This document is the authoritative contract policy for the current POS Backend.
The service is still an optional, local, in-memory mock. Its existing fixtures
support mobile UI development but do not define the final production API.

## Current endpoint inventory

| Method | Path | Success | Response | Errors | Tag | Operation ID |
| ------ | ---- | ------- | -------- | ------ | --- | ------------ |
| `GET` | `/health` | `200` | `HealthResponse` | `500` | Health | `get_health` |
| `GET` | `/{resource}` | `200` | Existing fixture JSON | `404`, `422`, `500` | Mock resources | `get_mock_resource` |
| `POST` | `/{resource}` | `200` | `Item` | `400`, `422`, `500` | Mock resources | `create_mock_resource_item` |
| `PATCH` | `/{resource}/{item_id}` | `200` | Updated item object | `400`, `404`, `422`, `500` | Mock resources | `update_mock_resource_item` |
| `DELETE` | `/{resource}/{item_id}` | `204` | No body | `400`, `422`, `500` | Mock resources | `delete_mock_resource_item` |
| `POST` | `/demo/reset` | `200` | `ResetResponse` | `500` | Demo | `reset_mock_demo` |
| `POST` | `/api/v1/auth/registrations` | `202` | `PendingVerificationResponse` | `400`, `422`, `429`, `500`, `503` | Authentication | `create_email_registration` |
| `POST` | `/api/v1/auth/email-verifications/confirm` | `201` | `EmailVerifiedResponse` | `400`, `409`, `410`, `422`, `429`, `500`, `503` | Authentication | `confirm_email_verification` |
| `POST` | `/api/v1/auth/email-verifications/resend` | `202` | `PendingVerificationResponse` | `400`, `410`, `422`, `429`, `500`, `503` | Authentication | `resend_email_verification` |
| `PATCH` | `/api/v1/auth/email-verifications/email` | `202` | `PendingVerificationResponse` | `400`, `410`, `422`, `429`, `500`, `503` | Authentication | `change_unverified_email` |

`/docs` and `/openapi.json` remain local framework endpoints. The dynamic mock
routes intentionally retain their existing unversioned paths and fixture shapes.

## Request and response conventions

- Paths use lowercase nouns and hyphenated multiword segments. New collection
  resources use plural nouns unless an approved domain contract requires
  otherwise.
- JSON fields use `snake_case`.
- Identifiers are opaque, stable strings. Clients must not infer ordering or
  meaning from an identifier.
- Required fields must be present. Optional fields may be omitted. Nullable
  fields explicitly allow JSON `null`; optional does not automatically mean
  nullable.
- Machine timestamps use ISO 8601 with an explicit offset and normally UTC `Z`.
  Human-readable dates in current fixtures are display data, not the future
  timestamp contract.
- Empty collections return `[]`, not `null`. Future paginated collections use
  the envelope described below.
- JSON responses use `application/json`. A `204` response has no body and no
  content type. Swagger uses HTML at `/docs`.
- `GET` is safe and idempotent. `PATCH` applies the submitted partial update and
  must be retry-safe only when the update itself is retry-safe. Existing mock
  deletion is idempotent: deleting an absent item still returns `204`. `POST` is
  not assumed idempotent unless a future contract explicitly defines an
  idempotency key.

Existing mock create/reset operations retain `200`; new production creation
contracts should normally use `201`. Successful response changes require an
OpenAPI review, contract tests, and frontend coordination.

## Standard error response

All API-controlled failures use this JSON shape:

```json
{
  "code": "item_not_found",
  "message": "Item not found",
  "request_id": "mobile-debug-123",
  "details": []
}
```

- `code` is stable and machine-readable. Frontends branch on this field rather
  than parsing `message`.
- `message` is concise, safe, and suitable for diagnostics. Product UI may map
  codes to its own user-facing copy.
- `request_id` matches the `X-Request-ID` response header.
- `details` is an ordered collection of safe field-level issues and is empty
  when no field detail applies.

Current stable codes are:

| Code | Status | Meaning |
| ---- | ------ | ------- |
| `resource_not_found` | `404` | A requested mock resource is unknown |
| `item_not_found` | `404` | A requested collection item is missing |
| `resource_operation_not_supported` | `400` | The resource cannot perform that mutation |
| `request_validation_failed` | `422` | Pydantic/FastAPI rejected request input |
| `route_not_found` | `404` | No HTTP route matches the path |
| `method_not_allowed` | `405` | The path does not support the method |
| `http_error` | Varies | Another approved HTTP-layer failure |
| `internal_server_error` | `500` | An unexpected implementation failure |
| `password_rejected` | `400` | Password policy or compromised-password check rejected input |
| `verification_flow_invalid` | `400` | Flow is invalid, superseded, or consumed |
| `verification_flow_expired` | `410` | Registration/verification flow expired |
| `verification_code_invalid` | `400` | Six-digit code did not verify |
| `verification_code_expired` | `410` | Issued code expired while the flow remains usable |
| `verification_attempts_exceeded` | `429` | Five attempts were consumed |
| `resend_cooldown_active` | `429` | Backend resend cooldown remains active |
| `account_already_exists` | `409` | Mailbox ownership proved for an existing account |
| `rate_limited` | `429` | Reserved for a separately approved general rate limit |
| `dependency_unavailable` | `503` | Required persistence/cryptographic dependency is unavailable |

Domain errors remain framework-independent. The API boundary maps them to
status codes and error codes. Validation details contain only `field`, `code`,
and `message`; submitted input and validator context are excluded. Unexpected
exceptions remain HTTP 500 failures and remain visible to the server runtime,
while client responses exclude exception names, stack traces, paths, secrets,
and internal messages.

## Request identification

Every HTTP response includes `X-Request-ID`. A caller-provided value is retained
only when it is 1–64 characters, starts with an ASCII letter or number, and then
contains only ASCII letters, numbers, `.`, `_`, or `-`. Missing, malformed, or
overlong values are replaced with a generated 32-character hexadecimal ID.

Mobile clients should retain the response ID with sanitized failure diagnostics.
Request IDs are correlation metadata, not credentials, authentication tokens, or
authorization inputs.

## Lifecycle, compatibility, and versioning

An endpoint becomes an approved contract only after its OpenAPI definition,
behavioral tests, security review, and frontend coordination are approved. The
current generic mock endpoints are provisional UI-development contracts.

Backward-compatible changes include adding an optional field, adding an endpoint,
or adding an error code for a newly documented failure. Breaking changes include
removing or renaming a field or endpoint, changing a field type or meaning,
changing a success status, or making optional input required.

Breaking changes require an explicit migration plan and coordinated mobile
release. Deprecated behavior remains available until every supported client has
migrated; no fixed deprecation clock is claimed before production support policy
exists.

The generic local mock API remains unversioned. The first production-backed
authentication contract uses `/api/v1/auth`; future stable authentication
changes must preserve it or follow an explicit coordinated migration. Do not
duplicate the version tree. Every contract change requires an OpenAPI diff
review.

## Pagination policy

Future unbounded lists use cursor pagination because it remains stable when
records are inserted or removed. The convention is:

- Query parameters: `cursor` (opaque, optional) and `limit` (default `50`, maximum
  `100`).
- Ordering: deterministic and documented, with a unique tie-breaker.
- Response: `items`, `next_cursor`, and `has_more`.
- Empty page: `{"items": [], "next_cursor": null, "has_more": false}`.
- Invalid or expired cursors use the standard validation/error contract.

Current small fixture arrays remain unpaginated. No database-dependent paging
logic is introduced by this foundation.

## OpenAPI and security rules

- Every operation has a unique explicit operation ID, meaningful tag, concise
  summary, success status, and relevant error responses.
- Request and stable response bodies use named Pydantic schemas where the current
  heterogeneous fixture contract permits.
- OpenAPI must generate successfully and remain free of environment values,
  secrets, internal exception types, and implementation paths.
- Secrets, submitted sensitive values, stack traces, SQL details, configuration,
  and infrastructure exceptions never belong in responses or documentation.
- Frontend changes must consume stable error codes, tolerate additive optional
  response fields, treat cursors as opaque, and report request IDs without
  attaching sensitive user data.
