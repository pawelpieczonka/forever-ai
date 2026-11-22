"""Dungeon runs in a long route (longroute.py, dungeon_plan; see test_addon_dungeons.py for the add-on): a part whose
quests are picked up on the way and done inside a dungeon, whose entrance is its last stop, whose creatures are those
of a run in a group of five, and whose next part (the turn-ins) waits until the player is out of the dungeon."""

import json

import pytest

from foreverai import longroute
from foreverai.openrouter import ToolCall
from foreverai.route import plan_route
from foreverai.service import Service
from foreverai.tools import TOOLS, format_dungeon_plan

from test_longroute import MENETHIL, PLAYER, _ask, _game, _snapshot, env  # noqa: F401 (env: the fixture)

# The Excavation Site of the Wetlands (one of WoW Forever's own dungeons) as the add-on reads it from the player's
# DungeonJournal add-on, for a level 25 character in Menethil Harbor.
EXCAVATION = {
    "name": "Excavation Site: Wetlands", "level": "26-33", "min": 26, "max": 33, "location": "Wetlands", "character": 25,
    "about": "A Titan dig site trapped in time above Whelgar's Excavation.",
    "entrance": {"zone": "Wetlands", "x": 47.7, "y": 56.2,
                 "detail": "Blue mist portal under a large tree, in the hills southeast of Whelgar's Excavation"},
    "quests": [
        {"id": 98815, "title": "Highland Hides", "level": 28, "minLevel": 24, "xp": 1150, "giver": "James Halloran",
         "where": "Wetlands 8.0, 55.0", "turnIn": "James Halloran, Menethil Harbor, Wetlands",
         "objective": "Collect 4 Thicket Raptor Hides and take them to James Halloran in Menethil Harbor.",
         "whereToGet": ["Collect 4 Thicket Raptor Hides: inside Excavation Site: Wetlands (no place on a zone map)"],
         "state": "can be taken now"},
        {"id": 95646, "title": "Horrors in the Highland", "level": 31, "minLevel": 24, "xp": 6150,
         "giver": "Rethiel the Greenwarden", "where": "Wetlands 56.2, 40.6", "turnIn": "Rethiel the Greenwarden, Wetlands",
         "objective": "Kill a Highland Horror within Excavation Sites and bring its root core to Rethiel.",
         "whereToGet": ["Kill a Highland Horror: inside Excavation Site: Wetlands (no place on a zone map)"],
         "state": "can be taken now"},
        {"id": 95772, "title": "Songblade Search", "level": 31, "minLevel": 24, "xp": 6150, "giver": "Dorin Songblade",
         "where": "Redridge Mountains 25.6, 46.6", "turnIn": "Daewyn Songblade, inside Excavation Site: Wetlands",
         "turnedInside": True, "state": "can be taken now",
         "whereToGet": ["Look for Daewyn: inside Excavation Site: Wetlands (no place on a zone map)"]},
        {"id": 95809, "title": "Heartwoven", "level": 31, "minLevel": 24, "giver": "Ardin Grassman, inside Excavation Site: Wetlands",
         "givenInside": True, "state": "needs other quests first", "needs": ["[Lost in the Thicket Things]"],
         "whereToGet": ["Take the Reed-woven Heart: inside Excavation Site: Wetlands (no place on a zone map)"]},
        {"id": 98824, "title": "Prehistoric Prism", "level": 31, "minLevel": 24, "state": "done"},
        {"id": 95700, "title": "Deep Trouble", "level": 33, "minLevel": 28, "giver": "Rethiel the Greenwarden",
         "where": "Wetlands 56.2, 40.6", "state": "opens at level 28"},
    ],
    "bosses": [{"name": "Shadetooth"}, {"name": "Guardian of the Site", "about": "A Titan construct."},
               {"name": "Grimtooth", "rare": True}],
}
DUNGEONS = {"Excavation Site: Wetlands": EXCAVATION}


def _offered():
    out = {q["id"]: q for q in MENETHIL}
    for q in EXCAVATION["quests"]:
        kept = longroute.dungeon_quest(q, EXCAVATION["name"])
        if kept:
            out[kept["id"]] = kept
    return out


RUN = {"title": "The Excavation Site", "zone": "Wetlands", "dungeon": "excavation",
       "quests": ["Highland Hides", "Horrors in the Highland", "Songblade Search"], "note": "with a group of five"}
BACK = {"title": "Back in Menethil", "zone": "Wetlands", "quests": ["Highland Hides", "Horrors in the Highland"]}


def _route(parts, snap=None, goal=28):
    snap = snap or _snapshot()
    route = longroute.new_route(goal, "Level 25 to 28", "", snap, "c", [])
    problems = []
    for raw in parts:
        part, why = longroute.make_part(raw, snap, _offered(), longroute.taken_titles(route), DUNGEONS)
        problems += why
        if part:
            route["parts"].append(part)
    return route, problems


# ------------------------------------------------------------------------ what the add-on read
def test_a_quest_of_the_dungeon_is_kept_as_zone_quests_gives_one():
    hides = longroute.dungeon_quest(EXCAVATION["quests"][0], "Excavation Site: Wetlands")
    assert hides == {"id": 98815, "title": "Highland Hides", "level": 28, "minLevel": 24, "xp": 1150,
                     "giver": "James Halloran", "where": "Wetlands 8.0, 55.0",
                     "objective": "Collect 4 Thicket Raptor Hides and take them to James Halloran in Menethil Harbor.",
                     "turnIn": "James Halloran, Menethil Harbor, Wetlands", "goals": 1,
                     "whereToGet": ["Collect 4 Thicket Raptor Hides: inside Excavation Site: Wetlands (no place on a zone map)"],
                     "dungeon": "Excavation Site: Wetlands"}
    # given inside the dungeon: no stop outside; needs other quests first: said
    heart = longroute.dungeon_quest(EXCAVATION["quests"][3], "Excavation Site: Wetlands")
    assert "where" not in heart and heart["needs"] == ["[Lost in the Thicket Things]"]
    assert longroute.dungeon_quest(EXCAVATION["quests"][2], "x")["turnedInside"] is True
    # done, in the quest log, not for this character: nothing to name
    assert longroute.dungeon_quest(EXCAVATION["quests"][4], "x") is None
    assert longroute.dungeon_quest({"title": "A", "state": "in the quest log"}, "x") is None
    assert longroute.dungeon_quest({"title": "A", "state": "not for this character"}, "x") is None
    # a place without coordinates is no place (the journal's words only)
    words = longroute.dungeon_quest({"title": "A", "giver": "Somebody", "where": "Ironforge", "objective": "Go",
                                     "state": "can be taken now"}, "Gnomeregan")
    assert "where" not in words and words["whereToGet"] == ["Go: inside Gnomeregan (no place on a zone map)"]


def test_the_model_reads_the_dungeon_with_its_entrance_quests_and_bosses():
    text = format_dungeon_plan("excavation", EXCAVATION)
    assert text.startswith("Excavation Site: Wetlands, levels 26-33 (the character is 25), in Wetlands. A Titan dig site")
    assert ("Entrance: Wetlands 47.7, 56.2 — Blue mist portal under a large tree, in the hills southeast of "
            "Whelgar's Excavation") in text
    assert ("- [Highland Hides] (level 28, opens at 24): can be taken now; given by James Halloran (Wetlands 8.0, 55.0); "
            "turned in to James Halloran, Menethil Harbor, Wetlands; Collect 4 Thicket Raptor Hides") in text
    assert "turned in to Daewyn Songblade, inside Excavation Site: Wetlands (inside: no turn-in part needed)" in text
    assert ("- [Heartwoven] (level 31, opens at 24): needs other quests first; given by Ardin Grassman, inside "
            "Excavation Site: Wetlands inside the dungeon; needs [Lost in the Thicket Things] first") in text
    assert "- [Deep Trouble] (level 33, opens at 28): opens at level 28" in text
    assert "Bosses: Shadetooth; Guardian of the Site: A Titan construct.; Grimtooth (rare)" in text
    assert 'a part with dungeon="Excavation Site: Wetlands"' in text and "/lfg when the addons section lists LFGForever" in text
    # the list for the level, a missing journal, and no answer
    listed = format_dungeon_plan("", {"level": 25, "dungeons": [
        {"name": "Excavation Site: Wetlands", "level": "26-33", "location": "Wetlands", "quests": 5, "open": 3,
         "entrance": "Wetlands 47.7, 56.2"}]})
    assert ("- Excavation Site: Wetlands (levels 26-33, Wetlands): 5 quests for this character, 3 of them open now; "
            "entrance Wetlands 47.7, 56.2") in listed
    assert "not installed" in format_dungeon_plan("x", {"error": "the DungeonJournal add-on (ForeverDungeonJournal) is not installed or not loaded"})
    assert "did not respond" in format_dungeon_plan("x", None)
    # no entrance on a zone map: no dungeon part
    assert "it cannot be a part of a long route" in format_dungeon_plan("x", dict(EXCAVATION, entrance=None))


def test_the_tools_name_the_dungeon_of_a_part():
    spec = {t["function"]["name"]: t["function"] for t in TOOLS}
    assert spec["dungeon_plan"]["parameters"]["properties"]["dungeon"]["type"] == "string"
    part = spec["long_route"]["parameters"]["properties"]["parts"]["items"]["properties"]
    assert part["dungeon"]["type"] == "string" and "dungeon_plan" in spec["long_route"]["description"]


# ------------------------------------------------------------------------ the part
def test_a_dungeon_part_ends_at_the_entrance_it_was_read_with():
    route, problems = _route([{"zone": "Wetlands", "quests": ["Claws from the Deep"]}, RUN, BACK])
    assert problems == []
    run = route["parts"][1]
    assert run["dungeon"] == {"name": "Excavation Site: Wetlands", "zone": "Wetlands", "x": 47.7, "y": 56.2,
                              "detail": "Blue mist portal under a large tree, in the hills southeast of Whelgar's Excavation",
                              "min": 26, "max": 33}
    assert [q["title"] for q in run["quests"]] == ["Highland Hides", "Horrors in the Highland", "Songblade Search"]
    # the part after it names the same quests for their turn-ins: an earlier dungeon part does not hold them
    assert [q["title"] for q in route["parts"][2]["quests"]] == ["Highland Hides", "Horrors in the Highland"]
    # a dungeon that was not read (or has no entrance on a map) is no part, and the model is told what to do
    part, why = longroute.make_part(dict(RUN, dungeon="Gnomeregan"), _snapshot(), _offered(), set(), DUNGEONS)
    assert part is None and "read the dungeon with dungeon_plan first" in why[0]
    part, why = longroute.make_part(RUN, _snapshot(), _offered(), set(), {"Excavation Site: Wetlands": dict(EXCAVATION, entrance=None)})
    assert part is None
    # the entrance in another zone than where the part starts: that zone comes on the way, last
    far = longroute.make_part(dict(RUN, zone="Arathi Highlands"), _snapshot(), _offered(), set(), DUNGEONS)[0]
    assert far["then"] == ["Wetlands"]
    # a dungeon part with no quest is a run for its creatures
    assert longroute.make_part(dict(RUN, quests=[]), _snapshot(), _offered(), set(), DUNGEONS)[0]["quests"] == []


def test_the_level_counts_a_run_in_a_group_and_its_quests_at_their_turn_in():
    route, _ = _route([RUN, BACK])
    est = longroute.estimate(route, _snapshot())
    run, back = est["parts"]
    # the creatures of a run: about 100 elites at twice a creature's XP, shared by five with the group bonus
    assert longroute.run_xp(25, 26, 33) == int(100 * (45 + 5 * 25) * (1 + 0.05 * 4) * 2 * 1.4 / 5)
    assert run["kills"] == longroute.run_xp(25, 26, 33) and run["run"] is True and run["after"] == 1
    # its quests give their XP where they are turned in: Songblade Search inside the dungeon, the others in part 2
    assert run["quests"] == 6150 and back["quests"] == 1150 + 6150
    assert run["hours"] == longroute.RUN_HOURS and run["left"] == 3
    assert est["warnings"] == [] or all("turned in after the dungeon" not in w for w in est["warnings"])
    # no part turns them in: said, and their XP is not counted
    alone, _ = _route([RUN])
    est = longroute.estimate(alone, _snapshot())
    assert any("turned in after the dungeon: add a part with their turn-ins" in w for w in est["warnings"])
    assert est["parts"][0]["quests"] == 6150 and est["parts"][0]["after"] is None
    # a run gives less as the character outgrows the dungeon (the dungeon's middle no longer above them)
    assert longroute.run_xp(30, 26, 33) < longroute.run_xp(30, 33, 40)


def test_the_model_and_the_add_on_see_the_dungeon_of_the_part():
    route, _ = _route([RUN, BACK])
    route["mapped"] = 0
    text = longroute.render(route, _snapshot())
    assert ("1. The Excavation Site (the dungeon Excavation Site: Wetlands, levels 26-33, a group of five; its entrance "
            "in Wetlands 47.7, 56.2) [on the map now]: level 25.0 ->") in text
    assert ("3 quests picked up on the way and done inside (its quests are turned in in part 2), about "
            f"{longroute.run_xp(25, 26, 33):,} XP from the creatures of a run, about 1 h 30 min of play") in text
    card = longroute.summary(route, _snapshot())
    assert card["parts"][0]["dungeon"] == "Excavation Site: Wetlands" and card["parts"][0]["mins"] == 90
    assert "dungeon" not in card["parts"][1]


def test_the_part_on_the_map_picks_the_quests_up_and_ends_at_the_entrance():
    route, _ = _route([RUN, BACK])
    args = longroute.part_args(route, 0, _snapshot())
    assert args["new_quests"] == ["Highland Hides", "Horrors in the Highland", "Songblade Search"]
    assert args["extra"][-1] == {"title": "Excavation Site: Wetlands entrance", "zone": "Wetlands", "x": 47.7,
                                 "y": 56.2, "when": "last",
                                 "note": "Blue mist portal under a large tree, in the hills southeast of Whelgar's Excavation",
                                 "tasks": [{"kind": "dungeon", "zone": "Excavation Site: Wetlands"}]}
    # the planner: each picked up at its giver (none of them turned in yet), the entrance last, with its task
    snap = _snapshot(nearbyQuests=args["offered"])
    res = plan_route(snap, new_quests=args["new_quests"], then=args.get("then"), extra=args["extra"])
    titles = [s["title"] for s in res["stops"]]
    assert titles[-1] == "Excavation Site: Wetlands entrance", titles
    assert res["stops"][-1]["tasks"] == [{"kind": "dungeon", "zone": "Excavation Site: Wetlands"}]
    kinds = {(t["kind"], t.get("quest")) for s in res["stops"] for t in s["tasks"]}
    assert ("pick_up", "Highland Hides") in kinds and ("pick_up", "Horrors in the Highland") in kinds
    assert not any(k == "turn_in" for k, _ in kinds)
    assert any("[Highland Hides]: done inside Excavation Site: Wetlands, with a group" in line
               for line in res.get("left_out") or []), res.get("left_out")
    # the part after the dungeon: its quests are in the quest log by then, and planned from there
    in_log = [{"id": 98815, "title": "Highland Hides", "level": 28, "status": "READY to turn in",
               "turnIn": "James Halloran (Wetlands 8.0, 55.0)"}]
    back = longroute.part_args(route, 1, _snapshot(quests=in_log))
    assert back.get("new_quests") == ["Horrors in the Highland"]


# ------------------------------------------------------------------------ through the app
def _dungeon_game(asked):
    zones = _game(asked)

    def call(ctx, op, args, timeout=45.0):
        if op == "dungeon_plan":
            asked.append((op, dict(args)))
            if not args.get("dungeon"):
                return {"level": 25, "dungeons": [{"name": EXCAVATION["name"], "level": "26-33", "quests": 5, "open": 3}]}
            return EXCAVATION if "excavation" in str(args["dungeon"]).lower() else {"error": "no such dungeon"}
        return zones(ctx, op, args, timeout)
    return call


BUILD = [ToolCall("t1", "dungeon_plan", '{"dungeon": "Excavation Site"}'),
         ToolCall("t2", "long_route", json.dumps({"op": "new", "goal_level": 28, "title": "Level 25 to 28"})),
         ToolCall("t3", "long_route", json.dumps({"op": "add", "parts": [RUN, BACK]}))]


def _builder(results):
    from test_service import FakeClient

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            done = [m for m in messages if m["role"] == "tool"]
            if "Player question: A route with the Excavation Site" in str(messages[-1]["content"]) or done:
                if len(done) < len(BUILD):
                    yield "tool_calls", [BUILD[len(done)]]
                    return
                results[:] = [m["content"] for m in done]
            yield "text", "The route is ready."
    return Client


def _built(env):
    store, addons, tmp = env
    results, asked = [], []
    svc = Service(store, None, addons, client_factory=_builder(results), lookahead=4)
    svc.game_call = _dungeon_game(asked)
    d = _ask(svc, addons, 1, "A route with the Excavation Site", _snapshot())
    return svc, addons, tmp, d, results, asked


INSIDE = "[Quest log changed] the stops of part 1 of the long route are done. Put the next part on the map."
OUT = ("[Quest log changed] the player came out of Excavation Site: Wetlands, the dungeon of part 1 of the long route. "
       "Put the next part on the map.")


def test_a_long_route_with_a_dungeon_part_through_the_app(env):
    svc, addons, tmp, d, results, asked = _built(env)
    assert ("dungeon_plan", {"dungeon": "Excavation Site"}) in asked
    assert "Entrance: Wetlands 47.7, 56.2" in results[0]
    built = results[2]
    assert "1. The Excavation Site (the dungeon Excavation Site: Wetlands, levels 26-33" in built
    assert "2. Back in Menethil (Wetlands): level" in built
    # part 1 on the map: the givers, and the entrance last, with its task for the add-on
    marks = d["marks"]
    assert marks[-1]["title"].endswith("Excavation Site: Wetlands entrance")
    assert marks[-1]["tasks"] == [{"kind": "dungeon", "zone": "Excavation Site: Wetlands"}]
    assert d["longRoute"]["parts"][0]["dungeon"] == "Excavation Site: Wetlands"
    kept = json.loads((tmp / "data" / "long_routes.json").read_text())["Magik-Realm"]
    assert kept["parts"][0]["dungeon"]["x"] == 47.7 and kept["mapped"] == 0

    # inside: the stops of the part are done, and an update does not put the next part on the map
    def inside(qid, title, what, turn_in):
        return {"id": qid, "title": title, "level": 28, "status": "in progress", "objectives": f"[TO DO] 0/1 {what}",
                "whereToGet": [f"{what}: dropped by Thicket Lurker — inside Excavation Site: Wetlands (no place on a zone map)"],
                "turnIn": turn_in}
    in_log = [inside(98815, "Highland Hides", "Thicket Raptor Hide", "James Halloran (Wetlands 8.0, 55.0)"),
              inside(95646, "Horrors in the Highland", "Root Core", "Rethiel the Greenwarden (Wetlands 56.2, 40.6)"),
              inside(95772, "Songblade Search", "Daewyn found", "Daewyn Songblade")]
    done_marks = [f"{m['title']} — Wetlands {m['x']}, {m['y']} (done)" for m in marks]
    snap = _snapshot(quests=in_log, marks=done_marks, longRoute={"id": kept["id"], "part": 1, "inside": True})
    d = _ask(svc, addons, 5, INSIDE, snap, auto=True, news="Next part of the long route", fast=True)
    assert d["text"] == "Route checked: nothing to change." and not d.get("longRoute")
    kept = json.loads((tmp / "data" / "long_routes.json").read_text())["Magik-Realm"]
    assert kept["current"] == 0 and kept["mapped"] == 0 and not kept["parts"][0].get("done")

    # out of the dungeon: the next part goes on the map, with the turn-ins
    in_log[0].update(status="READY to turn in", objectives="[DONE] 1/1 Thicket Raptor Hide")
    in_log[1].update(status="READY to turn in", objectives="[DONE] 1/1 Root Core")
    snap = _snapshot(quests=in_log[:2], marks=done_marks, routeDone=[95772],
                     longRoute={"id": kept["id"], "part": 1})
    d = _ask(svc, addons, 7, OUT, snap, auto=True, news="Next part of the long route", fast=True)
    assert d["text"] == "Long route: part 2 of 2, Back in Menethil."
    turn_ins = {(t["kind"], t.get("quest")) for m in d["marks"] for t in m.get("tasks") or []}
    assert {("turn_in", "Highland Hides"), ("turn_in", "Horrors in the Highland")} <= turn_ins
    assert d["longRoute"]["current"] == 2
