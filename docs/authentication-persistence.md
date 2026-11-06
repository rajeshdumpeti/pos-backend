# POS authentication persistence

This is the canonical persistence guide established by Milestone 07-02.
Milestone 07-03 now uses these boundaries for public email registration and
verification; its behavior is documented in
[`email-registration-verification.md`](email-registration-verification.md).

## Ownership and dependency direction

```text
API -> future authentication use cases -> authentication repository/UoW ports
                                      ^
                    SQLAlchemy infrastructure -> PostgreSQL

domain -> Python types and lifecycle rules only
```

`app/domain/authentication.py` owns typed values, safe records, and account
lifecycle transitions. `app/application/authentication_persistence.py` owns the
repository and Unit-of-Work ports plus safe persistence failures.
`app/infrastructure/authentication/` owns SQLAlchemy models, mapping, queries,
row locks, error translation, and transaction mechanics. Repositories receive a
session and never commit or create sessions. A later use case chooses when its
logical operation succeeds; the authentication Unit of Work commits once or
rolls back and always closes its isolated session.

## Tables and sensitive boundaries

| Table | Purpose | Sensitive representation |
| --- | --- | --- |
| `user_accounts` | Lifecycle, password capability, verification/onboarding/legal facts | Password hash only; nullable for provider-only accounts |
| `email_identities` | Display and normalized account email ownership | Email values are private and excluded from safe record representations |
| `external_provider_identities` | Provider plus stable provider subject | No provider access, identity, or refresh token is stored |
| `email_verification_challenges` | Verification and resend/supersession state | Flow token and code are digests, never raw values |
| `password_reset_challenges` | One-time reset state | Reset token and request-security facts are digests |
| `refresh_sessions` | Rotating session family, duration, revocation, device facts | Refresh token is a digest; device JSON has a safe bounded purpose |
| `refresh_rotation_retry_receipts` | Short authorized lost-response retry window | Replacement response is ciphertext with key ID/version |
| `authentication_attempt_states` | Progressive delay and temporary lock state | Identity and risk bucket are digests; raw passwords/IPs are prohibited |
| `security_events` | Append-only diagnosis/audit facts | Metadata is sanitized, not a payload store |
| `authentication_email_outbox` | Atomic intent, claim, retry, and delivery state | Payload is ciphertext; delivery fields are private |

Native UUIDv4 identifiers, `timestamptz`, explicit nullability, check
constraints, foreign-key actions, and deterministic names follow
[`relational-standards.md`](relational-standards.md). Uniqueness is enforced for
normalized emails, provider subjects, lookup digests, outbox deduplication, and
refresh retry identity. Cleanup and ownership paths have focused indexes;
unique constraints are not redundantly indexed.

## Locking and transactions

- Verification challenges, reset challenges, refresh sessions, and attempt
  state expose explicit `FOR UPDATE` operations.
- Outbox claim uses ordered `FOR UPDATE SKIP LOCKED` and marks a bounded batch
  within the worker transaction.
- A partial unique index permits one current unconsumed, unsuperseded challenge
  per account/purpose.
- Account, challenge, and outbox writes can share one Unit of Work and commit
  atomically. Repository `flush()` exposes invariants early but never commits.
- Future use cases, not routes or repositories, own retry policy.

## Migration and testing

Revision `0002_auth_persistence` follows `0001_migration_foundation`. The short
ID fits Alembic's standard 32-character version column; the filename/message
retain the full milestone name. Upgrade and reverse-order downgrade are explicit
and contain no seed data.

Revision `0003_registration_flow` adds flow expiry, digest key ID/version,
completion action, lifecycle checks, and a deferred supersession self-reference.
It changes no table inventory and seeds no account or authentication data.

After proving the configured target is the authorized local `pos_local`
database for `pos_app_local`, load local values without printing them and run:

```bash
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest tests/test_authentication_postgresql.py -q
.venv/bin/alembic downgrade 0002_auth_persistence
.venv/bin/alembic upgrade head
```

PostgreSQL tests require `POS_TEST_DATABASE_URL`; ordinary tests skip them
deliberately and remain database-independent. CI supplies disposable PostgreSQL
with test-only credentials. Never point the test variable at staging or
production.

## Safe errors and observability

The internal taxonomy distinguishes duplicate normalized/provider identity,
missing record, stale/concurrent update, constraint rejection, unavailable
persistence, and unexpected failure. Fixed messages omit SQL, constraint names,
database details, emails, and authentication artifacts. Logs use stable
operation, category, outcome, and optional request ID fields. Driver exceptions
remain privately attached for controlled diagnosis. Their public exception
chain uses a redacted cause so Python, Alembic, and pytest tracebacks cannot
render raw connection parameters. `database_diagnostics.py` also supplies the
allow-listed structured logger, defensive traceback formatter, and pytest
redaction boundary used by failure diagnostics.

| Failure | Safe signal | Immediate diagnostic command | Never print | Recovery |
| --- | --- | --- | --- | --- |
| Migration mismatch | revision/head mismatch | `.venv/bin/alembic current` and `heads` | database URL | Stop writes; compare reviewed revision |
| Database unavailable | UoW start/unavailable | secret-safe connectivity check | credentials/URL | Verify service; escalate infrastructure |
| Pool failure | unavailable category | pool/service health | SQL parameters | Investigate saturation |
| Unique conflict | typed duplicate category | inspect named constraint | email/provider subject | Apply frozen collision flow |
| Transaction rollback | rollback + request ID | correlate safe logs | secret inputs | Confirm atomic rollback |
| Deadlock/serialization | concurrent category | PostgreSQL lock views | parameter values | Retry only approved idempotent work |
| Refresh conflict | safe session/family ID | inspect family state | tokens/digests/ciphertext | Apply later replay policy |
| Outbox backlog | status/age/count | bounded status aggregate | addresses/bodies | Repair worker/provider |
| Cleanup backlog | expired count/oldest time | bounded expiry aggregate | secret columns | Run approved cleanup job |
| Schema drift | parity difference | focused parity test | connection details | Block release; corrective migration |

Future metrics should cover database availability, rollback/conflict rate, lock
latency, outbox age/failures, expired-secret backlog, and schema drift without
adding a vendor in this milestone.

## Deferred behavior

Final cleanup/retention periods and bounded cleanup jobs remain required before
production. Milestone 07-03 owns email normalization, Argon2id registration
hashing, verification material, encrypted registration email payloads, and the
four versioned endpoints. Later slices still own sessions/sign-in, provider
verification/linking, general rate limits, production key management and email
workers, authorization, and authenticated mobile routing.
