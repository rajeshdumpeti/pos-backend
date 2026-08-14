# Environment variables

POS Backend reads configuration through `app/config.py`. The module uses only
the Python standard library and does not load `.env` files automatically. Real
environment files remain local and ignored; `.env.example` contains names and
safe empty placeholders only.

## Supported environments

### Local

Copy `.env.example` to an ignored `.env` file when local overrides are needed,
then load it with Uvicorn's documented `--env-file` option:

```bash
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000 --env-file .env
```

On Windows PowerShell, create the file with
`Copy-Item .env.example .env`. The server command is otherwise the same. When no
local overrides are needed, `.env` is optional and the server may be started
without `--env-file .env`.

The current in-memory mock API needs no database, Redis, or JWT secret. Its
existing local permissive CORS behavior remains the default when `CORS_ORIGINS`
is empty. See the repository [README](../../README.md) for the complete setup and
validation workflow.

### Development

Development configuration will be scoped to the future approved development
runtime. Values must be injected by that runtime rather than committed. GitHub
Environment Secrets may later supply CI-only credentials after separate
approval.

### Staging

Staging configuration must be isolated from development and production. Future
hosting credentials belong in the hosting platform's secret manager. No staging
URLs or credentials are stored in this repository.

### Production

Production configuration and credentials must use the future hosting platform's
secret manager with least-privilege access and an approved rotation process. No
production values are stored in this repository.

## Variable classification

- `APP_ENV` identifies the runtime environment and is non-secret configuration.
- `CORS_ORIGINS` is a comma-separated allowlist and is normally non-secret, but
  it must still be environment-specific and reviewed.
- `DATABASE_URL` is secret when it contains database credentials.
- `REDIS_URL` is secret when it contains Redis credentials.
- `JWT_SECRET` is always a secret and must never be exposed to a client bundle.

The optional database, Redis, and JWT values are not required by the current
local mock API. Empty values become `None`; they are never replaced by fake
credentials. Configuration and secret values must never be logged.

Local secrets live only in ignored environment files or the developer's local
process environment. Future GitHub Environment Secrets are for approved CI
needs, while deployed runtime secrets belong in the selected hosting platform's
secret manager. GitHub, hosting, and EAS configuration remain outside the scope
of this foundation.
