# POS Backend migrations

Alembic is the only approved mechanism for versioning production PostgreSQL
schema changes. This guide describes the Milestone 10A migration foundation; it
does not authorize a live database connection or define an authentication or
product schema.

## Ownership and structure

- `app/infrastructure/relational.py` owns the canonical SQLAlchemy
  `RelationalBase`, metadata, naming convention, and deterministic
  PostgreSQL-name shortening helper.
- `migrations/env.py` is the Alembic execution boundary.
- `migrations/script.py.mako` is the revision template.
- `migrations/versions/` contains the linear reviewed revision history.
- `alembic.ini` contains paths and logging only. It contains no database URL.

Importing the metadata registers models but never constructs an engine, imports
FastAPI, or opens a connection. The metadata is intentionally empty until an
approved persistence milestone introduces its models.

## Baseline and single-head policy

`0001_migration_foundation` is the baseline and has no parent. Its upgrade
and downgrade are explicit no-ops. It represents the point before authentication
or product tables exist.

Revision `0002_auth_persistence` adds the authentication persistence schema;
its scope is documented in
[`authentication-persistence.md`](authentication-persistence.md). The normal
revision graph has exactly one head. A multiple-head result blocks
review unless a deliberate branch/merge migration has prior architecture
approval. Do not edit a revision that has been applied outside an isolated local
environment; create a new corrective revision.

Revision `0003_registration_flow` adds only the
flow lifecycle/key metadata and deferred challenge-supersession support required
by email registration. See
[`email-registration-verification.md`](email-registration-verification.md).

Revision `0004_auth_contract_v1` adds a unique
constraint on `refresh_rotation_retry_receipts.refresh_request_id`, binding one
logical refresh key to one request payload under concurrency. Before upgrading a
database containing pre-release traffic, verify that no duplicate request IDs
exist. The migration fails safely rather than choosing a receipt to delete.

Revision `0005_authoritative_onboarding` precedes daily planning. It creates
one account-owned onboarding state, strict required-preference checks, typed
optional JSONB containers, optimistic versioning, timestamps, and cascade
deletion. A completed-state CHECK requires non-empty priorities and confirmed
supported unit and currency values whenever `completed_at` is non-null. It
performs no legacy-account backfill; absence remains incomplete.
See [`onboarding.md`](onboarding.md).

Inspect the graph without connecting to a database:

```bash
.venv/bin/alembic history
.venv/bin/alembic heads
```

## Safe offline validation

Offline mode uses the PostgreSQL dialect directly. It does not load
`DATABASE_URL`, construct an engine, or open a connection:

```bash
.venv/bin/alembic upgrade head --sql
.venv/bin/alembic downgrade 0001_migration_foundation:base --sql
```

Generated SQL can include Alembic's own version table and revision bookkeeping.
The empty baseline contains no POS table, index, constraint, or seed operation.
Automated tests validate the revision graph, metadata identity, no-op baseline,
naming convention, and offline generation without depending on full SQL text.

## Creating a future revision

Only an approved schema milestone may create a revision. After its SQLAlchemy
models and repository contract are reviewed, generate a draft with:

```bash
.venv/bin/alembic revision --autogenerate -m "describe the schema change"
```

Autogeneration requires an authorized configured database and is never a final
migration. Review every generated type, nullability rule, server default,
constraint/index name, foreign-key action, data movement, lock risk, and
upgrade/downgrade operation. Remove accidental operations and add required data
or compatibility steps deliberately.

Revision files use descriptive messages and stable revision identifiers. They
must not contain passwords, URLs with credentials, API keys, tokens, private
user data, environment-specific values, or production seed data. Handwritten
schema mutation outside Alembic is prohibited.

## Upgrade, downgrade, and compatibility

- Upgrade behavior must be deterministic and safe for the supported prior
  revision.
- Implement downgrade when reversal is safe and does not destroy data required
  by a later contract.
- When direct downgrade is unsafe, document and test a forward compensating
  strategy.
- Destructive and compatibility-sensitive changes use
  expand/migrate/contract where old and new application versions may coexist.
- Naming conventions remain stable. Renaming a Python attribute must not
  silently rename an existing database object.

## Future online local workflow

Online mode obtains `DATABASE_URL` only through `app/config.py`, passes its
masked `SecretStr` to the existing engine boundary, and disposes the engine
after migration. Missing configuration fails with the variable name but no
value. Alembic never reads `.env` itself; an operator may populate the process
environment through the documented local tooling after authorization.

Do not run an online command until the previously exposed local PostgreSQL
password has been rotated and the operator confirms that the URL targets an
authorized local test database. Then the separately approved workflow will
include connectivity verification, current/head inspection, upgrade, downgrade
or compensation validation, and restoration rehearsal. Production credentials
are never required for ordinary CI.

## Milestone boundaries

- Milestone 09A defines relational identifiers, names, types, constraints,
  indexes, ownership, sensitivity, transactions, concurrency, and compatibility
  policy in [`relational-standards.md`](relational-standards.md).
- Milestone 10A encodes metadata and migration mechanics and supplies the empty
  baseline.
- Milestone 07-02 introduces authentication persistence through reviewed
  revision `0002_auth_persistence` after authorized live local validation.
- Milestone 07-03 extends that schema through focused revision
  `0003_registration_flow`; it adds no table or seed data.
- Milestone 07-09 extends it through `0004_auth_contract_v1`; it adds only the
  refresh-receipt request-ID uniqueness constraint and seeds no data.
- Milestone 2.A extends it through `0005_authoritative_onboarding`; it adds the
  onboarding state table and seeds or backfills no account data.

The baseline creates no product schema. Revision `0002_auth_persistence` creates
authentication persistence only. Revision `0003_registration_flow` adds no
product schema or seed data; runtime behavior remains owned by application and
infrastructure composition. Revision `0004_auth_contract_v1` changes no product
schema and deletes no data. Revision `0005_authoritative_onboarding` adds the
reversible onboarding table without modifying existing account rows.

Revision `0006_daily_planning` is the single current head. It adds account-owned
plan items and atomic creation receipts, with no seeds/backfills or changes to
0004/0005. See [Today architecture](today-dashboard.md) for constraints, indexes,
transaction rules and the guarded reversible validation sequence.

Milestone 2.C selected-date [Plan v1](plan-v1.md) makes no schema change. The
existing account/date/start index supports its equality-filtered full-date facts;
global sorting is application-owned. Head and model metadata remain unchanged at
0006_daily_planning, with existing offline parity and guarded live parity checks.
