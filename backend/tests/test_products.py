from app.services.products import ImportService, ProductService


def test_validate_import_requires_title_or_supplier_sku():
    service = ImportService(db_session=None)

    result = service.validate_import_payload({})

    assert result["valid"] is False
    assert "title or supplier_sku is required" in result["errors"]


def test_validate_import_accepts_title():
    service = ImportService(db_session=None)

    result = service.validate_import_payload(
        {
            "title": "Kids Summer Dress",
        }
    )

    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_import_accepts_supplier_sku():
    service = ImportService(db_session=None)

    result = service.validate_import_payload(
        {
            "supplier_sku": "KIDS-001",
        }
    )

    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_import_rejects_negative_cost():
    service = ImportService(db_session=None)

    result = service.validate_import_payload(
        {
            "title": "Kids Summer Dress",
            "cost": -1,
            "retail_price": 20,
        }
    )

    assert result["valid"] is False
    assert "cost must be non-negative" in result["errors"]


def test_validate_import_rejects_negative_retail_price():
    service = ImportService(db_session=None)

    result = service.validate_import_payload(
        {
            "title": "Kids Summer Dress",
            "cost": 5,
            "retail_price": -20,
        }
    )

    assert result["valid"] is False
    assert "retail_price must be non-negative" in result["errors"]


def test_validate_import_accepts_zero_prices():
    service = ImportService(db_session=None)

    result = service.validate_import_payload(
        {
            "title": "Kids Summer Dress",
            "cost": 0,
            "retail_price": 0,
        }
    )

    assert result["valid"] is True
    assert result["errors"] == []


def test_map_import_to_product():
    service = ProductService(api_client=None)

    result = service.map_import_to_product(
        {
            "title": "Kids Summer Dress",
            "description": "A lightweight summer dress.",
            "supplier": "Test Supplier",
            "category": "Kids Clothing",
            "tags": ["kids", "summer", "dress"],
        }
    )

    assert result["title"] == "Kids Summer Dress"
    assert result["descriptionHtml"] == "A lightweight summer dress."
    assert result["vendor"] == "Test Supplier"
    assert result["productType"] == "Kids Clothing"
    assert result["tags"] == ["kids", "summer", "dress"]
    assert result["status"] == "DRAFT"


def test_map_import_to_product_handles_missing_fields():
    service = ProductService(api_client=None)

    result = service.map_import_to_product({})

    assert result["tags"] == []
    assert result["status"] == "DRAFT"

    assert "title" not in result
    assert "descriptionHtml" not in result
    assert "vendor" not in result
    assert "productType" not in result


def test_map_import_to_product_handles_string_tags():
    service = ProductService(api_client=None)

    result = service.map_import_to_product(
        {
            "title": "Kids Dress",
            "tags": "kids, summer, dress",
        }
    )

    assert result["tags"] == [
        "kids",
        "summer",
        "dress",
    ]


def test_map_import_to_product_handles_empty_tags():
    service = ProductService(api_client=None)

    result = service.map_import_to_product(
        {
            "title": "Kids Dress",
            "tags": "",
        }
    )

    assert result["tags"] == []


def test_map_import_to_product_removes_none_values():
    service = ProductService(api_client=None)

    result = service.map_import_to_product(
        {
            "title": "Kids Dress",
            "description": None,
            "supplier": None,
            "category": None,
        }
    )

    assert result["title"] == "Kids Dress"
    assert "descriptionHtml" not in result
    assert "vendor" not in result
    assert "productType" not in result
    assert result["tags"] == []
    assert result["status"] == "DRAFT"
