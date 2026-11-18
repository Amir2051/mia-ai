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
- Migration 0010 on PR #27 now requires a pre-provisioned `mia_rls_definer` role with `NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`, no role memberships, no relation/schema ownership, and no database or non-system schema CREATE privileges. Three SECURITY DEFINER functions are explicitly transferred to this restricted role; `mia_current_shop_id()` is `SECURITY INVOKER`. PostgreSQL branch tests assert function ownership and effective table/column privileges and exercise RLS as the actual definer role. This is not present on `main` and is not production-approved.
- The proposed verifier-table initializer refuses a pre-existing relation, creates a fresh table without `IF NOT EXISTS`, revokes all non-owner table ACL grants (including PUBLIC and named default-ACL grantees) before inserting the secret, and fails closed if a login role retains effective access or membership in `pg_read_all_data`/`pg_write_all_data`. PostgreSQL CI covers hostile pre-existing relations, named default-ACL grants, and LOGIN/NOINHERIT membership in both data-wide roles. These are PR-branch test results; production ACLs remain unverified.
- Before applying the proposed migration, its preflight rejects a runtime role that is superuser/BYPASSRLS, has database/role-creation/replication privileges, inherits or has any direct/transitive role membership, owns protected tables/schemas/private verifier objects or public SECURITY DEFINER functions, can create in `public`/the database, can access `mia_private`/the verifier table, or has effective TRUNCATE/REFERENCES/TRIGGER privileges on public tables (including grants to PUBLIC). TRUNCATE is not subject to RLS. CI exercises these rejection paths; production catalog verification remains mandatory.
- These controls have PostgreSQL CI coverage on PR #27, but remain unmerged and undeployed pending independent security review and production rollout verification.

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

- PR #27 CI #292 (2026-11-17) reported zero findings in both configured npm audit commands. On 2026-11-18, a refreshed registry advisory caused the full dependency audit to report **11 high-severity dependency-tree entries**, all tracing to the single \`picomatch\` advisory GHSA-fc5q-39xc-vjpf / CVE-2026-112366. The separate production-only audit still reports zero vulnerabilities.
- The affected \`picomatch\` 4.0.7 copies are locked as development dependencies under Vite/Vitest/tooling. The multi-stage Dockerfile copies only the built frontend \`dist\` directory into the Python runtime image; it does not copy Node.js dependencies into the production runtime. The finding is therefore a build/development-tooling risk, not a shipped runtime dependency in the current image design.
- The GitHub advisory currently lists versions through 4.0.7 as affected and does not list a patched version. The upstream maintainer has publicly disputed the report and advised against allowing untrusted users to define glob patterns; an attempted fix PR was closed without merging. Do not silently suppress the finding or claim the full audit is clean.
- PR #27 now uses a narrow, fail-closed audit exception script: the production-only audit must pass; every moderate/high/critical full-tree entry must trace only to this exact GHSA; every locked \`picomatch\` copy must remain version 4.0.7 and \`dev: true\`; incomplete audit JSON or any additional advisory fails CI. Remove the exception when an upstream fixed release is available or the risk assessment changes. CI #294 failed before this exception was added; the rerun is pending.
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

The migration connection must be a superuser or a member of `mia_rls_definer` so PostgreSQL permits function ownership transfer. If the migration connection is not a superuser, grant membership to that migration role only. No application/runtime role or other non-superuser role may be able to `SET ROLE mia_rls_definer`.

The migration fails closed unless the owner role:
- Has the required NOLOGIN/NOSUPERUSER/NOBYPASSRLS and other restricted role attributes.
- Has no memberships in other roles and is not assumable by other non-superuser roles (except the migration connection needed for ownership transfer).
- Owns no relations or schemas and has no database or schema CREATE privileges.
- Has only the effective table privileges required by the signed-context functions.

The migration grants the role SELECT on `mia_private.tenant_context_keys`, SELECT on `public.shops`, and SELECT/DELETE on `public.audit_logs`. It grants schema USAGE and EXECUTE on pgcrypto's HMAC function. A post-grant catalog check evaluates effective table and column privileges across non-system schemas and rejects any additional access. The role preflight rejects effective CREATE on every non-system schema, not just `public`.

The following functions are owned by `mia_rls_definer` and execute with its restricted privileges:
- `public.mia_current_shop_domain()`
- `public.mia_is_internal_scheduler()`
- `public.mia_purge_audit_logs_for_privacy(bigint, text, text, boolean)`

The migration grants CREATE on `public` only temporarily while transferring ownership, then revokes it and verifies the definer role no longer has effective CREATE. `public.mia_current_shop_id()` is explicitly SECURITY INVOKER: it queries `shops` under the caller's role and the signed-domain RLS policy, avoiding a table-owner bypass.

PostgreSQL CI on PR #27 verifies the function owners and SECURITY DEFINER flags, role attributes, exact table privileges, absence of runtime membership, and cross-tenant read/delete denial when the actual function-owner role is active. CI #290 on 2026-11-17 passed **294 backend tests, 17 frontend tests, the PostgreSQL downgrade/re-upgrade check, and the production Docker build**. On 2026-11-18, CI #294 passed the backend suite with **295 tests** after adding schema-CREATE and column-only-grant regressions, but the frontend full-tree audit failed on the newly surfaced picomatch advisory. A narrow dev-only exception policy was added afterward; its CI rerun is pending.

These results are branch CI evidence only. Before production, independently verify the actual production role catalog, role memberships, function owners, runtime privileges, schema/table ACLs, and logging configuration. Provision `RLS_CONTEXT_SECRET` in the app and migration environments only after approval. The downgrade restores the legacy caller-settable GUC policies and must be treated as a security-sensitive rollback.

