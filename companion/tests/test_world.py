"""The ways between zones: the crossings, boats, trams and portals read from the map add-on the player has, the
flights between the flight masters the character knows, and the cheapest way between two places.

The player's own words: "ułóż route ... i dokładny route z przejściami pomiędzy lokacjami" (the answer was "I have no
ways in the data, so they are not marked"), and "kolejna trasa z dupy, jestem w Stormwind ..."."""

from pathlib import Path

import pytest

from foreverai import ways
from foreverai.route import plan_route

# a few zones in the format of the map add-on's icons file (made up for the test: the places are not the game's)
ICONS = '''
	Leatrix_Maps["Icons"] = {
		--[[Elwynn Forest]] [1429] = {
			{"FlightA", 40.0, 60.0, L["Goldshire"], nil, tATex, nil, nil},
			{"Arrow", 21.0, 79.6, L["Westfall"], nil, arTex, nil, nil, nil, nil, nil, 2.2, 1436},
			{"Arrow", 93.2, 72.3, L["Redridge Mountains"], nil, arTex, nil, nil, nil, nil, nil, 4.7, 1433},
			{"Arrow", 32.2, 49.7, L["Stormwind City"], nil, arTex, nil, nil, nil, nil, nil, 0.6, 1453},
		},
		--[[Redridge Mountains]] [1433] = {
			--{"Arrow", 3.3, 73.1, L["Elwynn Forest"], nil, arTex, nil, nil, nil, nil, nil, 2.1, 1429},
			{"Arrow", 47.3, 14.3, L["Burning Steppes"], nil, arTex, nil, nil, nil, nil, nil, 5.9, 1428},
		},
		--[[Burning Steppes]] [1428] = {
			{"Arrow", 78.3, 77.8, L["Redridge Mountains"], nil, arTex, nil, nil, nil, nil, nil, 3.3, 1433},
			{"Arrow", 31.9, 50.4, L["Loch Modan"], L["Requires Key to Searing Gorge"], arTex, nil, nil, nil, nil, nil, 0.8, 1432},
			{"Arrow", 20.0, 20.0, L["Loch Modan"], nil, arTex, nil, nil, nil, nil, nil, 0.8, 1432},
		},
		--[[Westfall]] [1436] = {
			{"Arrow", 62.0, 17.9, L["Elwynn Forest"], nil, arTex, nil, nil, nil, nil, nil, 5.4, 1429},
		},
		--[[Stormwind City]] [1453] = {
			{"TravelA", 66.6, 34.7, L["Tram to"] .. " " .. L["Tinker Town"] .. ", " .. L["Ironforge"], nil, fATex, nil, nil, nil, nil, nil, 0, 1455},
			{"Arrow", 74.5, 92.3, L["Elwynn Forest"], nil, arTex, nil, nil, nil, nil, nil, 3.8, 1429},
			{"TravelA", 21.8, 56.9, L["Boat to"] .. " " .. L["Auberdine"] .. ", " .. L["Darkshore"], nil, fATex, nil, nil, nil, nil, nil, 0, 1439},
			{"TravelH", 10.0, 10.0, L["Zeppelin to"] .. " " .. L["Orgrimmar"], nil, fHTex, nil, nil, nil, nil, nil, 0, 1439},
		},
		--[[Ironforge]] [1455] = {
			{"TravelA", 73.0, 50.2, L["Tram to"] .. " " .. L["Dwarven District"] .. ", " .. L["Stormwind"], nil, fATex, nil, nil, nil, nil, nil, 0, 1453},
			{"Arrow", 21.9, 77.5, L["Dun Morogh"], nil, arTex, nil, nil, nil, nil, nil, 2.2, 1426},
		},
		--[[Dun Morogh]] [1426] = {
			{"Arrow", 84.3, 31.1, L["Loch Modan"], L["North Gate Pass"], arTex, nil, nil, nil, nil, nil, 0, 1432},
			{"Arrow", 82.2, 53.5, L["Loch Modan"], L["South Gate Pass"], arTex, nil, nil, nil, nil, nil, 5, 1432},
			{"Arrow", 30.5, 34.5, L["Wetlands"], L["You will die!"], arTex, nil, nil, nil, nil, nil, 6.2, 1437},
			{"Arrow", 53.3, 35.1, L["Ironforge"], nil, arTex, nil, nil, nil, nil, nil, 5.4, 1455},
		},
		--[[Loch Modan]] [1432] = {
			{"Arrow", 21.5, 66.2, L["Dun Morogh"], L["South Gate Pass"], arTex, nil, nil, nil, nil, nil, 0.5, 1426},
			{"Arrow", 20.4, 17.4, L["Dun Morogh"], L["North Gate Pass"], arTex, nil, nil, nil, nil, nil, 1.1, 1426},
			{"Arrow", 80.0, 80.0, L["Burning Steppes"], nil, arTex, nil, nil, nil, nil, nil, 1.1, 1428},
		},
		--[[Darkshore]] [1439] = {
			{"TravelA", 30.5, 40.9, L["Boat to"] .. " " .. L["Stormwind City"], nil, fATex, nil, nil, nil, nil, nil, 0, 1453},
			{"TravelA", 32.3, 44.1, L["Boat to"] .. " " .. L["Menethil Harbor"] .. ", " .. L["Wetlands"], nil, fATex, nil, nil, nil, nil, nil, 0, 1437},
		},
		--[[Wetlands]] [1437] = {
			{"TravelA", 4.5, 56.7, L["Boat to"] .. " " .. L["Southshore"], nil, fATex, nil, nil, nil, nil, nil, 0, 1424},
		},
	}
'''
FLIGHTS = ["Thor <Gryphon Master> [flight path known] — Westfall 56.5, 52.6",
           "Dungar Longdrink <Gryphon Master> [flight path known] — Stormwind City 71.3, 72.2",
           "Ariena Stormfeather <Gryphon Master> [flight path not discovered] — Redridge Mountains 25.5, 59.4"]
SIZES = ["Elwynn Forest: 3174 x 2116", "Westfall: 3200 x 2134", "Redridge Mountains: 1985 x 1324",
         "Stormwind City: 1589 x 1059", "Ironforge: 723 x 482", "Dun Morogh: 4503 x 3002", "Loch Modan: 2522 x 1682"]


@pytest.fixture
def world(tmp_path):
    icons = tmp_path / "Leatrix_Maps_Icons.lua"
    icons.write_text(ICONS, encoding="utf-8")
    assert ways.use_icons(icons) == 25
    yield lambda level=18, flights=FLIGHTS: ways.world_for({"player": {"faction": "Alliance", "level": level},
                                                          "flightMasters": flights, "mapSizes": SIZES})
    ways.use_icons(None)


def steps(found):
    return [(s["a"], s["title"], s["b"]) for s in found[1]]


def test_the_icons_file_is_read_line_by_line(world):
    icons = ways.read_icons(ICONS)
    tram = next(i for i in icons if i["zone"] == "Stormwind City" and "Tram" in i["title"])
    assert tram == {"map": 1453, "zone": "Stormwind City", "kind": "Travel", "side": "A", "x": 66.6, "y": 34.7,
                    "title": "Tram to Tinker Town, Ironforge", "note": "", "dest": 1455, "off": False}
    off = next(i for i in icons if i["zone"] == "Redridge Mountains" and i["dest"] == 1429)
    assert off["off"] is True and (off["x"], off["y"]) == (3.3, 73.1)  # a line the add-on has switched off
    assert not any(i["kind"] == "FlightA" for i in icons)  # flight masters come from the game data, with what is known


def test_a_crossing_leads_to_the_crossing_back_and_the_passes_keep_their_names(world):
    w = world()
    by = {(link["a"], link["title"]): link for link in w.links}
    assert by[("Elwynn Forest", "To Redridge Mountains")]["arrive"] == (3.3, 73.1)
    # (the add-on has this line switched off, but Elwynn marks the same crossing from its side: it is a real one)
    assert by[("Redridge Mountains", "To Elwynn Forest")]["arrive"] == (93.2, 72.3)
    # two passes between the same zones: each comes out at its own end
    assert by[("Dun Morogh", "To Loch Modan (North Gate Pass)")]["arrive"] == (20.4, 17.4)
    assert by[("Dun Morogh", "To Loch Modan (South Gate Pass)")]["arrive"] == (21.5, 66.2)
    assert by[("Stormwind City", "Tram to Tinker Town, Ironforge")]["kind"] == "tram"
    # a boat that goes on lands at the harbour it comes to, also when no boat goes straight back
    assert by[("Darkshore", "Boat to Menethil Harbor, Wetlands")]["arrive"] == (4.5, 56.7)
    titles = {link["title"] for link in w.links}
    assert "Zeppelin to Orgrimmar" not in titles  # the other faction's
    assert not any("You will die" in t or "Requires Key" in t for t in titles)  # no way for a player


def test_the_way_from_westfall_to_redridge_is_a_flight_a_gate_and_a_road(world):
    found = world().route("Westfall", (56.3, 47.5), "Redridge Mountains", (21.4, 45.4))
    assert steps(found) == [("Westfall", "Thor (fly to Dungar Longdrink, Stormwind City)", "Stormwind City"),
                            ("Stormwind City", "To Elwynn Forest", "Elwynn Forest"),
                            ("Elwynn Forest", "To Redridge Mountains", "Redridge Mountains")]
    # without the flight the road through Elwynn is walked
    walked = world(flights=[]).route("Westfall", (56.3, 47.5), "Redridge Mountains", (21.4, 45.4))
    assert steps(walked) == [("Westfall", "To Elwynn Forest", "Elwynn Forest"),
                             ("Elwynn Forest", "To Redridge Mountains", "Redridge Mountains")]
    assert walked[0] > found[0]


def test_a_flight_path_the_character_does_not_have_is_no_way(world):
    """"Mam iść do Loch Modan, którego nie mam odblokowane": only a known path is flown."""
    w = world()
    assert not any(link["kind"] == "flight" and "Ariena" in link["title"] for link in w.links)
    assert not any(link["kind"] == "flight" and link["b"] == "Redridge Mountains" for link in w.links)


def test_a_zone_far_above_the_character_is_not_walked_through(world):
    """From Redridge to Loch Modan the straight way leads through the Burning Steppes (level 50): a level 18 goes back
    through Stormwind and the tram; a level 55 walks it."""
    low = world(18).route("Redridge Mountains", (21.4, 45.4), "Loch Modan")
    assert [s["b"] for s in low[1]] == ["Elwynn Forest", "Stormwind City", "Ironforge", "Dun Morogh", "Loch Modan"]
    high = world(55).route("Redridge Mountains", (21.4, 45.4), "Loch Modan")
    assert [s["b"] for s in high[1]] == ["Burning Steppes", "Loch Modan"]
    assert world(18).route("Redridge Mountains", (21.4, 45.4), "Burning Steppes") is not None  # going there is allowed


def test_names_are_taken_as_the_player_and_the_model_write_them(world):
    w = world()
    assert w.name("stormwind") == "Stormwind City" and w.name("Redridge") == "Redridge Mountains"
    assert w.name("Moonglade") == "Moonglade"  # unknown: as given
    assert w.route("Westfall", (50, 50), "Moonglade") is None


QUESTS = [{"id": 65, "title": "The Defias Brotherhood", "level": 18, "status": "READY TO TURN IN",
           "turnIn": "Wiley the Black (Redridge Mountains 21.4, 45.4)"},
          {"id": 353, "title": "Stormpike's Delivery", "level": 15, "status": "READY TO TURN IN",
           "turnIn": "Mountaineer Stormpike (Loch Modan 24.8, 18.4)"}]


def snapshot(zone="Stormwind City", coords="63.5, 73.0"):
    return {"player": {"coords": coords, "zone": zone, "map": zone, "level": 18, "faction": "Alliance"},
            "quests": [dict(q) for q in QUESTS], "flightMasters": FLIGHTS, "mapSizes": SIZES}


def titles(route):
    return [s["title"] for s in route["stops"]]


def test_a_route_into_other_zones_is_the_whole_way_step_by_step(world):
    """The player in Stormwind with a turn-in in Redridge and one in Loch Modan. The answer was: "the ways between the
    maps are not in my data, so they are not marked"."""
    route = plan_route(snapshot(), then=[{"zone": "Redridge Mountains"}, {"zone": "Loch Modan"}])
    assert titles(route) == ["To Elwynn Forest", "To Redridge Mountains", "Wiley the Black", "To Elwynn Forest",
                             "To Stormwind City", "Tram to Tinker Town, Ironforge", "To Dun Morogh",
                             "To Loch Modan (North Gate Pass)", "Mountaineer Stormpike"]
    assert [s["zone"] for s in route["stops"]] == ["Stormwind City", "Elwynn Forest", "Redridge Mountains",
                                                   "Redridge Mountains", "Elwynn Forest", "Stormwind City", "Ironforge",
                                                   "Dun Morogh", "Loch Modan"]
    assert route["stops"][0]["tasks"] == [{"kind": "travel", "zone": "Elwynn Forest"}]  # ticked when the zone is entered
    # every map is measured: "how far is it between the points" has an answer on each
    assert all(isinstance(s["meters"], int) for s in route["stops"])
    assert "on foot" in route["text"].splitlines()[0]
    # what an update plans again from: the zones gone on into, not the ways (those depend on where the player is)
    assert route["targets"] == [{"zone": "Redridge Mountains", "via": None, "title": "", "arrive": None},
                                {"zone": "Loch Modan", "via": None, "title": "", "arrive": None}]


def test_the_zones_are_put_in_the_order_that_costs_the_least_way(world):
    """"Kolejna trasa z dupy": from Westfall the model named Loch Modan before Redridge, with Stormwind between as a
    stop of its own. Redridge lies on the way; Stormwind is only passed through."""
    route = plan_route(snapshot("Elwynn Forest", "85.0, 70.0"),
                       then=[{"zone": "Loch Modan"}, {"zone": "Stormwind"}, {"zone": "Redridge Mountains"}])
    assert [t["zone"] for t in route["targets"]] == ["Redridge Mountains", "Loch Modan"]
    assert titles(route)[:2] == ["To Redridge Mountains", "Wiley the Black"]
    assert "- Stormwind City: nothing to do there" in route["text"]


def test_the_order_the_model_gave_stands_when_it_is_about_as_short(world):
    route = plan_route(snapshot(), then=[{"zone": "Loch Modan"}, {"zone": "Redridge Mountains"}])
    assert [t["zone"] for t in route["targets"]] == ["Loch Modan", "Redridge Mountains"]


def test_stops_the_player_asked_for_are_on_the_route_where_they_cost_least(world):
    """"Przecież prosiłem cię o zaktualizowanie routa o peta", "co z flymasterami o których też prosiłem"."""
    extra = [{"title": "Ariena Stormfeather (flight master)", "zone": "Redridge Mountains", "x": 25.5, "y": 59.4,
              "note": "open the flight path"},
             {"title": "Einris Brightspear (hunter trainer)", "zone": "Stormwind City", "x": 67.4, "y": 36.4, "when": "first"},
             {"title": "Camp", "zone": "Moonglade", "x": 50.0, "y": 50.0}]
    route = plan_route(snapshot(), then=[{"zone": "Redridge Mountains"}], extra=extra)
    assert titles(route) == ["Einris Brightspear (hunter trainer)", "To Elwynn Forest", "To Redridge Mountains",
                             "Ariena Stormfeather (flight master)", "Wiley the Black"]
    flight = route["stops"][3]
    assert flight["extra"] is True and flight["what"] == "open the flight path" and flight["tasks"] == []
    assert "Camp: in Moonglade (50, 50), another zone" in route["left_out"]
    # he is a stop already: not offered as a detour on top
    assert "Flight paths not discovered by the route" not in route["text"]
    # a zone named only for a stop the player asked for is a zone with something to do
    route = plan_route(snapshot("Elwynn Forest", "40.0, 60.0"), skip=["The Defias Brotherhood", "Stormpike's Delivery"],
                       then=[{"zone": "Redridge Mountains"}], extra=extra[:1])
    assert titles(route) == ["To Redridge Mountains", "Ariena Stormfeather (flight master)"]


def test_the_other_zones_with_stops_are_named_with_how_far_they_are(world):
    text = plan_route(snapshot("Westfall", "56.3, 47.5"))["text"]
    assert "Stops wait in Redridge Mountains (1). The way there is known (Thor (fly to Dungar Longdrink, Stormwind City))" in text
    assert "Stops also wait in Loch Modan (1, about " in text and "Name every zone the route should go on into" in text


def test_without_the_map_add_on_the_few_built_in_ways_serve():
    assert ways.use_icons(Path("no such file")) == 0
    w = ways.world_for({})
    found = w.route("Rut'theran Village", (56.3, 92.4), "Teldrassil")
    assert [s["title"] for s in found[1]] == ["Portal to Darnassus", "The gate of Darnassus (approx.)"]
    assert w.route("Westfall", (50, 50), "Redridge Mountains") is None  # the model is asked for the way then


REAL = Path("E:/wow forever/World of Warcraft/_forever_/Interface/AddOns/Leatrix_Maps/Leatrix_Maps_Icons.lua")


@pytest.mark.skipif(not REAL.is_file(), reason="the map add-on is not on this computer")
def test_the_players_own_map_add_on_gives_the_ways_of_this_client():
    try:
        assert ways.use_icons(REAL) > 100
        w = ways.world_for({"player": {"faction": "Alliance", "level": 18}, "flightMasters": FLIGHTS})
        assert steps(w.route("Darkshore", (38.0, 41.0), "Stormwind City")) == [
            ("Darkshore", "Boat to Stormwind City", "Stormwind City")]  # a boat WoW Forever has and Classic had not
        to_loch = w.route("Stormwind City", (63.5, 73.0), "Loch Modan")
        assert [s["b"] for s in to_loch[1]] == ["Ironforge", "Dun Morogh", "Loch Modan"] and to_loch[1][0]["kind"] == "tram"
        back = w.route("Redridge Mountains", (21.4, 45.4), "Loch Modan")
        assert "Burning Steppes" not in [s["b"] for s in back[1]]
    finally:
        ways.use_icons(None)


def test_a_flight_master_opened_on_the_route_is_flown_from(world):
    """The real model took the walk back out of the route by hand: "after Thelsamar you can fly". The planner knows
    it: a flight master the player asked to have on the route is opened there, and the way on is a flight from him."""
    flights = FLIGHTS + ["Thorgrum Borrelson <Gryphon Master> [flight path not discovered] — Loch Modan 33.9, 51.0"]
    s = snapshot()
    s["flightMasters"] = flights
    extra = [{"title": "Thorgrum Borrelson (flight master)", "zone": "Loch Modan", "x": 33.9, "y": 51.0,
              "note": "open the flight path"}]
    route = plan_route(s, then=[{"zone": "Loch Modan"}, {"zone": "Redridge Mountains"}], extra=extra)
    assert titles(route) == ["Tram to Tinker Town, Ironforge", "To Dun Morogh", "To Loch Modan (North Gate Pass)",
                             "Mountaineer Stormpike", "Thorgrum Borrelson (flight master)", "To Elwynn Forest",
                             "To Redridge Mountains", "Wiley the Black"]
    opened = route["stops"][4]
    assert opened["what"] == "open the flight path, then fly to Dungar Longdrink, Stormwind City"
    assert opened["tasks"] == [{"kind": "travel", "zone": "Stormwind City"}]  # one stop: he is ticked when the flight lands
    # The note of the stop comes back from the model with what the planner added to it the last time (it reads it
    # off the route on the map): it is not added a second time. From the game: "open the flight path, then fly to
    # Thor, Westfall, then fly to Thor, Westfall".
    again = [dict(extra[0], note=opened["what"])]
    twice = plan_route(s, then=[{"zone": "Loch Modan"}, {"zone": "Redridge Mountains"}], extra=again)
    assert twice["stops"][4]["what"] == "open the flight path, then fly to Dungar Longdrink, Stormwind City"
    # without him on the route the way back is walked
    walked = plan_route(s, then=[{"zone": "Loch Modan"}, {"zone": "Redridge Mountains"}])
    assert "To Dun Morogh (North Gate Pass)" in titles(walked) and len(walked["stops"]) > len(route["stops"])


def test_a_way_the_player_does_not_have_is_avoided(world):
    """"Mam iść do Loch Modan, którego nie mam odblokowane": the data said the flight path is known."""
    s = snapshot()
    s["flightMasters"] = FLIGHTS + ["Gryth Thurden <Gryphon Master> [flight path known] — Ironforge 55.5, 47.7"]
    assert titles(plan_route(s, then=[{"zone": "Loch Modan"}]))[0] == "Dungar Longdrink (fly to Gryth Thurden, Ironforge)"
    avoided = plan_route(s, then=[{"zone": "Loch Modan"}], avoid=["Gryth Thurden"])
    assert titles(avoided)[0] == "Tram to Tinker Town, Ironforge"
    assert plan_route(s, then=[{"zone": "Loch Modan"}], avoid=["gryth thurden", "Tram"])["stops"][0]["title"] != "Tram to Tinker Town, Ironforge"


def test_a_stop_asked_for_far_above_the_character_is_named_as_such(world, tmp_path):
    s = snapshot("Redridge Mountains", "40.0, 20.0")
    extra = [{"title": "Borgus Stoutarm (flight master)", "zone": "Burning Steppes", "x": 84.3, "y": 68.3}]
    text = plan_route(s, skip=["Stormpike's Delivery"], then=[{"zone": "Burning Steppes"}], extra=extra)["text"]
    assert "Far above the character (level 18): Burning Steppes (a zone for level 50 and up)" in text


def test_the_way_on_starts_where_the_stops_of_a_zone_are(world):
    """A real answer said "plan_route broke with several extra stops": with a flight master opened in each zone the
    planner went back to the zone before to fly from there, and then called itself without end. The way on from a zone
    starts where the player is after its stops."""
    s = snapshot("Westfall", "56.3, 47.5")
    s["flightMasters"] = FLIGHTS + ["Thorgrum Borrelson <Gryphon Master> [flight path not discovered] — Loch Modan 33.9, 51.0"]
    extra = [{"title": "Ariena Stormfeather (flight master)", "zone": "Redridge Mountains", "x": 25.5, "y": 59.4},
             {"title": "Thorgrum Borrelson (flight master)", "zone": "Loch Modan", "x": 33.9, "y": 51.0}]
    route = plan_route(s, then=[{"zone": "Redridge Mountains"}, {"zone": "Loch Modan"}], extra=extra)
    assert titles(route) == ["Thor (fly to Dungar Longdrink, Stormwind City)", "To Elwynn Forest", "To Redridge Mountains",
                             "Wiley the Black", "Ariena Stormfeather (flight master)",
                             "Tram to Tinker Town, Ironforge", "To Dun Morogh", "To Loch Modan (North Gate Pass)",
                             "Mountaineer Stormpike", "Thorgrum Borrelson (flight master)"]
    # Ariena is opened and flown from at once: to Stormwind, where the tram is
    assert route["stops"][4]["what"].endswith("then fly to Dungar Longdrink, Stormwind City")


def test_an_update_from_the_next_zone_does_not_lead_back_through_a_finished_one(world):
    """The plan was Redridge, then Loch Modan. Redridge is done and the player stands in Loch Modan when the quest log
    changes: the route planned again holds Loch Modan only, with no way back to Redridge and out again."""
    s = snapshot("Loch Modan", "30.0, 30.0")
    s["quests"] = [q for q in s["quests"] if q["title"] == "Stormpike's Delivery"]
    route = plan_route(s, then=[{"zone": "Redridge Mountains"}, {"zone": "Loch Modan"}])
    assert titles(route) == ["Mountaineer Stormpike"] and route["targets"] == []
    assert "Redridge Mountains: nothing to do there" in route["text"]


def test_the_zone_the_route_is_to_end_in_stays_with_nothing_to_do_there(world):
    s = snapshot("Elwynn Forest", "40.0, 60.0")
    s["quests"] = []
    route = plan_route(s, then=[{"zone": "Stormwind City"}], end=(71.3, 72.2), end_title="Dungar Longdrink")
    assert titles(route) == ["To Stormwind City"] and [t["zone"] for t in route["targets"]] == ["Stormwind City"]
    # without an end too: the zone a new route is named to end in is where the player goes ("then I go to Redridge":
    # found by the real model on the game's own Duskwood, which put the way there on the map by hand as a place)
    assert titles(plan_route(s, then=[{"zone": "Stormwind City"}])) == ["To Stormwind City"]
    # an update of a route does not lead back into it
    assert titles(plan_route(s, then=[{"zone": "Stormwind City"}], along=[])) == []


def test_a_ride_that_goes_one_way_only_does_not_break_the_order_of_the_zones():
    """A boat from A to C and none back: the order C, B has no way, and is simply never the shortest."""
    from foreverai.route import _travel_order

    def link(a, b, kind="walk"):
        return {"a": a, "via": (50.0, 50.0), "title": f"To {b}", "kind": kind, "b": b, "arrive": (50.0, 50.0)}
    w = ways.World([link("A", "B"), link("B", "A"), link("B", "C", "boat"), link("A", "C", "boat")])
    hops = [{"zone": "C", "via": None, "arrive": None, "title": ""}, {"zone": "B", "via": None, "arrive": None, "title": ""}]
    assert [h["zone"] for h in _travel_order(hops, "A", (10.0, 10.0), w)] == ["B", "C"]
    more = hops + [{"zone": z, "via": None, "arrive": None, "title": ""} for z in ("D", "E", "F", "G")]
    w = ways.World(w.links + [link("C", z) for z in ("D", "E", "F", "G")] + [link(z, "C") for z in ("D", "E", "F", "G")])
    assert sorted(h["zone"] for h in _travel_order(more, "A", (10.0, 10.0), w)) == ["B", "C", "D", "E", "F", "G"]


def test_a_stop_asked_for_at_a_quest_npc_is_one_stop(world):
    """In the game: "stops 1 and 2 are the same NPC, because the tool splits them". A stop asked for where the route
    stops anyway is that stop."""
    extra = [{"title": "Wiley the Black (ask about the chain)", "zone": "Redridge Mountains", "x": 21.4, "y": 45.4,
              "note": "ask about the chain"}]
    route = plan_route(snapshot("Elwynn Forest", "85.0, 70.0"), skip=["Stormpike's Delivery"],
                       then=[{"zone": "Redridge Mountains"}], extra=extra)
    assert titles(route) == ["To Redridge Mountains", "Wiley the Black"]
    stop = route["stops"][1]
    assert stop["what"] == "turn in [The Defias Brotherhood]; ask about the chain" and stop["extra"] is True
    assert stop["asked"] == ["Wiley the Black (ask about the chain)"]


def test_dungeon_entrances_come_with_their_levels(tmp_path):
    icons = tmp_path / "Leatrix_Maps_Icons.lua"
    icons.write_text("""
		--[[Westfall]] [1436] = {
			{"Dungeon", 42.5, 71.7, L["The Deadmines"], L["Dungeon"], dnTex, 17, 26},
			{"Arrow", 62.0, 17.9, L["Elwynn Forest"], nil, arTex, nil, nil, nil, nil, nil, 5.4, 1429},
		},
		--[[Eastern Plaguelands]] [1423] = {
			{"Dungeon", 27.8, 11.6, L["Stratholme"], L["Dungeon"], dnTex, 58, 60}, {"Raid", 33.7, 20.7, L["Naxxramas"], L["Raid"], rdTex, 60, 60},
		},
""", encoding="utf-8")
    try:
        ways.use_icons(icons)
        assert ways.dungeons(level=18) == ["The Deadmines (dungeon, levels 17-26): entrance at Westfall 42.5, 71.7"]
        assert ways.dungeons(zone="eastern plaguelands") == [
            "Stratholme (dungeon, levels 58-60): entrance at Eastern Plaguelands 27.8, 11.6",
            "Naxxramas (raid, levels 60-60): entrance at Eastern Plaguelands 33.7, 20.7"]
        assert ways.dungeons(level=40) == []
    finally:
        ways.use_icons(None)


def test_in_a_city_left_and_come_back_to_quests_are_taken_before_leaving(world):
    """A real answer: the player in Stormwind, the route to Westfall and back. The giver of the dungeon's quests and
    the stop asked for first were "not where the route passes through Stormwind City"; the model set them by hand."""
    s = snapshot()
    s["quests"] = [{"id": 2040, "title": "Underground Assault", "status": "READY TO TURN IN",
                    "turnIn": "Shoni the Shilent (Stormwind City 62.6, 34.1)"}]
    s["nearbyQuests"] = [{"id": 167, "title": "Oh Brother. . .", "giver": "Wilder Thistlenettle", "level": 20,
                          "where": "Stormwind City 40.0, 30.0"}]
    extra = [{"title": "Gunther Weller (repair)", "zone": "Stormwind City", "x": 30.0, "y": 60.0, "when": "first"},
             {"title": "The Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7},
             {"title": "Bank", "zone": "Stormwind City", "x": 35.0, "y": 80.0}]
    route = plan_route(s, new_quests=["Oh Brother. . ."], then=[{"zone": "Westfall"}, {"zone": "Stormwind City"}], extra=extra)
    names = titles(route)
    fly = names.index("Dungar Longdrink (fly to Thor, Westfall)")
    assert set(names[:fly]) == {"Gunther Weller (repair)", "Wilder Thistlenettle"}  # before leaving
    assert names[fly + 1] == "The Deadmines entrance"
    assert names.index("Bank") > names.index("The Deadmines entrance")  # the rest of the city: when the route is back
    assert not any("not where the route passes" in x for x in route["left_out"])


# ------------------------------------------------------------------------------------------------ over water
# Checked on the player's own map data: the lines below are the ones of the real file.
WATER = """
	Leatrix_Maps["Icons"] = {
		--[[Wetlands]] [1437] = {
			{"TravelA", 4.5, 56.7, L["Boat to"] .. " " .. L["Southshore"] .. ", " .. L["Hillsbrad"] .. " " .. L["and onto"] .. " " .. L["Auberdine"] .. ", " .. L["Darkshore"], nil, fATex, nil, nil, nil, nil, nil, 0, 1424},
		},
		--[[Hillsbrad Foothills]] [1424] = {
			{"TravelA", 50.7, 70.4, L["Boat to"] .. " " .. L["Auberdine"] .. ", " .. L["Darkshore"] .. " " .. L["and onto"] .. " " .. L["Menethil Harbor"] .. ", " .. L["Wetlands"], nil, fATex, nil, nil, nil, nil, nil, 0, 1439},
		},
		--[[Darkshore]] [1439] = {
			{"TravelA", 32.3, 44.1, L["Boat to"] .. " " .. L["Menethil Harbor"] .. ", " .. L["Wetlands"] .. " " .. L["and onto"] .. " " .. L["Southshore"] .. ", " .. L["Hillsbrad"], nil, fATex, nil, nil, nil, nil, nil, 0, 1437},
			{"Arrow", 43.3, 94.0, L["Ashenvale"], nil, arTex, nil, nil, nil, nil, nil, 3.1, 1440},
		},
		--[[Ashenvale]] [1440] = {
			{"Arrow", 29.1, 14.7, L["Darkshore"], nil, arTex, nil, nil, nil, nil, nil, 0, 1439},
			{"Arrow", 68.6, 86.8, L["The Barrens"], nil, arTex, nil, nil, nil, nil, nil, 3.1, 1413},
		},
		--[[The Barrens]] [1413] = {
			{"Arrow", 48.8, 5.6, L["Ashenvale"], nil, arTex, nil, nil, nil, nil, nil, 0, 1440},
			{"Arrow", 62.6, 19.2, L["Durotar"], nil, arTex, nil, nil, nil, nil, nil, 4.7, 1411},
			{"Arrow", 44.1, 91.5, L["Thousand Needles"], L["The Great Lift"], arTex, nil, nil, nil, nil, nil, 3.1, 1441},
		},
		--[[Durotar]] [1411] = {
			{"Arrow", 34.2, 42.3, L["The Barrens"], nil, arTex, nil, nil, nil, nil, nil, 1.6, 1413},
			{"Arrow", 45.5, 12.3, L["Orgrimmar"], nil, arTex, nil, nil, nil, nil, nil, 0, 1454},
		},
		--[[Orgrimmar]] [1454] = {
			{"Arrow", 52.4, 83.7, L["Durotar"], nil, arTex, nil, nil, nil, nil, nil, 3.1, 1411},
			{"Arrow", 18.0, 63.0, L["The Barrens"], nil, arTex, nil, nil, nil, nil, nil, 1.6, 1413},
		},
		--[[Thousand Needles]] [1441] = {
			{"Arrow", 32.1, 22.0, L["The Barrens"], L["The Great Lift"], arTex, nil, nil, nil, nil, nil, 0, 1413},
			{"Arrow", 8.9, 11.2, L["Feralas"], nil, arTex, nil, nil, nil, nil, nil, 1.1, 1444},
		},
		--[[Feralas]] [1444] = {
			{"TravelA", 43.1, 42.8, L["Boat to"] .. " " .. L["Feathermoon"] .. ", " .. L["Feralas"], nil, fATex, nil, nil, nil, nil, nil, 0, 1444},
			{"TravelA", 31.0, 39.5, L["Boat to"] .. " " .. L["Feralas"], nil, fATex, nil, nil, nil, nil, nil, 0, 1444},
			{"Arrow", 88.7, 41.1, L["Thousand Needles"], nil, arTex, nil, nil, nil, nil, nil, 4.7, 1441},
		},
	}
"""
WATER_SIZES = ["The Barrens: 9266 x 6178", "Durotar: 4835 x 3223", "Orgrimmar: 1283 x 855", "Feralas: 6355 x 4237",
               "Thousand Needles: 4023 x 2682", "Ashenvale: 5273 x 3515", "Darkshore: 5989 x 3993"]


@pytest.fixture
def water(tmp_path):
    icons = tmp_path / "Leatrix_Maps_Icons.lua"
    icons.write_text(WATER, encoding="utf-8")
    ways.use_icons(icons)
    yield lambda faction="Alliance", flights=(), level=60: ways.world_for(
        {"player": {"faction": faction, "level": level}, "flightMasters": list(flights), "mapSizes": WATER_SIZES})
    ways.use_icons(None)


def test_a_boat_that_goes_on_to_a_second_harbour_is_one_ride(water):
    """Menethil, Southshore, Auberdine and round again: from Menethil to Auberdine the player stays on board. It was
    two stops, "Boat to Southshore" and then, from the same pier, "Boat to Auberdine"."""
    w = water()
    cost, way = w.route("Wetlands", (10.0, 58.0), "Darkshore", (37.0, 44.0))
    assert steps((cost, way)) == [("Wetlands", "Boat to Southshore, Hillsbrad and onto Auberdine, Darkshore "
                                               "(stay on board at the first stop)", "Darkshore")]
    assert way[0]["arrive"] == (32.3, 44.1)  # the pier in Auberdine
    # one harbour on, the plain boat
    assert steps(w.route("Wetlands", (10.0, 58.0), "Hillsbrad Foothills", (50.0, 60.0))) == [
        ("Wetlands", "Boat to Southshore, Hillsbrad and onto Auberdine, Darkshore", "Hillsbrad Foothills")]


def test_an_island_on_the_same_map_is_reached_by_its_boat(water):
    """Feathermoon Stronghold lies on an island of the Feralas map: a stop there was "walked" to across the sea."""
    assert ways.region_of("Feralas", (30.2, 43.3)) == "Feathermoon Stronghold"
    assert ways.region_of("Feralas", (60.0, 45.0)) == "Feralas"
    w = water()
    assert steps(w.route("Feralas", (60.0, 45.0), "Feathermoon Stronghold", (30.2, 43.3))) == [
        ("Feralas", "Boat to Feathermoon, Feralas", "Feathermoon Stronghold")]
    assert steps(w.route("Feathermoon Stronghold", (30.2, 43.3), "Feralas", (60.0, 45.0))) == [
        ("Feathermoon Stronghold", "Boat to Feralas", "Feralas")]
    s = {"player": {"map": "Feralas", "zone": "Feralas", "coords": "60.0, 45.0", "faction": "Alliance", "level": 45},
         "quests": [{"id": 1, "title": "The Ruins of Solarsal", "status": "READY TO TURN IN",
                     "turnIn": "Shandris Feathermoon (Feralas 30.3, 46.2)"}], "mapSizes": WATER_SIZES}
    # the quest data says "Feralas": nothing tells the model that the island is a zone of its own, so a route asked
    # for with no zone named goes on there by itself (it used to say "stops wait there, call plan_route again")
    alone = plan_route(s)
    assert titles(alone) == ["Boat to Feathermoon, Feralas", "Shandris Feathermoon"] and alone["left_out"] == []
    route = plan_route(s, then=[{"zone": "Feathermoon Stronghold"}])
    assert titles(route) == ["Boat to Feathermoon, Feralas", "Shandris Feathermoon"]
    assert route["stops"][1]["zone"] == "Feralas"  # on the map it is still the map of Feralas


def test_no_way_leads_through_the_other_factions_capital(water):
    """From the real data: Stormwind to Feralas came out as "... The Barrens, Durotar, Orgrimmar, The Barrens ...":
    the map add-on shows Orgrimmar's west gate from the city's side only, and a crossing whose far end is not known
    counted as a short walk."""
    alliance = water()
    way = alliance.route("Ashenvale", (30.0, 20.0), "Thousand Needles", (30.0, 25.0))
    assert [s[0] for s in steps(way)] == ["Ashenvale", "The Barrens"]
    assert not any("Orgrimmar" in s for step in steps(way) for s in step)
    # the Horde may walk through its own city, but the unknown far end of that gate is no shortcut either
    horde = water("Horde")
    assert [s[0] for s in steps(horde.route("Ashenvale", (30.0, 20.0), "Thousand Needles", (30.0, 25.0)))] == [
        "Ashenvale", "The Barrens"]
    assert horde.route("Durotar", (45.0, 15.0), "Orgrimmar", (50.0, 60.0)) is not None
    assert alliance.meters("The Barrens", None, (44.1, 91.5)) == pytest.approx(0.3 * 9266)


def test_a_flight_never_crosses_the_sea(water):
    """The flight paths the character knows on the other continent are listed too, marked with it: a flight joins
    two of the same continent."""
    flights = ["Thor <Gryphon Master> [flight path known] — Westfall 56.5, 52.6",
               "Dungar Longdrink <Gryphon Master> [flight path known] — Stormwind City 71.3, 72.2",
               "Caylais Moonfeather <Hippogryph Master> [flight path known] — Darkshore 36.3, 45.6 (on Kalimdor)",
               "Vesprystus <Hippogryph Master> [flight path known] — Teldrassil 58.4, 94.0 (on Kalimdor)"]
    w = water(flights=flights)
    flown = {(link["a"], link["b"]) for link in w.links if link["kind"] == "flight"}
    assert flown == {("Westfall", "Stormwind City"), ("Stormwind City", "Westfall"),
                     ("Darkshore", "Rut'theran Village"), ("Rut'theran Village", "Darkshore")}
    # a flight master opened on the way joins the ones of his own continent
    opened = w.with_flight("Daelyshia", "Ashenvale", (34.4, 48.0), "Kalimdor")
    new = {(link["a"], link["b"]) for link in opened.links if link["kind"] == "flight"} - flown
    assert new == {("Ashenvale", "Darkshore"), ("Darkshore", "Ashenvale"), ("Ashenvale", "Rut'theran Village"),
                   ("Rut'theran Village", "Ashenvale")}


def test_zone_levels_and_boats_from_another_addon(water):
    w = water(level=18)
    assert w.danger("Moonglade", "Felwood") == 0.0  # not in the built-in list
    ways.use_zone_levels({"Moonglade": (55, 60), "Westfall": (9, 20)})
    try:
        assert w.danger("Moonglade", "Felwood") == ways.DANGER and w.danger("Westfall", "Duskwood") == 0.0
    finally:
        ways.use_zone_levels(None)
    # without the map add-on: the boats the other add-on lists
    ways.use_icons(None)
    ways.use_transports({"A": [{"kind": "boat", "seconds": 65, "a": ("Wetlands", (4.5, 56.7), "Menethil Harbor"),
                                "b": ("Darkshore", (32.3, 44.1), "Auberdine")}]})
    try:
        w = ways.world_for({"player": {"faction": "Alliance", "level": 30}})
        assert steps(w.route("Wetlands", (10.0, 58.0), "Darkshore", (37.0, 44.0))) == [
            ("Wetlands", "Boat to Auberdine, Darkshore", "Darkshore")]
        assert ways.world_for({"player": {"faction": "Horde", "level": 30}}).route("Wetlands", None, "Darkshore") is None
    finally:
        ways.use_transports(None)


def test_the_way_to_a_zone_step_by_step(water):
    """"Jak się dostać ze Stormwind do Darnassus? Którym statkiem i skąd": the model named the boat and guessed the
    rest ("tej części nie sprawdziłem"). The app has every step."""
    w = water()
    snap = {"player": {"map": "Wetlands", "zone": "Wetlands", "coords": "10.0, 58.0", "faction": "Alliance", "level": 45}}
    text, failed = ways.describe_way(w, snap, "darkshore")
    assert not failed
    lines = text.splitlines()
    assert lines[0].startswith("The way from Wetlands 10, 58 to Darkshore: 1 step.")
    assert lines[1].startswith("1. Boat to Southshore, Hillsbrad and onto Auberdine, Darkshore (stay on board at the first stop) "
                               "— Wetlands 4.5, 56.7 (boat, ")
    assert lines[1].endswith(" m from the player) -> Darkshore; it ends at Darkshore 32.3, 44.1")
    # an island of a map, by the place asked for
    snap = {"player": {"map": "Feralas", "zone": "Feralas", "coords": "60.0, 45.0", "faction": "Alliance", "level": 45}}
    text, failed = ways.describe_way(w, snap, "Feralas", (30.3, 46.2))
    assert not failed and "1. Boat to Feathermoon, Feralas — Feralas 43.1, 42.8 (boat, " in text
    assert "-> Feathermoon Stronghold; it ends at Feralas 31, 39.5" in text
    assert ways.describe_way(w, snap, "Feralas", (70.0, 50.0)) == (
        "The player is in Feralas already: no crossing or ride is needed.", False)
    assert ways.describe_way(w, snap, "Narnia")[1] is True
    # a harbour named in place of its zone
    there = {"player": {"map": "Darkshore", "zone": "Darkshore", "coords": "37.0, 44.0", "faction": "Alliance", "level": 45}}
    assert ways.describe_way(w, there, "Menethil Harbor")[0].startswith("The way from Darkshore 37, 44 to Wetlands: 1 step.")
    assert "position is not known" in ways.describe_way(w, {"player": {}}, "Darkshore")[0]


# ------------------------------------------------------------------------------------------------ crossings
# The lines of the player's real file for a tunnel with three mouths and for crossings the add-on has switched off.
PASSES = """
	Leatrix_Maps["Icons"] = {
		--[[Felwood]] [1448] = {
			{"Arrow", 65.0, 8.3, L["Winterspring"], L["Timbermaw Hold"], arTex, nil, nil, nil, nil, nil, 5.9, 1452},
			{"Arrow", 54.5, 89.2, L["Ashenvale"], nil, arTex, nil, nil, nil, nil, nil, 3, 1440},
		},
		--[[Moonglade]] [1450] =  {
			{"Arrow", 35.7, 72.4, L["Felwood"] .. ", " .. L["Winterspring"], L["Timbermaw Hold"], arTex, nil, nil, nil, nil, nil, 3, 1448},
		},
		--[[Winterspring]] [1452] = {
			{"Arrow", 27.9, 34.5, L["Felwood"], L["Timbermaw Hold"], arTex, nil, nil, nil, nil, nil, 2.2, 1448},
		},
		--[[Ashenvale]] [1440] = {
			{"Arrow", 55.8, 30.2, L["Felwood"], nil, arTex, nil, nil, nil, nil, nil, 0, 1448},
		},
		--[[Elwynn Forest]] [1429] = {
			{"Arrow", 93.2, 72.3, L["Redridge Mountains"], nil, arTex, nil, nil, nil, nil, nil, 4.7, 1433},
		},
		--[[Redridge Mountains]] [1433] = {
			--{"Arrow", 3.3, 73.1, L["Elwynn Forest"], nil, arTex, nil, nil, nil, nil, nil, 2.1, 1429},
			--{"Arrow", 60.0, 5.0, L["Burning Steppes"], nil, arTex, nil, nil, nil, nil, nil, 2.1, 1428},
		},
		--[[Burning Steppes]] [1428] = {
			{"Arrow", 31.9, 50.4, L["Searing Gorge"], L["Blackrock Mountain"], arTex, nil, nil, nil, nil, nil, 0.8, 1427},
		},
		--[[Searing Gorge]] [1427] = {
			{"Arrow", 33.6, 79.0, L["Burning Steppes"], L["Blackrock Mountain"], arTex, nil, nil, nil, nil, nil, 0.8, 1428},
		},
		--[[Arathi Highlands]] [1417] = {
			{"Arrow", 29.6, 67.5, L["Faldir's Cove"], L["Just follow the path west"], arTex, nil, nil, nil, nil, nil, 1.5, 1417},
			{"Arrow", 20.9, 30.6, L["Hillsbrad Foothills"], nil, arTex, nil, nil, nil, nil, nil, 1.5, 1424},
		},
		--[[Hillsbrad Foothills]] [1424] = {
			{"Arrow", 81.0, 56.1, L["Arathi Highlands"], nil, arTex, nil, nil, nil, nil, nil, 1.5, 1417},
		},
	}
"""


@pytest.fixture
def passes(tmp_path):
    icons = tmp_path / "Leatrix_Maps_Icons.lua"
    icons.write_text(PASSES, encoding="utf-8")
    ways.use_icons(icons)
    yield ways.world_for({"player": {"faction": "Alliance", "level": 60}})
    ways.use_icons(None)


def test_a_tunnel_marked_from_one_side_has_its_far_end_and_its_way_back(passes):
    """Moonglade is reached through Timbermaw Hold, whose mouth Felwood's map marks only as the way to Winterspring:
    the way into Moonglade had no way back, and its far end was not known."""
    by = {(link["a"], link["b"]): link for link in passes.links}
    out = by[("Moonglade", "Felwood")]
    assert out["title"] == "To Felwood (Timbermaw Hold)" and out["arrive"] == (65.0, 8.3)
    back = by[("Felwood", "Moonglade")]
    assert (back["title"], back["via"], back["arrive"]) == ("To Moonglade (Timbermaw Hold)", (65.0, 8.3), (35.7, 72.4))
    assert steps(passes.route("Ashenvale", (50.0, 40.0), "Moonglade", (50.0, 40.0))) == [
        ("Ashenvale", "To Felwood", "Felwood"), ("Felwood", "To Moonglade (Timbermaw Hold)", "Moonglade")]
    # the two zones that mark each other keep their own pairing
    assert by[("Felwood", "Winterspring")]["arrive"] == (27.9, 34.5)


def test_a_crossing_the_addon_switched_off_is_real_when_the_other_side_marks_it(passes):
    """From the real route: "11. To Elwynn Forest (approx.) (Redridge Mountains 3.3, 73.1)". Elwynn marks the same
    crossing from its side, leading there: it is no guess."""
    titles_ = {(link["a"], link["title"]) for link in passes.links}
    assert ("Redridge Mountains", "To Elwynn Forest") in titles_
    # nothing on the other side confirms this one: it stays a guess, and says so
    assert ("Redridge Mountains", "To Burning Steppes (approx.)") in titles_


def test_paths_inside_a_zone_are_told_to_the_model(passes):
    assert ways.paths_inside("Arathi Highlands") == [
        "Faldir's Cove: the path starts at Arathi Highlands 29.6, 67.5 (Just follow the path west)"]
    assert ways.paths_inside("Felwood") == []
    assert not any(link["a"] == link["b"] for link in passes.links)  # a hint, not a way between zones
    s = {"player": {"map": "Arathi Highlands", "zone": "Arathi Highlands", "coords": "45.0, 47.0", "faction": "Alliance",
                    "level": 35},
         "quests": [{"id": 1, "title": "Land Ho!", "status": "READY TO TURN IN",
                     "turnIn": "Shakes O'Breen (Arathi Highlands 32.3, 81.4)"}]}
    assert ("Paths inside Arathi Highlands that the map add-on marks (a stop beyond one is reached from where the path "
            "starts, not straight across): Faldir's Cove: the path starts at Arathi Highlands 29.6, 67.5 "
            "(Just follow the path west).") in plan_route(s)["text"]


def test_a_stop_asked_for_at_a_quest_giver_is_the_quests_stop(world):
    """From the game: the AI named the quests to pick up (new_quests) and also asked for a stop at their giver, "Wilder
    Thistlenettle (pick up Oh Brother, Collecting Mem". The route had two stops there, the first with nothing to wait
    for; the add-on kept that one, and it was done when the player walked up, with no quest taken."""
    s = snapshot()
    s["quests"] = []
    s["nearbyQuests"] = [{"id": 167, "title": "Oh Brother. . .", "giver": "Wilder Thistlenettle", "level": 20,
                          "where": "Stormwind City 70.3, 40.8"},
                         {"id": 168, "title": "Collecting Memories", "giver": "Wilder Thistlenettle", "level": 18,
                          "where": "Stormwind City 70.3, 40.8"}]
    extra = [{"title": "Wilder Thistlenettle (pick up Oh Brother, Collecting Mem", "zone": "Stormwind City", "x": 70.3,
              "y": 40.8, "note": "pick up Oh Brother. . . and Collecting Memories", "when": "first"},
             {"title": "Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7, "when": "last"}]
    route = plan_route(s, new_quests=["Oh Brother. . .", "Collecting Memories"], then=[{"zone": "Westfall"}], extra=extra)
    at_wilder = [st for st in route["stops"] if (st["x"], st["y"]) == (70.3, 40.8)]
    assert len(at_wilder) == 1
    assert at_wilder[0]["title"] == "Wilder Thistlenettle"
    assert [(t["kind"], t["id"]) for t in at_wilder[0]["tasks"]] == [("pick_up", 167), ("pick_up", 168)]
    assert at_wilder[0]["asked"] == ["Wilder Thistlenettle (pick up Oh Brother, Collecting Mem"]
    # the same when the stop is asked for without "first", or a little beside the giver under his name
    for e in ({"title": "Wilder Thistlenettle", "zone": "Stormwind City", "x": 70.3, "y": 40.8},
              {"title": "Wilder Thistlenettle (quests)", "zone": "Stormwind City", "x": 70.8, "y": 41.2}):
        again = plan_route(s, new_quests=["Oh Brother. . .", "Collecting Memories"], then=[{"zone": "Westfall"}], extra=[e])
        assert [st["title"] for st in again["stops"]].count("Wilder Thistlenettle") == 1
        assert not any(st["title"].startswith("Wilder Thistlenettle (") for st in again["stops"])


def test_the_zone_of_the_stop_wanted_last_is_the_last_zone(world):
    """From a trial on the real model: "turn the quest in at Lakeshire, then the Deadmines", asked in Elwynn, with the
    entrance of the Deadmines as the stop to end at. The border of Westfall is nearer than Redridge, and the route
    went to Westfall first and to Lakeshire after it."""
    s = snapshot()
    s["player"].update({"map": "Elwynn Forest", "zone": "Elwynn Forest", "coords": "40.1, 59.0"})
    s["quests"] = [{"id": 132, "title": "The Defias Brotherhood", "level": 18, "status": "READY TO TURN IN",
                    "turnIn": "Wiley the Black (Redridge Mountains 21.4, 45.4)"}]
    s["nearbyQuests"] = []
    end = [{"title": "The Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7, "when": "last"}]
    for then in ([{"zone": "Redridge Mountains"}, {"zone": "Westfall"}], [{"zone": "Westfall"}, {"zone": "Redridge Mountains"}]):
        names = [st["title"] for st in plan_route(s, then=then, extra=end)["stops"]]
        assert names.index("Wiley the Black") < names.index("The Deadmines entrance"), names
        assert names[-1] == "The Deadmines entrance"


def test_a_quest_taken_two_zones_on_is_not_turned_in_before_it_is_taken(world):
    """From a trial on the real model: the player stands in Elwynn, by the smith a letter from Lakeshire is for. The
    route passes Elwynn twice (now, and on the way back from Redridge): the turn-in belongs to the second time."""
    s = snapshot()
    s["player"].update({"map": "Elwynn Forest", "zone": "Elwynn Forest", "coords": "40.1, 59.0"})
    s["quests"] = [{"id": 132, "title": "The Defias Brotherhood", "level": 18, "status": "READY TO TURN IN",
                    "turnIn": "Wiley the Black (Redridge Mountains 21.4, 45.4)"}]
    s["nearbyQuests"] = [
        {"id": 118, "title": "The Price of Shoes", "level": 15, "giver": "Verner Osgood", "where": "Redridge Mountains 25.9, 47.3",
         "turnIn": "Smith Argus (Elwynn Forest 41.7, 65.5)"},
        {"id": 120, "title": "Messenger to Stormwind", "level": 14, "giver": "Magistrate Solomon",
         "where": "Redridge Mountains 24.9, 44.5", "turnIn": "General Marcus Jonathan (Stormwind City 70.7, 80.3)"}]
    end = [{"title": "The Deadmines entrance", "zone": "Westfall", "x": 42.5, "y": 71.7, "when": "last"}]
    route = plan_route(s, new_quests=["The Price of Shoes", "Messenger to Stormwind"], extra=end,
                       then=[{"zone": "Redridge Mountains"}, {"zone": "Elwynn Forest"}, {"zone": "Stormwind City"}, {"zone": "Westfall"}])
    names = [st["title"] for st in route["stops"]]
    assert names.index("Verner Osgood") < names.index("Smith Argus"), names
    # the city is on the route for the letter alone, and the letter is handed in there
    assert names.index("Magistrate Solomon") < names.index("General Marcus Jonathan") < names.index("The Deadmines entrance"), names
    assert not [line for line in route["left_out"] if "Messenger" in line or "Shoes" in line]


def test_a_quest_done_in_other_zones_is_turned_in_where_the_route_started(world):
    """From a trial: "Dry Times" of Lakeshire wants a drink from Duskwood, one from Goldshire, one from Stormwind and
    one from Westfall, and is turned in back in Lakeshire. The route left Redridge and never came back: the turn-in
    was "not where the route passes through Redridge Mountains after that", and the model put it on by hand in a
    second call. The route comes back by itself, after the last zone something of the quest is got in."""
    s = snapshot()
    s["player"].update({"map": "Redridge Mountains", "zone": "Redridge Mountains", "coords": "21.8, 44.8"})
    s["nearbyQuests"] = []
    s["quests"] = [{"id": 116, "title": "Dry Times", "level": 15, "status": "in progress",
                    "objectives": "0/1 Bottle of Moonshine [TO DO]; 0/1 Skin of Sweet Rum [TO DO]",
                    "turnIn": "Barkeep Daniels (Redridge Mountains 21.4, 44.0)",
                    "whereToGet": ["Bottle of Moonshine: sold by Barkeep Hann — Duskwood 73.7, 44.0",
                                   "Skin of Sweet Rum: sold by Barkeep Dobbins — Elwynn Forest 44.0, 65.7"]}]
    route = plan_route(s, then=[{"zone": "Duskwood"}, {"zone": "Elwynn Forest"}])
    names = [st["title"] for st in route["stops"]]
    assert {"Barkeep Hann", "Barkeep Dobbins", "Barkeep Daniels"} <= set(names), (names, route["left_out"])
    assert names.index("Barkeep Daniels") > max(names.index("Barkeep Hann"), names.index("Barkeep Dobbins"))
    assert names[-1] == "Barkeep Daniels" and route["stops"][-1]["zone"] == "Redridge Mountains"
    assert not [line for line in route["left_out"] if "Dry Times" in line]



def test_a_stop_wanted_last_in_the_players_own_zone_comes_after_the_other_zones(world):
    """From the game: the route ended at a stop the player had asked for, in the zone they stood in; they asked to go
    to another zone first and then back. Planned with that zone in then, the stop wanted last came first (it was
    near), and the route ended in the other zone."""
    s = snapshot("Westfall", "56.3, 47.5")
    s["quests"] = []
    extra = [{"title": "The cave", "zone": "Westfall", "x": 42.5, "y": 71.7, "when": "last"},
             {"title": "The smith", "zone": "Elwynn Forest", "x": 41.7, "y": 65.5}]
    route = plan_route(s, then=[{"zone": "Elwynn Forest"}], extra=extra)
    names, zones = titles(route), [st["zone"] for st in route["stops"]]
    assert names[-1] == "The cave" and names.index("The smith") < names.index("The cave"), names
    assert zones[0] == "Westfall" and zones[-1] == "Westfall" and "Elwynn Forest" in zones, list(zip(names, zones))
    # not wanted last: where it costs the least way, before the way out
    extra[0].pop("when")
    assert titles(plan_route(s, then=[{"zone": "Elwynn Forest"}], extra=extra))[0] == "The cave"


def test_a_flight_is_named_by_its_point_on_the_flight_map(world):
    """From the game: the arrow said "Dungar Longdrink (fly to Thor, Westfall)"; the flight map shows "Sentinel Hill,
    Westfall" and no Thor. The game data names the path of a flight master the character knows: the route uses it."""
    named = ["Thor <Gryphon Master> [flight path known: Sentinel Hill, Westfall] — Westfall 56.5, 52.6",
             "Dungar Longdrink <Gryphon Master> [flight path known: Stormwind, Elwynn] — Stormwind City 71.3, 72.2"]
    found = world(flights=named).route("Stormwind City", (60.0, 60.0), "Westfall", (56.3, 47.5))
    assert steps(found) == [("Stormwind City", "Dungar Longdrink (fly to Sentinel Hill, Westfall)", "Westfall")]
    # a path the game data does not name: the flight master and his zone, as before
    found = world().route("Stormwind City", (60.0, 60.0), "Westfall", (56.3, 47.5))
    assert steps(found) == [("Stormwind City", "Dungar Longdrink (fly to Thor, Westfall)", "Westfall")]
    # ... and a flight master opened on the way flies to the named path too
    opened = world(flights=named[:1]).with_flight("Ariena Stormfeather", "Redridge Mountains", (25.5, 59.4))
    assert any(link["title"] == "Ariena Stormfeather (fly to Sentinel Hill, Westfall)" for link in opened.links)
    assert any(link["title"] == "Thor (fly to Ariena Stormfeather, Redridge Mountains)" for link in opened.links)
