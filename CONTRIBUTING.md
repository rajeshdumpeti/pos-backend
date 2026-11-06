# Contributing to POS Backend

POS Backend is the optional local FastAPI mock for the UI-first POS MVP. It is
not the final production API or database architecture.

## Branch flow

Create `feature/*` or `fix/*` branches from `develop`. Changes progress through
pull requests from `develop` to `staging`, then from `staging` to `main`.

Do not commit directly to shared protected branches once protection is enabled.

## Local validation

Use the Python 3.12 `.venv` workflow and validation commands in the
[README](README.md). The README is the source of truth for first-time setup,
daily server startup, environment readiness, and troubleshooting.

Use the ignored local virtual environment:

Keep changes focused. Do not commit virtual environments, caches, real
environment files, credentials, certificates, service-account files, or private
user data.

Use the pull-request template to document API contracts, database migrations,
environment variables, security, privacy, logging, deployment, rollback, and
documentation impacts.
