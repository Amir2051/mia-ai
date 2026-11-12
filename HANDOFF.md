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

## Current verified engineering evidence — 2026-11-08

### Main branch
- Main commit: `11ff8cc5f0b427117576e43edc4918907e72f750`.
- Latest main CI run #231 (2026-11-02): **285 backend tests passed, 3 warnings; 17 frontend tests passed; lint/build and Docker production image build passed.**
- The latest merged Alembic revision in the repository is `0009_privacy_audit_purge`.
- The main-branch audit gates pass at the configured moderate-severity threshold, but the audit still reports **3 low-severity `deepmerge` findings**. This is not a zero-vulnerability result.

### Proposed tenant-isolation hardening — PR #27
- Branch: `security/signed-tenant-context-2026-11-03`.
- Latest code/test head: `f9a7e9089c424a46801dcc7b6c7191c32e03dc67`. Subsequent documentation-only PR #27 heads `48058e8141cec528c96eb2a5d60ec39e5c78bb8e` (CI #262 passed) and `858ffbac9dd546ed7f50e76a0229b7fe23f41c0f` (CI #264 passed) leave the code/test tree unchanged.
- CI #264 on the subsequent SECURITY.md-only head `858ffbac9dd546ed7f50e76a0229b7fe23f41c0f` also passed all three jobs: 287 backend tests, 17 frontend tests, lint/build, configured moderate-threshold audit gates, and Docker image build.
- CI #261 (2026-11-08) on the current head: **all 3 jobs passed**. Backend: **287 passed, 3 warnings**; frontend: **17 passed**; lint/build, configured moderate-threshold audit gates, PostgreSQL migration downgrade/re-upgrade, and production Docker image build passed.
- CI #258 on `81aae0f7307155bbf7f951be710f622657828121` and CI #260 on `e2548d49c31294a87d2e84f66a8e74362d0041b5` also passed all three jobs. CI #260 verified the transaction-bound scheduler-signature replay regression.
- The runtime-role preflight rejects unsafe role attributes, direct/transitive role memberships, protected table/schema/private-object ownership, public SECURITY DEFINER ownership, CREATE on public/database, private-schema/key access, and effective TRUNCATE/REFERENCES/TRIGGER grants on public tables.
- The scheduler HMAC is bound to the PostgreSQL transaction ID; a captured proof is rejected when replayed in a later transaction.
- The scheduler trigger uses a positive allowlist: only `next_run_at`, `last_run_at`, `last_status`, `last_error`, and `updated_at` may change under scheduler context. Newly added scheduled-report columns are protected by default.
- Migration 0010 downgrade is tested to preserve unrelated objects in `mia_private`, restore the legacy policy set, remove signed helper objects, and re-upgrade successfully.
- The audit still reports **3 low-severity `deepmerge` findings** through the Polaris dependency tree.
- PR #27 remains draft, unmerged, and undeployed. No independent security approval has been recorded. These CI runs validate the PR branch only, not production deployment.

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
## Latest runtime-role privilege preflight — 2026-11-08

### New fail-closed migration checks on PR #27

Migration 0010_signed_tenant_context now verifies the configured DATABASE_URL role before it changes RLS policies or creates verifier objects. It requires the role to be LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION, and NOBYPASSRLS.

The preflight also rejects:
- Any direct or transitive PostgreSQL role membership. NOINHERIT alone is insufficient because membership can still permit SET ROLE on supported PostgreSQL versions.
- Ownership of the protected tenant tables, public/mia_private schemas, or any object in the private verifier schema.
- Ownership of SECURITY DEFINER functions in public.
- CREATE on the public schema or database.
- USAGE/CREATE on mia_private or any privilege on mia_private.tenant_context_keys.

The CI runtime fixture now uses NOINHERIT. A new PostgreSQL integration test exercises unsafe role attributes, transitive membership, protected-table/schema/key ownership, private-schema/key grants, and SECURITY DEFINER ownership.

### Additional 2026-11-08 hardening after the initial preflight

- Scheduler proof replay: the application now signs the internal-scheduler context with the current PostgreSQL transaction ID, and the database verifier recomputes against the current transaction. CI includes a negative replay attempt in a separate transaction.
- Role attribute regression cases now cover NOLOGIN, SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS, and INHERIT, plus direct/transitive memberships and ownership/grant checks.
- The preflight now rejects effective TRUNCATE, REFERENCES, and TRIGGER privileges on every public table. TRUNCATE is not subject to RLS; CI verifies direct grants and a TRUNCATE grant to PUBLIC are detected.
- CI #260 passed all three jobs on the transaction-bound scheduler proof change (287 backend, 17 frontend).
- CI #261 passed all three jobs on current head `f9a7e9089c424a46801dcc7b6c7191c32e03dc67`: 287 backend tests, 17 frontend tests, lint/build, configured moderate-threshold audit gates, and Docker image build.
- The npm audit continues to report 3 low-severity deepmerge findings. Passing the moderate threshold is not a zero-vulnerability result.

### Verification

- CI #254 on commit 6fa2c6b2d91d5172e1c82da3da79ca5f234b5172: all three jobs passed. Backend: 287 passed, 3 warnings; frontend: 17 tests passed, lint/build passed; production Docker image build passed.
- CI #255 on code/test commit 4bc64e96e00e2d8137aa033d54ba25b2fe8466e1: all three jobs passed. Backend: 287 passed, 3 warnings; frontend: 17 tests passed, lint/build passed; configured moderate-threshold audit gates and production Docker image build passed.
- The two intermediate runs #252 and #253 exposed, respectively, an asyncpg OID parameter typing issue and a malformed multiline SQL string. Both were corrected before the successful #254/#255 runs.
- These are PR-branch CI results only. They do not prove the production database role has the required catalog attributes; direct production catalog verification remains a deployment gate.
- Documentation/security-handoff head `2824c4c3e415142667ce9a77858f30f2d047768e` was also verified by CI #256: all three jobs passed, with 287 backend tests, 17 frontend tests, lint/build, configured moderate-threshold audit gates, and Docker build successful.

### Current review and release state

PR #27 remains open, draft, unmerged, and undeployed. The only recorded PR reviews are comments by the repository owner; there is no independent security approval. The owner-authored inline thread requesting the runtime-role preflight has been resolved after the code/test implementation and CI verification. This is not an independent approval: all recorded review submissions remain owner-authored, no independent security reviewer has approved the migration, and PR #27 remains draft.

Main remains at 11ff8cc5f0b427117576e43edc4918907e72f750; no production database or application changes were made. The actual production Alembic revision and running application SHA remain unverified. Do not apply migration 0010 until independent review and direct production-state/role verification are complete.


## Verification refresh — 2026-11-09

### Latest repository and CI evidence

- Main remains at `11ff8cc5f0b427117576e43edc4918907e72f750`. Its latest recorded CI run #231 completed successfully: 285 backend tests, 17 frontend tests, and production Docker image build passed.
- PR #27 current head is `858ffbac9dd546ed7f50e76a0229b7fe23f41c0f`. CI #264 completed successfully across all three jobs: 287 backend tests passed (3 warnings), 17 frontend tests passed, lint/build and configured moderate-threshold audit gates passed, and the production Docker image built.
- PR #28 head before this documentation refresh is `e17b21468c1ef924c92dd7088e9ca1fb959b8044`. CI #265 completed successfully across all three jobs: 285 backend tests passed (3 warnings), 17 frontend tests passed across 3 files, lint/build and configured moderate-threshold audit gates passed, and the production Docker image built.
- The npm audit logs continue to report 3 low-severity `deepmerge` findings. The current lockfile resolves `deepmerge@4.3.1`; GitHub Advisory GHSA-gcqx-w48m-w2h4 lists 4.2.0 through 4.3.1 as affected and currently lists no patched version. Do not describe the audit as clean or apply a forced major upgrade without compatibility testing. Advisory: https://github.com/advisories/GHSA-gcqx-w48m-w2h4

### Review and repository governance

- PR #27's two owner-authored inline review threads are now resolved. The runtime-role preflight request was addressed in migration 0010 and covered by PostgreSQL regression tests. This does not constitute independent security approval: recorded PR reviews are owner-authored comments only, and no independent reviewer has approved the migration.
- PR #27 remains open, draft, unmerged, and undeployed.
- GitHub reports the `main` branch as `protected: false`. Required CI/review branch protection is therefore not verified as enforced; configure it before treating main as a protected release branch.

### Shopify acceptance items still requiring direct validation

- `shopify.app.toml` registers `https://mia.drivenest.info/auth/callback`, but the backend auth router exposes `/api/auth/login-url`, `/api/auth/session`, and `/api/auth/logout`; the frontend route table has no `/auth/callback` route. The current embedded ID-token exchange path is implemented, but the configured redirect must be tested in a real development-store install/reinstall flow and corrected if Shopify invokes it.
- Publication scopes `read_publications` and `write_publications` are declared in `shopify.app.toml`; release of the updated app configuration and merchant re-approval are not verified.
- No production database revision, production runtime-role catalog state, production `RLS_CONTEXT_SECRET`, deployed application SHA, or live merchant acceptance was directly verified in this run. Do not apply migration 0010 or claim production readiness from CI alone.

### Next actions

1. Obtain independent security review of migration 0010 and the deployment/rollback model.
2. Read-only verify the production Alembic revision and runtime-role attributes, memberships, ownership, and effective privileges before planning migration.
3. Enable required status checks and review protection on `main`.
4. Run development-store install/reinstall, token exchange, publication-scope approval, product publishing, webhook, GDPR, and billing acceptance.
5. Replace process-local FastAPI `BackgroundTasks` for campaign jobs with a durable worker/lease-recovery design before claiming restart-safe execution.
6. Track the `deepmerge` advisory until upstream publishes a fix; assess the actual transitive usage and avoid unsafe forced upgrades.
