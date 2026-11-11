import base64
import json
import re
import time
import zlib

import pytest

from foreverai import config
from foreverai.frames import decode_frame, encode_frames
from foreverai.inbox import ensure_pool
from foreverai.openrouter import ToolCall, is_free
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
                     "search_auction_house", "compare_sell_prices", "auctionator_list"]
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
    assert d["marks"] == [{"title": "1. Postman", "x": 65.0, "y": 50.0, "zone": "Teldrassil",
                           "tasks": [{"kind": "turn_in", "quest": "Letter", "id": 2}]},
                          {"title": "2. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar",
                           "tasks": [{"kind": "objective", "quest": "Far Hunt", "id": 1, "label": "Boar"}]},
                          {"title": "3. Giver", "x": 50.0, "y": 60.0, "zone": "Teldrassil",
                           "tasks": [{"kind": "turn_in", "quest": "Far Hunt", "id": 1}]}]
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
    assert ctx.marks[0] == {"title": "1. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar",
                           "tasks": [{"kind": "objective", "quest": "Far Hunt", "id": 1, "label": "Boar"}]}
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


ROUTE_SNAPSHOT = {"player": {"coords": "50.0, 50.0", "map": "Teldrassil", "level": 10}, "quests": [
    {"id": 1, "title": "Far Hunt", "status": "in progress", "turnIn": "Giver (Teldrassil 50.0, 60.0)",
     "whereToGet": ["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]},
    {"id": 2, "title": "Letter", "status": "READY TO TURN IN", "turnIn": "Postman (Teldrassil 65.0, 50.0)"}],
    "marks": ["1. Old Stop — Teldrassil 40.0, 40.0 (300 m)", "2. Byancie — Teldrassil 55.3, 56.8"]}


def test_a_route_the_player_did_not_ask_for_is_proposed(env):
    """When the AI decides by itself that the route no longer fits, the player decides: a card, not the map."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    text, err = svc.run_tool(ctx, "plan_route", {"skip": ["Letter"], "propose": True})
    assert not err and ctx.marks == [] and ctx.track == [] and ctx.route_marked is False
    (action,) = ctx.actions
    assert action["op"] == "route" and action["addon"] == "Map"
    assert action["stops"] == [{"title": "1. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar",
                           "tasks": [{"kind": "objective", "quest": "Far Hunt", "id": 1, "label": "Boar"}]},
                               {"title": "2. Giver", "x": 50.0, "y": 60.0, "zone": "Teldrassil",
                           "tasks": [{"kind": "turn_in", "quest": "Far Hunt", "id": 1}]}]
    assert action["track"] == [{"name": "Boar", "quest": "Far Hunt"}]
    assert text.endswith("Proposed to the player as a new route: a card with Accept and Reject. The marks on the map "
                         "stay until they accept it; do not mark these stops.")
    assert ctx.did() == ["proposed a new route (waits for the player's Accept): 1. Boar, 2. Giver",
                         "planned the route: left out [Letter]"]
    # a second proposal in the same answer takes the place of the first
    svc.run_tool(ctx, "plan_route", {"propose": True})
    assert [a["op"] for a in ctx.actions] == ["route"] and len(ctx.actions[0]["stops"]) == 3


def test_a_change_of_the_route_can_be_proposed(env):
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Old Stop"], "propose": True})
    assert not err and ctx.route_edit is None and ctx.edit_notes == {}
    (action,) = ctx.actions
    assert action["op"] == "route_edit" and action["edit"] == {"remove": [{"title": "Old Stop", "x": 40.0, "y": 40.0}]}
    assert action["untrack"] == [] and action["summary"] == "Taken off the route: Old Stop. The route now: 1. Byancie."
    assert text.startswith("Proposed to the player (a card with Accept and Reject), nothing changed yet: ")
    assert ctx.did() == ["proposed changing the route (waits for the player's Accept): "
                         "Taken off the route: Old Stop. The route now: 1. Byancie."]


def test_the_choices_of_a_route_are_kept_in_the_conversation(env):
    """An update of the route later keeps what was left out, done last and where it ends."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(ctx, "plan_route", {"skip": ["Letter"], "last": ["Far Hunt"], "end_x": 56.7, "end_y": 59.5,
                                     "end_title": "Dazalar"})
    assert ctx.did()[-1] == "planned the route: left out [Letter]; last [Far Hunt]; ends near Dazalar (56.7, 59.5)"
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(ctx, "plan_route", {})
    assert ctx.did()[-1] == "planned the route: nothing left out"


def test_the_reply_says_whether_the_marks_are_a_planned_route(env):
    """The add-on keeps a planned route up to date when the player wants that; a single mark is not a route."""
    svc, addons, _ = env

    class Client(FakeClient):
        calls_ = []

        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", Client.calls_[0][0], Client.calls_[0][1])]
                return
            yield "text", "Done."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    for n, (call, planned) in enumerate([(("plan_route", "{}"), True),
                                         (("mark_on_map", '{"title": "Dazalar", "x": 56.7, "y": 59.5}'), False),
                                         (("plan_route", '{"propose": true}'), False)]):
        Client.calls_ = [call]
        svc.handle({"type": "question", "msgId": 3 + n, "session": "S", "nextInbox": 3 + 4 * n, "conv": "c",
                    "text": "route", "snapshot": ROUTE_SNAPSHOT})
        assert _wait_final(addons, 3 + 4 * n)["routePlanned"] is planned


def test_a_message_from_the_add_on_is_answered_in_the_language_of_the_conversation(env):
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            Client.user = messages[-1]["content"]
            yield "text", "Trasa zaktualizowana."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "auto": True,
                "text": "[Quest log changed] took [Crown of the Earth]. Update the route on the map.", "snapshot": {}})
    _wait_final(addons, 3)
    assert Client.user.endswith(
        'Now answer this message from the add-on (the player did not type it): "[Quest log changed] took [Crown of the '
        'Earth]. Update the route on the map."\nAnswer in the language of the conversation so far, not in the language '
        "of this message or of the game data.")


def test_a_route_planned_in_this_answer_is_edited_in_place(env):
    """The real answer: plan_route, then edit_route to drop two stops and reorder, then the end point added. The edit
    went to the old route on the map, the model marked the stops again, and the map got ten stops, each twice, and no
    longer counted as a planned route."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(ctx, "plan_route", {})
    assert [m["title"] for m in ctx.marks] == ["1. Postman", "2. Boar", "3. Giver"]
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Postman"], "order": ["giver"]})
    assert not err and ctx.route_edit is None  # the route on the map is replaced by this one, not edited
    assert text == ("Taken off the route: Postman. The route now: 1. Giver, 2. Boar. These marks replace the ones on "
                    "the map; do not mark them again.")
    assert ctx.marks == [{"title": "1. Giver", "x": 50.0, "y": 60.0, "zone": "Teldrassil",
                           "tasks": [{"kind": "turn_in", "quest": "Far Hunt", "id": 1}]},
                         {"title": "2. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar",
                           "tasks": [{"kind": "objective", "quest": "Far Hunt", "id": 1, "label": "Boar"}]}]
    svc.run_tool(ctx, "mark_on_map", {"title": "Dazalar", "x": 56.7, "y": 59.5, "add": True})  # where it ends
    assert [m["title"] for m in ctx.marks] == ["1. Giver", "2. Boar", "Dazalar"]
    assert ctx.route_marked is True and ctx.replaces_marks is True  # an extra stop keeps it a planned route
    assert ctx.track == [{"name": "Boar", "quest": "Far Hunt"}]
    assert "took off the route: Postman" in ctx.did() and "put the route in another order" in ctx.did()

    svc.run_tool(ctx, "edit_route", {"remove": ["2"]})  # the stop with the creature: its watch goes with it
    assert [m["title"] for m in ctx.marks] == ["1. Giver", "Dazalar"] and ctx.track == []
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Byancie"]})
    assert err and text == ("Nothing changed: no stop called Byancie. The stops of the route you marked in this "
                            "answer: 1. Giver, 2. Dazalar.")


def test_the_reply_of_an_edited_planned_route(env):
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "plan_route", "{}"),
                                     ToolCall("c2", "edit_route", '{"remove": ["Postman"]}'),
                                     ToolCall("c3", "mark_on_map", '{"title": "Dazalar", "x": 56.7, "y": 59.5, "add": true}')]
                return
            yield "text", "Done."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "route again",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 3)
    assert [m["title"] for m in d["marks"]] == ["1. Boar", "2. Giver", "Dazalar"]
    assert d["marksReplace"] is True and d["routePlanned"] is True and d["route"] is None


def test_a_place_is_marked_once(env):
    """"Na mapie były zdublowane punkty": the same NPC twice, once with the quest in brackets after the name."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(ctx, "plan_route", {})  # 1. Postman, 2. Boar, 3. Giver
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "4. Boar (Far Hunt)", "x": 80.2, "y": 50.4, "add": True})
    assert err and text == ("Not marked: Boar (Far Hunt) is already on the route as stop 2 (Boar). A place is one "
                            "stop; say in the answer everything to do there.")
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Mailbox", "x": 65.1, "y": 50.1, "add": True})  # the same spot
    assert err and "already on the route as stop 1 (Postman)" in text
    assert [m["title"] for m in ctx.marks] == ["1. Postman", "2. Boar", "3. Giver"]

    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)  # the map: 1. Old Stop, 2. Byancie
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Byancie", "x": 55.3, "y": 56.8, "add": True})
    assert err and text == ("Not marked: Byancie is already on the map as stop 2 (Byancie). A place is one stop; say "
                            "in the answer everything to do there.")
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Byancie", "x": 55.3, "y": 56.8})  # a new set of marks
    assert not err and len(ctx.marks) == 1


def test_marks_say_what_is_to_be_done_there(env):
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(ctx, "plan_route", {})
    assert [m.get("tasks") for m in ctx.marks] == [[{"kind": "turn_in", "quest": "Letter", "id": 2}],
                                                   [{"kind": "objective", "quest": "Far Hunt", "id": 1, "label": "Boar"}],
                                                   [{"kind": "turn_in", "quest": "Far Hunt", "id": 1}]]
    ctx = _ctx(svc, addons, {})
    svc.run_tool(ctx, "mark_on_map", {"title": "Byancie", "x": 55.3, "y": 56.8, "quest": "[The Great Tree Provides]",
                                      "task": "turn_in"})
    svc.run_tool(ctx, "mark_on_map", {"title": "Dazalar", "x": 56.7, "y": 59.5, "quest": "Taming", "task": "dance", "add": True})
    svc.run_tool(ctx, "mark_on_map", {"title": "The dock", "x": 50, "y": 50, "add": True})
    assert [m.get("tasks") for m in ctx.marks] == [[{"kind": "turn_in", "quest": "The Great Tree Provides"}], None, None]


def test_a_place_that_is_done_can_be_marked_again(env):
    svc, addons, _ = env
    ctx = _ctx(svc, addons, {"marks": ["1. Nessa Shadowsong — Teldrassil 56.3, 92.4 (120 m)",
                                       "2. Vesprystus (Hippogryph Master) — Teldrassil 58.4, 94.0 (done)"]})
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Vesprystus", "x": 58.4, "y": 94.0, "add": True,
                                                  "quest": "Flight to Auberdine", "task": "objective"})
    assert not err and len(ctx.marks) == 1
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Vesprystus (Hippogryph Master)"]})  # and still be removed
    assert not err


UPDATE = "[Quest log changed] took [Crown of the Earth]. Update the route on the map."


def test_route_updates_can_have_their_own_model(env):
    """The player chooses in the app which model answers the add-on's own updates of the route (a cheaper or faster
    one than for questions)."""
    svc, addons, _ = env
    seen = []

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            seen.append(model)
            yield "text", "ok"

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    main = svc.store.settings.model
    assert svc.set_route_model("nope/model") == (False, "Model nope/model does not exist on OpenRouter.")
    assert svc.set_route_model("deepseek/deepseek-v4-flash") == (True, "Route updates: deepseek/deepseek-v4-flash")
    assert svc.store.settings.route_model == {"openrouter": "deepseek/deepseek-v4-flash"} and main != "deepseek/deepseek-v4-flash"

    def ask(n, text, **more):
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": text,
                    "snapshot": {}, **more})
        return _wait_final(addons, n)

    ask(2, "Where is Byancie?")
    d = ask(5, UPDATE, auto=True, news=True)
    assert seen == [main, "deepseek/deepseek-v4-flash"] and d["model"] == "deepseek/deepseek-v4-flash"
    ask(8, "[Player action] Accepted: repair.", auto=True)  # another message of the add-on: not an update of the route
    assert seen[-1] == main
    ask(11, UPDATE, auto=True)  # an add-on that does not say "news" yet
    assert seen[-1] == "deepseek/deepseek-v4-flash"

    assert svc.set_route_model("") == (True, "Route updates: the same model as questions")
    assert svc.store.settings.route_model == {}
    ask(14, UPDATE, auto=True, news=True)
    assert seen[-1] == main


def test_claude_route_updates_have_their_own_session(env, monkeypatch):
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.304", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    turns, made = [], []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append(model)
            self.key, self.model, self.workdir = (model, effort, search_mode, system), model, workdir
            self.conv, self.turns, self.busy = None, 0, False

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            turns.append((self.model, prompt))
            self.turns += 1
            yield "final", "ok"

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    assert svc.set_route_model("gpt-6") == (False, "gpt-6 is not a Claude model.")
    assert svc.set_route_model("claude-sonnet-5-5") == (True, "Route updates: Sonnet 5.5")

    def ask(n, text, **more):
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": text,
                    "snapshot": {}, **more})
        return _wait_final(addons, n)

    assert ask(3, "Plan a route")["model"] == "Haiku 5.5"
    assert ask(8, UPDATE, auto=True, news=True)["model"] == "Sonnet 5.5"
    assert ask(13, "And now?")["model"] == "Haiku 5.5"
    assert ask(18, UPDATE, auto=True, news=True)["model"] == "Sonnet 5.5"
    assert [m for m, _ in turns] == ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-sonnet-5-5"]
    assert made == ["claude-haiku-5-5", "claude-sonnet-5-5"]  # one session each, kept: no restart for every update
    # each side is told what the other did in between: the earlier turns go along
    assert "Plan a route" in turns[1][1] and "[Quest log changed]" in turns[2][1] and "And now?" in turns[3][1]


RUTTHERAN = {"player": {"coords": "58.4, 94.0", "map": "Teldrassil", "level": 11}, "quests": [
    {"id": 1, "title": "The Sisterhood of Elune", "status": "READY TO TURN IN", "turnIn": "Sister Aquinne (Darnassus 28.9, 45.8)"},
    {"id": 2, "title": "Training the Beast", "status": "READY TO TURN IN", "turnIn": "Jocaste (Darnassus 40.4, 8.6)"},
    {"id": 3, "title": "Easing Suffering", "status": "READY TO TURN IN", "turnIn": "Sentinel Eralya Leafshadow (Teldrassil 37.5, 36.8)"},
    {"id": 4, "title": "Nessa's Fish", "status": "READY TO TURN IN", "turnIn": "Nessa Shadowsong (Teldrassil 56.3, 92.4)"}]}


def test_a_route_through_zones_is_marked_with_the_ways_between_them(env):
    """"Nie bierze pod uwagę jak dostać się do danej lokacji": the way into a zone is a stop of the route, in its
    place, and what matters most comes first."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, RUTTHERAN)
    text, err = svc.run_tool(ctx, "plan_route", {})
    assert not err and ('Stops wait in Darnassus (2). The way there is known (Portal to Darnassus): call plan_route '
                        'again with then=[{"zone": "Darnassus"}]') in text
    ctx = _ctx(svc, addons, RUTTHERAN)
    text, err = svc.run_tool(ctx, "plan_route", {"first": ["Training the Beast"], "then": [
        {"zone": "Darnassus"}, {"zone": "Teldrassil", "via_x": "87", "via_y": 44, "via_title": "The city gate"}]})
    assert not err
    assert [(m["title"], m["zone"]) for m in ctx.marks] == [
        ("1. Nessa Shadowsong", "Teldrassil"), ("2. Portal to Darnassus", "Teldrassil"), ("3. Jocaste", "Darnassus"),
        ("4. Sister Aquinne", "Darnassus"), ("5. The gate of Darnassus (approx.)", "Darnassus"),
        ("6. Sentinel Eralya Leafshadow", "Teldrassil")]
    assert ctx.marks[1]["tasks"] == [{"kind": "travel", "zone": "Darnassus"}]
    assert ctx.marks[4]["tasks"] == [{"kind": "travel", "zone": "Teldrassil"}]
    # the next answers know how the route was planned: an update keeps to it
    (note,) = [n for n in ctx.did() if n.startswith("planned the route")]
    assert note == ("planned the route: first [Training the Beast]; then Darnassus by Portal to Darnassus (55, 89), "
                    "arriving at 30, 41; then Teldrassil by The gate of Darnassus (approx.) (88, 44), arriving at 38, 51")
    # a way the app does not know, as the model gives it
    snapshot = dict(RUTTHERAN, quests=RUTTHERAN["quests"] + [
        {"id": 5, "title": "The Keeper", "status": "READY TO TURN IN", "turnIn": "Keeper (Moonglade 51.0, 45.0)"}])
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "plan_route", {"skip": ["The Sisterhood of Elune", "Training the Beast"], "then": [
        {"zone": "Moonglade", "via_x": 58.4, "via_y": 94, "via_title": "Vesprystus", "arrive_x": 44, "arrive_y": 45}]})
    assert [m["title"] for m in ctx.marks] == ["1. Nessa Shadowsong", "2. Vesprystus", "3. Keeper"]
    assert ctx.marks[1]["tasks"] == [{"kind": "travel", "zone": "Moonglade"}] and ctx.marks[2]["zone"] == "Moonglade"


def test_the_order_of_a_route_can_name_a_stop_added_in_the_same_answer(env):
    """The real answer: the portal was added to a route on the map and was to come first; the order did not know
    the new stop, so it stayed last."""
    svc, addons, _ = env
    snapshot = {"marks": ["1. Sister Aquinne — Darnassus 28.9, 45.8", "2. Jocaste — Darnassus 40.4, 8.6"]}
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "mark_on_map", {"title": "Portal to Darnassus", "x": 55.9, "y": 89.6, "zone": "Teldrassil", "add": True})
    text, err = svc.run_tool(ctx, "edit_route", {"order": ["Portal to Darnassus", "Jocaste"]})
    assert not err and text == "The route now: 1. Portal to Darnassus, 2. Jocaste, 3. Sister Aquinne."
    assert ctx.route_edit == {"order": [{"title": "Portal to Darnassus", "x": 55.9, "y": 89.6},
                                         {"title": "Jocaste", "x": 40.4, "y": 8.6},
                                         {"title": "Sister Aquinne", "x": 28.9, "y": 45.8}]}
    assert [m["title"] for m in ctx.marks] == ["Portal to Darnassus"] and ctx.replaces_marks is False


def test_route_updates_can_have_their_own_thinking_level(env, monkeypatch):
    """An update of the route is a small job: the player lets it think less than questions, with the same model or
    with the one chosen for updates."""
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.304", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    turns, made = [], []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append((model, effort))
            self.key, self.model, self.effort, self.workdir = (model, effort, search_mode, system), model, effort, workdir
            self.conv, self.turns, self.busy = None, 0, False

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            turns.append((self.model, self.effort))
            self.turns += 1
            yield "final", "ok"

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    assert svc.set_route_effort("nope") == (False, "Unknown thinking level.")
    assert svc.set_route_effort("low") == (True, "Route updates: thinking low")
    assert svc.store.settings.route_effort == "low" and svc.store.settings.claude_effort == "high"

    def ask(n, text, **more):
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": text,
                    "snapshot": {}, **more})
        return _wait_final(addons, n)

    ask(1, "Plan a route")
    ask(4, UPDATE, auto=True, news=True)  # the same model, thinking less: a session of its own
    ask(7, "And now?")
    ask(10, UPDATE, auto=True, news=True)
    assert turns == [("claude-haiku-5-5", "high"), ("claude-haiku-5-5", "low")] * 2
    assert made == [("claude-haiku-5-5", "high"), ("claude-haiku-5-5", "low")]  # one session each, kept

    assert svc.set_route_model("claude-sonnet-5-5")[0]  # with the model chosen for updates
    ask(13, UPDATE, auto=True, news=True)
    assert turns[-1] == ("claude-sonnet-5-5", "low")

    assert svc.set_route_effort("") == (True, "Route updates: the same thinking level as questions")
    assert svc.store.settings.route_effort == ""
    ask(16, UPDATE, auto=True, news=True)
    assert turns[-1] == ("claude-sonnet-5-5", "high")


def test_a_mark_can_say_what_to_do_there(env):
    svc, addons, _ = env
    ctx = _ctx(svc, addons, {})
    svc.run_tool(ctx, "mark_on_map", {"title": "Jocaste", "x": 40.4, "y": 8.6, "zone": "Darnassus",
                                      "note": "  (learn Beast Training, then talk to her again about the pet's loyalty and food)  "})
    assert ctx.marks[0]["note"] == "learn Beast Training, then talk to her again about the pet's"
    svc.run_tool(ctx, "mark_on_map", {"title": "The dock", "x": 50, "y": 50, "add": True})
    assert "note" not in ctx.marks[1]
    svc.run_tool(ctx, "mark_on_map", {"title": "Fandral", "x": 34.8, "y": 9.3, "add": True,
                                      "note": "talk to Onu (Grove of the Ancients)"})
    assert ctx.marks[2]["note"] == "talk to Onu (Grove of the Ancients)"  # its own brackets stay whole


def _planning_service(svc, addons, seen):
    """A model that plans a route when asked for one (leaving [Letter] out) and answers an update without a tool."""
    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            seen.append(messages[-1]["content"] if messages[-1]["role"] == "user" else None)
            if "plan my route" in str(messages[-1]["content"]) and not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "plan_route", '{"skip": ["Letter"], "end_x": 50, "end_y": 60, '
                                                                  '"end_title": "Dolanaar"}')]
                return
            yield "text", "ok"

    return Service(svc.store, None, addons, client_factory=Client, lookahead=4)


def test_an_update_of_the_route_comes_already_planned(env):
    """"Trwa to bardzo długo": an update used to be the model reading everything, calling plan_route and writing an
    answer. The app plans the route again itself, the way the model planned it; the model checks it and says one line."""
    svc, addons, _ = env
    seen = []
    svc = _planning_service(svc, addons, seen)
    snapshot = dict(ROUTE_SNAPSHOT, bags={"free": 3}, spells=["Aimed Shot"], zoneNPCs=["Vendor — Teldrassil 50.0, 51.0 (20 m)"])
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "c", "text": "plan my route",
                "snapshot": snapshot})
    assert [m["title"] for m in _wait_final(addons, 1)["marks"]] == ["1. Boar", "2. Giver"]
    assert "## bags" in seen[0] and "planned again" not in seen[0]

    # a quest was taken: the update
    later = dict(snapshot, marks=["1. Boar — Teldrassil 80.0, 50.0 (900 m)", "2. Giver — Teldrassil 50.0, 60.0",
                                  "Taken off the route by the player: Giver"])
    later["quests"] = snapshot["quests"] + [
        {"id": 3, "title": "Fresh", "status": "READY TO TURN IN", "turnIn": "Elder (Teldrassil 55.0, 50.0)"}]
    del seen[:]
    svc.handle({"type": "question", "msgId": 6, "session": "S", "nextInbox": 6, "conv": "c", "text": UPDATE,
                "snapshot": later, "auto": True, "news": True})
    d = _wait_final(addons, 6)
    # planned the way it was planned ([Letter] left out), with the quest just taken, without what the player took off
    assert [m["title"] for m in d["marks"]] == ["1. Elder", "2. Boar"]
    assert d["marksReplace"] is True and d["routePlanned"] is True
    (told,) = seen
    assert "# The route, planned again" in told and "1. Elder (55, 50)" in told and "- [Letter]: left out as asked" in told
    assert "It is on the map in place of the old one when you call no tool" in told
    # only what a route needs is read again
    assert "## quests" in told and "## zoneNPCs" in told and "## bags" not in told and "## spells" not in told
    # and the plan is remembered after a restart of the app
    again = _planning_service(svc, addons, seen)
    del seen[:]
    again.handle({"type": "question", "msgId": 11, "session": "S", "nextInbox": 11, "conv": "c", "text": UPDATE,
                  "snapshot": later, "auto": True, "news": True})
    assert [m["title"] for m in _wait_final(addons, 11)["marks"]] == ["1. Elder", "2. Boar"]


def test_an_update_without_a_remembered_plan_is_left_to_the_model(env):
    svc, addons, _ = env
    seen = []
    svc = _planning_service(svc, addons, seen)
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "other", "text": UPDATE,
                "snapshot": ROUTE_SNAPSHOT, "auto": True, "news": True})
    d = _wait_final(addons, 1)
    assert not d.get("marks") and "planned again" not in seen[0] and "## quests" in seen[0]


def test_an_update_takes_the_route_on_from_the_zone_the_player_is_in(env):
    """The zones the route went through are remembered with it; the ones behind the player are not gone back to."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, RUTTHERAN)
    svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Darnassus"}, {"zone": "Teldrassil"}]})
    assert [m["zone"] for m in ctx.marks] == ["Teldrassil", "Teldrassil", "Darnassus", "Darnassus", "Darnassus", "Teldrassil"]
    in_town = dict(RUTTHERAN, player={"coords": "30.0, 41.0", "map": "Darnassus", "level": 11},
                   marks=["1. Jocaste — Darnassus 40.4, 8.6"])
    ctx = _ctx(svc, addons, in_town)
    assert svc.plan_again(ctx) is not None
    assert [m["title"] for m in ctx.marks] == ["1. Sister Aquinne", "2. Jocaste", "3. The gate of Darnassus (approx.)",
                                               "4. Sentinel Eralya Leafshadow"]
    out_of_town = dict(RUTTHERAN, player={"coords": "38.0, 50.0", "map": "Teldrassil", "level": 11},
                       marks=["1. Sentinel Eralya Leafshadow — Teldrassil 37.5, 36.8"])
    out_of_town["quests"] = [q for q in RUTTHERAN["quests"] if q["id"] == 3]
    ctx = _ctx(svc, addons, out_of_town)
    svc.plan_again(ctx)
    assert [m["title"] for m in ctx.marks] == ["1. Sentinel Eralya Leafshadow"]  # not back through the gate


def test_a_session_that_cannot_reach_the_tools_is_replaced(env, monkeypatch):
    """"Narzędzie do planowania trasy nie działa": the session's call of a tool came back as an error and never
    reached the app. The player must not get an answer written without the tools: the question is asked once more,
    on a fresh session."""
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.322", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    made, closed = [], []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append(self)
            self.key, self.workdir = (model, effort, search_mode, system), workdir
            self.conv, self.turns, self.busy, self.broken = None, 0, False, len(made) == 1

        def alive(self):
            return self not in closed

        def close(self):
            closed.append(self)

        def turn(self, prompt, images=None):
            self.turns += 1
            if self.broken:  # the first session: its tools answer with an error the app never sees
                yield "tool_error", "mcp__foreverai__plan_route"
                yield "final", "The route tool does not work, so here is a route by hand."
                return
            text, _ = svc.tool_from_bridge("plan_route", {})
            yield "final", "The route is on the map."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "plan my route",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 3)
    assert d["text"] == "The route is on the map." and [m["title"] for m in d["marks"]] == ["1. Postman", "2. Boar", "3. Giver"]
    assert len(made) == 2 and made[0] in closed and made[1] not in closed
    # a tool that answered with an error of its own is not a broken link: nothing is asked twice
    made[1].turn = lambda prompt, images=None: iter([("tool_error", "mcp__foreverai__mark_on_map"), ("final", "Done.")])
    svc.tool_from_bridge  # (the app saw the call: see below)
    orig = made[1].turn

    def seen_by_the_app(prompt, images=None):
        svc.tool_from_bridge("mark_on_map", {"title": "x", "x": "no", "y": 1})
        yield from orig(prompt, images)

    made[1].turn = seen_by_the_app
    svc.handle({"type": "question", "msgId": 8, "session": "S", "nextInbox": 8, "conv": "c", "text": "mark it",
                "snapshot": ROUTE_SNAPSHOT})
    assert _wait_final(addons, 8)["text"] == "Done." and len(made) == 2



def test_an_update_that_changes_nothing_does_not_ask_the_model(env):
    """"DZIAŁAŁ SZYBKO": the route planned again is the route on the map, so there is nothing for a model to check."""
    svc, addons, _ = env
    seen = []
    svc = _planning_service(svc, addons, seen)
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "c", "text": "plan my route",
                "snapshot": ROUTE_SNAPSHOT})
    _wait_final(addons, 1)
    del seen[:]
    same = dict(ROUTE_SNAPSHOT, marks=["1. Old Stop — Teldrassil 40.0, 40.0 (done)", "2. Boar — Teldrassil 80.0, 50.0 (900 m)",
                                       "3. Giver — Teldrassil 50.0, 60.0",
                                       "These stops leave the route when what is to be done there is done: Boar, Giver"])
    svc.handle({"type": "question", "msgId": 6, "session": "S", "nextInbox": 6, "conv": "c", "text": UPDATE,
                "snapshot": same, "auto": True, "news": True})
    d = _wait_final(addons, 6)
    assert seen == [] and d["routeUnchanged"] is True and not d.get("marks")
    assert d["text"] == "Route checked: nothing to change."
    assert len(svc.convs.history("c")) == 1  # nothing to remember about it


def test_a_long_route_is_marked_to_its_end(env):
    """The real update: sixteen stops, of which the last four (the portal, the flight, Darkshore) never reached the map."""
    svc, addons, _ = env
    quests = [{"id": n, "title": f"Errand {n}", "status": "READY TO TURN IN", "turnIn": f"Clerk {n} (Teldrassil {10 + 4 * n}.0, 50.0)"}
              for n in range(1, 17)]
    ctx = _ctx(svc, addons, {"player": {"coords": "5.0, 50.0", "map": "Teldrassil"}, "quests": quests})
    text, _ = svc.run_tool(ctx, "plan_route", {})
    assert len(ctx.marks) == 16 and ctx.marks[-1]["title"] == "16. Clerk 16" and "The 16 stops are marked" in text


class FreeClient(FakeClient):
    """OpenRouter with free models: a balance of ``balance``, 37 of 50 free requests left today before the requests
    made here."""
    balance = 0.0
    seen: list = []  # (model, web search) of every request
    answer_as = "nvidia/nemotron-3.5-lightning:free"
    fail: set = set()  # models that answer with an error

    def credits(self):
        return FreeClient.balance

    def models(self):
        from foreverai.openrouter import FREE_ROUTER
        return [{"id": "openai/gpt-6-luna", "in": 0.1, "out": 0.5, "free": False, "tools": True},
                {"id": "acme/new-model:free", "in": 0, "out": 0, "free": True, "tools": True},  # not in FREE_MODELS
                {"id": FREE_ROUTER, "in": 0, "out": 0, "free": True, "tools": True},
                {"id": "google/gemma-4-31b-it:free", "in": 0, "out": 0, "free": True, "tools": True},
                {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "in": 0, "out": 0, "free": True, "tools": True},
                {"id": "acme/no-tools:free", "in": 0, "out": 0, "free": True, "tools": False},
                {"id": "acme/music:free", "in": 0, "out": 0, "free": True, "tools": True, "text": False}]

    def key_info(self):
        used = 13 + sum(is_free(m) for m, _ in FreeClient.seen)
        return {"label": "x", "free_model_daily_requests": {"used": used, "limit": 50, "remaining": 50 - used}}

    def chat_stream(self, model, messages, tools, search_mode, max_tokens):
        FreeClient.seen.append((model, search_mode))
        if model in FreeClient.fail:
            yield "error", "Model not available (404): No endpoints found"
            return
        yield "model", FreeClient.answer_as
        if not any(m["role"] == "tool" for m in messages):
            yield "tool_calls", [ToolCall("c1", "search_knowledge_base", '{"query":"Balance of Nature"}')]
            yield "done", "tool_calls"
            return
        yield "text", "Kill 7 wolves."
        yield "usage", {"cost": 0}
        yield "done", "stop"


@pytest.fixture
def free_env(env):
    svc, addons, tmp = env
    svc.client_factory = FreeClient
    FreeClient.balance, FreeClient.seen, FreeClient.fail = 0.0, [], set()
    return svc, addons, tmp


def _answer(svc, addons, msg_id=7):
    _send(svc, {"type": "question", "msgId": msg_id, "session": "S", "nextInbox": 1, "conv": "c", "text": "hi",
                "snapshot": {}})
    deadline = time.time() + 5
    while time.time() < deadline:
        d = _read_inbox(addons, 1)
        if d.get("final") and d.get("msgId") == msg_id:
            return d
        time.sleep(0.01)
    raise AssertionError("no final answer")


def test_the_free_models_of_openrouter_in_the_app(free_env):
    from foreverai.openrouter import FREE_ROUTER
    svc, addons, _ = free_env
    choices = svc.model_choices("openrouter")
    ids = [c["id"] for c in choices]
    # the chosen model and the favorites first, then the free models that chat and call tools: the most used first,
    # newer ones after them, the free router last; free models OpenRouter no longer lists are left out
    free = ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free", "acme/new-model:free", FREE_ROUTER]
    assert ids[0] == config.DEFAULT_MODEL and ids[-4:] == free and len(set(ids)) == len(ids)
    assert ids[:len(config.FAVORITE_MODELS)] == config.FAVORITE_MODELS
    assert not {"acme/no-tools:free", "acme/music:free", "nvidia/nemotron-3.5-lightning:free"} & set(ids)
    by_id = {c["id"]: c for c in choices}
    assert by_id["google/gemma-4-31b-it:free"]["price"] == "Free · 37 of 50 requests left today"
    assert by_id["openai/gpt-6-luna"]["price"] == "$0.10 / $0.50 per 1M tokens"
    # a paid model: the balance, no free requests
    assert "free" not in svc.status()
    assert svc.set_model("google/gemma-4-31b-it:free") == (True, "Model: google/gemma-4-31b-it:free")
    st = svc.status()
    assert st["free"] == {"left": 37, "limit": 50} and st["balance"] == 0.0
    # web search is paid per search: with a free model and no balance the game is told it is off
    assert st["searchMode"] == "never" and svc.store.settings.search_mode == "auto"
    FreeClient.balance = 5.0
    assert svc.status(fresh=True)["searchMode"] == "auto"
    # without OpenRouter's list (no key yet) the free models of the list are offered
    assert svc.free_models({}) == __import__("foreverai.openrouter").openrouter.FREE_MODELS + [FREE_ROUTER]


def test_a_free_model_answers_at_no_cost_without_paid_search(free_env):
    svc, addons, _ = free_env
    svc.store.update(model="google/gemma-4-31b-it:free")
    assert svc.free_requests() == {"left": 37, "limit": 50}
    d = _answer(svc, addons)
    assert d["kind"] == "answer" and "Kill 7 wolves." in d["text"]
    # no balance: the answer goes without OpenRouter's paid web search, in both rounds of the answer
    assert FreeClient.seen == [("google/gemma-4-31b-it:free", "never")] * 2
    # the model OpenRouter says answered (the chosen one was busy and the free router stepped in), no cost
    assert d["model"] == "nvidia/nemotron-3.5-lightning:free"
    assert d["cost"] is None and d["costNote"] == "no cost"
    # the two requests of the answer count toward the free requests of the day, before OpenRouter is asked again
    assert d["status"]["free"] == {"left": 35, "limit": 50}
    assert svc.free_requests(cached=True) == {"left": 35, "limit": 50}
    # with a balance that covers a few searches, the web search chosen in the app is used
    FreeClient.balance, FreeClient.seen = 5.0, []
    svc.balance(fresh=True)
    _answer(svc, addons, msg_id=8)
    assert FreeClient.seen == [("google/gemma-4-31b-it:free", "auto")] * 2


def test_a_free_model_that_cannot_answer_is_followed_by_the_free_router(free_env):
    from foreverai.openrouter import FREE_ROUTER
    svc, addons, _ = free_env
    svc.store.update(model="google/gemma-4-31b-it:free", fallback_model="deepseek/deepseek-v4-flash")
    FreeClient.fail = {"google/gemma-4-31b-it:free"}
    d = _answer(svc, addons)
    # never the paid fallback model: it would cost
    assert [m for m, _ in FreeClient.seen] == ["google/gemma-4-31b-it:free", FREE_ROUTER, FREE_ROUTER]
    assert d["kind"] == "answer" and d["model"] == FreeClient.answer_as and d["cost"] is None
    # the free router itself has no fallback: its error reaches the player
    svc.store.update(model=FREE_ROUTER)
    FreeClient.fail, FreeClient.seen = {FREE_ROUTER}, []
    d = _answer(svc, addons, msg_id=8)
    assert d["kind"] == "error" and "404" in d["text"] and [m for m, _ in FreeClient.seen] == [FREE_ROUTER]
    # a paid model keeps its own fallback
    svc.store.update(model="openai/gpt-6-luna")
    FreeClient.fail, FreeClient.seen = {"openai/gpt-6-luna"}, []
    d = _answer(svc, addons, msg_id=9)
    assert [m for m, _ in FreeClient.seen][:2] == ["openai/gpt-6-luna", "deepseek/deepseek-v4-flash"]
    assert d["model"] == "deepseek/deepseek-v4-flash"  # a paid answer names the model it asked


def test_free_requests_that_cannot_be_read_are_not_asked_for_again_at_once(free_env):
    """OpenRouter does not tell the free requests left (network, a key it does not know): the model list asks once,
    not for every free model, and the next ask waits a minute, in the background."""
    svc, addons, _ = free_env
    asked = []

    def key_info(self):
        asked.append(1)
        raise RuntimeError("401: User not found")
    original, FreeClient.key_info = FreeClient.key_info, key_info
    try:
        choices = svc.model_choices("openrouter")
        assert [c["price"] for c in choices if c["id"].endswith(":free")][0] == "Free · 50 requests a day, 20 a minute"
        assert svc.free_requests() is None and svc.status().get("free") is None
        assert len(asked) == 1
    finally:
        FreeClient.key_info = original


def test_a_free_model_for_route_updates_follows_the_rules_of_free_models(free_env):
    """Questions and the add-on's updates of the route can have different models, one free and one paid: each answer
    follows the rules of its own model."""
    from foreverai.openrouter import FREE_ROUTER
    svc, addons, _ = free_env
    svc.store.update(model="openai/gpt-6-luna", search_mode="auto")
    assert svc.set_route_model("google/gemma-4-31b-it:free")[0]

    def ask(n, text, **more):
        FreeClient.seen = []
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": text,
                    "snapshot": {}, **more})
        return _wait_final(addons, n)

    # the free model of updates is busy: the free router answers, with no paid search (no balance) and at no cost
    FreeClient.fail = {"google/gemma-4-31b-it:free"}
    d = ask(2, UPDATE, auto=True, news=True)
    assert FreeClient.seen == [("google/gemma-4-31b-it:free", "never")] + [(FREE_ROUTER, "never")] * 2
    assert d["cost"] is None and d["costNote"] == "no cost" and d["model"] == FreeClient.answer_as
    # a question: the paid model with the web search chosen in the app
    d = ask(5, "Where is Byancie?")
    assert FreeClient.seen == [("openai/gpt-6-luna", "auto")] * 2
    assert d["model"] == "openai/gpt-6-luna" and "costNote" not in d
    # the other way round: a free model for questions and a paid one for updates
    FreeClient.fail = set()
    svc.store.update(model="google/gemma-4-31b-it:free")
    assert svc.set_route_model("openai/gpt-6-luna")[0]
    ask(8, UPDATE, auto=True, news=True)
    assert FreeClient.seen == [("openai/gpt-6-luna", "auto")] * 2
    ask(11, "Where is Byancie?")
    assert FreeClient.seen == [("google/gemma-4-31b-it:free", "never")] * 2


def test_a_knowledge_base_search_that_finds_nothing_names_the_web_as_the_next_step(env, tmp_path):
    """The real answer: "Which items are best to hand in at the Darkmoon Faire for the bag? Take the AH prices." The
    model searched the knowledge base, read "No results in the local knowledge base." and answered another question
    (what to sell on the auction house) without searching the web. With a web search the result now says to use it;
    without one it stays as it was."""
    from foreverai.kb.store import KB
    svc, addons, _ = env
    kb = KB(tmp_path / "empty.sqlite")
    kb.upsert_page("wiki", "1", "Skyborne", "https://warcraft.wiki.gg/wiki/Skyborne", "1", "s", "t",
                   "The Skyborne are a playable race in World of Warcraft: Forever with wings of their own.")

    class Client(FakeClient):
        results: list = []
        query = "Darkmoon Faire"

        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            done = [m["content"] for m in messages if m["role"] == "tool"]
            if not done:
                yield "tool_calls", [ToolCall("c1", "search_knowledge_base", json.dumps({"query": Client.query}))]
                return
            Client.results.append((search_mode, done[-1]))
            yield "text", "Done."

    svc = Service(svc.store, kb, addons, client_factory=Client, lookahead=4)

    def ask(n):
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": "q",
                    "snapshot": {}})
        _wait_final(addons, n)
        return Client.results[-1]

    mode, text = ask(3)
    assert mode == "auto" and text.startswith("No results in the local knowledge base. When the answer depends on it")
    assert "search the web for it before you answer" in text and "never answer another question" in text
    Client.query = "Skyborne wings"  # found: the pages, and nothing about the web
    mode, text = ask(7)
    assert "[Skyborne |" in text and "search the web" not in text
    Client.query = "Darkmoon Faire"
    assert svc.set_search_mode("never")[0]
    assert ask(11) == ("never", "No results in the local knowledge base.")
    assert svc.set_search_mode("always")[0]
    assert ask(15)[1].startswith("No results in the local knowledge base. When the answer depends on it")


def test_a_free_model_without_a_paid_search_gets_no_web_step(env, tmp_path):
    from foreverai.kb.store import KB
    svc, addons, _ = env

    class Client(FreeClient):
        results: list = []

        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            done = [m["content"] for m in messages if m["role"] == "tool"]
            if not done:
                yield "tool_calls", [ToolCall("c1", "search_knowledge_base", '{"query": "Darkmoon Faire"}')]
                return
            Client.results.append((search_mode, done[-1]))
            yield "text", "Done."

    FreeClient.balance, FreeClient.seen, FreeClient.fail = 0.0, [], set()
    svc = Service(svc.store, KB(tmp_path / "empty.sqlite"), addons, client_factory=Client, lookahead=4)
    assert svc.set_model("google/gemma-4-31b-it:free")[0]
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "q", "snapshot": {}})
    _wait_final(addons, 3)
    assert Client.results == [("never", "No results in the local knowledge base.")]


def test_claude_code_gets_the_web_step_while_its_websearch_is_on(env, tmp_path, monkeypatch):
    from foreverai import claude_code
    from foreverai.kb.store import KB
    svc, addons, _ = env
    svc.kb = KB(tmp_path / "empty.sqlite")
    monkeypatch.setattr(claude_code, "detect", lambda force=False: {"path": "claude", "version": "2.1.304",
                                                                    "loggedIn": True, "subscription": "max"})
    seen = {"results": []}

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            seen["bridge"] = bridge_url
            self.key = (model, effort, search_mode, system)
            self.workdir, self.conv, self.turns, self.busy = workdir, None, 0, False

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            from foreverai.mcp_server import http_bridge
            call = http_bridge(seen["bridge"].replace("/mcp", "/tool"), svc._bridge.token)
            seen["results"].append(call("search_knowledge_base", {"query": "Darkmoon Faire"}))
            yield "final", "Done."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    assert svc.set_provider("claude_code")[0]
    for n, mode in ((3, "auto"), (7, "never")):
        assert svc.set_search_mode(mode)[0]
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": "q",
                    "snapshot": {}})
        _wait_final(addons, n)
    (auto, auto_err), (never, never_err) = seen["results"]
    assert not auto_err and "search the web for it before you answer" in auto
    assert not never_err and never == "No results in the local knowledge base."
