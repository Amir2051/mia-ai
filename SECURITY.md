# Mia AI — Security Review

This document distinguishes controls present on `main` from proposed controls in PR #27. Proposed controls are not production controls until the PR is independently reviewed, merged, deployed, and verified against the running database and application.

## Authentication and tenant isolation

- Embedded Shopify requests use App Bridge ID tokens validated server-side.
- Shopify issuer, destination, audience, expiry, algorithm, and shop domain are validated.
- Legacy session cookies remain a compatibility fallback and are HttpOnly, Secure, and SameSite=None in production.
- Shopify access and refresh tokens are encrypted at rest.
- Runtime PostgreSQL access uses a dedicated `mia_runtime` role with no superuser, createdb, createrole, inheritance, replication, or BYPASSRLS privileges.
- PostgreSQL Row-Level Security (RLS) isolates merchant-owned rows when the application establishes the authenticated shop context.
- On the current `main` branch, the RLS context uses transaction-local PostgreSQL settings populated by authenticated application paths. This protects normal application access, but does not by itself prevent arbitrary SQL under the shared runtime role from forging custom GUC values; that limitation is tracked in issue #23.
- Merchant-owned tables use default-deny RLS behavior when no shop context exists.

### Proposed hardening — PR #27 (not deployed)

- PR #27 replaces caller-settable tenant trust with a transaction-local shop domain plus an HMAC proof over a versioned message containing the current PostgreSQL transaction ID and normalized shop domain, generated after Shopify authentication or webhook HMAC verification. Its proposed SECURITY DEFINER policies validate the proof; changing `app.shop_domain` or `app.shop_id` alone should not change the effective tenant, and a captured proof cannot be replayed in a later transaction.
- The proposed internal scheduled-report override requires a separate HMAC proof bound to the current PostgreSQL transaction ID; a caller-settable `app.internal_scheduler=true` flag alone grants no cross-shop access, and a captured scheduler proof cannot be replayed in a later transaction.
- The proposed migration stores HMAC key material in `mia_private`, which is denied to the runtime role. The application and migration process must receive the same protected `RLS_CONTEXT_SECRET`.
- Migration 0010 on PR #27 requires a pre-provisioned `mia_rls_definer` role with `NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, no parent-role memberships, no relation/schema ownership, and no database or non-system schema CREATE privileges. The only permitted member is the dedicated migration login, which must be able to `SET ROLE` to the definer (PostgreSQL 16+ checks the membership `SET` option; the runtime role and all other non-superuser roles are excluded). Three SECURITY DEFINER functions are explicitly transferred to this restricted role; `mia_current_shop_id()` is `SECURITY INVOKER`. PostgreSQL branch tests assert function ownership and effective table/column privileges and exercise RLS as the actual definer role. This is not present on `main` and is not production-approved.
- The proposed verifier-table initializer refuses a pre-existing relation, creates a fresh table without `IF NOT EXISTS`, revokes all non-owner table ACL grants (including PUBLIC and named default-ACL grantees) before inserting the secret, and fails closed if a login role retains effective access or membership in `pg_read_all_data`/`pg_write_all_data`. PostgreSQL CI covers hostile pre-existing relations, named default-ACL grants, and LOGIN/NOINHERIT membership in both data-wide roles. These are PR-branch test results; production ACLs remain unverified.
- Before applying the proposed migration, its preflight rejects a runtime role that is superuser/BYPASSRLS, has database/role-creation/replication privileges, inherits or has any direct/transitive role membership, owns protected tables/schemas/private verifier objects or public SECURITY DEFINER functions, can create in `public`/the database, can access `mia_private`/the verifier table, or has effective TRUNCATE/REFERENCES/TRIGGER privileges on public tables (including grants to PUBLIC). TRUNCATE is not subject to RLS. CI exercises these rejection paths; production catalog verification remains mandatory.
- The proposed migration requires `DATABASE_URL` and `MIGRATION_DATABASE_URL` to name the same database and then proves they reach the same PostgreSQL database instance with a random session-level advisory-lock challenge. This compares server-side lock visibility rather than client hostnames, so a pooler URL and a direct URL can still match while two clusters with same-named databases are rejected. It also rejects runtime-role-owned SECURITY DEFINER functions in all non-system schemas and unexpected functions already owned by `mia_rls_definer`. A normalized unique index on `lower(shops.shop_domain)` is created only after the migration rejects existing case-insensitive collisions. The runtime preflight checks column-level `REFERENCES` grants as well as table-level grants.
- These controls have PostgreSQL CI coverage on PR #27, but remain unmerged and undeployed pending independent security review and production rollout verification. The scheduler's cross-shop row policy is paired with a BEFORE UPDATE trigger that positively allowlists only `next_run_at`, `last_run_at`, `last_status`, `last_error`, and `updated_at`; merchant-owned schedule definition fields remain immutable under scheduler context.

## Database security

- Production application connections use PostgreSQL, not SQLite.
- Database migrations use a separate migration connection with schema ownership privileges.
- The runtime role has DML access only; it does not own the application tables or migration metadata.
- `public` schema CREATE privilege is revoked from PUBLIC.
- Runtime access is limited to application tables and sequences required by the app.
- Shop-scoped tables include `shops`, `shop_sessions`, `product_imports`, `sync_jobs`, `webhook_events`, `app_settings`, and `audit_logs` in the RLS boundary.
- Suppliers remain global reference data and are intentionally outside merchant RLS.

## Webhook and privacy security

- Shopify webhook HMAC validation is enforced before processing.
- Unsupported webhook topics are rejected.
- Unknown shops are rejected.
- Raw Shopify webhook payloads are not retained in operational webhook or audit records.
- GDPR compliance endpoints implement `customers/data_request`, `customers/redact`, and `shop/redact`.
- Shop deletion removes shop-owned sessions, webhook events, audit records, sync jobs, imports, and settings before deleting the shop.

## HTTP security

- Production HTTPS is enforced by deployment configuration and HSTS is returned by the application.
- Content Security Policy includes Shopify Admin frame ancestors and required Shopify origins.
- `X-Content-Type-Options: nosniff` is enabled.
- `Referrer-Policy: strict-origin-when-cross-origin` is enabled.
- `Permissions-Policy` disables camera, microphone, and geolocation.
- Request bodies are limited to 5 MiB at the application middleware layer.
- Request IDs are generated for server-side tracing.

## Dependency security

- CI #294 and CI #331 temporarily surfaced 11 dependency-tree entries associated with `picomatch` 4.0.7 and GHSA-fc5q-39xc-vjpf / CVE-2026-112366. GitHub's primary advisory page now records that advisory as **withdrawn on 2026-11-18 because it is not a security issue**. The later CI #332 run (2026-11-19) reported zero production dependencies with vulnerabilities and no moderate, high, or critical findings in the full dependency-tree check. This supersedes the earlier active-advisory assessment.
- The lockfile contains `picomatch` 4.0.7 copies under Vite/Vitest tooling, all in development dependencies. The multi-stage Dockerfile copies only the built frontend `dist` directory into the Python runtime image; it does not copy Node.js dependencies into the production runtime. This records dependency provenance; the cited GitHub advisory is withdrawn.
- GitHub's advisory page states: “Withdrawn This advisory was withdrawn on Nov 18, 2026” and explains that it was withdrawn because it is not a security issue. Retain the upstream reference for audit history, but do not describe GHSA-fc5q-39xc-vjpf as an active vulnerability or wait for a patched `picomatch` release to close it.
- The PR #27 workflow now uses a direct `npm audit --audit-level=moderate` gate for the complete dependency tree alongside the separate production-only audit; the advisory-specific exception parser has been removed. CI #334 on 2026-11-20 passed both audit commands with **0 vulnerabilities reported**. The direct gate therefore has current PR-branch CI evidence and will fail on any future moderate/high/critical advisory.
- React Router is pinned to 7.18.2. Vite/Vitest and TypeScript ESLint tooling are development dependencies.

## Secret handling

- Environment files are ignored by Git.
- Production environment files are restricted to mode 0600 on the audited host.
- Secrets are never returned by API endpoints or printed in application diagnostics.
- CI generates ephemeral test encryption and RLS-context keys at runtime instead of storing reusable keys in the repository.
- If PR #27 is approved for deployment, provision a random `RLS_CONTEXT_SECRET` of at least 32 bytes in both the protected application environment and migration process before applying migration `0010_signed_tenant_context`; the migration stores its verifier copy in the restricted `mia_private` schema. This secret is not currently required by the merged `main` migration chain.

### Migration-secret logging protection
- The Alembic migration engine uses SQLAlchemy `hide_parameters=True` so bound parameter values, including `RLS_CONTEXT_SECRET`, are not included in SQLAlchemy exception strings.
- Before deploying migration 0010, verify the PostgreSQL logging configuration and any proxy/provider logging path. On PostgreSQL 16, require `log_parameter_max_length=0` for non-error statement/duration logs and `log_parameter_max_length_on_error=0` for error logs. If `auto_explain` is loaded, require `auto_explain.log_parameter_max_length=0`.
- The actual production PostgreSQL, extension, proxy, and application logging settings have not been verified; this remains a deployment gate.

## Operational notes

- The active Shopify app deployment is managed through `shopify app deploy`.
- Production migrations are run before the application starts.
- The production health endpoint is monitored separately from authenticated application APIs.
- Image-generation integrations are intentionally outside this security hardening change.


## SECURITY DEFINER owner for signed tenant context (PR #27)

Migration `0010_signed_tenant_context` requires a pre-provisioned role named `mia_rls_definer`. The migration does not create this role. Provision it in the target database before applying the migration:

```sql
CREATE ROLE mia_rls_definer
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS;
```

The migration connection must be able to `SET ROLE mia_rls_definer` so PostgreSQL permits function ownership transfer and the subsequent ACL changes. On PostgreSQL 16+, grant the dedicated migration login membership with `SET TRUE, INHERIT FALSE`; the migration role must also own the `public` schema or hold `CREATE WITH GRANT OPTION` there so it can grant and revoke the temporary schema CREATE privilege. No application/runtime role or other non-superuser role may be able to assume the definer role. The migration runs under a separate non-superuser login in CI, including downgrade and re-upgrade.

The migration fails closed unless the owner role:
- Has the required NOLOGIN/NOSUPERUSER/NOBYPASSRLS and other restricted role attributes.
- Has no memberships in other roles and is not assumable by other non-superuser roles (except the migration connection needed for ownership transfer).
- Owns no relations or schemas and has no database or schema CREATE privileges.
- Has only the effective table privileges required by the signed-context functions.

The migration grants the role SELECT on `mia_private.tenant_context_keys`, SELECT on `public.shops`, and SELECT/DELETE on `public.audit_logs`. It grants schema USAGE and EXECUTE on pgcrypto's HMAC function. A post-grant catalog check evaluates effective table and column privileges across non-system schemas and rejects any additional access. The role preflight rejects effective CREATE on every non-system schema, not just `public`. Function EXECUTE ACLs are reconciled by revoking all existing named and PUBLIC grants before granting only the explicit runtime caller set; the purge function's prior named grants cannot survive `CREATE OR REPLACE FUNCTION`.

The following functions are owned by `mia_rls_definer` and execute with its restricted privileges:
- `public.mia_current_shop_domain()`
- `public.mia_is_internal_scheduler()`
- `public.mia_purge_audit_logs_for_privacy(bigint, text, text, boolean)`

The migration grants CREATE on `public` only temporarily while transferring ownership, then revokes it and verifies the definer role no longer has effective CREATE. `public.mia_current_shop_id()` is explicitly SECURITY INVOKER: it queries `shops` under the caller's role and the signed-domain RLS policy, avoiding a table-owner bypass.

PostgreSQL CI on PR #27 verifies function owners and SECURITY DEFINER flags, role attributes, exact table/column privileges, the migration-only SET membership, and cross-tenant read/delete denial when the actual function-owner role is active. CI #334 on 2026-11-20 passed **305 backend tests (3 warnings), 17 frontend tests, the PostgreSQL downgrade/re-upgrade check, both npm audit commands with zero vulnerabilities reported, and the production Docker build** on the current workflow/documentation head. The migration upgrade, downgrade to 0009, and re-upgrade ran under the restricted non-superuser migration login. This is PR-branch CI evidence, not production verification.

These results are branch CI evidence only. Before production, independently verify the actual production role catalog, role memberships, function owners, runtime privileges, schema/table ACLs, and logging configuration. Provision `RLS_CONTEXT_SECRET` in the app and migration environments only after approval. The downgrade restores the legacy caller-settable GUC policies and must be treated as a security-sensitive rollback.



## Latest PR-branch verification — 2026-11-19

CI #305 on code/test head `5df9b2b6e0eb7cd2a90d113a21b94d546baa780e` passed all three jobs: **298 backend tests passed (3 warnings), 17 frontend tests passed, lint/build passed, PostgreSQL migration downgrade/re-upgrade passed, production-only npm audit reported zero vulnerabilities, the documented dev-only picomatch exception checker passed, and the production Docker image built successfully**. This is PR-branch CI evidence only; PR #27 remains draft and unmerged, and production roles, secrets, logging, database revision, and deployment remain unverified.


## Latest PR-branch verification — 2026-11-19

CI #331 on code commit `05233f192a86bdae9eb223ed7e625d2019b269a7` passed all three GitHub Actions jobs: **305 backend tests passed (3 warnings), 17 frontend tests passed, frontend lint/build passed, the production-only npm audit reported zero vulnerabilities, the full-tree checker accepted only the documented 11 dev-only picomatch advisory entries, and the production Docker image built successfully**. Importantly, migration 0010 upgrade, downgrade to 0009, and re-upgrade all ran under the restricted non-superuser migration login with PostgreSQL 16 SET-only membership.

The downgrade preflight now allows only the expected `USAGE` on `mia_private` after installation; it continues to reject schema CREATE and checks the definer role's exact table/column privileges before rollback. The pre-upgrade check still rejects pre-existing private-schema access. This closes the CI #330 rollback-preflight failure in the PR branch.

This remains PR-branch evidence only. PR #27 is still draft and unmerged; independent human security approval and direct production catalog/logging/secret verification remain mandatory. The full npm dependency tree still carries the documented temporary development-only picomatch advisory exception; do not describe the full audit as clean.
