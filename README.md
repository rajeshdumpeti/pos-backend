# POS Backend

POS Backend is the optional local FastAPI mock service for the POS UI-first MVP.
It uses an in-memory store and is not the final production API or database
architecture.

## Stack and requirements

- Python 3.12.x
- FastAPI and Pydantic
- SQLAlchemy 2.x and Psycopg 3 data-access foundation
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
python -m uvicorn app.main:app --reload --port 8000 --env-file .env
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

All current API mutations are held in memory and reset when the server restarts.
The SQLAlchemy/Psycopg foundation is available only for explicit future
composition; `DATABASE_URL` does not select it automatically. Local startup and
CI require no PostgreSQL, Redis, migration, authentication, or cloud service.
The development CORS policy must not be treated as production configuration.

## Architecture

The current API is organized into concrete delivery, application, domain,
infrastructure, and core boundaries. See
[backend architecture](docs/backend-architecture.md) for responsibilities,
dependency direction, and placement rules for future approved work. Shared HTTP
contracts, errors, request IDs, lifecycle, and pagination policy are defined in
[API conventions](docs/api-conventions.md). Engine, session, transaction,
connectivity, and persistence-error ownership are defined in
[data access](docs/data-access.md). Canonical PostgreSQL schema policy,
including identifiers, naming, types, constraints, indexing, ownership,
sensitive columns, and migration compatibility, is defined in
[relational standards](docs/relational-standards.md). Milestone 09A establishes
policy only; Milestone 10A encodes metadata and Alembic conventions, and
Milestone 07-02 will apply them to authentication persistence.

The implemented migration structure, offline validation commands, review rules,
and live-database safety gate are documented in
[migrations](docs/migrations.md). The baseline is intentionally empty, and
ordinary local startup and CI still require no PostgreSQL service.
