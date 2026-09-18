from typing import Any, Dict, List, Optional
import json

from app.models.schemas import ProductImport, Shop
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError


class ProductService:
    def __init__(self, api_client: ShopifyAPIClient):
        self.api = api_client

    async def list_products(
        self,
        query: str = "",
        first: int = 250,
        after: Optional[str] = None,
    ) -> dict:
        gql = """
        query ListProducts(
            $query: String
            $first: Int!
            $after: String
        ) {
            products(
                first: $first
                query: $query
                after: $after
            ) {
                edges {
                    cursor
                    node {
                        id
                        title
                        handle
                        status
                        totalInventory
                        createdAt
                        updatedAt
                        descriptionHtml
                        productType
                        vendor
                        tags
                        variants(first: 5) {
                            edges {
                                node {
                                    id
                                    sku
                                    price
                                    inventoryQuantity
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """

        variables = {
            "query": query or None,
            "first": first,
            "after": after,
        }

        return await self.api.graphql(gql, variables)

    async def get_product(self, product_id: str) -> dict:
        gql = """
        query GetProduct($id: ID!) {
            product(id: $id) {
                id
                title
                handle
                descriptionHtml
                productType
                vendor
                tags
                status
                variants(first: 250) {
                    edges {
                        cursor
                        node {
                            id
                            sku
                            price
                            compareAtPrice
                            inventoryQuantity
                            selectedOptions { name value }
                        }
                    }
                    pageInfo { hasNextPage endCursor }
                }
                images(first: 250) {
                    edges {
                        cursor
                        node { id url altText }
                    }
                    pageInfo { hasNextPage endCursor }
                }
                metafields(first: 250) {
                    edges {
                        cursor
                        node { namespace key type value }
                    }
                    pageInfo { hasNextPage endCursor }
                }
            }
        }
        """

        result = await self.api.graphql(gql, {"id": product_id})
        product = result.get("product") or {}
        if not product:
            return result

        async def collect_connection(
            field: str,
            page_query: str,
        ) -> list[dict]:
            connection = product.get(field) or {}
            items = list(connection.get("edges") or [])
            after = (connection.get("pageInfo") or {}).get("endCursor")
            has_next = bool((connection.get("pageInfo") or {}).get("hasNextPage"))
            while has_next and after:
                page = await self.api.graphql(page_query, {"id": product_id, "after": after})
                page_product = page.get("product") or {}
                page_connection = page_product.get(field) or {}
                items.extend(page_connection.get("edges") or [])
                page_info = page_connection.get("pageInfo") or {}
                next_after = page_info.get("endCursor")
                if not page_info.get("hasNextPage") or not next_after or next_after == after:
                    break
                after = next_after
                has_next = True
            return items

        variants_query = """
        query ProductVariantsPage($id: ID!, $after: String) {
            product(id: $id) {
                variants(first: 250, after: $after) {
                    edges { cursor node { id sku price compareAtPrice inventoryQuantity selectedOptions { name value } } }
                    pageInfo { hasNextPage endCursor }
                }
            }
        }
        """
        images_query = """
        query ProductImagesPage($id: ID!, $after: String) {
            product(id: $id) {
                images(first: 250, after: $after) {
                    edges { cursor node { id url altText } }
                    pageInfo { hasNextPage endCursor }
                }
            }
        }
        """
        metafields_query = """
        query ProductMetafieldsPage($id: ID!, $after: String) {
            product(id: $id) {
                metafields(first: 250, after: $after) {
                    edges { cursor node { namespace key type value } }
                    pageInfo { hasNextPage endCursor }
                }
            }
        }
        """

        product["variants"]["edges"] = await collect_connection("variants", variants_query)
        product["variants"]["pageInfo"] = {"hasNextPage": False, "endCursor": None}
        product["images"]["edges"] = await collect_connection("images", images_query)
        product["images"]["pageInfo"] = {"hasNextPage": False, "endCursor": None}
        product["metafields"]["edges"] = await collect_connection("metafields", metafields_query)
        product["metafields"]["pageInfo"] = {"hasNextPage": False, "endCursor": None}
        return result

    async def create_product(
        self,
        input_data: dict,
    ) -> dict:
        gql = """
        mutation CreateProduct($product: ProductCreateInput!) {
            productCreate(product: $product) {
                product {
                    id
                    handle
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """

        return await self.api.graphql(
            gql,
            {"product": input_data},
        )

    async def update_product(
        self,
        product_id: str,
        input_data: dict,
    ) -> dict:
        # Shopify productUpdate handles product-level fields only. Variant price and
        # inventory must be updated through the variant/inventory mutations.
        product_input = dict(input_data)
        variants = product_input.pop("variants", None) or []
        product_input["id"] = product_id

        gql = """
        mutation UpdateProduct($product: ProductUpdateInput!) {
            productUpdate(product: $product) {
                product {
                    id
                    handle
                    title
                    status
                    productType
                    vendor
                    tags
                    descriptionHtml
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """

        result = await self.api.graphql(gql, {"product": product_input})
        product_update = result.get("productUpdate") or {}
        user_errors = product_update.get("userErrors") or []
        if user_errors:
            raise ShopifyAPIError(
                "Shopify productUpdate failed",
                status_code=200,
                response={"data": result, "userErrors": user_errors},
            )

        if variants:
            variant_updates = []
            for variant in variants:
                variant_id = variant.get("id")
                if not variant_id:
                    continue
                variant_input = {"id": variant_id}
                if "price" in variant and variant.get("price") not in (None, ""):
                    variant_input["price"] = variant["price"]
                if "compareAtPrice" in variant and variant.get("compareAtPrice") not in (None, ""):
                    variant_input["compareAtPrice"] = variant["compareAtPrice"]
                if "sku" in variant and variant.get("sku") not in (None, ""):
                    variant_input["inventoryItem"] = {"sku": str(variant["sku"])}
                if len(variant_input) > 1:
                    variant_updates.append(variant_input)

            if variant_updates:
                variant_gql = """
                mutation UpdateVariants($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
                    productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                        product { id }
                        productVariants { id sku price compareAtPrice }
                        userErrors { field message }
                    }
                }
                """
                variant_result = await self.api.graphql(
                    variant_gql,
                    {"productId": product_id, "variants": variant_updates},
                )
                variant_payload = variant_result.get("productVariantsBulkUpdate") or {}
                variant_errors = variant_payload.get("userErrors") or []
                if variant_errors:
                    raise ShopifyAPIError(
                        "Shopify variant update failed",
                        status_code=200,
                        response={"data": variant_result, "userErrors": variant_errors},
                    )
                result["productVariantsBulkUpdate"] = variant_payload

            # inventoryQuantity is not a ProductVariantsBulkInput field. It is an
            # inventory-level value, so resolve the variant's inventory item/location
            # and update it with inventorySetQuantities.
            quantity_variants = [v for v in variants if v.get("id") and "inventoryQuantity" in v and v.get("inventoryQuantity") not in (None, "")]
            if quantity_variants:
                inventory_gql = """
                query VariantInventory($id: ID!) {
                    productVariant(id: $id) {
                        id
                        inventoryItem {
                            id
                            inventoryLevels(first: 250) {
                                nodes { location { id } }
                            }
                        }
                    }
                }
                """
                inventory_quantities = []
                for variant in quantity_variants:
                    variant_result = await self.api.graphql(inventory_gql, {"id": variant["id"]})
                    node = (variant_result.get("productVariant") or {})
                    inventory_item = node.get("inventoryItem") or {}
                    inventory_item_id = inventory_item.get("id")
                    levels = ((inventory_item.get("inventoryLevels") or {}).get("nodes") or [])
                    if inventory_item_id:
                        for level in levels:
                            location_id = ((level.get("location") or {}).get("id"))
                            if location_id:
                                inventory_quantities.append({
                                    "inventoryItemId": inventory_item_id,
                                    "locationId": location_id,
                                    "quantity": int(variant["inventoryQuantity"]),
                                })
                if inventory_quantities:
                        set_inventory_gql = """
                        mutation SetInventory($input: InventorySetQuantitiesInput!) {
                            inventorySetQuantities(input: $input) {
                                inventoryAdjustmentGroup { reason
                                    changes { name delta }
                                }
                                userErrors { field message }
                            }
                        }
                        """
                        inv_result = await self.api.graphql(set_inventory_gql, {
                            "input": {
                                "name": "available",
                                "reason": "correction",
                                "ignoreCompareQuantity": True,
                                "quantities": inventory_quantities,
                            }
                        })
                        inv_payload = inv_result.get("inventorySetQuantities") or {}
                        inv_errors = inv_payload.get("userErrors") or []
                        if inv_errors:
                            raise ShopifyAPIError(
                                "Shopify inventory update failed",
                                status_code=200,
                                response={"data": inv_result, "userErrors": inv_errors},
                            )
                        result["inventorySetQuantities"] = inv_payload

        return result

    async def archive_product(
        self,
        product_id: str,
    ) -> dict:
        return await self.update_product(
            product_id,
            {
                "status": "ARCHIVED",
            },
        )

    def map_import_to_product(
        self,
        import_record: dict[str, Any],
    ) -> dict[str, Any]:
        tags = import_record.get("tags") or []

        if isinstance(tags, str):
            tags = [
                tag.strip()
                for tag in tags.split(",")
                if tag.strip()
            ]
        else:
            tags = [
                str(tag).strip()
                for tag in tags
                if str(tag).strip()
            ]

        product: dict[str, Any] = {
            "title": import_record.get("title"),
            "descriptionHtml": import_record.get("description"),
            "vendor": import_record.get("supplier"),
            "productType": import_record.get("category"),
            "tags": tags,
            "status": "DRAFT",
        }

        return {
            key: value
            for key, value in product.items()
            if value is not None
        }


class ImportService:
    def __init__(self, db_session, shop: Optional[Shop] = None, api_client: Optional[ShopifyAPIClient] = None):
        self.db = db_session
        self.shop = shop
        self.api_client = api_client

    async def list_imports(self) -> Dict[str, Any]:
        result = await self.db.execute(
            ProductImport.__table__.select()
            .where(ProductImport.shop_id == self.shop.id)
            .order_by(ProductImport.created_at.desc())
        )
        rows = result.mappings().all()
        return {
            "imports": [
                {
                    "id": row["id"],
                    "source": row["source"],
                    "supplier_sku": row["supplier_sku"],
                    "title": row["title"],
                    "status": row["status"],
                    "sync_status": row["sync_status"],
                    "error": row["error"],
                    "shopify_product_id": row["shopify_product_id"],
                    "created_at": (
                        row["created_at"].isoformat()
                        if row.get("created_at")
                        else None
                    ),
                }
                for row in rows
            ]
        }

    async def get_import(self, import_id: int) -> Optional[Dict[str, Any]]:
        result = await self.db.execute(
            ProductImport.__table__.select()
            .where(
                ProductImport.id == import_id,
                ProductImport.shop_id == self.shop.id,
            )
        )
        row = result.mappings().first()
        if not row:
            return None

        return {
            "id": row["id"],
            "source": row["source"],
            "supplier_sku": row["supplier_sku"],
            "title": row["title"],
            "description": row["description"],
            "images": row["images"],
            "cost": float(row["cost"]) if row.get("cost") is not None else None,
            "retail_price": (
                float(row["retail_price"])
                if row.get("retail_price") is not None
                else None
            ),
            "variants_json": row["variants_json"],
            "category": row["category"],
            "tags": row["tags"],
            "inventory": row["inventory"],
            "status": row["status"],
            "sync_status": row["sync_status"],
            "error": row["error"],
            "shopify_product_id": row["shopify_product_id"],
            "created_at": (
                row["created_at"].isoformat()
                if row.get("created_at")
                else None
            ),
            "updated_at": (
                row["updated_at"].isoformat()
                if row.get("updated_at")
                else None
            ),
        }

    def validate_import_payload(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        errors: List[str] = []

        if not payload.get("title") and not payload.get("supplier_sku"):
            errors.append("title or supplier_sku is required")

        if payload.get("title") and len(str(payload.get("title", ""))) > 500:
            errors.append("title must be 500 characters or fewer")

        if payload.get("category") and len(str(payload.get("category", ""))) > 255:
            errors.append("category must be 255 characters or fewer")

        cost = payload.get("cost")
        retail_price = payload.get("retail_price")
        if cost is not None:
            try:
                if float(cost) < 0:
                    errors.append("cost must be non-negative")
            except (TypeError, ValueError):
                errors.append("cost must be a number")

        if retail_price is not None:
            try:
                if float(retail_price) < 0:
                    errors.append("retail_price must be non-negative")
            except (TypeError, ValueError):
                errors.append("retail_price must be a number")

        if (
            cost is not None
            and retail_price is not None
        ):
            try:
                if float(cost) > float(retail_price):
                    errors.append(
                        "cost cannot be greater than retail_price"
                    )
            except (TypeError, ValueError):
                errors.append("cost and retail_price must be numbers")

        inventory = payload.get("inventory")
        if inventory is not None:
            try:
                if int(inventory) < 0:
                    errors.append("inventory must be non-negative")
            except (TypeError, ValueError):
                errors.append("inventory must be an integer")

        return {
            "valid": not errors,
            "errors": errors,
        }

    def detect_duplicates(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        title = (payload.get("title") or "").strip()
        supplier_sku = (payload.get("supplier_sku") or "").strip()

        return {
            "duplicate_title": bool(title),
            "duplicate_sku": bool(supplier_sku),
            "check": "title_or_supplier_sku",
        }

    def preview_product_payload(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        tags = payload.get("tags") or []
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",") if tag.strip()]

        product: Dict[str, Any] = {
            "title": payload.get("title"),
            "descriptionHtml": payload.get("description"),
            "vendor": payload.get("supplier"),
            "productType": payload.get("category"),
            "tags": tags,
            "status": "DRAFT",
        }

        return {
            "mapped": {key: value for key, value in product.items() if value is not None},
            "source": payload.get("source", "manual"),
        }

    async def create_import(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        validation = self.validate_import_payload(payload)
        duplicates = self.detect_duplicates(payload)
        preview = self.preview_product_payload(payload)

        record = ProductImport(
            shop_id=self.shop.id,
            source=payload.get("source") or "manual",
            supplier_id=payload.get("supplier_id"),
            supplier_sku=payload.get("supplier_sku"),
            title=payload.get("title"),
            description=payload.get("description"),
            images=payload.get("images"),
            cost=payload.get("cost"),
            retail_price=payload.get("retail_price"),
            variants_json=payload.get("variants_json"),
            category=payload.get("category"),
            tags=",".join(
                [str(tag) for tag in payload.get("tags") or [] if str(tag).strip()]
            ),
            inventory=payload.get("inventory"),
            status="pending",
            sync_status="pending",
            error=None,
        )

        self.db.add(record)
        await self.db.flush()
        await self.db.refresh(record)

        return {
            "import": {
                "id": record.id,
                "status": record.status,
                "sync_status": record.sync_status,
                "validation": validation,
                "duplicates": duplicates,
                "preview": preview,
            }
        }

    async def update_import_from_csv(
        self,
        import_id: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        content = payload.get("content") or ""
        mapping = payload.get("mapping") or {}
        duplicate_action = payload.get("duplicate_action") or "skip"
        default_status = payload.get("status") or "DRAFT"
        validate_only = bool(payload.get("validate_only"))

        parsed = self.parse_csv(content)
        validation = self.validate_import_rows(parsed["rows"], mapping)

        result = await self.db.execute(
            ProductImport.__table__.select()
            .where(
                ProductImport.id == import_id,
                ProductImport.shop_id == self.shop.id,
            )
        )
        row = result.mappings().first()
        if not row:
            raise ValueError("Import not found")

        record = await self.db.get(ProductImport, import_id)

        created = 0
        failed = 0
        skipped = 0
        processed = 0
        details: List[Dict[str, Any]] = []
        shopify_product_ids: List[str] = []

        for item in validation["valid"]:
            processed += 1
            mapped = item["mapped"]
            mapped["status"] = default_status

            variant_fields = {
                "sku",
                "price",
                "compareAtPrice",
                "inventoryQuantity",
                "weight",
                "inventoryPolicy",
                "fulfillmentService",
            }
            if any(key in mapped for key in variant_fields):
                variant_input: Dict[str, Any] = {}
                for key in variant_fields:
                    if key in mapped:
                        variant_input[key] = mapped.pop(key)

                mapped.setdefault("productOptions", [{"name": "Title"}])
                variant_input["selectedOptions"] = [
                    {"name": "Title", "value": "Default Title"}
                ]
                mapped["variants"] = [variant_input]

            if not validate_only and self.api_client is not None:
                shopify_product_id = None
                try:
                    result = await self.api_client.create_product(mapped)
                    product_create = result.get("productCreate") or {}
                    product = product_create.get("product") or {}
                    shopify_product_id = product.get("id")
                    if shopify_product_id:
                        shopify_product_ids.append(shopify_product_id)
                        created += 1
                        details.append({
                            "row": item["row"],
                            "status": "created",
                            "mapped": mapped,
                            "shopify_product_id": shopify_product_id,
                        })
                    else:
                        failed += 1
                        details.append({
                            "row": item["row"],
                            "status": "failed",
                            "error": "Shopify create_product succeeded but returned no product id",
                            "mapped": mapped,
                            "shopify_product_id": None,
                        })
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    details.append({
                        "row": item["row"],
                        "status": "failed",
                        "error": str(exc),
                        "mapped": mapped,
                        "shopify_product_id": None,
                    })
            else:
                details.append({"row": item["row"], "status": "preview", "mapped": mapped})

        for item in validation["errors"]:
            processed += 1
            skipped += 1
            details.append({"row": item["row"], "status": "skipped", "error": item["errors"]})

        if validate_only:
            status = "validated"
            sync_status = "preview"
        elif failed:
            status = "completed_with_errors"
            sync_status = "completed"
        else:
            status = "completed"
            sync_status = "completed"

        record.status = status
        record.sync_status = sync_status
        record.error = None
        record.shopify_product_id = (
            ",".join(shopify_product_ids) if shopify_product_ids else None
        )

        await self.db.flush()
        await self.db.refresh(record)
        await self.db.commit()

        return {
            "import": {
                "id": record.id,
                "status": record.status,
                "sync_status": record.sync_status,
                "summary": {
                    "processed": processed,
                    "created": created,
                    "failed": failed,
                    "skipped": skipped,
                    **validation["summary"],
                },
                "details": details,
            }
        }

    def parse_csv(self, content: str) -> Dict[str, Any]:
        import csv
        from io import StringIO

        reader = csv.DictReader(StringIO(content))
        rows = [dict(row) for row in reader if any(value.strip() for value in row.values())]
        columns = reader.fieldnames or []
        return {
            "rows": rows,
            "columns": columns,
            "count": len(rows),
        }

    def map_row_to_product(self, row: Dict[str, Any], mapping: Dict[str, str]) -> Dict[str, Any]:
        mapped: Dict[str, Any] = {}
        for target, source_key in mapping.items():
            if source_key in row and row[source_key] not in (None, ""):
                mapped[target] = row[source_key]
        return mapped

    def validate_import_rows(self, rows: List[Dict[str, Any]], mapping: Dict[str, str]) -> Dict[str, Any]:
        valid = []
        warnings = []
        errors = []
        duplicates = []

        seen_titles = set()
        seen_skus = set()

        for index, row in enumerate(rows, start=1):
            mapped = self.map_row_to_product(row, mapping)
            row_errors = []
            row_warnings = []

            if not mapped.get("title") and not mapped.get("sku"):
                row_errors.append("title or sku is required")

            if mapped.get("cost") not in (None, ""):
                try:
                    if float(mapped.get("cost", 0)) < 0:
                        row_errors.append("cost must be non-negative")
                except (TypeError, ValueError):
                    row_errors.append("cost must be a number")

            if mapped.get("retail_price") not in (None, ""):
                try:
                    if float(mapped.get("retail_price", 0)) < 0:
                        row_errors.append("retail_price must be non-negative")
                except (TypeError, ValueError):
                    row_errors.append("retail_price must be a number")

            title = (mapped.get("title") or "").strip()
            sku = (mapped.get("sku") or "").strip()

            if title and title in seen_titles:
                duplicates.append({"row": index, "type": "title", "value": title})
            elif title:
                seen_titles.add(title)

            if sku and sku in seen_skus:
                duplicates.append({"row": index, "type": "sku", "value": sku})
            elif sku:
                seen_skus.add(sku)

            if row_errors:
                errors.append({"row": index, "errors": row_errors, "mapped": mapped})
            elif row_warnings:
                warnings.append({"row": index, "warnings": row_warnings, "mapped": mapped})
                valid.append({"row": index, "mapped": mapped})
            else:
                valid.append({"row": index, "mapped": mapped})

        return {
            "valid": valid,
            "warnings": warnings,
            "errors": errors,
            "duplicates": duplicates,
            "summary": {
                "total": len(rows),
                "valid": len(valid),
                "warnings": len(warnings),
                "errors": len(errors),
                "duplicates": len(duplicates),
            },
        }

    async def create_import_from_csv(
        self,
        shop_id: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        content = payload.get("content") or ""
        mapping = payload.get("mapping") or {}
        duplicate_action = payload.get("duplicate_action") or "skip"
        default_status = payload.get("status") or "DRAFT"
        validate_only = bool(payload.get("validate_only"))

        parsed = self.parse_csv(content)
        validation = self.validate_import_rows(parsed["rows"], mapping)

        record = ProductImport(
            shop_id=shop_id,
            source="csv",
            supplier_sku=None,
            title=payload.get("title"),
            description=payload.get("description"),
            images=payload.get("images"),
            cost=payload.get("cost"),
            retail_price=payload.get("retail_price"),
            variants_json=payload.get("variants_json"),
            category=payload.get("category"),
            tags=",".join([str(tag) for tag in payload.get("tags") or []]),
            inventory=payload.get("inventory"),
            status="pending",
            sync_status="pending",
            error=None,
        )

        self.db.add(record)
        await self.db.flush()
        await self.db.refresh(record)
        await self.db.commit()

        created = 0
        failed = 0
        skipped = 0
        processed = 0
        details: List[Dict[str, Any]] = []
        shopify_product_ids: List[str] = []

        for item in validation["valid"]:
            processed += 1
            mapped = item["mapped"]
            mapped["status"] = default_status

            variant_fields = {
                "sku",
                "price",
                "compareAtPrice",
                "inventoryQuantity",
                "weight",
                "inventoryPolicy",
                "fulfillmentService",
            }
            if any(key in mapped for key in variant_fields):
                variant_input: Dict[str, Any] = {}
                for key in variant_fields:
                    if key in mapped:
                        variant_input[key] = mapped.pop(key)

                mapped.setdefault("productOptions", [{"name": "Title"}])
                variant_input["selectedOptions"] = [
                    {"name": "Title", "value": "Default Title"}
                ]
                mapped["variants"] = [variant_input]

            if not validate_only and self.api_client is not None:
                shopify_product_id = None
                try:
                    safe_mapped_keys = {
                        k: mapped[k] for k in ["title", "vendor", "productType", "status", "tags"]
                        if k in mapped
                    }
                    result = await self.api_client.create_product(mapped)
                    product_create = result.get("productCreate") or {}
                    product = product_create.get("product") or {}
                    shopify_product_id = product.get("id")
                    if shopify_product_id:
                        shopify_product_ids.append(shopify_product_id)
                        created += 1
                        details.append(
                            {
                                "row": item["row"],
                                "status": "created",
                                "mapped": mapped,
                                "shopify_product_id": shopify_product_id,
                            }
                        )
                    else:
                        failed += 1
                        details.append(
                            {
                                "row": item["row"],
                                "status": "failed",
                                "error": "Shopify create_product succeeded but returned no product id",
                                "mapped": mapped,
                                "shopify_product_id": None,
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    details.append(
                        {
                            "row": item["row"],
                            "status": "failed",
                            "error": str(exc),
                            "mapped": mapped,
                            "shopify_product_id": None,
                        }
                    )
            else:
                details.append({"row": item["row"], "status": "preview", "mapped": mapped})

        for item in validation["errors"]:
            processed += 1
            skipped += 1
            details.append({"row": item["row"], "status": "skipped", "error": item["errors"]})

        if validate_only:
            status = "validated"
            sync_status = "preview"
        elif failed:
            status = "completed_with_errors"
            sync_status = "completed"
        else:
            status = "completed"
            sync_status = "completed"

        record.status = status
        record.sync_status = sync_status
        record.error = None
        record.shopify_product_id = (
            ",".join(shopify_product_ids) if shopify_product_ids else None
        )

        await self.db.flush()
        await self.db.refresh(record)
        await self.db.commit()

        return {
            "import": {
                "id": record.id,
                "status": record.status,
                "sync_status": record.sync_status,
                "summary": {
                    "processed": processed,
                    "created": created,
                    "failed": failed,
                    "skipped": skipped,
                    **validation["summary"],
                },
                "details": details,
            }
        }
