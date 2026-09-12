# Mia AI — Security Review

This document summarizes the security posture of the local build as of this checkpoint.

## Positive Controls Implemented

- `.env` files are ignored by git and treated as development-only secrets.
- Shopify credentials are loaded from `backend/.env` via `pydantic-settings`; no secrets are hard-coded in source.
- Backend does not expose secrets in API responses.
- Merchant isolation is modeled at the shop level (`Shop.shop_domain`, per-shop scoping).
- Input validation exists in service stubs (`ImportService.validate_import_payload`).
- Access token handling is implemented via JWT abstraction with expiration.
- Webhook validation interfaces are defined and marked `NotImplemented` until secrets are configured.

## Pending Hardening Before Production

- Replace placeholder secrets with real values via environment configuration.
- Implement real Shopify OAuth token exchange, validation, and session persistence.
- Implement HMAC webhook verification once `SHOPIFY_API_SECRET` is supplied.
- Migrate dev database from SQLite to production PostgreSQL.
- Add rate limiting and request size limits on public endpoints.
- Add HTTPS enforcement and secure cookie flags when hosted behind TLS.
- Audit all Shopify Admin API calls for least-scope permissions.
- Remove or secure any debug routes before production.

## Confirmed

- No SafeNestT systems were accessed or modified.
- No production Shopify store was touched.
- No credentials were invented or committed.
