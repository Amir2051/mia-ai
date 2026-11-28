# Current verified checkpoint — 2026-11-27

> This section supersedes the November 26 checkpoint below. Older dated notes are retained as historical evidence. Use the exact PR heads and CI runs recorded here for current branch evidence.

## Latest security work — PR #27

- PR: https://github.com/Amir2051/mia-ai/pull/27
- Branch: `security/signed-tenant-context-2026-11-03`
- Current head: `400500b193c360ae54f7eaea460547f5282d0862`; 138 commits ahead of main, 0 behind; 13 files changed.
- State: open, draft, mergeable, not merged or deployed.
- FastAPI startup verifies that the configured PostgreSQL HMAC key matches the private database verifier. In one transaction it calls `set_internal_scheduler_context()` and checks `public.mia_is_internal_scheduler()`. It does not read or expose the private key and fails closed if migration 0010 is missing, the verifier cannot be executed, or the key does not match.
- A re-read of the November 24 CodeRabbit review summary found that the psycopg identity probe still forwarded SQLAlchemy's asyncpg-only `prepared_statement_cache_size` URL option. The probe now uses `_runtime_psycopg_url()`, removes only that asyncpg-specific option, and preserves other PostgreSQL query options such as TLS and application-name settings.
- Added parameterized tests for an unadorned PostgreSQL URL, a URL with `prepared_statement_cache_size=0`, and a URL that combines that option with `sslmode=require` and `application_name=mia`.
- The startup-key integration test remains in place: the CI restricted runtime role accepts the actual migration key, rejects a different valid-length key, and fails closed when EXECUTE on the verifier function is revoked. The test restores the grant in `finally`.
- CI #360 (2026-11-27) passed all three jobs on this exact head:
  - Backend: **316 passed, 3 warnings**.
  - Frontend: **17 passed**; lint and production build passed.
  - Production-only and full npm audits: **0 vulnerabilities** each.
  - Migration 0010 upgrade, downgrade to `0009_privacy_audit_purge`, and re-upgrade: **passed**.
  - Production Docker build: **passed**.
- CI #360 is PR-branch evidence only. It does not prove production roles, verifier provisioning, database/provider/proxy logging, or live merchant behavior.
- GitHub reports **26 total inline review threads, 26 resolved, 0 unresolved**. The recorded reviews are comments, not an independent `APPROVED` security review. A fresh CodeRabbit re-review of the latest head has not been recorded; the previous re-review request was rate-limited.
- Keep PR #27 draft. Do not apply migration 0010 until independent security review and direct production-state verification are complete.

[CI #360](https://github.com/Amir2051/mia-ai/actions/runs/46016050621)

## Repository and governance checkpoint

- Main remains at `59660d53d7c6b3ceeae7a584499e5ad392733fb8`; no application PR has been merged. The latest main commit is the November 10 documentation-only merge.
- GitHub branch metadata reports `main.protected=false`; the repository rulesets endpoint returns an empty list. Required CI and review protection are not enforced.
- The branch-protection detail endpoint returned HTTP 403 to the connected integration, but the public branch metadata independently reports protection disabled and the rulesets collection is empty.
- No production database, application secret, Shopify app configuration, or deployment was modified during this update. Production running SHA, Alembic revision, database role/ACL state, and health remain unverified.

## Open pull-request inventory and triage

The repository currently has **11 open pull requests**. The six current engineering/documentation PRs are #27, #30, #29, #25, #26, and #31. Five older PRs remain open and need deliberate triage:

- PR #2 — independent audit fixes: draft, non-mergeable, last updated 2026-09-18.
- PR #9 — scheduled-report RLS refresh: draft, non-mergeable, last updated 2026-10-23.
- PR #10 — sales-channel publishing API tests: draft, stale, last updated 2026-10-23.
- PR #11 — Vitest dependency advisory fixes: open, non-mergeable, last updated 2026-10-24. Compare its old dependency changes against the current clean PR-branch audits before deciding whether they remain needed.
- PR #12 — optional MIA-RZ provider: draft, non-mergeable, last updated 2026-10-23.

Do not merge stale branches without reviewing their current diffs, rebasing, and rerunning CI. Decide whether to refresh or close each one.

## Documentation handoff — PR #31

- PR #31 remains open, documentation-only, and unmerged.
- Its previous head `d295e99fefeb366199710fdfaa264b5cefc95ce6` passed CI #358 on 2026-11-26: backend **285 passed, 3 warnings**; frontend **17 passed**; both npm audits **0 vulnerabilities**; production Docker build passed.
- This November 27 checkpoint is a documentation-only update on the same PR branch. Its own CI must pass before this revision is described as CI-verified.
- No application code, Shopify configuration, secrets, production database, or deployment is changed by this handoff update.

## Tasks 1–8 and release gate

- Task 1 — Shopify authentication/App Bridge: implementation exists on main; embedded launch, token exchange/refresh, reauthorization, and install/uninstall behavior still need development-store acceptance. The manifest's `/auth/callback` URL does not match a route in the auth router; validate the managed-install/ID-token flow before changing it.
- Task 2 — Webhooks/GDPR: HMAC validation and uninstall/customer/shop redaction handlers exist. PR #30 adds shop-redaction cascade regression coverage and CI #339 passed; real Shopify webhook deliveries, redaction, and Protected Customer Data approval remain outstanding.
- Task 3 — Products/CSV/publishing: core endpoints exist. PR #29 aligns the backend's required-scope health check with the seven manifest scopes; CI #279 passed. It does not grant scopes or release the app. Reauthorization and live product/CSV/publish/unpublish acceptance remain.
- Task 4 — AI/orders/analytics/billing: implementation exists, but PR #25 still uses process-local FastAPI `BackgroundTasks`, so campaign execution is not restart-durable. PR #26's Meta fallback has automated coverage only. Billing defaults remain `billing_test=true` and `billing_enforced=false`; live billing, cancellation, webhook handling, paid entitlements, Meta OAuth/publishing, and OpenRouter production generation remain unverified.
- Task 5 — PostgreSQL/tenant isolation: PR #27's signed tenant and scheduler context migration has strong restricted-role PostgreSQL regression coverage, including the latest startup-key and psycopg URL-option checks. It remains PR-only and requires independent security approval plus direct production role, ownership, ACL, cluster, secret, and logging verification.
- Task 6 — Docker/deployment: PR #27 CI #360 and PR #31 CI #358 production image builds passed. A successful build does not prove deployment. Production SHA, applied Alembic revision, runtime configuration, and health remain unknown.
- Task 7 — CI/security/governance: the current PR branches have green CI, but main is unprotected and there are no repository rulesets. No independent security approval is recorded.
- Task 8 — production acceptance: incomplete. Development-store installation, merchant workflows, live integrations, production database verification, reviewed migration, deployment, smoke tests, and release sign-off remain.

Immediate gates:
1. Protect main with required backend/frontend/Docker checks and independent review.
2. Obtain independent human security approval for migration 0010.
3. Directly verify production database identity, Alembic revision, runtime/definer roles, memberships, function owners, effective ACLs, signing-key provisioning, and PostgreSQL/provider/proxy parameter logging.
4. Complete development-store install/authentication, product/CSV/publication, webhook/GDPR, billing, Meta, and OpenRouter acceptance.
5. Replace FastAPI process-local campaign background tasks with durable worker execution and restart recovery before claiming restart-safe jobs.

---

# Current verified checkpoint — 2026-11-25

> This section is the current source of truth for repository and release status. Older dated notes below are retained as historical evidence and must not override this checkpoint.

## Repository and release governance

- Repository: `Amir2051/mia-ai`; default branch: `main`.
- Current `main` SHA: `59660d53d7c6b3ceeae7a584499e5ad392733fb8`. No application PR in this checkpoint has been merged.
- GitHub's main branch object reports `protected: false`; the repository rulesets endpoint returns an empty list. Required checks and review protection are not enforced. The connected GitHub integration can read this state but does not expose the administration write operation needed to enable protection.
- The last Shopify app version recorded in the historical handoff is `mia-ai-9` (2026-09-18). A newer app release has not been verified.
- PR #28 was documentation-only; it is not evidence of an application release.

## Latest CI and pull-request evidence

### PR #27 — signed PostgreSQL tenant context

- PR: https://github.com/Amir2051/mia-ai/pull/27
- Branch: `security/signed-tenant-context-2026-11-03`
- Current head: `d41b26b5d6cd80bd57be97f63cbcebf314698647`; 131 commits ahead of main, 0 behind; 13 files changed.
- State: open, draft, mergeable, not merged or deployed.
- CI #349 (2026-11-25) passed all three jobs on this exact head:
  - Backend: **310 passed, 3 warnings**.
  - Frontend: **17 passed**.
  - Production Docker build: **passed**.
  - Signed-context migration upgrade, downgrade to `0009_privacy_audit_purge`, and re-upgrade passed using the restricted migration login.
  - The full and production-only npm audit gates passed with zero reported vulnerabilities in the latest relevant PR-branch run.
- Nine CodeRabbit findings from the November 24 review were addressed:
  - Runtime connection verifies `current_user` matches the username configured in `DATABASE_URL`.
  - Named function EXECUTE revocations use `CASCADE`; a PostgreSQL regression seeds a grant-option chain and verifies both the grantor and delegate lose execution.
  - Downgrade resolves the runtime role from configuration without opening the runtime login, while checking the rollback database through the migration connection.
  - RLS signing-key validation moved from module import to the FastAPI lifespan. Alembic can import settings for inspection/rollback without the application-only signing secret; application startup still fails closed.
  - Integration tests derive runtime and migration role identities from configured URLs and use the PostgreSQL 16 `SET` / pre-16 `MEMBER` membership gate. The CI-only unexpected-grant assertion remains in the workflow that seeds the role.
  - The PUBLIC-executable function fixture explicitly grants PUBLIC EXECUTE.
  - Advisory-lock tests record and compare the held, probed and released keys.
  - The normalized shop-domain index test proves that the installed expression index rejects case variants before the separate post-drop duplicate-diagnostic test.
  - SECURITY.md now reflects the withdrawn picomatch advisory and current clean PR-branch audit evidence, while preserving the production-verification caveat.
- CI #348 failed on one newly added startup-test import (`app.main` instead of the repository's top-level `main` module). That import was corrected; CI #349 then passed with 310 backend tests.
- All **25** recorded inline review threads are resolved; **0 unresolved**. A fresh `@coderabbitai review` request was posted in the PR conversation after CI #349; no new review result is recorded yet. There is still **no independent human security approval**.
- Migration `0010_signed_tenant_context` is proposed only. Main remains at `0009_privacy_audit_purge`. Do not deploy migration 0010 until independent review and direct production verification are complete.
- Downgrade restores the legacy caller-settable GUC policies; it is security-sensitive and is not a routine rollback.

### PR #30 — shop-redaction cascade regression

- PR: https://github.com/Amir2051/mia-ai/pull/30
- Branch: `test/gdpr-shop-redact-cascade-coverage-2026-11-19`
- Head: `a227966ab983f4c77a354f8d40938d2966eb80b7`; 9 commits ahead of main, 0 behind.
- State: open, ready for review, mergeable, not merged.
- CI #339 (2026-11-23) passed all three jobs on the current head: backend **285 passed, 3 warnings**; frontend **17 passed**; both npm audit commands reported zero vulnerabilities; Docker production build passed.
- The test covers Shop, ShopSession, ProductImport, SyncJob, AppSetting, WebhookEvent, AuditLog, SocialAccount, ScheduledReport, MiaMemory, and PublishingAction.
- AuditLog and WebhookEvent are verified and cleaned up by seeded primary key, so `ON DELETE SET NULL` cannot hide orphaned rows.
- GDPR HMAC tests restore the original shared Shopify secret in `finally` blocks.
- The earlier CodeRabbit inline finding is resolved. No independent human approval is recorded.
- This PR is test-only. CI #339 runs against main's migration 0009, not PR #27's proposed migration 0010. Rebase and rerun this regression against the signed-context migration after the security migration is integrated.

### PR #29 — publication scope health-check alignment

- PR: https://github.com/Amir2051/mia-ai/pull/29
- Head: `4bd375c1fabbcb28471f0a044f0344288dd009c2`; 8 commits ahead of main, 0 behind.
- State: open, mergeable, not merged.
- CI #279 passed all three jobs: 287 backend tests, 17 frontend tests, and production Docker build.
- The backend health-check requirement is aligned with all seven manifest scopes, including `read_publications` and `write_publications`. This does not grant scopes, release a new app configuration, or prove merchant reauthorization.
- The CodeRabbit test-coverage nitpick was addressed in the branch; no independent human approval is recorded.

### PR #25 and PR #26 — AI/social integration

- PR #25: https://github.com/Amir2051/mia-ai/pull/25 — CI #271 passed; 4 commits ahead of main, 0 behind. The full-campaign endpoint persists a `SyncJob` row and returns HTTP 202, but still dispatches execution with FastAPI `BackgroundTasks`. This is not restart-durable.
- PR #26: https://github.com/Amir2051/mia-ai/pull/26 — CI #273 passed; 3 commits ahead of main, 0 behind. The Meta test-token fallback is implemented, but live Meta OAuth and Facebook/Instagram publishing have not been accepted against production provider credentials.
- These are historical head checks, not live integration acceptance.

### PR #31 — this handoff

- PR: https://github.com/Amir2051/mia-ai/pull/31
- Branch: `docs/verified-handoff-2026-11-23`
- This update records the November 25 PR #27 findings, fixes, resolved review threads, and CI #349 evidence. It is documentation-only; it does not change application code, Shopify configuration, secrets, production database, or deployment.

## Tasks 1–8 and release blockers

1. **Shopify authentication/App Bridge:** ID-token validation, server-side token exchange, offline token persistence, session and logout code exist. The manifest still declares `/auth/callback`, while the backend auth router exposes `/api/auth/login-url`, `/api/auth/session`, and `/api/auth/logout`. Validate install/reinstall on a development store before changing this configuration.
2. **Webhooks/GDPR:** HMAC and GDPR handlers exist; PR #30 expands PostgreSQL cascade regression coverage. Real Shopify webhook deliveries, actual customer/shop redaction, uninstall/reinstall, and Protected Customer Data approval remain unverified.
3. **Products/CSV/publishing:** main has product/CSV/publication routes and the manifest declares seven scopes, but main's `shopify_scopes` default still lists only five. PR #29 aligns the branch default and required scope set with all seven, including `read_publications` and `write_publications`; it remains open and unmerged. After merge/release, obtain merchant reauthorization where required and test product/CSV/publish/unpublish workflows on a development store.
4. **AI/orders/analytics/billing:** orders, analytics, SEO/marketing generation, merchant memory, scheduled reports, OpenRouter, Meta, and Shopify Billing code exist. PR #25 still uses FastAPI process-local `BackgroundTasks` for campaign execution, so restart durability is not established; PR #26's Meta fallback is not live OAuth acceptance. Main's billing defaults are `billing_test=true` and `billing_enforced=false`; the current billing router exposes status and subscribe, but no cancellation endpoint. Live subscription creation/cancellation, webhook behavior, Meta/OpenRouter production credentials, and paid-entitlement enforcement remain unverified.
5. **PostgreSQL tenant isolation:** PR #27 has 310 passing backend tests and a successful migration rollback/re-upgrade CI sequence. Independent security approval and production role/ACL/secret/logging verification remain blockers.
6. **Docker/deployment:** production image builds pass in PR CI. Production running SHA, actual Alembic revision, role state, secrets, and current health have not been directly verified. Do not infer deployment from image builds.
7. **CI/security/governance:** latest PR #27, PR #30 and PR #31 audit commands report zero vulnerabilities. Main is unprotected; enforce required backend/frontend/Docker checks, independent review, and restrictions on direct pushes in repository settings.
8. **Production acceptance:** no complete development-store or live production acceptance record is available. Installation, auth refresh, product publishing, webhooks/GDPR, billing, Meta, OpenRouter, scheduled reports, production database state, deployment, smoke tests, and release sign-off remain outstanding.

## Immediate next actions

1. Enable and verify main branch protection and required status checks in GitHub settings.
2. Obtain independent human security review of PR #27; keep it draft and do not deploy migration 0010 until approved.
3. Wait for and assess the new CodeRabbit review requested in the PR conversation.
4. Obtain human review of PR #30, then rebase/rerun it against migration 0010 after the security migration is integrated.
5. Review PR #29 and complete a development-store scope/reauthorization and publishing test.
6. Replace PR #25 process-local campaign execution with durable workers and test restart recovery.
7. Complete development-store acceptance, then read-only verify production SHA, Alembic revision, roles/ACLs, logging, and secrets before planning a release.

## Evidence links

- CI #349 / PR #27 current head: https://github.com/Amir2051/mia-ai/actions/runs/45633010583
- CI #348 / PR #27 intermediate head (failed on the startup-test import, corrected in #349): https://github.com/Amir2051/mia-ai/actions/runs/45632768934
- CI #344 / PR #27 previous security head: https://github.com/Amir2051/mia-ai/actions/runs/45415876290
- CI #342 / PR #27 preceding security-preflight head: https://github.com/Amir2051/mia-ai/actions/runs/45414447045
- CI #341 / PR #27 failed intermediate SQL fixture: https://github.com/Amir2051/mia-ai/actions/runs/45414124943
- CI #335 / PR #27 preceding head: https://github.com/Amir2051/mia-ai/actions/runs/44754233252
- CI #339 / PR #30 current head: https://github.com/Amir2051/mia-ai/actions/runs/45192096528
- CI #345 / PR #31 previous head: https://github.com/Amir2051/mia-ai/actions/runs/45416282734
- CI #279 / PR #29: https://github.com/Amir2051/mia-ai/actions/runs/43238930678
- CI #271 / PR #25: https://github.com/Amir2051/mia-ai/actions/runs/43237067602
- CI #273 / PR #26: https://github.com/Amir2051/mia-ai/actions/runs/43237382266

---

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


## Engineering update — 2026-11-28

This section records a fresh read-only repository check on 2026-11-28. It supersedes earlier status statements only where the evidence below is newer. It does not imply that any PR was merged or any production environment was changed.

### PR #30 — GDPR shop-redaction regression

- Current head: `a227966ab983f4c77a354f8d40938d2966eb80b7`; base `main` at `59660d53d7c6b3ceeae7a584499e5ad392733fb8`.
- GitHub reports the PR open, non-draft, and mergeable. It changes one test file.
- CI #339 (2026-11-23) passed all three jobs on that exact head:
  - Backend: **285 passed, 3 warnings**.
  - Frontend: **17 passed**.
  - Production-only and full npm audits: **0 vulnerabilities**.
  - Production Docker build: **passed**.
- The updated shop-redaction test seeds and checks 11 merchant-owned record types, captures the primary keys of the WebhookEvent and AuditLog fixtures, checks for leftovers by primary key, and performs cleanup by those keys. This avoids overlooking rows whose shop_id is nulled by deletion behavior.
- The single CodeRabbit inline thread is resolved. This is regression-test evidence, not a live Shopify GDPR delivery test. PR #30 remains open and unmerged.

### PR #29 — Shopify publication-scope health check

- Current head: `4bd375c1fabbcb28471f0a044f0344288dd009c2`; base `main` at `59660d53d7c6b3ceeae7a584499e5ad392733fb8`.
- GitHub reports the PR open, non-draft, and mergeable. It changes four files.
- CI #279 (2026-11-12) passed all three jobs on that exact head:
  - Backend: **287 passed, 3 warnings**.
  - Frontend: **17 passed**.
  - Both npm audit commands reported **3 low-severity vulnerabilities**; the moderate-threshold CI gates passed. Do not describe this branch's audit as zero findings.
  - Production Docker build: **passed**.
- The current test patch compares the full default scope set with the manifest and REQUIRED_SHOPIFY_SCOPES, addressing the earlier review request to catch drift in either direction.
- This fixes the health-check's required-scope floor; it does not grant publication scopes to a merchant or deploy a new Shopify app version. PR #29 remains open and unmerged.

### PRs #25 and #26 — older feature branches

- PR #25 head `85eef38c40ca0990ccfa5c3188b17565ba41ee07`: CI #271 passed all three jobs on 2026-11-12 (backend **288 passed, 3 warnings**; frontend **17 passed**; Docker passed). Both npm audit steps reported **3 low-severity vulnerabilities**.
- PR #26 head `04edf103e0de175734949d1ed3819743c8aa1a63`: CI #273 passed all three jobs on 2026-11-12 (backend **285 passed, 3 warnings**; frontend **18 passed**; Docker passed). Both npm audit steps reported **3 low-severity vulnerabilities**.
- These are green CI results for the recorded heads, not live provider acceptance. PR #25 still uses FastAPI process-local BackgroundTasks and is not restart-durable. PR #26's test-token fallback does not prove live Meta OAuth or publishing.

### PR #27 and PR #31 — November 27 checkpoint revalidated

- PR #27 remains open, draft, mergeable, and unmerged at `400500b193c360ae54f7eaea460547f5282d0862`; it is 138 commits ahead and 0 behind main, with 13 changed files.
- CI #360 passed all three jobs on that exact head: backend **316 passed, 3 warnings**; frontend **17 passed**; both npm audits **0 vulnerabilities**; migration 0010 upgrade/downgrade/re-upgrade and production Docker build passed.
- PR #31 current head is `ea85a37b64b155820da0178fa575e7a6b010932a`; it remains open, non-draft, mergeable, and unmerged. CI #362 completed on 2026-11-28 on this exact head with all three jobs passed: backend **285 passed, 3 warnings**; frontend **17 passed**; lint and production build passed; production-only and full npm audits **0 vulnerabilities** each; production Docker build passed. This verifies the documentation-only update, not a new application-code change.
- CI #361 passed all three jobs on the previous PR #31 head `dd6998bd01ab3a757f4509740d2d878bcb6fdc4d` with the same backend/frontend/audit/Docker results.
- PR #27 has 26 inline review threads and all are marked resolved. Recorded review submissions do not include an independent APPROVE; CodeRabbit's follow-up review was rate-limited. Independent human security approval is still outstanding.

### Main and release governance

- Main remains at `59660d53d7c6b3ceeae7a584499e5ad392733fb8`.
- GitHub's branch endpoint reports `protected: false`; the repository rulesets endpoint returns an empty list. Required checks and independent approval are not enforced by branch protection.
- The connected GitHub integration can read repository metadata but does not expose a write operation for branch-protection settings. An administrator must configure and verify protection in GitHub.
- The repository still has 11 open PRs. No application PR was merged during this check.
- No production database was queried or modified, no production secrets were changed, no Shopify release was performed, and no deployment was made. Production running SHA, applied Alembic revision, role catalog state, signing-secret provisioning, database/provider/proxy parameter logging, and live merchant acceptance remain unverified.

### Immediate next actions

1. Obtain independent review and approval for PR #27; keep migration 0010 unmerged and undeployed until the production role, database identity, secret, logging, and rollback checks are approved.
2. Have a repository administrator enable required status checks and review protection on main.
3. Review and merge the test-only PR #30 and the scope-health-check PR #29 only after the normal maintainer review; then verify the merged main CI.
4. For PR #25, replace process-local BackgroundTasks with durable worker/lease/retry/stale-job recovery before claiming restart-safe execution.
5. Run real Shopify development-store acceptance for authentication, product/CSV/publishing, webhooks/GDPR, billing, and provider integrations.
