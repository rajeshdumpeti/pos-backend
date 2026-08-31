# POS Backend relational standards

This document is the canonical relational-policy source for POS Backend. It
defines the minimum PostgreSQL and SQLAlchemy schema conventions established by
Milestone 09A. It does not create a schema, SQLAlchemy model, metadata object,
database connection, or migration.

Milestone 10A encodes the approved naming convention in central SQLAlchemy
metadata, configures Alembic, and establishes migration validation. Milestone 07-02
applies these standards in
[`authentication-persistence.md`](authentication-persistence.md). Later product-domain
schemas must follow the same rules and their own approved domain specifications.

## Scope and authority

These standards refine the existing dependency direction and transaction rules:

```text
API -> application use case -> repository/unit-of-work contracts
                                  ^
                       infrastructure implementations

domain -> Python concepts only
composition -> selects concrete infrastructure explicitly
```

The application layer owns logical operations. Infrastructure owns SQLAlchemy
and PostgreSQL mechanics. Domain code does not depend on persistence types.
Where a future schema needs to depart from this document, the owning milestone
must record the reason and obtain architecture review before implementation.

## 1. Identifier strategy

- Internal primary keys use application-generated UUIDv4 values by default.
- Python represents identifiers as `uuid.UUID`.
- PostgreSQL stores them using native `UUID` columns.
- JSON and OpenAPI serialize them as lowercase canonical UUID strings with
  hyphens.
- A foreign-key column uses the same type as the referenced primary key.
- Generate an identifier in the application before persistence when an aggregate
  must be constructed transactionally, tested without a database, or referenced
  by future offline/synchronization work.
- Database-generated sequential integers must not be exposed accidentally as
  public resource identifiers. A table that needs an internal integer for a
  measured storage reason must also have an approved opaque public identifier.
- A public identifier may reuse the UUID primary key unless a product, privacy,
  lifecycle, or rotation requirement justifies a separate value. Do not create a
  second public identifier speculatively.
- Composite primary keys are not the default. Use one only when the row is
  inherently identified by the complete key, no independent lifecycle is
  required, and repository, foreign-key, API, and migration consequences have
  been reviewed.
- Moving to UUIDv7 or another identifier strategy requires an ADR plus migration,
  index-locality, ordering, privacy, and compatibility review. Do not mix UUID
  versions without an explicit transition rule.
- Milestone 09A adds no UUID dependency; Python's standard library supplies the
  current UUIDv4 default.

## 2. Table and column naming

- Use unquoted lowercase `snake_case` identifiers.
- Table names are plural nouns, such as `user_accounts` and
  `refresh_sessions`.
- Column names are singular and descriptive.
- Primary keys are named `id` unless an approved composite key is used.
- Foreign keys use `<referenced_singular>_id`, such as `user_account_id`.
- Standard instant columns use names such as `created_at`, `updated_at`,
  `expires_at`, `consumed_at`, `revoked_at`, and `deleted_at`.
- Boolean columns describe the true state with `is_`, `has_`, or an equally
  unambiguous predicate. Avoid inverted names such as `not_enabled`.
- Hash columns end in `_hash`; keyed lookup digests end in `_digest`; encrypted
  payloads end in `_ciphertext`. Do not use those terms interchangeably.
- State classifiers use `_status`, `_state`, `_type`, or `_kind` according to
  their domain meaning. Do not call every classifier `type`.
- Avoid ambiguous names such as `data`, `value`, `info`, `object`, or `metadata`
  unless a qualified name and documented schema make the content clear.
- Avoid PostgreSQL and SQL reserved words. Do not rely on quoted identifiers to
  make a reserved word usable.
- Names describe persisted meaning, not a temporary Python implementation.

## 3. Constraint and index naming

Use explicit deterministic names:

| Object | Pattern |
| --- | --- |
| Primary key | `pk_<table>` |
| Foreign key | `fk_<table>__<column>__<referenced_table>` |
| Unique constraint | `uq_<table>__<column_or_purpose>` |
| Check constraint | `ck_<table>__<purpose>` |
| Non-unique index | `ix_<table>__<column_or_purpose>` |
| Exclusion constraint | `ex_<table>__<purpose>` |

- Names are stable schema identifiers and must not depend on an ORM attribute's
  incidental name.
- A constraint purpose describes the business invariant, not an expression
  fragment such as `check_1`.
- PostgreSQL identifiers are limited to 63 bytes. Milestone 10A encodes a
  deterministic shortening rule. The approved rule is to retain a meaningful
  prefix and append `__` plus the first 10 lowercase hexadecimal characters of
  the SHA-256 digest of the full canonical name. The unshortened name remains in
  model or migration commentary for review.
- Renaming a Python attribute must not silently rename an existing production
  constraint or index. A database rename is an explicit reviewed migration.
- Exclusion constraints are allowed only for a demonstrated relational
  invariant that cannot be represented safely by a simpler constraint.

## 4. Timestamps and time zones

- Persisted instants are timezone-aware and normalized to UTC.
- PostgreSQL uses `TIMESTAMP WITH TIME ZONE` (`timestamptz`).
- Python uses timezone-aware `datetime` values. Naive values are rejected at
  persistence boundaries.
- API timestamps use RFC 3339/ISO 8601 with an explicit offset and normally the
  UTC `Z` representation.
- `created_at` records creation, `updated_at` records the latest persisted
  material change, and lifecycle instants use explicit names such as
  `expires_at`, `consumed_at`, `revoked_at`, and `deleted_at`.
- Required audit timestamps are non-null unless the timestamp represents an
  event that has not occurred.
- Application and database timestamp defaults have different failure and
  backfill behavior. The owning model and migration must state which boundary
  supplies each value.
- `updated_at` must be updated through an explicit, tested persistence rule. It
  must not rely on an undocumented ORM callback or Python-only default.
- Business-local dates and wall-clock schedules are not instants. Store them
  using domain-appropriate date/time and time-zone fields defined by the owning
  specification rather than forcing them into UTC timestamps.
- Tests use injected or controllable clocks and explicit instants. Do not use
  arbitrary sleeps to prove expiration behavior.

## 5. Nullability and defaults

- Every column declares nullability explicitly.
- `NULL` represents a real domain state such as "not yet verified" or "not
  revoked". It must not conceal an undecided requirement or failed backfill.
- Empty strings, zero values, empty UUIDs, or sentinel dates do not replace
  `NULL` without a documented domain rule.
- Application defaults support object construction. Database defaults protect
  direct/database-side insertion and migrations. When both exist, their
  equivalence and source of truth must be documented and tested.
- Mutable JSON or collection defaults are created per instance and are never
  shared Python objects.
- Required creation/audit fields cannot silently be nullable.
- Adding a non-null column to an existing table requires an explicit
  expand/backfill/enforce plan. A migration must not assume existing rows are
  absent.
- A server default introduced for a backfill must be reviewed to decide whether
  it remains part of the permanent schema.

## 6. Text, enum, numeric, JSON, and binary values

### Text

- Text length follows a product, protocol, security, or operational limit.
- Use bounded strings where a real maximum exists. Do not choose unbounded text
  for every field by default.
- Reject silent truncation. Validation and database constraints must agree.
- Free-form sensitive text receives explicit privacy, logging, search, export,
  and retention review.

### State values

- Application enums define typed domain values.
- The database enforces allowed values with a migration-safe check constraint or
  another explicitly approved strategy.
- PostgreSQL-native enums require justification because value changes and
  rollback behavior complicate migrations.
- Removing or reinterpreting a persisted value requires a data-migration and
  compatibility plan.

### Numeric values

- Money uses exact `NUMERIC`/`DECIMAL` storage with documented precision and
  scale plus an explicit ISO currency representation.
- Binary floating point is prohibited for money.
- Measurements and calculated quantities use domain-approved precision, scale,
  and units. A number without its unit or currency is not a complete contract.
- Counters and attempt values use an integer type sized for their real range and
  are protected against invalid negative values where applicable.

### JSON

- Use PostgreSQL `JSONB` only for genuinely flexible, query-justified data whose
  structure is documented and validated at the application boundary.
- JSONB must not replace normalized relationships, state columns, constraints,
  or frequently queried attributes.
- Generic metadata must not contain credentials, tokens, codes, passwords,
  private keys, or unclassified sensitive content.
- Index a JSON path only after a real query contract and plan review justify the
  write/storage cost.

### Binary, hashes, digests, and ciphertext

- Choose binary or encoded-text storage deliberately and document encoding,
  algorithm/version metadata, comparison behavior, and rotation implications.
- A password hash, lookup digest, and encrypted ciphertext are different
  security representations and use distinct column names.
- Do not place cryptographic material in generic text or JSON columns.

## 7. Email and normalized values

- Preserve an entered/display representation when the product needs to display
  the address back to the user.
- Store a separate deterministic normalized comparison representation.
- Enforce uniqueness against the normalized representation, not display text.
- POS email comparison is case-insensitive after validation and Unicode/domain
  normalization defined by the authentication implementation.
- Do not remove Gmail dots, strip plus tags, or apply provider-specific mailbox
  rewriting.
- Provider email is profile data and must not be used as the identity key for an
  external provider. Provider plus provider subject forms that identity.
- Normalization must be implemented once behind an approved application/domain
  boundary and covered by compatibility, Unicode, and collision tests before
  authentication persistence.
- Milestone 09A defines policy only and does not implement normalization.

Other normalized values follow the same pattern: retain the user-meaningful
representation when necessary, derive one deterministic comparison value, and
enforce invariants on the comparison value.

## 8. Foreign keys and relationships

- Every foreign key names an explicit referenced table and column.
- Foreign-key column type and nullability match the intended relationship.
- `ON DELETE` behavior is selected deliberately per relationship. `CASCADE`,
  `RESTRICT`, `SET NULL`, and `NO ACTION` are not global defaults.
- ORM cascade settings and database actions must agree. Database behavior is the
  final integrity boundary.
- Ownership relationships define creation, reassignment, deletion, retention,
  and orphan behavior.
- Cascades must not bypass required audit, revocation, outbox, or retention work.
- Circular foreign-key dependencies require architecture and migration review.
- Deferrable constraints require a documented transactional need and focused
  tests.
- Cross-module foreign keys respect modular-monolith ownership. A module must
  not mutate another module's tables merely because a foreign key exists.
- Prefer stable identifiers and application coordination when a direct
  cross-module relational dependency would violate ownership.

## 9. Deletion and retention

- Hard deletion, recoverable logical deletion, deactivation, anonymization,
  expiration, and legal/security retention are distinct lifecycle behaviors.
- Do not add a generic `is_deleted` column to every table.
- Use `deleted_at` only when an approved lifecycle requires recoverable logical
  deletion or a deletion-pending state.
- Account deletion must not leave orphaned sessions, provider identities,
  password material, verification/reset material, or undeliverable outbox work.
- Expired authentication secret material is cleaned according to its approved
  expiry and security-retention policy.
- Database cascades cannot replace application-level audit, revocation,
  external-provider cleanup, or user-visible deletion status.
- Legal, fraud-prevention, incident, backup, and security-event retention must be
  approved before production. Until then, schemas must not claim an arbitrary
  permanent retention period.
- Data that must remain for audit should be minimized or anonymized when its
  original user-identifying value is no longer required.

## 10. Index strategy

- Primary keys and unique constraints already create supporting PostgreSQL
  indexes. Do not add equivalent redundant indexes.
- Add a foreign-key index when join, ownership lookup, or parent-deletion access
  patterns justify it; PostgreSQL does not create one automatically.
- Index approved query and integrity paths, not imagined future queries.
- Composite index column order follows actual equality/range/order predicates
  and selectivity. Document the query shape it supports.
- Partial indexes require a stable predicate and a documented purpose.
- Expiration and cleanup processes need indexes that allow bounded operational
  batches without scanning whole tables.
- Secret lookup uses a safe digest, never a raw secret column.
- Every index has write-amplification and storage cost. Performance-only indexes
  require query-plan evidence in a representative environment.
- Schema reviews must check whether a new constraint already supplies the index
  required by a query.

Authentication persistence is expected to review, but not automatically create,
indexes for:

- normalized email uniqueness;
- provider plus provider-subject uniqueness;
- active verification challenge lookup;
- verification and reset digest lookup;
- refresh-token digest and session-family lookup;
- authentication-attempt window lookup;
- security-event user/request/time lookup; and
- email-outbox delivery and deduplication lookup.

## 11. Account ownership and scoping

- Every user-owned product record has an explicit relationship to its owning
  POS account or to an aggregate whose ownership is unambiguous and enforced.
- Authentication identity and product-resource ownership are separate concepts.
  An email or provider identity does not itself grant ownership of product data.
- Authorization scope is derived from verified server-side identity, not only a
  route/body account identifier supplied by a client.
- Queries and mutations for user-owned data include the approved ownership
  boundary and fail safely across accounts.
- Cross-account access is denied by default and receives isolation tests when
  the owning feature is implemented.
- Authorization policy belongs to Milestone 08. Milestone 09A defines only the
  relational ownership invariant and implements no authorization behavior.
- Do not introduce tenant, organization, or workspace abstractions without an
  authoritative multi-tenancy requirement.

## 12. Sensitive columns

- Store passwords only as maintained-library password hashes.
- Raw email-verification codes are prohibited.
- Raw password-reset tokens are prohibited.
- Raw refresh tokens are prohibited except in the separately approved,
  short-lived encrypted refresh-retry receipt.
- Provider identity, access, and authorization artifacts are prohibited from
  ordinary persistence.
- Lookup-capable secrets use keyed digests where the threat model requires them.
- Hash, digest, encrypted value, and ciphertext columns use unambiguous names.
- Sensitive fields are excluded from public API schemas and safe object
  representations.
- Logs, exceptions, fixtures, migrations, seeds, documentation, analytics, and
  OpenAPI must not contain real secrets or reusable sample credentials.
- Key/algorithm version, rotation, expiry, and deletion implications must be
  documented before production use.
- Database encryption at rest does not make raw reusable secrets acceptable.

## 13. Transactions and isolation

- Application use cases own logical transaction boundaries.
- The unit of work owns commit, rollback, session acquisition, and session close.
- Repositories never commit or roll back independently.
- API routes do not control database transactions.
- One logical mutation normally commits once. Every failure rolls back the
  complete unit of work.
- A domain change and its required outbox/audit record commit atomically.
- External network/provider calls occur outside held database locks unless an
  explicitly reviewed invariant makes that unavoidable.
- PostgreSQL `READ COMMITTED` is the default isolation level.
- Consumable or rotating state uses row locking, a unique constraint, or an
  atomic conditional update appropriate to the invariant.
- Isolation escalation requires a demonstrated anomaly, architecture review,
  and concurrency tests.
- Transaction retries distinguish transient database failures from unsafe replay
  of a business command. A retry must preserve the operation's idempotency rule.
- Long-running work and unreliable side effects use an outbox or another
  approved post-commit boundary rather than holding a transaction open.

## 14. Concurrency standards

The implementation slice that introduces each behavior must identify its
database invariant, locking/atomic-update mechanism, deterministic conflict
result, rollback behavior, and concurrency tests.

| Behavior | Required invariant |
| --- | --- |
| Normalized identity creation | Unique normalized email; concurrent duplicate resolves to the approved privacy-safe flow |
| Verification consumption | One challenge can be consumed once |
| Verification resend | One current challenge; replacement atomically supersedes prior active challenges |
| Unverified email replacement | Identity and challenge replacement occur in one transaction |
| Password-reset consumption | One reset credential changes the password at most once |
| Provider identity creation | Unique provider plus subject; concurrent creation produces one identity/account result |
| Authentication attempts | Atomic counter/window update; no lost increments |
| Refresh rotation | Session row lock or equivalent compare-and-swap; one replacement per valid request |
| Refresh retry receipt | Exact request replay only during approved short window; mismatched reuse revokes the family |
| Email outbox | State change and message intent commit atomically; delivery deduplicates by stable intent key |

Application-only prechecks are not sufficient for uniqueness or one-time
consumption. Database-enforced invariants are required even when application
validation provides better errors.

## 15. Migration compatibility boundary

- Every production schema change must be represented through Alembic after
  Milestone 10A establishes it.
- Handwritten production schema mutation is prohibited.
- Alembic autogeneration is a draft. An engineer reviews types, nullability,
  defaults, names, data movement, locking, upgrade, and rollback/compensation.
- Destructive changes require explicit data-migration, compatibility, backup,
  and rollback or compensating plans.
- Use expand/migrate/contract when old and new application versions must coexist.
- Constraint and index names remain deterministic across environments.
- Environment-specific schema drift is prohibited.
- Ordinary CI migration validation uses an isolated test database and must not
  require production credentials.
- A downgrade is implemented when safe. When direct reversal would lose data or
  violate a later contract, the migration documents and tests a forward
  compensating strategy instead.
- Milestone 10A owns Alembic, central SQLAlchemy metadata, the empty baseline
  revision, and migration validation. Milestone 09A creates none of them.

## Authentication schema review checklist

Milestone 07-02 must apply this checklist before creating or approving any
authentication model or migration.

### Entity-by-entity review

| Proposed entity | Required review |
| --- | --- |
| `user_accounts` | UUID key; lifecycle check constraint; explicit password-hash nullability; legal-version/timestamp rules; no public exposure of the hash; activation/deletion concurrency |
| `email_identities` | Matching UUID foreign key; unique normalized email; separate display value; verification timestamps; deliberate account-delete behavior |
| `external_provider_identities` | Unique provider plus subject; provider email not identity key; no raw provider tokens; collision and delete/revocation lifecycle |
| `email_verification_challenges` | Flow/code digests only; attempt/check constraints; expiry/consume/supersede timestamps; single-current-challenge invariant; cleanup index |
| `password_reset_challenges` | Token digest only; single-use consumption; expiry; account/session-revocation transaction; cleanup |
| `refresh_sessions` | Token digest and family lookups; expiry/revocation; Remember Me duration; rotation lock; no raw token |
| Refresh retry receipts | Separate short expiry; ciphertext and key version; exact request binding; cleanup; no ordinary raw-token storage |
| `authentication_attempt_states` | Safe identity/network digests; atomic counters; window and lock timestamps; minimized retention |
| `security_events` | Append-only intent; request/user/time lookup; sanitized metadata; retention/privacy classification; no sensitive payload |
| Authentication email outbox | Atomic creation with source state; stable deduplication key; bounded delivery state; message intent without raw reusable secrets where avoidable |

### Approval checklist

- [ ] Every primary key follows the UUID strategy or records an approved exception.
- [ ] Every foreign key has the same type as its referenced key and an explicit
      delete action.
- [ ] Every column declares nullability and each nullable value has a domain
      meaning.
- [ ] Unique email and provider-identity invariants are database-enforced.
- [ ] Status, attempts, and other bounded states have explicit check constraints
      or an approved migration-safe equivalent.
- [ ] Every persisted instant is timezone-aware; expiry and cleanup semantics are
      explicit.
- [ ] Passwords, codes, reset tokens, refresh tokens, provider artifacts, and
      retry receipts use their approved hash/digest/ciphertext representation.
- [ ] No raw reusable secret is stored outside the approved short-lived encrypted
      refresh-retry receipt.
- [ ] Required indexes support real lookup, expiry, ownership, and cleanup paths
      without duplicating primary/unique indexes.
- [ ] Ownership and lifecycle relationships cover activation, replacement,
      revocation, deactivation, deletion, and orphan prevention.
- [ ] Delete and retention behavior does not silently bypass audit, provider
      revocation, session revocation, or outbox processing.
- [ ] Each use case defines one transaction boundary and repositories do not
      commit.
- [ ] Consumable and rotating rows have an explicit row-lock or atomic-update
      strategy.
- [ ] Outbox creation is atomic with the state change and delivery has a stable
      deduplication rule.
- [ ] Migration upgrade, data/backfill, compatibility, and downgrade or
      compensation have been reviewed.
- [ ] Concurrency tests cover duplicate identity, consume/resend races, reset,
      provider creation, attempt increments, refresh rotation, and outbox
      deduplication as applicable.
- [ ] Public API schemas and logs exclude every sensitive column.
- [ ] Expired challenges, retry receipts, sessions, attempt state, events, and
      outbox records have a bounded cleanup or approved retention strategy.
- [ ] Security/audit fields are minimized, classified, and correlated with safe
      request identifiers.

## Review expectations

Every future model/migration PR must state:

1. The aggregate and module that own each table.
2. Identifier, naming, type, nullability, and timestamp decisions.
3. Constraints and the business invariant each enforces.
4. Indexes and the query/cleanup path each supports.
5. Transaction and concurrency strategy.
6. Sensitive-data classification and prohibited representations.
7. Deletion, cleanup, retention, and ownership behavior.
8. Migration compatibility and rollback/compensation behavior.
9. Tests proving constraints, rollback, concurrency, and secret safety.

Policy approval does not prove a future schema is correct. The implementing
milestone remains responsible for applying these standards to concrete,
approved requirements and for validating them against PostgreSQL.
