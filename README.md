# NU Connect Hub

Peer discovery and team formation for verified university students: profiles, explainable collaborator recommendations, projects, messaging, video rooms and Scan to Connect QR profiles.

> **Status:** early development. The repository skeleton, the local database and the web and API scaffolds exist so far.

## Stack

- **Web:** React, TypeScript, Webpack 5, Tailwind CSS, shadcn/ui
- **API:** Python, FastAPI, python-socketio
- **Data:** PostgreSQL with pgvector, SQLAlchemy 2, Alembic
- **Matching:** Sentence Transformers and scikit-learn, inside the API

## Local development

Requirements: Docker, Node.js 22 (see `.nvmrc`) and Python 3.12 (see `.python-version`).

Start the database from the repository root:

```sh
docker compose -f infra/compose/compose.yaml up -d postgres
docker compose -f infra/compose/compose.yaml ps    # wait until postgres reports "healthy"
```

This starts PostgreSQL 16 with pgvector on `localhost:5432`, with a `nu_hub` database for development and `nu_hub_test` for the API test suite. To change the defaults, copy `infra/compose/.env.example` to `infra/compose/.env`; Compose reads `.env` from the compose file's folder. The test database is created only when the data volume is first initialised; add `down -v` to the same command to reset it.

Run the API (FastAPI + Socket.IO):

```sh
cd apps/api
cp .env.example .env                  # local values only; never commit .env
python3 -m venv .venv                 # or `python` if that is your Python 3.12
source .venv/bin/activate
python -m pip install -c requirements.lock -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

`GET /api/v1/health` reports liveness and `GET /api/v1/health/ready` checks the database. Interactive API docs are at `/api/docs` outside production. API checks: `ruff check .`, `ruff format --check .`, `mypy`, `bandit -c pyproject.toml -r app` and `pytest` (the tests use the `nu_hub_test` database, never `nu_hub`). After changing dependencies in `pyproject.toml`, regenerate the lockfile with `pip-compile --extra dev --strip-extras -o requirements.lock pyproject.toml`.

Run the web app (React + Webpack) in another terminal:

```sh
cd apps/web
npm ci
npm run dev        # http://localhost:8080; /api and /socket.io are proxied to localhost:8000
```

Web checks: `npm run lint`, `npm run typecheck`, `npm test` and `npm run build`. The build fails if any backend variable name from `apps/api/.env.example` appears in the bundle.

## Repository layout

```
apps/web/        React + Webpack frontend
apps/api/        FastAPI backend, the only application server
infra/compose/   Local development services (PostgreSQL + pgvector)
```
