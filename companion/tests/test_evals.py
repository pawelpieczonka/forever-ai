"""The scenario runner that compares prompts (evals/): its checks, its scenarios and one run without a model."""

import pytest

from evals import checks as c
from evals.engine import apply_patch, base_snapshot, run_scenario
from evals.scenarios import SCENARIOS, Scenario, Turn


def turn(answer="", tools=(), did=()):
    return {"answer": answer, "tools": [dict(t) for t in tools], "did": list(did), "secs": 1.0}


def test_words_ignore_markup():
    assert c.words("# Plan\n- **Go** to [Conservator Ilthalaine] (58.7, 44.3), 30 m.") == 6


def test_length_and_heading_checks():
    short, long = turn("Tak, to normalne."), turn("# A\ntekst\n# B\n" + "słowo " * 80)
    assert c.max_words(20)(short).ok and not c.max_words(20)(long).ok
    assert c.max_words(20)(long).detail == "83 words"
    assert c.max_headings(0)(short).ok and not c.max_headings(1)(long).ok


def test_tool_checks():
    t = turn("ok", [{"name": "mcp__foreverai__mark_on_map", "args": {"title": "Dazalar", "x": 56.7, "y": 59.5}},
                    {"name": "mark_on_map", "args": {"title": "Screecher", "x": "64.2", "y": "67.2"}}])
    assert c.calls("mark_on_map", 2, 2)(t).ok and not c.calls("mark_on_map", 0, 1)(t).ok
    assert c.calls("search_knowledge_base", 0, 0)(t).ok
    assert c.marks(56.7, 59.5)(t).ok and c.marks(64.3, 67.0)(t).ok and not c.marks(38.4, 34.1)(t).ok


def test_text_checks():
    t = turn("Użyj [Taming Rod] (F11) na Strigid Screecherze.")
    assert c.says(r"Taming Rod", r"F11")(t).ok and not c.says(r"Dazalar")(t).ok
    assert c.never_says(r"PvP")(t).ok and not c.never_says(r"taming rod")(t).ok
    assert c.says(r"Dazalar")(t).detail == "missing: Dazalar"
    twice = turn("Z pamięci (Classic): tak. Z pamięci (Classic): nie.")
    assert c.at_most(1, r"Classic")(t).ok and not c.at_most(1, r"Classic")(twice).ok


def test_sale_checks():
    t = turn("Sprzedaję.", [{"name": "propose_vendor_action",
                             "args": {"op": "sell", "items": ["[Cracked Shortbow]", "5465"], "reason": "x"}}])
    assert c.sells("Cracked Shortbow")(t).ok and not c.sells("Scroll of Spirit")(t).ok
    assert c.keeps("Skinning Knife", "7005")(t).ok and not c.keeps("Small Spider Leg", "5465")(t).ok


def test_patch_merges_and_deletes():
    base = {"player": {"level": 11, "inCombat": None, "zone": "Teldrassil"}, "target": {"name": "Owl"}, "bags": [1, 2]}
    out = apply_patch(base, {"player": {"inCombat": True}, "target": None, "bags": [3]})
    assert out == {"player": {"level": 11, "inCombat": True, "zone": "Teldrassil"}, "bags": [3]}
    assert base["target"] == {"name": "Owl"}  # the base is not changed


def test_base_snapshot_names_no_real_player():
    import json
    text = json.dumps(base_snapshot())
    assert "Rossmann" not in text and base_snapshot()["player"]["name"] == "Evalhunter"
    assert len(base_snapshot()["nearby"]) <= 3


def test_scenarios_are_well_formed():
    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names)) >= 10
    for s in SCENARIOS:
        assert s.turns and s.why
        for t in s.turns:
            snap = t.snapshot(base_snapshot())
            assert snap["player"]["level"] and t.question and t.checks
            for check in t.checks:
                assert hasattr(check(turn("x")), "ok")


def test_scenario_runs_through_the_real_service(tmp_path, monkeypatch):
    from foreverai import claude_code, prompt
    monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(prompt, "SYSTEM_PROMPT", "TEST PROMPT {today} {kb_date} {hotfix_date}")
    monkeypatch.setattr(claude_code, "detect_cached", lambda: {"path": "claude", "loggedIn": True})
    seen = {"prompts": [], "systems": []}

    class FakeSession:
        def __init__(self, exe, workdir, system, model, effort, search_mode, bridge_url, bridge_token):
            self.key, self.workdir = (model, effort, search_mode, system), workdir
            self.conv, self.turns, self.busy = None, 0, False
            self.bridge = (bridge_url, bridge_token)
            seen["systems"].append(system)
            seen["model"], seen["effort"] = model, effort

        def alive(self):
            return True

        def close(self):
            pass

        def turn(self, prompt, images=None):
            from foreverai.mcp_server import http_bridge
            seen["prompts"].append(prompt)
            self.turns += 1
            if self.turns == 1:
                http_bridge(self.bridge[0].replace("/mcp", "/tool"), self.bridge[1])(
                    "mark_on_map", {"title": "Dazalar", "x": 56.7, "y": 59.5})
            yield "final", "Idź do Dazalara." if self.turns == 1 else "Tak."

    monkeypatch.setattr(claude_code, "ClaudeSession", FakeSession)
    sc = Scenario("two_turns", "the engine", [
        Turn("gdzie oddac?", [c.calls("mark_on_map", 1, 1), c.max_words(2)], memory=["Build: BM"]),
        Turn("ok?", [c.calls("mark_on_map", 0, 0), c.says("Tak")], patch={"player": {"inCombat": True}}),
    ])
    res = run_scenario(sc, model="claude-haiku-5-5", effort="low")
    assert [t["answer"] for t in res["turns"]] == ["Idź do Dazalara.", "Tak."]
    assert [[(r.label, r.ok) for r in t["checks"]] for t in res["turns"]] == [
        [("mark_on_map x1", True), ("at most 2 words", False)], [("mark_on_map x0", True), ("says Tak", True)]]
    assert res["passed"] == 3 and res["failed"] == 1
    assert seen["model"] == "claude-haiku-5-5" and seen["effort"] == "low" and seen["systems"][0].startswith("TEST PROMPT 20")
    assert "Build: BM" in seen["prompts"][0] and "inCombat: True" in seen["prompts"][1]
    assert "Evalhunter" in seen["prompts"][0] and len(seen["systems"]) == 1  # one session for the conversation


def test_language_check():
    pl = turn("Tak, to normalne. Użyj [Taming Rod] i stój w miejscu, aż pasek się skończy.")
    en = turn("Yes, that is normal. Use the [Taming Rod] and stand still until the bar is done, then you are set.")
    assert c.language("pl")(pl).ok and not c.language("pl")(en).ok
    assert c.language("en")(en).ok and not c.language("en")(pl).ok
    assert c.language("pl")(en).label == "answers in pl"


def test_marks_are_counted_from_what_the_answer_did():
    """The route tool marks its stops itself: the marks are in "did", not in mark_on_map calls."""
    t = turn("Trasa.", [{"name": "plan_route", "args": {}}],
             did=["marked on the map (earlier marks removed): 1. Dazalar (56.7, 59.5)",
                  "marked on the map: 2. Oracle Glade Moonwell (38.4, 34.1)"])
    assert c.marked(2, 2)(t).ok and not c.marked(3, 8)(t).ok and c.marked(3, 8)(t).detail == "2 marks"
    assert c.marks(56.7, 59.5)(t).ok and c.marks(38.4, 34.1)(t).ok and not c.marks(10, 10)(t).ok
    added = turn("ok", did=["added to the marks on the map: Lyreena (57.6, 56.6)"])
    assert c.marked(1, 1)(added).ok and c.marks(57.6, 56.6)(added).ok
