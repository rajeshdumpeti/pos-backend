# Backend architecture foundation

POS Backend is currently an optional local mock API. This structure creates clear
boundaries for responsibilities that exist today without introducing the deferred
production database, authentication, Redis, or deployment architecture.

## Composition

`app/main.py` is the single application composition point. `create_app()` selects
the current in-memory infrastructure, creates the application service, registers
the API router, applies centralized CORS configuration, and returns an isolated
FastAPI application. The module-level `app` remains the ASGI entry point used by
Uvicorn and CI.

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

Owns use-case orchestration and the storage capabilities those use cases require.
The current `MockResourceService` coordinates storage reads and writes around the
framework-independent resource rules. Future use cases belong here when a route
needs behavior beyond direct delivery.

### Domain — `app/domain`

Owns framework-independent business concepts and rules. The current domain
contains the resource collection rules and meaningful errors used by the local
mock behavior. Domain code must not import FastAPI, configuration, SQLAlchemy,
Redis, or infrastructure.

### Infrastructure — `app/infrastructure`

Owns concrete integrations selected during composition. The current integration
is the in-memory resource store and local demo seed data. Future approved external
integrations belong here, but their introduction requires a separate milestone.

### Core and configuration — `app/core` and `app/config.py`

`app/config.py` remains the only environment-reading module. `app/core` contains
central framework concerns; currently it applies the shared CORS and request-ID
policies. Business rules and infrastructure construction do not belong in either
location.

## Dependency direction

```text
API → application → domain
              ↑
     infrastructure adapter

main composition → API + application + infrastructure + core/configuration
```

- Domain depends only on Python-level concepts.
- Application depends on domain and defines capabilities needed by current use
  cases.
- Infrastructure satisfies those capabilities without being imported by domain.
- API depends on FastAPI plus application/domain contracts.
- Only composition connects configuration, framework setup, and infrastructure.

These boundaries describe the local mock service, not a final production API or
database design. Add no speculative repository, service, or base-class hierarchy
until an approved feature has a concrete responsibility for it.
