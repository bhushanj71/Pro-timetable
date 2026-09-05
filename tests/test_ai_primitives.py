"""The three calls the AI layer is built on.

generate, generate_structured and embed are the only places anything in this
application talks to a model, so the provider can be changed here and nowhere
else. These tests are all offline: what is worth pinning is the negotiation
and the parsing, not whether a vendor is up.

The negotiation exists because of a real failure. The configured chat model
reached end of life while the API key stayed valid, so every call returned 410
and the application went on answering from its rule-based extractor with
nothing on screen to say the AI was not involved. The replacement model then
rejected the response_format the code had always sent. Both are the same
lesson: a hard-coded assumption about one provider is a silent degradation
waiting to happen.
"""
import json

import httpx
import pytest

from app.services.ai_service import AIService, AIServiceError, _loads_json


def _http_error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def _service() -> AIService:
    svc = AIService()
    # Enough for is_configured; nothing here reaches the network.
    svc.provider, svc.api_key, svc.model = "openai", "test-key", "test-model"
    svc.base_url = "https://example.test/v1"
    return svc


# ---------------------------------------------------------------------------
# Reading what a model actually returns
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    '{"intent": "PLAN_DAY"}',
    '```json\n{"intent": "PLAN_DAY"}\n```',
    '```\n{"intent": "PLAN_DAY"}\n```',
    'Here is the result:\n{"intent": "PLAN_DAY"}',
    '{"intent": "PLAN_DAY"}\nHope that helps.',
])
def test_json_is_read_through_whatever_the_model_wraps_it_in(raw):
    """Asked for JSON and nothing else, models still fence it or introduce it.
    Refusing those would drop an answer that was right apart from its
    packaging."""
    assert _loads_json(raw) == {"intent": "PLAN_DAY"}


def test_nested_objects_survive_the_unwrapping():
    """The first balanced object, not the first closing brace -- a plan with a
    nested list would otherwise be truncated to nonsense."""
    raw = 'sure:\n{"plan": {"blocks": [{"start": "17:00"}]}, "ok": true}'
    assert _loads_json(raw) == {"plan": {"blocks": [{"start": "17:00"}]}, "ok": True}


def test_text_with_no_json_at_all_is_an_error_not_a_guess():
    with pytest.raises(json.JSONDecodeError):
        _loads_json("I could not do that.")


# ---------------------------------------------------------------------------
# Negotiating the response format
# ---------------------------------------------------------------------------
def test_the_strictest_format_is_asked_for_first():
    """A schema the provider will honour is worth more than one it ignores."""
    seen = []

    def fake_post(path, body, **kw):
        seen.append(body.get("response_format"))
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    svc = _service()
    svc._post = fake_post
    assert svc.generate_structured("go", {"type": "object"}) == {"ok": True}
    assert seen[0]["type"] == "json_schema"
    assert len(seen) == 1, "no retry was needed"


def test_a_provider_that_refuses_the_schema_is_stepped_down_not_abandoned():
    """This is exactly what the live model did: it rejects json_object and
    demands a schema, and other providers do the reverse. Neither should cost
    the application its structured answer."""
    seen = []

    def fake_post(path, body, **kw):
        fmt = body.get("response_format")
        seen.append(fmt and fmt["type"])
        if fmt is not None:
            raise _http_error(400, '{"error": "response_format is not supported"}')
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    svc = _service()
    svc._post = fake_post
    assert svc.generate_structured("go", {"type": "object"}) == {"ok": True}
    assert seen == ["json_schema", "json_object", None], "weakest form last"


def test_a_failure_that_is_not_about_the_format_is_raised_at_once():
    """A dead model, a bad key or a rate limit is a real failure. Retrying it
    three times in different clothes only delays saying so."""
    calls = []

    def fake_post(path, body, **kw):
        calls.append(1)
        raise _http_error(410, '{"detail": "model has reached its end of life"}')

    svc = _service()
    svc._post = fake_post
    with pytest.raises(httpx.HTTPStatusError):
        svc.generate_structured("go", {"type": "object"})
    assert len(calls) == 1, "not retried"


def test_a_provider_that_refuses_every_format_says_so():
    def fake_post(path, body, **kw):
        raise _http_error(400, "response_format unsupported")

    svc = _service()
    svc._post = fake_post
    with pytest.raises(AIServiceError, match="response_format"):
        svc.generate_structured("go", {"type": "object"})


def test_plain_generation_never_asks_for_a_format():
    """Prose does not need one, and asking for it is what some providers
    reject."""
    seen = []

    def fake_post(path, body, **kw):
        seen.append(body)
        return {"choices": [{"message": {"content": "a sentence"}}]}

    svc = _service()
    svc._post = fake_post
    assert svc.generate("why?") == "a sentence"
    assert "response_format" not in seen[0]


def test_a_response_in_an_unexpected_shape_is_an_error_not_a_crash():
    svc = _service()
    svc._post = lambda path, body, **kw: {"unexpected": True}
    with pytest.raises(AIServiceError, match="shape"):
        svc.generate("hello")


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
def test_embedding_uses_its_own_model_and_not_the_chat_one():
    """A chat model cannot embed, and hosts retire the two on their own
    schedules -- so they are named separately in the settings."""
    seen = {}

    def fake_post(path, body, **kw):
        seen.update(path=path, body=body)
        return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}

    svc = _service()
    svc._post = fake_post
    svc.embed(["hello"])
    assert seen["path"] == "/embeddings"
    assert seen["body"]["model"] != svc.model


def test_embeddings_come_back_in_the_order_they_went_in():
    """Providers may answer out of order. A chunk paired with another chunk's
    vector is a retrieval index that silently returns the wrong passage."""
    def fake_post(path, body, **kw):
        return {"data": [
            {"index": 2, "embedding": [3.0]},
            {"index": 0, "embedding": [1.0]},
            {"index": 1, "embedding": [2.0]},
        ]}

    svc = _service()
    svc._post = fake_post
    assert svc.embed(["a", "b", "c"]) == [[1.0], [2.0], [3.0]]


def test_embedding_nothing_costs_nothing():
    svc = _service()
    svc._post = lambda *a, **k: pytest.fail("should not have called the provider")
    assert svc.embed([]) == []


def test_stored_text_and_asked_text_are_embedded_differently():
    """Asymmetric retrieval models want to know which side they are on;
    the ones that do not simply ignore it."""
    seen = []
    svc = _service()
    svc._post = lambda path, body, **kw: (
        seen.append(body["input_type"]) or {"data": [{"index": 0, "embedding": [1.0]}]}
    )
    svc.embed(["a"], kind="passage")
    svc.embed(["a"], kind="query")
    assert seen == ["passage", "query"]


# ---------------------------------------------------------------------------
# Nothing above may cost the application its offline behaviour
# ---------------------------------------------------------------------------
def test_with_no_provider_configured_the_rule_based_extractor_still_answers():
    """The fallback is why this application works without a key at all, and it
    must keep working now that there is more built on top of the provider."""
    svc = AIService()
    svc.provider, svc.api_key, svc.base_url = "none", None, None
    assert svc.is_configured is False

    result = svc.process_prompt(
        "Lecture on Data Structures Monday 10am to 11am in LH-3",
        {"timezone": "Asia/Kolkata", "today": "2026-09-05", "weekday": "Saturday"},
    )
    assert result.intent == "CREATE_EVENT"
    assert result.events and result.events[0].start_time == "10:00"


def test_asking_an_unconfigured_provider_directly_is_refused_clearly():
    svc = AIService()
    svc.provider, svc.api_key, svc.base_url = "none", None, None
    with pytest.raises(AIServiceError, match="No AI provider"):
        svc.generate("anything")
