# POS Backend

POS Backend is the optional local FastAPI mock service for the POS UI-first MVP.
It uses an in-memory store and is not the final production API or database
architecture.

## Stack and requirements

- Python 3.12.x
- FastAPI, Pydantic, and email-validator
- SQLAlchemy 2.x and Psycopg 3 data-access foundation
- Argon2id password hashing, PyJWT access tokens, and AES-GCM authentication encryption
- Uvicorn
- pytest and HTTPX

Python 3.12 is the supported local and CI version. Confirm it before creating the
virtual environment:

```bash
python3.12 --version
```

On Windows, use `py -3.12 --version`. If the command is unavailable or reports a
different minor version, install Python 3.12 with your platform's Python manager
and reopen the terminal before continuing.

## First-time setup

The repository uses one local virtual environment named `.venv` and the pinned
direct dependencies in `requirements.txt`. Packaging-tool upgrades are not
required.

macOS or Linux:

```bash
cd pos-backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
cd pos-backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Both `.venv` and `.env` are local and ignored by Git. The example environment
contains only safe local values; PostgreSQL, Redis, and JWT configuration remain
optional. See [environment variables](docs/devops/environment-variables.md) for
details.

Configuration is validated when the application is created. Invalid variables
fail with the variable name and a safe correction message without echoing
sensitive values. Supported `APP_ENV` values are `local`, `development`, `test`,
`staging`, and `production`; local startup defaults to `local`.

## Daily development

Activate the existing environment, then start the development server:

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --reload --port 8000 --no-proxy-headers --no-access-log --no-server-header --env-file .env
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`; the server
command is otherwise the same. Stop the server with `Ctrl+C`. Leave the virtual
environment with:

```bash
deactivate
```

The server runs locally at `http://127.0.0.1:8000`:

- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI document: `http://127.0.0.1:8000/openapi.json`
- Health endpoint: `http://127.0.0.1:8000/health`

With the server running, verify health and an existing mock resource from a
second terminal:

```bash
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/tasks
```

When `DATABASE_URL`, both authentication keys, and the current legal document
versions are configured, the application also composes the versioned email
registration API under `/api/v1/auth`. The local verification-email workflow is
deliberately disabled by default. To inspect the latest pending encrypted
outbox message locally, explicitly enable
`AUTH_LOCAL_EMAIL_INSPECTION_ENABLED=true` in a local/development environment
and invoke:

```bash
python -m app.infrastructure.authentication.local_email inspect-latest
```

Do not paste the displayed code or reset link into logs, reports, or source files. See
[email registration and verification](docs/email-registration-verification.md)
for the security and operational contract.

Password recovery is composed independently when PostgreSQL, the existing
challenge/outbox keys, and `AUTH_PASSWORD_RESET_URL` are configured. Requests
remain enumeration-resistant and email is queued transactionally. Local mode
performs no network delivery. See
[password recovery and email delivery](docs/password-recovery-and-email-delivery.md).

## Validate the environment

Run the complete local validation from the repository root with `.venv`
activated:

```bash
python --version
python -m pytest
python -m compileall app
python -c "from app.main import app; print(app.title, app.version)"
```

The environment is ready when Python reports 3.12.x, all tests pass, compilation
completes, and metadata prints `POS Local Mock API 0.1.0`. CI runs the same test,
compilation, and import checks on Python 3.12. No separate lint, format, or static
type command is currently configured.

These commands are the readiness check for the current small service. A separate
setup script would duplicate them without adding a stable prerequisite check.

## Troubleshooting

- **`python3.12` or `py -3.12` is not found:** install Python 3.12 and ensure its
  launcher is available in a new terminal.
- **The activated environment reports another Python version:** deactivate it,
  remove only that local `.venv`, and recreate `.venv` with Python 3.12.
- **`ModuleNotFoundError` for FastAPI, pytest, or another declared package:**
  activate `.venv` and rerun `python -m pip install -r requirements.txt`.
- **PowerShell blocks activation:** follow Microsoft's documented execution-policy
  guidance for the current user, then retry `.venv\Scripts\Activate.ps1`.
- **The server cannot read `.env`:** create it from `.env.example`, or omit
  `--env-file .env` when no local overrides are needed.
- **Startup reports an invalid configuration variable:** correct the named value
  in the process environment or ignored `.env`. Error messages intentionally do
  not repeat database URLs, Redis URLs, or JWT secrets.
- **`curl` cannot connect:** confirm Uvicorn is still running and that the command
  and URL use the same port.
- **Port 8000 is already in use:** stop the other local process or choose another
  port and use that port in the verification URLs.

## Current limitations

Generic mock API mutations remain in memory and reset when the server restarts.
Email registration is composed only when its complete PostgreSQL/key configuration
and an explicit valid legal-policy mode are present; partial configuration never selects an
ambiguous adapter. Email sessions are independently composed only when
PostgreSQL, JWT signing, and refresh-receipt encryption are configured.
Application import, health, Swagger, and the mock endpoints
still require no PostgreSQL, Redis, authentication secret, or cloud service.
The development CORS policy must not be treated as production configuration.

## Architecture

The current API is organized into concrete delivery, application, domain,
infrastructure, and core boundaries. See
[backend architecture](docs/backend-architecture.md) for responsibilities,
dependency direction, and placement rules for future approved work. Shared HTTP
contracts, errors, request IDs, lifecycle, and pagination policy are defined in
[API conventions](docs/api-conventions.md). Engine, session, transaction,
connectivity, and persistence-error ownership are defined in
[data access](docs/data-access.md). Authentication persistence ownership and
operations are documented in
[authentication persistence](docs/authentication-persistence.md). Canonical PostgreSQL schema policy,
including identifiers, naming, types, constraints, indexing, ownership,
sensitive columns, and migration compatibility, is defined in
[relational standards](docs/relational-standards.md). Milestone 09A establishes
policy only; Milestone 10A encodes metadata and Alembic conventions, and
Milestone 07-02 applies them to authentication persistence.

The first public authentication API, password/email policy, verification
lifecycle, encrypted outbox, and operational diagnosis are documented in
[email registration and verification](docs/email-registration-verification.md).
The authoritative mobile integration contract and complete UX-state mapping are
in [Authentication Contract v1.1.0](docs/frontend-authentication-handoff.md).
The environment-aware registration policy and frontend branching rules are in
[the 07-03A registration handoff](docs/frontend-auth-registration-verification-handoff.md).
Earlier feature-specific frontend handoffs are retained as historical milestone evidence.
Email sign-in, rotating refresh sessions, logout, and the draft mobile contract
are documented in [session lifecycle](docs/email-sign-in-session-lifecycle.md)
and [frontend session handoff](docs/frontend-auth-session-handoff.md).
Password recovery, provider-neutral transactional email, and the mobile contract
are documented in
[password recovery and email delivery](docs/password-recovery-and-email-delivery.md)
and [frontend password-recovery handoff](docs/frontend-auth-password-recovery-handoff.md).

The implemented migration structure, offline validation commands, review rules,
and live-database safety gate are documented in
[migrations](docs/migrations.md). The baseline is intentionally empty, and
ordinary local startup and CI still require no PostgreSQL service.

## Email/password security — 07-08

See [authentication security hardening](docs/authentication-security-hardening.md)
for the canonical threat model, exact admission policy, Redis/local settings,
trusted proxies, secure server flags and guarded verification prerequisites.
The [frontend security handoff](docs/frontend-auth-security-handoff.md) defines
429 handling, privacy-safe states and session-expired navigation requirements.
Google, Apple and biometrics are deferred to a later MVP. Milestone 07-09 freezes
the email/password contract as v1.0.0; generated OpenAPI carries the same version.

## Today Dashboard Core v1

The authenticated daily planning slice and deterministic Today projection are
documented in [Today architecture](docs/today-dashboard.md) and the
[frontend handoff](docs/frontend-today-dashboard-handoff.md). Only implemented
planning actions are exposed; unsupported measurements remain unavailable.

## Plan v1 selected-date read

GET `/api/v1/plan` adds authoritative selected-date items, full-date summary,
opaque cursor pagination and deterministic Plan Insight using existing plan_items.
Plan contract 1.0 is separate from unchanged Today 1.0. See
[Plan architecture](docs/plan-v1.md) and
[frontend Plan handoff](docs/frontend-plan-v1-handoff.md).
