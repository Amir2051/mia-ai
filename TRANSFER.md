# Mia AI — Local Transfer Guide

## Transfer Archive Contents

Include:
- `backend/` except excluded items below
- `frontend/` except excluded items below
- `README.md`
- `SECURITY.md`
- `HANDOFF.md`
- `shopify.app.toml`

Exclude from archive:
- `.env` and any `*.env`
- `node_modules/`
- `backend/.venv/`
- `backend/.pytest_cache/`
- `frontend/dist/`
- `*.db`
- `*.sqlite`
- `*.log`
- `__pycache__/`
- `*.pyc`

## Prerequisites

- Python 3.11+
- Node.js 18+
- npm
- SQLite tooling for local development

## Backend Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
cp .env.example .env
pip install -r requirements.txt
```

## Frontend Setup

```bash
cd frontend
npm install
cp .env.example .env
```

## Database

Local SQLite is used by default. The application creates tables on startup. For local development, the default sqlite file path from `.env.example` is `sqlite:///./dev.db`.

## Migrations

```bash
cd backend
alembic upgrade head
```

## Local Startup

```bash
# Backend
cd backend
source .venv/bin/activate
uvicorn main:app --reload

# Frontend
cd frontend
npm run dev
```

## Required Environment Variables

See `backend/.env.example`. At minimum:
- `SECRET_KEY`
- `DATABASE_URL`
- `SHOPIFY_API_KEY`
- `SHOPIFY_API_SECRET`
- `SHOPIFY_APP_URL`
- `SHOPIFY_REDIRECT_URIS`
- `SHOPIFY_SCOPES`
- `SHOPIFY_API_VERSION`

## Shopify Connection Steps — DO NOT PERFORM NOW

1. Set `SHOPIFY_API_KEY` and `SHOPIFY_API_SECRET` in `backend/.env`.
2. Set `SHOPIFY_APP_URL` to the publicly reachable backend URL.
3. Configure `SHOPIFY_REDIRECT_URIS` for the local or public callback path.
4. Install the app in a Shopify dev store.
5. Verify `/auth/install` redirects to Shopify.
6. Complete OAuth callback and confirm `/auth/session` returns `connected: true`.
7. Verify webhooks with real Shopify-provided `API_SECRET_KEY`.

## Notes

- Do not copy `.env` with real credentials between machines.
- Keep Shopify credentials server-side only.
