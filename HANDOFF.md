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

The last Shopify app configuration release recorded in this handoff is **mia-ai-9** on 2026-09-18; no later release has been verified. The production URL is `https://mia.drivenest.info`, but its current health and running commit are not revalidated here.

## Current verified engineering evidence — 2026-11-06

### Main branch
- Main commit: `11ff8cc5f0b427117576e43edc4918907e72f750`.
- Latest main CI run #231 (2026-11-02): **285 backend tests passed, 3 warnings; 17 frontend tests passed; lint/build and Docker production image build passed.**
- The latest merged Alembic revision in the repository is `0009_privacy_audit_purge`.
- The main-branch audit gates pass at the configured moderate-severity threshold, but the audit still reports **3 low-severity `deepmerge` findings**. This is not a zero-vulnerability result.

### Proposed tenant-isolation hardening — PR #27
- Branch: `security/signed-tenant-context-2026-11-03`.
- Latest branch head at this handoff update: `25e45aa7837c1ca61c4b7d33496583585598a370`.
- CI run #250 (2026-11-06): **all 3 jobs passed**. Backend: **286 passed, 3 warnings**; PostgreSQL restricted-role migration and regression tests passed; the explicit downgrade/re-upgrade check passed. Frontend: **17 tests passed**, lint and production build passed. Both configured moderate-threshold audit gates passed. Production Docker image build passed.
- The latest regression test independently attempts to forge `app.shop_id`, forge `app.shop_domain` while retaining the original shop's valid signature, set `app.internal_scheduler=true` without its signature, and combine the forged GUCs. The test also verifies that scheduler claim/status fields remain writable while merchant-owned schedule fields remain blocked.
- The scheduler trigger uses a positive allowlist: only `next_run_at`, `last_run_at`, `last_status`, `last_error`, and `updated_at` may change under scheduler context. Newly added scheduled-report columns are protected by default.
- Migration 0010 downgrade is tested to preserve unrelated objects in `mia_private`, restore the legacy policy set, remove signed helper objects, and re-upgrade successfully.
- The audit still reports **3 low-severity `deepmerge` findings** through the Polaris dependency tree.
- PR #27 remains a draft, unmerged, undeployed, and not production-approved. No independent security review has been recorded. CI #250 validates the current code branch, not production deployment.

### Other open feature PRs
- PR #25 (async full-campaign jobs) remains open. Its last recorded CI #232 passed, but the branch is **3 commits ahead and 1 behind** current main. It uses FastAPI process-local `BackgroundTasks`; persisted job rows do not make execution durable across process restarts.
- PR #26 (Meta test-token fallback) remains open. Its last recorded CI #230 passed, but the branch is **1 commit ahead and 1 behind** current main. Its PR description says no new tests were added.
- Rebase/synchronize these PRs and rerun CI before treating their previous results as current merge evidence.

### Production evidence — historical, not revalidated
- The last release recorded in this handoff is Shopify app version **`mia-ai-9` on 2026-09-18**.
- The previous handoff recorded `/health` HTTP 200 and HTTPS/HSTS/security-header checks. Those checks have not been independently revalidated on 2026-11-06.
- The previous handoff recorded production Alembic head `0004_postgres_rls`. The repository has advanced through `0009_privacy_audit_purge`; the actual production database revision must be queried directly before any deployment or migration.
- The currently running production commit SHA remains unverified.
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
- `RLS_CONTEXT_SECRET` (required only if PR #27 is approved and migration `0010_signed_tenant_context` is deployed; random, at least 32 bytes, and identical for the app and migration process)

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
1. Install/reinstall from Shopify Admin, embedded launch, App Bridge ID-token exchange, and token refresh.
2. Product read/update/create and CSV import preview/run.
3. Sales-channel selection, publication, unpublishing, and publication-state read-back after scope approval.
4. Orders and analytics against live Shopify data.
5. Merchant-memory persistence and scheduled-report execution.
6. Webhook delivery, duplicate delivery, uninstall, customer redaction, and shop redaction.
7. Customer names/email/phone only after Shopify Protected Customer Data approval.
8. Meta connection and actual post publishing.
9. Shopify Billing subscription creation, cancellation/update webhooks, and paid-feature entitlement enforcement.
10. OpenRouter SEO/marketing generation with the production key.
11. Production health, migration revision, running commit SHA, and post-deployment smoke tests.

These are runtime acceptance steps, not unresolved code placeholders.

## Deployment
Build and restart the production container with Docker Compose after source changes (on the Kali host the command is `docker-compose`):

```bash
docker-compose up -d --build mia-ai
```

The container entrypoint runs Alembic migrations before Uvicorn. The image includes a Docker healthcheck against `/health`. Migration `0010_signed_tenant_context` is proposed on draft PR #27 and must not be applied until independent review, production database revision/ownership checks, and secret provisioning are complete. If approved, `RLS_CONTEXT_SECRET` must be present in both the protected runtime environment and migration process; do not rotate it independently on only one side.

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
