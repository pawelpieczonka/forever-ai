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
    assert d["status"]["balance"] == 6.17 and d["status"]["keyMask"] == "sk-or-…nop"
    assert d["cost"] == 0.0012
    sys_prompt = FakeClient.seen_messages[0]["content"]
    assert "is not Classic Era" in sys_prompt and "1.60.1.70862" in FakeClient.seen_messages[1]["content"]


def test_set_api_key_validates_and_masks(env):
    svc, addons, tmp = env
    svc.handle({"type": "set_api_key", "value": "bad", "session": "S", "sigPrefix": "k", "nextInbox": 1})
    d = _read_inbox(addons, 1)
    assert d["kind"] == "error" and "sk-or-v1-abcdefghijklmnop" in (tmp / ".env").read_text()
    svc.handle({"type": "set_api_key", "value": "sk-or-v1-NEWKEY0000000999", "session": "S", "sigPrefix": "k",
                "nextInbox": 2})
    d = _read_inbox(addons, 2)
    assert d["kind"] == "status" and d["status"]["keyMask"] == "sk-or-…999"
    assert "NEWKEY" in (tmp / ".env").read_text()
    assert "NEWKEY" not in json.dumps(d)


def test_set_model_rejects_unknown(env):
    svc, addons, _ = env
    svc.handle({"type": "set_model", "value": "nope/model", "session": "S", "sigPrefix": "m", "nextInbox": 1})
    assert _read_inbox(addons, 1)["kind"] == "error"
    svc.handle({"type": "set_model", "value": "openai/gpt-6-luna", "session": "S", "sigPrefix": "m", "nextInbox": 2})
    assert _read_inbox(addons, 2)["status"]["model"] == "openai/gpt-6-luna"


def test_claude_code_provider(env, monkeypatch):
    from foreverai import claude_code
    svc, addons, _ = env
    # not installed -> switching is refused, no suggestion
    svc.handle({"type": "set_provider", "value": "claude_code", "session": "S", "nextInbox": 1})
    assert _read_inbox(addons, 1)["kind"] == "error"
    assert svc.status()["suggestClaude"] is False

    info = {"path": "claude.exe", "version": "2.1.304", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    st = svc.status()
    assert st["suggestClaude"] is True and st["claude"]["model"] == "claude-haiku-5-5" and st["claude"]["effort"] == "high"

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
    svc.handle({"type": "set_provider", "value": "claude_code", "session": "S", "nextInbox": 2})
    d = _read_inbox(addons, 2)
    assert d["kind"] == "status" and d["status"]["provider"] == "claude_code" and d["status"]["suggestClaude"] is False
    svc.handle({"type": "set_effort", "value": "xhigh", "session": "S", "nextInbox": 3})
    assert _read_inbox(addons, 3)["status"]["claude"]["effort"] == "xhigh"

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
                     "mark_on_map", "addon_options", "propose_quest_abandon", "propose_vendor_action",
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
