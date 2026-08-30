# POS Backend data-access foundation

This document is authoritative for the Milestone 06 persistence boundary. It
defines engine, session, and transaction ownership without defining a product
schema. The local mock API remains the default application mode and requires no
database.

## Dependency direction

```text
API → application use case → repository/unit-of-work contracts
                               ↑
                    infrastructure implementations

domain → Python concepts only
composition → selects concrete infrastructure explicitly
```

The application layer owns `ResourceRepository`, `ResourceUnitOfWork`, and the
unit-of-work factory contract. Application services use those contracts and do
not import the in-memory adapter, SQLAlchemy, Psycopg, or runtime configuration.
The domain remains independent of persistence and HTTP frameworks.

`ResourceRepository` is deliberately limited to the generic mock-resource
operations that exist today. It is not a generic enterprise repository and is
not a proposed product-schema design. A future SQL repository can satisfy the
contract once an approved product model exists.

## Transaction ownership

One application-service operation receives one fresh unit of work. The use case
decides when its logical mutation has succeeded and calls `commit()` exactly
once. The infrastructure unit of work owns session acquisition, transaction
mechanics, rollback, and closure. Repositories and API routes never commit or
roll back independently.

The lifecycle is:

1. Enter a unit of work and acquire one isolated repository/session.
2. Execute the application operation.
3. Commit once after a successful mutation.
4. Roll back when the operation or commit fails.
5. Close the session in every outcome.

Read operations do not commit. Closing their SQLAlchemy session releases any
uncommitted transaction. The in-memory unit of work snapshots its state and
restores it on failure or when code exits without committing, preventing an
accidental partial mutation.

## SQLAlchemy engine and sessions

`app/infrastructure/database.py` is the sole SQLAlchemy construction boundary.
`create_database_engine()` accepts the masked `SecretStr` from `app/config.py`,
unwraps it only inside infrastructure, and creates a SQLAlchemy 2.x engine. It
does not open a connection. Plain `postgresql://` URLs are normalized to the
approved `postgresql+psycopg` driver; async-driver URLs are rejected by this
synchronous foundation.

There is no module-level engine, connection, or active session. A caller that
explicitly selects database infrastructure owns the engine and must call
`dispose_database_engine()` at the end of its lifecycle. The session factory
uses `autoflush=False` and `expire_on_commit=False`; every unit of work calls it
again so sessions are never shared across operations or requests.

## Synchronous driver decision

The current FastAPI handlers, application service, and in-memory adapter are
synchronous. Milestone 06 therefore uses synchronous SQLAlchemy with Psycopg 3
and its binary installation extra. This avoids introducing async boundaries and
pooling complexity without a measured need. It is not a claim that all future
POS workloads must remain synchronous; changing the model requires a separate
architecture and load review.

Only the direct requirements are exactly pinned. The repository does not claim
to have a complete transitive dependency lock.

## Local mock and explicit composition

`create_app()` constructs a new in-memory repository and unit-of-work factory by
default. Tests or a future approved composition root may inject a different
unit-of-work factory explicitly. `DATABASE_URL` being present does not select an
adapter, create an engine, or make Swagger, health, tests, local startup, or CI
depend on PostgreSQL.

No ambiguous environment-driven adapter switch exists. Database-backed product
composition is deferred until a concrete approved repository and schema exist.

## Connectivity verification

`check_database_connectivity()` opens one engine connection, runs `SELECT 1`,
and closes the connection. It creates no database, schema, table, or migration
and does not alter `/health`. SQLAlchemy engine construction and the connectivity
check are separate, so an import or ordinary mock startup never probes a
database.

Run a live check only after an operator confirms that the configured
`DATABASE_URL` belongs to an authorized local test database. Never print the URL.
Use `load_settings()` to obtain the masked value, construct the engine, run the
check, and dispose the engine in a `finally` block. If no safe local URL is
configured, leave live verification pending; SQLite is not evidence of
PostgreSQL compatibility.

## Errors and secrets

Infrastructure translates SQLAlchemy and driver failures into the
framework-independent `PersistenceUnavailableError` and uses fixed, secret-safe
messages. SQL statements, driver text, hosts, usernames, passwords, and engine
URLs must not cross into application contracts, HTTP responses, OpenAPI, logs,
or snapshots. Unexpected persistence failures continue through the existing
typed API 500 contract and retain the request ID.

Engine string representations must keep passwords masked. SQLAlchemy is created
with parameter hiding enabled. Configuration remains centralized in
`app/config.py`; no data-access module reads environment variables directly.

## Testing strategy

Automated tests use the in-memory adapter and controlled session, engine,
connection, and repository doubles. They verify engine construction without a
connection, session isolation, commit/rollback/close behavior, connectivity
success and failure, secret masking, dependency injection, and preserved API
contracts. They require no network, Docker, PostgreSQL, cloud service, schema, or
credential. An optional live check is an explicit manual verification only.

## Deferred work

Milestone 09A now defines the policy for identifiers, naming, relational types,
integrity, indexing, ownership, sensitive columns, concurrency, and migration
compatibility in [`relational-standards.md`](relational-standards.md). It adds no
runtime schema machinery. Milestone 10A owns central SQLAlchemy metadata,
Alembic, migrations, schema-version rules, and upgrade/downgrade or compensating
verification. Milestone 07-02 will apply both foundations to authentication
persistence. This data-access foundation intentionally adds no ORM product
model, metadata, table, schema, migration, seed migration, or database creation
behavior.

Milestone 10A's central metadata and empty Alembic baseline are documented in
[`migrations.md`](migrations.md). They do not select the database adapter or
change the engine, session, transaction, connectivity, or local-mock behavior
defined here.
