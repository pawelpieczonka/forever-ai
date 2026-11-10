import base64
import json
import re
import time
import zlib

import pytest

from foreverai import config
from foreverai.frames import decode_frame, encode_frames
from foreverai.inbox import ensure_pool
from foreverai.openrouter import ToolCall
from foreverai.service import Service
from foreverai.tools import validate_proposal


class FakeClient:
    def __init__(self, key):
        self.key = key
        self.calls = 0

    def credits(self):
        return 6.17

    def models(self):
        return [{"id": "deepseek/deepseek-v4-flash", "in": 0.091, "out": 0.182},
                {"id": "openai/gpt-6-luna", "in": 0.1, "out": 0.5}]

    def key_info(self):
        if self.key == "bad":
            raise RuntimeError("401")
        return {"label": "x"}

    def chat_stream(self, model, messages, tools, search_mode, max_tokens):
        FakeClient.seen_messages = messages
        if not any(m["role"] == "tool" for m in messages):
            yield "tool_calls", [ToolCall("c1", "search_knowledge_base", '{"query":"Balance of Nature"}')]
            yield "done", "tool_calls"
            return
        for word in ["# Plan\n", "Kill ", "7 **[Young Nightsaber]**"]:
            time.sleep(0.01)
            yield "text", word
        yield "usage", {"cost": 0.0012}
        yield "done", "stop"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from foreverai import claude_code
    monkeypatch.setattr(claude_code, "detect", lambda force=False: None)
    monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnop\n")
    addons = tmp_path / "AddOns"
    ensure_pool(addons, size=20)
    store = config.SettingsStore(tmp_path / "settings.json")
    svc = Service(store, None, addons, client_factory=FakeClient, lookahead=4)
    return svc, addons, tmp_path


def _read_inbox(addons, n):
    lua = ""
    for _ in range(50):
        try:
            lua = (addons / f"ForeverAI_Inbox{n:03d}" / "data.lua").read_text()
            break
        except PermissionError:  # being replaced right now
            time.sleep(0.01)
    if "ForeverAI_Receive" not in lua:
        return {}
    b64 = re.search(r'"([^"]+)"', lua).group(1)
    return json.loads(base64.b64decode(b64))


def _send(svc, msg):
    data = zlib.compress(json.dumps(msg).encode())
    for img in encode_frames(data, msg_id=42):
        svc.on_frame(decode_frame(img))


def test_question_flow_writes_final_answer(env):
    svc, addons, _ = env
    _send(svc, {"type": "question", "msgId": 42, "session": "S1", "sigPrefix": "p1", "nextInbox": 3, "conv": "c",
                "text": "What should I do with my quest?", "snapshot": {"game": {"build": "1.60.1.70862"},
                                                                 "quests": [{"title": "The Balance of Nature"}]}})
    deadline = time.time() + 5
    final = None
    while time.time() < deadline and final is None:
        d = _read_inbox(addons, 3)
        if d.get("final"):
            final = d
        time.sleep(0.05)
    assert final, "no final answer"
    d = final
    assert _read_inbox(addons, 6) == d  # whole lookahead holds the newest state
    assert "empty" in (addons / "ForeverAI_Inbox007" / "data.lua").read_text()
    assert d["kind"] == "answer" and d["session"] == "S1" and d["msgId"] == 42
    assert "|cffffd100Plan|r" in d["text"] and "[Young Nightsaber]" in d["text"]
    assert d["status"]["balance"] == 6.17 and d["status"]["provider"] == "openrouter"
    assert "abcdefghijklmnop" not in json.dumps(d) and "keyMask" not in d["status"]
    assert d["cost"] == 0.0012
    sys_prompt = FakeClient.seen_messages[0]["content"]
    assert "is not Classic Era" in sys_prompt and "1.60.1.70862" in FakeClient.seen_messages[1]["content"]


def test_save_key_validates(env):
    svc, addons, tmp = env
    assert svc.save_key("openrouter", "bad")[0] is False
    assert "sk-or-v1-abcdefghijklmnop" in (tmp / ".env").read_text()
    assert svc.save_key("openrouter", "sk-or-v1-NEWKEY0000000999") == (True, "Key saved.")
    assert "NEWKEY" in (tmp / ".env").read_text()


def test_ai_settings_from_an_old_addon_change_nothing(env):
    """The AI settings are in the app window. An add-on older than 0.6.0 (a game not reloaded since the update) gets
    a reply that says so instead of waiting, and nothing changes: no key from the game is saved."""
    svc, addons, tmp = env
    before = (tmp / "settings.json").read_text() if (tmp / "settings.json").exists() else None
    old = [("set_api_key", "sk-or-v1-FROMGAME000000999"), ("set_gemini_key", "AIzaSyFROMGAME0000000000000000"),
           ("set_provider", "gemini"), ("set_model", "deepseek/deepseek-v4-flash"), ("set_effort", "max"),
           ("set_search_mode", "never"), ("dismiss_claude", True)]
    for n, (kind, value) in enumerate(old, 1):
        svc.handle({"type": kind, "value": value, "msgId": n, "session": "S", "nextInbox": n})
        d = _read_inbox(addons, n)
        assert d["kind"] == "error" and d["final"] and d["msgId"] == n and "app window" in d["text"], kind
    assert "FROMGAME" not in (tmp / ".env").read_text()
    after = (tmp / "settings.json").read_text() if (tmp / "settings.json").exists() else None
    assert after == before
    s = svc.store.settings
    assert (s.provider, s.model, s.claude_effort, s.search_mode) == ("openrouter", config.DEFAULT_MODEL, "high", "auto")


def test_ai_settings_in_the_app(env):
    svc, addons, _ = env
    assert svc.set_model("nope/model") == (False, "Model nope/model does not exist on OpenRouter.")
    assert svc.set_model("  ")[0] is False and svc.store.settings.model == config.DEFAULT_MODEL
    assert svc.set_model(" deepseek/deepseek-v4-flash ") == (True, "Model: deepseek/deepseek-v4-flash")
    assert svc.store.settings.model == "deepseek/deepseek-v4-flash"
    assert svc.set_search_mode("sometimes")[0] is False
    assert svc.set_search_mode("never") == (True, "Web search: off") and svc.store.settings.search_mode == "never"
    assert svc.set_effort("huge")[0] is False and svc.store.settings.claude_effort == "high"
    assert svc.set_provider("gemini") == (False, "No Google Gemini key yet. Enter it in Keys & sign-in… first.")
    assert svc.set_provider("nobody")[0] is False and svc.store.settings.provider == "openrouter"
    # the model list of the app window: the chosen model first, prices from OpenRouter
    choices = svc.model_choices("openrouter")
    assert choices[0] == {"id": "deepseek/deepseek-v4-flash", "name": "deepseek/deepseek-v4-flash",
                          "price": "$0.09 / $0.18 per 1M tokens"}
    assert {"id": "openai/gpt-6-luna", "name": "openai/gpt-6-luna", "price": "$0.10 / $0.50 per 1M tokens"} in choices
    assert len({c["id"] for c in choices}) == len(choices) <= 10
    assert [c["name"] for c in svc.model_choices("claude_code")][:2] == ["Haiku 5.5", "Sonnet 5.5"]
    # the game gets the AI in use, not the settings' lists or the key
    st = svc.status()
    assert st["provider"] == "openrouter" and st["model"] == "deepseek/deepseek-v4-flash" and st["searchMode"] == "never"
    assert st["claude"] == {"model": "claude-haiku-5-5", "effort": "high"} and set(st["gemini"]) == {"model"}
    assert not {"keyMask", "models", "suggestClaude"} & set(st)
    # ... and the first reply to a question carries it, so the game header follows a change made in the app
    _send(svc, {"type": "question", "msgId": 5, "session": "S", "nextInbox": 1, "conv": "c", "text": "hi",
                "snapshot": {}})
    first = None
    deadline = time.time() + 5
    while time.time() < deadline:
        d = _read_inbox(addons, 1)
        first = first or (d if d.get("ai") else None)
        if d.get("final"):
            break
        time.sleep(0.01)
    assert first and first["ai"]["model"] == "deepseek/deepseek-v4-flash" and first["ai"]["searchMode"] == "never"
    assert d.get("final") and d["status"]["model"] == "deepseek/deepseek-v4-flash"


def test_claude_code_provider(env, monkeypatch):
    from foreverai import claude_code
    svc, addons, _ = env
    # not installed -> switching is refused
    assert svc.set_provider("claude_code")[0] is False and svc.store.settings.provider == "openrouter"

    info = {"path": "claude.exe", "version": "2.1.304", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    st = svc.status()
    assert st["claude"]["model"] == "claude-haiku-5-5" and st["claude"]["effort"] == "high"

    seen = {"prompts": []}

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            seen.update(model=model, effort=effort, system=system, bridge=bridge_url, sessions=seen.get("sessions", 0) + 1)
            self.key = (model, effort, search_mode, system)
            self.workdir = workdir
            self.conv, self.turns, self.busy = None, 0, False

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            seen["prompts"].append(prompt)
            self.turns += 1
            yield "phase", "Searching the knowledge base…"
            # tools go through the bridge into the companion, like the MCP server does
            from foreverai.mcp_server import http_bridge
            call = http_bridge(seen["bridge"].replace("/mcp", "/tool"), svc._bridge.token)
            call("propose_addon_change", {"addon": "Questie", "op": "slash_command", "command": "/questie",
                                          "description": "x"})
            call("save_memory", {"text": "Wants to clear the Barrow Deeps raid"})
            yield "text", "# Raids\n9 December"
            yield "final", "# Raids\n**9 December** 2026"

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    assert svc.set_provider("claude_code") == (True, "AI provider: Claude Code · Haiku 5.5 · thinking high")
    assert svc.set_effort("xhigh") == (True, "Thinking level: very high")
    assert svc.set_model("gpt-6") == (False, "gpt-6 is not a Claude model.")
    assert svc.status()["provider"] == "claude_code" and svc.status()["claude"]["effort"] == "xhigh"

    svc.handle({"type": "question", "msgId": 77, "session": "S", "nextInbox": 4, "conv": "c", "text": "When do raids open?",
                "snapshot": {"game": {"build": "1.60.1.70862"}}})
    deadline = time.time() + 5
    d = {}
    while time.time() < deadline and not d.get("final"):
        d = _read_inbox(addons, 4)
        time.sleep(0.05)
    assert d.get("final") and "|cffffd100Raids|r" in d["text"] and "9 December" in d["text"]
    assert d["costNote"] == "Claude subscription" and d["model"] == "Haiku 5.5"
    assert d["actions"][0]["addon"] == "Questie"
    assert seen["model"] == "claude-haiku-5-5" and seen["effort"] == "xhigh"
    assert "When do raids open?" in seen["prompts"][-1] and "World of Warcraft: Forever" in seen["system"]
    assert d["status"]["memory"][0]["text"] == "Wants to clear the Barrow Deeps raid"
    # the same conversation continues in the same session with only the new question
    sessions = seen["sessions"]
    svc.handle({"type": "question", "msgId": 78, "session": "S", "nextInbox": 10, "conv": "c", "text": "And heroics?",
                "snapshot": {"game": {"build": "1.60.1.70862"}}})
    deadline = time.time() + 5
    while time.time() < deadline and not _read_inbox(addons, 10).get("final"):
        time.sleep(0.05)
    assert seen["sessions"] == sessions and "When do raids open?" not in seen["prompts"][-1]
    assert "And heroics?" in seen["prompts"][-1] and "1: Wants to clear the Barrow Deeps raid" in seen["prompts"][-1]
    # another conversation -> a new session that gets the earlier turns of that conversation
    svc.handle({"type": "question", "msgId": 79, "session": "S", "nextInbox": 15, "conv": "other", "text": "Hi",
                "snapshot": {}})
    deadline = time.time() + 5
    while time.time() < deadline and not _read_inbox(addons, 15).get("final"):
        time.sleep(0.05)
    assert seen["sessions"] == sessions + 1


def test_memory_and_persistent_conversations(env):
    svc, addons, tmp = env
    base = {"session": "S", "char": "Aldric-Old Realm"}
    svc.handle({**base, "type": "memory_add", "value": "Build: Marksmanship, Agility > Stamina", "nextInbox": 1})
    st = _read_inbox(addons, 1)["status"]
    assert st["memory"][0]["text"].startswith("Build: Marksmanship")
    mem_id = st["memory"][0]["id"]
    svc.handle({**base, "type": "memory_add", "value": "Prefers short answers", "nextInbox": 2})
    svc.handle({**base, "type": "memory_delete", "value": mem_id, "nextInbox": 3})
    assert [m["text"] for m in _read_inbox(addons, 3)["status"]["memory"]] == ["Prefers short answers"]

    # memory reaches the system prompt; the conversation is persisted and survives a restart
    svc.handle({**base, "type": "question", "msgId": 5, "nextInbox": 4, "conv": "convA", "text": "What next?",
                "snapshot": {"game": {"build": "1.60.1"}}})
    deadline = time.time() + 5
    while time.time() < deadline and not _read_inbox(addons, 4).get("final"):
        time.sleep(0.05)
    assert "Prefers short answers" in FakeClient.seen_messages[1]["content"]
    assert "Prefers" not in FakeClient.seen_messages[0]["content"]  # system prompt stays cacheable
    from foreverai.service import Service
    svc2 = Service(svc.store, None, addons, client_factory=FakeClient, lookahead=4)
    assert svc2.convs.history("convA")[0]["q"] == "What next?"
    svc2.handle({**base, "type": "status", "nextInbox": 9, "deleteConvs": ["convA"]})
    assert svc2.convs.history("convA") == []
    svc2.handle({**base, "type": "memory_clear", "nextInbox": 10})
    assert _read_inbox(addons, 10)["status"]["memory"] == []


def test_links_from_knowledge_base(env, tmp_path):
    from foreverai.kb.store import KB
    from foreverai.wowformat import bracket_names, md_to_wow
    svc, _, _ = env
    kb = KB(tmp_path / "links.sqlite")
    kb.upsert_page("wiki", "1", "Alliance Outrunner Bow", "u", "1", "s", "t", "A bow.\n## External links\n[Elinks-item: 285347]")
    kb.upsert_page("wiki", "2", "A Difficult Path (Forever)", "u", "1", "s", "t", "Quest.\n## External links\n[Elinks-quest: 98601]")
    svc.kb = kb
    links = svc.links("Buy [Alliance Outrunner Bow] and do [A Difficult Path], then [Unknown Thing].")
    assert {"name": "Alliance Outrunner Bow", "kind": "item", "id": 285347} in links
    assert {"name": "A Difficult Path", "kind": "quest", "id": 98601} in links
    assert len(links) == 2
    assert bracket_names("[Worn Dagger] and [Rough Arrow] plus [Wiki](http://x) and [Worn Dagger]") == ["Worn Dagger", "Rough Arrow"]
    assert "□" not in md_to_wow("Good luck! 🙂🏹") and "Good luck!" in md_to_wow("Good luck! 🙂🏹")


def test_mcp_server_protocol():
    from foreverai.mcp_server import Server
    calls = []
    srv = Server(lambda name, args: (calls.append((name, args)) or (f"ok {name}", False)))
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["capabilities"] == {"tools": {}}
    names = [t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names == ["search_knowledge_base", "propose_addon_change", "save_memory", "delete_memory", "fetch_url",
                     "mark_on_map", "edit_route", "plan_route", "addon_options", "propose_quest_abandon", "propose_vendor_action",
                     "search_auction_house"]
    res = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "addon_options", "arguments": {"addon": "Questie"}}})
    assert res["result"] == {"content": [{"type": "text", "text": "ok addon_options"}], "isError": False}
    assert calls == [("addon_options", {"addon": "Questie"})]
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_bridge_rejects_wrong_token():
    import urllib.error
    from foreverai.bridge import Bridge
    from foreverai.mcp_server import http_bridge
    br = Bridge(lambda name, args: (f"hi {args.get('x')}", False))
    try:
        assert http_bridge(br.url, br.token)("t", {"x": 1}) == ("hi 1", False)
        with pytest.raises(urllib.error.HTTPError):
            http_bridge(br.url, "wrong")("t", {})
    finally:
        br.close()


def test_game_request_without_answer(env):
    """A request the game never answers ends with a clear message instead of hanging."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    writer = InboxWriter(addons, "S", 1, 20, 4)
    writer.send({"kind": "answer", "final": False, "msgId": 5, "text": "", "phase": "x"})
    ctx = TurnContext({"msgId": 5}, writer, {}, threading.Event(), [])
    ctx.cancel.set()
    text, is_error = svc.run_tool(ctx, "addon_options", {"addon": "Questie", "query": "minimap"})
    assert is_error and "did not respond" in text
    assert writer.requests == []


def test_memory_tools(env):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    svc._char = "Ross"
    ctx = TurnContext({"msgId": 1}, InboxWriter(addons, "S", 1, 20, 4), {}, threading.Event(), [])
    text, err = svc.run_tool(ctx, "save_memory", {"text": "Build: BM hunter"})
    assert not err and "id 1" in text
    assert svc.run_tool(ctx, "save_memory", {"text": "build: bm hunter"})[1] is True  # duplicate
    assert svc.run_tool(ctx, "delete_memory", {"id": 7})[1] is True
    assert svc.run_tool(ctx, "delete_memory", {"id": 1}) == ("Deleted from memory.", False)
    assert svc.memory.get("Ross") == []


def test_final_answer_does_not_wait_on_network(env, monkeypatch):
    svc, addons, _ = env
    calls = []

    class SlowClient(FakeClient):
        def credits(self):
            calls.append(time.time())
            time.sleep(1.0)
            return 5.0

    svc.client_factory = SlowClient
    svc._balance = (6.0, time.time())  # already cached
    t0 = time.time()
    st = svc._status_after(0.5)
    assert time.time() - t0 < 0.3  # refresh happens in the background
    assert abs(st["balance"] - 5.5) < 1e-9  # known cost applied immediately


def test_writer_lookahead_covers_slow_answers(env):
    svc, addons, _ = env
    from foreverai.service import InboxWriter
    assert InboxWriter(addons, "S", 1, 1000).lookahead >= 150


def test_validate_mark():
    from foreverai.tools import validate_mark
    m, _ = validate_mark({"title": "Conservator Ilthalaine", "x": 58.7, "y": 44.3, "zone": "Teldrassil"})
    assert m == {"title": "Conservator Ilthalaine", "x": 58.7, "y": 44.3, "zone": "Teldrassil"}
    m2, _ = validate_mark({"title": "Fraction", "x": 0.5, "y": 0.25})
    assert (m2["x"], m2["y"]) == (50.0, 25.0)
    bad, msg = validate_mark({"title": "Bad", "x": 140, "y": 3})
    assert bad is None and "outside 0-100" in msg


def test_validate_proposal():
    snap = {"addons": [{"name": "Questie"}],
            "addonSettings": {"addon": "Questie", "vars": {"QuestieConfig": {"profile": {"enableMapIcons": True,
                                                                                         "iconScale": 0.7}}}}}
    ok, _ = validate_proposal({"addon": "Questie", "op": "set_setting", "path": "QuestieConfig.profile.enableMapIcons",
                               "value": False, "description": "Hides the icons"}, snap)
    assert ok and ok["old"] is True and ok["value"] is False
    bad, msg = validate_proposal({"addon": "Questie", "op": "set_setting", "path": "QuestieConfig.profile.nope",
                                  "value": 1, "description": "x"}, snap)
    assert bad is None and "does not exist" in msg
    wrong_type, _ = validate_proposal({"addon": "Questie", "op": "set_setting", "path": "QuestieConfig.profile.iconScale",
                                       "value": "big", "description": "x"}, snap)
    assert wrong_type is None
    blocked, _ = validate_proposal({"addon": "Questie", "op": "slash_command", "command": "/run print(1)",
                                    "description": "x"}, snap)
    assert blocked is None


def test_prefetched_sources_only_when_used(env):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    _, addons, _ = env
    ctx = TurnContext({"msgId": 1}, InboxWriter(addons, "S", 1, 20, 4), {}, threading.Event(),
                      [{"title": "Songblade Stabilizer", "url": "u1", "date": ""},
                       {"title": "Barrow Deeps (Forever)", "url": "u2", "date": ""}])
    ctx.pre_urls = {"u1", "u2"}
    ctx.add_source({"title": "Hunter trainer", "url": "u3", "date": ""})  # looked up by the model
    assert [x["url"] for x in ctx.used_sources("The Barrow Deeps raid opens on 9 December.")] == ["u2", "u3"]


def test_memory_follows_character_across_realm_names(tmp_path):
    from foreverai.memory import MemoryStore
    m = MemoryStore(tmp_path / "memory.json")
    m.add("Aldric-Old Realm", "Build: Beast Mastery by Gladius Gaming")
    m.add("Aldric-New Realm", "Goal: level 10 today")
    m.add("Other-New Realm", "Another character")
    texts = [i["text"] for i in m.get("Aldric-New Realm")]
    assert texts == ["Build: Beast Mastery by Gladius Gaming", "Goal: level 10 today"] or \
           sorted(texts) == sorted(["Build: Beast Mastery by Gladius Gaming", "Goal: level 10 today"])
    assert [i["id"] for i in m.get("Aldric-New Realm")] == [1, 2]
    assert "Aldric-Old Realm" not in m._data and m.get("Other-New Realm")[0]["text"] == "Another character"
    assert MemoryStore(tmp_path / "memory.json").get("Aldric-New Realm") == m.get("Aldric-New Realm")


def test_addon_options_and_set_option(env):
    """The model looks the option up in the game, then proposes a validated change."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    writer = InboxWriter(addons, "S", 1, 20, 4)
    writer.send({"kind": "answer", "final": False, "msgId": 9, "text": "", "phase": "x"})
    snap = {"addons": [{"name": "Questie"}, {"name": "TomTom"}]}
    ctx = TurnContext({"msgId": 9}, writer, snap, threading.Event(), [])
    # proposing before looking the option up is refused
    action, msg = None, svc.run_tool(ctx, "propose_addon_change", {
        "addon": "Questie", "op": "set_option", "path": "tracker.setTomTom", "value": "ctrlright", "description": "x"})
    assert msg[1] is True and "addon_options" in msg[0]
    out = {}
    t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "addon_options",
                                                                   {"addon": "Questie", "query": "tomtom arrow"})))
    t.start()
    deadline = time.time() + 5
    req = None
    while time.time() < deadline and not req:
        req = (_read_inbox(addons, 1).get("requests") or [None])[0]
        time.sleep(0.02)
    assert req["op"] == "addon_options" and req["addon"] == "Questie" and req["query"] == "tomtom arrow"
    _send(svc, {"type": "game_result", "id": req["id"], "result": {"addon": "Questie", "apps": ["Questie"], "matched": 2,
        "options": [
            {"app": "Questie", "path": "tracker_tab.binds.setTomTom", "type": "select", "name": "Set TomTom Target",
             "value": "ctrlleft", "values": {"left": "Left Click", "ctrlleft": "Ctrl + Left Click",
                                              "disabled": "Disabled"},
             "desc": "This shortcut will set the TomTom arrow"},
            {"app": "Questie", "path": "general.enabled", "type": "toggle", "name": "Enable Questie", "value": True}],
        "slashCommands": ["/questie"]}})
    t.join(5)
    text, err = out["r"]
    assert not err and "path=tracker_tab.binds.setTomTom" in text and 'current="ctrlleft"' in text
    assert "ctrlleft=Ctrl + Left Click" in text and "/questie" in text
    # a value outside the allowed keys is refused; a label is mapped to its key
    text, err = svc.run_tool(ctx, "propose_addon_change", {
        "addon": "Questie", "op": "set_option", "path": "tracker_tab.binds.setTomTom", "value": "middle",
        "description": "x"})
    assert err and "allowed values" in text
    text, err = svc.run_tool(ctx, "propose_addon_change", {
        "addon": "Questie", "op": "set_option", "path": "tracker_tab.binds.setTomTom", "value": "Left Click",
        "description": "TomTom arrow on a plain click"})
    assert not err
    a = ctx.actions[-1]
    assert a["op"] == "set_option" and a["app"] == "Questie" and a["value"] == "left" and a["old"] == "ctrlleft"
    assert a["optionName"] == "Set TomTom Target" and a["valueLabel"] == "Left Click"
    text, err = svc.run_tool(ctx, "propose_addon_change", {
        "addon": "Questie", "op": "set_option", "path": "general.enabled", "value": "false", "description": "x"})
    assert not err and ctx.actions[-1]["value"] is False


def test_screen_is_attached_only_when_the_player_ticks_it(env, monkeypatch):
    """The AI cannot ask for the screen; it gets the image only with a question the player sent with "Screen"."""
    from foreverai import claude_code
    from foreverai.tools import TOOLS
    assert "view_screen" not in [t["function"]["name"] for t in TOOLS]
    svc, addons, _ = env
    svc.screen_for = lambda msg_id: b"\xff\xd8JPEG"
    monkeypatch.setattr(claude_code, "detect", lambda force=False: {"path": "claude.exe", "loggedIn": True})
    seen = []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            self.key = (model, effort, search_mode, system)
            self.workdir, self.conv, self.turns, self.busy = workdir, None, 0, False

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            seen.append((prompt, images))
            yield "final", "ok"

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.store.update(provider="claude_code")
    for inbox, want in ((1, False), (6, True)):
        svc.handle({"type": "question", "msgId": 70 + inbox, "session": "S", "nextInbox": inbox, "conv": "c",
                    "text": "What is this window?", "wantScreen": want, "snapshot": {}})
        deadline = time.time() + 5
        while time.time() < deadline and not _read_inbox(addons, inbox).get("final"):
            time.sleep(0.05)
    assert seen[0][1] == [] and "attached the current screen" not in seen[0][0]
    assert seen[1][1][0]["source"]["data"] == "/9hKUEVH" and "attached the current screen" in seen[1][0]


def test_multiselect_option_proposal(env):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    ctx = TurnContext({"msgId": 3}, InboxWriter(addons, "S", 1, 20, 4), {"addons": [{"name": "Questie"}]},
                      threading.Event(), [])
    ctx.known[("questie", "icons_tab.icon_toggles_group.vendorOptions")] = {
        "app": "Questie", "path": "icons_tab.icon_toggles_group.vendorOptions", "type": "multiselect",
        "name": "Vendors", "values": {"Repair": "Repair", "Bags": "Bags"}, "value": {"Repair": True, "Bags": False}}
    text, err = svc.run_tool(ctx, "propose_addon_change", {"addon": "Questie", "op": "set_option",
        "path": "icons_tab.icon_toggles_group.vendorOptions", "value": "Bags=true", "description": "Show bag vendors"})
    assert not err, text
    a = ctx.actions[-1]
    assert a["value"] == {"Bags": True} and a["oldLabel"] == "Bags: off" and a["valueLabel"] == "Bags: on"
    text, err = svc.run_tool(ctx, "propose_addon_change", {"addon": "Questie", "op": "set_option",
        "path": "icons_tab.icon_toggles_group.vendorOptions", "value": {"Nope": True}, "description": "x"})
    assert err and "unknown choice" in text


def test_quest_abandon_proposal(env):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    snap = {"quests": ["Quest log: 2/20",
                       {"id": 3522, "title": "Iverron's Antidote", "status": "FAILED (time ran out or a condition was broken)"},
                       {"id": 916, "title": "Webwood Venom", "status": "in progress"}]}
    ctx = TurnContext({"msgId": 4}, InboxWriter(addons, "S", 1, 20, 4), snap, threading.Event(), [])
    text, err = svc.run_tool(ctx, "propose_quest_abandon", {"quest_id": 9999, "reason": "x"})
    assert err and "not in the player's quest log" in text
    text, err = svc.run_tool(ctx, "propose_quest_abandon", {"quest_id": 3522, "reason": "Quest failed: time ran out"})
    assert not err and "Iverron's Antidote" in text
    a = ctx.actions[-1]
    assert a["op"] == "abandon_quest" and a["questId"] == 3522 and a["title"] == "Iverron's Antidote"


def test_answer_that_claims_a_mark_gets_one(env):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    snap = {"zoneNPCs": ["Lyrai <General Supplies> [vendor] — Teldrassil 59.5, 40.9 (87 m)"],
            "quests": [{"id": 1, "title": "Q", "turnIn": "Iverron (Teldrassil 54.6, 33.0)"}]}
    ctx = TurnContext({"msgId": 5}, InboxWriter(addons, "S", 1, 20, 4), snap, threading.Event(), [])
    svc.ensure_marks(ctx, "Sell at Lyrai (59.5, 40.9), about 87 m away. I marked her on the map.")
    assert ctx.marks == [{"title": "Lyrai", "x": 59.5, "y": 40.9, "zone": ""}]
    ctx2 = TurnContext({"msgId": 6}, InboxWriter(addons, "S", 1, 20, 4), snap, threading.Event(), [])
    svc.ensure_marks(ctx2, "Iverron stands by the cave (54.6, 33.0).")  # no claim -> nothing added
    assert ctx2.marks == []
    svc.ensure_marks(ctx2, "Iverron (54.6, 33.0) is marked on your map.")
    assert ctx2.marks[0]["title"] == "Iverron"


def test_vendor_sell_and_repair(env):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    bags = {"items": [
        {"item": "[Ruined Pelt]", "id": 4865, "count": 6, "sellCopper": 5, "junk": True},
        {"item": "[Sticky Ichor]", "id": 3576, "count": 1, "sellCopper": 7, "junk": True},
        {"item": "[Hearthstone]", "id": 6948, "count": 1},
        {"item": "[Webwood Venom Sac]", "id": 5166, "count": 3}]}
    closed = TurnContext({"msgId": 1}, InboxWriter(addons, "S", 1, 20, 4), {"bags": bags}, threading.Event(), [])
    text, err = svc.run_tool(closed, "propose_vendor_action", {"op": "sell", "items": ["Ruined Pelt"], "reason": "x"})
    assert err and "not open" in text
    snap = {"bags": bags, "windows": {"merchant": {"npc": "Lyrai", "canRepair": True, "repairCost": "0g 0s 42c",
                                                   "repairCopper": 42}}}
    ctx = TurnContext({"msgId": 2}, InboxWriter(addons, "S", 1, 20, 4), snap, threading.Event(), [])
    text, err = svc.run_tool(ctx, "propose_vendor_action", {
        "op": "sell", "items": ["Ruined Pelt", "3576", "Hearthstone", "Nope"], "reason": "Junk"})
    assert not err and "37c" in text and "Hearthstone" in text and "Nope" in text
    a = ctx.actions[-1]
    assert a["op"] == "vendor_sell" and [i["id"] for i in a["items"]] == [4865, 3576] and a["total"] == "37c"
    assert a["items"][0]["price"] == "30c"
    text, err = svc.run_tool(ctx, "propose_vendor_action", {"op": "repair", "reason": "Durability is low"})
    assert not err and ctx.actions[-1]["op"] == "vendor_repair" and ctx.actions[-1]["cost"] == "0g 0s 42c"


def test_mcp_over_http_in_the_app():
    """Claude Code talks to the app's own MCP endpoint (no extra process)."""
    import urllib.request
    from foreverai.bridge import Bridge
    br = Bridge(lambda name, args: (f"ok {name} {args.get('query')}", False))
    try:
        def post(body, token=br.token):
            req = urllib.request.Request(br.mcp_url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Token": token,
                                                  "Accept": "application/json, text/event-stream"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, (json.loads(r.read() or b"null"))
        st, init = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert st == 200 and init["result"]["serverInfo"]["name"] == "foreverai"
        assert post({"jsonrpc": "2.0", "method": "notifications/initialized"})[0] == 202
        st, res = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "search_knowledge_base", "arguments": {"query": "raid"}}})
        assert res["result"]["content"][0]["text"] == "ok search_knowledge_base raid"
        import urllib.error
        with pytest.raises(urllib.error.HTTPError):
            post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, token="bad")
    finally:
        br.close()


def _wait_final(addons, n, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = _read_inbox(addons, n)
        if d.get("final"):
            return d
        time.sleep(0.05)
    raise AssertionError("no final answer")


class MarkingClient(FakeClient):
    def chat_stream(self, model, messages, tools, search_mode, max_tokens):
        if not any(m["role"] == "tool" for m in messages):
            yield "tool_calls", [ToolCall("c1", "mark_on_map", '{"title":"Dazalar","x":56.7,"y":59.5}'),
                                 ToolCall("c2", "save_memory", '{"text":"Plays Beast Mastery"}')]
            return
        yield "text", "Go to Dazalar."


def test_what_an_answer_did_stays_in_the_conversation(env):
    svc, addons, tmp = env
    svc = Service(svc.store, None, addons, client_factory=MarkingClient, lookahead=4)
    svc.handle({"type": "question", "msgId": 5, "session": "S", "nextInbox": 2, "conv": "c", "char": "A-R",
                "text": "Where do I turn it in?", "snapshot": {}})
    _wait_final(addons, 2)
    (turn,) = svc.convs.history("c")
    assert turn["did"] == ["marked on the map (earlier marks removed): Dazalar (56.7, 59.5)",
                           "saved to memory: Plays Beast Mastery"]


def test_marks_of_an_answer_replace_earlier_ones_unless_added(env):
    svc, addons, _ = env

    class Client(FakeClient):
        marks = []

        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall(f"c{i}", "mark_on_map", json.dumps(m)) for i, m in enumerate(self.marks)]
                return
            yield "text", "Done."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)

    def ask(n, marks):
        Client.marks = marks
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": "mark it",
                    "snapshot": {}})
        return _wait_final(addons, n)

    d = ask(2, [{"title": "1. Dazalar", "x": 56.7, "y": 59.5}, {"title": "2. Byancie", "x": 55.3, "y": 56.8}])
    assert d["marksReplace"] is True and len(d["marks"]) == 2
    assert svc.convs.history("c")[-1]["did"] == ["marked on the map (earlier marks removed): 1. Dazalar (56.7, 59.5)",
                                                 "marked on the map: 2. Byancie (55.3, 56.8)"]
    d = ask(8, [{"title": "Lyreena", "x": 57.6, "y": 56.6, "add": True}])
    assert d["marksReplace"] is False
    assert svc.convs.history("c")[-1]["did"] == ["added to the marks on the map: Lyreena (57.6, 56.6)"]


def test_claude_sessions_started_together_get_their_own_folders(env, monkeypatch):
    from foreverai import claude_code
    svc, addons, _ = env
    folders = []

    class FakeSession:
        def __init__(self, exe, workdir, *rest):
            folders.append(str(workdir))

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    other = Service(svc.store, None, addons, client_factory=FakeClient, lookahead=4)
    for _ in range(20):  # within the same millisecond
        svc._new_claude_session("claude", "system")
        other._new_claude_session("claude", "system")
    assert len(set(folders)) == 40


def test_plan_route_marks_its_stops(env):
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            Client.tools = [t["function"]["name"] for t in tools]
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "plan_route", "{}")]
                return
            Client.result = messages[-1]["content"]
            yield "text", "Here is the route."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    snapshot = {"player": {"coords": "50.0, 50.0", "map": "Teldrassil", "level": 10}, "quests": [
        {"id": 2, "title": "Letter", "status": "READY TO TURN IN", "turnIn": "Postman (Teldrassil 65.0, 50.0)"},
        {"id": 1, "title": "Far Hunt", "status": "in progress", "turnIn": "Giver (Teldrassil 50.0, 60.0)",
         "whereToGet": ["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]}]}
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "plan my route",
                "snapshot": snapshot})
    d = _wait_final(addons, 3)
    assert "plan_route" in Client.tools
    assert d["marks"] == [{"title": "1. Postman", "x": 65.0, "y": 50.0, "zone": "Teldrassil"},
                          {"title": "2. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar"},
                          {"title": "3. Giver", "x": 50.0, "y": 60.0, "zone": "Teldrassil"}]
    assert d["marksReplace"] is True
    assert Client.result.startswith("Route from 50, 50 (Teldrassil): 3 stops, about 4.9 km.")
    assert Client.result.endswith("The 3 stops are marked on the map in this order (the marks of earlier answers are off "
                                  "the map); do not mark them again. To mark other stops instead, call mark_on_map for each of them.")


def test_every_tool_is_offered_to_claude_code():
    from foreverai import claude_code
    from foreverai.tools import TOOLS
    names = {t["function"]["name"] for t in TOOLS}
    assert {t.removeprefix("mcp__foreverai__") for t in claude_code.MCP_TOOLS} == names
    assert all(claude_code.tool_phase(n) for n in names)


def test_marks_after_plan_route_replace_the_route_marks(env):
    """The model asked for the route and then marked its own choice of stops: those are the marks, not both sets."""
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "plan_route", "{}"),
                                     ToolCall("c2", "mark_on_map", '{"title": "Only this", "x": 65, "y": 50}'),
                                     ToolCall("c3", "mark_on_map", '{"title": "And this", "x": 50, "y": 60}')]
                return
            yield "text", "Marked."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    snapshot = {"player": {"coords": "50.0, 50.0", "map": "Teldrassil", "level": 10}, "quests": [
        {"id": 2, "title": "Letter", "status": "READY TO TURN IN", "turnIn": "Postman (Teldrassil 65.0, 50.0)"},
        {"id": 3, "title": "Parcel", "status": "READY TO TURN IN", "turnIn": "Giver (Teldrassil 50.0, 60.0)"}]}
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "mark two",
                "snapshot": snapshot})
    d = _wait_final(addons, 3)
    assert [m["title"] for m in d["marks"]] == ["Only this", "And this"] and d["marksReplace"] is True


def _ctx(svc, addons, snapshot=None):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    return TurnContext({"msgId": 5}, InboxWriter(addons, "S", 1, 20, 4), snapshot or {}, threading.Event(), [])


def test_the_model_decides_what_the_route_holds(env):
    svc, addons, _ = env
    snapshot = {"player": {"coords": "50.0, 50.0", "map": "Teldrassil", "level": 10}, "quests": [
        {"id": 1, "title": "Far Hunt", "status": "in progress", "level": 12, "type": "Elite",
         "turnIn": "Giver (Teldrassil 50.0, 60.0)",
         "whereToGet": ["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]},
        {"id": 2, "title": "Letter", "status": "READY TO TURN IN", "turnIn": "Postman (Teldrassil 65.0, 50.0)"}],
        "nearbyQuests": [{"id": 9, "title": "Fresh", "giver": "Elder", "level": 9, "where": "Teldrassil 55.0, 50.0"},
                         {"id": 8, "title": "Ursal the Mauler", "giver": "Athridas", "where": "Teldrassil 56.0, 50.0"}]}
    ctx = _ctx(svc, addons, snapshot)
    text, err = svc.run_tool(ctx, "plan_route", {"skip": ["Far Hunt"], "new_quests": ["Fresh"], "end_x": 70, "end_y": 50,
                                                 "end_title": "the road east"})
    assert not err and [m["title"] for m in ctx.marks] == ["1. Elder", "2. Postman"]
    assert "ending near the road east." in text and "- [Far Hunt]: left out as asked" in text
    # hard quests at the end
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "plan_route", {"last": ["Far Hunt"]})
    assert [m["title"] for m in ctx.marks] == ["1. Postman", "2. Boar", "3. Giver"]


def test_an_answer_holds_twelve_marks_and_says_when_one_does_not_fit(env):
    svc, addons, _ = env
    ctx = _ctx(svc, addons)
    results = [svc.run_tool(ctx, "mark_on_map", {"title": f"Stop {n}", "x": 10 + n, "y": 20}) for n in range(1, 14)]
    assert len(ctx.marks) == 12 and all(not err for _, err in results[:12])
    assert results[12] == ("Not marked: an answer holds at most 12 marks, and Stop 13 would be one more.", True)


def test_tool_results_count_toward_the_size_of_a_claude_session(env):
    svc, addons, _ = env

    class Session:
        sent_chars = 1000

    svc._claude = Session()
    svc._turn = _ctx(svc, addons)
    text, _ = svc.tool_from_bridge("search_knowledge_base", {"query": "x"})
    assert svc._claude.sent_chars == 1000 + len(text) and len(text) > 10
    svc._claude = None


def test_creatures_of_a_route_and_of_a_mark_are_tracked(env):
    """The game watches for the creatures the player is sent to: the reply names them (and the quest they are for)."""
    svc, addons, _ = env
    snapshot = {"player": {"coords": "50.0, 50.0", "map": "Teldrassil", "level": 10}, "quests": [
        {"id": 1, "title": "Far Hunt", "status": "in progress", "turnIn": "Giver (Teldrassil 50.0, 60.0)",
         "whereToGet": ["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)",
                        "Old Chest: from object Old Chest — Teldrassil 70.0, 50.0 (1600 m, 1 spawn point in your zone)"]}]}
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "plan_route", {})
    assert ctx.track == [{"name": "Boar", "quest": "Far Hunt"}]  # not the chest, not the quest giver
    assert "watching for: Boar" in ctx.did()

    ctx = _ctx(svc, addons, snapshot)
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Strigid Screecher", "x": 64.2, "y": 69.2,
                                                  "mob": "Strigid Screecher"})
    assert not err and ctx.track == [{"name": "Strigid Screecher"}] and "a living one in sight is marked on the screen and announced" in text
    svc.run_tool(ctx, "mark_on_map", {"title": "Dazalar", "x": 56.7, "y": 59.5})
    assert ctx.track == [{"name": "Strigid Screecher"}]
    # marks the model sets after a route replace the route's marks, and with them what the route tracked
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "plan_route", {})
    svc.run_tool(ctx, "mark_on_map", {"title": "Only this", "x": 65, "y": 50})
    assert ctx.track == []


def test_the_reply_tells_the_game_what_to_track(env):
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "mark_on_map",
                                              '{"title": "Strigid Screecher", "x": 64.2, "y": 69.2, "mob": "Strigid Screecher"}')]
                return
            yield "text", "Marked."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "where is it",
                "snapshot": {}})
    d = _wait_final(addons, 3)
    assert d["track"] == [{"name": "Strigid Screecher"}] and len(d["marks"]) == 1


def test_a_watch_that_cannot_be_added_is_not_claimed(env):
    """The reply to mark_on_map says what the game really watches for: not a name that is no name, not a sixth one."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, {})
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Somewhere", "x": 10, "y": 10, "mob": "[]"})
    assert not err and ctx.track == [] and "watches for" not in text
    for n in range(5):
        text, _ = svc.run_tool(ctx, "mark_on_map", {"title": f"Stop {n}", "x": 20 + n, "y": 10, "mob": f"Creature {n}",
                                                    "add": True})
        assert f"The game watches for Creature {n}:" in text
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Stop 5", "x": 30, "y": 10, "mob": "Creature 5", "add": True})
    assert not err and len(ctx.track) == 5 and "The game watches for" not in text
    assert "Creature 5 is not watched for: the game watches for at most 5 creatures at once" in text
    text, _ = svc.run_tool(ctx, "mark_on_map", {"title": "Again", "x": 31, "y": 10, "mob": "creature 0", "add": True})
    assert "The game watches for Creature 0:" in text and len(ctx.track) == 5  # watched already


ROUTE_ON_THE_MAP = {"marks": ["1. Bloodfeather Rogue — Teldrassil 37.3, 37.8 (97 m)",
                              "2. Sentinel Arynia Cloudsbreak — Teldrassil 38.3, 34.4 (115 m)",
                              "3. Oracle Glade Moonwell — Teldrassil 38.4, 34.1",
                              "4. Byancie — Teldrassil 55.3, 56.8",
                              "Taken off the route by the player: Dazalar"],
                    "tracked": ["Bloodfeather Rogue: none in sight now; not seen since it is tracked",
                                "Windmistress Gaedress (also counts for Amulet: Hatescreech): none in sight now"]}


def test_the_model_edits_the_route_on_the_map(env):
    """"I have no tool that removes marks" was the answer when the player wanted two stops gone."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_ON_THE_MAP)
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["3. Oracle Glade Moonwell", "bloodfeather rogue"]})
    assert not err
    assert ctx.route_edit == {"remove": [{"title": "Bloodfeather Rogue", "x": 37.3, "y": 37.8},
                                         {"title": "Oracle Glade Moonwell", "x": 38.4, "y": 34.1}]}
    assert text == ("Taken off the route: Bloodfeather Rogue, Oracle Glade Moonwell. "
                    "The route now: 1. Sentinel Arynia Cloudsbreak, 2. Byancie.")
    assert ctx.did() == ["took off the route: Bloodfeather Rogue, Oracle Glade Moonwell"]

    ctx = _ctx(svc, addons, ROUTE_ON_THE_MAP)
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["2"], "order": ["Byancie"], "stop_tracking": ["windmistress gaedress"]})
    assert not err and ctx.untrack == ["Windmistress Gaedress"]
    assert ctx.route_edit == {"remove": [{"title": "Sentinel Arynia Cloudsbreak", "x": 38.3, "y": 34.4}],
                              "order": [{"title": "Byancie", "x": 55.3, "y": 56.8},
                                        {"title": "Bloodfeather Rogue", "x": 37.3, "y": 37.8},
                                        {"title": "Oracle Glade Moonwell", "x": 38.4, "y": 34.1}]}
    assert text == ("Taken off the route: Sentinel Arynia Cloudsbreak. The route now: 1. Byancie, 2. Bloodfeather Rogue, "
                    "3. Oracle Glade Moonwell. No longer watched for: Windmistress Gaedress.")
    assert ctx.did() == ["took off the route: Sentinel Arynia Cloudsbreak", "put the route in another order",
                         "stopped watching for: Windmistress Gaedress"]

    ctx = _ctx(svc, addons, ROUTE_ON_THE_MAP)
    text, err = svc.run_tool(ctx, "edit_route", {"clear": True, "stop_tracking": ["all"]})
    assert not err and ctx.route_edit == {"clear": True} and ctx.untrack == ["Bloodfeather Rogue", "Windmistress Gaedress"]
    assert ctx.did() == ["cleared the route", "stopped watching for: Bloodfeather Rogue, Windmistress Gaedress"]


def test_an_edit_that_fits_nothing_changes_nothing(env):
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_ON_THE_MAP)
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Dazalar", "Byancie"]})
    assert err and ctx.route_edit is None
    assert text == ("Nothing changed: no stop called Dazalar. The stops on the map: 1. Bloodfeather Rogue, "
                    "2. Sentinel Arynia Cloudsbreak, 3. Oracle Glade Moonwell, 4. Byancie.")
    text, err = svc.run_tool(ctx, "edit_route", {"stop_tracking": ["Strigid Owl"]})
    assert err and ctx.untrack == [] and "nothing called Strigid Owl is watched for" in text
    text, err = svc.run_tool(ctx, "edit_route", {})
    assert err and "say what to change" in text
    text, err = svc.run_tool(_ctx(svc, addons, {}), "edit_route", {"remove": ["Byancie"]})
    assert err and text == "Nothing changed: nothing of yours is marked on the map."


def test_marks_after_an_edit_are_added_to_the_edited_route(env):
    """Replacing the route would undo the edit: a mark set after edit_route is an extra stop."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_ON_THE_MAP)
    svc.run_tool(ctx, "edit_route", {"remove": ["Byancie"]})
    svc.run_tool(ctx, "mark_on_map", {"title": "Dazalar", "x": 56.7, "y": 59.5})
    assert ctx.replaces_marks is False and ctx.did()[-1] == "added to the marks on the map: Dazalar (56.7, 59.5)"
    # a whole new route takes the place of the old one, edited or not
    ctx.snapshot.update({"player": {"coords": "50.0, 50.0", "map": "Teldrassil", "level": 10}, "quests": [
        {"id": 1, "title": "Far Hunt", "status": "in progress", "turnIn": "Giver (Teldrassil 50.0, 60.0)",
         "whereToGet": ["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]}]})
    svc.run_tool(ctx, "plan_route", {})
    assert ctx.route_edit is None and ctx.replaces_marks is True
    assert ctx.marks[0] == {"title": "1. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar"}
    assert "took off the route: Byancie" not in ctx.did()


def test_the_reply_tells_the_game_how_to_change_the_route(env):
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "edit_route", '{"remove": ["Byancie"], "stop_tracking": ["Bloodfeather Rogue"]}'),
                                     ToolCall("c2", "mark_on_map", '{"title": "Owl", "x": 64.2, "y": 69.2, "mob": "Strigid Owl"}')]
                return
            yield "text", "Done."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "drop Byancie",
                "snapshot": ROUTE_ON_THE_MAP})
    d = _wait_final(addons, 3)
    assert d["route"] == {"remove": [{"title": "Byancie", "x": 55.3, "y": 56.8}]} and d["untrack"] == ["Bloodfeather Rogue"]
    assert d["marks"] == [{"title": "Owl", "x": 64.2, "y": 69.2, "zone": "", "mob": "Strigid Owl"}]  # the stop knows its creature
    assert d["marksReplace"] is False
