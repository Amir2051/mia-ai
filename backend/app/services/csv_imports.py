from typing import Any, Dict, List, Optional

from app.models.schemas import ProductImport
from app.services.products import ImportService


VALID_DUPLICATE_ACTIONS = {"skip", "update", "create", "fail"}


class CsvImportService(ImportService):
    """Production CSV import execution with deterministic duplicate handling."""

    @staticmethod
    def normalize_duplicate_action(value: Optional[str]) -> str:
        action = (value or "skip").strip().lower()
        if action not in VALID_DUPLICATE_ACTIONS:
            raise ValueError("duplicate_action must be one of: skip, update, create, fail")
        return action

    @staticmethod
    def _escape_search_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').strip()

    async def find_duplicate_product(self, mapped: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find an existing Shopify product, preferring an exact SKU match over title."""
        if self.api_client is None:
            return None
        sku = str(mapped.get("sku") or "").strip()
        title = str(mapped.get("title") or "").strip()
        queries: List[tuple[str, str]] = []
        if sku:
            queries.append(("sku", f"sku:{self._escape_search_value(sku)}"))
        if title:
            queries.append(("title", f"title:{self._escape_search_value(title)}"))

        gql = """
        query FindImportDuplicate($query: String!) {
            products(first: 10, query: $query) {
                nodes {
                    id
                    title
                    variants(first: 20) { nodes { sku } }
                }
            }
        }
        """
        normalized_title = title.casefold()
        normalized_sku = sku.casefold()
        title_candidate = None

        for kind, query in queries:
            result = await self.api_client.graphql(gql, {"query": query})
            products = ((result.get("products") or {}).get("nodes") or [])
            for product in products:
                product_title = str(product.get("title") or "").strip()
                variant_nodes = ((product.get("variants") or {}).get("nodes") or [])
                skus = {str(v.get("sku") or "").strip().casefold() for v in variant_nodes if v.get("sku")}
                if kind == "sku" and normalized_sku and normalized_sku in skus:
                    return {"id": product.get("id"), "title": product_title, "match": "sku"}
                if kind == "title" and normalized_title and product_title.casefold() == normalized_title:
                    title_candidate = {"id": product.get("id"), "title": product_title, "match": "title"}
        return title_candidate

    @staticmethod
    def _prepare_product(mapped: Dict[str, Any], default_status: str) -> Dict[str, Any]:
        product = dict(mapped)
        product["status"] = default_status
        variant_fields = {"sku", "price", "compareAtPrice", "inventoryQuantity", "weight", "inventoryPolicy", "fulfillmentService"}
        if any(key in product for key in variant_fields):
            variant_input: Dict[str, Any] = {}
            for key in variant_fields:
                if key in product:
                    variant_input[key] = product.pop(key)
            product.setdefault("productOptions", [{"name": "Title"}])
            variant_input["selectedOptions"] = [{"name": "Title", "value": "Default Title"}]
            product["variants"] = [variant_input]
        return product

    @staticmethod
    def _product_update_input(prepared: Dict[str, Any]) -> Dict[str, Any]:
        """Only send fields supported by ProductUpdateInput during duplicate updates."""
        allowed = {"title", "descriptionHtml", "vendor", "productType", "tags", "status"}
        return {key: value for key, value in prepared.items() if key in allowed}

    async def _execute_csv(self, record: ProductImport, content: str, mapping: Dict[str, str], duplicate_action: str, default_status: str, validate_only: bool) -> Dict[str, Any]:
        parsed = self.parse_csv(content)
        validation = self.validate_import_rows(parsed["rows"], mapping)
        created = updated = failed = skipped = processed = 0
        details: List[Dict[str, Any]] = []
        shopify_product_ids: List[str] = []

        for item in validation["valid"]:
            processed += 1
            source_mapped = dict(item["mapped"])
            duplicate = None
            if not validate_only and self.api_client is not None:
                duplicate = await self.find_duplicate_product(source_mapped)
            prepared = self._prepare_product(source_mapped, default_status)

            if validate_only or self.api_client is None:
                details.append({"row": item["row"], "status": "preview", "mapped": prepared})
                continue

            try:
                if duplicate:
                    duplicate_id = duplicate.get("id")
                    match = duplicate.get("match")
                    if duplicate_action == "skip":
                        skipped += 1
                        if duplicate_id:
                            shopify_product_ids.append(duplicate_id)
                        details.append({"row": item["row"], "status": "skipped", "reason": "duplicate", "duplicate_match": match, "shopify_product_id": duplicate_id, "mapped": prepared})
                        continue
                    if duplicate_action == "fail":
                        failed += 1
                        details.append({"row": item["row"], "status": "failed", "error": f"Duplicate product found by {match}", "duplicate_match": match, "shopify_product_id": duplicate_id, "mapped": prepared})
                        continue
                    if duplicate_action == "update":
                        if not duplicate_id:
                            raise ValueError("Duplicate product was found without a Shopify product id")
                        await self.api_client.update_product(duplicate_id, self._product_update_input(prepared))
                        updated += 1
                        shopify_product_ids.append(duplicate_id)
                        details.append({"row": item["row"], "status": "updated", "duplicate_match": match, "shopify_product_id": duplicate_id, "mapped": prepared})
                        continue
                    # create intentionally falls through.

                result = await self.api_client.create_product(prepared)
                product = ((result.get("productCreate") or {}).get("product") or {})
                product_id = product.get("id")
                if not product_id:
                    failed += 1
                    details.append({"row": item["row"], "status": "failed", "error": "Shopify create_product succeeded but returned no product id", "shopify_product_id": None, "mapped": prepared})
                    continue
                created += 1
                shopify_product_ids.append(product_id)
                details.append({"row": item["row"], "status": "created", "shopify_product_id": product_id, "mapped": prepared})
            except Exception as exc:  # noqa: BLE001
                failed += 1
                details.append({"row": item["row"], "status": "failed", "error": str(exc), "shopify_product_id": None, "mapped": prepared})

        for item in validation["errors"]:
            processed += 1
            skipped += 1
            details.append({"row": item["row"], "status": "skipped", "error": item["errors"]})

        status, sync_status = (("validated", "preview") if validate_only else (("completed_with_errors", "completed") if failed else ("completed", "completed")))
        record.status = status
        record.sync_status = sync_status
        record.error = None
        record.shopify_product_id = ",".join(dict.fromkeys(shopify_product_ids)) or None
        await self.db.commit()
        await self.db.refresh(record)

        return {"import": {"id": record.id, "status": record.status, "sync_status": record.sync_status, "duplicate_action": duplicate_action, "summary": {"processed": processed, "created": created, "updated": updated, "failed": failed, "skipped": skipped, **validation["summary"]}, "details": details}}

    async def create_import_from_csv(self, shop_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        duplicate_action = self.normalize_duplicate_action(payload.get("duplicate_action"))
        record = ProductImport(shop_id=shop_id, source="csv", supplier_sku=None, title=payload.get("title"), description=payload.get("description"), images=payload.get("images"), cost=payload.get("cost"), retail_price=payload.get("retail_price"), variants_json=payload.get("variants_json"), category=payload.get("category"), tags=",".join(str(tag) for tag in payload.get("tags") or [] if str(tag).strip()), inventory=payload.get("inventory"), status="pending", sync_status="pending", error=None)
        self.db.add(record)
        await self.db.flush()
        await self.db.refresh(record)
        return await self._execute_csv(record, payload.get("content") or "", payload.get("mapping") or {}, duplicate_action, payload.get("status") or "DRAFT", bool(payload.get("validate_only")))

    async def update_import_from_csv(self, import_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        duplicate_action = self.normalize_duplicate_action(payload.get("duplicate_action"))
        result = await self.db.execute(ProductImport.__table__.select().where(ProductImport.id == import_id, ProductImport.shop_id == self.shop.id))
        if not result.mappings().first():
            raise ValueError("Import not found")
        record = await self.db.get(ProductImport, import_id)
        return await self._execute_csv(record, payload.get("content") or "", payload.get("mapping") or {}, duplicate_action, payload.get("status") or "DRAFT", bool(payload.get("validate_only")))
