# Mia publishing approval workflow (Phase 4)

This backend workflow covers Shopify product-to-sales-channel publishing and Meta Facebook/Instagram publishing. It is intentionally backend-only in this phase; the frontend must be updated before merchants can use the new flow end-to-end.

## Lifecycle

1. `POST /api/publishing/requests` stores a validated request as `pending_approval`. Include an `Idempotency-Key` in the JSON body (`idempotency_key`) and the action payload.
2. The merchant reviews the stored payload with `GET /api/publishing/requests/{id}` and explicitly approves it with `POST /api/publishing/requests/{id}/approve`.
3. `POST /api/publishing/requests/{id}/execute` atomically claims an `approved` request as `publishing` before any external API call. Only the request that wins the conditional update can execute.
4. Confirmed success becomes `published`. A known provider rejection becomes `failed`. A timeout, unexpected response, or uncertain outcome becomes `needs_review`; neither `failed` nor `needs_review` can be retried through execute. Resolve uncertain outcomes by checking the destination manually and create a new request only after confirming it is safe.

Allowed states: `pending_approval` → `approved` → `publishing` → `published`, `failed`, or `needs_review`. Re-approval, execution before approval, and repeated execution are rejected with HTTP 409. Reusing the same idempotency key and same normalized request returns the existing request; reusing the key for a different request returns HTTP 409. Requests and status lookups are scoped to the authenticated shop.

## Supported action payloads

- `shopify_product_channels`: `product_id` (Shopify Product GID), `publication_ids` (non-empty list of Publication GIDs), `publish` (boolean).
- `meta_facebook`: `account_id`, `message`, optional `link`.
- `meta_instagram`: `account_id`, public HTTPS `image_url`, optional `caption` (up to 2,200 characters).

The persisted request must contain the content needed for execution, but audit entries only record action type, status, destination/result identifiers and limited metadata—not post text, access tokens or secrets. Provider results are reduced to safe identifiers. Direct legacy mutation endpoints for these covered destinations return HTTP 409 and must not be used to publish.

## Operational notes

Apply Alembic revision `0008_publishing_actions` through the normal reviewed deployment/migration process before deploying the code. No migration or deployment is run automatically by this change. A process crash after the request enters `publishing` can leave it there; this is deliberate fail-closed behavior to avoid accidental duplicate posts. Inspect the provider and reconcile manually rather than retrying blindly. Frontend approval UX, production provider-permission verification, and integration tests against a controlled non-production store remain follow-up work.
