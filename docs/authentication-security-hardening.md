# Authentication security hardening — Milestone 07-08

Email/password only. This document is the canonical threat model and security
policy for 07-08, supplementing [registration](email-registration-verification.md),
[sessions](email-sign-in-session-lifecycle.md), and
[recovery/email](password-recovery-and-email-delivery.md). Google, Apple and
biometrics are deferred to a later MVP. 07-09 remains the final authentication
contract and release-handoff milestone; its OpenAPI freeze and staging release
matrix are not performed here.

## Evidence and preserved contracts

Starting revision: `920c6b4094c17cc678cf917627d635d26d1614f8`, merged password
recovery (#17), following session lifecycle (#16), registration (#15), and
persistence (#14). The available canonical milestone documents and frontend
handoffs define the preserved behavior. No separately named Frozen Authentication
Contract v1, 07-01B handoff, or prior canonical threat model is present in this
checkout. This document does not reconstruct missing approved documents.

Unchanged frozen limits: six-digit verification code, 600-second code lifetime,
five challenge attempts, 60-second resend cooldown, 1,800-second flow lifetime;
15–1,024 Unicode-character passwords; HS256 access JWTs for exactly 600 seconds
with zero clock skew; 7/30-day absolute refresh lifetime; 300-second exact refresh
retry receipt with the same idempotency key. Reusing a rotated token with a
different key, or outside that retry window, revokes its family. The permitted
same-key retry is not a second rotation.

## Architecture and admission policy

`ASGI HTTP boundary → AuthenticationAdmission → Limiter/SecurityKeyDeriver ports
← local or Redis infrastructure`. Existing routes still map HTTP to authentication
application services; those services own account/challenge/session transactions.
Admission precedes account resolution, password hashing, and database access.
Internal callers must use the admission port before exposing another network
entry point. The existing generic mock resources are not production protected
resources; this milestone adds no authorization or RBAC.

Every attempt counts, including successful ones. Counters use a fixed duration
starting at first admission; rejected attempts do not extend expiry. There is no
permanent lock. A distributed attacker can repeatedly consume a victim's identity
budget; recovery and expiry bound individual cooldowns, not a sustained attack.

| Operation setting name | Source attempts | Identity/material attempts | Window seconds |
|---|---:|---:|---:|
| `REGISTRATION` | 20 | 5 | 900 |
| `VERIFICATION_CONFIRM` | 60 | 10 | 600 |
| `VERIFICATION_RESEND` | 30 | 5 | 600 |
| `CHANGE_EMAIL` | 20 | 5 | 900 |
| `SIGN_IN` | 60 | 10 | 900 |
| `PASSWORD_RESET_REQUEST` | 20 | 3 | 900 |
| `PASSWORD_RESET_CONFIRM` | 30 | 5 | 900 |
| `REFRESH` | 120 | 30 | 60 |
| `RESTORE` | 120 | 0 (source only) | 60 |
| `LOGOUT` | 60 | 0 (source only) | 60 |

For each row, configure `AUTH_LIMIT_<NAME>_SOURCE`,
`AUTH_LIMIT_<NAME>_IDENTITY`, and `AUTH_LIMIT_<NAME>_WINDOW_SECONDS`.
Windows are bounded to 1–3,600 seconds, source limits to 1–100,000, and identity
limits to 1–100,000 except source-only policies which allow zero. Defaults permit
ordinary typo correction, retries and multi-device use while bounding expensive
hashing and email volume. These new policy values are conservative initial
settings, not capacity benchmarks. Measure shared-NAT effects and deployment load
before tuning. Frozen challenge-level controls remain independently enforced.

All endpoints first consume the shared emergency ceiling (10,000 attempts per
60 seconds), then their source bucket. Only admitted, bounded JSON can create
an identity bucket. Registration, sign-in, change-email and recovery-request
identities use the existing email normalizer (case folding and IDNA; no stripping
of plus tags/dots). Verification uses the opaque flow value; reset confirmation
uses reset material; refresh uses the presented opaque refresh value. These
values enter only domain-separated HMAC-SHA-256 derivation, never storage keys or
values. Account existence and credential correctness never select a bucket.

## Privacy and failures

Public throttling is `429 rate_limited`, centralized error envelope, request ID,
`Retry-After` integer 1–3,600 seconds, `Cache-Control: no-store`, and
`Pragma: no-cache`. No dimension, threshold, identity or material is disclosed.
Existing and unknown identities follow the same admission sequence and policy.
Retry times describe request history, not account existence.

Forgot Password suppresses rate-limited requests and limiter outages with the
same `202 {"status":"instructions_if_eligible"}` and no `Retry-After`. Database
availability/write failures after rollback also preserve that acknowledgement.
It is an acknowledgement, not proof that mail was queued. Missing whole-service
configuration remains the established uniform `503`; malformed HTTP/JSON remains
an input error independent of account existence. Other unexpected failures after state access also return the generic
acknowledgement after rollback. Pre-resolution cryptography failures retain the
uniform safe existing error contract. Verification resend is a
flow-token operation, not an email lookup: admission never reads account state;
its existing replacement-flow response and 60-second cooldown are preserved.

Other operations fail closed with `503 dependency_unavailable` on limiter
failure or capacity exhaustion. There is no memory fallback when Redis is selected.
Mutations already authorized before an outage can complete; the limiter is not a
distributed transaction coordinator. No automatic network retry is performed.

## Redis and local infrastructure

`AUTH_RATE_LIMIT_BACKEND=memory` is the local default. A per-app random 256-bit
key is used when no key is configured. Memory state is thread-safe, capped and
clock-injectable, and resets on restart. It is unsuitable for multiple replicas.

Redis uses one atomic Lua admission invocation per source/global phase and per
identity phase. Two fixed, same-slot namespace keys contain a hash of counters
and a sorted set of logical expiry times. Members are only keyed digests; values
are counts/timestamps. At most `AUTH_RATE_LIMIT_MAX_ENTRIES` members (100,000 by
default; range 100–1,000,000) exist. Each invocation cleans at most 128 expired
members; directly accessed expired members reset immediately. Namespace keys
expire after 3,601 idle seconds. Capacity exhaustion fails closed. Counters use
Redis TIME so application instance clock differences do not alter enforcement.
All instances of one environment require the same key, policies and dedicated
Redis database/namespace. Use a separate Redis database per environment.

The maintained `redis` client is pinned to 6.4.0. `AUTH_REDIS_TIMEOUT_MS=200`
(range 10–2,000) bounds both connect and read; pool maximum is 20 connections,
with zero retries. `REDIS_URL` is SecretStr, rejects query options/fragment and
requires a numeric database path, preventing URL overrides of timeout/retry
policy. Use `rediss` outside a protected private transport boundary. No Redis
connection occurs at import. Client closure belongs to application shutdown.
Redis should use a dedicated capacity budget and `noeviction`; eviction/restart
can reset throttling history. This is a documented residual operational risk.
See official [Lua atomicity](https://redis.io/docs/latest/develop/programmability/eval-intro/)
and [client timeouts](https://redis.io/docs/latest/develop/clients/redis-py/produsage/).

## Trusted proxies and HTTP

`AUTH_TRUSTED_PROXY_NETWORKS` defaults empty; only the immediate ASGI peer is used.
Untrusted forwarding headers are ignored, including invalid ones. Trusted CIDRs
must be canonical and cannot trust all addresses. For a trusted immediate proxy,
only one X-Forwarded-For header with at most eight literal addresses/1,024
characters is accepted. The resolver walks right to left through configured
trusted hops and selects the nearest untrusted address. Duplicate XFF, invalid
addresses, zone identifiers and any Forwarded header from a trusted proxy are
rejected safely. Other forwarded headers are never used. IPv4-mapped IPv6 maps
to IPv4. Full source addresses are transient and never logged. Non-IP local ASGI
peers/Unix sockets share an explicit unknown-local bucket.

The edge must append the real peer to XFF or overwrite it. Start Uvicorn with
`--no-proxy-headers --no-access-log --no-server-header`: application trust
resolution requires the original immediate peer; Uvicorn must not rewrite it
first. Proxy/CDN/access-log policy must independently suppress bodies, credentials,
URLs with query strings, full IPs, and sensitive headers. Python middleware cannot
remove a server header added by the ASGI server after application processing.

Authentication body size defaults to 16,384 bytes (`AUTH_MAX_BODY_BYTES`, allowed
8,192–65,536), enforced while receiving chunks even without Content-Length.
Authorization length defaults to 4,096 bytes (`AUTH_MAX_AUTHORIZATION_BYTES`,
allowed 1,024–4,096). Credentials must use one unambiguous Bearer header; duplicates,
whitespace ambiguity and unexpected schemes are rejected. No auth query strings
are accepted. Idempotency-Key is a single 1–64-character ASCII identifier using
letters, digits, dot, underscore or hyphen. JSON mutation bodies require
application/json; compressed bodies, duplicate JSON members, nonfinite JSON
numbers, and non-object bodies are rejected. Field bounds and extra-field
rejection remain in the existing schemas. Auth validation details are empty to
avoid reflecting attacker-controlled JSON property names. Error codes `413
request_too_large` and `415 unsupported_media_type` are additive input failures.

All auth responses prevent caching, including errors and logout. Request IDs
preserve the established safe ASCII grammar; duplicates generate a new ID.
Unexpected auth exceptions are contained with a fixed safe diagnostic/error.
CORS uses approved origins, never credentialed wildcard behavior, and HTTPS
origins in staging/production. No authentication cookies exist, so cookie-CSRF
infrastructure is not applicable. The existing encrypted reset-email link is the
only preserved token-bearing URL; no new redirects are created.

## Session gate, credentials and transactions

The existing `EmailSessionService.authenticate` is the reusable session gate.
JWT verification fixes HS256, issuer `pos-backend`, audience `pos-mobile`, header
type JWT and claim type access. All claims are required; audience lists, duplicate
JSON claims/headers, non-integer/boolean numeric dates and epoch, malformed/nil
UUID subject/session/jti, inconsistent lifetime, future iat/nbf and expired exp
are rejected. nbf equals iat, exp is exactly iat + 600 seconds, with frozen zero
skew. Header fields other than alg/typ are rejected. See
[PyJWT verifier](https://github.com/jpadilla/pyjwt/blob/master/jwt/api_jwt.py).

Persisted account must be active with email_verified_at, session must belong to
that account, be unrevoked/unexpired, and account/session/token epochs must match.
The exact-retry receipt path now also checks the persisted account and epoch.
Logout with a previously revoked credential is a no-op; it cannot revoke newer
sessions. Access tokens from reset/revoked sessions cannot restore a session.
All-device logout revokes all sessions under the frozen contract; password reset
also increments the account epoch. No RBAC checks are introduced.

Refresh persists only digests plus the existing encrypted exact-response receipt.
Conditional digest replacement and row locks prevent two rotations. Replay
revocation and its security event commit together; failed receipt encryption or
persistence rolls back rotation. Account/session lock contention can produce a
safe dependency error; clients must use the same idempotency key for an uncertain
refresh outcome. Service-level PostgreSQL tests cover races and rollback, but
require a guarded database to execute.

Argon2id remains 64 MiB/three iterations/four lanes, 32-byte output and 16-byte
salt, with existing typed configuration. Registration/reset share the existing
15–1,024 Unicode-character policy and offline compromised-password checker;
spaces and Unicode are not trimmed/truncated. Sign-in and the hashing adapter
reject oversized input before the primitive. Maintained verification and
transactional rehash remain; rehash failure cannot commit a new session or
partial password change. The small offline blocklist is not a comprehensive
breached-password corpus.

## Events and operational diagnosis

Successful mutations, challenge-attempt increments and replay containment append
security_events inside their primary transaction. Persistence failure rolls back
the mutation. Domain records enforce approved event types, UUID identifiers,
safe request IDs, keyed source format, and the current metadata allow-list
(empty, or category=invalid_code). No new schema or duplicate table is needed.

Rejected admission, session restoration, configuration and dependency state use
best-effort structured diagnostics. Those diagnostics are not additional database
writes under attack and are not claimed as durable security-event records.
Diagnostic sink failure cannot change a successful committed response. Labels
are allow-listed, IDs are UUID objects, request IDs are sanitized, and no raw IP,
email, user-agent, material, digest, body, provider response or exception text is
included. Authentication availability retains its established diagnostic shape.

Investigate by response request ID, operation, outcome/category and approved
UUIDs: distinguish admission denial, dependency failure, invalid credentials,
replay containment and an outbox backlog. Inspect counts/status/key versions,
never decrypted payloads. Alerts on elevated denial/failure rates and outbox
backlog belong to operators; this milestone does not provision an alert platform.

## Canonical threat model

| Surface / asset | Trust boundary and threat | Existing mitigation | New mitigation | Residual risk | Detection | Deferred controls |
|---|---|---|---|---|---|---|
| Registration / credential and mailbox | Internet to hashing/DB; stuffing and email floods | Equivalent pending responses, shared password policy, encrypted outbox | Source/identity/global admission, body bounds | Distributed requests and sustained targeted cooldowns | Admission category and request ID | CAPTCHA, comprehensive corpus |
| Verification / ownership | Internet to challenge; guessing | Five attempts, six digits, expiry, single use | Source/flow admission, bounded JSON | Mailbox compromise | Rejected challenge event | MFA |
| Resend/change email / mailbox | Internet to challenge replacement; flooding | 60-second resend cooldown, supersession, ownership proof | Separate source/material/email budgets | Stolen flow token, distributed abuse | Admission and resend events | CAPTCHA |
| Sign-in / password/session | Internet to Argon2/DB; brute force and enumeration | Dummy hash, generic invalid credentials, rehash | Source/normalized identity/global budgets, primitive bounds | Distributed credential stuffing is not prevented by limits alone | Sign-in outcomes and throttling | MFA/passkeys, comprehensive corpus |
| Access JWT / identity | Bearer to verifier; algorithm and claim confusion | Signature/issuer/audience/type checks | Strict numeric/UUID/duplicate/lifetime checks and bounded Bearer | Stolen valid bearer until revoke/expiry | Safe token rejection | Asymmetric signing/key ring |
| Restore / active session | JWT to DB; epoch/revocation bypass | Persisted session/account check | Verified timestamp and bounded admission | Already authorized in-flight work | Session-restored/rejection diagnostic | Device binding |
| Refresh / session family | Opaque bearer to locked mutation; concurrent replay | Digest CAS, receipt encryption, family revocation | Receipt account/epoch check, budgets, service regression tests | Same-key stolen retry during frozen 300 seconds; failover races | Rotation/replay event and session UUID | Device binding |
| Logout / other sessions | Old bearer to revocation; stolen revoked credential | Idempotent revoke | Revoked credential becomes no-op before all-session mutation | Active stolen bearer can log out sessions | Logout event | Device confirmation |
| Reset request / mailbox and privacy | Email to eligible DB write; flooding/enumeration | Generic acknowledgement, transactional outbox | Suppression and post-rollback dependency equivalence | Delivery timing and resource contention are not constant-time guarantees | Suppression/dependency diagnostic | Delivery telemetry |
| Reset confirmation / credentials | Bearer to epoch/password; guessing/races | Single-use lock, shared policy, epoch increment, all-session revoke | Material/source admission; regression coverage | Compromised mailbox/reset link | Password-reset event | MFA |
| Outbox / email content | DB to provider; theft/duplicate sends | AES-GCM, claim lease, provider idempotency, bounded HTTPS timeouts | Input-volume admission, protected sender requirement | Provider/mailbox compromise, email delivery delay | Outbox state/counts, UUIDs | Webhooks/bounce monitoring/key ring |
| Local inspection / decrypted material | Developer CLI to secret output | Disabled by default, local/development only | Protected configuration still prohibits inspection | Authorized developer terminal capture | Explicit CLI invocation | Secure frontend storage integration |
| Proxy / source budget | Edge to app; spoofed XFF | No prior explicit application trust policy | Explicit CIDRs/right-to-left XFF, server rewrite disabled | Misconfigured trusted edge, NAT collateral limits | Safe invalid-source response/request ID | Edge deployment validation |
| HTTP / credentials | Untrusted body/header to parser/logs | SecretStr, safe error envelope, field bounds | Streaming size, JSON/header ambiguity checks, no-store everywhere | Edge logs and slow connections require server policy | Input status and request ID | Edge connection limits |
| Configuration / secrets | Environment to adapters | SecretStr, safe loader, key validation | Redis-only protected mode, HTTPS origin, provider requirements | Operator misconfiguration/key compromise | Safe startup variable names | Managed secret rotation |
| Diagnostics / private data | Application to storage/log sink | Transactional append-only events, redacted database errors | Label/UUID/metadata validation and auth exception containment | Caller may deliberately misuse a valid request ID | Safe correlation fields and sink availability | SIEM integration |

## Configuration and manual verification

Additional settings: `AUTH_RATE_LIMIT_KEY` (dedicated URL-safe base64 256-bit
secret), `AUTH_RATE_LIMIT_GLOBAL=10000`, the adapter/timeout/capacity/bounds and
per-operation settings above, `AUTH_TRUSTED_PROXY_NETWORKS`, and
`AUTH_PUBLIC_ORIGIN`. No actual values belong in source control. Protected mode
requires Redis and its shared key, an explicit HTTPS public origin/approved CORS,
complete existing authentication keys/database/legal settings, real email
provider and reset URL. Local import needs no secrets or network. Local email
inspection remains off by default and prohibited in test/staging/production.

Founder prerequisites: isolated disposable `POS_TEST_DATABASE_URL` accepted by
the existing local `pos_test` guard; optional `POS_TEST_REDIS_URL` targeting a
dedicated local database 15 plus `POS_TEST_REDIS_DISPOSABLE=1`; no ordinary dev DB
or shared Redis. No .env reading is necessary for automated validation. Real
email delivery requires a verified sender and app-owned HTTPS reset destination.

1. Run database-independent tests; then guarded PostgreSQL and Redis tests when
   explicitly configured. Confirm single migration head and live metadata parity.
2. Start the API with the safe server flags above. Use disposable test accounts,
   a secret-aware client with body/history logging off, and status/header-only
   evidence. Never paste credentials or token-bearing URLs into a report.
3. Register, verify and sign in. Check wrong-password and unknown-email states,
   limit expiry, resend cooldown, and generic repeated recovery acknowledgements.
4. Verify one rotation, same-key retry, different-key replay containment, and
   current/all-device logout. Confirm a revoked bearer cannot log out a new login.
5. Reset the password; old access, refresh and retry-receipt restoration must fail.
6. Simulate trusted/untrusted XFF, Redis outage, oversized/chunked input and
   duplicate headers. Verify safe status/request IDs/cache headers without secrets.
7. Validate HTTPS edge configuration, shared Redis/noeviction, representative
   Argon2 cost and actual email inbox receipt in the later protected-environment
   check. No deployment is authorized by this milestone.

Migrations remain at `0003_registration_flow`; metadata/migration files are
unchanged. Offline migration checks do not prove live PostgreSQL metadata parity.
