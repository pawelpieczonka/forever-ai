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
