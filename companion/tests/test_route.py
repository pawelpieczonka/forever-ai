"""plan_route: the stops of the quest log in the shortest walking order, each turn-in after its objectives."""

import json
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
    assert order[0] == "Strigid Screecher" and len(order) == 10  # eight, and two quests offered next to stops
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
    assert text.startswith("Route from 64.1, 68.5 (Teldrassil): 10 stops, about ") and "1. Strigid Screecher (64.2, 69.2)" in text
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
    route = plan_route(mainland())
    assert sorted(titles(route)) == ["Nessa Shadowsong", "Vesprystus"]
    assert ("[Easing Suffering]: turn in to Sentinel Eralya Leafshadow in Teldrassil (37.5, 36.8), another zone, which "
            "the app knows the way to") in route["left_out"]
    route = plan_route(mainland(), then=[{"zone": "Darnassus"}, {"zone": "Teldrassil"}])
    assert titles(route)[:3] == ["Vesprystus", "Nessa Shadowsong", "Portal to Darnassus"]  # the village, then out of it
    back = titles(route)[-4:]
    assert back[0] == "The gate of Darnassus (approx.)" and sorted(back[1:]) == ["Moss", "Sentinel Eralya Leafshadow", "Tallonkai Swiftroot"]
    assert back.index("Moss") < back.index("Tallonkai Swiftroot")
    assert [st["zone"] for st in route["stops"]] == ["Teldrassil"] * 3 + ["Darnassus"] * 5 + ["Teldrassil"] * 3
    assert route["stops"][-4]["tasks"] == [{"kind": "travel", "zone": "Teldrassil"}]
    assert route["text"].split("\n")[0].endswith(", then Darnassus, then Teldrassil.")
    assert "9. Sentinel Eralya Leafshadow (Teldrassil 37.5, 36.8)" in route["text"] or "Sentinel Eralya Leafshadow (Teldrassil 37.5, 36.8)" in route["text"]
    # a turn-in the route has left behind is not on it, and the result says so
    s = mainland()
    s["quests"].append(quest(11, "Owls", turn_in="Nessa Shadowsong (Teldrassil 56.3, 92.4)",
                             where=["Owl: use Owl — Darnassus 40.0, 40.0"]))
    route = plan_route(s, then=[{"zone": "Darnassus"}, {"zone": "Teldrassil"}])
    assert "Owl" in titles(route) and titles(route).count("Nessa Shadowsong") == 1
    assert ("[Owls]: turn in to Nessa Shadowsong in Rut'theran Village (56.3, 92.4), not where the route passes through "
            "Rut'theran Village after that") in route["left_out"]


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
    assert ("Picked up on the way, because their givers stand by the route: [Right Here], [By The Postman]. Leave one "
            "out with skip when the player should not take it.") in route["text"]
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
    assert [t for t in titles(route) if t.startswith("Clerk")] == ["Clerk 1", "Clerk 2", "Clerk 3", "Clerk 4"]
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
