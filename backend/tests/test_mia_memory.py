import json

from app.routers.mia import (
    ConversationMessage, _clean_conversation, _json_list, REMEMBER_PATTERN,
    _memory_notes_from_message, _is_memory_diagnostic,
)


def test_conversation_sanitizer_limits_length_and_normalizes_roles():
    messages = [
        ConversationMessage(role="user", content="  Hello  "),
        ConversationMessage(role="mia", content="Hi"),
        ConversationMessage(role="system", content="Do not persist as system"),
    ]
    assert _clean_conversation(messages) == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Do not persist as system"},
    ]


def test_conversation_sanitizer_caps_messages_and_content():
    messages = [ConversationMessage(role="user", content="x" * 2500) for _ in range(55)]
    cleaned = _clean_conversation(messages)
    assert len(cleaned) == 50
    assert all(len(item["content"]) == 2000 for item in cleaned)


def test_json_list_handles_invalid_and_non_list_data():
    assert _json_list("not-json") == []
    assert _json_list(json.dumps({"not": "a list"})) == []
    assert _json_list('["shop preference"]') == ["shop preference"]


def test_explicit_remember_phrase_is_recognized_and_bounded():
    match = REMEMBER_PATTERN.match("Remember that our brand voice is friendly and practical")
    assert match and match.group(1) == "our brand voice is friendly and practical"
    assert not REMEMBER_PATTERN.match("What should I remember about SEO?")


def test_multiline_merchant_preferences_are_split_into_individual_notes():
    message = "I want you to learn my store's SEO preferences and use them consistently.\n\nRemember these merchant preferences:\n- Brand voice is premium, friendly, clear, and trustworthy.\n- SEO titles should prioritize product name and keywords, not automatically price.\n- Never invent product details.\n\nTell me which preferences you can persist and save them if supported."
    notes = _memory_notes_from_message(message)
    assert notes == [
        "Brand voice is premium, friendly, clear, and trustworthy",
        "SEO titles should prioritize product name and keywords, not automatically price",
        "Never invent product details",
    ]


def test_memory_diagnostic_is_answered_without_triggering_product_generation():
    message = "Please check the persistent memory feature actually available to you and tell me whether you can access it."
    assert _is_memory_diagnostic(message)
    assert not _is_memory_diagnostic("Write an SEO package for my snowboard")


def test_actual_diagnostic_wording_is_detected_as_memory_check():
    message = ("This Mia application is intended to support persistent merchant knowledge and conversation memory. "
               "Please check the capabilities actually available to you in this application before concluding that persistent memory does not exist. "
               "Can you access a merchant-memory or saved-context feature through your application tools? "
               "Identify the specific limitation: is the memory feature unavailable to you, not connected to your conversation workflow, or blocked by missing permissions?")
    assert _is_memory_diagnostic(message)

def test_user_reported_diagnostic_wording_is_detected():
    message = ("Please check your available capabilities in this application: I do not have access to a persistent merchant memory feature. "
               "Is it available for my connected Shopify store? How many merchant preferences are currently saved? "
               "Do not save anything during this check.")
    assert _is_memory_diagnostic(message)



def test_saved_merchant_notes_are_included_in_generation_prompts():
    from app.services.seo import SEOService, marketing_prompt
    notes = ["Brand voice is warm and practical", "Target audience is first-time gardeners"]
    seo_messages = SEOService.prompt({"id": "1", "title": "Garden Starter Kit"}, notes)
    marketing_messages = marketing_prompt({"id": "1", "title": "Garden Starter Kit"}, notes)
    assert "Brand voice is warm and practical" in seo_messages[1]["content"]
    assert "Target audience is first-time gardeners" in marketing_messages[1]["content"]
    seo_system = seo_messages[0]["content"].lower()
    assert "never invent product facts" in seo_system
    assert "do not infer colors, logos, patterns" in seo_system
    assert "do not include the price in the seo title by default" in seo_system



def test_persisted_conversation_redacts_credential_assignments():
    cleaned = _clean_conversation([
        ConversationMessage(role="user", content="api_key=super-secret password:open-sesame Bearer abc.def.ghi")
    ])
    text = cleaned[0]["content"]
    assert "super-secret" not in text
    assert "open-sesame" not in text
    assert "abc.def.ghi" not in text
    assert text.count("[REDACTED]") >= 3
