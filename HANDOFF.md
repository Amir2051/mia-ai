# Mia AI — Production Handoff

## Project
- Repository: `Amir2051/mia-ai`
- Local path: `/home/ronzoro/Downloads/mia-ai/mia-ai`
- Production URL: `https://drivenest.info`
- Shopify app: Mia AI
- Shopify client ID: `9c422dedf9e850a1f8fb3078ca747fa7`
- Shopify Admin API version: `2026-07`

## Current state
Mia AI is a production-oriented embedded Shopify Admin app using FastAPI, React/Vite, PostgreSQL, Shopify managed installation, App Bridge ID-token authentication, and server-side token exchange. The app does not implement an `/auth/callback` OAuth route; the obsolete callback URL has been removed from `shopify.app.toml`.

The latest Shopify app configuration was deployed and released as version **mia-ai-9** on 2026-09-18.

The production web container is served through Cloudflare at `https://drivenest.info`.

## Verified
- Backend: 80 tests passed.
- Frontend TypeScript build: passed.
- Frontend ESLint: passed.
- npm production audit: 0 vulnerabilities.
- npm full audit: 0 vulnerabilities.
- Production `/health`: HTTP 200.
- Production HTTPS/HSTS/security headers: verified.
- PostgreSQL Alembic head: `0004_postgres_rls`.
- Shopify app version deployment: successful.
- App Bridge script is loaded from Shopify's official CDN.
- Mandatory compliance topics are configured.
- Unauthenticated API probes do not expose merchant data.
- Settings mutation requires an authenticated Shopify session.

## Production configuration
Required server secrets remain environment-only:
- `SHOPIFY_API_SECRET`
- `SECRET_KEY`
- `TOKEN_ENCRYPTION_KEY`
- `DATABASE_URL`
- `OPENROUTER_API_KEY`

Production must use PostgreSQL, HTTPS, secure/HttpOnly/SameSite=None cookies, and only the production origin in CORS.

## Shopify scopes
Current declared scopes:
`read_customers,read_inventory,read_orders,read_products,write_products`

The app uses Shopify managed installation and embedded ID-token authentication.

## Customer-data limitation
The app requests `read_customers`, but Shopify protected customer data is separately controlled. Production stores may return ACCESS_DENIED for protected fields until the app receives the required protected customer-data approval. Mia handles this state gracefully instead of exposing an error page.

To enable production customer names/email/phone data, complete the Protected Customer Data request in the Shopify Partner Dashboard and request only the fields the app legitimately needs.

## Remaining manual acceptance
A real merchant/dev-store session is still required for final end-to-end acceptance of:
1. Install/reinstall from Shopify Admin.
2. Embedded launch and App Bridge ID-token exchange.
3. Product read/update/create.
4. CSV import preview and run.
5. Orders and analytics.
6. Customer data after protected-data approval.
7. Webhook delivery and uninstall.
8. OpenRouter SEO/marketing generation with the production key.

These are runtime acceptance steps, not unresolved code placeholders.

## Deployment
Build and restart the production container with Docker Compose after source changes:

```bash
docker compose up -d --build
```

The container entrypoint runs Alembic migrations before Uvicorn. The image now includes a Docker healthcheck against `/health`.

## CI
GitHub Actions runs:
- Alembic migrations
- Backend pytest
- Frontend build
- npm security audits
- Production Docker image build

## Security reminders
- Never commit `.env`, Shopify secrets, OpenRouter keys, database credentials, or migration credentials.
- Do not expose access tokens in logs or API responses.
- Keep production and development Shopify configurations separate.
- Re-run the production smoke tests after every deployment.
