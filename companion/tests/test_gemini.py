import json
import threading
import time

import httpx

from foreverai import config
from foreverai.gemini import SKIP_SIGNATURE, Gemini
from foreverai.openrouter import ToolCall

from test_service import _read_inbox, _send, env  # noqa: F401  (fixture)


def _sse(*chunks):
    return ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def _client(handler):
    return Gemini("AIza-test-key-000000000000000", transport=httpx.MockTransport(handler))


def test_stream_keeps_the_thought_signature_of_tool_calls():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "Let me check"}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "fc1", "type": "function", "extra_content": {"google": {"thought_signature": "SIG"}},
                 "function": {"name": "search_auction_house", "arguments": "{\"slot\": \"one-hand\"}"}}]},
                "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ), headers={"content-type": "text/event-stream"})

    events = list(_client(handler).chat_stream("gemini-flash-latest", [{"role": "user", "content": "hi"}],
                                               tools=[], search_mode="auto"))
    assert seen["path"] == "/v1beta/openai/chat/completions" and seen["auth"].startswith("Bearer AIza")
    assert seen["body"]["reasoning_effort"] == "low" and seen["body"]["max_tokens"] > 1800
    assert [t["function"]["name"] for t in seen["body"]["tools"]] == ["web_search"]
    calls = next(v for k, v in events if k == "tool_calls")
    assert calls[0].name == "search_auction_house" and calls[0].args() == {"slot": "one-hand"}
    assert calls[0].extra == {"google": {"thought_signature": "SIG"}}
    assert events[-1] == ("done", "tool_calls")


def test_missing_signature_gets_the_documented_placeholder_and_never_search():
    def handler(request):
        assert "tools" not in json.loads(request.content)
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"tool_calls": [
            {"id": "a", "function": {"name": "fetch_url", "arguments": "{}"}},
            {"id": "b", "function": {"name": "mark_on_map", "arguments": "{}"}}]}}]}))

    calls = next(v for k, v in _client(handler).chat_stream("m", [], search_mode="never") if k == "tool_calls")
    assert [c.id for c in calls] == ["a", "b"]
    assert calls[0].extra == {"google": {"thought_signature": SKIP_SIGNATURE}} and calls[1].extra is None


def test_errors_models_and_web_search():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(400, json=[{"error": {"code": 400, "message": "API key not valid."}}])
        if request.url.path.endswith(":generateContent"):
            assert json.loads(request.content)["tools"] == [{"google_search": {}}]
            return httpx.Response(200, json={"candidates": [{
                "content": {"parts": [{"text": "Patch 1.1 is out.", "thought": False}]},
                "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://x.example/a", "title": "x.example"}}]}}]})
        assert request.headers["x-goog-api-key"].startswith("AIza")
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]}]})

    c = _client(handler)
    kind, text = next(iter(c.chat_stream("m", [], search_mode="never")))
    assert kind == "error" and "rejected the Gemini key" in text
    assert [m["id"] for m in c.models()] == ["gemini-3.8-flash"]
    text, sources = c.web_search("gemini-3.8-flash", "forever patch")
    assert text.startswith("Patch 1.1 is out.") and sources == [{"title": "x.example", "url": "https://x.example/a"}]


class FakeGemini:
    seen: list = []

    def __init__(self, key):
        self.key = key

    def key_info(self):
        if not self.key.startswith("AIza"):
            raise RuntimeError("400 API key not valid")
        return {}

    def models(self):
        return [{"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"}]

    def web_search(self, model, query):
        return f"results for {query}", [{"title": "News", "url": "https://news.example/1"}]

    def chat_stream(self, model, messages, tools, search_mode, max_tokens):
        FakeGemini.seen.append([dict(m) for m in messages])
        if not any(m["role"] == "tool" for m in messages):
            yield "tool_calls", [ToolCall("w1", "web_search", '{"query": "forever news"}',
                                          {"google": {"thought_signature": "S1"}})]
            yield "done", "tool_calls"
            return
        yield "text", "Check **[Patch notes]**."
        yield "done", "stop"


def test_gemini_key_provider_and_answer(env, monkeypatch):  # noqa: F811
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    svc, addons, tmp = env
    svc.gemini_factory = FakeGemini
    # without a key the provider cannot be chosen; a rejected key changes nothing
    svc.handle({"type": "set_provider", "value": "gemini", "session": "S", "nextInbox": 1})
    assert _read_inbox(addons, 1)["kind"] == "error"
    svc.handle({"type": "set_gemini_key", "value": "bad-key-1234567890123", "session": "S", "nextInbox": 2})
    assert _read_inbox(addons, 2)["kind"] == "error" and "GEMINI" not in (tmp / ".env").read_text()
    # the first Gemini key switches to Gemini; the OpenRouter key stays
    svc.handle({"type": "set_gemini_key", "value": "AIzaSyTEST0000000000000000000xyz", "session": "S", "nextInbox": 3})
    d = _read_inbox(addons, 3)
    assert d["kind"] == "status" and d["status"]["provider"] == "gemini"
    assert d["status"]["gemini"]["hasKey"] and d["status"]["gemini"]["keyMask"] == "AIzaSy…xyz"
    assert "AIzaSyTEST" not in json.dumps(d)
    env_text = (tmp / ".env").read_text()
    assert "GEMINI_API_KEY=AIzaSyTEST" in env_text and "OPENROUTER_API_KEY=" in env_text
    svc.handle({"type": "set_model", "value": "gemini-3.8-flash", "session": "S", "nextInbox": 4})
    assert _read_inbox(addons, 4)["status"]["gemini"]["model"] == "gemini-3.8-flash"
    svc.handle({"type": "set_model", "value": "openai/gpt-6-luna", "session": "S", "nextInbox": 5})
    assert _read_inbox(addons, 5)["kind"] == "error"

    FakeGemini.seen = []
    _send(svc, {"type": "question", "msgId": 7, "session": "S2", "nextInbox": 8, "conv": "g", "text": "news?",
                "snapshot": {}})
    deadline, final = time.time() + 5, None
    while time.time() < deadline and final is None:
        d = _read_inbox(addons, 8)
        final = d if d.get("final") else None
        time.sleep(0.05)
    assert final and final["kind"] == "answer" and final["model"] == "gemini-3.8-flash"
    assert final["costNote"] == "Gemini API" and final["cost"] is None
    second = FakeGemini.seen[1]
    call = next(m for m in second if m["role"] == "assistant")["tool_calls"][0]
    assert call["extra_content"] == {"google": {"thought_signature": "S1"}}  # the signature goes back
    assert next(m for m in second if m["role"] == "tool")["content"] == "results for forever news"
    assert {"title": "News", "url": "https://news.example/1", "date": ""} in final["sources"]
    svc.handle({"type": "set_provider", "value": "openrouter", "session": "S", "nextInbox": 12})
    assert _read_inbox(addons, 12)["status"]["provider"] == "openrouter"


def test_choose_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
    for var in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = config.Settings(game_dir="x")
    signed_in = {"loggedIn": True}
    assert config.choose_provider(s, None) is None  # nothing at all: the app asks for a key
    assert config.choose_provider(s, {"loggedIn": False}) is None
    assert config.choose_provider(s, signed_in) == "claude_code"
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-from-env")
    assert config.read_key("gemini") == "AIza-from-env"
    assert config.choose_provider(s, None) == "gemini"
    assert config.choose_provider(s, signed_in) == "claude_code"  # Claude Code first
    config.write_key("openrouter", "sk-or-v1-x")
    assert config.choose_provider(s, signed_in) == "openrouter"  # the chosen provider works: it stays
    s.provider = "gemini"
    assert config.choose_provider(s, signed_in) == "gemini"


def test_window_key_dialog_flow_uses_service(env, monkeypatch):  # noqa: F811
    """The window saves keys through Service.save_key (checked first, then saved and used)."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    svc, _, tmp = env
    svc.gemini_factory = FakeGemini
    ok, text = svc.save_key("gemini", "nope")
    assert not ok and "rejected" in text
    done = threading.Event()
    res = {}

    def run():
        res["r"] = svc.save_key("gemini", " AIzaSyGOOD000000000000000000000 ", use=True)
        done.set()
    threading.Thread(target=run).start()
    assert done.wait(5)
    assert res["r"] == (True, "Key saved. Using Google Gemini.")
    assert svc.store.settings.provider == "gemini" and config.read_key("gemini") == "AIzaSyGOOD000000000000000000000"
