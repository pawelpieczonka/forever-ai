"""Quests the character cannot take, and routes that do not send the player to them.

The player's words, an evening in the Wetlands (2026-11-22): "Stoję przed Harlo Barnaby i chyba nie mogę od niego podnieść
questa..." (the game data said [Fall of Dun Modr] was there to take, the NPC did not give it, and the AI said the player
stood too far away), and "Tomtom mówi mi że mam podnieść jakieś questy, a nie mam questów do podniesienia tutaj." (the route
had taken [From the Ashes], a quest of WoW Forever's own whose Attack Plans the database has no place for, as done when
taken, and sent the player back to its NPC for the next part of the chain).

Here: the planner (objectives with no known place keep the turn-in off the route; the next part of a chain that needs
another quest too is no stop; a quest its NPC did not offer is left out), what the model reads (zone_quests,
quest_requirements), the add-on (what the database lacks is said, what a quest needs is read from Questie, an NPC's window
that lacks the quest of a stop), and a journey of the simulated player past an NPC who does not give his quest."""

import json

import pytest

from foreverai import config, longroute
from foreverai.inbox import ensure_pool
from foreverai.openrouter import ToolCall
from foreverai.route import has_goals, plan_route, refusal
from foreverai.service import Service
from foreverai.tools import format_quest_requirements, format_zone_quests

from test_service import FakeClient, _wait_final

STOUTFIST = "Captain Stoutfist (Wetlands 9.9, 57.5)"
ASHES = {"id": 98221, "title": "From the Ashes", "level": 28, "minLevel": 23, "giver": "Captain Stoutfist",
         "where": "Wetlands 9.9, 57.5", "goals": 0, "turnIn": STOUTFIST,
         "objective": "Captain Stoutfist in Menethil Keep wants you to destroy the 4 sets of Attack Plans in the "
                      "Angerfang Encampment.",
         "next": f"[Nek'rosh's Gambit] (level 31) from {STOUTFIST}", "nextId": 465}
DUN_MODR = {"id": 472, "title": "Fall of Dun Modr", "level": 25, "minLevel": 25, "giver": "Harlo Barnaby",
            "where": "Wetlands 10.8, 55.9", "goals": 0, "objective": "Search for Longbraid near the town of Dun Modr.",
            "turnIn": "Longbraid the Grim (Wetlands 49.8, 18.3)"}
LETTER = {"id": 288, "title": "The Third Fleet", "level": 22, "minLevel": 18, "giver": "First Mate Fitzsimmons",
          "where": "Wetlands 10.9, 59.6", "goals": 0, "objective": "Bring a Mug of Thunder Ale to First Mate Fitzsimmons "
          "in Menethil Harbor.", "turnIn": "First Mate Fitzsimmons (Wetlands 10.9, 59.6)"}
HARLO = {"id": 472, "title": "Fall of Dun Modr", "npc": "Harlo Barnaby", "level": 25}


def wetlands(quests=(), nearby=(), **extra):
    return {"player": {"coords": "10.5, 56.0", "zone": "Wetlands", "map": "Wetlands", "level": 25},
            "quests": ["Quest log: 3/20", *quests], "nearbyQuests": [dict(q) for q in nearby], **extra}


def tasks(route):
    return [(s["title"], t["kind"], t.get("quest")) for s in route["stops"] for t in s["tasks"]]


# ------------------------------------------------------------------------ the planner
def test_a_quest_whose_objectives_have_no_known_place_is_not_turned_in_when_it_is_taken():
    route = plan_route(wetlands(nearby=[ASHES]), new_quests=["From the Ashes"])
    # it is picked up; its turn-in and the next part of its chain wait for the quest log to say it is done
    assert tasks(route) == [("Captain Stoutfist", "pick_up", "From the Ashes")]
    assert ("[From the Ashes]: no known place for destroy the 4 sets of Attack Plans in the Angerfang Encampment"
            in route["left_out"])
    assert "Nek'rosh" not in json.dumps(route["stops"])


def test_a_quest_that_asks_only_for_its_turn_in_is_still_done_at_once():
    # a mug to take to the first mate (nothing in the database to do for it): picked up and turned in on the way
    letter = dict(LETTER, objective="Take the sealed orders to First Mate Fitzsimmons in Menethil Harbor.")
    route = plan_route(wetlands(nearby=[letter]), new_quests=["The Third Fleet"])
    assert [k for _, k, q in tasks(route) if q == "The Third Fleet"] == ["pick_up", "turn_in"]


@pytest.mark.parametrize("q, goals", [
    ({"objective": "Kill 10 Mottled Raptors."}, True),
    ({"objective": "Destroy the 4 sets of Attack Plans."}, True),
    ({"objective": "Bring 8 Gnoll Paws to Deputy Rainer."}, True),
    ({"objective": "Slay Nek'rosh."}, True),
    ({"objective": "Search for Longbraid near the town of Dun Modr."}, False),
    ({"objective": "Take the letter to Innkeeper Helbrek."}, False),
    ({"objective": "Speak with Marshal McBride, he is 25 yards inside the abbey."}, False),
    ({"objective": "Talk to the guard.", "goals": 2}, True),  # the database says so, whatever the text
    ({"objective": "Kill 10 Mottled Raptors.", "whereToGet": []}, True),
    ({"whereToGet": ["Scale: dropped by Raptor (level 22) — Wetlands 20.0, 50.0"]}, True),
    ({}, False),
])
def test_what_asks_for_more_than_a_turn_in(q, goals):
    assert has_goals(q) is goals


def test_an_objective_with_no_known_place_keeps_the_turn_in_off_the_route():
    two = {"id": 300, "title": "Two Things", "level": 24, "status": "in progress",
           "objectives": "0/5 Mottled Scale [TO DO]; 0/1 Bolt of Wool [TO DO]",
           "whereToGet": ["Mottled Scale: dropped by Mottled Raptor (level 22) — Wetlands 20.0, 50.0 (1200 m, 6 spawn "
                          "points in your zone)", "Bolt of Wool: no known place"],
           "turnIn": "Glorin Steelbrow (Wetlands 10.6, 60.6)"}
    route = plan_route(wetlands([two]))
    # the scales are on the route, the turn-in is not: the wool has no place the data knows
    assert tasks(route) == [("Mottled Raptor", "objective", "Two Things")]
    assert any(x.startswith("[Two Things]: no known place for Bolt of Wool; its other objectives are on the route, "
                            "its turn-in is not") for x in route["left_out"])
    # done (the quest log says so): the turn-in comes
    ready = dict(two, status="READY TO TURN IN", objectives="5/5 Mottled Scale [DONE]; 1/1 Bolt of Wool [DONE]")
    assert tasks(plan_route(wetlands([ready]))) == [("Glorin Steelbrow", "turn_in", "Two Things")]


def test_the_next_part_of_a_chain_that_needs_another_quest_too_is_no_stop():
    span = {"id": 631, "title": "The Thandol Span", "level": 30, "status": "READY TO TURN IN",
            "turnIn": "Rhag Garmason (Wetlands 51.1, 8.0)",
            "next": "[The Thandol Span] (level 30) from Rhag Garmason (Wetlands 51.1, 8.0)", "nextId": 632}
    route = plan_route(wetlands([dict(span, nextNeeds=["[Ebenezer's Report]"])]))
    assert tasks(route) == [("Rhag Garmason", "turn_in", "The Thandol Span")]
    assert any("follows [The Thandol Span], and needs [Ebenezer's Report] done first too" in x for x in route["left_out"])
    # with nothing else needed it is picked up at the turn-in, and says which quest opens it
    plain = plan_route(wetlands([span]))
    stop = plain["stops"][0]
    assert [(t["kind"], t.get("id"), t.get("after")) for t in stop["tasks"]] == [("turn_in", 631, None), ("pick_up", 632, 631)]


def test_a_quest_its_npc_did_not_offer_is_left_out_with_the_reason():
    snap = wetlands(nearby=[DUN_MODR, LETTER], notOffered=[HARLO])
    named = plan_route(snap, new_quests=["Fall of Dun Modr", "The Third Fleet"])
    assert "Harlo Barnaby" not in [s["title"] for s in named["stops"]]
    assert ("[Fall of Dun Modr]: Harlo Barnaby did not offer it when the player talked to him (at level 25)"
            in named["left_out"])
    every = plan_route(snap, new_quests=True)
    assert "Harlo Barnaby" not in [s["title"] for s in every["stops"]]
    assert [x for x in every["text"].splitlines() if "Fall of Dun Modr" in x] == [
        "- [Fall of Dun Modr]: Harlo Barnaby did not offer it when the player talked to him (at level 25)"]
    # not named, it is not even among the quests offered: there is nothing to offer
    other = plan_route(snap)
    assert "Fall of Dun Modr" not in other["text"] and "Harlo" not in other["text"]
    # by number when both have one: the parts of a chain share titles
    assert refusal(snap, {"id": 472, "title": "Fall of Dun Modr"}) and not refusal(snap, {"id": 473, "title": "Fall of Dun Modr"})
    assert refusal(snap, title="fall of dun modr") and not refusal(snap, title="The Third Fleet")
    # the next part of a chain its NPC did not offer is no stop at the turn-in either
    chain = {"id": 471, "title": "Before the Fall", "level": 24, "status": "READY TO TURN IN",
             "turnIn": "Harlo Barnaby (Wetlands 10.8, 55.9)",
             "next": "[Fall of Dun Modr] (level 25) from Harlo Barnaby (Wetlands 10.8, 55.9)", "nextId": 472}
    route = plan_route(wetlands([chain], notOffered=[HARLO]))
    assert tasks(route) == [("Harlo Barnaby", "turn_in", "Before the Fall")]
    assert any(x.startswith("[Fall of Dun Modr]: follows [Before the Fall]; Harlo Barnaby did not offer it")
               for x in route["left_out"])


def test_a_long_route_leaves_out_a_quest_its_npc_did_not_offer_and_names_what_a_chain_needs():
    offered = {472: dict(DUN_MODR), 288: dict(LETTER)}
    snap = {"player": {"level": 25, "zone": "Wetlands", "coords": "10.5, 56.0"}, "quests": [], "routeDone": [],
            "notOffered": [HARLO]}
    part, problems = longroute.make_part({"title": "Menethil Harbor", "zone": "Wetlands",
                                          "quests": ["Fall of Dun Modr", "The Third Fleet"]}, snap, offered, set())
    assert [q["title"] for q in part["quests"]] == ["The Third Fleet"]
    assert any("[Fall of Dun Modr] is left out" in p and "Harlo Barnaby did not offer it" in p for p in problems)
    log = [{"id": 631, "title": "The Thandol Span", "level": 30, "turnIn": "Rhag Garmason (Wetlands 51.1, 8.0)",
            "next": "[The Thandol Span II] (level 30) from Rhag Garmason (Wetlands 51.1, 8.0)", "nextId": 632,
            "nextNeeds": ["[Ebenezer's Report]"]}]
    part2, problems2 = longroute.make_part({"zone": "Wetlands", "quests": ["The Thandol Span II"]},
                                           dict(snap, quests=log), {}, set())
    assert [q["title"] for q in part2["quests"]] == ["The Thandol Span II"]
    assert any("[The Thandol Span II] needs [Ebenezer's Report] done first" in p for p in problems2)
    # named in an earlier part, it is in order
    _, problems3 = longroute.make_part({"zone": "Wetlands", "quests": ["The Thandol Span II"]},
                                       dict(snap, quests=log), {}, {"ebenezer's report"})
    assert not any("needs" in p for p in problems3)


# ------------------------------------------------------------------------ what the model reads
def test_zone_quests_marks_what_an_npc_did_not_offer_and_what_a_chain_needs():
    res = {"zone": "Wetlands", "level": 25, "quests": [
        DUN_MODR, dict(ASHES, nextNeeds=["[Ironbeard's Orders]"])]}
    text = format_zone_quests("Wetlands", res, [], {"notOffered": [HARLO]})
    line = next(x for x in text.splitlines() if x.startswith("- [Fall of Dun Modr]"))
    assert line.endswith("— NOT OFFERED: Harlo Barnaby did not offer it when the player talked to him (at level 25); "
                         "do not plan it")
    assert "    next: [Nek'rosh's Gambit] (level 31) from Captain Stoutfist (Wetlands 9.9, 57.5), which needs " \
           "[Ironbeard's Orders] done too" in text
    assert "NOT OFFERED" not in format_zone_quests("Wetlands", res, [], {})


def test_the_requirements_of_a_quest_in_words():
    res = {"level": 25, "quests": [
        {"id": 465, "title": "Nek'rosh's Gambit", "level": 31, "minLevel": 26, "giver": STOUTFIST,
         "turnIn": STOUTFIST, "questie": "Unavailable: Incomplete pre-quest",
         "reasons": ["it opens at level 26 (the character is 25)"],
         "before": [{"id": 98221, "title": "From the Ashes", "state": "in the quest log", "level": 28,
                     "turnIn": STOUTFIST}], "beforeKind": "one of"}]}
    text = format_quest_requirements("Nek'rosh's Gambit", res)
    assert text.startswith("[Nek'rosh's Gambit] (quest 465, level 31, opens at level 26): the character cannot take it now.")
    assert "- It opens at level 26 (the character is 25)." in text
    assert "- It needs this done first:\n    [From the Ashes] (quest 98221, level 28): in the quest log; turned in to " \
           "Captain Stoutfist (Wetlands 9.9, 57.5)" in text
    assert "- Questie's own check: Unavailable: Incomplete pre-quest." in text
    # the data says it is there to take, and the NPC did not give it: the server, not the player
    harlo = {"level": 25, "quests": [{"id": 472, "title": "Fall of Dun Modr", "level": 25, "minLevel": 25,
                                      "giver": "Harlo Barnaby (Wetlands 10.8, 55.9)", "questie": "Available",
                                      "notOffered": {"npc": "Harlo Barnaby", "level": 25}}]}
    text = format_quest_requirements("Fall of Dun Modr", harlo)
    assert "the game data says the character can take it now" in text
    assert "- Harlo Barnaby did not offer it when the player talked to him at level 25, although the game data says " \
           "the character can take it: this server does not give it to this character now" in text
    # a pre-quest still to do: who gives it, and what it needs in turn
    deep = {"quests": [{"id": 3, "title": "C", "before": [{"id": 1, "title": "A", "state": "not done", "giver": "X (Y 1.0, 2.0)",
                                                          "needs": "[Z]"}, {"id": 2, "title": "B", "state": "done"}]}]}
    text = format_quest_requirements("C", deep)
    assert "    [A] (quest 1): not done; given by X (Y 1.0, 2.0); it needs [Z] first" in text and "[B]" not in text
    assert format_quest_requirements("C", None).startswith("The game did not respond")
    assert "failed: Questie is not loaded" in format_quest_requirements("C", {"error": "Questie is not loaded"})


# ------------------------------------------------------------------------ through the app
@pytest.fixture
def env(tmp_path, monkeypatch):
    from foreverai import claude_code
    monkeypatch.setattr(claude_code, "detect", lambda force=False: None)
    monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnop\n")
    addons = tmp_path / "AddOns"
    ensure_pool(addons, size=20)
    return config.SettingsStore(tmp_path / "settings.json"), addons, tmp_path


def _asker(calls, results):
    class Client(FakeClient):
        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            done = [m for m in messages if m["role"] == "tool"]
            if len(done) < len(calls):
                yield "tool_calls", [calls[len(done)]]
                return
            results[:] = [m["content"] for m in done]
            yield "text", "Harlo does not give it here."
    return Client


def test_quest_requirements_asks_the_game_by_the_numbers_the_app_has_seen(env):
    store, addons, _ = env
    asked, results = [], []
    calls = [ToolCall("t1", "quest_requirements", '{"quest": "[Fall of Dun Modr]"}'),
             ToolCall("t2", "quest_requirements", '{"quest": "Some Quest Nobody Listed"}'),
             ToolCall("t3", "quest_requirements", '{"quest": "x", "id": 465}')]
    svc = Service(store, None, addons, client_factory=_asker(calls, results), lookahead=4)

    def game(ctx, op, args, timeout=45.0):
        asked.append((op, dict(args), timeout))
        return {"level": 25, "quests": [{"id": 472, "title": "Fall of Dun Modr", "questie": "Available",
                                         "notOffered": {"npc": "Harlo Barnaby", "level": 25}}]}
    svc.game_call = game
    snap = wetlands(nearby=[DUN_MODR], notOffered=[HARLO])
    svc.handle({"type": "question", "msgId": 1, "session": "S", "nextInbox": 1, "conv": "c",
                "text": "Stoję przed Harlo Barnaby i nie mogę wziąć questa", "char": "Magik-Realm", "snapshot": snap})
    d = _wait_final(addons, 1)
    assert d["text"].startswith("Harlo does not give it here")
    # a title the data of the question has: by its number; one it has not: by the title (the add-on looks it up)
    assert [(op, args) for op, args, _ in asked] == [("quest_requirements", {"ids": [472]}),
                                                     ("quest_requirements", {"title": "Some Quest Nobody Listed"}),
                                                     ("quest_requirements", {"ids": [465]})]
    assert all(t >= 30 for _, _, t in asked)
    assert "this server does not give it to this character now" in results[0]


def test_the_model_is_told_about_quests_an_npc_did_not_offer():
    from foreverai.prompt import SYSTEM_PROMPT
    assert "- notOffered: quests whose NPC did not offer them" in SYSTEM_PROMPT
    assert "call quest_requirements first" in SYSTEM_PROMPT and "Never guess another reason" in SYSTEM_PROMPT



def test_the_update_after_an_npc_did_not_offer_the_quest_plans_the_route_without_it(env):
    store, addons, _ = env
    calls = [ToolCall("t1", "plan_route", json.dumps({"new_quests": ["Fall of Dun Modr", "The Third Fleet"]}))]
    model = []
    svc = Service(store, None, addons, client_factory=_asker(calls, model), lookahead=4)
    svc.game_call = lambda ctx, op, args, timeout=45.0: None

    def ask(n, text, snap, **more):
        svc.handle({"type": "question", "msgId": n, "session": "S", "nextInbox": n, "conv": "c", "text": text,
                    "char": "Magik-Realm", "snapshot": snap, **more})
        return _wait_final(addons, n)
    d = ask(1, "Plan my route through Menethil", wetlands(nearby=[DUN_MODR, LETTER]))
    stops = [m["title"] for m in d["marks"]]
    assert any("Harlo Barnaby" in t for t in stops) and any("Longbraid" in t for t in stops)
    # the add-on saw Harlo's window without the quest: the planner plans the route again at once, without it
    marks = [f"{m['title']} — Wetlands {m['x']}, {m['y']}" for m in d["marks"]]
    d = ask(2, "[Quest log changed] Harlo Barnaby does not offer [Fall of Dun Modr]. Update the route on the map.",
            wetlands(nearby=[DUN_MODR, LETTER], notOffered=[HARLO], marks=marks),
            auto=True, news="Updating the route", fast=True)
    after = [m["title"] for m in d["marks"]]
    assert after and not any("Harlo" in t or "Longbraid" in t for t in after)
    assert any("Fitzsimmons" in t for t in after)
    assert "Harlo does not give it here" not in d["text"]  # (the planner answered: no model for the update)
