"""The route on the map can be looked at and changed: the player opens the list of stops from the strip, takes a stop
off or goes to one next; the AI does the same with its edit_route tool. In the add-on simulator."""

import re

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")

ROUTE = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.",
  marks = { { title = "1. Befouled Webwood", x = 45, y = 40, mob = "Befouled Webwood" },
            { title = "2. Sentinel Glynda", x = 38.1, y = 43.2 },
            { title = "3. Old Chest", x = 41, y = 45 } },
  track = { { name = "Befouled Webwood" } } })"""
SECTION = "local s = ForeverAI.Map.Section() return s and table.concat(s, ' | ') or 'nothing'"


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game").start()
    s.slash("/fai")
    s._wow.player.x, s._wow.player.y = 0.40, 0.40
    s.lua(ROUTE)
    s.run(1)
    yield s
    s.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def shown(sim) -> str:
    return " | ".join(sim.texts())


def stops(sim) -> list[str]:
    """The titles in the section the AI gets, in order."""
    return [re.sub(r" — .*", "", line) for line in text(sim.lua(SECTION)).split(" | ")]


def test_the_strip_opens_the_list_of_stops(sim, tmp_path):
    assert "Route (3)" in shown(sim)
    assert not re.search(r"Sentinel Glynda · \d+ m", shown(sim))  # closed until asked for
    sim.click("list")
    sim.run(1)
    rows = re.findall(r"(\d)\. ([A-Za-z ]+) · (\d+) m", shown(sim))
    assert [(n, title) for n, title, _ in rows] == [("1", "Befouled Webwood"), ("2", "Sentinel Glynda"), ("3", "Old Chest")]
    sim.screenshot(tmp_path / "route.png")
    sim.click("hide")
    sim.run(1)
    assert not re.search(r"Sentinel Glynda · \d+ m", shown(sim))
    assert sim.errors == []


def test_the_player_takes_a_stop_off_and_the_ai_is_told(sim):
    sim.click("list")
    sim.run(1)
    sim.click("x", nth=5)  # the window, the two chips, then the rows: the second stop
    sim.run(1)
    assert "Route (2)" in shown(sim) and not re.search(r"Sentinel Glynda · \d+ m", shown(sim))
    assert re.search(r"2\. Old Chest · \d+ m", shown(sim))  # the stops are numbered as they stand now
    assert stops(sim) == ["1. Befouled Webwood", "2. Old Chest", "Taken off the route by the player: Sentinel Glynda"]
    assert "Tracking Befouled Webwood" in shown(sim)

    sim.click("x", nth=4)  # the stop where the creature is: nothing left to watch for there
    sim.run(1)
    assert "Tracking Befouled Webwood" not in shown(sim)
    assert stops(sim) == ["1. Old Chest", "Taken off the route by the player: Sentinel Glynda, Befouled Webwood"]
    sim.click("x", nth=3)  # the last stop: the list closes with the route
    sim.run(1)
    assert "Route (" not in shown(sim) and text(sim.lua(SECTION)) == "nothing"
    assert sim.errors == []


# a TomTom that remembers where its arrow points
TOMTOM = """TomTom = {}
function TomTom:AddWaypoint(map, x, y, opts) if opts.crazy then self.arrow = opts.title end return { title = opts.title } end
function TomTom:RemoveWaypoint(uid) end
function TomTom:SetCrazyArrow(uid, dist, title) self.arrow = title end
ForeverAI.Map.Restore()"""
ARROW = "return TomTom.arrow"
HEADING = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do if s.current then out[#out + 1] = i .. ' ' .. s.title end end return table.concat(out, ',')"


def test_a_click_on_a_stop_makes_it_the_one_to_go_to(sim):
    """The order stays; the arrow leads to the stop the player chose, and on from there when it is reached."""
    sim.lua(TOMTOM)
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Befouled Webwood" and text(sim.lua(HEADING)) == "1 Befouled Webwood"
    sim.click("list")
    sim.run(1)
    sim.click("2. Sentinel Glynda")
    sim.run(1)
    assert text(sim.lua(ARROW)) == "ForeverAI: 2. Sentinel Glynda" and text(sim.lua(HEADING)) == "2 Sentinel Glynda"
    assert stops(sim) == ["1. Befouled Webwood", "2. Sentinel Glynda", "3. Old Chest",
                          "The player is heading to stop 2 (Sentinel Glynda) now, not to the first one."]
    sim.reload()
    sim.lua(TOMTOM)
    sim.run(3)
    assert text(sim.lua(HEADING)) == "2 Sentinel Glynda"  # kept over a /reload
    sim.slash("/fai")

    sim._wow.player.x, sim._wow.player.y = 0.381, 0.432  # there: the stop is done, the arrow leads to the one after it
    sim.run(3)
    assert stops(sim) == ["1. Befouled Webwood", "2. Sentinel Glynda", "3. Old Chest",
                          "The player is heading to stop 3 (Old Chest) now, not to the first one."]
    assert "2. Sentinel Glynda — Darkshore 38.1, 43.2 (done)" in text(sim.lua(SECTION))  # it stays, with its number
    assert text(sim.lua(ARROW)) == "ForeverAI: 3. Old Chest" and "Route (1/3)" in shown(sim)
    sim._wow.player.x, sim._wow.player.y = 0.41, 0.45  # the last stop reached: back to the first that is left
    sim.run(3)
    assert stops(sim) == ["1. Befouled Webwood", "2. Sentinel Glynda", "3. Old Chest"]
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Befouled Webwood" and "Route (2/3)" in shown(sim)

    sim._wow.player.x, sim._wow.player.y = 0.30, 0.30  # walked on
    sim.click("list")
    sim.run(1)
    sim.click("3. Old Chest · done")  # going back to a stop that is done: it is a stop to do again
    sim.run(1)
    assert text(sim.lua(HEADING)) == "3 Old Chest"
    assert sim.errors == []


LOCKED = "Locked by the player (these stops stay on the route until they unlock them): "


def test_a_locked_stop_stays_until_it_is_unlocked(sim):
    """Reaching it, a new route from the AI, the AI taking it off or clearing the route: a locked stop is still there."""
    sim.click("list")
    sim.run(1)
    sim.click("Lock this stop", nth=3)
    sim.run(1)
    assert stops(sim)[-1] == LOCKED + "Old Chest"
    sim._wow.player.x, sim._wow.player.y = 0.41, 0.45
    sim.run(3)
    assert "3. Old Chest" in stops(sim)  # reached, and still on the route

    answer = "ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = 'Done.', %s })"
    sim.lua(answer % (2, 'marks = { { title = "1. Dazalar", x = 50, y = 40 }, { title = "2. Postman", x = 52, y = 42 } }'))
    assert stops(sim) == ["1. Dazalar", "2. Postman", "3. Old Chest", LOCKED + "Old Chest"]
    sim.lua(answer % (3, 'marks = { { title = "1. Old Chest (Treasure)", x = 41.1, y = 45.1 }, { title = "2. Postman", x = 52, y = 42 } }'))
    assert stops(sim) == ["1. Old Chest (Treasure)", "2. Postman", LOCKED + "Old Chest (Treasure)"]  # the same place, once
    sim.lua(answer % (4, 'route = { remove = { { title = "Old Chest (Treasure)", x = 41.1, y = 45.1 }, { title = "Postman", x = 52, y = 42 } } }'))
    assert stops(sim) == ["1. Old Chest (Treasure)", LOCKED + "Old Chest (Treasure)"]
    sim.lua(answer % (5, "route = { clear = true }"))
    assert stops(sim) == ["1. Old Chest (Treasure)", LOCKED + "Old Chest (Treasure)"]

    sim.run(1)
    sim.click("list")
    sim.run(1)
    sim.click("Locked: this stop stays")  # unlock: the player stands on it, so it is done like any stop
    sim.run(3)
    assert text(sim.lua(SECTION)).endswith("(done)")
    assert sim.errors == []


def test_the_player_can_still_take_a_locked_stop_off(sim):
    sim.lua("ForeverAI.Map.Lock(2, true) ForeverAI.Map.Lock(3, true)")
    sim.lua('ForeverAI.Map.Remove(2, "player")')
    assert stops(sim)[:2] == ["1. Befouled Webwood", "2. Old Chest"]
    sim.lua("ForeverAI.Map.Clear()")  # the x of the route, /fai stop: everything goes
    assert text(sim.lua(SECTION)) == "nothing"


def test_a_locked_stop_keeps_the_arrow(sim):
    """The lock means: this stop, until the player unlocks it or clicks another one. Reaching it changes nothing."""
    sim.lua("ForeverAI.Map.Lock(2, true) ForeverAI.Map.SetCurrent(2)")
    sim._wow.player.x, sim._wow.player.y = 0.381, 0.432
    sim.run(5)
    assert text(sim.lua(HEADING)) == "2 Sentinel Glynda" and len(stops(sim)) == 5
    sim.lua("ForeverAI.Map.SetCurrent(3)")  # the player clicks another stop
    sim.run(3)
    assert text(sim.lua(HEADING)) == "3 Old Chest" and "2. Sentinel Glynda" in stops(sim)


# TomTom takes a waypoint off by itself when the player arrives (its "clear distance"): in the real game the locked
# stop was gone from the map the moment it was reached.
TOMTOM_CLEARS = """TomTom = { live = {} }
function TomTom:AddWaypoint(map, x, y, opts)
  local uid = { title = opts.title, clear = opts.cleardistance }
  self.live[uid] = true
  if opts.crazy then self.arrow = opts.title end
  return uid
end
function TomTom:RemoveWaypoint(uid) self.live[uid] = nil end
function TomTom:IsValidWaypoint(uid) return self.live[uid] == true end
function TomTom:SetCrazyArrow(uid, dist, title) self.arrow = title end
function TEST_arrive() for uid in pairs(TomTom.live) do if uid.clear ~= 0 then TomTom.live[uid] = nil end end TomTom.arrow = nil end
function TEST_lose(title) for uid in pairs(TomTom.live) do if uid.title == title then TomTom.live[uid] = nil end end TomTom.arrow = nil end
function TEST_live() local out = {} for uid in pairs(TomTom.live) do out[#out + 1] = uid.title end table.sort(out) return table.concat(out, ",") end
ForeverAI.Map.Restore()"""


def test_tomtom_does_not_take_a_stop_off_by_itself(sim):
    sim.lua(TOMTOM_CLEARS)
    sim.lua("ForeverAI.Map.Lock(2, true) ForeverAI.Map.SetCurrent(2)")
    sim._wow.player.x, sim._wow.player.y = 0.381, 0.432
    sim.lua("TEST_arrive()")  # TomTom's own arrival: it keeps the add-on's waypoints, which say not to clear them
    sim.run(3)
    live = "ForeverAI: 1. Befouled Webwood,ForeverAI: 2. Sentinel Glynda,ForeverAI: 3. Old Chest"
    assert text(sim.lua("return TEST_live()")) == live
    sim.lua('TEST_lose("ForeverAI: 2. Sentinel Glynda")')  # gone anyway (another add-on, a TomTom command): put back
    sim.run(3)
    assert text(sim.lua("return TEST_live()")) == live and text(sim.lua(ARROW)) == "ForeverAI: 2. Sentinel Glynda"
    assert sim.errors == []


def test_the_ai_edits_the_route(sim):
    edit = "ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = 'Done.', %s })"
    sim.lua(edit % (2, 'route = { remove = { { title = "Sentinel Glynda", x = 38.1, y = 43.2 } } }'))
    sim.run(1)
    assert stops(sim) == ["1. Befouled Webwood", "2. Old Chest"]  # the AI's own change is not "by the player"
    assert "Tracking Befouled Webwood" in shown(sim) and "Route (2)" in shown(sim)

    sim.lua(edit % (3, 'route = { order = { { title = "Old Chest", x = 41, y = 45 }, '
                       '{ title = "Befouled Webwood", x = 45, y = 40 } } }'))
    assert stops(sim) == ["1. Old Chest", "2. Befouled Webwood"]

    sim.lua(edit % (4, 'untrack = { "befouled webwood" }'))
    sim.run(1)
    assert "Tracking Befouled Webwood" not in shown(sim) and "Route (2)" in shown(sim)

    # an edit and an extra stop in one answer: the edit first, then the new stop
    sim.lua(edit % (5, 'route = { remove = { { title = "Old Chest", x = 41, y = 45 } } }, marksReplace = false, '
                       'marks = { { title = "Dazalar", x = 50, y = 40 } }'))
    assert stops(sim) == ["1. Befouled Webwood", "2. Dazalar"]

    sim.lua(edit % (6, "route = { clear = true }"))
    sim.run(1)
    assert text(sim.lua(SECTION)) == "nothing" and "Route (" not in shown(sim)
    assert sim.errors == []


def test_a_new_route_forgets_what_the_player_took_off(sim):
    sim.lua('ForeverAI.Map.Remove(2, "player")')
    assert stops(sim)[-1] == "Taken off the route by the player: Sentinel Glynda"
    sim.reload()
    sim.run(3)
    assert stops(sim)[-1] == "Taken off the route by the player: Sentinel Glynda"  # kept over a /reload
    sim.lua(ROUTE.replace("msgId = 1", "msgId = 9"))
    assert stops(sim) == ["1. Befouled Webwood", "2. Sentinel Glynda", "3. Old Chest"]


def test_the_map_gets_a_place_once(sim):
    """Whatever the answer sends, the add-on does not put a second mark on a stop that is on the route."""
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "One more.", marksReplace = false, '
            'marks = { { title = "4. Sentinel Glynda (The Tower)", x = 38.2, y = 43.3 }, { title = "Dazalar", x = 50, y = 40 } } })')
    assert stops(sim) == ["1. Befouled Webwood", "2. Sentinel Glynda", "3. Old Chest", "4. Dazalar"]


# "The route must not be: you walk to the point and it is ticked off." A stop is there for something to do: an
# objective of a quest, a turn-in, a quest to pick up. It leaves the route when that is done, wherever the player is.
LOG = r"""
TEST_log = {
  { id = 1, title = "The Fronds", objectives = { { text = "0/6 Dewy Lasher Frond", finished = false },
                                                 { text = "Speak with Byancie", finished = false } } },
  { id = 2, title = "The Great Tree Provides", complete = true, objectives = {} },
}
local function quest(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.IsComplete = function(id) local q = quest(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = quest(id) return q and q.objectives or {} end
"""
TASKS = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
  { title = "1. Lasher Sproutling", x = 45, y = 40, tasks = { { kind = "objective", quest = "The Fronds", label = "Dewy Lasher Frond" } } },
  { title = "2. Byancie", x = 38.1, y = 43.2, tasks = { { kind = "turn_in", quest = "The Great Tree Provides" } } },
  { title = "3. Innkeeper Keldamyr", x = 41, y = 45, tasks = { { kind = "pick_up", quest = "Rest and Relaxation" } } },
  { title = "4. The dock", x = 43, y = 47 } } })"""
WAITING = "These stops leave the route when what is to be done there is done, not when the player reaches them: "


DONE = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do if s.done then out[#out + 1] = i end end return table.concat(out, ',')"


def test_a_stop_is_done_when_what_is_to_be_done_there_is_done(sim):
    sim.lua(LOG)
    sim.lua(TASKS)
    assert stops(sim) == ["1. Lasher Sproutling", "2. Byancie", "3. Innkeeper Keldamyr", "4. The dock",
                          WAITING + "Lasher Sproutling, Byancie, Innkeeper Keldamyr"]
    sim._wow.player.x, sim._wow.player.y = 0.45, 0.40  # at the fronds: none collected yet
    sim.run(5)
    assert text(sim.lua(DONE)) == ""
    sim.lua("TEST_log[1].objectives[1].finished = true")  # six of six: this stop is done, the quest is not
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(5)
    assert text(sim.lua(DONE)) == "1" and stops(sim)[-1] == WAITING + "Byancie, Innkeeper Keldamyr"

    sim._wow.player.x, sim._wow.player.y = 0.381, 0.432  # at Byancie: standing there is not turning in
    sim.run(5)
    assert text(sim.lua(DONE)) == "1"
    sim.lua("table.remove(TEST_log, 2)")  # turned in
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,2"

    sim.lua('table.insert(TEST_log, { id = 3, title = "Rest and Relaxation", objectives = {} })')  # taken, from afar
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,2,3" and "Route (3/4)" in shown(sim)
    sim._wow.player.x, sim._wow.player.y = 0.43, 0.47  # a place with nothing to do there is done when it is reached
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,2,3,4" and "Route (4/4)" in shown(sim)
    assert stops(sim) == ["1. Lasher Sproutling", "2. Byancie", "3. Innkeeper Keldamyr", "4. The dock"]  # numbers stay
    sim.lua("ForeverAI.Map.DropDone()")  # the route is updated: the done ones leave
    assert text(sim.lua(SECTION)) == "nothing" and "Route (" not in shown(sim)
    assert sim.errors == []


def test_an_empty_quest_log_after_loading_finishes_no_stop(sim):
    sim.lua(LOG)
    sim.lua(TASKS)
    sim.reload()
    sim.lua("C_QuestLog.GetNumQuestLogEntries = function() return 0 end")  # the game has not filled it yet
    sim.run(6)
    assert len(stops(sim)) == 5
    sim.lua(LOG)
    sim.run(40)
    assert len(stops(sim)) == 5


def test_the_list_says_what_a_stop_waits_for(sim):
    sim.lua(LOG)
    sim.lua(TASKS)
    waits = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[i] = s.waits or '-' end return table.concat(out, ' | ')"
    assert text(sim.lua(waits)) == ("Dewy Lasher Frond for [The Fronds] | turning in [The Great Tree Provides] | "
                                    "picking up [Rest and Relaxation] | -")


TRAVEL = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
  { title = "1. Portal to Darnassus", x = 37.1, y = 44.1, zone = "Darkshore", tasks = { { kind = "travel", zone = "Darnassus" } } },
  { title = "2. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus", tasks = { { kind = "turn_in", quest = "Training the Beast" } } },
  { title = "3. Portal back", x = 30, y = 41, zone = "Darnassus", tasks = { { kind = "travel", zone = "Darkshore" } } } } })"""


def test_the_way_into_a_zone_is_done_when_the_player_is_there(sim):
    """The coordinates of a portal or a boat are rarely exact: standing at them proves nothing, being in the zone does."""
    sim.lua(TOMTOM)
    sim.lua(LOG)
    sim.lua('table.insert(TEST_log, { id = 9, title = "Training the Beast", objectives = {} })')
    sim._wow.player.x, sim._wow.player.y = 0.371, 0.441
    sim.lua(TRAVEL)
    sim.run(5)  # the player stands at the portal's mark
    # nothing is done: the way back leads into the zone the player is in, but they have not left it yet
    assert text(sim.lua(DONE)) == "" and stops(sim)[:3] == ["1. Portal to Darnassus", "2. Jocaste", "3. Portal back"]
    assert text(sim.lua("return ForeverAI.Map.List()[1].waits")) == "arriving in Darnassus"
    sim._wow.player.mapID, sim._wow.player.zone = 1457, "Darnassus"
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.41
    sim.run(5)
    assert text(sim.lua(DONE)) == "1" and text(sim.lua(ARROW)) == "ForeverAI: 2. Jocaste (turn in Training the Beast)"
    sim._wow.player.mapID, sim._wow.player.zone = 1439, "Darkshore"  # back through the portal
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,3" and sim.errors == []


def test_an_edit_orders_a_stop_added_in_the_same_answer(sim):
    sim.lua(TOMTOM)
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Sister Aquinne", x = 28.9, y = 45.8 }, { title = "2. Jocaste", x = 40.4, y = 8.6 } } })""")
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 6, text = "The portal first.", marksReplace = false,
      marks = { { title = "Portal to Darnassus", x = 55.9, y = 89.6 } },
      route = { order = { { title = "Portal to Darnassus", x = 55.9, y = 89.6 }, { title = "Jocaste", x = 40.4, y = 8.6 },
                          { title = "Sister Aquinne", x = 28.9, y = 45.8 } } } })""")
    sim.run(1)
    assert stops(sim) == ["1. Portal to Darnassus", "2. Jocaste", "3. Sister Aquinne"]
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Portal to Darnassus" and sim.errors == []


def test_going_back_to_a_done_stop_keeps_it_until_it_is_reached(sim):
    """"Jak klikam na jakiś, który jest done, to od razu się odznacza i przeskakuje na kolejny": what was to be done
    there is done already, so that cannot be what finishes the stop again. Being there does."""
    sim.lua(TOMTOM)
    sim.lua(LOG)
    sim.lua(TASKS)
    sim.lua("table.remove(TEST_log, 2)")  # [The Great Tree Provides] turned in: Byancie is done
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(5)
    assert text(sim.lua(DONE)) == "2"
    sim.click("list")
    sim.run(1)
    sim.click("2. Byancie · done")
    sim.run(6)
    assert text(sim.lua(DONE)) == "" and text(sim.lua(HEADING)) == "2 Byancie"
    assert text(sim.lua(ARROW)) == "ForeverAI: 2. Byancie (turn in The Great Tree Provides)"
    assert "Byancie" not in stops(sim)[-2] and stops(sim)[-1] == "The player is heading to stop 2 (Byancie) now, not to the first one."

    sim._wow.player.x, sim._wow.player.y = 0.381, 0.432  # there
    sim.run(5)
    assert text(sim.lua(DONE)) == "2" and text(sim.lua(HEADING)) == "3 Innkeeper Keldamyr"

    sim.click("2. Byancie · done")  # clicked while standing on it: it stays until the player has left and come back
    sim.run(6)
    assert text(sim.lua(DONE)) == "" and text(sim.lua(HEADING)) == "2 Byancie"
    sim.click("1. Lasher Sproutling")  # another stop chosen instead: the one gone back to is done as it was
    sim.run(3)
    assert text(sim.lua(DONE)) == "2" and text(sim.lua(HEADING)) == "1 Lasher Sproutling"
    assert sim.errors == []


def test_a_stop_says_in_brackets_what_to_do_there(sim):
    """"To powinno być opisane w punktach, co mam tutaj zrobić": the list is read without the answer above it."""
    sim.lua(LOG)
    sim.lua('table.insert(TEST_log, { id = 7, title = "Twisted Hatred", objectives = { { text = "0/8 Befouled Webwood", finished = false } } })')
    # (the two quests to turn in are in the log: a stop does not say again what is done there already)
    sim.lua('table.insert(TEST_log, { id = 8, title = "Crown of the Earth", complete = true, objectives = {} }) '
            'table.insert(TEST_log, { id = 9, title = "Teldrassil", complete = true, objectives = {} })')
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Lasher Sproutling", x = 45, y = 40, tasks = { { kind = "objective", quest = "The Fronds", label = "Dewy Lasher Frond" } } },
      { title = "2. Befouled Webwood", x = 46, y = 41, tasks = { { kind = "objective", quest = "Twisted Hatred", label = "Befouled Webwood" } } },
      { title = "3. Arch Druid Fandral Staghelm", x = 38.1, y = 43.2, tasks = { { kind = "turn_in", quest = "Crown of the Earth" },
          { kind = "turn_in", quest = "Teldrassil" }, { kind = "pick_up", quest = "Grove of the Ancients" } } },
      { title = "4. Portal to Darnassus", x = 41, y = 45, tasks = { { kind = "travel", zone = "Darnassus" } } },
      { title = "5. Jocaste", x = 42, y = 46, note = "learn Beast Training" },
      { title = "6. The dock", x = 43, y = 47 } } })""")
    # the card under the answer says it too: it is what the player sees without opening the list
    card = shown(sim)
    assert "• 3. Arch Druid Fandral Staghelm (turn in Crown of the Earth, Teldrassil; then pick up Grove of the Ancients)  (Darkshore 38.1, 43.2)" in card
    assert "• 5. Jocaste (learn Beast Training)  (Darkshore 42.0, 46.0)" in card and "• 6. The dock  (Darkshore 43.0, 47.0)" in card
    sim.click("list")
    sim.run(1)
    rows = shown(sim)
    assert re.search(r"1\. Lasher Sproutling \(Dewy Lasher Frond for The Fronds\) · \d+ m", rows)
    assert re.search(r"2\. Befouled Webwood \(for Twisted Hatred\) · \d+ m", rows)  # the creature is the stop's name
    assert re.search(r"3\. Arch Druid Fandral Staghelm \(turn in Crown of the Earth, Teldrassil; then pick up Grove of the Ancients\) · \d+ m", rows)
    assert re.search(r"4\. Portal to Darnassus \(go on to Darnassus\) · \d+ m", rows)
    assert re.search(r"5\. Jocaste \(learn Beast Training\) · \d+ m", rows)  # the AI's own words for a stop it marked
    assert re.search(r"6\. The dock · \d+ m", rows)  # only a place
    # and the arrow says it while the player walks: the stop, then what to do there
    sim.lua(TOMTOM)
    sim.run(3)
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Lasher Sproutling (Dewy Lasher Frond for The Fronds)"
    sim.click("6. The dock")
    sim.run(1)
    assert text(sim.lua(ARROW)) == "ForeverAI: 6. The dock"
    sim.reload()
    sim.slash("/fai")
    sim.click("list")
    sim.run(1)
    assert re.search(r"5\. Jocaste \(learn Beast Training\) · \d+ m", shown(sim))
    assert sim.errors == []


def test_a_way_into_a_zone_the_game_does_not_know_is_only_a_place(sim):
    """A name the game has no map for cannot be entered: the stop must not wait for that for ever."""
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. The boat", x = 45, y = 40, tasks = { { kind = "travel", zone = "Stormwind" } } },
      { title = "2. Portal to Darnassus", x = 46, y = 41, tasks = { { kind = "travel", zone = "darnassus" } } } } })""")
    waits = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[i] = s.waits or '-' end return table.concat(out, ' | ')"
    assert text(sim.lua(waits)) == "- | arriving in Darnassus"  # the zone as the game names it
    sim._wow.player.x, sim._wow.player.y = 0.45, 0.40
    sim.run(5)
    assert text(sim.lua(DONE)) == "1"
    sim._wow.player.mapID, sim._wow.player.zone = 1457, "Darnassus"
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,2" and sim.errors == []


def test_a_locked_way_does_not_hold_the_ways_after_it(sim):
    sim.lua(LOG)
    sim.lua('table.insert(TEST_log, { id = 9, title = "Training the Beast", objectives = {} })')
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.lua(TRAVEL)
    sim.lua("ForeverAI.Map.Lock(1, true)")
    sim._wow.player.mapID, sim._wow.player.zone = 1457, "Darnassus"
    sim.run(5)
    assert text(sim.lua(DONE)) == ""  # locked: it stays on the list
    sim._wow.player.mapID, sim._wow.player.zone = 1439, "Darkshore"  # back through the portal
    sim.run(5)
    assert text(sim.lua(DONE)) == "3"  # the way back is taken, although the way out is still on the list
    sim.lua("ForeverAI.Map.Lock(1, false)")
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,3" and sim.errors == []


def test_a_long_description_is_cut_between_characters(sim):
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "x", marks = { { title = "Jocaste", x = 42, y = 46, '
            'note = "a" .. string.rep("\\197\\188", 100) } } })')
    assert sim.lua("return #ForeverAI.Map.List()[1].what") == 119  # not 120: that would be half of a letter
    assert sim.errors == []


CHAIN = r"""
local quests = {
  [940] = { name = "Teldrassil", finishedBy = { { 3516 } }, nextQuestInChain = 952 },
  [952] = { name = "Grove of the Ancients", questLevel = 17, requiredLevel = 10, startedBy = { { 3516 } } },
  [935] = { name = "Crown of the Earth", finishedBy = { { 3516 } }, nextQuestInChain = 936 },
  [936] = { name = "Crown of the Earth", questLevel = 30, requiredLevel = 28, startedBy = { { 3516 } } },
}
local npcs = { [3516] = { name = "Arch Druid Fandral Staghelm", spawns = { [1657] = { { 34.8, 9.3 } } } } }
local db = { GetQuest = function(a, b) return quests[b or a] end, GetNPC = function(a, b) return npcs[b or a] end }
QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db end error("no such module") end }
C_Map.GetAreaInfo = function(id) return id == 1657 and "Darnassus" or nil end
TEST_log = { { id = 940, title = "Teldrassil", complete = true, objectives = {} },
             { id = 935, title = "Crown of the Earth", complete = true, objectives = {} } }
local function quest(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.IsComplete = function(id) local q = quest(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = quest(id) return q and q.objectives or {} end
"""


def test_the_quest_that_follows_a_turn_in_is_known(sim):
    """"Żeby pathing przewidywał, że tutaj będzie quest do wzięcia po oddaniu": the add-on tells which quest follows
    (Questie's chain), when the character can take it; the stop then stays until that quest is taken."""
    sim.lua(CHAIN)
    out = text(sim.lua("""local out = {}
      for _, q in ipairs(ForeverAI.Collect.sections.quests()) do
        if type(q) == "table" then out[#out + 1] = q.title .. " -> " .. tostring(q.next) .. " #" .. tostring(q.nextId) end
      end
      return table.concat(out, " | ")"""))
    assert out == ("Teldrassil -> [Grove of the Ancients] (level 17) from Arch Druid Fandral Staghelm (Darnassus 34.8, 9.3) #952"
                   " | Crown of the Earth -> nil #nil")  # its next part asks for level 28; the character is 24

    # a stop for a turn-in and the quest that follows it; the parts of a chain share a title, so the quest's id counts
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Arch Druid Fandral Staghelm", x = 38.1, y = 43.2, tasks = {
          { kind = "turn_in", quest = "Crown of the Earth", id = 935 }, { kind = "turn_in", quest = "Teldrassil", id = 940 },
          { kind = "pick_up", quest = "Grove of the Ancients", id = 952 } } },
      { title = "2. The dock", x = 43, y = 47 } } })""")
    assert "• 1. Arch Druid Fandral Staghelm (turn in Crown of the Earth, Teldrassil; then pick up Grove of the Ancients)" in shown(sim)
    # both turned in, the next part of the crown taken (the same title, another quest), the grove not yet
    sim.lua('TEST_log = { { id = 936, title = "Crown of the Earth", objectives = {} } }')
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(5)
    assert text(sim.lua(DONE)) == ""
    sim.lua('table.insert(TEST_log, { id = 952, title = "Grove of the Ancients", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(5)
    assert text(sim.lua(DONE)) == "1" and sim.errors == []


# the route the player had in Darnassus: the portal out, then on to Darkshore
CITY_ROUTE = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
  { title = "1. Portal to Rut'theran Village", x = 30, y = 41, zone = "Darnassus" },
  { title = "2. Hippogryph", x = 58.4, y = 94, zone = "Teldrassil" },
  { title = "3. Laird", x = 36.8, y = 44.3, zone = "Darkshore" },
  { title = "4. Onu", x = 43.5, y = 76.3, zone = "Darkshore" } } })"""
ADD = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = "Added.", marksReplace = false,
  marks = { { title = "%s", x = %s, y = %s, zone = "%s" } } })"""
ORDER = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[i] = s.title end return table.concat(out, ', ')"


def in_darnassus(sim):
    sim.lua(TOMTOM)
    sim._wow.player.mapID, sim._wow.player.zone = 1457, "Darnassus"
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.15
    sim.lua(CITY_ROUTE)
    sim.run(1)


def test_a_stop_added_to_a_route_goes_where_it_costs_the_least_way(sim):
    """"Dodał Jocaste jako kolejny punkt, więc wylądowała na końcu listy": the trainer 71 m away came after the flight
    to another continent. It goes where the detour is shortest, and the arrow goes to it when it comes first, also
    when the player had chosen another stop."""
    in_darnassus(sim)
    sim.lua("ForeverAI.Map.SetCurrent(2)")  # the player had chosen the hippogryph
    sim.lua(ADD % (6, "Jocaste (Hunter Trainer)", 40.4, 8.6, "Darnassus"))
    sim.run(1)
    assert text(sim.lua(ORDER)) == "Jocaste (Hunter Trainer), Portal to Rut'theran Village, Hippogryph, Laird, Onu"
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Jocaste (Hunter Trainer)" and text(sim.lua(HEADING)) == "1 Jocaste (Hunter Trainer)"
    # a stop in Darkshore goes among the stops there, where it fits best; the arrow stays where it was
    sim.lua(ADD % (7, "Gwennyth", 36.6, 45.6, "Darkshore"))
    sim.run(1)
    assert text(sim.lua(ORDER)) == "Jocaste (Hunter Trainer), Portal to Rut'theran Village, Hippogryph, Laird, Gwennyth, Onu"
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Jocaste (Hunter Trainer)"
    # one far from everything on the route goes at its end
    sim.lua(ADD % (8, "Astranaar", 36.5, 49.9, "Ashenvale"))
    sim.run(1)
    assert text(sim.lua(ORDER)).endswith("Gwennyth, Onu, Astranaar") and sim.errors == []


def test_the_ai_sends_the_player_to_a_stop(sim):
    in_darnassus(sim)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 6, text = "Onu first.", goTo = { title = "Onu", x = 43.5, y = 76.3 } })')
    sim.run(1)
    assert text(sim.lua(HEADING)) == "4 Onu" and text(sim.lua(ARROW)) == "ForeverAI: 4. Onu"
    # a new order from the AI starts from its first stop, whatever the player had chosen
    sim.lua("ForeverAI.Map.SetCurrent(3)")
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 7, text = "Laird first.", route = { order = { '
            '{ title = "Laird", x = 36.8, y = 44.3 } } } })')
    sim.run(1)
    assert text(sim.lua(ORDER)).startswith("Laird, ") and text(sim.lua(HEADING)) == "1 Laird"
    # the stop it sends the player to can be one this answer adds
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 8, text = "The trainer now.", marksReplace = false, '
            'marks = { { title = "Jocaste", x = 40.4, y = 8.6, zone = "Darnassus" } }, '
            'goTo = { title = "Jocaste", x = 40.4, y = 8.6 } })')
    sim.run(1)
    assert text(sim.lua(ARROW)).endswith(". Jocaste") and "Jocaste" in text(sim.lua(HEADING)) and sim.errors == []


# Questie with three flight masters (two in Darkshore, one in Ashenvale) and a vendor; the taxi map of the game as it
# is while a flight master is open
FLIGHT = r"""
local npcs = {
  [1] = { name = "Caylais Moonfeather", sub = "Hippogryph Master", flags = 8192, zone = 148, at = { 36.3, 45.6 } },
  [2] = { name = "Laird", sub = "Fish Vendor", flags = 128, zone = 148, at = { 36.8, 44.3 } },
  [3] = { name = "Daelyshia", sub = "Hippogryph Master", flags = 8192, zone = 331, at = { 34.4, 48.0 } },
}
local db = { NPCPointers = { [1] = true, [2] = true, [3] = true }, npcFlags = { FLIGHT_MASTER = 8192, VENDOR = 128 } }
function db.QueryNPCSingle(id, field)
  local n = npcs[id]
  if not n then return nil end
  if field == "name" then return n.name elseif field == "subName" then return n.sub
  elseif field == "npcFlags" then return n.flags elseif field == "zoneID" then return n.zone
  elseif field == "spawns" then return { [n.zone] = { n.at } } elseif field == "friendlyToFaction" then return "A" end
end
local zones = { GetAreaIdByUiMapId = function(_, mapID) return mapID == 1439 and 148 or mapID == 1440 and 331 or nil end,
                GetUiMapIdByAreaId = function(_, area) return area == 148 and 1439 or area == 331 and 1440 or nil end }
QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db elseif name == "ZoneDB" then return zones end error("no module") end }
C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or id == 331 and "Ashenvale" or nil end
TEST_taxi = {}
NumTaxiNodes = function() return #TEST_taxi end
TaxiNodeName = function(i) return TEST_taxi[i] and TEST_taxi[i][1] end
TaxiNodeGetType = function(i) return TEST_taxi[i] and TEST_taxi[i][2] end
local unitName = UnitName
UnitName = function(u) if u == "npc" and TEST_npc then return TEST_npc end return unitName(u) end
"""
FLIGHT_LINE = ("local out = {} for _, l in ipairs(ForeverAI.Npcs.ZoneNPCs() or {}) do if l:find('Hippogryph') then "
               "out[#out + 1] = l end end return table.concat(out, ' | ')")


def test_the_flight_paths_the_character_knows_are_remembered(sim):
    """The game says which flight paths a character knows only while a flight master is open: the add-on remembers
    them then, and the NPC list says of every flight master whether its path is known."""
    sim.lua(FLIGHT)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    # nothing opened yet: the add-on cannot tell
    assert text(sim.lua(FLIGHT_LINE)).startswith("Caylais Moonfeather <Hippogryph Master> [flight master] — Darkshore 36.3, 45.6 (")
    # a flight master opened elsewhere: Auberdine is not among the known paths
    sim.lua('TEST_taxi = { { "Rut\'theran Village, Teldrassil", "CURRENT" }, { "Astranaar, Ashenvale", "NONE" } } TEST_npc = "Vesprystus"')
    sim._wow.fire(b"TAXIMAP_OPENED")
    sim.run(1)
    assert text(sim.lua(FLIGHT_LINE)).startswith("Caylais Moonfeather <Hippogryph Master> [flight master, flight path not discovered] — ")
    # it is opened: known from now on, after a /reload too
    sim.lua('TEST_taxi = { { "Rut\'theran Village, Teldrassil", "REACHABLE" }, { "Auberdine, Darkshore", "CURRENT" } } TEST_npc = "Caylais Moonfeather"')
    sim._wow.fire(b"TAXIMAP_OPENED")
    sim.run(1)
    sim.reload()
    sim.lua(FLIGHT)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    assert text(sim.lua(FLIGHT_LINE)).startswith(
        "Caylais Moonfeather <Hippogryph Master> [flight master, flight path known: Auberdine, Darkshore] — ")
    assert sim.errors == []


def test_in_a_town_only_npcs_with_a_role_are_listed(sim):
    """From the game, Stormwind: of the forty-five NPCs nearest to the player a dozen were Argent Dawn emissaries,
    recruiters and a guard captain: a title, nothing to do there."""
    sim.lua(FLIGHT)
    sim.lua(r"""
      local db = QuestieLoader:ImportModule("QuestieDB")
      db.NPCPointers[9] = true
      local query = db.QueryNPCSingle
      db.QueryNPCSingle = function(id, field)
        if id ~= 9 then return query(id, field) end
        if field == "name" then return "Sentinel Elissa" elseif field == "subName" then return "Captain of the Guard"
        elseif field == "npcFlags" then return 0 elseif field == "zoneID" then return 148
        elseif field == "spawns" then return { [148] = { { 36.5, 45.0 } } } elseif field == "friendlyToFaction" then return "A" end
      end
    """)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    names = "local out = {} for _, l in ipairs(ForeverAI.Npcs.ZoneNPCs() or {}) do out[#out + 1] = l:match('^[^<]+') end return table.concat(out, '|')"
    sim.lua("IsResting = function() return false end")
    assert "Sentinel Elissa" in text(sim.lua(names)) and "Laird" in text(sim.lua(names))
    sim.lua("IsResting = function() return true end")
    assert "Sentinel Elissa" not in text(sim.lua(names)) and "Laird" in text(sim.lua(names))


FLIGHT_MASTERS = "local s = ForeverAI.Collect.sections.flightMasters() return s and table.concat(s, ' | ') or 'nothing'"


def test_the_flight_masters_of_every_zone_are_listed(sim):
    """"A zony, które są obok?": the route goes on into other zones, and a flight master there is worth a word too."""
    sim.lua(FLIGHT)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    assert text(sim.lua(FLIGHT_MASTERS)) == (
        "Daelyshia <Hippogryph Master> [flight path not known yet] — Ashenvale 34.4, 48.0 | "
        "Caylais Moonfeather <Hippogryph Master> [flight path not known yet] — Darkshore 36.3, 45.6")
    sim.lua('TEST_taxi = { { "Auberdine, Darkshore", "CURRENT" } } TEST_npc = "Caylais Moonfeather"')
    sim._wow.fire(b"TAXIMAP_OPENED")
    sim.run(1)
    assert text(sim.lua(FLIGHT_MASTERS)) == (
        "Daelyshia <Hippogryph Master> [flight path not discovered] — Ashenvale 34.4, 48.0 | "
        "Caylais Moonfeather <Hippogryph Master> [flight path known: Auberdine, Darkshore] — Darkshore 36.3, 45.6")
    assert sim.errors == []


def test_flight_paths_known_on_another_continent_are_listed_and_marked(sim):
    """A route from Stormwind to Darkshore flies on from Auberdine: the planner has to know the paths the character
    has there. Of another continent only the known ones are listed, marked with the continent (a flight never crosses
    the sea)."""
    sim.lua(FLIGHT)
    sim.lua(r"""
      local db, zones = QuestieLoader:ImportModule("QuestieDB"), QuestieLoader:ImportModule("ZoneDB")
      local far = { [4] = { name = "Thor", zone = 40, at = { 56.5, 52.6 } },
                    [5] = { name = "Ariena Stormfeather", zone = 44, at = { 30.6, 59.4 } } }
      db.NPCPointers[4], db.NPCPointers[5] = true, true
      local query = db.QueryNPCSingle
      db.QueryNPCSingle = function(id, field)
        local n = far[id]
        if not n then return query(id, field) end
        if field == "name" then return n.name elseif field == "subName" then return "Gryphon Master"
        elseif field == "npcFlags" then return 8192 elseif field == "zoneID" then return n.zone
        elseif field == "spawns" then return { [n.zone] = { n.at } } elseif field == "friendlyToFaction" then return "A" end
      end
      local toMap = zones.GetUiMapIdByAreaId
      zones.GetUiMapIdByAreaId = function(self, area) return area == 40 and 1436 or area == 44 and 1433 or toMap(self, area) end
      local area = C_Map.GetAreaInfo
      C_Map.GetAreaInfo = function(id) return id == 40 and "Westfall" or id == 44 and "Redridge Mountains" or area(id) end
      local info = C_Map.GetMapInfo
      C_Map.GetMapInfo = function(id)
        if id == 1436 or id == 1433 then return { mapID = id, name = "a zone", mapType = 3, parentMapID = 1415 } end
        if id == 1415 then return { mapID = 1415, name = "Eastern Kingdoms", mapType = 2, parentMapID = 947 } end
        return info(id)
      end
    """)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    # nothing known yet: the other continent adds nothing
    assert "Thor" not in text(sim.lua(FLIGHT_MASTERS)) and "Ariena" not in text(sim.lua(FLIGHT_MASTERS))
    sim.lua('TEST_taxi = { { "Sentinel Hill, Westfall", "CURRENT" } } TEST_npc = "Thor"')
    sim._wow.fire(b"TAXIMAP_OPENED")
    sim.run(1)
    assert text(sim.lua(FLIGHT_MASTERS)) == (
        "Daelyshia <Hippogryph Master> [flight path not discovered] — Ashenvale 34.4, 48.0 | "
        "Caylais Moonfeather <Hippogryph Master> [flight path not discovered] — Darkshore 36.3, 45.6 | "
        "Thor <Gryphon Master> [flight path known: Sentinel Hill, Westfall] — Westfall 56.5, 52.6 (on Eastern Kingdoms)")
    assert sim.errors == []


DETOURS = "local s = ForeverAI.Collect.sections.detours() return s and table.concat(s, ' | ') or 'nothing'"
TO_B = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
  { title = "1. Bridge End", x = 50, y = 40 }, { title = "2. Far Away", x = 80, y = 80 } } })"""


def walk(sim, points, fight=False):
    """The player walks through these map points (0-100), one look of the add-on at each."""
    if fight:
        sim.lua("InCombatLockdown = function() return true end")
    for x, y in points:
        sim._wow.player.x, sim._wow.player.y = x / 100, y / 100
        sim.run(2.1)
    sim.lua("InCombatLockdown = function() return false end")


def line(a, b, n=10):
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]


def test_a_way_round_the_player_walked_is_remembered(sim):
    """"Okazuje się, że trzeba się cofnąć, bo jest jakaś góra": the map knows no mountains. Where the player had to go
    round on the way to a stop, the add-on remembers it for every character."""
    sim.lua("ForeverAI.db.detours = nil")
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua(TO_B)
    sim.run(2.1)
    walk(sim, line((40, 40), (45, 48)) + line((45, 48), (50, 40)))  # round the hill, to the stop
    assert text(sim.lua(DONE)) == "1"
    # One walk round proves nothing ("to zwykła górka, mogłem iść bezpośrednio"): it is kept, and counts when the
    # player goes round a second time, on another occasion.
    assert sim.lua("return #ForeverAI.db.detours") == 1 and text(sim.lua(DETOURS)) == "nothing"
    back = 'ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = "x", routePlanned = true, marks = { { title = "1. %s", x = %d, y = 40 } } })'
    sim.lua(back % (6, "Camp", 40))
    sim.run(2.1)
    walk(sim, line((50, 40), (45, 48)) + line((45, 48), (40, 40)))  # back the same way round, a moment later
    assert text(sim.lua(DETOURS)) == "nothing"  # (the same occasion)
    sim.lua("ForeverAI.Map.DETOUR_AGAIN_S = 0")
    sim.lua(back % (7, "Bridge End", 50))
    sim.run(2.1)
    walk(sim, line((40, 40), (45, 48)) + line((45, 48), (50, 40)))
    assert re.fullmatch(r"Darkshore 40\.0, 40\.0 → 50\.0, 40\.0: through 45\.0, 48\.0 \(straight 599 m, round 87\d m\)",
                        text(sim.lua(DETOURS)))
    assert sim.lua("return #ForeverAI.db.detours") == 1  # account-wide: the land is the same for every character
    # walked straight between the two places: there is a way through after all, and the way round is forgotten
    sim.lua(back % (8, "Camp", 40))
    sim.run(2.1)
    walk(sim, line((50, 40), (40, 40)))
    assert text(sim.lua(DETOURS)) == "nothing" and sim.lua("return #ForeverAI.db.detours") == 0


def test_a_way_round_of_the_route_teaches_nothing_and_goes_when_the_player_takes_it_off(sim):
    """From the game: the walk to the stop "The way round" was itself "a way round, remembered". And a way round the
    player does not want ("mogłem iść bezpośrednio") is gone for good when they take it off the route."""
    sim.lua("ForeverAI.Map.DETOUR_AGAIN_S = 0")
    sim.lua("""ForeverAI.db.detours = { { mapID = 1439, zone = "Darkshore", a = { 60, 60 }, b = { 70, 60 }, via = { 65, 68 },
                                          straight = 599, round = 870, at = 0, n = 2 } }""")
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "x", routePlanned = true, marks = { '
            '{ title = "1. The way round", x = 50, y = 40 }, { title = "2. The way round", x = 65, y = 68 } } })')
    sim.run(2.1)
    walk(sim, line((40, 40), (45, 48)) + line((45, 48), (50, 40)))  # round, to a stop that is a way round itself
    assert text(sim.lua(DONE)) == "1" and sim.lua("return #ForeverAI.db.detours") == 1  # nothing learned from it
    assert "through 65.0, 68.0" in text(sim.lua(DETOURS))
    sim.lua('ForeverAI.Map.Remove(2, "player")')
    assert text(sim.lua(DETOURS)) == "nothing" and sim.errors == []


def test_wandering_and_fighting_are_not_ways_round(sim):
    sim.lua("ForeverAI.db.detours = nil")
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua(TO_B)
    sim.run(2.1)
    # to and fro (after a creature, say): much longer than the straight way, but not one way round
    walk(sim, line((40, 40), (44, 46), 6) + line((44, 46), (41, 41), 6) + line((41, 41), (46, 47), 6) + line((46, 47), (50, 40), 6))
    assert text(sim.lua(DONE)) == "1" and text(sim.lua(DETOURS)) == "nothing"
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 6, text = "x", routePlanned = true, marks = { { title = "1. C", x = 60, y = 40 } } })')
    sim.run(2.1)
    walk(sim, line((50, 40), (55, 48)) + line((55, 48), (60, 40)), fight=True)  # a fight on the way: no telling why
    assert text(sim.lua(DETOURS)) == "nothing" and sim.errors == []


OBJECT_QUEST = r"""
local quests = { [983] = { name = "Buzzbox 827", startedBy = { { 3666 } }, finishedBy = { nil, { 777 } } } }
local npcs = { [3666] = { name = "Wizbang Cranktoggle", spawns = { [148] = { { 37.0, 44.1 } } } } }
local objects = { [777] = { name = "Buzzbox 827", spawns = { [148] = { { 41.9, 28.7 } } } } }
local db = { GetQuest = function(a, b) return quests[b or a] end, GetNPC = function(a, b) return npcs[b or a] end,
             GetObject = function(a, b) return objects[b or a] end }
QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db end error("no module") end }
C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or nil end
TEST_log = { { id = 983, title = "Buzzbox 827", complete = true, objectives = { { text = "6/6 Crawler Leg", finished = true } } } }
local function quest(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.IsComplete = function(id) local q = quest(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = quest(id) return q and q.objectives or {} end
"""


def test_a_quest_turned_in_at_an_object_has_a_place(sim):
    """[Buzzbox 827] is turned in at a box, not at an NPC: the planner had no place for it, and the AI marked one by
    hand every time."""
    sim.lua(OBJECT_QUEST)
    out = text(sim.lua("""local q = ForeverAI.Collect.sections.quests()[2]
      return tostring(q.questGiver) .. " | " .. tostring(q.turnIn)"""))
    assert out == "Wizbang Cranktoggle (Darkshore 37.0, 44.1) | Buzzbox 827 (Darkshore 41.9, 28.7)"
    assert sim.errors == []


FLIGHT_MASTERS = "return table.concat(ForeverAI.Npcs.FlightMasters() or {}, ' | ')"


def test_a_flight_path_is_known_only_when_the_character_can_fly_it(sim):
    """"Mam iść do Loch Modan, którego nie mam odblokowane": this client's old functions list every node of the
    continent, none of them as NONE. Eighteen paths looked known; the flight map showed three."""
    sim.lua(FLIGHT)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    sim.lua('TEST_taxi = { { "Auberdine, Darkshore", "CURRENT" }, { "Astranaar, Ashenvale", "DISTANT" } } TEST_npc = "Caylais Moonfeather"')
    sim._wow.fire(b"TAXIMAP_OPENED")
    sim.run(1)
    lines = text(sim.lua(FLIGHT_MASTERS))
    assert "Caylais Moonfeather <Hippogryph Master> [flight path known: Auberdine, Darkshore] — Darkshore 36.3, 45.6" in lines
    assert "Daelyshia <Hippogryph Master> [flight path not discovered] — Ashenvale 34.4, 48.0" in lines
    # the flight map of the game itself, when the client has it: what it marks unreachable is not known
    sim.lua("""
      GetTaxiMapID = function() return 1414 end
      C_TaxiMap.GetAllTaxiNodes = function() return { { name = "Auberdine, Darkshore", state = 0 },
        { name = "Astranaar, Ashenvale", state = 1 }, { name = "Nijel's Point, Desolace", state = 2 } } end
    """)
    sim._wow.fire(b"TAXIMAP_OPENED")
    sim.run(1)
    assert ("Daelyshia <Hippogryph Master> [flight path known: Astranaar, Ashenvale] — Ashenvale 34.4, 48.0"
            in text(sim.lua(FLIGHT_MASTERS)))
    assert sim.to_py(sim.lua("return ForeverAI.char.taxi.known")) == {"Auberdine, Darkshore": True, "Astranaar, Ashenvale": True}
    assert "taxi: flight map 1414: Auberdine, Darkshore=0, Astranaar, Ashenvale=1, Nijel's Point, Desolace=2" in \
        text(sim.lua("return table.concat(ForeverAI.DebugLog.Lines(5), ' | ')"))


def test_what_an_older_version_remembered_of_flight_paths_counts_for_nothing(sim):
    """The saved data of the player's character: every node of the Eastern Kingdoms "known". Only the flight masters
    the character has stood at are kept."""
    sim.lua(FLIGHT)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    sim.lua("""ForeverAI.char.taxi = { at = 1794515841, masters = { ["Caylais Moonfeather"] = "Auberdine, Darkshore" },
      known = { ["Auberdine, Darkshore"] = true, ["Astranaar, Ashenvale"] = true, ["Thelsamar, Loch Modan"] = true } }""")
    lines = text(sim.lua(FLIGHT_MASTERS))
    assert "Caylais Moonfeather <Hippogryph Master> [flight path known: Auberdine, Darkshore]" in lines
    assert "Daelyshia <Hippogryph Master> [flight path not discovered]" in lines
    assert sim.to_py(sim.lua("return ForeverAI.char.taxi.known")) == {"Auberdine, Darkshore": True}
    sim.reload()
    assert sim.lua("return ForeverAI.char.taxi.v") == 2


SERVICES = r"""
local npcs = {
  [1] = { name = "Einris Brightspear", sub = "Hunter Trainer", flags = 16, at = { 61.0, 16.0 } },
  [2] = { name = "Far Hunter Trainer", sub = "Hunter Trainer", flags = 16, at = { 10.0, 90.0 } },
  [3] = { name = "Gunther Weller", sub = "Weapons Merchant", flags = 128 + 4096, at = { 37.0, 44.6 } },
  [4] = { name = "Innkeeper Allison", sub = "Innkeeper", flags = 128 + 65536, at = { 37.2, 44.0 } },
  [5] = { name = "Caylais Moonfeather", sub = "Hippogryph Master", flags = 8192, at = { 36.3, 45.6 } },
  [6] = { name = "Frederick Stover", sub = "Bow & Arrow Merchant", flags = 128, at = { 38.0, 43.0 } },
  [7] = { name = "Simon Tanner", sub = "Expert Leatherworker", flags = 16, at = { 39.0, 42.0 } },
  [8] = { name = "Jenova Stoneshield", sub = "Stable Master", flags = 4194304, at = { 37.5, 41.5 } },
  [9] = { name = "Mage Lady", sub = "Mage Trainer", flags = 16, at = { 37.1, 44.2 } },
}
local db = { NPCPointers = {}, npcFlags = { FLIGHT_MASTER = 8192, VENDOR = 128, TRAINER = 16, REPAIR = 4096,
                                            INNKEEPER = 65536, STABLEMASTER = 4194304 } }
for id in pairs(npcs) do db.NPCPointers[id] = true end
function db.QueryNPCSingle(id, field)
  local n = npcs[id]
  if not n then return nil end
  if field == "name" then return n.name elseif field == "subName" then return n.sub
  elseif field == "npcFlags" then return n.flags elseif field == "zoneID" then return 148
  elseif field == "spawns" then return { [148] = { n.at } } elseif field == "friendlyToFaction" then return "A" end
end
local zones = { GetAreaIdByUiMapId = function(_, mapID) return mapID == 1439 and 148 or nil end }
QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db elseif name == "ZoneDB" then return zones end error("no module") end }
C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or nil end
"""


def test_the_services_this_character_uses_are_named_with_the_nearest_of_each(sim):
    """"Oznacz mi trenera hunterów": in a city the list of the nearest 45 NPCs is cheese sellers and emissaries; the
    hunter trainer was not in it, and the AI searched the web for where he stands."""
    sim.lua(SERVICES)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim.run(1)
    lines = [text(l) for l in sim.to_py(sim.lua("return ForeverAI.Collect.sections.services()"))]
    kinds = [l.split(":")[0] for l in lines]
    assert kinds == ["hunter trainer", "stable master", "ammunition", "leatherworking trainer", "repair", "innkeeper",
                     "flight master"]
    assert lines[0].startswith("hunter trainer: Einris Brightspear <Hunter Trainer> — Darkshore 61.0, 16.0 (")  # the nearer
    assert lines[2].startswith("ammunition: Frederick Stover <Bow & Arrow Merchant> — Darkshore 38.0, 43.0")
    assert lines[3].startswith("leatherworking trainer: Simon Tanner <Expert Leatherworker>")
    assert "Mage" not in " ".join(lines)  # another class's trainer is nothing to this character


def test_the_arrow_says_a_long_stop_in_one_short_line(sim):
    """From the game: "ForeverAI: 1. Wilder Thistlenettle (pick up [Collecting Memories]; pick up Oh Brother and
    Collecting Memories; pick up [Oh Brother. . .])" in one line across the action bars."""
    sim.lua(TOMTOM)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "x", marks = { { title = "1. Wilder Thistlenettle", '
            'x = 42, y = 46, note = "pick up [Collecting Memories]; pick up Oh Brother and Collecting Memories; pick up [Oh Brother. . .]", '
            'tasks = { { kind = "pick_up", quest = "Collecting Memories", id = 168 }, { kind = "pick_up", quest = "Oh Brother. . .", id = 167 } } } } })')
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. Wilder Thistlenettle (pick up Collecting Memories, Oh Brother. . .)"
    # a note of the player's own is kept, as far as the line goes
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 6, text = "x", marks = { { title = "The old mill", x = 42, y = 46, '
            'note = "look for the chest behind the wheel, then the one under the bridge, then the one in the cellar of the '
            'mill by the stairs" } } })')
    arrow = text(sim.lua(ARROW))
    assert len(arrow) == 128 and arrow.startswith("ForeverAI: 1. The old mill (look for the chest behind") and arrow.endswith("...)")
    # a name that fills the line by itself comes alone, and a very long one is cut too
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 7, text = "x", marks = { { title = "' + "Long name " * 14 + '", x = 42, y = 46, '
            'note = "anything" } } })')
    arrow = text(sim.lua(ARROW))
    assert len(arrow) == 128 and arrow.endswith("...") and sim.errors == []


def test_the_way_into_the_next_zone_says_that_it_is_the_crossing(sim):
    """The player read "To Redridge Mountains (go on to Redridge Mountains)" as a stop somewhere in that zone, not as
    the pass the arrow leads to."""
    sim.lua(TOMTOM)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "x", marks = { { title = "1. To Darnassus", x = 55, y = 89, '
            'tasks = { { kind = "travel", zone = "Darnassus" } } } } })')
    assert text(sim.lua(ARROW)) == "ForeverAI: 1. To Darnassus (the crossing is here: go through)" and sim.errors == []



def test_a_way_into_a_zone_the_player_chose_is_done_once_they_are_there(sim):
    """Found by walking the game's own Redridge: in Elwynn the player clicked the way into Redridge, a later stop of
    the route (after Westfall and back), and went through. The ways into zones are done in their order, the way to
    Westfall came before it, and it stayed: the arrow with it."""
    sim.lua(TOMTOM)
    sim.lua(LOG)
    sim.lua('table.insert(TEST_log, { id = 9, title = "Training the Beast", objectives = {} })')
    sim._wow.player.x, sim._wow.player.y = 0.371, 0.441
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Portal to Darnassus", x = 37.1, y = 44.1, zone = "Darkshore", tasks = { { kind = "travel", zone = "Darnassus" } } },
      { title = "2. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus", tasks = { { kind = "turn_in", quest = "Training the Beast" } } },
      { title = "3. Portal back", x = 30, y = 41, zone = "Darnassus", tasks = { { kind = "travel", zone = "Darkshore" } } },
      { title = "4. To Ashenvale", x = 44, y = 92, zone = "Darkshore", tasks = { { kind = "travel", zone = "Ashenvale" } } },
      { title = "5. Astranaar", x = 36, y = 50, zone = "Ashenvale" } } })""")
    sim.run(3)
    assert text(sim.lua(DONE)) == ""
    sim.lua("ForeverAI.Map.SetCurrent(4)")  # their own way: Darnassus later
    sim.run(2)
    sim._wow.player.mapID, sim._wow.player.zone = 1440, "Ashenvale"
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.50
    sim.run(5)
    assert text(sim.lua(DONE)) == "4", text(sim.lua(DONE))
    assert text(sim.lua(ARROW)).startswith("ForeverAI: 5. Astranaar") and sim.errors == []
    # a way into the zone the player stands in, chosen there, is not done by standing there
    sim.lua("ForeverAI.Map.SetCurrent(1)")
    sim._wow.player.mapID, sim._wow.player.zone = 1439, "Darkshore"
    sim.run(2)
    sim.lua("ForeverAI.Map.SetCurrent(3)")  # the way back into Darkshore, chosen in Darkshore
    sim.run(5)
    assert "3" not in text(sim.lua(DONE)).split(",") and sim.errors == []



def test_after_the_players_own_stop_the_arrow_does_not_lead_out_past_what_is_left(sim):
    """Two findings of the journeys on the game's own Redridge. After a stop the player chose, the arrow led on to
    the stop after it, a way into another zone, with stops before it still to do: the player left, and the route
    sent them back. The arrow leads to the first stop left instead; with nothing left behind it leads through the way,
    which is done once the player is in that zone."""
    sim.lua(TOMTOM)
    sim.lua(LOG)
    sim.lua('table.insert(TEST_log, { id = 9, title = "Training the Beast", objectives = {} })')
    sim._wow.player.x, sim._wow.player.y = 0.371, 0.441
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. The well", x = 40, y = 45, zone = "Darkshore" },
      { title = "2. The shore", x = 50, y = 50, zone = "Darkshore" },
      { title = "3. To Ashenvale", x = 44, y = 92, zone = "Darkshore", tasks = { { kind = "travel", zone = "Ashenvale" } } },
      { title = "4. Astranaar", x = 36, y = 50, zone = "Ashenvale" } } })""")
    sim.run(2)
    sim.lua("ForeverAI.Map.SetCurrent(2)")  # the player's own way: the shore first
    sim._wow.player.x, sim._wow.player.y = 0.50, 0.50
    sim.run(5)
    assert text(sim.lua(DONE)) == "2" and text(sim.lua(ARROW)).startswith("ForeverAI: 1. The well")  # not the way out
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.45
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,2" and text(sim.lua(ARROW)).startswith("ForeverAI: 3. To Ashenvale")
    sim._wow.player.mapID, sim._wow.player.zone = 1440, "Ashenvale"  # through, as the arrow led
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.50
    sim.run(5)
    assert text(sim.lua(DONE)) == "1,2,3", text(sim.lua(DONE))
    assert text(sim.lua(ARROW)).startswith("ForeverAI: 4. Astranaar") and sim.errors == []


def test_an_update_whose_woven_order_cannot_be_walked_takes_the_plans(sim):
    """Found by the seeded replays of the game's own Redridge: the player had gone on into another zone; the update
    planned the way back first. Kept in the order of the map, the old way out of the zone between stood before the
    new way back into it: the arrow led to a stop in a zone with no way there."""
    sim.lua(TOMTOM)
    sim.lua(LOG)
    sim._wow.player.mapID, sim._wow.player.zone = 1457, "Darnassus"
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.10
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. To Ashenvale", x = 44, y = 92, zone = "Darkshore", tasks = { { kind = "travel", zone = "Ashenvale" } } },
      { title = "2. Astranaar", x = 36, y = 50, zone = "Ashenvale" },
      { title = "3. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus" } } })""")
    sim.run(2)
    # the update from Darnassus: the way back into Darkshore first, then on to Ashenvale
    sim.lua("ForeverAI.Map.Merge(...)", [
        {"title": "1. Portal back", "x": 30, "y": 41, "zone": "Darnassus", "tasks": [{"kind": "travel", "zone": "Darkshore"}]},
        {"title": "2. To Ashenvale", "x": 44, "y": 92, "zone": "Darkshore", "tasks": [{"kind": "travel", "zone": "Ashenvale"}]},
        {"title": "3. Astranaar", "x": 36, "y": 50, "zone": "Ashenvale"},
        {"title": "4. Jocaste", "x": 40.4, "y": 8.6, "zone": "Darnassus"}])
    sim.run(2)
    names = "local o = {} for i, s in ipairs(ForeverAI.Map.List()) do if not s.done then o[#o + 1] = s.title end end return table.concat(o, ', ')"
    assert text(sim.lua(names)).startswith("Portal back, To Ashenvale"), text(sim.lua(names))
    assert sim.errors == []

