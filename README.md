# Mia AI - Shopify Embedded Admin App

Production-quality Shopify embedded admin application built for the existing Mia AI Partner app (App ID: 418029862913, API version 2026-07).

## What This Is

Mia AI is a modular Shopify merchant administration application. It provides a professional embedded Admin experience for:

- Dashboard
- Product management
- Product imports
- Order management
- Customer insights
- Analytics
- Marketing architecture
- Settings

## Project Location

```
/opt/data/mia-ai/
```

## Architecture

```
mia-ai/
├── backend/            # FastAPI backend (Python)
│   ├── app/
│   │   ├── shopify/    # Shopify integration layer
│   │   ├── models/     # Database models & schemas
│   │   ├── services/   # Business logic
│   │   └── routers/    # API routes
│   └── tests/          # Backend tests
├── frontend/           # React + TypeScript SPA
│   └── src/
│       ├── components/ # Reusable UI components
│       ├── pages/      # Route pages
│       ├── services/   # API clients
│       └── hooks/      # React hooks
├── tests/              # Cross-cutting tests
└── README.md
```

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy + SQLite (dev) / PostgreSQL (prod-ready)
- **Frontend:** React 18 + TypeScript + Vite + Tailwind CSS
- **Shopify:** Admin GraphQL abstraction with placeholder credentials
- **Testing:** pytest (backend) + Vitest (frontend)

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 18+
- npm or pnpm

### Setup

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

### Environment Variables

See `.env.example` for required variables. Shopify-specific values are intentionally left as placeholders until credentials are supplied.

### Database

Local SQLite database is created automatically at `backend/dev.db`. Schema includes:

- `shops`
- `shop_sessions`
- `products`
- `product_variants`
- `suppliers`
- `product_imports`
- `sync_jobs`
- `webhook_events`
- `app_settings`
- `audit_logs`

## Testing

```bash
# Backend
cd backend && pytest

# Frontend
cd frontend && npm test

# All
cd tests && ./run_tests.sh
```

## Shopify Connection (Pending)

The app is structured to connect to the existing Mia AI Partner app (418029862913) once credentials are supplied.

Required Shopify configuration:
- `SHOPIFY_API_KEY`
- `SHOPIFY_API_SECRET`
- `SHOPIFY_APP_URL` (development URL)
- `SHOPIFY_REDIRECT_URIS`
- `SHOPIFY_SCOPES`
- `SHOPIFY_API_VERSION=2026-07`

See `backend/app/shopify/config.py` for integration points.

## Security

- No secrets in source code
- `.env` files gitignored
- Server-side only Shopify credentials
- Merchant data isolated per shop
- Input validation on all endpoints
- Webhook HMAC verification stubs ready

## Production Deployment

**NOT YET DEPLOYED.** Do not install on production Shopify stores until:
1. Shopify credentials are configured
2. OAuth flow is tested
3. Webhook endpoints are verified
4. Security review is complete
5. Merchant isolation is validated

## License

Proprietary - Mia AI
