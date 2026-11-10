"""plan_route: the stops of the quest log in the shortest walking order, each turn-in after its objectives."""

import json
from pathlib import Path

from foreverai.route import plan_route

BASE = json.loads((Path(__file__).resolve().parents[1] / "evals" / "base_snapshot.json").read_text(encoding="utf-8"))


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
        "[City Errand]: turn in to Sister in Darnassus (28.9, 45.8), another zone",
        "[Camping 101: Skinning]: needs a skill, not a place (Raise your skinning skill to 20)",
        "[Lost]: no known place for Thing",
        "[Broken]: failed, abandon it and pick it up again",
    ]


def test_route_of_a_real_quest_log():
    route = plan_route(BASE)
    order = titles(route)
    assert order[0] == "Strigid Screecher" and len(order) == 8
    assert order.index("Dazalar") > order.index("Strigid Screecher")
    assert order.index("Corithras Moonrage") > order.index("Oracle Glade Moonwell")
    assert order.index("Tallonkai Swiftroot") > max(order.index("Befouled Webwood"), order.index("Xethorr the Wicked"))
    assert "Sentinel Eralya Leafshadow" in order
    assert any("Darnassus" in x and "another zone" in x for x in route["left_out"])
    assert sum("needs a skill" in x for x in route["left_out"]) == 2
    assert "elite" in next(s for s in route["stops"] if s["title"] == "Xethorr the Wicked")["note"]
    # no order of these stops is shorter: here the far west (Oracle Glade) is one trip, not two
    assert abs(order.index("Sentinel Eralya Leafshadow") - order.index("Oracle Glade Moonwell")) == 1
    text = route["text"]
    assert text.startswith("Route from 64.1, 68.5 (Teldrassil): 8 stops, about ") and "1. Strigid Screecher (64.2, 69.2)" in text
    assert "Not on the route:" in text


def test_new_quests_only_when_asked():
    s = snap([quest(2, "Letter", status="READY TO TURN IN", turn_in="Postman (Teldrassil 60.0, 50.0)")],
             nearbyQuests=[{"id": 9, "title": "Fresh", "giver": "Elder", "level": 9, "minLevel": 5,
                            "where": "Teldrassil 55.0, 50.0", "distance": "400 m"},
                           {"id": 10, "title": "Elsewhere", "giver": "Dwarf", "level": 9, "where": "Dun Morogh 30.0, 30.0"}])
    assert titles(plan_route(s)) == ["Postman"]
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
