# Mia AI — Handoff Guide

This guide is intended for another Hermes agent or developer taking over the Mia AI project from this environment.

## Project Location

```
/opt/data/mia-ai/
```

## What Is Ready

- Complete backend architecture: FastAPI app shell, Shopify abstraction, auth interfaces, database models, services, health endpoints, and pytest tests.
- Complete frontend architecture: React app shell with Polaris layout, route pages for all required modules, API service layer, and query hooks.
- Local database schema and migration-ready models.
- Test suite for margin calculations, product mapping, auth config, and Shopify client behavior.
- Documentation including README, security review, and this handoff guide.

## What Is Still Pending

- Real Shopify Partner credentials for Mia AI (`418029862913`)
- Shopify CLI linking to the existing Partner app
- OAuth flow completion and session persistence
- Shopify API client activation with access token
- Product/order/customer live routes
- Webhook endpoints and HMAC verification
- Import supplier integrations
- Embedded app installation on a dev store

## Portability Notes

- No machine-specific paths are required. Copy the entire `mia-ai/` directory.
- Use Node.js 18+ and Python 3.11+.
- Backend runs on `http://localhost:8000`.
- Frontend dev server runs on `http://localhost:5173` and proxies `/api` to the backend.

## Next Steps

1. Supply Shopify Partner credentials via environment variables.
2. Link local app to existing Mia AI app via Shopify CLI.
3. Connect a Shopify development store.
4. Complete OAuth and test embedded Admin flow.
5. Implement live data routes.
6. Validate install/auth/products/orders/customers/webhooks/session persistence.
7. Stop before production deployment until approval.

## Safety Reminders

- Do not modify SafeNestT infrastructure.
- Do not install on the production Shopify store without approval.
- Do not commit `.env` files or real secrets.
