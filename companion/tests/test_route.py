"""plan_route: the stops of the quest log in the shortest walking order, each turn-in after its objectives."""

import json
import re
from pathlib import Path

from foreverai.route import plan_route

BASE = json.loads((Path(__file__).resolve().parent / "base_snapshot.json").read_text(encoding="utf-8"))


def quest(qid, title, status="in progress", turn_in=None, where=None, **extra):
    return {"id": qid, "title": title, "level": 10, "status": status, "turnIn": turn_in, "whereToGet": where, **extra}


def snap(quests, coords="50.0, 50.0", **extra):
    return {"player": {"coords": coords, "zone": "Teldrassil", "map": "Teldrassil", "level": 10},
            "quests": ["Quest log: 3/40"] + quests, **extra}


def titles(route):
    return [s["title"] for s in route["stops"]]


def test_shortest_order_with_the_turn_in_after_its_objective():
    s = snap([
        quest(1, "Far Hunt", turn_in="Giver (Teldrassil 50.0, 60.0)",
              where=["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]),
        quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)"),
    ])
    route = plan_route(s)
    # the giver stands nearest to the player, but the boars come first; the postman is on the way to them
    assert titles(route) == ["Postman", "Boar", "Giver"]
    assert [s["what"] for s in route["stops"]] == ["turn in [Letter]", "[Far Hunt]: kill Boar (level 5)", "turn in [Far Hunt]"]
    assert [s["meters"] for s in route["stops"]] == [1200, 1200, 2530]  # 80 m per map unit, from the game's own distance
    assert route["meters"] == 4930


def test_what_is_not_a_place_to_walk_to_is_left_out_with_the_reason():
    s = snap([
        quest(1, "City Errand", status="READY TO TURN IN", turn_in="Sister (Darnassus 28.9, 45.8)"),
        quest(2, "Camping 101: Skinning", turn_in="Radnaal (Teldrassil 42.1, 50.0)", objectives="Raise your skinning skill to 20 [TO DO]",
              where=["Radnaal: kill Radnaal (level 19) — Teldrassil 42.1, 50.0 (600 m, 1 spawn point in your zone)"]),
        quest(3, "Lost", where=["Thing: from object Chest — no known spawn point"], turn_in="Giver (Teldrassil 51.0, 50.0)"),
        quest(4, "Broken", status="FAILED (time ran out or a condition was broken) — abandon and pick it up again",
              turn_in="Giver (Teldrassil 51.0, 50.0)"),
    ])
    route = plan_route(s)
    assert route["stops"] == []
    assert route["left_out"] == [
        "[City Errand]: turn in to Sister in Darnassus (28.9, 45.8), another zone, which the app knows the way to",
        "[Camping 101: Skinning]: needs a skill, not a place (Raise your skinning skill to 20)",
        "[Lost]: no known place for Thing",
        "[Broken]: failed, abandon it and pick it up again",
    ]


def test_route_of_a_real_quest_log():
    route = plan_route(BASE)
    order = titles(route)
    # eight, and two quests offered next to stops; Sentinel Arynia stands at the moonwell, one stop with it
    assert order[0] == "Strigid Screecher" and len(order) == 9
    well = next(st for st in route["stops"] if st["title"] == "Oracle Glade Moonwell")
    assert ("pick_up", "The Enchanted Glade", "Sentinel Arynia Cloudsbreak") in [(t["kind"], t["quest"], t.get("at")) for t in well["tasks"]]
    assert order.index("Dazalar") > order.index("Strigid Screecher")
    assert order.index("Corithras Moonrage") > order.index("Oracle Glade Moonwell")
    assert order.index("Tallonkai Swiftroot") > max(order.index("Befouled Webwood"), order.index("Xethorr the Wicked"))
    assert "Sentinel Eralya Leafshadow" in order
    assert any("Darnassus" in x and "another zone" in x for x in route["left_out"])
    assert sum("needs a skill" in x for x in route["left_out"]) == 2
    assert "elite" in next(s for s in route["stops"] if s["title"] == "Xethorr the Wicked")["note"]
    # no order of these stops is shorter: here the far west (Oracle Glade) is one trip, not two
    assert abs(order.index("Sentinel Eralya Leafshadow") - order.index("Oracle Glade Moonwell")) <= 2
    text = route["text"]
    assert text.startswith("Route from 64.1, 68.5 (Teldrassil): 9 stops, about ") and "1. Strigid Screecher (64.2, 69.2)" in text
    assert "Not on the route:" in text


def test_new_quests_only_when_asked():
    s = snap([quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 60.0, 50.0)")],
             nearbyQuests=[{"id": 9, "title": "Fresh", "giver": "Elder", "level": 9, "minLevel": 5,
                            "where": "Teldrassil 55.0, 50.0", "distance": "400 m"},
                           {"id": 10, "title": "Elsewhere", "giver": "Dwarf", "level": 9, "where": "Dun Morogh 30.0, 30.0"}])
    assert titles(plan_route(s)) == ["Postman"]  # 400 m off the way: the model's choice
    route = plan_route(s, new_quests=True)
    assert titles(route) == ["Elder", "Postman"] and route["stops"][0]["what"] == "pick up [Fresh]"


def test_nothing_to_route():
    route = plan_route(snap([]))
    assert route["stops"] == [] and route["text"] == "Nothing in the quest log has a place to walk to in Teldrassil."


def npc(name, x, y, meters):
    return f"{name} <Vendor> [vendor] — Teldrassil {x:.1f}, {y:.1f} ({meters} m)"


def test_a_map_is_not_square():
    """In Teldrassil one map unit is 46.6 m east-west and 31.0 m north-south: the nearer stop by the numbers on the map
    is not the nearer one on foot."""
    s = snap([quest(1, "East", status="READY TO TURN IN", turn_in="Eastman (Teldrassil 60.0, 50.0)"),
              quest(2, "North", status="READY TO TURN IN", turn_in="Northman (Teldrassil 50.0, 36.0)")],
             zoneNPCs=[npc("A", 60, 50, 400), npc("B", 50, 40, 200), npc("C", 60, 40, 447), npc("D", 40, 60, 447)])
    route = plan_route(s)
    assert titles(route) == ["Northman", "Eastman"]  # 280 m against 400 m, although 14 units against 10
    assert [x["meters"] for x in route["stops"]] == [280, 488]


def test_an_unfinished_quest_without_a_known_place_is_not_turned_in():
    s = snap([quest(1, "Explore", turn_in="Giver (Teldrassil 51.0, 50.0)", objectives="Explore the cave [TO DO]")])
    route = plan_route(s)
    assert route["stops"] == []
    assert route["left_out"] == ["[Explore]: no known place for what is left to do (Explore the cave)"]


def test_the_model_chooses_what_is_on_the_route():
    s = snap([quest(1, "Far Hunt", turn_in="Giver (Teldrassil 50.0, 60.0)", level=12, type="Elite",
                    where=["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]),
              quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)", level=7),
              quest(3, "Parcel", status="READY TO TURN IN", turn_in="Clerk (Teldrassil 52.0, 50.0)", level=9)],
             nearbyQuests=[{"id": 9, "title": "Fresh", "giver": "Elder", "level": 9, "where": "Teldrassil 55.0, 50.0"},
                           {"id": 8, "title": "Ursal the Mauler", "giver": "Athridas", "level": 12,
                            "where": "Teldrassil 56.0, 50.0"}])
    route = plan_route(s, skip=["far hunt"], new_quests=["Fresh"])
    assert titles(route) == ["Clerk", "Elder", "Postman"]  # no boars, no Ursal
    assert route["left_out"] == ["[Far Hunt]: left out as asked"]
    # hard quests last: everything else first, then the boars and their turn-in
    late = plan_route(s, last=["Far Hunt"])
    assert titles(late) == ["Clerk", "Postman", "Boar", "Giver"]
    assert [x["note"] for x in late["stops"]] == ["quest level 9", "quest level 7", "quest level 12, elite",
                                                  "quest level 12, elite"]
    assert "3. Boar (80, 50), +1200 m: [Far Hunt]: kill Boar (level 5) — quest level 12, elite" in late["text"]


def test_the_route_ends_where_the_player_goes_next():
    s = snap([quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 70.0, 50.0)"),
              quest(3, "Parcel", status="READY TO TURN IN", turn_in="Clerk (Teldrassil 40.0, 50.0)"),
              quest(4, "Hunt", turn_in="Giver (Teldrassil 80.0, 50.0)",
                    where=["Boar: kill Boar (level 5) — Teldrassil 80.0, 55.0 (2400 m, 9 spawn points in your zone)"])])
    assert titles(plan_route(s)) == ["Clerk", "Postman", "Boar", "Giver"]  # nearest first when nothing follows
    west = plan_route(s, end=(30.0, 50.0), end_title="the road to Darnassus")
    assert titles(west) == ["Boar", "Giver", "Postman", "Clerk"]  # the far side first, finishing in the west
    assert west["text"].splitlines()[0].endswith("ending near the road to Darnassus.")


def test_one_stop_for_one_place():
    """Two quests turned in to the same NPC, two quests' creatures at the same spawn: one stop each, with all of it,
    and the turn-in after the objectives of both quests."""
    boar = "Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"
    tusk = "Boar Tusk: dropped by Boar (level 5) — Teldrassil 80.2, 50.1 (2410 m, 9 spawn points in your zone)"
    s = snap([
        quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)"),
        quest(2, "Parcel", turn_in="Postman (Teldrassil 65.0, 50.0)", where=[boar]),
        quest(3, "Tusks", turn_in="Giver (Teldrassil 50.0, 60.0)", where=[tusk]),
    ])
    route = plan_route(s)
    assert titles(route) == ["Boar", "Postman", "Giver"]
    assert [s["what"] for s in route["stops"]] == ["[Parcel]: kill Boar (level 5); [Tusks]: dropped by Boar (level 5)",
                                                   "turn in [Letter]; turn in [Parcel]", "turn in [Tusks]"]
    assert route["text"].startswith("Route from 50, 50 (Teldrassil): 3 stops")


def test_every_stop_says_what_is_to_be_done_there():
    """The add-on takes a stop off the route when that is done, not when the player reaches the place."""
    boar = "Boar Tusk: dropped by Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)"),
              quest(2, "Parcel", turn_in="Postman (Teldrassil 65.0, 50.0)", where=[boar])],
             nearbyQuests=[{"title": "Fresh", "giver": "Newcomer", "where": "Teldrassil 55.0, 50.0", "level": 9}])
    route = plan_route(s, new_quests=True)
    assert {st["title"]: st["tasks"] for st in route["stops"]} == {
        "Newcomer": [{"kind": "pick_up", "quest": "Fresh"}],
        "Boar": [{"kind": "objective", "quest": "Parcel", "id": 2, "label": "Boar Tusk"}],
        "Postman": [{"kind": "turn_in", "quest": "Letter", "id": 1}, {"kind": "turn_in", "quest": "Parcel", "id": 2}]}


# The real game: the player stood in Rut'theran Village with five quests to turn in in Darnassus. The planner knew
# one zone only, so the model set the Darnassus stops by hand, with no way into the city ("the first step is 6 km
# away?"), and the portal it added later ended up as the last stop.
def darnassus():
    return snap([
        quest(1, "The Sisterhood of Elune", status="READY TO TURN IN", turn_in="Sister Aquinne (Darnassus 28.9, 45.8)"),
        quest(2, "Rellian Greenspyre", status="READY TO TURN IN", turn_in="Rellian Greenspyre (Darnassus 38.2, 21.6)"),
        quest(3, "Crown of the Earth", status="READY TO TURN IN", turn_in="Arch Druid Fandral Staghelm (Darnassus 34.8, 9.3)"),
        quest(4, "Teldrassil", status="READY TO TURN IN", turn_in="Arch Druid Fandral Staghelm (Darnassus 34.8, 9.3)"),
        quest(5, "Training the Beast", status="READY TO TURN IN", turn_in="Jocaste (Darnassus 40.4, 8.6)"),
        quest(6, "Nessa's Fish", status="READY TO TURN IN", turn_in="Nessa Shadowsong (Teldrassil 56.3, 92.4)"),
        quest(7, "Flight to Auberdine", status="READY TO TURN IN", turn_in="Laird (Darkshore 36.8, 44.3)"),
    ], coords="58.4, 94.0", nearbyQuests=[{"title": "Eyes of the Sentinels", "giver": "Sentinel Dalia", "where": "Darnassus 39.6, 89.5"}])


def test_without_the_way_into_the_next_zone_the_result_says_how_to_ask_for_it():
    route = plan_route(darnassus())
    assert titles(route) == ["Nessa Shadowsong"]
    # the app knows this way itself: the zone is all the model has to name
    assert ('Stops wait in Darnassus (5). The way there is known (Portal to Darnassus): call plan_route again with '
            'then=[{"zone": "Darnassus"}]') in route["text"]
    assert "Do not set stops of another zone by hand" in route["text"]
    assert ("Rut'theran Village has no ground way to the rest of Teldrassil: it counts as a zone of its own here, and what "
            "lies elsewhere in Teldrassil is reached through Darnassus (Portal to Darnassus).") in route["text"]


def test_a_route_goes_on_into_the_next_zone_through_the_way_in():
    route = plan_route(darnassus(), then={"zone": "Darnassus"})
    assert titles(route)[:4] == ["Nessa Shadowsong", "Portal to Darnassus", "Sister Aquinne", "Rellian Greenspyre"]
    assert sorted(titles(route)[4:]) == ["Arch Druid Fandral Staghelm", "Jocaste"]
    assert [s["zone"] for s in route["stops"]] == ["Teldrassil", "Teldrassil"] + ["Darnassus"] * 4  # the maps to mark on
    fandral = next(s for s in route["stops"] if s["title"] == "Arch Druid Fandral Staghelm")
    assert fandral["what"] == "turn in [Crown of the Earth]; turn in [Teldrassil]"  # one stop for both
    # the way into the next zone is a stop of its own, done when the player is in that zone
    portal = route["stops"][1]
    assert portal["what"] == "go on to Darnassus" and (portal["x"], portal["y"]) == (55.0, 89.0)
    assert portal["tasks"] == [{"kind": "travel", "zone": "Darnassus"}]
    assert any("Laird in Darkshore" in x for x in route["left_out"])  # a third zone is not on this route
    assert "2. Portal to Darnassus (55, 89)" in route["text"] and "3. Sister Aquinne (Darnassus 28.9, 45.8)" in route["text"]
    head = route["text"].split("\n")[0]
    assert head.startswith("Route from 58.4, 94 (Rut'theran Village, Teldrassil): 6 stops") and head.endswith(", then Darnassus.")
    assert "The way from Rut'theran Village into Darnassus is the one the app knows: Portal to Darnassus." in route["text"]
    assert route["ways"] == [{"zone": "Darnassus", "via": (55.0, 89.0), "title": "Portal to Darnassus", "arrive": (30.0, 41.0)}]
    # what the app knows comes before what the model recalls (it once put this portal's far end in the Temple)
    recalled = plan_route(darnassus(), then=[{"zone": "Darnassus", "via": (56.5, 92.0), "via_title": "Portal", "arrive": (39.0, 82.0)}])
    assert recalled["stops"] == route["stops"]
    # a quest to pick up in the next zone
    route = plan_route(darnassus(), then=[{"zone": "Darnassus"}], new_quests=["Eyes of the Sentinels"])
    assert "Sentinel Dalia" in titles(route) and titles(route)[1] == "Portal to Darnassus"


def test_what_matters_most_comes_first():
    """A class quest that gives the hunter their pet skills is not left for the end because it stands farthest."""
    route = plan_route(darnassus(), then={"zone": "Darnassus"}, first=["Training the Beast"])
    assert titles(route)[:3] == ["Nessa Shadowsong", "Portal to Darnassus", "Jocaste"]
    route = plan_route(snap([
        quest(1, "Far Hunt", turn_in="Giver (Teldrassil 50.0, 60.0)",
              where=["Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0 (2400 m, 9 spawn points in your zone)"]),
        quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)"),
    ]), first=["Far Hunt"])
    assert titles(route) == ["Boar", "Giver", "Postman"]  # the whole quest first, its turn-in after its objective


def test_a_way_the_app_does_not_know_is_named_by_the_model():
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)"),
              quest(2, "The Keeper", status="READY TO TURN IN", turn_in="Keeper (Moonglade 51.0, 45.0)")])
    route = plan_route(s)
    assert ('Stops wait in Moonglade (1). To take the route on there, call plan_route again with '
            'then=[{"zone": "Moonglade", "via_x": …, "via_y": …, "via_title": …, "arrive_x": …, "arrive_y": …}]') in route["text"]
    route = plan_route(s, then={"zone": "Moonglade"})
    assert titles(route) == ["Postman", "Keeper"]  # ordered, and the result says what is missing
    assert "The way from Teldrassil into Moonglade is not on the route" in route["text"]
    route = plan_route(s, then={"zone": "Moonglade", "via": (58.4, 94.0), "arrive": (44.0, 45.0)})
    assert titles(route) == ["Postman", "The way to Moonglade", "Keeper"]
    assert route["stops"][1]["tasks"] == [{"kind": "travel", "zone": "Moonglade"}]
    route = plan_route(s, then={"zone": "Moonglade", "via": (58.4, 94.0), "via_title": "The flight master"})
    assert titles(route) == ["Postman", "The flight master", "Keeper"]


def mainland():
    s = darnassus()
    s["quests"] += [
        quest(8, "Easing Suffering", status="READY TO TURN IN", turn_in="Sentinel Eralya Leafshadow (Teldrassil 37.5, 36.8)"),
        quest(9, "The Glade", turn_in="Tallonkai Swiftroot (Teldrassil 55.6, 57.0)",
              where=["Moss: from object Moss — Teldrassil 55.0, 70.0 (1100 m, 4 spawn points in your zone)"]),
        quest(10, "A Lost Letter", status="READY TO TURN IN", turn_in="Vesprystus (Teldrassil 58.4, 94.0)"),
    ]
    return s


def test_a_part_of_a_map_cut_off_from_the_rest():
    """Rut'theran Village lies on the map of Teldrassil, and the only way to the rest of it leads through Darnassus.
    "Nie bierze pod uwagę jak dostać się do danej lokacji": the forest is not walked to from the village."""
    # asked for with no zone named, the route goes on to the rest of the map by itself, through the city: the quest
    # data says "Teldrassil" for the village and for the forest alike. Another map is still the model's to name.
    route = plan_route(mainland())
    assert titles(route)[:3] == ["Vesprystus", "Nessa Shadowsong", "Portal to Darnassus"]
    assert sorted(titles(route)[-3:]) == ["Moss", "Sentinel Eralya Leafshadow", "Tallonkai Swiftroot"]
    assert route["left_out"] == ["[Flight to Auberdine]: turn in to Laird in Darkshore (36.8, 44.3), another zone, which "
                                 "the app knows the way to"]
    route = plan_route(mainland(), then=[{"zone": "Darnassus"}, {"zone": "Teldrassil"}])
    assert titles(route)[:3] == ["Vesprystus", "Nessa Shadowsong", "Portal to Darnassus"]  # the village, then out of it
    back = titles(route)[-4:]
    assert back[0] == "The gate of Darnassus (approx.)" and sorted(back[1:]) == ["Moss", "Sentinel Eralya Leafshadow", "Tallonkai Swiftroot"]
    assert back.index("Moss") < back.index("Tallonkai Swiftroot")
    assert [st["zone"] for st in route["stops"]] == ["Teldrassil"] * 3 + ["Darnassus"] * 5 + ["Teldrassil"] * 3
    assert route["stops"][-4]["tasks"] == [{"kind": "travel", "zone": "Teldrassil"}]
    assert route["text"].split("\n")[0].endswith(", then Darnassus, then Teldrassil.")
    assert "9. Sentinel Eralya Leafshadow (Teldrassil 37.5, 36.8)" in route["text"] or "Sentinel Eralya Leafshadow (Teldrassil 37.5, 36.8)" in route["text"]
    # a quest done further on and turned in where the route started: the route comes back for it (it used to leave
    # the turn-in out and say so, and the model put it on by hand in a second call)
    s = mainland()
    s["quests"].append(quest(11, "Owls", turn_in="Nessa Shadowsong (Teldrassil 56.3, 92.4)",
                             where=["Owl: use Owl — Darnassus 40.0, 40.0"]))
    route = plan_route(s, then=[{"zone": "Darnassus"}, {"zone": "Teldrassil"}])
    names = titles(route)
    assert names.count("Nessa Shadowsong") == 2 and not [line for line in route["left_out"] if "Owls" in line]
    back = len(names) - 1 - names[::-1].index("Nessa Shadowsong")
    assert names.index("Owl") < back and route["stops"][back]["what"] == "turn in [Owls]"
    assert "Portal to Rut'theran Village" in names[names.index("Owl"):back]  # by the way that leads there


def test_from_the_forest_the_city_is_entered_by_its_gate():
    """The real model sent a player in Dolanaar to the portal in Rut'theran Village, which cannot be walked to."""
    s = mainland()
    s["player"]["coords"] = "56.0, 60.0"
    route = plan_route(s, then=[{"zone": "Darnassus", "via": (56.5, 92.0), "via_title": "Portal to Darnassus (Rut'theran Village)"}])
    gate = next(st for st in route["stops"] if st["tasks"] == [{"kind": "travel", "zone": "Darnassus"}])
    assert gate["title"] == "The gate of Darnassus (approx.)" and (gate["x"], gate["y"]) == (38.0, 51.0)
    assert "Nessa Shadowsong" not in titles(route) and "Vesprystus" not in titles(route)  # the village is not walked to
    assert any(x == "[Nessa's Fish]: turn in to Nessa Shadowsong in Rut'theran Village (56.3, 92.4), another zone, which the app knows the way to"
               for x in route["left_out"])


def test_the_planner_finds_the_way_through_the_zones_between():
    """The real model named the zones (Darnassus, Teldrassil, Darkshore) and the route walked from the forest to the
    hippogryph master across the sea. The way back leads through the gate and the portal, and the app knows both."""
    route = plan_route(mainland(), then=[{"zone": "Darnassus"}, {"zone": "Teldrassil"},
                                         {"zone": "Darkshore", "via": (58.4, 94.0), "via_title": "Vesprystus"}])
    assert titles(route)[:3] == ["Vesprystus", "Nessa Shadowsong", "Portal to Darnassus"]
    assert titles(route)[7] == "The gate of Darnassus (approx.)" and route["stops"][7]["zone"] == "Darnassus"
    assert sorted(titles(route)[8:11]) == ["Moss", "Sentinel Eralya Leafshadow", "Tallonkai Swiftroot"]
    assert titles(route)[11:] == ["The gate of Darnassus (approx.)", "Portal to Rut'theran Village",
                                  "Hippogryph or boat to Auberdine", "Laird"]
    assert [st["zone"] for st in route["stops"]][11:] == ["Teldrassil", "Darnassus", "Teldrassil", "Darkshore"]
    assert [st["tasks"][0]["zone"] for st in route["stops"][11:14]] == ["Darnassus", "Teldrassil", "Darkshore"]
    assert route["text"].split("\n")[0].endswith(
        ", then Darnassus, then Teldrassil, then Darnassus, then Rut'theran Village, then Darkshore.")
    # the city and on: the portal back is not something the model has to know
    route = plan_route(darnassus(), then=[{"zone": "Darnassus"}, {"zone": "Darkshore"}])
    assert titles(route)[:2] == ["Nessa Shadowsong", "Portal to Darnassus"]
    assert titles(route)[-3:] == ["Portal to Rut'theran Village", "Hippogryph or boat to Auberdine", "Laird"]
    assert len(route["stops"]) == 9 and "is not on the route" not in route["text"]
    # and from the village straight on
    route = plan_route(darnassus())
    assert "Stops wait in Darnassus (5). The way there is known (Portal to Darnassus)" in route["text"]


def test_a_zone_the_route_only_passes_through():
    """The real model's route: the portal to Darnassus, back through it to Rut'theran, the hippogryph to Darkshore.
    The rest of Teldrassil is never on the way: a stop there is left out, not walked to across the sea."""
    fly = {"zone": "Darkshore", "via": (58.4, 94.0), "via_title": "Vesprystus", "arrive": (36.5, 44.0)}
    for back in ({"zone": "Rut'theran Village"}, {"zone": "Teldrassil", "via": (30.0, 41.0), "arrive": (56.0, 92.0)}):
        route = plan_route(mainland(), skip=["A Lost Letter"], then=[{"zone": "Darnassus"}, back, fly])
        assert titles(route)[:2] == ["Nessa Shadowsong", "Portal to Darnassus"]
        assert titles(route)[-3:] == ["Portal to Rut'theran Village", "Hippogryph or boat to Auberdine", "Laird"]
        assert sorted(titles(route)[2:-3]) == ["Arch Druid Fandral Staghelm", "Jocaste", "Rellian Greenspyre", "Sister Aquinne"]
        assert ("[Easing Suffering]: turn in to Sentinel Eralya Leafshadow in Teldrassil (37.5, 36.8), another zone, "
                "which the app knows the way to") in route["left_out"]
        assert "[The Glade]: Moss in Teldrassil (55.0, 70.0), another zone, which the app knows the way to" in route["left_out"]
        assert "Stops wait in" not in route["text"]
    # a cut-off part is small and all of it can be walked: its stops are done on the first visit, wherever the player
    # stands in it (here: next to the portal, with the hippogryph master at the other end of the village)
    s = mainland()
    s["player"]["coords"] = "55.2, 89.3"
    route = plan_route(s, then=[{"zone": "Darnassus"}, {"zone": "Rut'theran Village"}, fly])
    assert sorted(titles(route)[:2]) == ["Nessa Shadowsong", "Vesprystus"] and titles(route)[2] == "Portal to Darnassus"


def test_the_quest_that_follows_a_turn_in_is_on_the_route():
    """"Żeby pathing przewidywał, że tutaj będzie quest do wzięcia po oddaniu": the game data says which quest
    follows; the stop of the turn-in is also where it is taken, and it stays on the route until it is."""
    s = snap([
        quest(1, "Teldrassil", status="READY TO TURN IN", turn_in="Fandral (Teldrassil 60.0, 50.0)",
              next="[Grove of the Ancients] (level 17) from Fandral (Teldrassil 60.0, 50.0)", nextId=952),
        quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)",
              next="[The Reply] (level 9) from Scribe (Teldrassil 70.0, 50.0); Clerk (Teldrassil 10.0, 10.0)", nextId=77),
        quest(3, "Far", status="READY TO TURN IN", turn_in="Giver (Teldrassil 55.0, 50.0)",
              next="[Farther] from Keeper (Moonglade 50.0, 50.0)", nextId=78),
    ])
    route = plan_route(s)
    assert titles(route) == ["Giver", "Fandral", "Postman", "Scribe"]
    fandral, scribe = route["stops"][1], route["stops"][3]
    assert fandral["what"] == "turn in [Teldrassil], then pick up [Grove of the Ancients]"
    assert fandral["tasks"] == [{"kind": "turn_in", "quest": "Teldrassil", "id": 1},
                                {"kind": "pick_up", "quest": "Grove of the Ancients", "id": 952}]
    assert scribe["what"] == "pick up [The Reply], which follows [Letter]" and scribe["note"] == "quest level 9"
    assert scribe["tasks"] == [{"kind": "pick_up", "quest": "The Reply", "id": 77}]
    assert "[Farther]: follows [Far], from Keeper in Moonglade (50.0, 50.0), another zone" in route["left_out"]
    assert "2. Fandral (60, 50)" in route["text"] and "then pick up [Grove of the Ancients]" in route["text"]
    # the model leaves one out by its title; one the player already has is not taken again
    route = plan_route(s, skip=["Grove of the Ancients"])
    assert route["stops"][1]["what"] == "turn in [Teldrassil]" and "[Grove of the Ancients]: left out as asked" in route["left_out"]
    s["quests"].append(quest(77, "The Reply", turn_in="Nobody (Moonglade 1.0, 1.0)", where=["x: use x — Moonglade 2.0, 2.0"]))
    assert "Scribe" not in titles(plan_route(s))


def test_quests_offered_by_the_route_are_picked_up_on_the_way():
    """"Czemu nie zwrócił uwagi na questa": two quest givers stood four and eight metres from the player and the
    route left without them, because the model did not name them. Taking a quest costs nothing: one offered next to
    the player or to a stop is on the route unless the model leaves it out; the others are named to the model."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[
                 {"id": 11, "title": "Right Here", "giver": "Dalia", "where": "Teldrassil 50.1, 50.2", "level": 11, "minLevel": 4, "distance": "8 m"},
                 {"id": 12, "title": "By The Postman", "giver": "Clerk", "where": "Teldrassil 65.5, 50.5", "level": 9, "distance": "700 m"},
                 {"id": 13, "title": "Far Away", "giver": "Hermit", "where": "Teldrassil 20.0, 20.0", "level": 9, "distance": "1900 m"},
                 {"id": 14, "title": "Too Soon", "giver": "Elder", "where": "Teldrassil 50.2, 50.1", "level": 14, "minLevel": 13, "distance": "9 m"},
                 {"id": 15, "title": "Elsewhere", "giver": "Guard", "where": "Darnassus 30.0, 40.0", "level": 9}])
    s["player"]["level"] = 10
    route = plan_route(s)
    assert titles(route)[0] == "Dalia" and sorted(titles(route)[1:]) == ["Clerk", "Postman"]
    assert route["stops"][0]["what"] == "pick up [Right Here], offered on the way"
    assert route["stops"][0]["tasks"] == [{"kind": "pick_up", "quest": "Right Here", "id": 11}]
    assert "Picked up on the way, because their givers stand by the route: [Right Here], [By The Postman]." in route["text"]
    assert "Leave one out with skip when the player should not take it." in route["text"]
    assert ("Also offered here, not on the route: [Far Away] (level 9) from Hermit (Teldrassil 20.0, 20.0). Put the ones "
            "worth taking into new_quests.") in route["text"]
    assert "Too Soon" not in route["text"]  # the character cannot take it yet
    assert "Dalia" not in titles(plan_route(s, skip=["Right Here"]))
    far = plan_route(s, new_quests=["Far Away"])
    assert "Hermit" in titles(far) and next(st for st in far["stops"] if st["title"] == "Hermit")["what"] == "pick up [Far Away]"
    assert "Also offered here" not in far["text"]
    # a hub full of quests does not bury the route: the nearest few, the rest named
    s["nearbyQuests"] = [{"id": 20 + n, "title": f"Errand {n}", "giver": f"Clerk {n}", "level": 9, "distance": f"{n} m",
                          "where": f"Teldrassil {50 + n / 10:.1f}, 50.0"} for n in range(1, 8)]
    route = plan_route(s)
    clerks = [t for st in route["stops"] if st["title"].startswith("Clerk") for t in st["tasks"]]
    assert [t["quest"] for t in clerks] == ["Errand 1", "Errand 2", "Errand 3", "Errand 4"]  # (side by side: one stop)
    assert "Also offered here, not on the route: [Errand 5]" in route["text"]



def test_an_objective_the_database_calls_credit_is_named_by_the_quest_log():
    """Placing owls: the database knows the four spots as an invisible creature called "Credit". The stop is what the
    quest log calls it, nothing is watched for there, and each stop is done with its own objective."""
    s = snap([quest(1, "Eyes of the Sentinels", turn_in="Dalia (Teldrassil 50.0, 51.0)",
                    objectives="0/1 Darnassus Bank [TO DO]; 1/1 City Gate [DONE]; 0/1 Craftsman's Terrace Inn [TO DO]",
                    where=["Credit: find Credit (level 60) — Teldrassil 41.0, 43.5 (259 m, 1 spawn point in your zone)",
                           "Credit: find Credit (level 60) — Teldrassil 66.5, 15.3 (515 m, 1 spawn point in your zone)"])])
    route = plan_route(s)
    assert sorted(titles(route)) == ["Craftsman's Terrace Inn", "Dalia", "Darnassus Bank"]
    bank = next(st for st in route["stops"] if st["title"] == "Darnassus Bank")
    assert bank["mob"] is None and bank["what"] == "[Eyes of the Sentinels]: Darnassus Bank"
    assert bank["tasks"] == [{"kind": "objective", "quest": "Eyes of the Sentinels", "id": 1, "label": "Darnassus Bank"}]
    assert (bank["x"], bank["y"]) == (41.0, 43.5)


def test_credit_spots_of_objectives_that_are_done_are_not_stops():
    """The game data as it really comes: all four owl spots stay listed, also the two whose objective is done.
    The route planned again was the same four "Credit" stops, so the update said "nothing to change"."""
    s = snap([quest(1, "Eyes of the Sentinels", turn_in="Dalia (Teldrassil 50.0, 51.0)",
                    objectives=("1/1 Cenarion Hold depths entrance [DONE]; 1/1 Darnassus Bank [DONE]; "
                                "0/1 Craftsman's Terrace Inn [TO DO]; 0/1 City Gate [TO DO]"),
                    where=["Credit: find Credit (level 60) — Teldrassil 33.4, 16.8 (12 m, 1 spawn point in your zone)",
                           "Credit: find Credit (level 60) — Teldrassil 41.0, 43.5 (189 m, 1 spawn point in your zone)",
                           "Credit: find Credit (level 60) — Teldrassil 66.5, 15.3 (309 m, 1 spawn point in your zone)",
                           "Credit: find Credit (level 60) — Teldrassil 89.1, 34.8 (543 m, 1 spawn point in your zone)"])])
    route = plan_route(s)
    assert sorted(titles(route)) == ["City Gate", "Craftsman's Terrace Inn", "Dalia"]
    inn = next(st for st in route["stops"] if st["title"] == "Craftsman's Terrace Inn")
    assert (inn["x"], inn["y"]) == (66.5, 15.3) and inn["mob"] is None
    assert inn["tasks"] == [{"kind": "objective", "quest": "Eyes of the Sentinels", "id": 1, "label": "Craftsman's Terrace Inn"}]


def test_flight_paths_not_discovered_by_the_route_are_named():
    """"Powinien tworzyć drogę bez nich, ale proponować, że może je dodać, bo są blisko i warto odblokować" — only
    the ones the character does not have."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 60.0, 50.0)"),
              quest(2, "Far", status="READY TO TURN IN", turn_in="Hermit (Teldrassil 20.0, 20.0)")],
             zoneNPCs=["Talon <Hippogryph Master> [flight master, flight path not discovered] — Teldrassil 61.0, 51.0 (550 m)",
                       "Known One <Hippogryph Master> [flight master, flight path known] — Teldrassil 59.0, 49.5 (450 m)",
                       "Far Bird <Hippogryph Master> [flight master, flight path not discovered] — Teldrassil 90.0, 90.0 (2500 m)",
                       "Unsure <Wind Rider Master> [flight master] — Teldrassil 21.0, 21.0 (1500 m)",
                       "Baker <Food & Drink> [vendor] — Teldrassil 60.5, 50.5 (500 m)"])
    route = plan_route(s)
    assert "Talon" not in titles(route)  # the route is made without them
    assert re.search(r"Flight paths not discovered by the route: Talon <Hippogryph Master> \(61, 51\), \d+ m from stop "
                     r"1 \(Postman\)\. Leave them off the route", route["text"])
    assert ("The add-on does not know yet which flight paths the character has (no flight master opened since): offer "
            "none; opening any flight master tells it.") in route["text"] and "Unsure" not in route["text"]
    assert "Known One" not in route["text"] and "Far Bird" not in route["text"]
    # one the route passes anyway (here: its way out of the zone) is said too: talking to him opens the path
    route = plan_route(s, then={"zone": "Moonglade", "via": (61.0, 51.0), "via_title": "Talon"})
    assert re.search(r"Flight paths not discovered by the route: Talon <Hippogryph Master> \(61, 51\), at stop \d \(Talon\)\.",
                     route["text"])


def test_a_remembered_way_round_is_taken():
    """The straight line runs over the hill: the route goes the way the player walked before, and says so."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 60.0, 50.0)")], coords="40.0, 50.0",
             detours=["Teldrassil 40.5, 49.5 → 59.5, 50.5: through 50.0, 70.0 (straight 900 m, round 1400 m)"])
    route = plan_route(s)
    assert titles(route) == ["The way round", "Postman"]
    round_ = route["stops"][0]
    assert (round_["x"], round_["y"]) == (50.0, 70.0) and round_["tasks"] == []
    assert round_["what"] == "go round: the straight way is blocked (the player walked this way before)"
    # the other way too
    s = snap([quest(1, "Back", status="READY TO TURN IN", turn_in="Hermit (Teldrassil 40.0, 50.0)")], coords="60.0, 50.0",
             detours=s["detours"])
    assert titles(plan_route(s)) == ["The way round", "Hermit"]
    # a way that does not end where the walk did is straight
    s["player"]["coords"] = "60.0, 30.0"
    assert titles(plan_route(s)) == ["Hermit"]


def test_flight_masters_in_the_zones_the_route_goes_on_into():
    s = darnassus()
    s["flightMasters"] = [
        "Caylais Moonfeather <Hippogryph Master> [flight path not discovered] — Darkshore 36.3, 45.6",
        "Vesprystus <Hippogryph Master> [flight path known] — Teldrassil 58.4, 94.0",
        "Daelyshia <Hippogryph Master> [flight path not discovered] — Ashenvale 34.4, 48.0"]
    route = plan_route(s, then=[{"zone": "Darnassus"}, {"zone": "Darkshore"}])
    assert "Laird" in titles(route)
    assert re.search(r"Flight paths not discovered by the route: Caylais Moonfeather <Hippogryph Master> \(Darkshore "
                     r"36\.3, 45\.6\), (at|near) stop \d+ \(", route["text"])
    assert "Vesprystus <Hippogryph" not in route["text"].split("Flight paths")[-1] and "Daelyshia" not in route["text"]


# Buzzbox 525 in the game: the route sent the player to Den Mother (one spawn point, level 18-19), the nearest, though
# the bears of the quest text drop the scalps more often and stand in nineteen places
BUZZBOX = quest(1003, "Buzzbox 525", turn_in="Buzzbox 525 (Darkshore 41.4, 80.6)", where=[
    "Grizzled Scalp: dropped by Den Mother (level 18-19, elite, 1342 health, 18% drop chance) — Darkshore 51.5, 38.3 "
    "(82 m, 1 spawn point in your zone); dropped by Grizzled Thistle Bear (level 16-17, 617-665 health, 32% drop "
    "chance) — Darkshore 39.1, 70.0 (1548 m, 19 spawn points in your zone)"])


def darkshore(quests):
    return {"player": {"coords": "52.2, 36.9", "zone": "Darkshore", "map": "Darkshore", "level": 13}, "quests": quests}


def test_the_model_chooses_the_source_of_an_objective():
    # nothing said: the surest of them, as the model was told to choose (the nearest is an elite with one spawn point)
    route = plan_route(darkshore([BUZZBOX]))
    assert titles(route) == ["Grizzled Thistle Bear", "Buzzbox 525"]
    text = route["text"]
    assert ("- Grizzled Scalp for [Buzzbox 525]: dropped by Grizzled Thistle Bear (level 16-17, 617-665 health, 32% drop "
            "chance) — Darkshore 39.1, 70.0 (1548 m, 19 spawn points in your zone) [3-4 levels above the player] — taken, "
            "the surest (not the nearest); also dropped by Den Mother (level 18-19, elite, 1342 health, 18% drop chance) — "
            "Darkshore 51.5, 38.3 (82 m, 1 spawn point in your zone) [5-6 levels above the player]") in text
    assert titles(plan_route(darkshore([BUZZBOX]), use=["Den Mother"])) == ["Den Mother", "Buzzbox 525"]  # named: it stands
    # right after the stops: the model reads it before it answers
    assert text.splitlines()[3].startswith("Check the sources: these objectives have more than one")
    route = plan_route(darkshore([BUZZBOX]), use=["grizzled thistle bear"])
    assert titles(route) == ["Grizzled Thistle Bear", "Buzzbox 525"]
    bears = route["stops"][0]
    assert (bears["x"], bears["y"], bears["mob"]) == (39.1, 70.0, "Grizzled Thistle Bear")
    assert bears["tasks"] == [{"kind": "objective", "quest": "Buzzbox 525", "id": 1003, "label": "Grizzled Scalp"}]
    assert "Check the sources" not in route["text"] and "No objective" not in route["text"]
    route = plan_route(darkshore([BUZZBOX]), use=["Thistle Bear"])
    assert "No objective of the quest log has a source called Thistle Bear" in route["text"]


def test_an_update_adds_no_quest_the_route_did_not_have():
    """From the game: the player turned a quest in at Lakeshire, and the update of the route put four quest givers of
    the town on it; the next update, half a minute later, four others. "Za dużo zmienia listę ... nagle bierze questa,
    który poprzedni route totalnie olał." The quests taken along on the way belong to the route when it is made: an
    update keeps those (the plan remembers them) and adds none."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[
                 {"id": 11, "title": "Right Here", "giver": "Dalia", "where": "Teldrassil 50.1, 50.2", "level": 11, "minLevel": 4},
                 {"id": 12, "title": "By The Postman", "giver": "Clerk", "where": "Teldrassil 65.5, 50.5", "level": 9}])
    s["player"]["level"] = 10
    made = plan_route(s)
    assert sorted(titles(made)) == ["Clerk", "Dalia", "Postman"] and made["along_titles"] == ["Right Here", "By The Postman"]
    # the route was planned with Dalia's quest; the clerk's came into reach since
    again = plan_route(s, along=["Right Here"])
    assert sorted(titles(again)) == ["Dalia", "Postman"] and again["along_titles"] == ["Right Here"]
    assert ("Also offered here, not on the route: [By The Postman] (level 9) from Clerk (Teldrassil 65.5, 50.5). An update "
            "adds none of them: they go on the route when the player asks for them.") in again["text"]
    # a quest the model named for the route is on it through every update, wherever its giver stands
    assert sorted(titles(plan_route(s, new_quests=["By The Postman"], along=[]))) == ["Clerk", "Postman"]
    # a route planned with none of them takes none along
    assert titles(plan_route(s, along=[])) == ["Postman"]


# ---------------------------------------------------------------- quests done along the route, planned when it is made
# The player's words: "on powinien brać pod uwagę questy, które są po drodze i które można zrobić przy okazji, nie
# zbaczając z trasy, i planować je od razu ... dobierane do lvla, ale żeby nie przesadzał, że 1 czy 2 lvl wyżej to za
# dużo". In Teldrassil here one map unit is about 47 m across and 31 m down.
def _offer(qid, title, giver, at, where=None, turn_in=None, **extra):
    return {"id": qid, "title": title, "giver": giver, "where": f"Teldrassil {at[0]}, {at[1]}", "level": 10, "minLevel": 5,
            "whereToGet": where, "turnIn": turn_in, **extra}


def test_a_quest_done_by_the_route_is_planned_whole():
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[_offer(21, "Pests", "Clerk", (65.5, 50.5), turn_in="Clerk (Teldrassil 65.5, 50.5)",
                                  where=["Rat: kill Rat (level 9) — Teldrassil 67.0, 52.0"])])
    route = plan_route(s)
    assert titles(route) == ["Postman", "Clerk", "Rat", "Clerk"] and route["along_titles"] == ["Pests"]
    kinds = [[(t["kind"], t["quest"]) for t in st["tasks"]] for st in route["stops"]]
    assert kinds == [[("turn_in", "Letter")], [("pick_up", "Pests")], [("objective", "Pests")], [("turn_in", "Pests")]]
    # once it is taken, the route planned again from the quest log is the same route without the stop it was taken at
    taken = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)"),
                  quest(21, "Pests", turn_in="Clerk (Teldrassil 65.5, 50.5)",
                        where=["Rat: kill Rat (level 9) — Teldrassil 67.0, 52.0"])], coords="65.5, 50.5")
    assert sorted(titles(plan_route(taken, along=["Pests"]))) == ["Clerk", "Postman", "Rat"]  # (the map keeps their order)


def test_a_quest_that_would_pull_the_route_away_is_offered_not_taken():
    """Its giver stands by the route; what it wants is across the zone. Taken along, it would send the player there
    the moment it is in the log."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[
                 _offer(22, "Far Pests", "Clerk", (65.5, 50.5), turn_in="Clerk (Teldrassil 65.5, 50.5)",
                        where=["Wolf: kill Wolf (level 9) — Teldrassil 30.0, 80.0"]),
                 _offer(23, "Deliver It", "Scribe", (65.2, 50.2), turn_in="Elder (Teldrassil 20.0, 20.0)"),
                 _offer(24, "Deliver It Here", "Scribe", (65.2, 50.2), turn_in="Postman (Teldrassil 65.0, 50.0)"),
                 _offer(25, "Elsewhere", "Scribe", (65.2, 50.2), turn_in="Guard (Darnassus 30.0, 40.0)")])
    route = plan_route(s)
    assert route["along_titles"] == ["Deliver It Here"]
    # (the scribe and the postman stand side by side: one stop, with whose each task is)
    assert len(route["stops"]) == 1 and sorted((t["kind"], t["quest"]) for t in route["stops"][0]["tasks"]) == [
        ("pick_up", "Deliver It Here"), ("turn_in", "Deliver It Here"), ("turn_in", "Letter")]
    assert "[Far Pests] (level 10) from Clerk" in route["text"] and "[Deliver It] (level 10) from Scribe" in route["text"]
    # named by the model, a quest is on the route whatever the way it costs
    assert "Wolf" in titles(plan_route(s, new_quests=["Far Pests"]))


def test_quests_taken_along_are_of_the_characters_level():
    """Up to two levels above is theirs to do; three is not taken by itself; a quest that gives nothing any more is
    not even named; a group's quest is not taken along."""
    here = dict(turn_in="Postman (Teldrassil 65.0, 50.0)")
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[dict(_offer(31, "Two Above", "A", (65.1, 50.1), **here), level=12),
                           dict(_offer(32, "Three Above", "B", (65.2, 50.2), **here), level=13),
                           dict(_offer(33, "Gray", "C", (65.3, 50.3), **here), level=4),
                           dict(_offer(34, "Low But Green", "D", (65.4, 50.4), **here), level=5),
                           dict(_offer(35, "For A Group", "E", (65.5, 50.5), **here), level=10, type="Elite")])
    route = plan_route(s)
    assert sorted(route["along_titles"]) == ["Low But Green", "Two Above"]
    assert "[Three Above] (level 13)" in route["text"] and "[For A Group] (level 10)" in route["text"]
    assert "Gray" not in route["text"]


def test_each_zone_of_the_route_has_its_own_few():
    """Four from the town the player stands in must not use up what the next zone may take along."""
    from foreverai.route import NEAR_MAX
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[_offer(40 + n, f"Errand {n}", f"Clerk {n}", (50.0 + n / 100, 50.0), turn_in="Postman (Teldrassil 65.0, 50.0)")
                           for n in range(1, 8)])
    assert len(plan_route(s)["along_titles"]) == NEAR_MAX


def test_a_long_grind_is_not_taken_along():
    """From a trial on the real model: "Selling Fish" stood by the route, its murlocs too; the planner took it along
    and the model planned again to drop it: ten fish at a drop in two is some twenty murlocs."""
    here = dict(turn_in="Postman (Teldrassil 65.0, 50.0)")
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")],
             nearbyQuests=[
                 dict(_offer(51, "Selling Fish", "A", (65.1, 50.1), **here), objective="Bring 10 Spotted Sunfish.",
                      whereToGet=["Sunfish: dropped by Murloc (level 9, 45% drop chance) — Teldrassil 65.5, 50.8"]),
                 dict(_offer(52, "Four Tails", "B", (65.2, 50.2), **here), objective="Bring 4 Tails.",
                      whereToGet=["Tail: dropped by Cat (level 9, 80% drop chance) — Teldrassil 65.5, 50.8"]),
                 dict(_offer(53, "Cull", "C", (65.3, 50.3), **here), objective="Kill 8 Owls.",
                      whereToGet=["Owl: kill Owl (level 9) — Teldrassil 65.5, 50.8"]),
                 dict(_offer(54, "Purge", "D", (65.4, 50.4), **here), objective="Kill 30 Sprites.",
                      whereToGet=["Sprite: kill Sprite (level 9) — Teldrassil 65.5, 50.8"])])
    route = plan_route(s)
    assert sorted(route["along_titles"]) == ["Cull", "Four Tails"]
    assert "[Selling Fish] (level 10)" in route["text"] and "[Purge] (level 10)" in route["text"]


def test_many_stops_come_close_to_the_shortest_order(monkeypatch):
    """More stops in a zone than every order can be tried for: nearest-first alone is long (a stop passed by is come
    back for at the end). Measured here against the exact search on the same stops, with stops that wait for others
    (a quest is picked up before it is done, turned in after)."""
    import math
    import random
    from foreverai import route as R
    rng = random.Random(7)
    worse, greedy_worse = [], []
    for case in range(30):
        n = rng.choice([13, 14])
        nodes = [{"at": (rng.uniform(0, 3000), rng.uniform(0, 2000)), "after": []} for _ in range(n)]
        for k in range(0, n - 2, 3):  # chains of three: taken, done, turned in
            nodes[k + 1]["after"], nodes[k + 2]["after"] = [k], [k, k + 1]
        start, end = (rng.uniform(0, 3000), rng.uniform(0, 2000)), ((0.0, 0.0) if case % 2 else None)

        def length(order):
            total, pos = 0.0, start
            for i in order:
                total += math.dist(pos, nodes[i]["at"])
                pos = nodes[i]["at"]
            return total + (math.dist(pos, end) if end else 0.0)

        def waits(order):
            return all(order.index(a) < order.index(i) for i in order for a in nodes[i]["after"])
        monkeypatch.setattr(R, "EXACT_MAX", 12)
        got = R._order(start, [dict(node) for node in nodes], end)
        monkeypatch.setattr(R, "_shorter", lambda order, *a: order)
        greedy = R._order(start, [dict(node) for node in nodes], end)
        monkeypatch.undo()
        monkeypatch.setattr(R, "EXACT_MAX", 14)
        exact = R._order(start, [dict(node) for node in nodes], end)
        monkeypatch.undo()
        assert sorted(got) == list(range(n)) and waits(got)
        worse.append(length(got) / length(exact))
        greedy_worse.append(length(greedy) / length(exact))
    assert max(worse) < 1.15 and sum(worse) / len(worse) < 1.04, (max(worse), sum(worse) / len(worse))
    assert sum(greedy_worse) / len(greedy_worse) > 1.15  # what it was before


def test_the_surest_source_of_an_objective_is_taken_not_the_nearest():
    """From a trial: of two creatures the nearest was one or two levels above the character; the model called the
    planner a second time only to name the other. The planner takes the one the model was told to prefer."""
    from foreverai.route import _surest
    near_high = "dropped by Moonstalker Matriarch (level 19-20, 40% drop chance) — Darkshore 60.9, 13.4 (900 m, 6 spawn points in your zone)"
    far_fit = "dropped by Moonstalker Sire (level 17-18, 40% drop chance) — Darkshore 44.0, 81.0 (2400 m, 12 spawn points in your zone)"
    assert _surest([near_high, far_fit], 18) == far_fit and _surest([near_high, far_fit], 20) == near_high
    lone = "dropped by Old Grizzle (level 10, rare, 100% drop chance) — Teldrassil 52.0, 50.0 (90 m, 1 spawn points in your zone)"
    common = "dropped by Bear (level 9-10, 60% drop chance) — Teldrassil 58.0, 52.0 (400 m, 9 spawn points in your zone)"
    assert _surest([lone, common], 10) == common
    s = snap([quest(1, "Pelts", turn_in="Giver (Teldrassil 50.0, 60.0)", where=[f"Pelt: {lone}; {common}"])])
    route = plan_route(s)
    assert titles(route)[0] == "Bear" and "taken, the surest (not the nearest)" in route["text"]
    assert titles(plan_route(s, use=["Old Grizzle"]))[0] == "Old Grizzle"  # what the model names stands


def test_an_object_with_several_places_is_an_area_to_search():
    """From the game: the mud of [Hilary's Necklace] was marked as a point on the lake bed; it lies in one of several.
    "Musi informować w takich przypadkach, że to jest ogólna okolica i może być wszędzie w pobliżu"."""
    s = snap([quest(1, "Necklace", turn_in="Hilary (Teldrassil 52.0, 52.0)",
                    where=["Necklace: from object Glinting Mud — Teldrassil 55.0, 55.0 (300 m, 6 spawn points in your zone)"]),
              quest(2, "Chest", turn_in="Hilary (Teldrassil 52.0, 52.0)",
                    where=["Key: from object Old Chest — Teldrassil 56.0, 56.0 (350 m, 1 spawn point in your zone)"]),
              quest(3, "Boars", turn_in="Hilary (Teldrassil 52.0, 52.0)",
                    where=["Boar: kill Boar (level 9) — Teldrassil 57.0, 57.0 (400 m, 9 spawn points in your zone)"])])
    stops = {st["title"]: st for st in plan_route(s)["stops"]}
    mud = stops["Glinting Mud"]
    assert mud["what"] == "[Necklace]: from object Glinting Mud (it lies in one of 6 places around there: search the area)"
    assert mud["tasks"] == [{"kind": "objective", "quest": "Necklace", "id": 1, "label": "Necklace", "spots": 6}]
    assert "spots" not in stops["Old Chest"]["tasks"][0] and "search" not in stops["Old Chest"]["what"]  # one place: a spot
    assert "spots" not in stops["Boar"]["tasks"][0]  # creatures walk about anyway, and are watched for
    # the quests of another zone come without a distance: the count is there all the same
    far = dict(s, quests=["Quest log: 0/40"], nearbyQuests=[{
        "id": 9, "title": "Far Necklace", "level": 10, "giver": "Shawn", "where": "Teldrassil 50.2, 50.2",
        "turnIn": "Shawn (Teldrassil 50.2, 50.2)",
        "whereToGet": ["Necklace: from object Glinting Mud — Teldrassil 51.0, 51.0 (5 spawn points in that zone)"]}])
    mud = next(st for st in plan_route(far, new_quests=["Far Necklace"])["stops"] if st["title"] == "Glinting Mud")
    assert mud["tasks"][0]["spots"] == 5 and (mud["x"], mud["y"]) == (51.0, 51.0)


def test_the_next_part_of_a_chain_taken_along_is_planned():
    """Found by walking a route with the real model: [Young Lovers] was taken along (taken at Maybell, turned in at
    Tommy) and [Speak with Gramma], which Tommy gives right after, was not on the route."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 50.5, 50.5)")],
             nearbyQuests=[_offer(106, "Young Lovers", "Maybell", (50.2, 50.2), turn_in="Tommy (Teldrassil 51.0, 51.0)",
                                  next="[Speak with Gramma] (level 9) from Tommy (Teldrassil 51.0, 51.0)", nextId=111)])
    route = plan_route(s)
    tommy = next(st for st in route["stops"] if st["title"] == "Tommy")
    assert [(t["kind"], t["quest"]) for t in tommy["tasks"]] == [("turn_in", "Young Lovers"), ("pick_up", "Speak with Gramma")]


def test_a_chain_whose_parts_share_a_title_is_known_by_number():
    """Found by walking a route on the game's own quests: "Messenger to Westfall" is two quests, taken one after the
    other. Part 1 was taken along; when it was turned in, the update took part 2 for it."""
    part1 = _offer(143, "Messenger", "Clerk", (65.2, 50.2), turn_in="Postman (Teldrassil 65.0, 50.0)")
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")], nearbyQuests=[part1])
    made = plan_route(s)
    assert made["along"] == [143] and made["along_titles"] == ["Messenger"]
    # part 1 is turned in; part 2, of the same name, is offered by the postman: the update does not take it
    part2 = _offer(144, "Messenger", "Postman", (65.0, 50.0), turn_in="Clerk (Teldrassil 65.2, 50.2)")
    later = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 65.0, 50.0)")], nearbyQuests=[part2])
    assert plan_route(later, along=[143])["along"] == []
    # a plan of an older version named them by title: it is still read so
    assert plan_route(s, along=["Messenger"])["along"] == [143]


def test_an_objective_that_is_done_is_no_stop():
    """Found by walking the game's own quests: "15/15 Defias Smuggler slain [DONE]" was planned as a stop again, because
    the add-on says where every objective is found, done or not."""
    s = snap([quest(12, "Militia", turn_in="Gryan (Teldrassil 50.0, 50.0)",
                    objectives="15/15 Defias Smuggler slain [DONE]; 0/15 Defias Trapper slain [TO DO]",
                    where=["Defias Smuggler: kill Defias Smuggler (level 12) — Teldrassil 40.0, 40.0",
                           "Defias Trapper: kill Defias Trapper (level 12) — Teldrassil 60.0, 60.0"])])
    assert titles(plan_route(s)) == ["Defias Trapper", "Gryan"]


def test_two_npcs_side_by_side_are_one_stop_in_the_plan():
    """Found by walking the game's own Goldshire: Lee Brown stands 0.15 map units from Jason Mathers. The add-on shows
    them as one stop; the plan had two, and the update's line said "+ Lee Brown"."""
    s = snap([quest(1, "Letter", status="READY TO TURN IN", turn_in="Jason (Teldrassil 47.46, 62.18)"),
              quest(2, "Kelp", status="READY TO TURN IN", turn_in="Lee (Teldrassil 47.61, 62.32)")])
    route = plan_route(s)
    assert titles(route) == ["Jason"] or titles(route) == ["Lee"], titles(route)
    tasks = route["stops"][0]["tasks"]
    assert sorted(t["quest"] for t in tasks) == ["Kelp", "Letter"]
    assert [t.get("at") for t in tasks if t.get("at")] in (["Lee"], ["Jason"])


def test_a_next_part_also_named_to_pick_up_has_one_stop():
    """Found by the real model on the game's own Duskwood: [Jitters' Growling Gut] follows [Raven Hill], and the model
    named it in new_quests too; the route had two stops at Jitters to pick it up."""
    s = snap([quest(163, "Raven Hill", status="READY TO TURN IN", turn_in="Jitters (Teldrassil 60.0, 60.0)",
                    next="[Jitters' Growling Gut] (level 20) from Jitters (Teldrassil 60.0, 60.0)", nextId=5)],
             nearbyQuests=[_offer(5, "Jitters' Growling Gut", "Jitters", (60.0, 60.0), turn_in="Chef (Teldrassil 62.0, 62.0)")])
    route = plan_route(s, new_quests=["Jitters' Growling Gut"])
    picks = [t for st in route["stops"] for t in st["tasks"] if t["kind"] == "pick_up" and t.get("id") == 5]
    assert len(picks) == 1, [(st["title"], st["tasks"]) for st in route["stops"]]



def test_a_next_part_the_player_is_through_with_is_not_picked_up_again():
    """Found on the game's own Duskwood: [Jitters' Growling Gut] follows [Raven Hill], and the game gives it without
    it. The player took it first and turned it in; every update put "pick up [Jitters' Growling Gut]" back on the stop
    where [Raven Hill] is turned in."""
    s = snap([quest(163, "Raven Hill", status="READY TO TURN IN", turn_in="Jitters (Teldrassil 60.0, 60.0)",
                    next="[Jitters' Growling Gut] (level 20) from Jitters (Teldrassil 60.0, 60.0)", nextId=5)])
    picks = lambda route: [t for st in route["stops"] for t in st["tasks"] if t["kind"] == "pick_up"]
    assert [t["id"] for t in picks(plan_route(s))] == [5]
    assert picks(plan_route(dict(s, through=[5]))) == [] and picks(plan_route(dict(s, routeDone=[5]))) == []



def test_one_creature_for_two_quests_a_little_apart_is_one_stop():
    """Found by walking the game's own Goldshire: [Gold Dust Exchange] in the log (the spawn the game names nearest to
    the player) and [Kobold Candles] to pick up (the spawn the zone's data names) both want Kobold Miners, 47 m apart.
    The route had two stops for them, with another between: there and back."""
    s = snap([quest(47, "Gold Dust Exchange", turn_in="Remy (Teldrassil 50.0, 50.0)",
                    where=["Gold Dust: dropped by Kobold Miner (level 6) — Teldrassil 40.0, 78.6 (900 m, 12 spawn points in your zone)"]),
              quest(60, "Kobold Candles", turn_in="William (Teldrassil 51.0, 50.0)",
                    where=["Large Candle: dropped by Kobold Miner (level 6) — Teldrassil 40.5, 80.3 (950 m, 12 spawn points in your zone)"]),
              quest(61, "Princess", turn_in="Ma (Teldrassil 34.7, 84.5)",
                    where=["Collar: dropped by Princess (level 9) — Teldrassil 37.0, 81.5 (1000 m, 1 spawn point in your zone)"])])
    route = plan_route(s)
    assert titles(route).count("Kobold Miner") == 1, titles(route)
    miners = next(st for st in route["stops"] if st["title"] == "Kobold Miner")
    assert {t["id"] for t in miners["tasks"]} == {47, 60}
    # far apart, they are two grounds of it
    far = snap([s["quests"][1], dict(s["quests"][2], whereToGet=[
        "Large Candle: dropped by Kobold Miner (level 6) — Teldrassil 60.0, 20.0 (950 m, 12 spawn points in your zone)"])])
    assert titles(plan_route(far)).count("Kobold Miner") == 2



def test_an_update_keeps_the_source_the_route_has():
    """Found by walking the game's own Goldshire: every update chose the source of an objective again, from where the
    player stood: the stop for the gold dust went from the Kobold Miners to the Kobold Geomancers and back."""
    def at(coords):
        return snap([quest(47, "Gold Dust Exchange", turn_in="Remy (Teldrassil 42.0, 67.0)", where=[
            "Gold Dust: dropped by Kobold Miner (level 6) — Teldrassil 40.0, 78.6 (300 m, 12 spawn points in your zone); "
            "dropped by Kobold Geomancer (level 7) — Teldrassil 63.5, 58.3 (700 m, 8 spawn points in your zone)"])], coords=coords)
    assert titles(plan_route(at("42.0, 70.0"))) == ["Kobold Miner", "Remy"]
    near_the_geomancers = at("62.0, 60.0")
    near_the_geomancers["quests"][1]["whereToGet"] = [
        "Gold Dust: dropped by Kobold Geomancer (level 7) — Teldrassil 63.5, 58.3 (60 m, 8 spawn points in your zone); "
        "dropped by Kobold Miner (level 6) — Teldrassil 40.0, 78.6 (800 m, 12 spawn points in your zone)"]
    assert titles(plan_route(near_the_geomancers))[0] == "Kobold Geomancer"  # chosen anew: the nearest
    kept = dict(near_the_geomancers, keepSources={"47:gold dust": "Kobold Miner"})
    assert titles(plan_route(kept, along=[])) == ["Kobold Miner", "Remy"]  # an update keeps the miners
    # a source the data names no more: chosen anew
    gone = dict(near_the_geomancers, keepSources={"47:gold dust": "Kobold Tunneler"})
    assert titles(plan_route(gone, along=[]))[0] == "Kobold Geomancer"



def test_an_update_keeps_a_source_the_quest_log_names_no_more():
    """Found by walking the game's own Westfall: the quest log names the five sources nearest to the player. Walking
    away, the Murloc Coastrunners the route had fell out of them, and the update took other murlocs, far off."""
    coast = "dropped by Murloc Coastrunner (level 13) — Teldrassil 43.9, 7.9 (900 m, 12 spawn points in your zone)"
    planned = snap([quest(92744, "Murloc Gills", turn_in="Alba (Teldrassil 52.5, 53.0)",
                          where=["Longshore Murloc Gill: " + coast])], coords="50.0, 20.0")
    made = plan_route(planned)
    assert titles(made) == ["Murloc Coastrunner", "Alba"] and made["sources"] == {"92744:longshore murloc gill": coast}
    later = snap([quest(92744, "Murloc Gills", turn_in="Alba (Teldrassil 52.5, 53.0)", where=[
        "Longshore Murloc Gill: dropped by Murloc Warrior (level 14) — Teldrassil 27.7, 39.1 (300 m, 12 spawn points in "
        "your zone); dropped by Murloc (level 12) — Teldrassil 30.0, 40.0 (350 m, 12 spawn points in your zone); and 1 more"])],
        coords="30.0, 45.0")
    assert titles(plan_route(later))[0] != "Murloc Coastrunner"  # chosen anew, among the ones the log names
    kept = plan_route(dict(later, keepSources=made["sources"]), along=[])
    assert titles(kept) == ["Murloc Coastrunner", "Alba"] and (kept["stops"][0]["x"], kept["stops"][0]["y"]) == (43.9, 7.9)



def test_an_item_whose_name_holds_a_colon():
    """Found on the game's own Goldshire: [Rare Books] wants "Arcane Explainer: Magical Stuff in Simple Words". Split
    at the first ": ", the objective was "Arcane Explainer" and the stop "Magical Stuff in Simple Words: dropped by
    Mother Fang"."""
    from foreverai.route import split_line
    line = ("Arcane Explainer: Magical Stuff in Simple Words: dropped by Mother Fang (level 10) — Teldrassil 62.1, 48.0 "
            "(900 m, 1 spawn point in your zone)")
    assert split_line(line)[0] == "Arcane Explainer: Magical Stuff in Simple Words"
    assert split_line("Boar: kill Boar (level 5) — Teldrassil 80.0, 50.0") == ("Boar", "kill Boar (level 5) — Teldrassil 80.0, 50.0")
    route = plan_route(snap([quest(91777, "Rare Books", turn_in="Brother Paxton (Teldrassil 49.6, 40.4)", where=[line])]))
    mother = route["stops"][0]
    assert mother["title"] == "Mother Fang" and mother["mob"] == "Mother Fang"
    assert mother["tasks"][0]["label"] == "Arcane Explainer: Magical Stuff in Simple Words"
