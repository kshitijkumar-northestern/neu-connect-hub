# NU Connect Hub

Peer discovery and team formation for verified university students: profiles, explainable collaborator recommendations, projects, messaging, video rooms and Scan to Connect QR profiles.

> **Status:** early development. Only the repository skeleton and the local database exist so far.

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

Setup instructions for the API and web app will be added as they are built.

## Repository layout

```
apps/web/        React + Webpack frontend
apps/api/        FastAPI backend, the only application server
infra/compose/   Local development services (PostgreSQL + pgvector)
```
