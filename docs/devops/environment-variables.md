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

PostgreSQL and registration keys remain optional for mock-only local startup.
Email registration is composed only when the database, both keys, and current
legal versions are all configured. Email sessions are composed only when the
database, JWT signing secret, and refresh-receipt encryption key are configured.
Password recovery is composed only when the database, challenge/outbox keys, and
reset URL are configured. Authentication email delivery defaults to local,
network-free inspection and requires explicit Resend configuration to send.
Staging and production require the complete registration and session set. Redis
remains unused and optional.

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
| `AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY` | unset | Required for sessions/protected environments | Separate URL-safe base64 256-bit key |
| `AUTH_REFRESH_RECEIPT_ENCRYPTION_KEY_ID` / `_VERSION` | named ID / `1` | Optional metadata | ID 1–64 characters; version positive |
| `AUTH_CHALLENGE_DIGEST_KEY` | unset | Required for registration/protected environments | URL-safe base64 256-bit key |
| `AUTH_CHALLENGE_DIGEST_KEY_ID` / `_VERSION` | named ID / `1` | Optional metadata | ID 1–64 characters; version positive |
| `AUTH_OUTBOX_ENCRYPTION_KEY` | unset | Required for registration/protected environments | Separate URL-safe base64 256-bit key |
| `AUTH_OUTBOX_ENCRYPTION_KEY_ID` / `_VERSION` | named ID / `1` | Optional metadata | ID 1–64 characters; version positive |
| `AUTH_VERIFICATION_CODE_LIFETIME_SECONDS` | `600` | Frozen | Exactly 600 |
| `AUTH_VERIFICATION_MAX_ATTEMPTS` | `5` | Frozen | Exactly 5 |
| `AUTH_RESEND_COOLDOWN_SECONDS` | `60` | Frozen | Exactly 60 |
| `AUTH_VERIFICATION_FLOW_LIFETIME_SECONDS` | `1800` | Optional tuning | At least code lifetime |
| `AUTH_ARGON2_TIME_COST` | `3` | Optional tuning | At least 2 |
| `AUTH_ARGON2_MEMORY_COST_KIB` | `65536` | Optional tuning | At least 19456 |
| `AUTH_ARGON2_PARALLELISM` | `4` | Optional tuning | Positive integer |
| `CURRENT_TERMS_VERSION` / `CURRENT_PRIVACY_VERSION` | unset | Required for registration/protected environments | 1–64 characters |
| `AUTH_LOCAL_EMAIL_INSPECTION_ENABLED` | `false` | Local/development only | Explicit boolean |
| `AUTH_EMAIL_PROVIDER` | `local` | Optional | `local` or `resend` |
| `RESEND_API_KEY` | unset | Required only for `resend` | Secret value; never represented |
| `AUTH_EMAIL_FROM` | unset | Required only for `resend` | Protected environments reject the `resend.dev` test domain |
| `AUTH_PASSWORD_RESET_URL` | unset | Required to compose password recovery | HTTP(S), `pos://`, or `pos-dev://` locally; HTTPS only in protected environments; no credentials, fragment, or decoded `token` query field |

Whitespace and empty CORS entries are ignored, and duplicate origins are
removed while preserving order. `*` cannot be combined with explicit origins.
The wildcard remains the intentional local, development, and test default;
staging and production require explicit origins. CORS credentials remain
disabled, so unrestricted origins are never combined with credentialed browser
requests.

## Secret safety

Database URLs, Redis URLs, JWT secrets, provider API keys, authentication digest keys, and outbox
encryption keys use masked secret values. They are
excluded from settings `repr`/string output, masked during settings
serialization, and never exposed by HTTP or OpenAPI. Configuration errors name
the affected variable but never repeat a supplied sensitive value.

Do not print, log, serialize to an API response, or commit configuration values.
Local secrets belong only in ignored environment files or the developer's
process environment. Empty example values are intentional and must not be
replaced with fake production credentials.

See the repository [README](../../README.md) for setup and validation, and
[secrets management](secrets-management.md) for handling and incident rules.


## Authentication security (07-08)

The [canonical security policy](../authentication-security-hardening.md) specifies
all configurable operation thresholds and bounds. New settings:
AUTH_RATE_LIMIT_BACKEND (memory locally, redis required in staging/production),
AUTH_RATE_LIMIT_KEY (dedicated secret), AUTH_RATE_LIMIT_GLOBAL (10000/minute),
AUTH_RATE_LIMIT_MAX_ENTRIES (100000), AUTH_REDIS_TIMEOUT_MS (200),
AUTH_TRUSTED_PROXY_NETWORKS (empty), AUTH_PUBLIC_ORIGIN (HTTPS in protected mode),
AUTH_MAX_BODY_BYTES (16384), AUTH_MAX_AUTHORIZATION_BYTES (4096), and
AUTH_LIMIT_<OPERATION>_{SOURCE,IDENTITY,WINDOW_SECONDS}. REDIS_URL remains secret;
query options/fragment are forbidden. Zero JWT skew remains frozen.
Protected mode also requires a real email provider and reset destination.

Run Uvicorn with --no-proxy-headers --no-access-log --no-server-header so the
application observes the actual immediate peer and secrets do not enter URL logs.
Configure trusted proxies to append the actual peer or overwrite XFF. Do not use
an ordinary development Redis database for tests. The optional integration guard
requires POS_TEST_REDIS_URL targeting a disposable local database 15 and
POS_TEST_REDIS_DISPOSABLE=1; tests delete only their unique namespace keys.
