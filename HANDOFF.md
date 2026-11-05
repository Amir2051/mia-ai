# Mia AI — Production Handoff

## Project
- Repository: `Amir2051/mia-ai`
- Local path: `/home/ronzoro/Downloads/mia-ai/mia-ai`
- Production URL: `https://mia.drivenest.info`
- Shopify app: Mia AI
- Shopify client ID: `9c422dedf9e850a1f8fb3078ca747fa7`
- Shopify Admin API version: `2026-07`

## Current state
Mia AI is a production-oriented embedded Shopify Admin app using FastAPI, React/Vite, PostgreSQL, Shopify managed installation, App Bridge ID-token authentication, and server-side token exchange.

The latest Shopify app configuration was deployed and released as version **mia-ai-9** on 2026-09-18.

The production web container is served through Cloudflare at `https://mia.drivenest.info`.

## Current verified engineering evidence — 2026-11-05

### Main branch
- Main commit: `11ff8cc5f0b427117576e43edc4918907e72f750`.
- Latest main CI run #231 (2026-11-02): **285 backend tests passed, 3 warnings; 17 frontend tests passed; lint/build and Docker production image build passed.**
- The repository's latest merged Alembic revision is `0009_privacy_audit_purge`.
- The current main-branch audit gates are configured at the moderate-severity threshold. They pass, but the audit still reports **3 low-severity `deepmerge` findings**. Do not describe the dependency tree as vulnerability-free.

### Proposed tenant-isolation hardening
- PR #27 adds proposed migration `0010_signed_tenant_context` with HMAC-verified tenant and scheduler contexts.
- Current PR #27 head: `ef0d6a1b979500302cd780f9ff761cd7feea8be1`.
- Latest CI run #245 (2026-11-05): **286 backend tests passed, 3 warnings; 17 frontend tests passed; frontend lint/build, both configured audit gates, and Docker image build passed.**
- The backend CI also exercises a real downgrade to `0009_privacy_audit_purge`, checks that unrelated objects in `mia_private` survive, verifies the prior scheduler policy is restored, then upgrades back to `0010_signed_tenant_context`.
- PR #27 is still a draft, unmerged, undeployed, and not production-approved. Independent security review is required before merge/deployment.

### Production evidence — historical, not revalidated today
- The previous handoff recorded a successful Shopify app configuration release as **`mia-ai-9` on 2026-09-18**.
- The previous handoff also recorded `/health` HTTP 200 and HTTPS/HSTS/security-header checks. Those checks were not independently revalidated on 2026-11-05.
- The previous handoff recorded production Alembic head `0004_postgres_rls`. The repository has since advanced through `0009_privacy_audit_purge`; the actual production database revision must be queried directly before any deployment or migration.
- No newer Shopify app configuration release or currently running production commit SHA has been verified.
- The declared `read_publications`/`write_publications` scopes still need a confirmed Shopify CLI release and merchant re-approval where required.

## Production verification status

A real merchant/development-store session and direct production checks are still required. Do not infer current production health or database migration state from the historical handoff entries above.

## Shopify authentication configuration check

- `shopify.app.toml` currently registers `https://mia.drivenest.info/auth/callback`.
- The backend auth router currently exposes `/api/auth/login-url`, `/api/auth/session`, and `/api/auth/logout`; no `/auth/callback` route is present.
- The app uses Shopify managed installation and embedded ID-token token exchange, so the redirect URL may be legacy/unused. Confirm the actual install and re-authentication flow on a development store before changing the configured URL.

## Frontend

The admin UI is built with Shopify Polaris and App Bridge:
- Navigation is the App Bridge `ui-nav-menu`, shown in the Shopify admin sidebar (not an in-app sidebar).
- `src/lib/session.tsx` loads `/api/auth/session` once for the whole app.
- Product and AI-generated HTML always renders through `src/components/SafeHtml.tsx` (DOMPurify).
- Shopify ids contain slashes; route them with `idPath()` from `src/lib/format.ts`.
- Opening the app URL outside Shopify Admin shows a short "open it from Shopify" message by design.

## Production configuration
Required server secrets remain environment-only:
- `SHOPIFY_API_SECRET`
- `SECRET_KEY`
- `TOKEN_ENCRYPTION_KEY`
- `DATABASE_URL`
- `OPENROUTER_API_KEY`
- `RLS_CONTEXT_SECRET` (random, at least 32 bytes; must match the verifier provisioned by migration `0010_signed_tenant_context`)

Production must use PostgreSQL, HTTPS, secure/HttpOnly/SameSite=None cookies, and only the production origin in CORS.

## Shopify scopes
Current declared scopes (kept in sync with `shopify.app.toml`):
`read_customers,read_inventory,read_orders,read_products,read_publications,write_products,write_publications`

`read_publications`/`write_publications` are used for sales-channel publishing. Confirm the latest `shopify.app.toml` has been released with `shopify app deploy` and that merchants re-approve the new scopes; the last release recorded in this document is `mia-ai-9`.

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
Build and restart the production container with Docker Compose after source changes (on the Kali host the command is `docker-compose`):

```bash
docker-compose up -d --build mia-ai
```

The container entrypoint runs Alembic migrations before Uvicorn. The image now includes a Docker healthcheck against `/health`. Ensure `RLS_CONTEXT_SECRET` is present in the protected runtime environment before applying migration `0010_signed_tenant_context`; the migration also needs it in its environment to provision the private database verifier. Do not rotate it independently on only one side.

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
