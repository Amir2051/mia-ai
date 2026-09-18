import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from app.shopify.config import settings

logger = logging.getLogger("mia_ai")


class ShopifyAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class ShopifyAPIClient:
    def __init__(self, shop_domain: str, access_token: Optional[str] = None):
        if not shop_domain:
            raise ValueError("shop_domain is required")
        self.shop_domain = shop_domain.rstrip("/")
        self.access_token = access_token
        self.base_url = f"https://{self.shop_domain}/admin/api/{settings.shopify_api_version}"

    def _require_token(self) -> str:
        if not self.access_token:
            raise ShopifyAPIError("Shopify access token is not configured")
        return self.access_token

    @staticmethod
    def _error_diagnostics(response_data: Dict[str, Any]) -> tuple[list[str], list[str]]:
        errors = response_data.get("errors") or []
        if isinstance(errors, dict):
            errors = [errors]
        messages = [
            str(error.get("message"))
            for error in errors
            if isinstance(error, dict) and error.get("message")
        ]
        codes = [
            str((error.get("extensions") or {}).get("code"))
            for error in errors
            if isinstance(error, dict) and (error.get("extensions") or {}).get("code")
        ]
        return messages[:5], codes[:5]

    async def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None, *, allow_graphql_errors: bool = False) -> Dict[str, Any]:
        token = self._require_token()
        payload: Dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/graphql.json",
                headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
                json=payload,
            )

        try:
            response_data = response.json()
        except ValueError:
            response_data = {"raw": response.text}

        if response.status_code != 200:
            messages, codes = self._error_diagnostics(response_data)
            logger.warning(
                "shopify_graphql_http_error shop=%s http_status=%s messages=%s codes=%s",
                self.shop_domain,
                response.status_code,
                messages,
                codes,
            )
            raise ShopifyAPIError(
                f"Shopify API error: HTTP {response.status_code}",
                status_code=response.status_code,
                response=response_data,
            )

        messages, codes = self._error_diagnostics(response_data)
        if messages or codes:
            logger.warning(
                "shopify_graphql_error shop=%s http_status=%s messages=%s codes=%s",
                self.shop_domain,
                response.status_code,
                messages,
                codes,
            )
            if not allow_graphql_errors:
                raise ShopifyAPIError("Shopify GraphQL error", status_code=200, response=response_data)

        data = response_data.get("data", {}) or {}
        if allow_graphql_errors and (messages or codes):
            data["_graphql_errors"] = response_data.get("errors") or []
        return data

    async def rest(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        token = self._require_token()
        url = f"{self.base_url}{path}"
        headers = {"X-Shopify-Access-Token": token}
        if body is not None:
            headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url, params=params, json=body, headers=headers)

        try:
            response_data = response.json()
        except ValueError:
            response_data = {"raw": response.text}

        if response.status_code >= 400:
            logger.warning(
                "shopify_rest_error shop=%s method=%s path=%s http_status=%s",
                self.shop_domain,
                method,
                path,
                response.status_code,
            )
            raise ShopifyAPIError(f"Shopify REST error: HTTP {response.status_code}", status_code=response.status_code, response=response_data)
        return response_data

    async def import_product(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.services.products import ProductService
        return await self.create_product(ProductService(self).map_import_to_product(payload))

    async def create_product(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        # Shopify 2026-07 productCreate accepts product fields only; variant pricing/SKU
        # must be applied to the initial variant after product creation.
        product_input = dict(input_data)
        variants = product_input.pop("variants", None) or []
        product_input.pop("productOptions", None)

        gql = """
        mutation CreateProduct($product: ProductCreateInput!) {
            productCreate(product: $product) {
                product {
                    id handle title status
                    variants(first: 1) { nodes { id } }
                }
                userErrors { field message }
            }
        }
        """
        result = await self.graphql(gql, {"product": product_input})
        product_create = result.get("productCreate") or {}
        user_errors = product_create.get("userErrors") or []
        if user_errors:
            raise ShopifyAPIError("Shopify productCreate failed", status_code=200, response={"data": result, "userErrors": user_errors})

        product = product_create.get("product") or {}
        product_id = product.get("id")
        if variants and product_id:
            variant = dict(variants[0])
            variant.pop("selectedOptions", None)
            variant.pop("optionValues", None)
            default_variant = ((product.get("variants") or {}).get("nodes") or [None])[0]
            variant_id = (default_variant or {}).get("id")
            if variant_id:
                update_input: Dict[str, Any] = {"id": variant_id}
                if "price" in variant and variant.get("price") not in (None, ""):
                    update_input["price"] = variant["price"]
                if "compareAtPrice" in variant and variant.get("compareAtPrice") not in (None, ""):
                    update_input["compareAtPrice"] = variant["compareAtPrice"]
                if "inventoryPolicy" in variant and variant.get("inventoryPolicy") not in (None, ""):
                    update_input["inventoryPolicy"] = variant["inventoryPolicy"]
                if "sku" in variant and variant.get("sku") not in (None, ""):
                    update_input["inventoryItem"] = {"sku": str(variant["sku"])}
                if len(update_input) > 1:
                    update_gql = """
                    mutation UpdateInitialVariant($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
                        productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                            product { id }
                            productVariants { id sku price compareAtPrice }
                            userErrors { field message }
                        }
                    }
                    """
                    update_result = await self.graphql(update_gql, {"productId": product_id, "variants": [update_input]})
                    update_payload = update_result.get("productVariantsBulkUpdate") or {}
                    update_errors = update_payload.get("userErrors") or []
                    if update_errors:
                        raise ShopifyAPIError("Shopify initial variant update failed", status_code=200, response={"data": update_result, "userErrors": update_errors})
                    result["productCreate"]["product"]["variantUpdate"] = update_payload
        return result

    async def update_product(self, product_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        product_input = dict(input_data)
        product_input["id"] = product_id
        gql = """
        mutation UpdateProduct($product: ProductUpdateInput!) {
            productUpdate(product: $product) {
                product { id handle title status }
                userErrors { field message }
            }
        }
        """
        result = await self.graphql(gql, {"product": product_input})
        product_update = result.get("productUpdate") or {}
        user_errors = product_update.get("userErrors") or []
        if user_errors:
            raise ShopifyAPIError("Shopify productUpdate failed", status_code=200, response={"data": result, "userErrors": user_errors})
        return result

    async def archive_product(self, product_id: str) -> Dict[str, Any]:
        return await self.update_product(product_id, {"status": "ARCHIVED"})

    async def get_product(self, product_id: str) -> Dict[str, Any]:
        gql = """
        query GetProduct($id: ID!) {
            product(id: $id) {
                id title handle descriptionHtml productType vendor status tags
                variants(first: 20) { nodes { id title sku price compareAtPrice inventoryQuantity selectedOptions { name value } } }
                media(first: 20) { nodes { id alt mediaContentType preview { image { url altText } } } }
            }
        }
        """
        return await self.graphql(gql, {"id": product_id})

    async def list_products(self, query: str = "", first: int = 250, after: Optional[str] = None) -> Dict[str, Any]:
        gql = """
        query ListProducts($query: String, $first: Int!, $after: String) {
            products(first: $first, query: $query, after: $after) {
                edges { cursor node { id title handle status totalInventory createdAt updatedAt descriptionHtml productType vendor tags variants(first: 5) { nodes { id title sku price inventoryQuantity } } } }
                pageInfo { hasNextPage endCursor }
            }
        }
        """
        return await self.graphql(gql, {"query": query or None, "first": first, "after": after})

    @staticmethod
    def verify_webhook_hmac(body: bytes, received_hmac: str) -> bool:
        if not settings.shopify_api_secret:
            return False
        digest = hmac.new(settings.shopify_api_secret.encode("utf-8"), body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode("utf-8"), received_hmac)

    @staticmethod
    def normalize_shop_domain(shop_domain: str) -> str:
        domain = shop_domain.strip()
        if domain.startswith("https://"):
            domain = domain[8:]
        if domain.startswith("http://"):
            domain = domain[7:]
        return domain.rstrip("/")

    @staticmethod
    def is_valid_shop_domain(shop_domain: str) -> bool:
        return ShopifyAPIClient.normalize_shop_domain(shop_domain).endswith(".myshopify.com")

    @staticmethod
    def parse_shop_domain(shop_domain: str) -> str:
        normalized = ShopifyAPIClient.normalize_shop_domain(shop_domain)
        if not ShopifyAPIClient.is_valid_shop_domain(normalized):
            raise ValueError("Invalid Shopify shop domain")
        return normalized

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)
