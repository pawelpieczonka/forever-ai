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
