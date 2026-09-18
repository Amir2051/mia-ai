# Mia AI — Security Review

This document describes the production security controls currently implemented.

## Authentication and tenant isolation

- Embedded Shopify requests use App Bridge ID tokens validated server-side.
- Shopify issuer, destination, audience, expiry, algorithm, and shop domain are validated.
- Legacy session cookies remain a compatibility fallback and are HttpOnly, Secure, and SameSite=None in production.
- Shopify access and refresh tokens are encrypted at rest.
- Runtime PostgreSQL access uses a dedicated `mia_runtime` role with no superuser, createdb, createrole, inheritance, replication, or BYPASSRLS privileges.
- PostgreSQL Row-Level Security (RLS) isolates merchant-owned rows by the authenticated shop context.
- RLS context is transaction-local and is populated only after Shopify authentication or webhook HMAC verification.
- Merchant-owned tables use default-deny RLS behavior when no shop context exists.

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

- Production npm dependencies pass `npm audit --omit=dev` with zero known vulnerabilities at this checkpoint.
- React Router was upgraded to the current 7.18.x security line.
- Development tooling was upgraded to current Vite/Vitest releases so the full npm audit is clean at this checkpoint.

## Secret handling

- Environment files are ignored by Git.
- Production environment files are restricted to mode 0600 on the audited host.
- Secrets are never returned by API endpoints or printed in application diagnostics.
- CI generates its test encryption key at runtime instead of storing a reusable key in the repository.

## Operational notes

- The active Shopify app deployment is managed through `shopify app deploy`.
- Production migrations are run before the application starts.
- The production health endpoint is monitored separately from authenticated application APIs.
- Image-generation integrations are intentionally outside this security hardening change.
