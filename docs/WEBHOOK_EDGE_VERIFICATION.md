# Webhook Edge Verification

## Current production probe

On 2026-09-18, https://drivenest.info/api/webhooks was tested through the public Cloudflare edge with the Shopify webhook user-agent:

- User-Agent: Shopify-Captain-Hook
- X-Shopify-Topic: products/create
- X-Shopify-Shop-Domain: safenestt.myshopify.com
- X-Shopify-Event-Id: edge-test-20260918
- intentionally invalid HMAC

The response was **HTTP 401** with the application JSON body `{"detail":"Invalid webhook signature"}` and an `X-Request-ID` header. This is important: the request reached FastAPI and was rejected by the app's HMAC verification, rather than receiving a Cloudflare managed challenge/403.

A normal browser user-agent to the same path also reached the application.

## Cloudflare configuration finding

The Cloudflare API token available on the deployment host can identify the active drivenest.info zone and inspect the existing rate-limit ruleset, but it does not have permission to read the zone-level custom/managed WAF rulesets. The API therefore could not be used from this environment to create or verify a WAF skip rule.

Cloudflare's current recommended mechanism is a **Skip** custom rule for legitimate automated API traffic. A rule for this endpoint should be narrowly scoped to `/api/webhooks`; if a user-agent condition is also used, it should be combined with other Shopify-specific request characteristics rather than trusting a user-agent alone. Cloudflare documents that skip rules can bypass managed rules, Super Bot Fight Mode, and rate limiting, and that rule ordering matters.

For the current edge state, the live probe above is the evidence that Shopify-style webhook traffic is not presently being challenged at the edge. A real Shopify subscription delivery should still be confirmed in Shopify's Dev Dashboard/Monitoring delivery logs before production rollout; Shopify notes that the delivery log records the response code returned by the app.
