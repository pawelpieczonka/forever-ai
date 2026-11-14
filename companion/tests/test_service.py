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
                     "mark_on_map", "edit_route", "plan_route", "addon_options", "zone_quests", "gear_upgrades",
                     "map_notes", "pet_abilities", "way_to", "propose_auctions", "my_auctions",
                     "propose_auction_cancel", "propose_quest_abandon", "propose_vendor_action", "search_auction_house",
                     "auctionator_list", "compare_sell_prices"]
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
    svc.game_call = lambda ctx, op, args, timeout=45.0: None  # no game to ask for auction prices
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
    # an order with a quest's turn-in before the stop where its objective is done is not set (found by a journey with
    # the real model: a letter "turned in" at stop 1 and picked up at stop 5)
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Postman"], "order": ["giver"]})
    assert err and text.startswith("Nothing changed: in this order Giver comes before Boar, and [Far Hunt] is turned in "
                                   "at Giver only after Boar.")
    assert [m["title"] for m in ctx.marks] == ["1. Postman", "2. Boar", "3. Giver"]
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Postman"], "order": ["boar"]})
    assert not err and ctx.route_edit is None  # the route on the map is replaced by this one, not edited
    assert text == ("Taken off the route: Postman. The route now: 1. Boar, 2. Giver. These marks replace the ones on "
                    "the map; do not mark them again.")
    assert ctx.marks == [{"title": "1. Boar", "x": 80.0, "y": 50.0, "zone": "Teldrassil", "mob": "Boar",
                           "tasks": [{"kind": "objective", "quest": "Far Hunt", "id": 1, "label": "Boar"}]},
                         {"title": "2. Giver", "x": 50.0, "y": 60.0, "zone": "Teldrassil",
                           "tasks": [{"kind": "turn_in", "quest": "Far Hunt", "id": 1}]}]
    svc.run_tool(ctx, "mark_on_map", {"title": "Dazalar", "x": 56.7, "y": 59.5, "add": True})  # where it ends
    assert [m["title"] for m in ctx.marks] == ["1. Boar", "2. Giver", "Dazalar"]
    assert ctx.route_marked is True and ctx.replaces_marks is True  # an extra stop keeps it a planned route
    assert ctx.track == [{"name": "Boar", "quest": "Far Hunt"}]
    assert "took off the route: Postman" in ctx.did() and "put the route in another order" in ctx.did()

    svc.run_tool(ctx, "edit_route", {"remove": ["1"]})  # the stop with the creature: its watch goes with it
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
    # the marks on the map are no planned route (another conversation: no plan is remembered): a new set of marks
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    ctx.conv = "no plan here"
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Byancie", "x": 55.3, "y": 56.8})
    assert not err and len(ctx.marks) == 1 and "are off the map" in text

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
    # with no zone named the route goes on from the village to the rest of its map by itself, through the city
    # (what is to do there is done on the way): one call is the route
    text, err = svc.run_tool(ctx, "plan_route", {})
    assert not err and "call plan_route again" not in text
    assert [m["title"] for m in ctx.marks][:2] == ["1. Nessa Shadowsong", "2. Portal to Darnassus"]
    assert {"Sister Aquinne", "Jocaste", "Sentinel Eralya Leafshadow"} <= {m["title"].split(". ", 1)[1] for m in ctx.marks}
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
    assert not err and text == "The route now: 1. Portal to Darnassus (added in this answer), 2. Jocaste, 3. Sister Aquinne."
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
            # (asked by the player: an add-on's message quotes the player's own last words too)
            if ("Player question: plan my route" in str(messages[-1]["content"])
                    and not any(m["role"] == "tool" for m in messages)):
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

def test_the_ai_can_send_the_player_to_a_stop(env):
    """edit_route go_to: the arrow leads to that stop now; also one added or planned in this answer."""
    svc, addons, _ = env
    snapshot = {"marks": ["1. Sister Aquinne — Darnassus 28.9, 45.8", "2. Jocaste — Darnassus 40.4, 8.6"]}
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "mark_on_map", {"title": "Trainer", "x": 40.0, "y": 10.0, "zone": "Darnassus", "add": True})
    text, err = svc.run_tool(ctx, "edit_route", {"go_to": "Trainer"})
    assert not err and text == "The arrow leads to Trainer now."
    assert ctx.go_to == {"title": "Trainer", "x": 40.0, "y": 10.0} and ctx.route_edit is None
    assert "the arrow leads to Trainer now" in ctx.did()
    text, err = svc.run_tool(ctx, "edit_route", {"go_to": "Nobody"})
    assert err and text.startswith("Nothing changed: no stop called Nobody.")
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(ctx, "plan_route", {})  # 1. Postman, 2. Boar, 3. Giver
    text, err = svc.run_tool(ctx, "edit_route", {"go_to": "Boar"})
    assert not err and ctx.go_to == {"title": "Boar", "x": 80.0, "y": 50.0} and "The arrow leads to Boar now." in text

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "edit_route", '{"go_to": "2"}')]
                return
            yield "text", "Jocaste first."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "trainer first",
                "snapshot": snapshot})
    assert _wait_final(addons, 3)["goTo"] == {"title": "Jocaste", "x": 40.4, "y": 8.6}

def test_a_session_given_no_planner_is_replaced(env, monkeypatch):
    """A session whose turn started without the app's tools (they were not connected yet) answers without them: the
    question is asked again on a new session."""
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.322", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    made = []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append(self)
            self.key, self.workdir = (model, effort, search_mode, system), workdir
            self.conv, self.turns, self.busy = None, 0, False
            self.mcp_missing = ["mcp__foreverai__plan_route"] if len(made) == 1 else []

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            self.turns += 1
            if self.mcp_missing:
                yield "final", "The route tool does not work, so here is a route by hand."
                return
            svc.tool_from_bridge("plan_route", {})
            yield "final", "The route is on the map."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "plan my route",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 3)
    assert d["text"] == "The route is on the map." and len(made) == 2 and len(d["marks"]) == 3

def test_log_files_are_written_only_when_asked_and_can_be_cleared(env, monkeypatch):
    """"W apce powinien być checkbox, czy zapisywać logi, czy nie, domyślnie odznaczony, oraz clear logs, który czyści
    plik z logami"."""
    import logging
    from foreverai import config, logs
    svc, addons, _ = env
    data = config.data_dir()
    monkeypatch.setattr(logs, "_on", False)
    monkeypatch.setattr(logs, "_handler", None)
    lines = {"type": "debug", "char": "Simtester", "entries": [{"t": 1794526620, "k": "market", "m": "checking 1 items"}]}
    question = {"text": "wystaw mieso", "snapshot": {"player": {"level": 18}}}
    assert config.Settings().save_logs is False  # off unless the player ticks the box
    try:
        svc._debug_log(lines)
        svc._save_snapshot(question)
        logging.getLogger("foreverai").info("nothing of this goes to a file")
        assert not any((data / name).exists() for name in logs.NAMES)
        # ticked: the three kinds of file are written, and the choice is kept for the next start
        assert svc.set_save_logs(True) == (True, "Log files are written from now on.")
        assert svc.store.settings.save_logs is True and config.SettingsStore(svc.store.path).settings.save_logs is True
        svc._debug_log(lines)
        svc._save_snapshot(question)
        logging.getLogger("foreverai").warning("this one goes to the file")
        assert "market: checking 1 items" in (data / "debug.log").read_text(encoding="utf-8")
        assert "wystaw mieso" in (data / "last_question.json").read_text(encoding="utf-8")
        assert "this one goes to the file" in (data / "foreverai.log").read_text(encoding="utf-8")
        # cleared: the files are gone (the open one too), and writing goes on as it was set
        (data / "debug.log.1").write_text("older half", encoding="utf-8")
        assert svc.clear_logs() == (True, "4 log files removed.")
        assert not any((data / name).exists() for name in logs.NAMES if name != "foreverai.log")
        logging.getLogger("foreverai").warning("after the clearing")
        text = (data / "foreverai.log").read_text(encoding="utf-8")
        assert "after the clearing" in text and "this one goes to the file" not in text
        # unticked again: nothing more is written
        assert svc.set_save_logs(False)[1] == "Log files are no longer written."
        svc._debug_log(lines)
        assert not (data / "debug.log").exists()
        assert svc.clear_logs() == (True, "1 log file removed.") and svc.clear_logs() == (True, "There were no log files.")
    finally:
        logs.set_enabled(False)

def test_the_debug_log_of_the_add_on_is_written_to_a_file(env, monkeypatch):
    from foreverai import logs
    monkeypatch.setattr(logs, "_on", True)  # the player has the log files switched on
    svc, addons, tmp = env
    svc.handle({"type": "debug", "char": "Simtester-Forever", "entries": [
        {"t": 1794400000, "k": "track", "m": "Darkshore Thresher: 0 in sight | plates 0 | nameplates off"},
        {"t": 1794400003, "k": "route", "m": "stop done: 2. City Gate (tasks done)"}]})
    text = (config.data_dir() / "debug.log").read_text(encoding="utf-8")
    assert "Simtester-Forever track: Darkshore Thresher: 0 in sight | plates 0 | nameplates off" in text
    assert text.count("\n") == 2 and _read_inbox(addons, 1) == {}  # nothing goes back
    assert svc.status()["debugLog"] is config.dev_mode()

def test_an_update_is_answered_at_once_by_the_planner_and_checked_by_the_model_after(env):
    svc, addons, _ = env
    seen = []
    svc = _planning_service(svc, addons, seen)
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "c", "text": "plan my route",
                "snapshot": ROUTE_SNAPSHOT})
    _wait_final(addons, 1)
    later = dict(ROUTE_SNAPSHOT, marks=["1. Boar — Teldrassil 80.0, 50.0 (900 m)", "2. Giver — Teldrassil 50.0, 60.0"])
    later["quests"] = ROUTE_SNAPSHOT["quests"] + [
        {"id": 3, "title": "Fresh", "status": "READY TO TURN IN", "turnIn": "Elder (Teldrassil 55.0, 50.0)"}]
    del seen[:]
    svc.handle({"type": "question", "msgId": 6, "session": "S", "nextInbox": 6, "conv": "c", "text": UPDATE,
                "snapshot": later, "auto": True, "news": True, "fast": True})
    d = _wait_final(addons, 6)
    assert seen == []  # no model: the planner's route goes at once
    assert sorted(m["title"][3:] for m in d["marks"]) == ["Boar", "Elder", "Giver"]
    assert d["check"] is True and d["routePlanned"] is True and d["text"] == "Route updated: + Elder."
    # the check a moment later: the model looks; calling no tool means the route stays as the planner made it
    checked = dict(later, marks=["1. Elder — Teldrassil 55.0, 50.0 (250 m)", "2. Boar — Teldrassil 80.0, 50.0"])
    svc.handle({"type": "question", "msgId": 11, "session": "S", "nextInbox": 11, "conv": "c",
                "text": "[Route check] took [Fresh]. Check the route on the map.", "snapshot": checked, "auto": True,
                "news": True, "check": True})
    d = _wait_final(addons, 11)
    assert len(seen) == 1 and "# The route, planned again" in seen[0]
    assert d["routeUnchanged"] is True and not d.get("marks")

DARKSHORE_ROUTE = {
    "player": {"coords": "52.2, 36.9", "map": "Darkshore", "zone": "Darkshore", "level": 13},
    "quests": [{"id": 1003, "title": "Buzzbox 525", "status": "in progress", "turnIn": "Buzzbox 525 (Darkshore 41.4, 80.6)",
                "objectives": "0/4 Grizzled Scalp [TO DO]", "whereToGet": [
                    "Grizzled Scalp: dropped by Den Mother (level 18-19) — Darkshore 51.5, 38.3 (82 m, 1 spawn point in "
                    "your zone); dropped by Grizzled Thistle Bear (level 16-17) — Darkshore 39.1, 70.0 (1548 m, 19 spawn "
                    "points in your zone)"]}],
    "marks": ["1. Elisa Steelhand (repair + vendor) — Darkshore 38.1, 41.1 (done)",
              "2. Den Mother — Darkshore 51.5, 38.3 (82 m)",
              "3. Buzzbox 525 — Darkshore 41.4, 80.6 (1871 m)",
              "4. Hippogryph or boat to Rut'theran Village (approx.) — Darkshore 36.3, 45.6 (1027 m)",
              "5. Nessa Shadowsong — Teldrassil 56.3, 92.4",
              "6. Portal to Darnassus — Teldrassil 55.0, 89.0",
              "7. The gate of Darnassus (approx.) — Darnassus 88.0, 44.0",
              "8. Denalan — Teldrassil 60.9, 68.5",
              "These stops leave the route when what is to be done there is done, not when the player reaches them: "
              "Den Mother, Buzzbox 525, Denalan"],
    "tracked": ["Den Mother (also counts for Grizzled Scalp: Grizzled Thistle Bear): none in sight now"]}

def test_edits_of_one_answer_add_up_and_an_added_stop_is_listed_where_it_goes(env):
    """The game's answer: the AI added the bears, took Den Mother off, then put the bears first, then took off Elisa too;
    each call undid the one before, and the tools listed the bears last, so it told the player the edit had not worked
    (in the game the bears were stop 2, after the Buzzbox they are for)."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, DARKSHORE_ROUTE)
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Grizzled Thistle Bears", "x": 39.1, "y": 70, "add": True,
                                                  "mob": "Grizzled Thistle Bear", "quest": "Buzzbox 525", "task": "objective"})
    assert not err and "On the route it is stop 3 of 9, where it costs the least way (after Den Mother)" in text
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Den Mother"], "go_to": "Grizzled Thistle Bears",
                                                 "stop_tracking": ["Den Mother"]})
    assert not err and text.startswith("Taken off the route: Den Mother. The route now: 1. Elisa Steelhand (repair + "
                                       "vendor) (done), 2. Grizzled Thistle Bears (added in this answer), 3. Buzzbox 525, ")
    text, err = svc.run_tool(ctx, "edit_route", {"order": ["Grizzled Thistle Bears", "Buzzbox 525"]})
    assert not err and text.startswith("The route now: 1. Grizzled Thistle Bears (added in this answer), 2. Buzzbox 525, ")
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Den Mother", "Elisa Steelhand (repair + vendor)"]})
    assert not err
    edit = ctx.route_edit
    assert [s["title"] for s in edit["remove"]] == ["Elisa Steelhand (repair + vendor)", "Den Mother"]
    assert [s["title"] for s in edit["order"]] == [
        "Grizzled Thistle Bears", "Buzzbox 525", "Hippogryph or boat to Rut'theran Village (approx.)",
        "Nessa Shadowsong", "Portal to Darnassus", "The gate of Darnassus (approx.)", "Denalan"]
    assert edit["order"][0] == {"title": "Grizzled Thistle Bears", "x": 39.1, "y": 70.0}  # as the game is sent stops
    assert ctx.go_to["title"] == "Grizzled Thistle Bears" and ctx.untrack == ["Den Mother"]
    assert ctx.did()[:2] == ["took off the route: Den Mother", "put the route in another order"]

def test_without_a_quest_an_added_stop_goes_where_the_way_is_shortest(env):
    """The quests hold only a stop that is for them: a camp on the way back north goes after the Buzzbox."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, DARKSHORE_ROUTE)
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Camp", "x": 38.5, "y": 66.0, "add": True})
    assert not err and "On the route it is stop 4 of 9, where it costs the least way (after Buzzbox 525)" in text

def test_an_update_plans_the_route_with_the_chosen_source(env):
    svc, addons, _ = env
    import threading
    from foreverai.service import InboxWriter, TurnContext
    snapshot = dict(DARKSHORE_ROUTE)
    del snapshot["marks"]
    ctx = TurnContext({"msgId": 5, "conv": "scalps"}, InboxWriter(addons, "S", 1, 20, 4), snapshot, threading.Event(), [])
    text, err = svc.run_tool(ctx, "plan_route", {"use": ["Grizzled Thistle Bear"]})
    assert not err and [m["title"] for m in ctx.marks] == ["1. Grizzled Thistle Bear", "2. Buzzbox 525"]
    assert svc._plans()["scalps"]["use"] == ["Grizzled Thistle Bear"]
    assert "planned the route: objectives from Grizzled Thistle Bear" in ctx.did()
    # the add-on's update a moment later: planned again the same way, the bears and not Den Mother
    later = dict(snapshot, marks=["1. Grizzled Thistle Bear — Darkshore 39.1, 70.0 (1548 m)",
                                  "2. Buzzbox 525 — Darkshore 41.4, 80.6"])
    ctx = TurnContext({"msgId": 9, "conv": "scalps"}, InboxWriter(addons, "S", 9, 20, 4), later, threading.Event(), [])
    assert svc.plan_again(ctx) and [m["title"] for m in ctx.marks] == ["1. Grizzled Thistle Bear", "2. Buzzbox 525"]

def test_a_hand_change_of_a_planned_route_holds_when_it_is_updated(env):
    """A real answer: the AI took Den Mother off with edit_route and marked the bears with mark_on_map instead of
    planning again with use. The next update planned the route the old way: Den Mother would have come back."""
    svc, addons, _ = env
    import threading
    from foreverai.service import InboxWriter, TurnContext
    planned = dict(DARKSHORE_ROUTE)
    del planned["marks"]
    ctx = TurnContext({"msgId": 5, "conv": "scalps"}, InboxWriter(addons, "S", 1, 20, 4), planned, threading.Event(), [])
    svc.run_tool(ctx, "plan_route", {"skip": ["Twisted Hatred"], "use": ["Den Mother"]})  # (its own choice then)
    assert [m["title"] for m in ctx.marks] == ["1. Den Mother", "2. Buzzbox 525"]
    on_map = dict(planned, marks=["1. Den Mother — Darkshore 51.5, 38.3 (82 m)", "2. Buzzbox 525 — Darkshore 41.4, 80.6"])
    ctx = TurnContext({"msgId": 7, "conv": "scalps"}, InboxWriter(addons, "S", 7, 20, 4), on_map, threading.Event(), [])
    svc.run_tool(ctx, "edit_route", {"remove": ["Den Mother"]})
    svc.run_tool(ctx, "mark_on_map", {"title": "Grizzled Thistle Bears", "x": 39.1, "y": 70, "add": True,
                                      "mob": "Grizzled Thistle Bear", "quest": "Buzzbox 525", "task": "objective"})
    plan = svc._plans()["scalps"]
    assert plan["use"] == ["Grizzled Thistle Bear"] and plan["removed"] == ["Den Mother"] and plan["skip"] == ["Twisted Hatred"]
    later = dict(planned, marks=["1. Grizzled Thistle Bears — Darkshore 39.1, 70.0 (1548 m)",
                                 "2. Buzzbox 525 — Darkshore 41.4, 80.6"])
    ctx = TurnContext({"msgId": 9, "conv": "scalps"}, InboxWriter(addons, "S", 9, 20, 4), later, threading.Event(), [])
    assert svc.plan_again(ctx)
    assert [m["title"] for m in ctx.marks] == ["1. Grizzled Thistle Bear", "2. Buzzbox 525"]
    # a new plan of the model's own starts from its own choices
    svc.run_tool(ctx, "plan_route", {})
    assert "use" not in svc._plans()["scalps"] and "removed" not in svc._plans()["scalps"]

def test_an_answer_that_blames_the_tools_without_calling_them_is_asked_again(env, monkeypatch):
    """A real answer, with the tools there: "Nie mogę teraz ustawić trasy na mapie, bo narzędzia do planowania i
    znaczników nie działają." The player gets a route instead."""
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.322", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    made = []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append(self)
            self.key, self.workdir = (model, effort, search_mode, system), workdir
            self.conv, self.turns, self.busy, self.mcp_missing = None, 0, False, []

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            self.turns += 1
            if len(made) == 1:
                yield "final", ("Nie mogę teraz ustawić trasy na mapie, bo narzędzia do planowania i znaczników nie "
                                "działają. Poniżej jest kolejność, którą bym wybrał.")
                return
            svc.tool_from_bridge("plan_route", {})
            yield "final", "Trasa jest na mapie."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "ułóż trasę",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 3)
    assert d["text"] == "Trasa jest na mapie." and len(made) == 2 and len(d["marks"]) == 3

STORMWIND = {"player": {"coords": "63.5, 73.0", "map": "Stormwind City", "zone": "Stormwind City", "level": 18},
             "quests": [{"id": 65, "title": "The Defias Brotherhood", "status": "READY TO TURN IN",
                         "turnIn": "Wiley the Black (Stormwind City 80.0, 60.0)"},
                        {"id": 353, "title": "Stormpike's Delivery", "status": "READY TO TURN IN",
                         "turnIn": "Mountaineer Stormpike (Stormwind City 40.0, 30.0)"}]}

def _turn(svc, addons, conv, snapshot, n):
    import threading
    from foreverai.service import InboxWriter, TurnContext
    return TurnContext({"msgId": n, "conv": conv}, InboxWriter(addons, "S", n, 20, 4), snapshot, threading.Event(), [])

def test_stops_the_player_asked_for_stay_on_the_route_until_they_are_done(env):
    """"Czemu wywaliłeś łapanie peta i flymasterów?": a route planned again (a quest turned in) held only the quests."""
    svc, addons, _ = env
    extra = [{"title": "Gunther Weller (vendor)", "zone": "Stormwind City", "x": 64.2, "y": 68.6, "note": "sell and repair",
              "when": "first"},
             {"title": "Einris Brightspear (hunter trainer)", "zone": "Stormwind City", "x": 67.4, "y": 36.4}]
    ctx = _turn(svc, addons, "sw", STORMWIND, 5)
    text, err = svc.run_tool(ctx, "plan_route", {"extra": extra})
    assert not err and [m["title"] for m in ctx.marks] == ["1. Gunther Weller (vendor)", "2. Wiley the Black",
                                                           "3. Einris Brightspear (hunter trainer)",
                                                           "4. Mountaineer Stormpike"]
    assert ctx.marks[0]["note"] == "sell and repair"  # what to do there, for the player's list
    assert "planned the route: with the stops asked for: Gunther Weller (vendor), Einris Brightspear (hunter trainer)" in ctx.did()
    plan = svc._plans()["sw"]
    assert [e["title"] for e in plan["extra"]] == ["Gunther Weller (vendor)", "Einris Brightspear (hunter trainer)"]
    assert all(e["placed"] for e in plan["extra"])
    # the add-on's update after a turn-in; the vendor is done (the player was there), the trainer is not
    later = dict(STORMWIND, quests=STORMWIND["quests"][1:], marks=[
        "1. Gunther Weller (vendor) — Stormwind City 64.2, 68.6 (done)", "2. Wiley the Black — Stormwind City 80.0, 60.0 (done)",
        "3. Einris Brightspear (hunter trainer) — Stormwind City 67.4, 36.4 (300 m)",
        "4. Mountaineer Stormpike — Stormwind City 40.0, 30.0"])
    ctx = _turn(svc, addons, "sw", later, 9)
    assert svc.plan_again(ctx)
    assert sorted(m["title"][3:] for m in ctx.marks) == ["Einris Brightspear (hunter trainer)", "Mountaineer Stormpike"]
    assert [e["title"] for e in svc._plans()["sw"]["extra"]] == ["Einris Brightspear (hunter trainer)"]

def test_a_stop_added_by_hand_is_one_the_player_asked_for(env):
    """A flight master added with mark_on_map to a planned route is on it after the next update too."""
    svc, addons, _ = env
    ctx = _turn(svc, addons, "sw", STORMWIND, 5)
    svc.run_tool(ctx, "plan_route", {})
    on_map = dict(STORMWIND, marks=["1. Wiley the Black — Stormwind City 80.0, 60.0 (500 m)",
                                    "2. Mountaineer Stormpike — Stormwind City 40.0, 30.0"])
    ctx = _turn(svc, addons, "sw", on_map, 7)
    svc.run_tool(ctx, "mark_on_map", {"title": "Dungar Longdrink (flight master)", "x": 71.3, "y": 72.2, "add": True,
                                      "note": "open the flight path"})
    assert svc._plans()["sw"]["extra"] == [{"title": "Dungar Longdrink (flight master)", "zone": "Stormwind City",
                                            "x": 71.3, "y": 72.2, "placed": True, "note": "open the flight path"}]
    later = dict(on_map, marks=on_map["marks"] + ["3. Dungar Longdrink (flight master) — Stormwind City 71.3, 72.2"])
    ctx = _turn(svc, addons, "sw", later, 9)
    assert svc.plan_again(ctx) and "Dungar Longdrink (flight master)" in [m["title"][3:] for m in ctx.marks]
    # taken off again by the model: it is no stop of the route any more
    ctx = _turn(svc, addons, "sw", later, 11)
    svc.run_tool(ctx, "edit_route", {"remove": ["Dungar Longdrink (flight master)"]})
    assert "extra" not in svc._plans()["sw"]

def test_the_notes_of_a_conversation_are_kept_and_sent_with_every_question(env):
    """"Taki memory per czat": what was agreed and what the player corrected goes with every question, also when the
    turns themselves are long gone."""
    svc, addons, _ = env
    seen = []

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            seen.append(messages)
            if not tools:  # the notes: no tools, the notes prompt
                yield "text", "Corrections:\n- Thelsamar is not a known flight path (\"nie mam\")."
                return
            yield "text", "Idź pieszo."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4, keep_notes=True)
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "n", "text": "nie mam tego lotu",
                "snapshot": {"player": {"level": 18}}})
    _wait_final(addons, 1)
    for _ in range(100):
        if svc.convs.notes("n"):
            break
        time.sleep(0.05)
    assert svc.convs.notes("n") == 'Corrections:\n- Thelsamar is not a known flight path ("nie mam").'
    asked = seen[1]
    assert asked[0]["content"].startswith("You keep the notes of one conversation")
    assert "Notes so far:\n(none yet)" in asked[1]["content"] and "Player: nie mam tego lotu\nAssistant: Idź pieszo." in asked[1]["content"]
    assert svc.convs.unnoted("n") == []
    # the next question carries them, and they survive a restart of the app (they are in the conversation's file)
    svc.handle({"type": "question", "msgId": 6, "session": "S", "nextInbox": 6, "conv": "n", "text": "to jak isc?",
                "snapshot": {"player": {"level": 18}}})
    _wait_final(addons, 6)
    question = [m for m in seen if m[-1]["content"].startswith("Player question: to jak isc?")][0][-1]["content"]
    assert "# Notes of this conversation" in question and "Thelsamar is not a known flight path" in question
    again = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    assert "Thelsamar" in again.convs.notes("n")

def test_the_plan_of_the_route_goes_with_the_question(env):
    """"Nagle zmienia decyzję, że miał brać tę misję, a później jej nie bierze": the choices of the route on the map
    are in front of the model every time."""
    svc, addons, _ = env
    seen = []

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            seen.append(messages[-1]["content"])
            yield "text", "ok"

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    ctx = _turn(svc, addons, "p", STORMWIND, 3)
    svc.run_tool(ctx, "plan_route", {"skip": ["Stormpike's Delivery"], "extra": [
        {"title": "Einris Brightspear (hunter trainer)", "zone": "Stormwind City", "x": 67.4, "y": 36.4, "note": "train"}]})
    on_map = dict(STORMWIND, marks=["1. Wiley the Black — Stormwind City 80.0, 60.0 (500 m)"])
    svc.handle({"type": "question", "msgId": 9, "session": "S", "nextInbox": 9, "conv": "p", "text": "co dalej?",
                "snapshot": on_map})
    _wait_final(addons, 9)
    assert "# How the route on the map was planned" in seen[0]
    assert "- left out (skip): [Stormpike's Delivery]" in seen[0]
    assert "- stops the player asked for (extra): Einris Brightspear (hunter trainer) (Stormwind City 67.4, 36.4: train)" in seen[0]
    # no route on the map: nothing to keep to
    svc.handle({"type": "question", "msgId": 15, "session": "S", "nextInbox": 15, "conv": "p", "text": "a teraz?",
                "snapshot": STORMWIND})
    _wait_final(addons, 15)
    assert "How the route on the map was planned" not in seen[-1]

def test_a_stop_asked_for_that_did_not_fit_on_the_map_is_not_taken_as_done(env):
    """A route of more than twenty stops marks the first twenty. The trainer was stop 22: the next update found no
    such mark, took him as visited and dropped him from the plan."""
    svc, addons, _ = env
    from foreverai.service import MAX_ROUTE_MARKS, _extra_stops
    quests = [{"id": 100 + i, "title": f"Letter {i}", "status": "READY TO TURN IN",
               "turnIn": f"Clerk {i} (Stormwind City {20 + 3 * i:.1f}, 50.0)"} for i in range(MAX_ROUTE_MARKS + 1)]
    snapshot = dict(STORMWIND, quests=quests)
    extra = [{"title": "Einris Brightspear (hunter trainer)", "zone": "Stormwind City", "x": 67.4, "y": 36.4, "when": "last"}]
    ctx = _turn(svc, addons, "long", snapshot, 5)
    svc.run_tool(ctx, "plan_route", {"extra": extra})
    assert len(ctx.marks) == MAX_ROUTE_MARKS and "Einris" not in " ".join(m["title"] for m in ctx.marks)
    assert "placed" not in svc._plans()["long"]["extra"][0]
    later = dict(snapshot, quests=quests[1:], marks=[f"{n}. {m['title'][m['title'].index(' ') + 1:]} — Stormwind City "
                                                     f"{m['x']:.1f}, {m['y']:.1f}" for n, m in enumerate(ctx.marks[1:], 1)])
    ctx = _turn(svc, addons, "long", later, 9)
    assert svc.plan_again(ctx)
    assert [e["title"] for e in svc._plans()["long"]["extra"]] == ["Einris Brightspear (hunter trainer)"]
    # a title is cut so that the mark "20. title" still carries all of it: the stop is found again on the map by it
    cut = _extra_stops([{"title": "A flight master with a very long name and a long note in its title, too", "zone": "Westfall",
                         "x": 50, "y": 50}])[0]["title"]
    assert len(f"20. {cut}") <= 60

def test_the_notes_are_written_by_the_cheap_model_of_the_provider(env):
    """A player who pays per question does not pay their best model twice for every message."""
    svc, addons, _ = env
    models = []

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            models.append((model, bool(tools)))
            yield "text", "Agreed:\n- nothing yet."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4, keep_notes=True)
    svc.store.settings.model, svc.store.settings.fallback_model = "anthropic/claude-opus-5.5", "deepseek/deepseek-v4-flash"
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "cheap", "text": "hej",
                "snapshot": {"player": {"level": 18}}})
    _wait_final(addons, 1)
    for _ in range(100):
        if svc.convs.notes("cheap"):
            break
        time.sleep(0.05)
    assert ("deepseek/deepseek-v4-flash", False) in models and ("anthropic/claude-opus-5.5", False) not in models

def test_a_conversation_from_before_the_notes_is_taken_in_whole_and_a_deleted_one_stays_deleted(tmp_path):
    import json as _json
    from foreverai.memory import ConversationStore
    (tmp_path / "old.json").write_text(_json.dumps({"id": "old", "turns": [{"q": "a", "a": "b"}, {"q": "c", "a": "d"}]}),
                                       encoding="utf-8")
    store = ConversationStore(tmp_path)
    assert [t["n"] for t in store.unnoted("old")] == [1, 2]  # the turns of the player's long chat are not left out
    store.append("old", "X", "e", "f")
    assert [t["n"] for t in store.unnoted("old")] == [1, 2, 3]
    store.set_notes("old", "Agreed: x", 3)
    assert store.unnoted("old") == [] and store.notes("old") == "Agreed: x"
    store.delete("old")
    store.set_notes("old", "late notes", 3)  # the notes call came back after the player deleted the chat
    assert not (tmp_path / "old.json").exists()

def test_the_model_reads_the_quests_of_another_zone_and_routes_through_them(env, tmp_path):
    """The player in Stormwind wants the quests of Westfall: the tool asks the game, the answer names them with their
    givers, and plan_route picks the ones named up at their givers, in a zone the player is not in."""
    import threading
    from foreverai import ways
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    icons = tmp_path / "icons.lua"
    icons.write_text("""
		--[[Westfall]] [1436] = {
			{"Dungeon", 42.5, 71.7, L["The Deadmines"], L["Dungeon"], dnTex, 17, 26},
			{"Arrow", 62.0, 17.9, L["Stormwind City"], nil, arTex, nil, nil, nil, nil, nil, 5.4, 1453},
		},
		--[[Stormwind City]] [1453] = {
			{"Arrow", 74.5, 92.3, L["Westfall"], nil, arTex, nil, nil, nil, nil, nil, 3.8, 1436},
		},
""", encoding="utf-8")
    ways.use_icons(icons)
    try:
        writer = InboxWriter(addons, "S", 1, 20, 4)
        writer.send({"kind": "answer", "final": False, "msgId": 9, "text": "", "phase": "x"})
        snapshot = {"player": {"coords": "63.5, 73.0", "map": "Stormwind City", "zone": "Stormwind City", "level": 18},
                    "quests": []}
        ctx = TurnContext({"msgId": 9, "conv": "west"}, writer, snapshot, threading.Event(), [])
        out = {}
        t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "zone_quests", {"zone": "Westfall"})))
        t.start()
        req, deadline = None, time.time() + 5
        while time.time() < deadline and not req:
            req = (_read_inbox(addons, 1).get("requests") or [None])[0]
            time.sleep(0.02)
        assert req["op"] == "zone_quests" and req["zone"] == "Westfall"
        _send(svc, {"type": "game_result", "id": req["id"], "result": {"zone": "Westfall", "level": 18, "later": 2, "quests": [
            {"id": 12, "title": "The People's Militia", "level": 12, "minLevel": 9, "giver": "Gryan Stoutmantle",
             "where": "Westfall 56.3, 47.5", "objective": "Kill 15 Defias Trappers.", "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)",
             "whereToGet": ["Defias Trapper: kill Defias Trapper (level 12-13) — Westfall 48.0, 30.0"]},
            {"id": 167, "title": "Oh Brother. . .", "level": 20, "type": "Dungeon", "about": "The Deadmines",
             "giver": "Wilder Thistlenettle", "where": "Stormwind City 70.3, 40.8"}]}})
        t.join(5)
        text, err = out["r"]
        assert not err and text.startswith("Quests the character (level 18) can pick up for Westfall: 2.")
        assert "- [The People's Militia] (level 12) from Gryan Stoutmantle (Westfall 56.3, 47.5): Kill 15 Defias Trappers." in text
        assert "    Defias Trapper: kill Defias Trapper (level 12-13) — Westfall 48.0, 30.0" in text
        assert "- [Oh Brother. . .] (level 20, Dungeon) [belongs to The Deadmines] from Wilder Thistlenettle" in text
        assert "Entrances in this zone: The Deadmines (dungeon, levels 17-26): entrance at Westfall 42.5, 71.7." in text
        assert "2 more open at a higher level" in text
        # the route picks the quest up in Westfall, through the way there
        text, err = svc.run_tool(ctx, "plan_route", {"new_quests": ["The People's Militia"], "then": [{"zone": "Westfall"}]})
        # ... and all of it is on the route: where it is taken, what it wants, where it is turned in
        whole = ["1. To Westfall", "2. Gryan Stoutmantle", "3. Defias Trapper", "4. Gryan Stoutmantle"]
        assert not err and [m["title"] for m in ctx.marks] == whole
        assert ctx.marks[1]["tasks"] == [{"kind": "pick_up", "quest": "The People's Militia", "id": 12}]
        assert ctx.marks[2]["tasks"] == [{"kind": "objective", "quest": "The People's Militia", "id": 12, "label": "Defias Trapper"}]
        assert ctx.marks[3]["tasks"] == [{"kind": "turn_in", "quest": "The People's Militia", "id": 12}]
        # and an update of that route still knows the quest, though the game data around the player does not have it
        assert svc._plans()["west"]["offered"][0]["title"] == "The People's Militia"
        later = dict(snapshot, marks=["1. To Westfall — Stormwind City 74.5, 92.3 (300 m)", "2. Gryan Stoutmantle — Westfall 56.3, 47.5"])
        ctx = TurnContext({"msgId": 15, "conv": "west"}, InboxWriter(addons, "S", 15, 20, 4), later, threading.Event(), [])
        assert svc.plan_again(ctx) and [m["title"] for m in ctx.marks] == whole
    finally:
        ways.use_icons(None)

def test_the_dungeons_for_the_characters_level_go_with_a_question(env, tmp_path):
    from foreverai import ways
    svc, addons, _ = env
    icons = tmp_path / "icons.lua"
    icons.write_text('\t\t--[[Westfall]] [1436] = {\n\t\t\t{"Dungeon", 42.5, 71.7, L["The Deadmines"], L["Dungeon"], dnTex, 17, 26},\n\t\t},\n',
                     encoding="utf-8")
    seen = []

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            seen.append(messages[-1]["content"])
            yield "text", "ok"

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    ways.use_icons(icons)
    try:
        svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "d", "text": "kiedy deadmines?",
                    "snapshot": {"player": {"level": 18}}})
        _wait_final(addons, 1)
        assert "## dungeons\n- The Deadmines (dungeon, levels 17-26): entrance at Westfall 42.5, 71.7" in seen[0]
    finally:
        ways.use_icons(None)

def test_auction_tools_ask_the_game_and_put_a_card_up(env):
    """The whole way of "wystaw mięso na AH": the bags, the game's price check, the app's rules, the card."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    bags = {"items": [{"item": "[Bear Meat]", "id": 3173, "count": 13, "sellCopper": 15, "quality": "common"},
                      {"item": "[Glowing Ring]", "id": 9999, "count": 1, "sellCopper": 300, "quality": "uncommon"},
                      {"item": "[Ruined Pelt]", "id": 4865, "count": 5, "sellCopper": 5, "quality": "poor"}]}
    asked = []

    def game(ctx, op, args, timeout=45.0):
        asked.append((op, args))
        if op == "auction_check":
            return {"durations": [2, 8, 24], "items": [
                {"id": 3173, "name": "Bear Meat", "maxStack": 10, "vendor": 15, "have": 13, "auctions": 9, "sellers": 5,
                 "commodity": True,
                 "levels": [{"unit": 19, "auctions": 3, "units": 30}, {"unit": 20, "auctions": 6, "units": 44}]}]}
        if op == "auction_owned":
            return {"auctions": [{"n": 1, "auctionID": 77, "id": 3173, "name": "Bear Meat", "count": 10, "unit": 18,
                                  "buyout": 180, "left": "8 h"}]}
        if op == "auction_search":
            return {"lastScan": "2 h ago (full scan)", "dbItems": 4000,
                    "items": [{"id": 3173, "name": "Bear Meat", "copper": 40, "available": 74}]}
    svc.game_call = game
    closed = TurnContext({"msgId": 1}, InboxWriter(addons, "S", 1, 20, 4), {"bags": bags}, threading.Event(), [])
    text, err = svc.run_tool(closed, "propose_auctions", {"items": [{"item": "Bear Meat"}]})
    assert err and "auction house window is not open" in text and asked == []
    snap = {"bags": bags, "windows": {"auctionHouse": {"open": True}, "merchant": {"npc": "Lyrai"}}}
    ctx = TurnContext({"msgId": 2}, InboxWriter(addons, "S", 1, 20, 4), snap, threading.Event(), [])
    text, err = svc.run_tool(ctx, "propose_auctions", {"items": [{"item": "Bear Meat"}], "reason": "mięso"})
    assert not err and asked[0] == ("auction_check", {"items": [{"id": 3173, "name": "Bear Meat"}]})
    post = ctx.actions[-1]
    # this auction house sells by the piece: all thirteen in one auction, at a price for one
    assert post["op"] == "auction_post" and [(lot["count"], lot["unit"]) for lot in post["lots"]] == [(13, 18.0)]
    assert "sure price: 9 offers from 5 sellers" in text
    assert "[Bear Meat] ×10 — 18c each (1s 80c in all)" in svc.run_tool(ctx, "my_auctions", {})[0]
    text, err = svc.run_tool(ctx, "propose_auction_cancel", {"auctions": [1], "reason": "drożej"})
    assert not err and ctx.actions[-1]["op"] == "auction_cancel" and ctx.actions[-1]["auctions"][0]["auctionID"] == 77
    # what the answer did, for the turns that follow
    assert "proposed posting on the auction house (waits for the player's clicks): Bear Meat ×13 at 18c each" in ctx.did()
    assert "proposed cancelling auctions: Bear Meat ×10" in ctx.did()
    # a vendor sale asks the price data once for the whole list, and keeps back what is not junk
    del asked[:]
    text, err = svc.run_tool(ctx, "propose_vendor_action", {"op": "sell", "items": ["Bear Meat", "Glowing Ring", "Ruined Pelt"], "reason": "śmieci"})
    assert [op for op, _ in asked] == ["auction_search"] and asked[0][1]["items"] == [{"id": 3173}, {"id": 9999}, {"id": 4865}]
    assert [i["name"] for i in ctx.actions[-1]["items"]] == ["[Ruined Pelt]"]
    assert "[Bear Meat]: KEPT, worth more on the auction house" in text and "[Glowing Ring]: KEPT, uncommon quality" in text

def test_planning_again_keeps_the_quest_looked_up_in_another_zone(env):
    """From a trial on the real model: "ustaw routing jeszcze raz" named the same quests, but the one read with
    zone_quests in the first answer was no longer known to the planner; the model set its giver by hand."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    snap = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "56.0, 47.0", "level": 18, "faction": "Alliance"},
            "quests": [], "nearbyQuests": []}
    grayson = {"zone": "Westfall", "level": 18, "quests": [
        {"id": 152, "title": "The Coast Isn't Clear", "level": 19, "minLevel": 14, "giver": "Captain Grayson",
         "where": "Westfall 30.0, 86.0", "objective": "Kill murlocs."}]}
    svc.game_call = lambda ctx, op, args, timeout=45.0: grayson if op == "zone_quests" else None

    def turn(n):
        ctx = TurnContext({"msgId": n, "conv": "c1"}, InboxWriter(addons, "S", n, 40, 4), snap, threading.Event(), [])
        ctx.conv = "c1"
        return ctx
    first = turn(1)
    svc.run_tool(first, "zone_quests", {"zone": "Westfall"})
    text, err = svc.run_tool(first, "plan_route", {"new_quests": ["The Coast Isn't Clear"]})
    assert not err and [m["title"] for m in first.marks] == ["1. Captain Grayson"]
    # the next answer plans again with the same words and no look-up of the zone
    again = turn(2)
    text, err = svc.run_tool(again, "plan_route", {"new_quests": ["The Coast Isn't Clear"]})
    assert not err and [m["title"] for m in again.marks] == ["1. Captain Grayson"]
    assert again.marks[0]["tasks"] == [{"kind": "pick_up", "quest": "The Coast Isn't Clear", "id": 152}]

def test_a_route_is_planned_with_the_quests_of_its_zones_read_from_the_game(env, monkeypatch):
    """The quests around the player are all the game sends with a question. A route into another zone is made with
    that zone's quests read too, so that the ones done along it are on it from the start: read once per zone, kept
    for a few minutes, and not waited for again when the game does not answer."""
    import threading
    from foreverai.service import InboxWriter, Service, TurnContext
    svc, addons, _ = env
    monkeypatch.setattr(Service, "fetch_route_quests", True)
    asked = []
    westfall = {"zone": "Westfall", "level": 18, "quests": [
        {"id": 12, "title": "The People's Militia", "level": 17, "minLevel": 9, "giver": "Gryan Stoutmantle",
         "where": "Westfall 56.3, 47.5", "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)",
         "whereToGet": ["Defias Trapper: kill Defias Trapper (level 12-13) — Westfall 55.0, 46.0"]},
        {"id": 13, "title": "Far Afield", "level": 17, "giver": "Captain Danuvin", "where": "Westfall 56.4, 47.6",
         "turnIn": "Captain Danuvin (Westfall 56.4, 47.6)",
         "whereToGet": ["Harvest Watcher: kill Harvest Watcher (level 14) — Westfall 30.0, 80.0"]}]}

    def game(ctx, op, args, timeout=45.0):
        asked.append((op, args.get("zone")))
        return westfall if args.get("zone") == "Westfall" else {"zone": args.get("zone"), "quests": []}
    svc.game_call = game
    snap = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "56.0, 47.0", "level": 18, "faction": "Alliance"},
            "quests": [{"id": 1, "title": "Report", "level": 15, "status": "READY TO TURN IN",
                        "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)"}], "nearbyQuests": []}

    def turn(n):
        ctx = TurnContext({"msgId": n, "conv": "c1"}, InboxWriter(addons, "S", n, 40, 4), snap, threading.Event(), [])
        ctx.conv = "c1"
        return ctx
    first = turn(1)
    text, err = svc.run_tool(first, "plan_route", {})
    assert not err and asked == [("zone_quests", "Westfall")]
    # the quest done beside the turn-in is on the route, all of it; the one whose creatures are across the zone is named
    assert [m["title"] for m in first.marks] == ["1. Gryan Stoutmantle", "2. Defias Trapper", "3. Gryan Stoutmantle"]
    assert "[Far Afield] (level 17) from Captain Danuvin" in text and svc._plans()["c1"]["along"] == [12]
    # planned again a moment later: the game is not asked a second time
    svc.run_tool(turn(2), "plan_route", {})
    assert asked == [("zone_quests", "Westfall")]
    # the game does not answer: the route is made without, and the next one does not wait for it again
    svc._zone_quests.clear()
    svc.game_call = lambda ctx, op, args, timeout=45.0: asked.append((op, "no answer")) or None
    svc.run_tool(turn(3), "plan_route", {})
    svc.run_tool(turn(4), "plan_route", {})
    assert asked.count(("zone_quests", "no answer")) == 1

def test_a_quest_turned_in_is_not_offered_again_by_an_update(env, monkeypatch):
    """From a trial: a quest taken along was picked up and turned in; the update after the turn-in put its giver and
    its turn-in back on the route. The plan held it as a quest to pick up, and nothing said it had been in the log."""
    import threading
    from foreverai.service import InboxWriter, Service, TurnContext
    svc, addons, _ = env
    monkeypatch.setattr(Service, "fetch_route_quests", True)
    lunch = {"id": 129, "title": "A Free Lunch", "level": 15, "minLevel": 11, "giver": "Darcy", "where": "Westfall 56.0, 47.2",
             "turnIn": "Guard Parker (Westfall 54.0, 49.0)"}
    svc.game_call = lambda ctx, op, args, timeout=45.0: {"zone": args.get("zone"), "level": 18, "quests": [lunch]}
    report = {"id": 1, "title": "Report", "level": 15, "status": "READY TO TURN IN", "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)"}

    def turn(n, quests, marks=None):
        snap = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "56.0, 47.0", "level": 18, "faction": "Alliance"},
                "quests": quests, "nearbyQuests": [], **({"marks": marks} if marks else {})}
        ctx = TurnContext({"msgId": n, "conv": "c2"}, InboxWriter(addons, "S", n, 40, 4), snap, threading.Event(), [])
        ctx.conv = "c2"
        return ctx
    first = turn(1, [report])
    svc.run_tool(first, "plan_route", {})
    made = [m["title"] for m in first.marks]
    assert "1. Darcy" in made and any(t.endswith("Guard Parker") for t in made) and svc._plans()["c2"]["along"] == [129]
    lines = [f"{m['title']} — Westfall {m['x']:.1f}, {m['y']:.1f}" for m in first.marks]
    # taken: it is in the log, its giver is no stop any more, its turn-in stays
    taken = turn(2, [report, {"id": 129, "title": "A Free Lunch", "level": 15, "status": "READY TO TURN IN",
                              "turnIn": "Guard Parker (Westfall 54.0, 49.0)"}], lines)
    svc.plan_again(taken)
    assert not any("Darcy" in m["title"] for m in taken.marks) and any("Guard Parker" in m["title"] for m in taken.marks)
    assert 129 in svc._plans()["c2"]["taken"]
    # turned in: it left the log, and nothing of it comes back
    after = turn(3, [report], lines)
    svc.plan_again(after)
    assert [m["title"] for m in after.marks] == ["1. Gryan Stoutmantle"]

def test_a_quest_taken_and_turned_in_between_two_messages_does_not_come_back(env, monkeypatch):
    """From the game's log: [Hilary's Necklace] was planned whole, taken and turned in with no message to the app
    between (taking a quest planned whole asks for no update); the update after the turn-in put its three stops back,
    and the add-on ticked them off a second later. The game's own word that a quest of the route is turned in
    (routeDone), or any message that showed it in the log, keeps it off."""
    import threading
    from foreverai.service import InboxWriter, Service, TurnContext
    svc, addons, _ = env
    monkeypatch.setattr(Service, "fetch_route_quests", True)
    necklace = {"id": 3741, "title": "Hilary's Necklace", "level": 15, "minLevel": 12, "giver": "Shawn",
                "where": "Westfall 55.6, 46.6", "turnIn": "Hilary (Westfall 55.0, 48.2)",
                "whereToGet": ["Hilary's Necklace: from object Glinting Mud — Westfall 54.6, 47.0 (4 spawn points in that zone)"]}
    svc.game_call = lambda ctx, op, args, timeout=45.0: {"zone": args.get("zone"), "level": 18, "quests": [necklace]}
    report = {"id": 1, "title": "Report", "level": 15, "status": "READY TO TURN IN", "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)"}

    def turn(n, conv, quests, marks=None, **more):
        snap = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "56.0, 47.0", "level": 18, "faction": "Alliance"},
                "quests": quests, "nearbyQuests": [], **({"marks": marks} if marks else {}), **more}
        ctx = TurnContext({"msgId": n, "conv": conv}, InboxWriter(addons, "S", n, 40, 4), snap, threading.Event(), [])
        ctx.conv = conv
        return ctx, snap
    for conv, how in (("told", "routeDone"), ("seen", "message"), ("unknown", None)):
        first, _ = turn(1, conv, [report])
        svc.run_tool(first, "plan_route", {"new_quests": ["Hilary's Necklace"]})
        made = [m["title"].split(". ", 1)[1] for m in first.marks]
        assert {"Shawn", "Glinting Mud", "Hilary", "Gryan Stoutmantle"} <= set(made)
        lines = [f"{m['title']} — Westfall {m['x']:.1f}, {m['y']:.1f}" + ("" if "Gryan" in m["title"] else " (done)")
                 for m in first.marks]
        if how == "message":  # the player asked something while the quest was in the log
            _, snap = turn(2, conv, [report, {"id": 3741, "title": "Hilary's Necklace", "status": "in progress"}])
            svc._saw_log(conv, snap)
        after, _ = turn(3, conv, [report], lines, **({"routeDone": [3741]} if how == "routeDone" else {}))
        svc.plan_again(after)
        names = [m["title"].split(". ", 1)[1] for m in after.marks]
        if how:
            assert names == ["Gryan Stoutmantle"], (how, names)
        else:  # nothing told the app: the add-on is the one that keeps such stops off the map (tested there)
            assert "Shawn" in names

def test_an_update_answered_by_the_model_adds_no_quest_either(env, monkeypatch):
    """Found by the real model on the game's own Redridge: an update that went to the model (a turn-in sent the route
    back to another zone) called plan_route, and the planner chose quests to take along as for a new route: givers
    the route was never planned with came onto it."""
    import threading
    from foreverai.service import InboxWriter, Service, TurnContext
    svc, addons, _ = env
    monkeypatch.setattr(Service, "fetch_route_quests", True)
    lunch = {"id": 129, "title": "A Free Lunch", "level": 15, "minLevel": 11, "giver": "Darcy", "where": "Westfall 56.0, 47.2",
             "turnIn": "Guard Parker (Westfall 54.0, 49.0)"}
    gnolls = {"id": 244, "title": "Encroaching Gnolls", "level": 15, "minLevel": 11, "giver": "Deputy Rainer",
              "where": "Westfall 56.2, 47.4", "turnIn": "Deputy Rainer (Westfall 56.2, 47.4)"}
    zone = [lunch]
    svc.game_call = lambda ctx, op, args, timeout=45.0: {"zone": args.get("zone"), "level": 18, "quests": list(zone)}
    report = {"id": 1, "title": "Report", "level": 15, "status": "READY TO TURN IN", "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)"}

    def turn(n, text, nearby=()):
        snap = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "56.0, 47.0", "level": 18, "faction": "Alliance"},
                "quests": [report], "nearbyQuests": list(nearby)}
        ctx = TurnContext({"msgId": n, "conv": "c3", "text": text}, InboxWriter(addons, "S", n, 40, 4), snap,
                          threading.Event(), [])
        ctx.conv = "c3"
        return ctx
    first = turn(1, "plan me a route")
    svc.run_tool(first, "plan_route", {})
    assert svc._plans()["c3"]["along"] == [129] and any("Darcy" in m["title"] for m in first.marks)
    # the deputy's quest came into reach since, by the route; the model plans the route again in an update
    zone.append(gnolls)
    offer = dict(gnolls, where="Westfall 56.2, 47.4", distance="20 m")
    update = turn(2, "[Quest log changed] [Report] left the log. Update the route on the map.", [offer])
    svc.run_tool(update, "plan_route", {"new_quests": ["Encroaching Gnolls"]})
    def picks(ctx):
        return [t.get("id") for m in ctx.marks for t in m.get("tasks") or [] if t["kind"] == "pick_up"]
    assert picks(update) == [129], [(m["title"], m.get("tasks")) for m in update.marks]
    # asked by the player in a message of their own, it goes on the route
    asked = turn(3, "add Encroaching Gnolls", [offer])
    svc.run_tool(asked, "plan_route", {"new_quests": ["Encroaching Gnolls"]})
    assert 244 in picks(asked)

def test_a_session_still_busy_with_its_previous_answer_is_replaced(env, monkeypatch):
    """Found on a long journey with the real model: an update of the route came before the session had closed its
    previous answer, and it was answered with "Claude Code did not finish the previous answer." The route was not
    planned again, and the player was left with quests in the log and no stop on the map."""
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.322", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    made, closed = [], []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append(self)
            self.key, self.workdir = (model, effort, search_mode, system), workdir
            self.conv, self.turns, self.busy = None, 0, False

        def alive(self):
            return self not in closed

        def close(self):
            closed.append(self)

        def turn(self, prompt, images=None):
            self.turns += 1
            if len(made) == 1:
                yield "unsettled", "Claude Code did not finish the previous answer."
                return
            svc.tool_from_bridge("plan_route", {})
            yield "final", "The route is on the map."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "plan my route",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 3)
    assert d["text"] == "The route is on the map." and d["marks"]
    assert len(made) == 2 and made[0] in closed and made[1] not in closed
    # it happens again later: again a new session answers
    made[1].turn = lambda prompt, images=None: iter([("unsettled", "Claude Code did not finish the previous answer.")])
    svc.handle({"type": "question", "msgId": 8, "session": "S", "nextInbox": 8, "conv": "c", "text": "and now?",
                "snapshot": ROUTE_SNAPSHOT})
    assert _wait_final(addons, 8)["text"] == "The route is on the map." and len(made) == 3 and made[1] in closed
    # the new session fails the same way: the player is told, nothing is asked a third time
    FakeSession.turn = lambda self, prompt, images=None: iter([("unsettled", "Claude Code did not finish the previous answer.")])
    svc.handle({"type": "question", "msgId": 9, "session": "S", "nextInbox": 9, "conv": "c", "text": "and now?",
                "snapshot": ROUTE_SNAPSHOT})
    assert "did not finish" in _wait_final(addons, 9)["text"] and len(made) == 4

def test_a_mark_for_a_quest_has_its_number(env):
    """Found by the real model on the game's own Goldshire: its mark for the turn-in of [A Fishy Peril] named the quest
    by title alone, beside the planner's stop that had it by number: the stop said the turn-in twice."""
    svc, addons, _ = env
    snapshot = {"player": {"coords": "42.0, 66.0", "map": "Elwynn Forest", "level": 12},
                "quests": [{"id": 40, "title": "A Fishy Peril", "status": "READY TO TURN IN",
                            "turnIn": "Marshal Dughan (Elwynn Forest 42.1, 65.9)"}],
                "nearbyQuests": [{"id": 35, "title": "Further Concerns", "giver": "Marshal Dughan", "where": "Elwynn Forest 42.1, 65.9"}]}
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "mark_on_map", {"title": "Marshal Dughan", "x": 42.1, "y": 65.9, "quest": "A Fishy Peril", "task": "turn_in"})
    assert ctx.marks[-1]["tasks"] == [{"kind": "turn_in", "quest": "A Fishy Peril", "id": 40}]
    ctx = _ctx(svc, addons, snapshot)
    svc.run_tool(ctx, "mark_on_map", {"title": "Marshal Dughan", "x": 42.1, "y": 65.9, "quest": "Further Concerns",
                                      "task": "pick_up"})
    assert ctx.marks[-1]["tasks"] == [{"kind": "pick_up", "quest": "Further Concerns", "id": 35}]

def test_a_way_into_a_zone_the_player_took_off_comes_back_when_needed(env):
    """Found by walking the game's own Redridge: the player took the way into Redridge off the route; later the route
    went back there from Westfall, and the update kept the way off: the arrow led to Darcy with no way there."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, RUTTHERAN)
    svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Darnassus"}, {"zone": "Teldrassil"}]})
    ways = [m["title"] for m in ctx.marks if any(t.get("kind") == "travel" for t in m.get("tasks") or [])]
    assert ways, [m["title"] for m in ctx.marks]
    lines = [f"{m['title']} — {m.get('zone') or 'Teldrassil'} {m['x']:.1f}, {m['y']:.1f}" for m in ctx.marks]
    off = "Taken off the route by the player: " + re.sub(r"^\d+[.)]\s*", "", ways[0])
    again = _ctx(svc, addons, dict(RUTTHERAN, marks=lines + [off]))
    svc.plan_again(again)
    assert ways[0] in [m["title"] for m in again.marks], [m["title"] for m in again.marks]

def test_an_update_keeps_a_stop_the_quest_log_needs(env):
    """Found by the real model on the game's own Lakeshire: two parts of one chain are called [Messenger to Westfall];
    after the first was turned in, the model took the second for it and took its turn-in stop off the route."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    snapshot = {"player": {"coords": "56.3, 47.5", "map": "Westfall", "level": 18},
                "quests": [{"id": 144, "title": "Messenger to Westfall", "status": "READY TO TURN IN",
                            "turnIn": "Magistrate Solomon (Redridge Mountains 24.9, 44.5)"}],
                "marks": ["1. To Elwynn Forest — Westfall 62.0, 17.9", "2. Magistrate Solomon — Redridge Mountains 24.9, 44.5",
                          "3. Hilary — Redridge Mountains 24.1, 53.6"]}

    def turn(text):
        return TurnContext({"msgId": 3, "text": text}, InboxWriter(addons, "S", 3, 40, 4), snapshot, threading.Event(), [])
    update = turn("[Route check] [Messenger to Westfall] turned in, its next part taken. Check the route on the map.")
    text, err = svc.run_tool(update, "edit_route", {"remove": ["Magistrate Solomon"]})
    assert err and "Magistrate Solomon is where [Messenger to Westfall] of the quest log is turned in" in text
    text, err = svc.run_tool(update, "edit_route", {"remove": ["Hilary"]})
    assert not err, text  # nothing of the log there
    # the player asks for it: it goes
    asked = turn("take Solomon off the route")
    text, err = svc.run_tool(asked, "edit_route", {"remove": ["Magistrate Solomon"]})
    assert not err, text

def test_a_session_that_ended_in_the_middle_of_an_answer(env, monkeypatch):
    """Found with several live journeys at once: the Claude Code session ended in the middle of an answer, after
    plan_route had made the route; the player got "Claude Code ended without an answer." and no route."""
    from foreverai import claude_code
    svc, addons, _ = env
    info = {"path": "claude.exe", "version": "2.1.322", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    made, closed = [], []

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            made.append(self)
            self.key, self.workdir = (model, effort, search_mode, system), workdir
            self.conv, self.turns, self.busy = None, 0, False

        def alive(self):
            return self not in closed

        def close(self):
            closed.append(self)

        def turn(self, prompt, images=None):
            self.turns += 1
            if len(made) == 1:  # the first session makes the route, then ends
                svc.tool_from_bridge("plan_route", {})
            closed.append(self)  # (the process is gone)
            yield "ended", "Claude Code ended without an answer."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    svc.prewarm_later = lambda: None
    assert svc.set_provider("claude_code")[0]
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "plan my route",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 3)
    assert d.get("kind") != "error" and d["marks"] and d["text"].startswith("The route is on the map: Postman"), d
    # nothing done yet: asked once more on a new session, then the player is told
    n = len(made)
    svc.handle({"type": "question", "msgId": 8, "session": "S", "nextInbox": 8, "conv": "c", "text": "and now?",
                "snapshot": ROUTE_SNAPSHOT})
    d = _wait_final(addons, 8)
    assert "ended without an answer" in d["text"] and len(made) == n + 2, (d, len(made), n)  # (its own session, then one more)

def test_plan_route_in_an_update_keeps_what_the_route_has(env):
    """Found by the real model on the game's own Lakeshire: the player went their own way into another zone; checking
    the update, the model planned for that zone alone, and the turn-in of a quest of the log, left behind, was off the
    route: the player had asked for nothing."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    ctx = _ctx(svc, addons, RUTTHERAN)
    ctx.conv = "c9"
    svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Darnassus"}, {"zone": "Teldrassil"}]})
    zones = {m.get("zone") for m in ctx.marks}
    assert "Darnassus" in zones and "Teldrassil" in zones, zones
    lines = [f"{m['title']} — {m.get('zone') or 'Teldrassil'} {m['x']:.1f}, {m['y']:.1f}" for m in ctx.marks]
    in_town = dict(RUTTHERAN, player={"coords": "30.0, 41.0", "map": "Darnassus", "level": 11}, marks=lines)
    update = TurnContext({"msgId": 4, "text": "[Route check] the player went to Darnassus, away from the route."},
                         InboxWriter(addons, "S", 4, 40, 4), in_town, threading.Event(), [])
    update.conv = "c9"
    titles = [q["title"] for q in RUTTHERAN["quests"] if isinstance(q, dict)]
    svc.run_tool(update, "plan_route", {"then": [{"zone": "Darnassus"}], "skip": titles})
    assert "Teldrassil" in {m.get("zone") for m in update.marks}, [(m["title"], m.get("zone")) for m in update.marks]

def test_an_update_with_nothing_left_to_route_clears_the_map(env, monkeypatch):
    """Found by the seeded replays of the game's own Redridge: the player dropped the quest the last stops were for;
    the update planned nothing, the stops stayed on the map, and the arrow led there for nothing."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    report = {"id": 1, "title": "Report", "level": 15, "status": "READY TO TURN IN",
              "turnIn": "Gryan Stoutmantle (Westfall 56.3, 47.5)"}

    def turn(n, quests, marks=None):
        snap = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "50.0, 40.0", "level": 18, "faction": "Alliance"},
                "quests": quests, "nearbyQuests": [], **({"marks": marks} if marks else {})}
        ctx = TurnContext({"msgId": n, "conv": "c5"}, InboxWriter(addons, "S", n, 40, 4), snap, threading.Event(), [])
        ctx.conv = "c5"
        return ctx
    first = turn(1, [report])
    svc.run_tool(first, "plan_route", {})
    lines = [f"{m['title']} — Westfall {m['x']:.1f}, {m['y']:.1f}" for m in first.marks]
    dropped = turn(2, [], lines)
    text = svc.plan_again(dropped)
    assert text and "Nothing of the route is left to do" in text and dropped.route_edit == {"clear": True}
    # nothing on the map either: nothing to say
    assert svc.plan_again(turn(3, [], [])) is None

def test_the_news_of_an_update_takes_a_stop_at_the_same_place_for_it(env):
    """Found by the seeded replays of the game's own Goldshire: Ma Stonefield and "Auntie" Bernice Stonefield stand side
    by side; the update named the stop there after Ma, and the news said one stop was added and one taken off."""
    import threading
    from foreverai.service import InboxWriter, Service, TurnContext
    svc, addons, _ = env
    snap = {"marks": ['1. "Auntie" Bernice Stonefield — Elwynn Forest 34.5, 84.2', "2. Remy — Elwynn Forest 42.1, 67.3"]}
    ctx = TurnContext({"msgId": 1}, InboxWriter(addons, "S", 1, 40, 4), snap, threading.Event(), [])
    ctx.marks = [{"title": "1. Ma Stonefield", "x": 34.7, "y": 84.5}, {"title": "2. Remy", "x": 42.1, "y": 67.3},
                 {"title": "3. Deputy Rainer", "x": 34.0, "y": 72.0}]
    assert Service._route_news(ctx) == "Route updated: + Deputy Rainer."

def test_a_mark_in_an_update_is_added_to_the_route(env):
    """Found by the real model on the game's own Lakeshire and Westfall: checking an update, the model marked its own
    stops in place of the route, and stops quests of the log still needed (the turn-in of a chain's next part, which
    has the title of the part before) were off the map."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    ctx = _ctx(svc, addons, RUTTHERAN)
    ctx.conv = "c8"
    svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Darnassus"}, {"zone": "Teldrassil"}]})
    planned = [m["title"] for m in ctx.marks]
    update = TurnContext({"msgId": 4, "text": "[Route check] took [X]. Check the route on the map."},
                         InboxWriter(addons, "S", 4, 40, 4), RUTTHERAN, threading.Event(), [])
    update.conv = "c8"
    svc.run_tool(update, "plan_route", {"then": [{"zone": "Darnassus"}, {"zone": "Teldrassil"}]})
    svc.run_tool(update, "mark_on_map", {"title": "Rut'theran Village dock", "x": 55.0, "y": 90.0})
    titles = [m["title"] for m in update.marks]
    assert len(titles) == len(planned) + 1 and titles[-1].endswith("Rut'theran Village dock"), titles

def test_an_update_does_not_mark_a_stop_in_another_zone(env):
    """Found by the real model on the game's own Duskwood: the next part of a chain goes back to an NPC in another
    zone; the model marked him by hand in an update, and the arrow led there with no way."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    snap = {"player": {"coords": "71.9, 46.4", "map": "Duskwood", "level": 20}, "quests": []}

    def turn(text):
        return TurnContext({"msgId": 4, "text": text}, InboxWriter(addons, "S", 4, 40, 4), snap, threading.Event(), [])
    update = turn("[Quest log changed] [Messenger to Darkshire] turned in, its next part taken. Update the route on the map.")
    text, err = svc.run_tool(update, "mark_on_map", {"title": "Magistrate Solomon", "x": 24.9, "y": 44.5,
                                                     "zone": "Redridge Mountains"})
    assert err and 'Call plan_route with it in extra and {"zone": "Redridge Mountains"} in then' in text and not update.marks
    text, err = svc.run_tool(update, "mark_on_map", {"title": "Lord Ello", "x": 71.9, "y": 46.4, "zone": "Duskwood"})
    assert not err, text
    # asked by the player, a mark anywhere is theirs
    asked = turn("mark Solomon for me")
    text, err = svc.run_tool(asked, "mark_on_map", {"title": "Magistrate Solomon", "x": 24.9, "y": 44.5,
                                                    "zone": "Redridge Mountains"})
    assert not err, text

AH_SNAPSHOT = {"player": {"coords": "56.3, 47.5", "map": "Westfall", "zone": "Westfall", "level": 18, "faction": "Alliance"},
               "quests": [{"id": 135, "title": "The Defias Brotherhood", "level": 18, "status": "READY TO TURN IN",
                           "turnIn": "Master Mathias Shaw (Stormwind City 78.3, 70.7)"}]}

def _asked_route(svc, addons, conv="ah"):
    """A route planned with a stop the player asked for, as the map then shows it."""
    import threading
    from foreverai.service import InboxWriter, TurnContext

    def turn(n, snap, text="x"):
        ctx = TurnContext({"msgId": n, "conv": conv, "text": text}, InboxWriter(addons, "S", n, 40, 4), snap, threading.Event(), [])
        ctx.conv = conv
        return ctx
    first = turn(1, dict(AH_SNAPSHOT, quests=[]))
    svc.run_tool(first, "plan_route", {"extra": [{"title": "Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7,
                                                  "when": "last"}]})
    assert [m["title"] for m in first.marks] == ["1. Deadmines entrance"]
    on_map = dict(AH_SNAPSHOT, marks=["1. Deadmines entrance — Westfall 42.5, 71.7 (675 m)"])
    return turn, on_map

def test_marks_do_not_take_the_place_of_a_route_with_stops_to_do(env):
    """From the game: the route ended at a stop the player had asked for; asked to go somewhere else first, the model
    set marks of its own, which took the place of the route: the stop was gone."""
    svc, addons, _ = env
    turn, on_map = _asked_route(svc, addons)
    ctx = turn(2, on_map)
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Thor", "x": 56.5, "y": 52.6, "zone": "Westfall"})
    assert not err and "The route on the map stays, with its stops still to do (Deadmines entrance)" in text
    assert ctx.mark_adds == [True] and ctx.replaces_marks is False
    # a stop in a zone the route does not reach is the planner's: it makes the way there
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Auction House", "x": 60.8, "y": 71.5, "zone": "Stormwind City"})
    assert err and 'Call plan_route with it in extra and {"zone": "Stormwind City"} in then' in text
    # cleared in this answer, the marks are a new set
    ctx = turn(3, on_map)
    svc.run_tool(ctx, "edit_route", {"clear": True})
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Auction House", "x": 60.8, "y": 71.5, "zone": "Stormwind City"})
    assert not err, text
    # all of the route done: nothing stands in the way of new marks
    ctx = turn(4, dict(AH_SNAPSHOT, marks=["1. Deadmines entrance — Westfall 42.5, 71.7 (done)"]))
    text, err = svc.run_tool(ctx, "mark_on_map", {"title": "Auction House", "x": 60.8, "y": 71.5, "zone": "Stormwind City"})
    assert not err and ctx.replaces_marks is True, text

def test_plan_route_keeps_the_stops_the_player_asked_for_earlier(env):
    """From the game: "first to the auction house in Stormwind, then back". Planned again with the new stop alone, the
    stop asked for earlier fell off; and wanted last in the player's own zone, it came first."""
    svc, addons, _ = env
    turn, on_map = _asked_route(svc, addons)
    ctx = turn(2, on_map)
    text, err = svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Stormwind City"}], "extra": [
        {"title": "Auction House", "zone": "Stormwind City", "x": 60.8, "y": 71.5}]})
    titles = [re.sub(r"^\d+\. ", "", m["title"]) for m in ctx.marks]
    assert "Kept on the route, because the player asked for them earlier and they are not done: Deadmines entrance" in text
    assert "Deadmines entrance" in titles and "Auction House" in titles, titles
    # (that it comes last, after the way back, is the planner's: tests/test_world.py, where the ways are known)
    # left out by name: the player no longer wants it
    ctx = turn(3, on_map)
    text, err = svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Stormwind City"}], "skip": ["Deadmines entrance"]})
    assert "Kept on the route" not in text and not any("Deadmines" in m["title"] for m in ctx.marks)
    # done already: not kept
    ctx = turn(4, dict(AH_SNAPSHOT, marks=["1. Deadmines entrance — Westfall 42.5, 71.7 (done)"]))
    text, err = svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Stormwind City"}]})
    assert "Kept on the route" not in text

def test_a_stop_named_again_keeps_what_the_player_wanted_of_it(env):
    """Found by the real model on the game's own question: it named the stop the player had asked for earlier again,
    without "last"; the planner put it first, as the nearest."""
    svc, addons, _ = env
    turn, on_map = _asked_route(svc, addons)
    ctx = turn(2, on_map)
    args, kept = svc._keep_asked(ctx, {"extra": [{"title": "Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7},
                                                 {"title": "Auction House", "zone": "Stormwind City", "x": 60.8, "y": 71.5}]})
    assert kept == [] and args["extra"][0].get("when") == "last" and "when" not in args["extra"][1]
    # what the call says itself stands
    args, _ = svc._keep_asked(ctx, {"extra": [{"title": "Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7,
                                               "when": "first"}]})
    assert args["extra"][0]["when"] == "first"

def test_an_order_that_cannot_be_walked_zone_by_zone_is_refused(env):
    """Found by the real model on the game's own question: after plan_route it put the stops in its own order: a stop
    in the player's zone after the stops of another zone, with no way back between them."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, {"player": {"coords": "56.3, 47.5", "map": "Westfall", "level": 18}})
    ctx.route_marked = True
    ctx.marks = [{"title": "1. The cave", "x": 42.5, "y": 71.7, "zone": "Westfall"},
                 {"title": "2. The gryphon", "x": 56.5, "y": 52.6, "zone": "Westfall", "tasks": [{"kind": "travel", "zone": "Stormwind City"}]},
                 {"title": "3. The auction house", "x": 60.8, "y": 71.5, "zone": "Stormwind City"}]
    ctx.mark_adds = [False, False, False]
    text, err = svc.run_tool(ctx, "edit_route", {"order": ["The gryphon", "The auction house", "The cave"]})
    assert err and "The cave (Westfall) comes after stops of another zone with no way between them" in text
    assert [m["title"] for m in ctx.marks] == ["1. The cave", "2. The gryphon", "3. The auction house"]
    # an order within the zones stands
    ctx.marks.insert(1, {"title": "2. The well", "x": 50.0, "y": 60.0, "zone": "Westfall"})
    ctx.mark_adds.append(False)
    text, err = svc.run_tool(ctx, "edit_route", {"order": ["The well", "The cave"]})
    assert not err, text

def test_a_question_without_flight_masters_uses_the_ones_last_seen(env):
    """From the game: asked a second after login, the game data had no flight masters; the planner knew no flight of
    the character and walked."""
    svc, addons, _ = env
    known = ["Thor <Gryphon Master> [flight path known] — Westfall 56.5, 52.6"]
    with_them = svc._with_flights("Someone-Realm", {"flightMasters": known, "player": {}})
    assert with_them["flightMasters"] == known and "flightMastersFrom" not in with_them
    without = svc._with_flights("Someone-Realm", {"player": {}})
    assert without["flightMasters"] == known and without["flightMastersFrom"] == "an earlier question"
    assert "flightMasters" not in svc._with_flights("Another-Realm", {"player": {}})  # each character has its own
    svc._flights = None  # (after a restart of the app)
    assert svc._with_flights("Someone-Realm", {})["flightMasters"] == known

def _ah_ctx(svc, addons, listed, conv="shop", snapshot=None):
    """An answer in progress, with the game's auction data: ``listed`` name -> (id, price)."""
    ctx = _ctx(svc, addons, snapshot or {})
    ctx.conv = conv
    by_id = {str(i): n for n, (i, _) in listed.items()}

    def game(c, op, req, timeout=45.0):
        assert op == "auction_search"
        if req.get("items"):
            want = [by_id.get(str(x.get("id")), str(x.get("name") or x.get("id"))) for x in req["items"]]
            return {"exact": True, "lastScan": "today", "items": [{"id": listed[n][0], "name": n, "price": listed[n][1]}
                                                                 for n in want if n in listed],
                    "missing": [{"name": n, "why": "not seen in the last scans"} for n in want if n not in listed]}
        return {"items": [{"id": i, "name": n, "price": p} for n, (i, p) in listed.items()], "matched": len(listed)}
    svc.game_call = game
    return ctx

def test_the_auction_card_is_what_the_answer_recommends(env):
    """From the game: the answer recommended the gloves and the trousers of an earlier answer and advised against the
    bow, the ring and the cloak its searches had returned; the card listed the bow, the ring and the cloak, and neither
    of the picks. And the gloves were named at a price the auction house no longer had."""
    svc, addons, _ = env
    listed = {"Gloves of the Fang": (1, "1g 50s"), "Green Tinted Goggles": (2, "15s"), "Short Ash Bow": (3, "17s"),
              "Ring of the Moon": (4, "47s")}
    ctx = _ah_ctx(svc, addons, listed)
    text, err = svc.run_tool(ctx, "search_auction_house", {"slot": "ranged"})
    assert not err and "goes into auctionator_list before you answer" in text
    text, err = svc.run_tool(ctx, "auctionator_list", {"items": ["Gloves of the Fang", "Stonemason Trousers",
                                                                      "Green Tinted Goggles"]})
    assert not err and "- [Gloves of the Fang] (item 1) — 1g 50s" in text  # the price of now, not the one it remembered
    # (an item the last scan has no price for stays on the list, marked so)
    assert "- [Stonemason Trousers] — no price in the last scan (not seen in the last scans) (added now)" in text
    answer = "Buy [Gloves of the Fang] and [Green Tinted Goggles]. Not worth it: [Short Ash Bow], [Ring of the Moon]."
    assert [it["name"] for it in ctx.auction_card(answer)] == ["Gloves of the Fang", "Stonemason Trousers",
                                                               "Green Tinted Goggles"]
    assert any(d.startswith("auction card shown to the player") and "Gloves of the Fang 1g 50s" in d for d in ctx.did())
    # nothing recommended and nothing on the list: no card, whatever the answer names
    ctx = _ah_ctx(svc, addons, listed, conv="none")
    svc.run_tool(ctx, "search_auction_house", {"slot": "ranged"})
    svc.run_tool(ctx, "auctionator_list", {"items": []})
    assert ctx.auction_card("Not worth it: [Short Ash Bow].") is None
    # the game does not answer: the card holds the items without prices, and it is said
    ctx = _ah_ctx(svc, addons, listed, conv="quiet")
    svc.game_call = lambda c, op, req, timeout=45.0: None
    text, err = svc.run_tool(ctx, "auctionator_list", {"items": ["Gloves of the Fang"]})
    assert not err and "The prices could not be checked now" in text and "give no price as current" in text
    assert ctx.auction_card("[Gloves of the Fang]") == [{"name": "Gloves of the Fang"}]

def test_the_shopping_list_is_kept_through_the_conversation(env):
    """From the game, 16:02-16:06: an answer put boots, a belt, bracers, trousers and gloves on the card; the player
    asked about the head slot, and the card of that answer held two helmets and nothing of the list before: "the next
    proposal is only helmets, without the earlier search I asked for"."""
    svc, addons, _ = env
    listed = {"Bristlebark Boots": (11, "39s"), "Dark Leather Pants": (12, "10s 89c"), "Gloves of the Fang": (13, "1g 50s"),
              "Green Tinted Goggles": (14, "16s"), "Flying Tiger Goggles": (15, "4s 98c")}
    first = _ah_ctx(svc, addons, listed)
    svc.run_tool(first, "auctionator_list", {"items": ["Bristlebark Boots", "Dark Leather Pants", "Gloves of the Fang"]})
    # the next answer, about the head slot: it adds to the list
    second = _ah_ctx(svc, addons, listed)
    text, err = svc.run_tool(second, "auctionator_list", {"items": ["Green Tinted Goggles"]})
    assert not err and "- [Bristlebark Boots] (item 11) — 39s (on the list from an earlier answer)" in text
    assert "- [Green Tinted Goggles] (item 14) — 16s (added now)" in text
    assert [it["name"] for it in second.auction_card("")] == ["Bristlebark Boots", "Dark Leather Pants", "Gloves of the Fang",
                                                               "Green Tinted Goggles"]
    # taken off by name; and what the player has now leaves by itself
    bought = {"equipment": [{"item": "[Dark Leather Pants]", "slot": "Legs"}],
              "bags": {"items": [{"item": "[Gloves of the Fang]", "count": 1}]}}  # (bags hold more of what stacks)
    third = _ah_ctx(svc, addons, listed, snapshot=bought)
    text, err = svc.run_tool(third, "auctionator_list", {"items": [], "remove": ["Gloves of the Fang"]})
    assert "[Gloves of the Fang]: taken off as you asked" in text and "[Dark Leather Pants]: the player has it now" in text
    assert [it["name"] for it in third.auction_card("")] == ["Bristlebark Boots", "Green Tinted Goggles"]
    # not listed any more: it stays on the list, marked, until the model takes it off
    del listed["Bristlebark Boots"]
    fourth = _ah_ctx(svc, addons, listed)
    text, err = svc.run_tool(fourth, "auctionator_list", {"items": ["Flying Tiger Goggles"]})
    assert ("- [Bristlebark Boots] (item 11) — no price in the last scan (not seen in the last scans) (on the list "
            "from an earlier answer)") in text
    assert fourth.auction_card("") == [{"id": 11, "name": "Bristlebark Boots"}, {"id": 14, "name": "Green Tinted Goggles", "price": "16s"},
                                       {"id": 15, "name": "Flying Tiger Goggles", "price": "4s 98c"}]
    fifth = _ah_ctx(svc, addons, listed)
    svc.run_tool(fifth, "auctionator_list", {"items": [], "remove": ["Bristlebark Boots"]})
    assert [it["name"] for it in fifth.auction_card("")] == ["Green Tinted Goggles", "Flying Tiger Goggles"]
    # another conversation has a list of its own; and the list outlives a restart of the app
    other = _ah_ctx(svc, addons, listed, conv="another")
    svc.run_tool(other, "auctionator_list", {"items": ["Flying Tiger Goggles"]})
    assert [it["name"] for it in other.auction_card("")] == ["Flying Tiger Goggles"]
    svc._shopping = None
    assert [it["name"] for it in svc._shop()["shop"]["items"]] == ["Green Tinted Goggles", "Flying Tiger Goggles"]
    assert svc._shop()["shop"]["name"] == "ForeverAI: Shopping"  # the Auctionator list it is saved as

def test_the_language_of_a_conversation_is_named_once_by_a_model(env, monkeypatch):
    from foreverai.service import Service
    monkeypatch.setattr(Service, "name_language", True)
    svc, addons, _ = env
    asked = []
    svc._ask_plain = lambda system, prompt: asked.append(prompt) or "Polish."
    history = [{"q": "ułóż mi trasę", "a": "x"}, {"q": "[Quest log changed] took [X]. Update the route on the map.", "a": "y"}]
    assert svc._language("c1", history) == "Polish" and svc._language("c1", history) == "Polish"
    assert asked == ["ułóż mi trasę"]  # the player's own message, asked once
    svc._ask_plain = lambda system, prompt: None  # no answer: nothing is claimed
    assert svc._language("c2", history) is None
    assert svc._language("c3", [{"q": "[Quest log changed] x", "a": "y"}]) is None  # no message of the player's

def test_an_update_does_not_clear_the_route_it_just_planned(env):
    """Found by a journey with the real model: in an update of the route the model planned the route again, then
    cleared it and set marks of its own; the stops of quests in the log were gone."""
    import threading
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    update = TurnContext({"msgId": 4, "text": "[Route check] took [X]. Check the route on the map."},
                         InboxWriter(addons, "S", 4, 40, 4), ROUTE_SNAPSHOT, threading.Event(), [])
    svc.run_tool(update, "plan_route", {})
    planned = [m["title"] for m in update.marks]
    text, err = svc.run_tool(update, "edit_route", {"clear": True})
    assert err and "an update of the route keeps what the quest log needs" in text
    text, err = svc.run_tool(update, "edit_route", {"remove": ["Boar"]})
    assert err and "Boar is where something of a quest of the quest log is to be done" in text
    assert [m["title"] for m in update.marks] == planned
    # asked by the player, the route can go
    asked = _ctx(svc, addons, ROUTE_SNAPSHOT)
    svc.run_tool(asked, "plan_route", {})
    text, err = svc.run_tool(asked, "edit_route", {"clear": True})
    assert not err, text

def test_a_new_plan_keeps_the_zones_the_quest_log_still_needs(env):
    """Found by a journey with the real model: the route led on to a zone where quests of the log are turned in; asked
    for another way, the model named other zones, and the turn-ins were off the route."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, RUTTHERAN)
    ctx.conv = "c7"
    svc.run_tool(ctx, "plan_route", {"then": [{"zone": "Darnassus"}, {"zone": "Teldrassil"}]})
    assert "Darnassus" in {m.get("zone") for m in ctx.marks}
    lines = [f"{m['title']} — {m.get('zone') or 'Teldrassil'} {m['x']:.1f}, {m['y']:.1f}" for m in ctx.marks]
    on_map = dict(RUTTHERAN, marks=lines)
    again = _ctx(svc, addons, on_map)
    again.conv = "c7"
    args, kept = svc._keep_zones(again, {"then": []})
    assert kept == ["Darnassus"] and args["then"] == [{"zone": "Darnassus"}]
    # before the zone named last (where the player wants to end)
    args, kept = svc._keep_zones(again, {"then": [{"zone": "Darkshore"}]})
    assert [h["zone"] for h in args["then"]] == ["Darnassus", "Darkshore"]
    # named already, or its quests left out by name: nothing is added
    assert svc._keep_zones(again, {"then": [{"zone": "Darnassus"}]})[1] == []
    titles = [q["title"] for q in RUTTHERAN["quests"] if isinstance(q, dict)]
    assert svc._keep_zones(again, {"then": [], "skip": titles})[1] == []
    text, err = svc.run_tool(again, "plan_route", {})
    assert not err and "Kept on the route, because it still has stops there for quests of the quest log: Darnassus" in text

def test_what_the_player_saw_and_did_is_kept_with_the_turn(env):
    """The player: "it should see the alerts it sends and how the player reacted to them". The add-on's section
    sinceLastAnswer goes to the model with the message, and stays with the turn in the conversation."""
    from datetime import datetime
    from foreverai.prompt import build_messages
    svc, addons, _ = env
    seen = ["40 s ago: the player dismissed the shopping list card: Gloves of the Fang",
            "12 s ago: route alert shown to the player: Done: Thor. Next: 2. Auction House (120 m)"]
    ctx = _ctx(svc, addons, {"sinceLastAnswer": seen})
    ctx.conv = "seen"
    svc._keep_turn(ctx, "and now?", "Go on.")
    history = svc.convs.history("seen")
    assert history[-1]["seen"] == seen
    msgs = build_messages("what next?", {"sinceLastAnswer": ["3 s ago: the route was taken off the map by the player"]},
                          history, [], now=datetime(2026, 11, 13, 16, 0), kb_date=None, hotfix_date=None)
    earlier = next(m["content"] for m in msgs if m["role"] == "user" and "and now?" in m["content"])
    assert earlier.startswith("[In the game before this message: 40 s ago: the player dismissed the shopping list card")
    assert "## sinceLastAnswer" in msgs[-1]["content"] and "taken off the map by the player" in msgs[-1]["content"]

def test_the_shopping_list_leaves_out_what_the_character_cannot_use(env):
    """From the game: [Green Tinted Goggles] went on the shopping list as an upgrade; it requires Engineering (150) and
    the character has none. Such an item stays off the list, and the result says what it requires; it goes on only for
    a player who asked for it, and then stays through the next answers."""
    svc, addons, _ = env
    listed = {"Green Tinted Goggles": (14, "16s"), "Cured Leather Cap": (15, "20s")}

    def ctx_for(conv="unusable"):
        ctx = _ah_ctx(svc, addons, listed, conv=conv)
        plain = svc.game_call

        def game(c, op, req, timeout=45.0):
            res = plain(c, op, req, timeout)
            for it in res.get("items") or []:
                if it["name"] == "Green Tinted Goggles":
                    it["cannotUse"] = "not met: Requires Engineering (150)"
            return res
        svc.game_call = game
        return ctx
    ctx = ctx_for()
    text, err = svc.run_tool(ctx, "auctionator_list", {"items": ["Green Tinted Goggles", "Cured Leather Cap"]})
    assert not err and "[Green Tinted Goggles]: the character cannot use it (not met: Requires Engineering (150))" in text
    assert [it["name"] for it in ctx.auction_card("")] == ["Cured Leather Cap"]
    # the player asked for it all the same: on the list, said, and kept by the next answer
    ctx = ctx_for()
    text, err = svc.run_tool(ctx, "auctionator_list", {"items": ["Green Tinted Goggles"], "include_unusable": True})
    assert "- [Green Tinted Goggles] (item 14) — 16s (added now) — the character cannot use it" in text
    ctx = ctx_for()
    svc.run_tool(ctx, "auctionator_list", {"items": []})
    assert [it["name"] for it in ctx.auction_card("")] == ["Cured Leather Cap", "Green Tinted Goggles"]
    # an item put on the list before the check existed leaves it
    svc._shop()["old"] = [{"id": 14, "name": "Green Tinted Goggles"}]
    ctx = ctx_for("old")
    text, _ = svc.run_tool(ctx, "auctionator_list", {"items": []})
    assert "[Green Tinted Goggles]: the character cannot use it" in text and ctx.auction_card("") is None

def test_results_of_the_game_that_come_in_one_message_reach_every_caller(env):
    """Lookups of one turn go to the game together and come back as one message (a screenshot each took a second)."""
    import threading
    from foreverai.mcp_server import _tool_list
    svc, addons, _ = env
    ctx = _ctx(svc, addons, {})
    ctx.writer.send({"kind": "answer", "final": False, "msgId": 5, "text": "", "phase": "x"})
    out = {}
    threads = [threading.Thread(target=lambda k=k: out.update({k: svc.game_call(ctx, "auction_search", {"n": k}, timeout=5)}))
               for k in ("a", "b")]
    for t in threads:
        t.start()
    deadline = time.time() + 5
    while time.time() < deadline and len(ctx.writer.requests) < 2:
        time.sleep(0.01)
    ids = {r["n"]: r["id"] for r in ctx.writer.requests}
    deadline = time.time() + 5
    while time.time() < deadline and len(_read_inbox(addons, 1).get("requests") or []) < 2:
        time.sleep(0.01)
    assert len(_read_inbox(addons, 1)["requests"]) == 2 and len(ids) == 2  # both in front of the game at once
    svc.handle({"type": "game_results", "results": [{"id": ids["a"], "result": {"items": [1]}},
                                                    {"id": ids["b"], "result": {"items": [2]}}]})
    for t in threads:
        t.join(5)
    assert out == {"a": {"items": [1]}, "b": {"items": [2]}} and ctx.writer.requests == []
    # the tools that only read are marked so for the client: it runs such calls of one turn at once
    marked = {t["name"] for t in _tool_list() if (t.get("annotations") or {}).get("readOnlyHint")}
    assert "search_auction_house" in marked and "search_knowledge_base" in marked
    assert not marked & {"plan_route", "mark_on_map", "edit_route", "auctionator_list", "save_memory",
                         "propose_auctions", "propose_vendor_action"}


def test_a_kind_of_npc_on_a_route_is_found_where_the_player_is_and_in_other_zones(env):
    """plan_route's extra with a service: the NPCs of that kind are the options, from the game data in the player's
    zone (nearest first there) and from the player's map notes elsewhere; one the app knows none of is said."""
    svc, addons, _ = env
    snap = {"player": {"map": "Westfall", "zone": "Westfall", "faction": "Alliance", "coords": "56.0, 50.0"},
            "zoneNPCs": ["William MacGregor <Bowyer> [repair, vendor] — Westfall 57.7, 53.9 (156 m)",
                         "Quartermaster Lewis <Quartermaster> [vendor] — Westfall 57.0, 47.2 (90 m)"],
            "services": ["repair: William MacGregor <Bowyer> — Westfall 57.7, 53.9 (156 m)"]}
    ctx = _ctx(svc, addons, snap)

    class Notes:
        def installed(self):
            return True

        def npc_spots(self, zone, faction, kind):
            return [("Gregory Ardus", 48.2, 55.1), ("Carla Granger", 62.0, 67.8)] if (zone, kind) == ("Stormwind City", "repair") else []
    svc.map_notes = Notes()
    out, unknown = svc._service_stops(ctx, [{"service": "repair", "zone": "Westfall"},
                                            {"service": "Vendor", "zone": "Stormwind City", "note": "sell junk"},
                                            {"service": "stable master", "zone": "Duskwood"},
                                            {"title": "Gryan", "zone": "Westfall", "x": 56.3, "y": 47.5}])
    assert [o["title"] for o in out[0]["options"]] == ["William MacGregor (repair)"]
    assert [o["title"] for o in out[1]["options"]] == ["Gregory Ardus (vendor)", "Carla Granger (vendor)"]  # (they all sell)
    assert out[1]["note"] == "sell junk" and out[2] == {"title": "Gryan", "zone": "Westfall", "x": 56.3, "y": 47.5}
    assert unknown == ["stable master in Duskwood"]


def test_an_update_of_the_route_changes_it_at_once_never_with_a_card(env):
    """From the game, 2026-11-14 00:50: the check of the route the add-on asked for called plan_route with
    propose=true, and the player got a card waiting for Accept with a route that had not changed, while the map kept
    the old one. In the add-on's own message (an update, a check) the change is made at once."""
    svc, addons, _ = env
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    ctx.route_update = True
    text, err = svc.run_tool(ctx, "plan_route", {"skip": ["Letter"], "propose": True})
    assert not err and ctx.actions == [] and [m["title"] for m in ctx.marks] == ["1. Boar", "2. Giver"]
    assert "Accept" not in text
    ctx = _ctx(svc, addons, ROUTE_SNAPSHOT)
    ctx.route_update = True
    text, err = svc.run_tool(ctx, "edit_route", {"remove": ["Old Stop"], "propose": True})
    assert not err and ctx.actions == [] and ctx.route_edit == {"remove": [{"title": "Old Stop", "x": 40.0, "y": 40.0}]}
    assert "Accept" not in text
