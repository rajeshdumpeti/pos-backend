# Environment variables

`app/config.py` is the only runtime environment reader. It creates a new,
immutable settings object for each `load_settings()` call; settings are not
cached globally. `create_app()` accepts explicit settings for tests or loads a
fresh settings object during composition.

The module uses existing Pydantic secret types for masked representations but
does not load `.env` files itself. Uvicorn loads the optional local file before
application import.

## Precedence

1. A mapping explicitly passed to `load_settings()` is used in isolation. This
   is the test-safe override path.
2. Otherwise, configuration is read from the process environment.
3. The documented Uvicorn `--env-file .env` option populates missing process
   variables from the ignored local file.
4. Missing local values use the safe defaults documented below.

## Supported environments

`APP_ENV` accepts `local`, `development`, `test`, `staging`, or `production`.
Names are trimmed and normalized to lowercase. Empty values default to `local`;
other values fail at application creation with an actionable error.

- **Local:** developer-owned configuration and the in-memory mock service.
- **Development:** shared integration represented by `develop`.
- **Test:** isolated automated or local validation.
- **Staging:** future release-candidate runtime; no deployment exists yet.
- **Production:** future production runtime; no deployment exists yet.

PostgreSQL, Redis, and JWT remain optional in every current context because no
integration consumes them. A future production milestone must define required
credentials and inject them from an approved secret manager.

## Local `.env`

Copy `.env.example` to an ignored `.env` file when local overrides are needed:

```bash
cp .env.example .env
python -m uvicorn app.main:app --reload --port 8000 --env-file .env
```

On Windows PowerShell, create the file with
`Copy-Item .env.example .env`. When no overrides are needed, `.env` is optional
and the server may be started without `--env-file .env`.

## Variables and validation

| Variable       | Local default | Current requirement | Validation |
| -------------- | ------------- | ------------------- | ---------- |
| `APP_ENV`      | `local`       | Optional            | Supported environment name |
| `CORS_ORIGINS` | `*`           | Optional locally    | Comma-separated origin-only HTTP(S) URLs |
| `DATABASE_URL` | unset         | Optional            | PostgreSQL URL when supplied |
| `REDIS_URL`    | unset         | Optional            | Redis or TLS Redis URL when supplied |
| `JWT_SECRET`   | unset         | Optional            | At least 32 characters when supplied |

Whitespace and empty CORS entries are ignored, and duplicate origins are
removed while preserving order. `*` cannot be combined with explicit origins.
The wildcard remains the intentional local, development, and test default;
staging and production require explicit origins. CORS credentials remain
disabled, so unrestricted origins are never combined with credentialed browser
requests.

## Secret safety

Database URLs, Redis URLs, and JWT secrets use masked secret values. They are
excluded from settings `repr`/string output, masked during settings
serialization, and never exposed by HTTP or OpenAPI. Configuration errors name
the affected variable but never repeat a supplied sensitive value.

Do not print, log, serialize to an API response, or commit configuration values.
Local secrets belong only in ignored environment files or the developer's
process environment. Empty example values are intentional and must not be
replaced with fake production credentials.

See the repository [README](../../README.md) for setup and validation, and
[secrets management](secrets-management.md) for handling and incident rules.
