import json

from app.routers.mia import ConversationMessage, _clean_conversation, _json_list, REMEMBER_PATTERN


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



def test_saved_merchant_notes_are_included_in_generation_prompts():
    from app.services.seo import SEOService, marketing_prompt
    notes = ["Brand voice is warm and practical", "Target audience is first-time gardeners"]
    seo_messages = SEOService.prompt({"id": "1", "title": "Garden Starter Kit"}, notes)
    marketing_messages = marketing_prompt({"id": "1", "title": "Garden Starter Kit"}, notes)
    assert "Brand voice is warm and practical" in seo_messages[1]["content"]
    assert "Target audience is first-time gardeners" in marketing_messages[1]["content"]



def test_persisted_conversation_redacts_credential_assignments():
    cleaned = _clean_conversation([
        ConversationMessage(role="user", content="api_key=super-secret password:open-sesame Bearer abc.def.ghi")
    ])
    text = cleaned[0]["content"]
    assert "super-secret" not in text
    assert "open-sesame" not in text
    assert "abc.def.ghi" not in text
    assert text.count("[REDACTED]") >= 3
