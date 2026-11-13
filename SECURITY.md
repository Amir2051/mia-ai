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

- The configured npm audit gates pass at the moderate-severity threshold. The latest recorded audit still reports three low-severity `deepmerge` findings through the deprecated Polaris React dependency; this is not a zero-vulnerability result.
- React Router was upgraded to the current 7.18.x security line.
- Development tooling was upgraded to current Vite/Vitest releases; the three low-severity `deepmerge` findings listed above remain, so the full npm audit is not clean.

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
