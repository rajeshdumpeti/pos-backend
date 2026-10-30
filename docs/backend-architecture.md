# Backend architecture foundation

POS Backend is currently an optional local mock API. This structure creates clear
boundaries for responsibilities that exist today without introducing the deferred
production database, authentication, Redis, or deployment architecture.

## Composition

`app/main.py` is the single application composition point. `create_app()` selects
the current in-memory repository and unit of work by default, creates the
application service, registers the API router, applies centralized CORS
configuration, and returns an isolated FastAPI application. Tests and future
approved composition may inject a unit-of-work factory explicitly. The
module-level `app` remains the ASGI entry point used by Uvicorn and CI.

## Layers

### API — `app/api`

Owns FastAPI routers, HTTP request/response schemas, status codes, and translation
from domain errors to HTTP errors. `app/api/router.py` is the central route
registry. Future routes belong in `app/api/routes`, and future Pydantic HTTP
contracts belong in `app/api/schemas.py` or a focused schema package when needed.

Route handlers may validate HTTP input, call one application use case, and map its
result or errors to HTTP. They must not read environment variables, construct
infrastructure, own mutable stores, or contain substantial business logic.
Shared exception handlers translate domain and validation failures into the
typed contract defined in [`api-conventions.md`](api-conventions.md).

### Application — `app/application`

Owns use-case orchestration and the persistence capabilities those use cases
require. The current `MockResourceService` coordinates repository reads and
writes inside an application-facing unit of work around the framework-independent
resource rules. Future use cases belong here when a route needs behavior beyond
direct delivery. Application services depend on contracts, never concrete
infrastructure.

### Domain — `app/domain`

Owns framework-independent business concepts and rules. The current domain
contains the resource collection rules and meaningful errors used by the local
mock behavior. Domain code must not import FastAPI, configuration, SQLAlchemy,
Redis, or infrastructure.

### Infrastructure — `app/infrastructure`

Owns concrete integrations selected during composition. The default integration
is the in-memory repository, unit of work, and local demo seed data. This layer
also owns the SQLAlchemy engine/session foundation and controlled PostgreSQL
connectivity probe; neither is selected or connected automatically. See
[`data-access.md`](data-access.md) for lifecycle and transaction rules.

### Core and configuration — `app/core` and `app/config.py`

`app/config.py` remains the only environment-reading module. `app/core` contains
central framework concerns; currently it applies the shared CORS and request-ID
policies. Business rules and infrastructure construction do not belong in either
location.

## Dependency direction

```text
API → application → domain
          ↓ contracts
     repository / unit of work
          ↑
     infrastructure adapters

main composition → API + application + infrastructure + core/configuration
```

- Domain depends only on Python-level concepts.
- Application depends on domain and defines repository/unit-of-work capabilities
  needed by current use cases.
- Infrastructure satisfies those capabilities without being imported by domain.
- API depends on FastAPI plus application/domain contracts.
- Only composition connects configuration, framework setup, and infrastructure.

These boundaries describe the local mock service and data-access lifecycle, not
a final production API or database design. The canonical policy for future
PostgreSQL models, constraints, indexes, ownership, and sensitive columns is in
[`relational-standards.md`](relational-standards.md). Milestone 09A defines that
policy without changing runtime behavior. Milestone 10A establishes central
SQLAlchemy metadata and Alembic; Milestone 07-02 applies the standards through
[`authentication-persistence.md`](authentication-persistence.md). The resulting metadata ownership, migration graph,
and offline validation workflow are defined in
[`migrations.md`](migrations.md).

Email sign-in, JWT validation, refresh rotation/retry, and logout are orchestrated
by the application layer and documented in
[`email-sign-in-session-lifecycle.md`](email-sign-in-session-lifecycle.md).
Password-reset orchestration remains in the application layer; SQLAlchemy
locking and outbox state transitions remain infrastructure concerns. The
provider-neutral worker decrypts only claimed messages at the delivery boundary.
See [`password-recovery-and-email-delivery.md`](password-recovery-and-email-delivery.md).

Milestone 2.B adds the domain-owned planning vertical slice and read-only Today
projection through the same ports and Unit-of-Work direction. See
[today-dashboard.md](today-dashboard.md).

## Plan v1 selected-date projection

[Plan v1](plan-v1.md) adds a thin selected-date route and application read service
over the existing planning repository/UoW. Pure shared selectors preserve frozen
Today behavior while Plan classifies ongoing intervals explicitly. Full-date facts
are materialized once; summary/insight are independent of bounded cursor pages.
No new persistence model or migration is introduced.

## Background jobs

[Milestone 16](background-jobs.md) adds framework-independent job intent and
application ports, PostgreSQL persistence and a deliberate bounded worker CLI.
It is not composed into API startup and introduces no public job administration
or creation endpoints. Domain producers, Notification Inbox and push delivery
remain separate capabilities.
