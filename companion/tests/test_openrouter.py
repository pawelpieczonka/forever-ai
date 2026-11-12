import json

import httpx

from foreverai.openrouter import OpenRouter


def _sse(*chunks):
    lines = [": OPENROUTER PROCESSING"]
    for c in chunks:
        lines.append("data: " + json.dumps(c))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


def _client(handler):
    return OpenRouter("sk-test", transport=httpx.MockTransport(handler))


def test_stream_text_tool_calls_and_usage():
    body_seen = {}

    def handler(request: httpx.Request):
        body_seen.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Hello "}}]},
            {"choices": [{"delta": {"content": "player"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "search_knowledge_base", "arguments": "{\"que"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ry\": \"raids\"}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001}},
        ), headers={"content-type": "text/event-stream"})

    events = list(_client(handler).chat_stream("deepseek/deepseek-v4-flash", [{"role": "user", "content": "hi"}],
                                               tools=[], search_mode="auto"))
    kinds = [k for k, _ in events]
    assert "".join(v for k, v in events if k == "text") == "Hello player"
    calls = next(v for k, v in events if k == "tool_calls")
    assert calls[0].name == "search_knowledge_base" and calls[0].args() == {"query": "raids"}
    assert next(v for k, v in events if k == "usage")["cost"] == 0.0001
    assert kinds[-1] == "done"
    assert any(t.get("type") == "openrouter:web_search" for t in body_seen["tools"])
    assert body_seen["usage"] == {"include": True}


def test_search_modes_body():
    c = OpenRouter("k")
    always = c.build_body("m", [], [], "always")
    assert always["plugins"][0]["id"] == "web" and "tools" not in always
    never = c.build_body("m", [], [], "never")
    assert "plugins" not in never and "tools" not in never


def test_http_errors_are_friendly():
    def handler(request):
        return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})

    events = list(_client(handler).chat_stream("m", [], search_mode="never"))
    assert events[0][0] == "error" and "402" in events[0][1]


def test_credits():
    def handler(request):
        assert request.url.path.endswith("/credits")
        return httpx.Response(200, json={"data": {"total_credits": 20, "total_usage": 13.83}})

    assert abs(_client(handler).credits() - 6.17) < 1e-9


def test_free_models_are_marked_in_the_model_list():
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"id": "openai/gpt-6-luna", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
             "supported_parameters": ["tools"]},
            {"id": "google/gemma-4-31b-it:free", "pricing": {"prompt": "0", "completion": "0"},
             "supported_parameters": ["tools", "reasoning"],
             "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
            {"id": "google/lyria-3-pro-preview", "pricing": {"prompt": "0", "completion": "0"},
             "architecture": {"input_modalities": ["text"], "output_modalities": ["audio"]}},
            {"id": "liquid/lfm-3-1.7b:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]})

    by_id = {m["id"]: m for m in _client(handler).models()}
    assert by_id["openai/gpt-6-luna"]["free"] is False and by_id["openai/gpt-6-luna"]["text"] is True
    gemma = by_id["google/gemma-4-31b-it:free"]
    assert gemma["free"] and gemma["tools"] and gemma["image"] and gemma["text"] and gemma["in"] == 0
    assert by_id["google/lyria-3-pro-preview"]["text"] is False  # music, not a chat model
    assert by_id["liquid/lfm-3-1.7b:free"]["tools"] is False


def test_a_free_model_is_followed_by_the_free_router_only():
    from foreverai.openrouter import FREE_ROUTER, is_free
    c = OpenRouter("k")
    assert c.build_body("google/gemma-4-31b-it:free", [], [], "never")["models"] == [FREE_ROUTER]
    # a paid model is never added after a free one, and the router and paid models get no fallback list
    assert "models" not in c.build_body(FREE_ROUTER, [], [], "never")
    assert "models" not in c.build_body("openai/gpt-6-luna", [], [], "never")
    assert is_free(FREE_ROUTER) and is_free("x/y:free") and not is_free("x/y") and not is_free("x/free-y")


def test_the_model_that_answers_is_reported_once():
    def handler(request):
        return httpx.Response(200, content=_sse(
            {"model": "nvidia/nemotron-3.5-lightning:free", "choices": [{"delta": {"content": "Hi"}}]},
            {"model": "nvidia/nemotron-3.5-lightning:free", "choices": [{"delta": {"content": "!"}}]},
        ), headers={"content-type": "text/event-stream"})

    events = list(_client(handler).chat_stream("openrouter/free", [], search_mode="never"))
    assert [v for k, v in events if k == "model"] == ["nvidia/nemotron-3.5-lightning:free"]
    assert "".join(v for k, v in events if k == "text") == "Hi!"


def _error(status, body, headers=None, model="google/gemma-4-31b-it:free"):
    def handler(request):
        return httpx.Response(status, json=body, headers=headers or {})
    events = list(_client(handler).chat_stream(model, [], search_mode="never"))
    assert events[0][0] == "error" and len(events) == 1
    return events[0][1]


def test_the_limits_of_free_models_are_explained():
    import time as _time
    reset = int(_time.mktime((2026, 11, 12, 1, 0, 0, 0, 0, -1)) * 1000)
    # OpenRouter's own daily limit: its headers say how many and when it resets
    daily = _error(429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day. Add 10 credits "
                                                          "to unlock 1000 free model requests per day"}},
                   {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
    assert "daily limit" in daily and "50 requests" in daily and "resets at 01:00" in daily
    assert "1000 a day" in daily and "paid model" in daily
    # the message alone (no headers) is enough; with 1000 a day nothing more can be bought
    assert "daily limit" in _error(429, {"error": {"message": "Rate limit exceeded: free-models-per-day"}})
    assert "1000 a day" not in _error(429, {"error": {"message": "Rate limit exceeded"}},
                                      {"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "0"})
    # ... and the limit of a minute
    minute = _error(429, {"error": {"message": "Rate limit exceeded: free-models-per-min."}},
                    {"X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0"})
    assert "20 requests a minute" in minute and "daily" not in minute
    # the provider's limit (no headers): the model is busy, the player can try another one
    busy = _error(429, {"error": {"code": 429, "message": "Provider returned error",
                                  "metadata": {"raw": "is temporarily rate-limited upstream"}}})
    assert "busy at its provider" in busy
    assert "Too many requests" in _error(429, {"error": {"message": "Rate limit exceeded"}}, model="openai/gpt-6-luna")
    # a balance below zero stops free models too
    assert "below zero" in _error(402, {"error": {"message": "Insufficient credits"}})
    assert "No credits left" in _error(402, {"error": {"message": "Insufficient credits"}}, model="openai/gpt-6-luna")
    # the provider's own words come with OpenRouter's generic message
    assert "No endpoints" in _error(404, {"error": {"message": "Provider returned error",
                                                    "metadata": {"raw": "No endpoints found"}}})


def test_a_limit_hit_while_streaming_is_explained():
    def handler(request):
        return httpx.Response(200, content=_sse(
            {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min."},
             "choices": [{"delta": {"content": ""}, "finish_reason": "error"}]},
        ), headers={"content-type": "text/event-stream"})

    events = list(_client(handler).chat_stream("google/gemma-4-31b-it:free", [], search_mode="never"))
    assert events[0][0] == "error" and "20 requests a minute" in events[0][1]
