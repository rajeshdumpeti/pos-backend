# Email sign-in and secure session lifecycle

Current security policy: [Milestone 07-08](authentication-security-hardening.md).
Earlier deferred-control notes below describe the milestone at its original delivery.

This is the canonical Milestone 07-04 design for verified email sign-in and
session lifecycle. Registration remains documented separately. Social sign-in,
password recovery, biometrics, rate limiting, and final account-lockout policy
remain deferred.

## Request and dependency flow

`FastAPI route → EmailSessionService → authentication UoW contracts → SQLAlchemy repositories`

Routes map HTTP only. The application service owns logical transactions and
security decisions. Infrastructure owns JWT, opaque-token, AES-GCM, SQLAlchemy,
and PostgreSQL mechanics. Domain records never import those libraries.

Session composition is explicit and requires `DATABASE_URL`, `JWT_SECRET`, and
`AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY`. Missing configuration leaves health,
Swagger, registration when independently configured, and local mock resources
available; session routes return the existing safe `dependency_unavailable`
contract. Importing the app never connects to PostgreSQL.

## Frozen lifecycle

- Access tokens are HS256 JWTs valid for exactly 10 minutes. Verification fixes
  the algorithm to `HS256` and validates issuer, audience, type, timestamps,
  account ID, session ID, token ID, and authentication epoch.
- Clock skew is deliberately zero for this first-party mobile/API contract;
  server timestamps are authoritative and token timestamps use whole UTC seconds.
- JWTs contain no email, display name, role, password, provider token, or other
  profile data.
- Refresh tokens are opaque 256-bit random bearer values. Only SHA-256 digests
  are persisted. Normal sessions expire after 7 days; Remember Me sessions
  expire after 30 days.
- Rotation never extends the original absolute refresh expiry. Repositories do
  not commit; the application use case commits the token replacement, encrypted
  retry receipt, and security event atomically.
- `Idempotency-Key` is required only for refresh. A retry with the same old token
  and key within five minutes returns the exact prior encrypted response. Reuse
  with another key, or after the receipt window, is replay: the token family is
  revoked and the public response is `refresh_session_replayed`.
- Current-session validation checks both the JWT and persisted session/account
  state, so logout and authentication-epoch changes take effect immediately.
- Current-device and all-device logout are idempotent. They return `204` with no
  body and do not create duplicate security events.

## Password and account behavior

Sign-in normalizes email through the registration policy and uses Argon2id.
Unknown email and incorrect password share `invalid_credentials`; unknown email
still performs a dummy Argon2 verification. Only a correct password can produce
`email_not_verified`. Disabled or otherwise unavailable accounts use
`account_unavailable`. A valid older Argon2 parameter set is rehashed inside the
same successful sign-in transaction.

Milestone 07-08 owns throttling, progressive delay, and final temporary-lockout
behavior. This milestone does not infer or implement those policies.

## Secret and diagnostic rules

Access tokens, refresh tokens, passwords, digests, signing material, receipt
keys, ciphertext, emails, request bodies, SQL, and raw driver exceptions are
never logged. Stored security events contain only allow-listed event type,
account/session identifiers, request ID, timestamp, and empty or sanitized
metadata. Token responses use `Cache-Control: no-store` and `Pragma: no-cache`.

Retry receipts use a dedicated URL-safe base64 256-bit AES-GCM key, key ID, and
version. Associated data binds ciphertext to the receipt ID. Key rotation must
retain any version needed to decrypt unexpired receipts; this implementation
currently composes one active version and safely rejects unavailable versions.

## Persistence and migration impact

Milestone 07-02 already supplied `refresh_sessions`,
`refresh_rotation_retry_receipts`, and `security_events`, including the required
constraints and indexes. 07-04 adds repository operations only; it creates no
table, product model, seed, or Alembic revision. The database must remain at
`0003_registration_flow (head)`.

## Validation

Unit tests use deterministic clocks and in-process repositories/doubles. They
cover token expiry and tampering, 7/30-day duration, enumeration resistance,
rotation, exact retry, replay-family revocation, idempotent logout, secret-safe
errors, HTTP caching, OpenAPI, and configuration. PostgreSQL integration uses
the repository's opt-in `POS_TEST_DATABASE_URL` convention and must never target
an unknown or non-local database.
