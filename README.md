# POS Backend

POS Backend is the optional local FastAPI mock service for the POS UI-first MVP.
It uses an in-memory store and is not the final production API or database
architecture.

## Stack

- Python 3.11 or newer
- FastAPI
- Pydantic
- Uvicorn
- pytest and HTTPX

## Local setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The virtual environment is local and ignored by Git.

## Run the server

```bash
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

The API documentation is available at `http://127.0.0.1:8000/docs`. The health
endpoint is `GET /health`.

## Tests

```bash
.venv/bin/python -m pytest
```

All mutations are held in memory and reset when the server restarts. The
development CORS policy must not be treated as production configuration.

## Architecture

The current API is organized into concrete delivery, application, domain,
infrastructure, and core boundaries. See
[`docs/backend-architecture.md`](docs/backend-architecture.md) for responsibilities,
dependency direction, and placement rules for future approved work.
