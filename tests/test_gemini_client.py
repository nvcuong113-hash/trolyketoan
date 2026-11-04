import json

from gemini_client import create_interaction, interaction_text


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({
            "status": "completed",
            "steps": [{
                "type": "model_output",
                "content": [{"type": "text", "text": "Gemini response"}]
            }]
        }).encode("utf-8")


def test_create_interaction_uses_gemini_store_false_and_api_key_header(monkeypatch):
    import gemini_client

    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(gemini_client, "urlopen", fake_urlopen)
    response = create_interaction(
        "test-api-key", "gemini-3.5-flash-lite",
        [{"type": "text", "text": "test"}],
        response_format={"type": "text", "mime_type": "application/json", "schema": {"type": "object"}}
    )
    body = json.loads(captured["request"].data.decode("utf-8"))
    assert captured["request"].full_url.endswith("/v1beta/interactions")
    assert captured["request"].get_header("X-goog-api-key") == "test-api-key"
    assert captured["timeout"] == 120
    assert body["model"] == "gemini-3.5-flash-lite"
    assert body["store"] is False
    assert body["input"] == [{"type": "text", "text": "test"}]
    assert body["response_format"]["mime_type"] == "application/json"
    assert interaction_text(response) == "Gemini response"


def test_interaction_text_supports_direct_output_text():
    assert interaction_text({"output_text": "direct response"}) == "direct response"


def test_interaction_text_rejects_empty_model_output():
    try:
        interaction_text({"steps": [{"type": "thought", "summary": []}]})
        assert False
    except ValueError as exc:
        assert "không trả về nội dung" in str(exc)
