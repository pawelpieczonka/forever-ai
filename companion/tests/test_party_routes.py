"""A route for the party (party.py, plan_route with party; see test_addon_party.py for the add-on): the teammate's
quests the player can take come on the route, the planner's result says member by member what both have, what to share
and who picks up what, and the add-on is told to send the route to the party. ("Weź pod uwagę mojego teammate'a, aby
route był dopasowany": the AI planned for a duo without seeing his quest log.)"""

import json

from foreverai import party
from foreverai.openrouter import ToolCall
from foreverai.service import Service
from foreverai.tools import TOOLS

from test_service import FakeClient, _wait_final, env  # noqa: F401 (env: the fixture)

ROSSMANN = {"name": "Rossmann", "level": 27, "class": "Hunter", "meters": 30, "questie": True,
            "both": [{"title": "WANTED: Murkdeep!", "progress": "0/1"}],
            "theirs": [{"id": 1275, "title": "Researching the Corruption", "level": 24, "minLevel": 18, "canTake": True,
                        "done": True, "giver": "Gershala Nightwhisper", "where": "Darkshore 38.3, 43.1"},
                       {"id": 7011, "title": "The Absent Minded Prospector", "level": 24, "canTake": False,
                        "why": "needs [Bashal'Aran] first", "giver": "Thundris Windweaver", "where": "Darkshore 37.4, 40.1"},
                       {"id": 7010, "title": "Beached Sea Creature", "level": 30, "canTake": False, "why": "opens at level 26"},
                       {"id": 9999, "title": "A Quest of Forever's Own"}],
            "lacks": [{"title": "Encrusted Tail Fins", "share": True}, {"title": "The Tower of Althalaxx", "share": False},
                      {"title": "Plagued Lands", "share": True, "opensAt": 28}]}

SNAPSHOT = {
    "player": {"coords": "37.1, 44.1", "map": "Darkshore", "zone": "Darkshore", "level": 24},
    "quests": [
        {"id": 7001, "title": "WANTED: Murkdeep!", "level": 23, "status": "in progress",
         "objectives": "[TO DO] 0/1 Murkdeep slain", "turnIn": "Sentinel Glynda Nal'Shea (Darkshore 37.7, 43.4)",
         "whereToGet": ["Murkdeep slain: kill Murkdeep (level 23) — Darkshore 36.6, 76.6 (3600 m, 1 spawn point in your zone)"]},
        {"id": 7006, "title": "The Tower of Althalaxx", "level": 31, "status": "in progress",
         "objectives": "[TO DO] 0/8 Dark Strand Fanatic slain", "turnIn": "Thundris Windweaver (Darkshore 37.4, 40.1)",
         "whereToGet": ["Dark Strand Fanatic slain: kill Dark Strand Fanatic (level 30) — Darkshore 55.0, 26.0 "
                        "(2600 m, 9 spawn points in your zone)"]}],
    "nearbyQuests": [{"id": 1275, "title": "Researching the Corruption", "level": 24, "minLevel": 18,
                      "giver": "Gershala Nightwhisper", "where": "Darkshore 38.3, 43.1",
                      "turnIn": "Gershala Nightwhisper (Darkshore 38.3, 43.1)",
                      "whereToGet": ["Corrupted Brain Stem: kill Blackfathom Myrmidon (level 24) — Darkshore 30.0, 80.0 "
                                     "(4000 m, 6 spawn points in your zone)"]}],
    "party": {"members": [ROSSMANN]},
}


def test_the_teammates_quests_the_player_can_take_go_on_the_route():
    assert party.members(SNAPSHOT) == [ROSSMANN]
    assert party.takeable(party.members(SNAPSHOT)) == ["Researching the Corruption"]
    assert party.members({}) == [] and party.members({"party": {"members": [{"level": 3}]}}) == []


def test_the_result_says_member_by_member_what_to_share_and_who_picks_up_what():
    route = {"stops": [{"tasks": [{"kind": "pick_up", "quest": "Researching the Corruption"}]},
                       {"tasks": [{"kind": "objective", "quest": "The Tower of Althalaxx"}]},
                       {"tasks": [{"kind": "objective", "quest": "WANTED: Murkdeep!"}]}]}
    text = party.together(SNAPSHOT, route)
    assert ("- Rossmann (level 27 Hunter, 30 m away; their quest log from Questie):\n"
            "  both have: [WANTED: Murkdeep!] (they: 0/1)\n"
            "  theirs, on the route for the player too (they can share it, else the player picks it up at the giver): "
            "[Researching the Corruption] (they have it done)\n"
            "  theirs the player cannot take: [The Absent Minded Prospector] (needs [Bashal'Aran] first), "
            "[Beached Sea Creature] (opens at level 26)\n"
            "  theirs the quest data does not know: [A Quest of Forever's Own]\n"
            "  the player's quests they lack: [Encrusted Tail Fins] can be shared (Share Quest in the player's quest "
            "log, while they are near); [The Tower of Althalaxx] cannot be shared: they pick it up at its giver "
            "themselves; [Plagued Lands] (opens at level 28 for them)") in text
    # a quest of the route three or more levels above a member is red for them
    assert "  red for them (3 or more levels above them): [The Tower of Althalaxx] (31)" in text
    # one the planner left out of the route is still named, with its giver
    off = party.together(SNAPSHOT, {"stops": []})
    assert ("theirs the player can take, not on this route (another zone, or no place known): "
            "[Researching the Corruption] from Gershala Nightwhisper (Darkshore 38.3, 43.1)") in off
    # far away, in another zone, offline; without Questie in their game
    far = dict(ROSSMANN, meters=600)
    assert "600 m away: wait for them" in party.together({"party": {"members": [far]}}, route)
    gone = {k: v for k, v in ROSSMANN.items() if k != "meters"}
    assert "in Ashenvale: wait for them, or meet on the way" in party.together(
        {"party": {"members": [dict(gone, zone="Ashenvale")]}}, route)
    assert "offline" in party.together({"party": {"members": [dict(ROSSMANN, offline=True)]}}, route)
    blind = {"name": "Mira", "level": 22, "class": "Mage", "meters": 10, "questie": False,
             "both": [{"title": "WANTED: Murkdeep!"}]}
    assert ("- Mira (level 22 Mage, 10 m away). Questie in their game sent nothing, so their quest log is not known; of "
            "the player's quests they have [WANTED: Murkdeep!].") in party.together({"party": {"members": [blind]}}, route)
    assert "No party" in party.together({}, route)


def test_the_tool_takes_party():
    spec = next(t["function"] for t in TOOLS if t["function"]["name"] == "plan_route")
    assert spec["parameters"]["properties"]["party"]["type"] == "boolean"


def _ask(env, args: dict, snapshot: dict, n: int = 3):
    svc, addons, _ = env

    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not any(m["role"] == "tool" for m in messages):
                yield "tool_calls", [ToolCall("c1", "plan_route", json.dumps(args))]
                return
            Client.result = messages[-1]["content"]
            yield "text", "Here is the route for both of you."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c",
                "text": "weź pod uwagę mojego teammate'a", "snapshot": snapshot})
    return _wait_final(addons, n), Client.result


def test_a_route_for_the_party_through_the_app(env):
    d, result = _ask(env, {"party": True, "skip": ["The Tower of Althalaxx"]}, SNAPSHOT)
    # his quest the player can take: picked up at Gershala (he can share it instead)
    tasks = {(t["kind"], t.get("quest")) for m in d["marks"] for t in m.get("tasks") or []}
    assert ("pick_up", "Researching the Corruption") in tasks and ("objective", "WANTED: Murkdeep!") in tasks
    assert "Together with the party" in result and "- Rossmann (level 27 Hunter, 30 m away" in result
    assert "theirs, on the route for the player too" in result and "[Researching the Corruption]" in result
    # the add-on sends the route to the party (their ForeverAI asks them to follow it)
    assert d["shareRoute"] is True and d["routePlanned"] is True

    # the player's own route: not for the party, not sent to it
    d, result = _ask(env, {"skip": ["The Tower of Althalaxx"]}, SNAPSHOT, n=5)
    assert "shareRoute" not in d and "Together with the party" not in result
    assert ("pick_up", "Researching the Corruption") not in {(t["kind"], t.get("quest")) for m in d["marks"]
                                                             for t in m.get("tasks") or []}
    # party asked, nobody in the party section: the player's own route, said so
    alone = {k: v for k, v in SNAPSHOT.items() if k != "party"}
    d, result = _ask(env, {"party": True, "skip": ["The Tower of Althalaxx"]}, alone, n=7)
    assert "shareRoute" not in d and "No party: the party section lists nobody" in result
    # a quest of his the player leaves out stays out
    d, result = _ask(env, {"party": True, "skip": ["The Tower of Althalaxx", "Researching the Corruption"]}, SNAPSHOT, n=9)
    assert ("pick_up", "Researching the Corruption") not in {(t["kind"], t.get("quest")) for m in d["marks"]
                                                             for t in m.get("tasks") or []}
