"""Routes walked from start to end (tests/journey.py): the real add-on and the real planner, a player who follows the
arrow. Each journey is a situation from the player's own evenings, or one built to tangle the route: quests taken and
turned in on the way, two NPCs side by side, a quest whose giver is also where it is turned in, zones crossed, quests
the player takes that the route did not have.

A journey passes when nothing went wrong on the way (``problems``): nothing done came back on the route, nothing was
on it twice, an update added no quest the player did not take, no stop was a way round nobody walked, the arrow never
got stuck, and every stop for a way between zones stood at the crossing."""

import pytest

from journey import Journey, World

pytest.importorskip("lupa")


def lakeshire() -> tuple[World, dict]:
    """Lakeshire as the player found it at level 18, with Elwynn and Westfall behind it."""
    w, n = World(), {}
    for name, zone, x, y in (("Shawn", "Redridge Mountains", 24.2, 53.6), ("Hilary", "Redridge Mountains", 24.1, 53.6),
                             ("Darcy", "Redridge Mountains", 21.5, 44.3), ("Barkeep Daniels", "Redridge Mountains", 21.4, 44.0),
                             ("Guard Parker", "Redridge Mountains", 10.4, 71.3), ("Wiley the Black", "Redridge Mountains", 21.4, 45.4),
                             ("Verner Osgood", "Redridge Mountains", 25.9, 47.3), ("Chef Breanna", "Redridge Mountains", 17.6, 43.8),
                             ("Deputy Feldon", "Redridge Mountains", 25.6, 60.0), ("Foreman Oslow", "Redridge Mountains", 27.1, 48.6),
                             ("Smith Argus", "Elwynn Forest", 41.7, 65.5), ("Gryan Stoutmantle", "Westfall", 56.3, 47.5),
                             ("Captain Danuvin", "Westfall", 56.4, 47.6)):
        n[name] = w.who(name, zone, x, y)
    n["Great Goretusk"] = w.who("Great Goretusk", "Redridge Mountains", 30.0, 70.0, level=17, more=[(33.0, 72.0), (28.0, 74.0)])
    n["Dire Condor"] = w.who("Dire Condor", "Redridge Mountains", 55.0, 60.0, level=19, more=[(58.0, 62.0)])
    n["Defias Trapper"] = w.who("Defias Trapper", "Westfall", 48.0, 30.0, level=13, more=[(49.0, 31.0), (47.0, 29.0)])
    n["Harvest Watcher"] = w.who("Harvest Watcher", "Westfall", 52.0, 40.0, level=15, more=[(53.0, 41.0)])
    n["Glinting Mud"] = w.thing("Glinting Mud", "Redridge Mountains", 16.8, 53.2, more=[(17.5, 54.0), (18.2, 52.5)])
    n["Sunken Chest"] = w.thing("Sunken Chest", "Redridge Mountains", 41.5, 54.6)
    w.quest(3741, "Hilary's Necklace", 15, n["Shawn"], n["Hilary"], use=[(n["Glinting Mud"], 1)])
    w.quest(129, "A Free Lunch", 15, n["Darcy"], n["Guard Parker"])
    w.quest(118, "The Price of Shoes", 18, n["Verner Osgood"], n["Smith Argus"])
    w.quest(132, "The Defias Brotherhood", 18, n["Wiley the Black"], n["Gryan Stoutmantle"], then=135)
    w.quest(135, "The Defias Brotherhood", 18, n["Gryan Stoutmantle"], n["Gryan Stoutmantle"], kill=[(n["Defias Trapper"], 8)], after=132)
    w.quest(92, "Redridge Goulash", 18, n["Chef Breanna"], kill=[(n["Great Goretusk"], 5), (n["Dire Condor"], 5)])
    w.quest(125, "The Lost Tools", 16, n["Foreman Oslow"], use=[(n["Sunken Chest"], 1)])
    w.quest(244, "Encroaching Gnolls", 16, n["Guard Parker"], n["Deputy Feldon"])
    w.quest(64, "The Forgotten Heirloom", 15, n["Captain Danuvin"], kill=[(n["Harvest Watcher"], 6)])
    return w, n


@pytest.fixture
def start(tmp_path, monkeypatch):
    made = []

    def go(world, at, level=18):
        j = Journey(tmp_path, monkeypatch, world, at, level)
        made.append(j)
        return j
    yield go
    for j in made:
        j.close()


def test_the_evening_in_lakeshire(start):
    """The player's own evening: in the Lakeshire inn with a quest to carry to Westfall; the route takes along what
    costs no way, they walk it to the end. In the game the stops of [Hilary's Necklace] came back when it was turned
    in, the arrow said the flight twice, and an update put every quest giver of the town on the route."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.take(132)
    j.pump(3)
    route = j.plan({"then": [{"zone": "Westfall"}]})
    assert any("Shawn" in t for t in route) and any("Guard Parker" in t for t in route), (route, j.plan_text)
    j.follow()
    assert j.problems == [], j.story()
    # what was taken along is done, each update left the rest of the route alone, and the route is walked to its end
    assert {3741, 129, 132} <= j.done, j.story()
    assert not [s for s in j.stops() if not s["done"]], j.story()
    # an update added only the next part of the chain, after its first part was turned in
    assert [u["new"] for u in j.updates if u["new"]] == [["Defias Trapper", "Gryan Stoutmantle"]], (j.updates, j.story())


def test_a_quest_of_my_own_on_the_way(start):
    """The player takes a quest the route did not have (Redridge Goulash: boars south, condors east). The update puts
    its stops on the route and leaves the rest of the route as it is; nothing else of the town comes with it."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.take(132)
    j.pump(3)
    route = j.plan({"then": [{"zone": "Westfall"}]})
    j.follow(until=lambda j: 3741 in j.done)  # up to the necklace turned in
    left = [t for t in j.titles() if "(done)" not in t]
    j.walk(17.6, 43.8)  # to the chef, off the route
    j.take(92)
    j.pump()
    new = [u["new"] for u in j.updates if u["new"]]
    assert new and set(new[-1]) <= {"Great Goretusk", "Dire Condor", "Chef Breanna"}, (j.updates, j.story())
    assert [t for t in left if t not in j.titles()] == [], (left, j.titles())
    j.follow()
    assert j.problems == [] and 92 in j.done and 132 in j.done, j.story()


def test_a_quest_of_the_route_taken_before_its_stop(start):
    """The player passes Shawn on the way to Darcy and takes the necklace quest at once, out of the route's order.
    The stop to take it is done there and then, and nothing is asked: the route has the rest of that quest."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.plan({})
    j.walk(24.2, 53.6)
    j.take(3741)
    j.pump()
    assert not j.updates, j.updates
    shawn = [s for s in j.stops() if s["title"] == "Shawn"]
    assert shawn and shawn[0]["done"], j.titles()
    j.follow()
    assert j.problems == [] and 3741 in j.done, j.story()


def test_a_quest_dropped_on_the_way(start):
    """The player abandons a quest the route was planned with: its stops leave, the others stay."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.plan({})
    j.follow(until=lambda j: 3741 in j.log)
    del j.log[3741]
    j.note("abandoned [Hilary's Necklace]")
    j._push(b"QUEST_REMOVED", b"QUEST_LOG_UPDATE")
    j.pump()
    assert not [s for s in j.stops() if not s["done"] and any(t["id"] == 3741 for t in s["tasks"])], j.titles()
    j.follow()
    assert [p for p in j.problems if "Hilary" not in p] == [], j.story()


def test_the_player_heads_for_another_stop_first(start):
    """The player clicks a stop further on and goes there first; the route goes on from there, and the stop passed
    by is still on it until it is done."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.take(132)
    j.pump(3)
    j.plan({"then": [{"zone": "Westfall"}]})
    n = next(s["n"] for s in j.stops() if s["title"] == "Shawn")
    j.sim.lua(f"ForeverAI.Map.SetCurrent({n})")
    j.sim.run(1)
    cur = next(s for s in j.stops() if s["current"])
    assert cur["title"] == "Shawn", j.titles()
    j.follow()
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


def darkshore() -> World:
    """Auberdine for a level 14 character, the road south into Ashenvale."""
    w = World()
    n = {}
    for name, x, y in (("Gwennyth Bly'Leggonde", 36.6, 45.6), ("Wizbang Cranktoggle", 37.0, 44.1), ("Buzzbox 827", 36.6, 46.3),
                       ("Jaelysia", 37.9, 44.2), ("Alanndarian Nightsong", 36.8, 44.5), ("Tharnariun Treetender", 38.8, 43.4),
                       ("Terenthis", 39.4, 43.5), ("Barithras Moonshade", 37.3, 43.6), ("Cerellean Whiteclaw", 35.7, 43.7)):
        n[name] = w.who(name, "Darkshore", x, y)
    n["Shindrell"] = w.who("Shindrell Swiftfire", "Ashenvale", 34.7, 48.8)
    n["crawler"] = w.who("Pygmy Tide Crawler", "Darkshore", 36.0, 47.5, level=10, more=[(35.6, 48.4), (36.3, 49.0)])
    n["strider"] = w.who("Foreststrider Fledgling", "Darkshore", 37.8, 46.8, level=12, more=[(38.4, 47.6)])
    n["bear"] = w.who("Rabid Thistle Bear", "Darkshore", 38.9, 53.0, level=14, more=[(39.6, 54.0)])
    n["anaya"] = w.who("Anaya Dawnrunner", "Darkshore", 43.0, 58.5, level=16)
    n["wrathtail"] = w.who("Wrathtail Myrmidon", "Ashenvale", 14.0, 30.0, level=19)
    n["bones"] = w.thing("Beached Sea Creature", "Darkshore", 36.4, 50.9)
    n["camp"] = w.thing("Furbolg Camp", "Darkshore", 39.5, 53.5)
    n["stalk"] = w.thing("Scaber Stalk", "Darkshore", 55.0, 34.0, more=[(56.0, 35.0)])
    w.quest(983, "Buzzbox 827", 12, n["Wizbang Cranktoggle"], n["Buzzbox 827"], kill=[(n["crawler"], 6)])
    w.quest(2118, "Plagued Lands", 14, n["Tharnariun Treetender"], kill=[(n["bear"], 1)])
    w.quest(3524, "Washed Ashore", 14, n["Gwennyth Bly'Leggonde"], use=[(n["bones"], 1)])
    w.quest(1001, "Easy Strider Living", 15, n["Jaelysia"], n["Alanndarian Nightsong"], kill=[(n["strider"], 5)])
    w.quest(984, "How Big a Threat?", 14, n["Terenthis"], use=[(n["camp"], 1)])
    w.quest(947, "Cave Mushrooms", 16, n["Barithras Moonshade"], use=[(n["stalk"], 5)])
    w.quest(963, "For Love Eternal", 17, n["Cerellean Whiteclaw"], kill=[(n["anaya"], 1)])
    w.quest(1010, "The Zoram Strand", 19, n["Shindrell"], kill=[(n["wrathtail"], 8)])
    return w


def test_auberdine_at_level_14(start):
    """Another zone, another level: two quests in the log, the pier full of quest givers. What costs no way is taken
    along, the far ones and the ones three levels up are not, and the route is walked to the end."""
    j = start(darkshore(), ("Darkshore", 36.8, 44.2), level=14)
    j.take(983)
    j.take(2118)
    j.pump(3)
    j.plan({})
    along = {j.world.quests[q]["title"] for q in j.svc._plans().get("default", {}).get("along") or [] if q in j.world.quests}
    assert "Cave Mushrooms" not in along and "For Love Eternal" not in along, (along, j.plan_text)
    j.follow()
    assert j.problems == [] and {983, 2118} <= j.done, j.story()


def test_from_auberdine_into_ashenvale(start):
    """A quest given in the next zone: the route walks to the road out, the arrow leads through the crossing, and the
    quest is taken on the other side."""
    j = start(darkshore(), ("Darkshore", 36.8, 44.2), level=19)
    j.take(2118)
    j.pump(3)
    j.plan({"new_quests": ["The Zoram Strand"], "then": [{"zone": "Ashenvale"}]})
    titles = j.titles()
    assert "To Ashenvale" in titles and titles.index("To Ashenvale") < titles.index("Shindrell Swiftfire"), (titles, j.plan_text)
    j.follow()
    assert j.problems == [] and 1010 in j.done and j.zone == "Ashenvale", j.story()


def stormwind_errands() -> World:
    """Two quests from one NPC, a quest taken and turned in at one NPC with its creatures between, a chain whose next
    part is given by another NPC across town, in Elwynn by Goldshire."""
    w = World()
    marshal = w.who("Marshal Dughan", "Elwynn Forest", 42.1, 65.9)
    remy = w.who("Remy Two Times", "Elwynn Forest", 42.2, 67.1)
    smith = w.who("Smith Argus", "Elwynn Forest", 41.7, 65.5)
    maybell = w.who("Maybell Maclure", "Elwynn Forest", 43.2, 89.6)
    tommy = w.who("Tommy Joe Stonefield", "Elwynn Forest", 34.7, 84.2)
    kobold = w.who("Kobold Vermin", "Elwynn Forest", 40.0, 79.0, level=8, more=[(41.0, 80.0)])
    wolf = w.who("Prowler", "Elwynn Forest", 48.0, 75.0, level=9, more=[(49.0, 76.0)])
    candle = w.thing("Fargodeep Candle", "Elwynn Forest", 39.8, 81.0, more=[(40.2, 82.0), (39.5, 82.5)])
    w.quest(60, "Kobold Candles", 9, remy, kill=[(kobold, 8)], use=[(candle, 1)])
    w.quest(47, "Gold Dust Exchange", 8, remy, marshal, kill=[(kobold, 4)])
    w.quest(33, "Wolves Across the Border", 7, smith, kill=[(wolf, 8)])
    w.quest(106, "Young Lovers", 9, maybell, tommy, then=111)
    w.quest(111, "Speak with Gramma", 9, tommy, maybell, after=106)
    return w


def test_two_quests_from_one_npc_and_a_chain(start):
    j = start(stormwind_errands(), ("Elwynn Forest", 42.0, 66.5), level=9)
    j.plan({})
    titles = j.titles()
    assert "Remy Two Times" in titles, (titles, j.plan_text)
    j.follow()
    assert j.problems == [], j.story()
    assert {60, 47} <= j.done, (j.done, j.story())
    # the chain: taken at Maybell, turned in at Tommy where its next part is taken, that one turned in at Maybell
    if 106 not in j.done:
        j.walk(43.2, 89.6)
        j.take(106)
        j.pump()
        j.follow()
    assert j.problems == [] and {106, 111} <= j.done, j.story()


def test_a_character_too_low_for_the_town(start):
    """Level 12 in Lakeshire: the quests there are three levels above or more; none is taken along by itself, the
    route goes only where the quest log says."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8), level=12)
    j.take(129)
    j.pump(3)
    j.plan({})
    along = [j.world.quests[q]["title"] for q in j.svc._plans().get("default", {}).get("along") or [] if q in j.world.quests]
    assert not [t for t in along if t != "A Free Lunch"], (along, j.plan_text)
    j.follow()
    assert j.problems == [] and 129 in j.done, j.story()


def test_from_elwynn_through_redridge_to_westfall(start):
    """Started in Elwynn: the quest to hand in at Lakeshire first, then Westfall. The route crosses into Redridge,
    does what is there, comes back through Elwynn and goes on."""
    world, _ = lakeshire()
    j = start(world, ("Elwynn Forest", 40.1, 59.0))
    j.take(132)
    j.log[132] = []
    j.take(129)
    j.pump(3)
    titles = j.plan({"then": [{"zone": "Redridge Mountains"}, {"zone": "Westfall"}]})
    assert titles[0] == "To Redridge Mountains", (titles, j.plan_text)
    j.follow()
    assert j.problems == [] and {129, 132} <= j.done and j.zone == "Westfall", j.story()


@pytest.mark.parametrize("seed", range(6))
def test_a_player_who_goes_their_own_way(start, seed):
    """Now and then the player clicks another stop of the list and goes there first. Whatever the order, nothing done
    comes back, the arrow never leads to a stop they cannot do anything at, and the route ends."""
    import random
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.take(132)
    j.pump(3)
    j.plan({"then": [{"zone": "Westfall"}]})
    j.follow(wander=random.Random(seed))
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


def test_the_player_flies_away_with_stops_left_behind(start):
    """Half way through Lakeshire the player flies to Westfall (or takes the hearthstone). The stops left behind still
    wait for their quests: an update keeps them, after what is to do where the player is now."""
    world, _ = lakeshire()
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.take(132)
    j.pump(3)
    j.plan({"then": [{"zone": "Westfall"}]})
    j.follow(until=lambda j: 3741 in j.log and 129 in j.log)
    behind = {s["title"] for s in j.stops() if not s["done"] and s["zone"] == "Redridge Mountains"}
    j.note("flies to Westfall")
    j.place("Westfall", 56.6, 52.6)
    j.sim._wow.fire(b"ZONE_CHANGED_NEW_AREA")
    j.finish(3741)  # (the necklace was found on the way to the flight master)
    j.pump(24)  # (the add-on waits a moment: crossing a border for a moment is not leaving the route)
    still = {s["title"] for s in j.stops() if not s["done"]}
    assert {"Hilary", "Guard Parker"} <= still, (behind, j.titles(), j.story())
    j.follow()
    assert j.problems == [] and {3741, 129, 132} <= j.done, j.story()


# ---------------------------------------------------------------- the real quests of the game
# The quests, NPCs, objects and items of these zones as the quest database the add-on reads in the game has them
# (QuestieDB, read from the installed add-on: tests/questiedb.py). Stormwind is left out: Forever redrew its map.
REAL = ["Redridge Mountains", "Elwynn Forest", "Westfall", "Duskwood"]


def real_world(level):
    import questiedb
    if not questiedb.available():
        pytest.skip("QuestieDB is not installed")
    return World.from_questie(REAL, level)


def test_the_players_own_case_lakeshire_with_its_flight_master(start):
    """The player's evening in Lakeshire on the game's own data: a quest to carry to Sentinel Hill, the flight
    master of Lakeshire not discovered yet, Sentinel Hill's known. The route takes along what costs no way, opens the
    flight path, flies from it, and is walked to the end."""
    world = real_world(18)
    j = start(world, ("Redridge Mountains", 21.8, 44.8))
    j.know("Thor")
    j.take(132)
    j.pump(3)
    route = j.plan({"then": [{"zone": "Westfall"}], "extra": [{"title": "Ariena Stormfeather (flight master)",
                    "zone": "Redridge Mountains", "x": 25.5, "y": 59.4, "note": "open the flight path"}]})
    redridge = [s for s in j.stops() if s["zone"] == "Redridge Mountains"]
    assert redridge[-1]["title"].startswith("Ariena Stormfeather"), (route, j.plan_text)
    assert not any(t.startswith("To Elwynn") for t in route), route  # flown, not walked
    j.follow()
    assert j.problems == [], j.story()
    assert 132 in j.done and j.zone == "Westfall", j.story()


@pytest.mark.parametrize("level,at", [(12, ("Elwynn Forest", 42.0, 66.0)), (15, ("Westfall", 56.0, 47.0)),
                                      (18, ("Redridge Mountains", 21.8, 44.8)), (21, ("Duskwood", 73.8, 44.4))])
def test_a_town_of_the_real_game_from_an_empty_quest_log(start, level, at):
    """An empty quest log in a town of the game: the route is made of what is to do around it, and walked."""
    j = start(real_world(level), at, level=level)
    titles = j.plan({})
    j.follow(steps=80)
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()
    assert titles == [] or j.done, (titles, j.plan_text)


@pytest.mark.parametrize("seed", range(4))
def test_the_real_redridge_the_players_own_way(start, seed):
    """The game's Redridge at level 18, a few quests taken, the player clicking stops of their own now and then."""
    import random
    j = start(real_world(18), ("Redridge Mountains", 21.8, 44.8))
    for qid in (129, 3741, 118):
        j.take(qid)
    j.pump(3)
    j.plan({"then": [{"zone": "Elwynn Forest"}]})
    j.follow(steps=80, wander=random.Random(seed))
    assert j.problems == [], j.story()


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("level,at,then", [(12, ("Elwynn Forest", 42.0, 66.0), []),
                                           (15, ("Westfall", 56.0, 47.0), [{"zone": "Elwynn Forest"}]),
                                           (21, ("Duskwood", 73.8, 44.4), [{"zone": "Redridge Mountains"}])])
def test_a_real_town_quests_taken_and_the_players_own_way(start, level, at, then, seed):
    """In a town of the game: the quests given right there taken first, a route through them and on, the player
    clicking stops of their own now and then."""
    import random
    world = real_world(level)
    j = start(world, at, level=level)
    here = [qid for qid, q in world.quests.items() if world.npcs[q["giver"]]["zone"] == at[0] and not q["after"]
            and j.meters(at[0], at[1:], world.npcs[q["giver"]]["at"][0]) < 150][:3]
    for qid in here:
        j.take(qid)
    j.pump(3)
    j.plan({"then": then})
    j.follow(steps=90, wander=random.Random(seed))
    assert j.problems == [], j.story()


def test_a_long_plan_of_the_real_westfall(start):
    """What the real model asked for at Sentinel Hill ("the quests around here"): twelve quests, thirty stops, more than
    the map holds. Two of them share a title. Every update keeps the stops after the cut, and the route is walked to
    its end."""
    j = start(real_world(15), ("Westfall", 56.0, 47.0), level=15)
    j.know("Thor")
    j.plan({"new_quests": ["The People's Militia", "Patrolling Westfall", "Red Leather Bandanas", "Murloc Gills",
                           "Testing the Wells", "Report to Gryan Stoutmantle", "The Killing Fields", "Harvesting the Harvesters",
                           "Goretusk Liver Pie", "Westfall Stew", "Poor Old Blanchy", "The Forgotten Heirloom"],
            "skip": ["The Defias Brotherhood"]})
    j.follow(steps=90)
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


def test_a_long_plan_of_the_real_goldshire(start):
    """What the real model asked for in Goldshire ("what is here to do"): fourteen quests, two NPCs side by side."""
    j = start(real_world(12), ("Elwynn Forest", 42.0, 66.0), level=12)
    j.plan({"new_quests": ["Manhunt", "The Fargodeep Mine", "A Fishy Peril", "Taming the Beast", "Gold Dust Exchange",
                           "Kobold Candles", "Collecting Kelp", "Rough Wolf Pelts", "A Net Disaster", "Bottles and Baubles",
                           "Goldtooth", "Princess Must Die!", "Stolen Enchanting Supplies", "Delicate Instruments"],
            "first": ["Taming the Beast"], "skip": ["Rare Books"]})
    j.follow(steps=90)
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


# what the real model asked for in Darkshire ("the quests of Darkshire, what pays"): [Jitters' Growling Gut] follows
# [Raven Hill], and the game gives it without it
DARKSHIRE = {"new_quests": ["The Night Watch", "The Valor Family", "Raven Hill", "Deliveries to Sven", "Eight-Legged Menaces",
                            "Wolves at Our Heels", "Jitters' Growling Gut", "Supplies from Darkshire"],
             "skip": ["The Totem of Infliction", "The Hermit", "Look To The Stars", "The Shadowy Figure"]}


def test_the_real_darkshire_jitters_first(start):
    """Found by the real model on the game's own Duskwood: the player went to Jitters first, whose stop was "turn in
    [Raven Hill], then pick up [Jitters' Growling Gut]", and took [Jitters' Growling Gut]. The arrow stayed on
    Jitters, and once that quest was turned in, every update put its pick-up back on the route."""
    j = start(real_world(22), ("Duskwood", 73.8, 44.4), level=22)
    j.know("Felicia Maline", "Thor")
    j.plan(DARKSHIRE)
    n = next(s["n"] for s in j.stops() if s["title"] == "Jitters")
    j.sim.lua(f"ForeverAI.Map.SetCurrent({n})")
    j.sim.run(1)
    j.follow(steps=90)
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()
    assert {5, 163} <= j.done, j.story()


@pytest.mark.parametrize("seed", range(6))
def test_the_real_darkshire_the_players_own_way(start, seed):
    import random
    j = start(real_world(22), ("Duskwood", 73.8, 44.4), level=22)
    j.know("Felicia Maline", "Thor")
    j.plan(DARKSHIRE)
    j.follow(steps=120, wander=random.Random(seed))
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


GOLDSHIRE = {"new_quests": ["Manhunt", "The Fargodeep Mine", "A Fishy Peril", "Taming the Beast", "Gold Dust Exchange",
                            "Kobold Candles", "Collecting Kelp", "Rough Wolf Pelts", "A Net Disaster", "Bottles and Baubles",
                            "Goldtooth", "Princess Must Die!", "Stolen Enchanting Supplies", "Delicate Instruments"],
             "first": ["Taming the Beast"]}
WESTFALL = {"new_quests": ["The People's Militia", "Patrolling Westfall", "Red Leather Bandanas", "Murloc Gills",
                           "Testing the Wells", "Report to Gryan Stoutmantle", "The Killing Fields", "Goretusk Liver Pie",
                           "Harvesting the Harvesters", "Westfall Stew", "Poor Old Blanchy", "The Forgotten Heirloom"]}


@pytest.mark.parametrize("seed", [1, 5])
def test_the_real_goldshire_the_players_own_way(start, seed):
    """What the real model planned in Goldshire, the player clicking stops of their own. Found with these seeds: the
    sources of objectives changing at every update (the stops jumping across the map), a stop matched to another by
    place losing what the plan had for it later, two quests' stops for one creature."""
    import random
    j = start(real_world(12), ("Elwynn Forest", 42.0, 66.0), level=12)
    j.plan(GOLDSHIRE)
    j.follow(steps=120, wander=random.Random(seed))
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


@pytest.mark.parametrize("seed", [0, 1])
def test_the_real_westfall_the_players_own_way(start, seed):
    """What the real model planned at Sentinel Hill, the player clicking stops of their own. Found with these seeds:
    a stop finished by the end of tracking for one of its quests, the arrow held at a stop whose rest waits, a source
    the quest log names no more."""
    import random
    j = start(real_world(15), ("Westfall", 56.0, 47.0), level=15)
    j.know("Thor")
    j.plan(WESTFALL)
    j.follow(steps=120, wander=random.Random(seed))
    assert j.problems == [], j.story()
    assert not [s for s in j.stops() if not s["done"]], j.titles()


def test_a_quest_wanted_first_is_done_on_the_first_visit_and_no_order_puts_a_turn_in_before_its_pick_up(start):
    """Found by a journey with the real model on the game's own Redridge. The player walked off to Westfall with a
    quest to turn in there and a letter for the same NPC still to be picked up in Redridge. Planned again with the
    quest in first and Westfall named last too, the planner left the turn-in for the last visit, in one stop with the
    letter's; the model moved that stop to the front, and the letter was turned in before it was picked up."""
    import threading
    from foreverai.service import TurnContext
    from journey import _Writer
    j = start(real_world(18), ("Redridge Mountains", 21.8, 44.8))
    j.know("Thor")
    j.take(132); j.take(129); j.take(3741)
    j.pump(3)
    j.plan({"then": [{"zone": "Westfall"}], "new_quests": ["Messenger to Westfall"]})
    j.place("Westfall", 62.0, 17.9)  # walked off to Westfall, nothing done
    j.sim._wow.fire(b"ZONE_CHANGED_NEW_AREA")
    j.pump(30)
    args = {"new_quests": ["Messenger to Westfall"], "first": ["The Defias Brotherhood"],
            "then": [{"zone": "Elwynn Forest"}, {"zone": "Redridge Mountains"}, {"zone": "Elwynn Forest"}, {"zone": "Westfall"}]}
    j.plan(args)
    stops = [(s["title"], sorted(f"{t['kind']}:{t.get('id')}" for t in s["tasks"] if t["kind"] != "travel"))
             for s in j.stops() if not s["done"]]
    # the quest wanted first: its own stop, now; the letter: turned in on the way back, after it is picked up
    assert stops[0][0] == "Gryan Stoutmantle" and "turn_in:132" in stops[0][1] and "turn_in:143" not in stops[0][1], stops
    assert stops[-1][0] == "Gryan Stoutmantle" and "turn_in:143" in stops[-1][1] and "turn_in:132" not in stops[-1][1]
    assert j.problems == []
    # an order that puts the letter's turn-in before its pick-up is refused, and says what to do instead
    j.sim.ask("and Gryan first?")
    msg = None
    for _ in range(60):
        j.sim.run(0.5)
        msg = next(iter(j._questions()), None)
        if msg:
            break
    ctx = TurnContext(msg, _Writer(), msg["snapshot"], threading.Event(), [])
    ctx.conv = "order"
    text, err = j.svc.run_tool(ctx, "plan_route", {"new_quests": ["Messenger to Westfall"],
                                                  "then": [{"zone": "Elwynn Forest"}, {"zone": "Redridge Mountains"},
                                                           {"zone": "Elwynn Forest"}, {"zone": "Westfall"}]})
    assert not err, text
    assert [m["title"].split(". ", 1)[-1] for m in ctx.marks][-1] == "Gryan Stoutmantle"  # both turn-ins, at the end
    text, err = j.svc.run_tool(ctx, "edit_route", {"order": ["Gryan Stoutmantle"], "go_to": "Gryan Stoutmantle"})
    assert err and "comes before Magistrate Solomon" in text and "[Messenger to Westfall] is turned in" in text
    assert [m["title"].split(". ", 1)[-1] for m in ctx.marks][-1] == "Gryan Stoutmantle"  # nothing changed
