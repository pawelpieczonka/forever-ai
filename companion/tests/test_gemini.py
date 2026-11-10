import json
import threading
import time

import httpx
import pytest

from foreverai import config, gemini
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


def test_errors_another_model_may_answer():
    def handler(request: httpx.Request):
        model = json.loads(request.content)["model"]
        if model == "busy":
            return httpx.Response(503, json=[{"error": {"code": 503, "message": "The model is overloaded."}}])
        if model == "silent":
            raise httpx.ReadTimeout("The read operation timed out", request=request)
        if model == "streamed-quota":
            return httpx.Response(200, content=_sse({"error": {"code": 429, "message": "Quota exceeded"}}))
        return httpx.Response(400, json=[{"error": {"code": 400, "message": "Invalid argument."}}])

    c = _client(handler)

    def first(model):
        return next(iter(c.chat_stream(model, [], search_mode="never")))
    assert first("busy") == ("unavailable", "Gemini is overloaded or unavailable (503). Try again in a moment.")
    assert first("silent") == ("unavailable", "silent did not answer within 45 s.")
    assert first("streamed-quota")[0] == "unavailable"
    assert first("bad-request")[0] == "error"  # the same request would fail on any model


def test_a_model_that_does_not_start_answering_is_probed():
    """An overloaded model often never answers on the OpenAI-compatible endpoint; a tiny native request tells it from
    a busy model within seconds instead of the 45 s read timeout."""
    release = threading.Event()
    probes = []

    def handler(request: httpx.Request):
        if request.url.path.endswith(":generateContent"):
            model = request.url.path.split("/models/")[1].removesuffix(":generateContent")
            probes.append((model, json.loads(request.content)["generationConfig"]))
            if model == "overloaded":
                return httpx.Response(503, json=[{"error": {"code": 503, "message": "High demand."}}])
            if model == "dead":
                raise httpx.ReadTimeout("The read operation timed out", request=request)
            return httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]})
        model = json.loads(request.content)["model"]
        if model == "broken":
            raise RuntimeError("bug")
        if model in ("overloaded", "dead"):  # no answer at all (until the test ends)
            release.wait(10)
            raise httpx.ReadTimeout("The read operation timed out", request=request)
        if model == "busy":  # slow to start, but it answers
            time.sleep(0.3)
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}]}))

    c = Gemini("AIza-test-key-000000000000000", transport=httpx.MockTransport(handler), check_after=0.05)

    def events(model):
        return list(c.chat_stream(model, [], search_mode="never"))
    try:
        started = time.monotonic()
        assert events("overloaded") == [("unavailable", "Gemini is overloaded or unavailable (503). Try again in a "
                                                        "moment.")]
        assert events("dead") == [("unavailable", "dead is not answering (no reply to a short test request within "
                                                  "8 s).")]
        assert time.monotonic() - started < 3
        assert events("busy") == [("text", "Hello"), ("done", "stop")]  # the probe answered: it waits for the model
        assert probes == [("overloaded", {"maxOutputTokens": 1}), ("dead", {"maxOutputTokens": 1}),
                          ("busy", {"maxOutputTokens": 1})]
        c.check_after = 0.3
        assert events("fast") == [("text", "Hello"), ("done", "stop")]
        time.sleep(0.4)
        assert len(probes) == 3  # an answer that started in time costs no probe
        with pytest.raises(RuntimeError):  # errors in the request's thread are not swallowed
            events("broken")
    finally:
        release.set()


def test_drop_signatures_before_another_model():
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "a", "function": {}, "extra_content": {"google": {"thought_signature": "SIG"}}},
                    {"id": "b", "function": {}}]}]
    gemini.drop_signatures(messages)
    calls = messages[1]["tool_calls"]
    assert calls[0]["extra_content"] == {"google": {"thought_signature": SKIP_SIGNATURE}}
    assert "extra_content" not in calls[1]


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


class BusyGemini(FakeGemini):
    """The chosen model is overloaded; the fallback answers."""
    models_used: list = []

    def chat_stream(self, model, messages, tools, search_mode, max_tokens):
        BusyGemini.models_used.append(model)
        if model != gemini.FALLBACK_MODEL:
            yield "unavailable", f"{model} did not answer within 45 s."
            return
        yield "text", "Hello from the fallback."
        yield "done", "stop"


def _final_answer(addons, n):
    deadline = time.time() + 5
    while time.time() < deadline:
        d = _read_inbox(addons, n)
        if d.get("final"):
            return d
        time.sleep(0.05)
    return None


def test_gemini_falls_back_when_the_model_does_not_answer(env, monkeypatch):  # noqa: F811
    svc, addons, _ = env
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTEST0000000000000000000xyz")
    svc.gemini_factory = BusyGemini
    svc.store.update(provider="gemini", gemini_model="gemini-flash-latest")
    BusyGemini.models_used = []
    _send(svc, {"type": "question", "msgId": 7, "session": "S", "nextInbox": 1, "conv": "g", "text": "hi",
                "snapshot": {}})
    final = _final_answer(addons, 1)
    assert final and final["kind"] == "answer" and final["model"] == gemini.FALLBACK_MODEL
    assert "Hello from the fallback." in final["text"]
    assert BusyGemini.models_used == ["gemini-flash-latest", gemini.FALLBACK_MODEL]
    # the next question does not wait for the model that just failed
    _send(svc, {"type": "question", "msgId": 8, "session": "S", "nextInbox": 5, "conv": "g", "text": "again",
                "snapshot": {}})
    assert _final_answer(addons, 5)["model"] == gemini.FALLBACK_MODEL
    assert BusyGemini.models_used[2:] == [gemini.FALLBACK_MODEL]
    # choosing the model again gives it another chance
    svc.handle({"type": "set_model", "value": "gemini-flash-latest", "session": "S", "nextInbox": 9})
    _send(svc, {"type": "question", "msgId": 9, "session": "S", "nextInbox": 10, "conv": "g", "text": "and now",
                "snapshot": {}})
    assert _final_answer(addons, 10)["model"] == gemini.FALLBACK_MODEL
    assert BusyGemini.models_used[3:] == ["gemini-flash-latest", gemini.FALLBACK_MODEL]


def test_gemini_does_not_wait_for_an_overloaded_model(env, monkeypatch):  # noqa: F811
    """Seen on 2026-11-10: gemini-flash-latest returned 503 to native requests for hours and never answered on the
    OpenAI-compatible endpoint. The fallback answers within seconds, and the model is used again once a probe in the
    background finds that it can answer."""
    svc, addons, _ = env
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTEST0000000000000000000xyz")
    release = threading.Event()
    state = {"overloaded": True, "chat": [], "probes": []}

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": []})
        if request.url.path.endswith(":generateContent"):
            model = request.url.path.split("/models/")[1].removesuffix(":generateContent")
            state["probes"].append(model)
            if state["overloaded"] and model == "gemini-flash-latest":
                return httpx.Response(503, json=[{"error": {"code": 503, "message": "High demand."}}])
            return httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]})
        model = json.loads(request.content)["model"]
        state["chat"].append(model)
        if state["overloaded"] and model == "gemini-flash-latest":
            release.wait(10)
            raise httpx.ReadTimeout("The read operation timed out", request=request)
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": f"Hi from {model}."},
                                                              "finish_reason": "stop"}]}))

    svc.gemini_factory = lambda key: Gemini(key, transport=httpx.MockTransport(handler), check_after=0.05)
    svc.store.update(provider="gemini", gemini_model="gemini-flash-latest")

    def ask(n, inbox):
        _send(svc, {"type": "question", "msgId": n, "session": "S", "nextInbox": inbox, "conv": "g", "text": "hi",
                    "snapshot": {}})
        return _final_answer(addons, inbox)
    try:
        final = ask(7, 1)
        assert final and final["model"] == gemini.FALLBACK_MODEL and final["secs"] < 3
        assert "Hi from gemini-flash-lite-latest." in final["text"]
        assert state["chat"] == ["gemini-flash-latest", gemini.FALLBACK_MODEL]
        assert state["probes"] == ["gemini-flash-latest"]
        # the next questions do not wait for it, and it is not checked again before GEMINI_RECHECK_S
        assert ask(8, 5)["model"] == gemini.FALLBACK_MODEL
        assert state["chat"][2:] == [gemini.FALLBACK_MODEL] and len(state["probes"]) == 1
        # the overload is over: the next question is still answered by the fallback, without waiting, and the check
        # in the background brings the model back for the question after it
        state["overloaded"] = False
        svc._gemini_down["gemini-flash-latest"] = 0  # the check is due
        assert ask(9, 10)["model"] == gemini.FALLBACK_MODEL
        deadline = time.time() + 5
        while "gemini-flash-latest" in svc._gemini_down and time.time() < deadline:
            time.sleep(0.02)
        assert state["probes"] == ["gemini-flash-latest"] * 2
        final = ask(10, 15)
        assert final["model"] == "gemini-flash-latest" and "Hi from gemini-flash-latest." in final["text"]
    finally:
        release.set()


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


def test_key_dialog_paste_shows_and_claude_sign_in(monkeypatch):
    """A pasted key must be visible as "*" (a Tk without fontconfig draws "•" zero wide); Claude Code that is
    installed but not signed in gets a sign-in button that runs claude auth login in a terminal."""
    import tkinter as tk
    import types

    from foreverai import claude_code, window
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    monkeypatch.setattr(claude_code, "_detect_cache", ({"path": "/opt/claude", "loggedIn": False}, time.time()))
    opened = []
    monkeypatch.setattr(window, "open_terminal", lambda argv: opened.append(argv) or True)
    comp = types.SimpleNamespace(store=types.SimpleNamespace(settings=config.Settings(game_dir="x")))
    try:
        d = window.KeyDialog(root, comp)
        d.entry.insert(0, "AIzaSyPASTED00000000000000000000000")  # (not the clipboard: it is the user's)
        assert d.entry.cget("show") == "*" and d.entry.get().startswith("AIzaSyPASTED")
        assert int(root.tk.call("font", "measure", d.entry.cget("font"), "*" * 35)) > 35  # visible
        root.update()
        assert d.sign_in_btn.winfo_manager() == "pack" and "claude desktop app" in d.claude_line.cget("text").lower()
        d.sign_in_btn.invoke()
        assert opened == [["/opt/claude", "auth", "login"]] and "Check Claude Code again" in d.msg.cget("text")
        claude_code._detect_cache = ({"path": "/opt/claude", "loggedIn": True, "version": "2"}, time.time())
        comp.store.settings.provider = "claude_code"
        d._show_claude_button()
        root.update()
        assert d.sign_in_btn.winfo_manager() == "" and d.use_claude_btn.winfo_manager() == ""
    finally:
        root.destroy()


def test_key_dialog_switches_back_to_claude_code(monkeypatch):
    """After a Gemini key was saved, a signed-in Claude Code could not be chosen again in the window: "Check Claude
    Code again" kept the working Gemini. "Use Claude Code" switches back."""
    import tkinter as tk
    import types

    from foreverai import claude_code, window
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    monkeypatch.setattr(claude_code, "_detect_cache", ({"path": "/opt/claude", "loggedIn": True, "version": "2"},
                                                       time.time()))
    settings = config.Settings(game_dir="x", provider="gemini")
    calls = []

    def check_providers(prefer_claude=False):
        calls.append(prefer_claude)
        settings.provider = "claude_code" if prefer_claude else settings.provider
        return settings.provider
    comp = types.SimpleNamespace(store=types.SimpleNamespace(settings=settings), check_providers=check_providers)
    try:
        d = window.KeyDialog(root, comp)
        root.update()
        assert d.use_claude_btn.winfo_manager() == "pack" and d.sign_in_btn.winfo_manager() == ""
        assert "Google Gemini" in d.claude_line.cget("text")
        d.use_claude_btn.invoke()
        deadline = time.time() + 5
        while "Using Claude Code" not in d.msg.cget("text") and time.time() < deadline:
            root.update()
            time.sleep(0.02)
        assert calls == [True] and settings.provider == "claude_code"
        assert d.use_claude_btn.winfo_manager() == "" and "in use" in d.claude_line.cget("text")
    finally:
        root.destroy()


def test_check_providers_prefers_claude_code_on_request(tmp_path, monkeypatch):
    """At start a working saved provider stays; the player's request switches to a signed-in Claude Code."""
    import types

    from foreverai import app, claude_code
    monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
    config.write_key("gemini", "AIza-saved")
    monkeypatch.setattr(claude_code, "detect", lambda force=False: {"loggedIn": True, "version": "2"})
    store = config.SettingsStore(tmp_path / "settings.json")
    store.update(provider="gemini", game_dir="x")
    comp = types.SimpleNamespace(store=store, needs_key=True, claude_checked=False,
                                 service=types.SimpleNamespace(_prewarm_safe=lambda: None))
    assert app.Companion.check_providers(comp) == "gemini" and store.settings.provider == "gemini"
    assert app.Companion.check_providers(comp, prefer_claude=True) == "claude_code"
    assert store.settings.provider == "claude_code" and not comp.needs_key
