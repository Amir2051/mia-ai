from app.routers.mia import (
    _find_target,
    _is_explicit_content_request,
    _is_explanation_request,
    _needs_store_context,
    _references_selected_product,
)


def test_general_seo_question_is_explanation_not_content_generation():
    question = "What is SEO and how does it work?"
    assert _is_explanation_request(question)
    assert not _is_explicit_content_request(question)


def test_marketing_explanation_is_not_a_campaign_request():
    question = "Explain what a marketing campaign is"
    assert _is_explanation_request(question)
    assert not _is_explicit_content_request(question)


def test_explicit_seo_generation_is_content_request():
    assert _is_explicit_content_request("Generate an SEO package for this product")


def test_selected_product_can_still_be_used_for_explicit_generation():
    products = [{"id": "gid://shopify/Product/1", "title": "Blue Travel Mug"}]
    target = _find_target(products, "generate an seo package", "gid://shopify/Product/1")
    assert target == products[0]


def test_general_question_does_not_need_selected_product():
    products = [{"id": "gid://shopify/Product/1", "title": "Blue Travel Mug"}]
    target = _find_target(products, "what is seo", None)
    assert target is None



def test_selected_product_reference_adds_context_without_requesting_generation():
    question = "What do you think about this product?"
    assert _references_selected_product(question)
    assert not _is_explicit_content_request(question)


def test_product_follow_up_can_reference_its_seo_without_generating_content():
    question = "How can I improve its SEO?"
    assert _references_selected_product(question)
    assert not _is_explicit_content_request(question)


def test_general_seo_explanation_does_not_reference_selected_product():
    question = "What is SEO and how does it work?"
    assert not _references_selected_product(question)
    assert not _is_explicit_content_request(question)



def test_product_context_helper_does_not_treat_general_how_does_it_work_as_product_reference():
    from app.routers.mia import _references_selected_product
    assert not _references_selected_product("What is SEO and how does it work?")
    assert _references_selected_product("What do you think about this product?")
    assert _references_selected_product("How can I improve its SEO?")


def test_explicitly_named_product_overrides_dropdown_selection():
    products = [
        {"id": "gid://shopify/Product/1", "title": "Blue Travel Mug"},
        {"id": "gid://shopify/Product/2", "title": "Red Travel Mug"},
    ]
    target = _find_target(
        products,
        "Generate an SEO package for Red Travel Mug",
        "gid://shopify/Product/1",
    )
    assert target == products[1]


def test_ambiguous_product_name_does_not_guess_selected_item():
    products = [
        {"id": "gid://shopify/Product/1", "title": "Blue Travel Mug"},
        {"id": "gid://shopify/Product/2", "title": "Red Travel Mug"},
    ]
    target = _find_target(products, "Generate an SEO package for Travel Mug", None)
    assert target is None


def test_general_explanation_skips_live_store_context():
    assert not _needs_store_context("What is SEO?")
    assert not _needs_store_context("Explain what a marketing campaign is")


def test_store_data_questions_and_product_actions_load_context():
    assert _needs_store_context("How many orders did I have?")
    assert _needs_store_context("What is my store revenue?")
    assert _needs_store_context("Generate an SEO package for this product", "gid://shopify/Product/1")


def test_context_plan_only_fetches_datasets_needed_for_question():
    from app.routers.mia import _context_plan

    assert _context_plan("How many products do I have?") == {
        "products": True, "orders": False, "customers": False, "publications": False
    }
    assert _context_plan("What is my store revenue?") == {
        "products": False, "orders": True, "customers": False, "publications": False
    }
    assert _context_plan("What sales channels are available?") == {
        "products": False, "orders": False, "customers": False, "publications": True
    }
    assert _context_plan("Which products sold the most?") == {
        "products": True, "orders": True, "customers": False, "publications": False
    }
    assert all(_context_plan("Give me a store overview").values())


def test_context_plan_keeps_marketing_generation_on_product_data_only():
    from app.routers.mia import _context_plan

    plan = _context_plan("Generate a marketing campaign for Blue Mug")
    assert plan == {
        "products": True, "orders": False, "customers": False, "publications": False
    }


def test_order_date_filter_uses_store_timezone_for_yesterday():
    from datetime import datetime, timezone
    from app.routers.mia import _order_date_filter

    query, label, start = _order_date_filter(
        "How much sales did I make yesterday?",
        "America/New_York",
        now=datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc),
    )

    assert query == "created_at:>='2026-10-19T04:00:00Z' created_at:<'2026-10-20T04:00:00Z'"
    assert "yesterday, 2026-10-19" in label
    assert start is not None and start.isoformat() == "2026-10-19T00:00:00-04:00"


def test_order_date_filter_supports_last_week_and_explicit_dates():
    from datetime import datetime, timezone
    from app.routers.mia import _order_date_filter

    query, label, _ = _order_date_filter(
        "Show sales last week",
        "America/New_York",
        now=datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc),
    )
    assert query == "created_at:>='2026-10-12T04:00:00Z' created_at:<'2026-10-19T04:00:00Z'"
    assert "last calendar week" in label

    query, label, _ = _order_date_filter(
        "Revenue from 2026-10-01 to 2026-10-03",
        "America/New_York",
    )
    assert "created_at:>='2026-10-01T04:00:00Z'" in query
    assert "created_at:<'2026-10-04T04:00:00Z'" in query
    assert "2026-10-01 through 2026-10-03" in label


def test_order_date_filter_rejects_reversed_explicit_dates():
    from app.routers.mia import _order_date_filter

    query, label, start = _order_date_filter("Sales from 2026-10-20 to 2026-10-01")
    assert query == ""
    assert label == "invalid date range"
    assert start is None


def test_selected_product_does_not_force_catalog_fetch_for_unrelated_sales_question():
    from app.routers.mia import _context_plan

    selected_id = "gid://shopify/Product/1"
    assert _context_plan("What is my store revenue?", selected_id) == {
        "products": False, "orders": True, "customers": False, "publications": False
    }
    assert _context_plan("What do you think about this product?", selected_id) == {
        "products": True, "orders": False, "customers": False, "publications": False
    }
