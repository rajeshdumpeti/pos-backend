# Email registration and verification

Current frozen client contract: [Authentication Contract v1.0.0](frontend-authentication-handoff.md).
Earlier deferred-control notes below describe the milestone at its original delivery.

Milestone 07-03 introduces POS's first public authentication capability. It is
limited to account registration and proof of email ownership. Sign-in, session
creation, JWTs, password recovery, social providers, biometrics, and production
email delivery remain deferred.

## Architecture and transaction boundary

The dependency path is:

`FastAPI route -> EmailRegistrationService -> authentication contracts -> SQLAlchemy unit of work -> PostgreSQL/outbox`

Routes translate typed HTTP payloads only. The application service owns each
logical transaction. Repositories flush but never commit. Registration,
challenge replacement, identity changes, security events, and encrypted outbox
writes commit once or roll back together. A network email provider is never
called inside the transaction.

The registration endpoints are under `/api/v1/auth`:

- `GET /registration-policy`
- `POST /registrations`
- `POST /email-verifications/confirm`
- `POST /email-verifications/resend`
- `PATCH /email-verifications/email`

The initial registration response is deliberately equivalent for new,
pending, and active email identities. An active account is disclosed only
after a valid code proves control of its mailbox. A repeated registration never
changes an existing password, name, legal acceptance, or account state.
Clients fetch current legal identifiers from `GET /registration-policy`.
Outdated submitted identifiers return `409 legal_acceptance_outdated` with
action `refresh_registration_policy`; false acceptance booleans remain validation
failures.

## Password policy

Passwords must contain 15 through 1,024 Unicode characters. POS imposes no
composition rule and accepts spaces, paste, autofill, password managers, and at
least 64 characters. An offline replaceable compromised-password port blocks a
small deterministic set of obvious passwords without transmitting plaintext.
Production-grade compromised-password corpus/provider selection remains
deferred.

The infrastructure adapter uses Argon2id through `argon2-cffi` with defaults of
64 MiB memory, three iterations, four lanes, a 32-byte output, and a 16-byte
random salt. The parameters meet the current minimum security baseline and are
typed configuration so they can be tuned from measured deployment benchmarks.
`verify` uses the maintained library path and `needs_rehash` supports later
sign-in upgrades. Plaintext is never persisted or included in representations,
logs, API errors, or test diagnostics.

On the Founder's local Python 3.12 development machine, three hashes at the
default settings measured 38.2 ms, 35.8 ms, and 34.6 ms (35.8 ms median). This
is development evidence only: staging must benchmark representative hardware
and tune within the documented minimums before production approval.

## Email identity policy

`email-validator` is the single syntax and Unicode/IDNA normalization boundary.
POS preserves its normalized display form and separately stores a case-folded
local part plus ASCII IDNA domain for comparison. Uniqueness is
case-insensitive. Plus tags and provider-specific dots are preserved; an email
address is never used as an external-provider subject.

## Verification material and lifecycle

Each challenge has an exactly six-digit, cryptographically random code, a
10-minute code lifetime, five attempts, and single-use state. Each flow token
contains at least 256 random bits and has a separate 30-minute flow lifetime.
The database stores only HMAC-SHA-256 digests, produced with domain separation
and a configured key ID/version. Comparison uses `hmac.compare_digest`.

Resend has a server-authoritative 60-second cooldown. It atomically supersedes
the current challenge and creates a replacement challenge and outbox record.
Changing an unverified email supersedes the old flow. A collision with another
identity creates an ownership-proof flow without modifying either account;
only successful proof may return `account_already_exists`.

A correct sign-in password for a pending unverified account returns a masked,
typed continuation. While a challenge is usable, it rotates only the active flow
credential and preserves the code, expiry, cooldown, and single outbox message.
If no usable challenge exists, challenge and encrypted outbox creation share one
transaction. Invalid credentials never create a flow.

## Encrypted outbox and local inspection

Verification email content is encoded and encrypted with AES-256-GCM. Every
message uses a unique 96-bit nonce and binds its deduplication context as
additional authenticated data. Only nonce-prefixed ciphertext, key ID, and key
version are persisted. Authentication, key-version, and parsing failures become
safe typed errors.

No HTTP debug endpoint exists. For deliberate local inspection, set
`APP_ENV=local` or `development`, configure the database and encryption key,
and explicitly set `AUTH_LOCAL_EMAIL_INSPECTION_ENABLED=true`. Then run:

```bash
python -m app.infrastructure.authentication.local_email inspect-latest
```

The command reads the latest pending encrypted outbox record, prints only a
masked destination, its code, and expiry, and fails with a generic message. It
is rejected in test, staging, and production. Do not paste its output into logs,
tickets, or reports.

## Configuration and key rotation

`.env.example` contains variable names and generation instructions only. The
digest and encryption keys are separate URL-safe base64-encoded 256-bit values.
Key IDs and positive versions are stored with records. Protected environments
require the database URL, both keys, and current Terms and Privacy versions.
Secrets are `SecretStr` values and are decoded only during infrastructure
composition.

Rotation currently means retaining an adapter capable of reading every live
key version while writing with the newest version. The schema records the
version, but a production key-ring implementation is deferred and must precede
rotation of live challenges/outbox messages.

## Safe errors and diagnosis

Public failures use the centralized error envelope and request ID. Logs use an
allow-list: operation, outcome, request ID, safe category, and optional internal
UUIDs. Never log email, password/hash, code, flow token/digest, key, ciphertext,
request body, SQL, or database URL.

Operational checks:

- No email: find `registration` accepted by request ID, then inspect pending
  outbox count/status; do not decrypt in production.
- Backlog: inspect pending/claimed/failed counts and oldest available timestamp.
- Invalid/expired code: use the safe error category and authoritative expiry;
  never request the code from the user.
- Cooldown dispute: use `retry_at` and `retry_after_seconds` from the response.
- Duplicate account: initial 202 is intentionally indistinguishable; disclosure
  occurs only after ownership proof.
- Constraint conflict: correlate request ID and safe constraint category; never
  render the driver exception.
- Encryption/key mismatch: compare configured non-secret key ID/version with
  stored metadata and preserve ciphertext.
- Schema mismatch: confirm Alembic is at `0004_auth_contract_v1` and run the
  metadata-parity test.

## Migration

Revision `0003_registration_flow` adds the separate flow expiry, digest key
metadata, completion action, constraints, and a deferred self-reference needed
for atomic challenge supersession. It adds no account/product table and seeds no
data. Upgrade, downgrade, re-upgrade, and metadata parity are required before
release.
Revision `0004_auth_contract_v1` adds only the refresh-receipt idempotency-key
uniqueness invariant; it does not change registration storage.
