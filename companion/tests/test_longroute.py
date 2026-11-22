"""The long route (longroute.py and the long_route tool): a route to a level, kept by the app part after part."""

import json

import pytest

from foreverai import config, longroute
from foreverai.inbox import ensure_pool
from foreverai.openrouter import ToolCall
from foreverai.service import Service

from test_service import FakeClient, _wait_final


# ------------------------------------------------------------------------ XP
def test_quest_xp_is_reduced_once_the_character_is_six_levels_above_the_quest():
    # (Questie's getAdjustedXP: cmangos)
    assert longroute.quest_xp(2050, 27, 25) == 2050
    assert longroute.quest_xp(2050, 27, 32) == 2050  # five above: all of it
    assert longroute.quest_xp(2050, 27, 33) == 1650  # six above: 8 tenths, rounded to 50
    assert longroute.quest_xp(2050, 27, 37) == 210  # ten above: one tenth, rounded to 10
    assert longroute.quest_xp(330, 5, 15) == 35
    assert longroute.quest_xp(0, 27, 25) == 0
    assert longroute.quest_xp(1000, None, 25) == 1000


def test_the_base_xp_of_a_quest_of_the_log_comes_from_what_it_gives_now():
    assert longroute.base_xp({"xp": 2200, "level": 27}, 25) == (2200, False)
    # the quest log says what it gives at the character's level
    assert longroute.base_xp({"rewards": "1750 XP, 0g 20s 0c", "level": 29}, 25) == (1750, False)
    assert longroute.base_xp({"rewards": "1,320 XP", "level": 20}, 27) == pytest.approx((2200, False))  # 6 tenths now
    # nothing known: a typical quest of its level, and said to be a guess
    assert longroute.base_xp({"level": 25}, 25) == (2000, True)


def test_the_level_carries_over_level_ups_with_the_classic_table():
    lv = longroute.Level(25, 30000, 34000)
    lv.add(4000 + 36400 + 1000)  # the rest of 25, all of 26, 1000 into 27
    assert lv.level == 27 and lv.xp == pytest.approx(1000)
    assert lv.value() == pytest.approx(27 + 1000 / 38900)
    assert longroute.Level(25, 30000, 34000).to(27) == pytest.approx(4000 + 36400)
    # a server with other figures: the table is scaled to the level the game showed
    lv = longroute.Level(25, 0, 17000)
    lv.add(17000 + 9100)
    assert lv.level == 26 and lv.xp == pytest.approx(9100) and lv.value() == pytest.approx(26.5)
    # never past the highest level
    top = longroute.Level(59, 0, 209800, max_level=60)
    top.add(10 ** 7)
    assert top.level == 60 and top.value() == 60


def test_creatures_count_with_the_players_own_share_once_enough_is_known():
    share, note = longroute.kill_share({"progress": {"session": {"gain": 1000, "questXp": 500},
                                                    "sessions": [{"gain": 99000, "questXp": 52000}]}})
    assert share == pytest.approx(47500 / 52500) and "48% of their XP came from creatures" in note
    # too little played: an assumed share, on the low side
    assert longroute.kill_share({"progress": {"session": {"gain": 3000, "questXp": 2000}}})[0] == longroute.SHARE_DEFAULT
    assert longroute.kill_share({})[0] == longroute.SHARE_DEFAULT
    # a player who only grinds does not make quests worth five times their XP
    assert longroute.kill_share({"progress": {"sessions": [{"gain": 90000, "questXp": 5000}]}})[0] == longroute.SHARE_MAX


# ------------------------------------------------------------------------ the world of the tests
PLAYER = {"level": 25, "xp": 0, "xpMax": 34000, "rested": 0, "map": "Wetlands", "zone": "Wetlands", "coords": "10.0, 60.0"}


def _q(qid, title, level, x, y, xp, *, mob=None, min_level=None, giver="Captain Stoutfist", turn_in=None):
    """A quest as zone_quests lists it: given and turned in at Menethil, its creature out east."""
    q = {"id": qid, "title": title, "level": level, "minLevel": min_level or level - 5, "xp": xp, "giver": giver,
         "where": f"Wetlands {x:.1f}, {y:.1f}", "turnIn": turn_in or f"{giver} (Wetlands {x:.1f}, {y:.1f})"}
    if mob:
        q["whereToGet"] = [f"{mob}: kill {mob} (level {level - 1}-{level}) — Wetlands {x + 15:.1f}, {y - 5:.1f} "
                           "(900 m, 12 spawn points in your zone)"]
    return q


MENETHIL = [_q(301, "Claws from the Deep", 26, 10.5, 60.2, 2050, mob="Bluegill Murloc"),
            _q(302, "Young Crocolisk Skins", 25, 11.0, 58.0, 1950, mob="Young Crocolisk", giver="Karl Boran")]
DUN_MODR = [_q(311, "The Dark Iron War", 27, 49.8, 18.3, 2200, mob="Dark Iron Dwarf", giver="Longbraid the Grim"),
            _q(312, "Fall of Dun Modr", 28, 49.9, 18.5, 2350, mob="Dark Iron Spy", giver="Longbraid the Grim"),
            _q(313, "Crushing the Crown", 30, 49.5, 18.0, 2700, mob="Dark Iron Demolitionist", giver="Rhag Garmason",
               min_level=27)]
ZONES = {"wetlands": MENETHIL + DUN_MODR}


def _snapshot(**more):
    snap = {"player": dict(PLAYER), "quests": [], "marks": [], "routeDone": []}
    snap.update(more)
    return snap


def test_a_part_names_only_quests_the_game_data_has():
    offered = {q["id"]: q for q in MENETHIL}
    snap = _snapshot(quests=[{"id": 290, "title": "The Lost Ingots", "level": 29, "rewards": "1750 XP",
                              "turnIn": "Glorin Steelbrow (Wetlands 10.6, 60.6)"}])
    part, problems = longroute.make_part({"title": "Menethil Harbor", "zone": "Wetlands",
                                          "quests": ["[Claws from the Deep]", "the lost ingots", "Made Up Quest",
                                                     "Claws from the Deep"]}, snap, offered, set())
    assert [q["title"] for q in part["quests"]] == ["Claws from the Deep", "The Lost Ingots"]
    assert part["quests"][1]["inLog"] is True and part["quests"][0]["xp"] == 2050
    assert len(problems) == 1 and "[Made Up Quest] is left out" in problems[0] and "zone_quests" in problems[0]
    # a quest an earlier part holds, or one the character has done, is not taken twice
    part2, problems2 = longroute.make_part({"zone": "Wetlands", "quests": ["Claws from the Deep", "Young Crocolisk Skins"]},
                                           _snapshot(routeDone=[302]), offered, {"claws from the deep"})
    assert part2 is None and any("an earlier part has it" in p for p in problems2) and any("done it already" in p for p in problems2)
    assert longroute.make_part({"title": "Nowhere", "quests": ["Claws from the Deep"]}, snap, offered, set())[0] is None


def test_the_next_part_of_a_chain_can_be_named_from_the_quest_that_leads_to_it():
    snap = _snapshot(quests=[{"id": 324, "title": "The Lost Ingots", "level": 29, "rewards": "1750 XP",
                              "next": "[Blessed Arm] (level 29) from Glorin Steelbrow (Wetlands 10.6, 60.6)",
                              "nextId": 325, "nextXp": 1800}])
    part, problems = longroute.make_part({"zone": "Wetlands", "quests": ["The Lost Ingots", "Blessed Arm"]}, snap, {}, set())
    assert not problems
    arm = part["quests"][1]
    assert (arm["id"], arm["level"], arm["xp"], arm["giver"], arm["after"]) == (325, 29, 1800, "Glorin Steelbrow",
                                                                                "The Lost Ingots")


def _route_of(parts, snap, goal=27):
    route = longroute.new_route(goal, "Level 25 to 27", "", snap, "c", [])
    offered = {q["id"]: q for q in MENETHIL + DUN_MODR}
    for raw in parts:
        part, _ = longroute.make_part(raw, snap, offered, longroute.taken_titles(route))
        route["parts"].append(part)
    return route


def test_the_levels_after_each_part_follow_from_the_xp_of_its_quests_and_the_creatures():
    snap = _snapshot(progress={"sessions": [{"gain": 40000, "questXp": 25000}]})  # creatures: 60% of the quests' XP
    route = _route_of([{"title": "Menethil Harbor", "zone": "Wetlands",
                        "quests": ["Claws from the Deep", "Young Crocolisk Skins"]},
                       {"title": "Dun Modr", "zone": "Wetlands",
                        "quests": ["The Dark Iron War", "Fall of Dun Modr", "Crushing the Crown"]}], snap)
    route["mapped"] = 0  # (the app put part 1 on the map)
    est = longroute.estimate(route, snap)
    one, two = est["parts"]
    assert one["quests"] == 4000 and one["kills"] == pytest.approx(2400)
    assert one["from"] == 25 and one["to"] == pytest.approx(25 + 6400 / 34000)
    assert two["quests"] == 2200 + 2350 + 2700 and two["kills"] == pytest.approx(7250 * 0.6)
    assert est["end"] == pytest.approx(25 + (6400 + 7250 * 1.6) / 34000)
    assert est["short"] == pytest.approx(34000 + 36400 - 6400 - 7250 * 1.6)
    # [Crushing the Crown] opens at 27, and the character is not 27 when Dun Modr comes
    assert any("[Crushing the Crown] opens at level 27" in w for w in est["warnings"])
    text = longroute.render(route, snap, est)
    assert "1. Menethil Harbor (Wetlands) [on the map now]: level 25.0 -> 25.2, 2 quests, 4,000 XP from quests" in text
    assert "levels short of 27" in text and "add parts" in text
    assert "Creatures on the way count 60% of the quests' XP (the player's own pace" in text
    # a part that is done counts no more, and a quest turned in is in the character's XP already
    route["current"] = 1
    later = longroute.estimate(route, _snapshot(routeDone=[311]))
    assert later["parts"][0] == {"done": True} and later["parts"][1]["left"] == 2


def test_rested_xp_doubles_the_creatures_of_the_first_parts():
    snap = _snapshot(player=dict(PLAYER, rested=1000))
    route = _route_of([{"title": "Menethil Harbor", "zone": "Wetlands", "quests": ["Claws from the Deep"]}], snap)
    (part,) = longroute.estimate(route, snap)["parts"]
    assert part["kills"] == pytest.approx(2050 * longroute.SHARE_DEFAULT + 820)


def test_a_part_is_planned_with_its_quests_and_without_those_of_later_parts():
    log = [{"id": 290, "title": "The Lost Ingots", "level": 29, "rewards": "1750 XP"},
           {"id": 291, "title": "Daily Delivery", "level": 26, "rewards": "900 XP"}]
    snap = _snapshot(quests=log)
    route = _route_of([{"title": "Menethil Harbor", "zone": "Wetlands", "quests": ["Claws from the Deep"],
                        "extra": [{"service": "repair", "zone": "Wetlands"}], "first": ["Claws from the Deep"]},
                       {"title": "Dun Modr", "zone": "Wetlands", "then": ["Arathi Highlands"],
                        "quests": ["The Dark Iron War", "The Lost Ingots", "Crushing the Crown"]}], snap)
    args = longroute.part_args(route, 0, snap)
    assert args["new_quests"] == ["Claws from the Deep"]
    assert args["skip"] == ["The Lost Ingots"]  # it waits for Dun Modr; [Daily Delivery] is done where the route passes
    assert args["extra"] == [{"service": "repair", "zone": "Wetlands"}] and args["first"] == ["Claws from the Deep"]
    assert "then" not in args  # the player is in Wetlands already
    assert [q["id"] for q in args["offered"]] == [301]
    two = longroute.part_args(route, 1, snap)
    # [Crushing the Crown] opens at 27: the character cannot pick it up yet
    assert two["new_quests"] == ["The Dark Iron War"] and two["then"] == [{"zone": "Arathi Highlands"}]
    assert "skip" not in two
    assert longroute.waiting(route, 1, snap) == ["[Crushing the Crown] (opens at level 27)"]
    assert longroute.waiting(route, 1, _snapshot(player=dict(PLAYER, level=27))) == []


def test_the_long_route_of_each_character_is_kept_in_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path))
    store = longroute.Store()
    store.put("Magik-Realm", {"id": "a1", "goal": 29, "parts": []})
    assert json.loads((tmp_path / "long_routes.json").read_text())["Magik-Realm"]["goal"] == 29
    assert longroute.Store().get("Magik-Realm")["id"] == "a1" and longroute.Store().get("Other") is None
    assert store.drop("Magik-Realm") and longroute.Store().get("Magik-Realm") is None and not store.drop("Magik-Realm")


# ------------------------------------------------------------------------ the tool, through the app
@pytest.fixture
def env(tmp_path, monkeypatch):
    from foreverai import claude_code
    monkeypatch.setattr(claude_code, "detect", lambda force=False: None)
    monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(Service, "fetch_route_quests", True)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnop\n")
    addons = tmp_path / "AddOns"
    ensure_pool(addons, size=20)
    return config.SettingsStore(tmp_path / "settings.json"), addons, tmp_path


def _game(asked):
    """The game: zone_quests lists the quests of a zone the character can take now, or up to a level."""
    def call(ctx, op, args, timeout=45.0):
        asked.append((op, dict(args)))
        if op != "zone_quests":
            return None
        level = (ctx.snapshot.get("player") or {}).get("level") or 25
        upto = args.get("upTo") or level
        quests = [q for q in ZONES.get(str(args.get("zone")).lower(), []) if q["minLevel"] <= upto
                  and q["id"] not in set(ctx.snapshot.get("routeDone") or [])
                  and q["id"] not in {x.get("id") for x in ctx.snapshot.get("quests") or []}]
        return {"zone": args.get("zone"), "level": level, "quests": quests, **({"upTo": upto} if args.get("upTo") else {})}
    return call


BUILD = [ToolCall("t1", "zone_quests", '{"zone": "Wetlands", "up_to_level": 27}'),
         ToolCall("t2", "long_route", json.dumps({"op": "new", "goal_level": 27, "title": "Level 25 to 27",
                                                  "summary": "Menethil, then Dun Modr"})),
         ToolCall("t3", "long_route", json.dumps({"op": "add", "parts": [
             {"title": "Menethil Harbor", "zone": "Wetlands", "quests": ["Claws from the Deep", "Young Crocolisk Skins"],
              "note": "the murlocs and crocolisks by the harbour"},
             {"title": "Dun Modr", "zone": "Wetlands", "quests": ["The Dark Iron War", "Fall of Dun Modr", "No Such Quest"]}]}))]


def _builder(results):
    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            done = [m for m in messages if m["role"] == "tool"]
            if "Player question: Make me a route from 25 to 27" in str(messages[-1]["content"]) or done:
                if len(done) < len(BUILD):
                    yield "tool_calls", [BUILD[len(done)]]
                    return
                results[:] = [m["content"] for m in done]
            yield "text", "The route is ready."
    return Client


def _ask(svc, addons, n, text, snapshot, **more):
    svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": text,
                "char": "Magik-Realm", "snapshot": snapshot, **more})
    return _wait_final(addons, n)


def _built(env):
    store, addons, tmp = env
    results, asked = [], []
    svc = Service(store, None, addons, client_factory=_builder(results), lookahead=4)
    svc.game_call = _game(asked)
    d = _ask(svc, addons, 1, "Make me a route from 25 to 27", _snapshot())
    return svc, addons, tmp, d, results, asked


def test_a_long_route_is_built_in_steps_and_its_first_part_goes_on_the_map(env):
    svc, addons, tmp, d, results, asked = _built(env)
    # the zone was read up to the goal: its quests that open later are there too
    assert ("zone_quests", {"zone": "Wetlands", "upTo": 27}) in asked
    assert "opens at level 27" in results[0] and "2,700 XP at its level" in results[0]
    assert results[1].startswith("Long route \"Level 25 to 27\" (goal: level 27)") and "No parts yet" in results[1]
    built = results[2]
    assert "1. Menethil Harbor (Wetlands) [on the map now]: level 25.0 ->" in built
    assert "2. Dun Modr (Wetlands): level" in built
    assert "[No Such Quest] is left out of part \"Dun Modr\"" in built
    assert "Part 1 on the map now:" in built and "marked on the map in this order" in built
    # the stops of part 1 are the marks of the answer
    titles = [m["title"] for m in d["marks"]]
    assert titles and all(any(t.get("quest") in ("Claws from the Deep", "Young Crocolisk Skins") for t in m.get("tasks") or [])
                          for m in d["marks"])
    assert d["routePlanned"] is True
    # the add-on gets the parts as a card
    lr = d["longRoute"]
    assert lr["card"] is True and lr["current"] == 1 and lr["goal"] == 27 and lr["title"] == "Level 25 to 27"
    assert [p["title"] for p in lr["parts"]] == ["Menethil Harbor", "Dun Modr"]
    assert lr["parts"][0]["from"] == 25.0 and lr["parts"][0]["quests"] == 2 and lr["parts"][1]["quests"] == 2
    assert lr["parts"][0]["note"] == "the murlocs and crocolisks by the harbour"
    # kept in the file of the character
    kept = json.loads((tmp / "data" / "long_routes.json").read_text())["Magik-Realm"]
    assert kept["goal"] == 27 and kept["current"] == 0 and kept["mapped"] == 0 and len(kept["parts"]) == 2
    # and the model sees it with every question from now on
    seen = []

    class Asker(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            seen.append(messages[-1]["content"])
            yield "text", "ok"
    other = Service(svc.store, None, addons, client_factory=Asker, lookahead=4)
    other.game_call = _game([])
    _ask(other, addons, 3, "What next?", _snapshot(marks=["1. Captain Stoutfist — Wetlands 10.5, 60.2 (40 m)"]))
    assert "# The long route" in seen[0] and "1. Menethil Harbor (Wetlands) [on the map now]" in seen[0]
    assert "[Claws from the Deep]" not in seen[0].split("# The long route")[1].split("#")[0]  # (short: no quests)


def _done_snapshot(*ids, level=25, marks=None):
    return _snapshot(player=dict(PLAYER, level=level), routeDone=list(ids),
                     marks=marks or ["1. Captain Stoutfist — Wetlands 10.5, 60.2 (done)"])


UPDATE = "[Quest log changed] the stops of part 1 of the long route are done. Put the next part on the map."


def test_the_next_part_goes_on_the_map_by_itself_when_the_part_on_the_map_is_done(env):
    svc, addons, tmp, *_ = _built(env)
    d = _ask(svc, addons, 5, UPDATE, _done_snapshot(301, 302), auto=True, news="Next part of the long route", fast=True)
    assert d["text"] == "Long route: part 2 of 2, Dun Modr."
    assert {t.get("quest") for m in d["marks"] for t in m.get("tasks") or []} == {"The Dark Iron War", "Fall of Dun Modr"}
    assert d["routePlanned"] is True and d["marksReplace"] is True
    lr = d["longRoute"]
    assert lr["current"] == 2 and "card" not in lr and lr["parts"][0] == {"title": "Menethil Harbor", "zone": "Wetlands",
                                                                          "done": True,
                                                                          "note": "the murlocs and crocolisks by the harbour"}
    kept = json.loads((tmp / "data" / "long_routes.json").read_text())["Magik-Realm"]
    assert kept["current"] == 1 and kept["mapped"] == 1 and kept["parts"][0]["done"] is True

    # the last part done: the long route is finished, and the add-on is told
    d = _ask(svc, addons, 7, UPDATE.replace("part 1", "part 2"),
             _done_snapshot(301, 302, 311, 312, marks=["1. Longbraid the Grim — Wetlands 49.8, 18.3 (done)"]),
             auto=True, news="Next part of the long route", fast=True)
    assert d["text"] == "Long route finished: Level 25 to 27."
    assert d["longRoute"]["ended"] is True
    assert "Magik-Realm" not in json.loads((tmp / "data" / "long_routes.json").read_text())


def test_a_quest_that_opens_later_comes_on_the_part_when_the_level_is_reached(env):
    store, addons, tmp = env
    results, asked = [], []
    build = list(BUILD)
    BUILD[2] = ToolCall("t3", "long_route", json.dumps({"op": "add", "parts": [
        {"title": "Dun Modr", "zone": "Wetlands", "quests": ["The Dark Iron War", "Crushing the Crown"]}]}))
    try:
        svc = Service(store, None, addons, client_factory=_builder(results), lookahead=4)
        svc.game_call = _game(asked)
        d = _ask(svc, addons, 1, "Make me a route from 25 to 27", _snapshot())
    finally:
        BUILD[:] = build
    assert "Not on the map yet, because the character's level does not open them: [Crushing the Crown] (opens at level 27)" in results[2]
    assert "Crushing the Crown" not in json.dumps(d["marks"])
    # level 27: the update plans the part again with it
    marks = ["1. Longbraid the Grim — Wetlands 49.8, 18.3 (300 m)", "2. Dark Iron Dwarf — Wetlands 64.8, 13.3"]
    d = _ask(svc, addons, 5, "[Quest log changed] level 27. Update the route on the map.",
             _snapshot(player=dict(PLAYER, level=27), marks=marks), auto=True, news="Updating the route", fast=True)
    assert "Crushing the Crown" in json.dumps(d["marks"])


def test_the_model_changes_skips_and_ends_the_long_route(env):
    svc, addons, tmp, *_ = _built(env)
    ctx_snap = _snapshot(marks=["1. Captain Stoutfist — Wetlands 10.5, 60.2 (40 m)"])
    from test_service import _ctx
    ctx = _ctx(svc, addons, ctx_snap)
    ctx.conv = "c"
    svc._char = "Magik-Realm"
    # a part changed that is not on the map: the map stays
    text, err = svc.run_tool(ctx, "long_route", {"op": "change", "part": 2, "parts": [
        {"title": "Dun Modr", "zone": "Wetlands", "quests": ["Fall of Dun Modr"]}]})
    assert not err and not ctx.marks and "2. Dun Modr (Wetlands): level" in text and ctx.long_card
    assert [q["title"] for q in svc.long_routes.get("Magik-Realm")["parts"][1]["quests"]] == ["Fall of Dun Modr"]
    # go: the player skips ahead
    text, err = svc.run_tool(ctx, "long_route", {"op": "go", "part": 2})
    assert not err and "Part 2 on the map now" in text
    assert {t.get("quest") for m in ctx.marks for t in m.get("tasks") or []} == {"Fall of Dun Modr"}
    kept = svc.long_routes.get("Magik-Realm")
    assert kept["current"] == 1 and kept["parts"][0].get("done") is True
    assert "1. Menethil Harbor (Wetlands): done." in svc.run_tool(ctx, "long_route", {"op": "show"})[0]
    # bad calls say what is wrong
    assert svc.run_tool(ctx, "long_route", {"op": "remove", "part": 9})[1] is True
    assert svc.run_tool(ctx, "long_route", {"op": "new", "goal_level": 20})[1] is True
    assert svc.run_tool(ctx, "long_route", {"op": "fly"})[1] is True
    # end: gone from the file and from the add-on
    text, err = svc.run_tool(ctx, "long_route", {"op": "end"})
    assert not err and svc.long_routes.get("Magik-Realm") is None
    # (this answer showed it before it ended: the add-on is told that it ended, not shown the card)
    assert ctx.long_route_payload() == {"longRoute": {"id": kept["id"], "ended": True}}
    assert svc.run_tool(ctx, "long_route", {"op": "add", "parts": [{"zone": "Wetlands", "quests": ["x"]}]})[1] is True


def test_the_player_ends_the_long_route_in_the_window(env):
    svc, addons, tmp, *_ = _built(env)
    rid = svc.long_routes.get("Magik-Realm")["id"]

    class Asker(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            Asker.seen = messages[-1]["content"]
            yield "text", "ok"
    other = Service(svc.store, None, addons, client_factory=Asker, lookahead=4)
    d = _ask(other, addons, 9, "Thanks", _snapshot(longRoute={"id": rid, "ended": True}))
    assert d["longRoute"] == {"id": rid, "ended": True}
    assert other.long_routes.get("Magik-Realm") is None and "# The long route" not in Asker.seen
    assert "longRoute" not in Asker.seen  # (the add-on's own section is for the app)


def test_a_new_conversation_follows_the_part_on_the_map(env):
    svc, addons, tmp, *_ = _built(env)
    svc.handle({"type": "question", "msgId": 5, "session": "S", "nextInbox": 5, "conv": "other", "text": UPDATE,
                "char": "Magik-Realm", "snapshot": _done_snapshot(301, 302), "auto": True,
                "news": "Next part of the long route", "fast": True})
    d = _wait_final(addons, 5)
    assert d["text"] == "Long route: part 2 of 2, Dun Modr." and d["longRoute"]["current"] == 2


def test_another_route_in_between_does_not_finish_the_part_of_the_long_route(env):
    """The player asks for something else on the way (the way to a trainer): that route takes the map, and when it is
    done the long route's part is not done with it. The long route waits, and goes on when the AI puts it back."""
    svc, addons, tmp, *_ = _built(env)
    from test_service import _ctx
    ctx = _ctx(svc, addons, _snapshot(marks=["1. Captain Stoutfist — Wetlands 10.5, 60.2 (40 m)"]))
    ctx.conv = "c"
    svc._char = "Magik-Realm"
    text, err = svc.run_tool(ctx, "plan_route", {"extra": [{"title": "Trainer", "zone": "Wetlands", "x": 11, "y": 59}],
                                                "skip": ["Claws from the Deep", "Young Crocolisk Skins"]})
    assert not err and "The long route's part 1 (Menethil Harbor) is off the map now" in text, text
    kept = svc.long_routes.get("Magik-Realm")
    assert kept["mapped"] is None and kept["current"] == 0
    assert ctx.long_route["offMap"] is True and "card" not in ctx.long_route_payload()["longRoute"]
    assert "[the current part, not on the map: long_route go puts it there]" in svc.run_tool(ctx, "long_route", {"op": "show"})[0]
    # parts added meanwhile leave the map as it is
    ctx2 = _ctx(svc, addons, _snapshot())
    ctx2.conv = "c"
    text, err = svc.run_tool(ctx2, "long_route", {"op": "add", "parts": [
        {"title": "Crushing", "zone": "Wetlands", "quests": ["Crushing the Crown"]}]})
    assert not err and not ctx2.marks and "Part 1 on the map now" not in text
    # the trainer's route is done: nothing of the long route moves on
    svc._plans()["c"] = {"extra": [{"title": "Trainer", "zone": "Wetlands", "x": 11, "y": 59}]}
    d = _ask(svc, addons, 5, "[Quest log changed] the trainer is done. Update the route on the map.",
             _snapshot(marks=["1. Trainer — Wetlands 11.0, 59.0 (done)"]), auto=True, news="Updating the route", fast=True)
    assert "Long route" not in str(d.get("text"))
    kept = svc.long_routes.get("Magik-Realm")
    assert kept["current"] == 0 and not kept["parts"][0].get("done")
    # put back: the part is on the map again
    ctx3 = _ctx(svc, addons, _snapshot())
    ctx3.conv = "c"
    text, err = svc.run_tool(ctx3, "long_route", {"op": "go", "part": 1})
    assert not err and "Part 1 on the map now" in text and ctx3.marks
    assert svc.long_routes.get("Magik-Realm")["mapped"] == 0 and "offMap" not in ctx3.long_route


def test_the_tool_schema_has_properties_for_every_object():
    """Gemini's function declarations refuse an object type without properties."""
    from foreverai.tools import TOOLS
    (spec,) = [t["function"] for t in TOOLS if t["function"]["name"] == "long_route"]
    bad = []

    def walk(node, path):
        if isinstance(node, dict):
            if node.get("type") == "object" and not node.get("properties"):
                bad.append(path)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    walk(spec["parameters"], "long_route")
    assert bad == []
    assert spec["parameters"]["properties"]["op"]["enum"] == ["new", "add", "change", "remove", "go", "show", "end"]
