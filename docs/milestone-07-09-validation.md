# Milestone 07-09 validation report

1. **Starting state.** Work began in `pos-backend` on
   `feat/backend-auth-contract-freeze` at
   `7e6f280b97b5a9f087c0076eaf7e6e9a7567708c`. The working tree was clean,
   `origin/develop...HEAD` was `0 0`, the expected base was present, Milestone
   07-08 was the checked-out commit, and no merge, rebase, or cherry-pick was in
   progress.
2. **Baseline.** The database-independent suite passed before implementation:
   `194 passed, 31 skipped in 4.02s`.
3. **Evidence inspected.** Repository instructions and charter, all Milestone 07
   source/docs/handoffs, generated OpenAPI, routes and schemas, registration and
   verification services, challenge/session persistence, password and session
   security, refresh receipts, logout, recovery/outbox delivery, admission and
   proxy controls, centralized errors, configuration, diagnostics/redaction,
   migrations, and unit/PostgreSQL/Redis/concurrency tests were reviewed.
4. **Unverified-sign-in root cause.** The sign-in service raised the generic
   `email_not_verified` error after password verification, but no recoverable raw
   flow token crossed that boundary. The only persisted value was a digest, so a
   cold mobile client could not call confirm, resend, or change-email.
5. **Continuation design.** Correct credentials for an eligible pending account
   now rotate the current challenge's flow credential or create one bounded
   replacement through existing verification primitives. The named 403 response
   contains the transient token, masked email, flow expiry, resend availability,
   and `verify_email`; it creates no access or refresh session.
6. **Enumeration safety.** Unknown email and wrong password still execute the
   established generic path and return the same `401 invalid_credentials` shape.
   Neither path invokes continuation creation. Tests compare both failures and
   prove that no session or continuation is produced.
7. **Transactions and concurrency.** The account and current challenge are
   locked in the sign-in unit of work. Active challenges retain their code,
   timings, and outbox row while only the HMAC flow digest/key metadata rotates.
   A missing/expired flow uses atomic challenge, encrypted-outbox, security-event,
   and commit behavior. The partial unique challenge index bounds current state;
   guarded tests cover concurrent sign-in and rollback failures.
8. **Registration policy.** Public
   `GET /api/v1/auth/registration-policy` uses named
   `RegistrationPolicyResponse`, unique operation ID
   `get_authentication_registration_policy`, validated configuration values,
   request IDs, and authentication-wide no-store/no-cache headers. It requires
   neither authentication, idempotency, database, nor cryptographic composition.
9. **Legal versions.** False acceptance remains a safe validation failure.
   Version mismatch is frozen as `409 legal_acceptance_outdated`, exact message
   `Registration policy has changed`, action `refresh_registration_policy`, and
   empty details; submitted versions are not echoed.
10. **Remember Me.** `remember_me` was removed from the public sign-in schema and
    command. Extra-input rejection covers both boolean values, all sessions keep
    the established seven-day absolute lifetime, and the legacy database column
    is written `false` only for schema compatibility.
11. **Reset link.** The link is formed with URL utilities and exactly one decoded
    query field named `token`; existing safe query fields remain. Conflicting,
    encoded, or duplicate token fields, malformed query strings, credentials,
    fragments, and excessive query fields are rejected. Local/development allow
    HTTP(S), `pos://`, and `pos-dev://`; protected environments allow HTTPS only.
12. **Refresh idempotency.** One key is bound to one previous-token digest.
    Same old token/key within five minutes returns the exact encrypted result;
    same key/different material returns `409 refresh_idempotency_conflict` without
    mutation; old token/different key remains replay containment and family
    revocation. A named database constraint and safe exception translation cover
    the concurrent uniqueness race.
13. **Logout.** `DELETE /api/v1/auth/session` revokes the current session and
    `DELETE /api/v1/auth/sessions` revokes all account sessions. Confirmed server
    success is empty `204`; invalid credentials retain security-sensitive 401
    behavior. The handoff requires immediate, idempotent local cleanup even when
    offline or remote revocation is unconfirmed, and forbids refresh solely for
    logout.
14. **Onboarding boundary.** `onboarding_required` remains the only backend
    routing signal. Product/Frontend owns the concrete onboarding or home screen;
    no onboarding endpoint was added.
15. **Contract version.** Authentication Contract `1.0.0` is exposed at OpenAPI
    top level as `x-pos-authentication-contract-version` without changing the
    unrelated FastAPI product version.
16. **Endpoint inventory.** The twelve frozen method/path, success-schema, status,
    and operation-ID contracts are listed in
    [the consolidated handoff](frontend-authentication-handoff.md#endpoint-inventory).
    A contract test compares the exact generated endpoint/status map.
17. **Error/action matrix.** The canonical matrix is in
    [the consolidated handoff](frontend-authentication-handoff.md#error-and-action-matrix).
    It records exact messages, allowed actions, empty details, timing/header
    behavior, classifications, and mobile transitions. Every documented 429 has
    bounded `Retry-After`; Forgot Password suppression remains generic 202.
18. **Files created.** `docs/frontend-authentication-handoff.md`,
    `docs/milestone-07-09-validation.md`,
    `migrations/versions/0004_authentication_contract_v1.py`, and
    `tests/test_authentication_contract_v1.py`.
19. **Files modified.** Backend application/API/config/domain/infrastructure
    authentication files; README and authentication/migration/environment docs;
    the four historical frontend handoffs; and focused API, service, persistence,
    migration, PostgreSQL, hardening, and lifecycle tests. No mobile, governance,
    product, provider-authentication, authorization, or RBAC files changed.
20. **Migration.** A migration was necessary to prove the missing concurrent
    one-key/one-payload refresh invariant. Linear head `0004_auth_contract_v1`
    adds only unique constraint
    `uq_refresh_rotation_retry_receipts__refresh_request_id`; downgrade drops it.
    It seeds no data. Before adding uniqueness it deletes duplicate non-null
    refresh request-ID receipts, retaining the earliest `created_at`, then lowest
    `id`. Audit and back up pre-release data before upgrade; downgrade cannot
    restore those receipts.
21. **OpenAPI.** Generation passes. Version `1.0.0`, twelve unique authentication
    operation IDs, named policy and verification-continuation schemas, the typed
    sign-in 403 union, exact statuses, and 429 headers are covered by tests.
22. **Tests and checks.** Focused 07-09: `10 passed in 0.63s`. Focused contract,
    lifecycle, API, and migrations: `41 passed in 1.87s`. Complete suite:
    `204 passed, 36 skipped in 4.26s` (earlier repeat with skip reasons:
    `204 passed, 36 skipped in 4.28s`). Python compilation passed. `pip check`
    reported no broken requirements.
23. **PostgreSQL/Redis.** Live guarded tests were not executed because authorized
    `POS_TEST_DATABASE_URL` and explicit disposable Redis configuration were not
    available. Thirty-five PostgreSQL tests and one Redis test skipped without
    weakening guards. Live migration/metadata parity therefore remains deferred.
24. **Redaction.** Targeted changed-file scans found no credentialed service URL,
    literal Bearer credential, or literal token-bearing URL. Existing and new
    tests cover safe representations, errors, logs, masked addresses, empty auth
    validation details, and cache prevention. No `.env` value was read or printed.
25. **Documentation.** The consolidated handoff is authoritative and contains
    TypeScript-style contracts, safe placeholders, all flows, mobile state and
    accessibility rules, secure storage, offline/ambiguous behavior, fixtures,
    manual verification, acceptance, ownership, and platform prerequisites.
    Earlier handoffs are marked historical. Local documentation link validation
    found zero broken links across all 23 Markdown files.
26. **Deferred work.** Google, Apple, account linking, biometrics, MFA, passkeys,
    authorization, and RBAC remain deferred. Protected release still requires an
    approved HTTPS universal/app link, Apple/Android association files, verified
    sender/domain, guarded live PostgreSQL/Redis tests and migration parity,
    representative Argon2/load checks, and secret-safe end-to-end delivery tests.
27. **Diff stat.** The final tracked `git diff --stat` is
    `38 files changed, 791 insertions(+), 106 deletions(-)`. Three implementation
    files and this report are untracked and therefore excluded from that Git statistic.
28. **Working tree.** Final status is intentionally unstaged: 38 modified tracked
    files plus the four created files named in item 18. `git diff --check` passes.
29. **Delivery safety.** Nothing was staged, committed, pushed, merged, rebased,
    cherry-picked, deployed, or sent through live email. Git HEAD and history are
    unchanged.
30. **Final status.** `READY WITH DEFERRED LIVE CHECKS`.
