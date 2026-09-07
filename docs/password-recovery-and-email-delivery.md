# Password recovery and authentication email delivery

Current security policy: [Milestone 07-08](authentication-security-hardening.md).
Earlier deferred-control notes below describe the milestone at its original delivery.

## Boundaries

The HTTP layer validates and maps requests. `PasswordRecoveryService` owns the
logical operation. Application-facing repository and unit-of-work contracts own
no SQLAlchemy details. SQLAlchemy repositories implement locking and mutations,
and the unit of work owns commit, rollback, and session closure.

The request endpoint deliberately returns the same `202` response for eligible,
unknown, unverified, provider-only, disabled, and otherwise suppressed accounts.
Only an active account with a verified primary email and password credential
receives an outbox record. Routes do not send email.

## Token and transaction lifecycle

Reset tokens contain 256 random bits and expire after 30 minutes. Only a keyed,
domain-separated HMAC digest is persisted. A new eligible request consumes prior
active reset challenges while holding the account row lock. Confirmation locks
the challenge and account, validates single use and expiry, applies the existing
password policy and Argon2id hashing, increments the authentication epoch,
revokes every refresh session, consumes the challenge, appends a security event,
and commits once. Concurrent confirmation therefore has one winner.

The raw token exists only during generation, in the AES-256-GCM encrypted outbox
payload and reset link, and in the incoming confirmation request. It is excluded
from representations, logs, errors, database columns, and OpenAPI examples.

## Email delivery

`AUTH_EMAIL_PROVIDER=local` is the network-free default and leaves messages for
the explicitly enabled local inspection tool. `resend` activates the narrow
HTTPS adapter. It sends both text and accessible HTML and uses the outbox UUID as
Resend's `Idempotency-Key`. Bounded network timeouts are mandatory. `429` and
`5xx` outcomes are retryable; other provider rejections are permanent. Provider
bodies, recipients, payloads, credentials, and raw exceptions are never logged.

Process a bounded production batch deliberately:

```bash
python -m app.infrastructure.authentication.email_delivery process-pending 25
```

Workers claim with `FOR UPDATE SKIP LOCKED`, commit the claim, deliver, and then
record accepted, retryable, or permanent status in a separate transaction.
Claims have a five-minute lease; abandoned claims can be reclaimed safely with
the same provider idempotency key.
Resend acceptance is not proof of inbox placement.

## Configuration

- `AUTH_PASSWORD_RESET_URL`: app-owned reset destination. Protected environments
  require HTTPS; the token is appended as a query parameter only after decrypt.
- `AUTH_EMAIL_PROVIDER`: `local` or `resend`.
- `RESEND_API_KEY`: secret and required only for `resend`.
- `AUTH_EMAIL_FROM`: sender required for `resend`; protected environments reject
  the `resend.dev` test domain.
- Existing challenge-digest and outbox-encryption keys protect reset material.

No configuration values may appear in diagnostics. The example environment
contains blank secrets and URLs and keeps local inspection disabled.

## Testing and operations

Automated tests use doubles and never call Resend or require a network. Live
PostgreSQL tests run only against the explicitly guarded disposable test
database. Operators must configure a verified sender domain, set a controlled
universal-link URL, run the migration/metadata parity checks, and verify a test
delivery before production enablement.

Broad account-aware/IP rate limiting, provider webhooks, bounce/complaint
handling, inbox placement monitoring, and product UI integration are deferred.
