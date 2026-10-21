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
