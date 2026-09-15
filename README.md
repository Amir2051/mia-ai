# Mia AI — Shopify Embedded Admin App

Production Shopify embedded admin application for the existing Mia AI Partner app.

- **Shopify App ID:** `418029862913`
- **Client ID:** `c3778df7ce03ca72b52ea21020c3570b`
- **Admin API:** `2026-07`
- **Production URL:** `https://drivenest.info`

## Features

- Embedded Shopify Admin dashboard
- Product management
- CSV product imports with duplicate actions: `skip`, `update`, `create`, `fail`
- Orders and customers
- Analytics and marketing architecture
- Settings
- Shopify App Bridge ID-token authentication and OAuth fallback
- Encrypted Shopify token storage
- HMAC-verified, deduplicated Shopify webhooks

## Architecture

```text
mia-ai/
├── backend/       # FastAPI + SQLAlchemy + Alembic
├── frontend/      # React + TypeScript + Vite
├── docker-entrypoint.sh
├── Dockerfile
└── shopify.app.toml
```

Production uses PostgreSQL. SQLite is development/test only.

## Required Production Environment

Set these values in the deployment environment; never commit the Shopify secret:

```text
ENVIRONMENT=production
SECRET_KEY=<long-random-secret>
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/mia
SHOPIFY_API_KEY=c3778df7ce03ca72b52ea21020c3570b
SHOPIFY_API_SECRET=<Shopify secret>
SHOPIFY_APP_URL=https://drivenest.info
SHOPIFY_SCOPES=read_products,write_products,read_orders,read_customers,read_inventory
SHOPIFY_API_VERSION=2026-07
TOKEN_ENCRYPTION_KEY=<Fernet key>
CORS_ORIGINS=https://drivenest.info
SESSION_COOKIE_SECURE=true
```

## Production build

The Docker image builds the React frontend, installs the FastAPI backend, copies the built SPA into the runtime image, runs Alembic migrations, and starts Uvicorn.

```bash
docker build -t mia-ai .
docker run --env-file .env -p 8000:8000 mia-ai
```

Cloudflare should proxy `drivenest.info` to the production origin. Keep the Shopify secret and database credentials only in the server environment.

## Database migrations

Schema changes are managed by Alembic. Application startup does **not** call `create_all()`.

```bash
cd backend
alembic upgrade head
```

## Testing

```bash
cd backend && pytest -q
cd frontend && npm run build
```

GitHub Actions also runs backend migrations/tests and the frontend build for pushes and pull requests to `main`.

## Shopify configuration

`shopify.app.toml` is configured for:

- `https://drivenest.info`
- Embedded authentication: Shopify managed installation + ID-token token exchange
- webhook endpoint: `https://drivenest.info/api/webhooks`
- app uninstall, product create/update/delete, and order create/update subscriptions

Before production install, perform a live dev-store acceptance test covering managed installation, embedded loading, ID-token token exchange, products, CSV import, orders, customers, uninstall, reinstall, and webhook delivery.

## Security

- Shopify access tokens encrypted at rest
- App Bridge ID-token validation
- Shopify ID-token validation and token exchange
- Webhook HMAC verification and event deduplication
- Merchant isolation on shop-scoped records
- Production PostgreSQL requirement
- Request-size limits and security headers
- Environment-driven CORS
- Structured request IDs and safe 500 responses
- No secrets committed to source control
