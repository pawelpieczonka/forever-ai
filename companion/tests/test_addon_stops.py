"""When a stop of a route is done: when what is to be done there is done, as far as the game lets the add-on see it.

The player's words: "Wcześniej było tak, że czekał, aż wezmę questa czy zrobię coś w danym checkpoincie routa. Teraz nie
wziąłem questa i od razu zniknął znacznik ... powinien pilnować aktywności gracza i oznaczać checkpoint za wykonany, gdy
faktycznie zostanie wykonany."

What had happened in the game: two stops at one quest giver (the one the player asked for, and the one for picking its
quests up) were made one by dropping the second with its tasks; the stop left had nothing to wait for and was done when
the player walked up. In the add-on simulator: a quest log, a quest add-on that knows who stands where, and the windows
of NPCs the player opens."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")

# the quest log, and the quest add-on's NPCs of Darkshore: a quest giver with no title, a vendor who repairs, a second
# vendor beside him, a flight master, a hostile creature; nobody at 60, 60
WORLD = r"""
TEST_log, TEST_done = {}, {}
local function quest(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.IsComplete = function(id) local q = quest(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = quest(id) return q and q.objectives or {} end
C_QuestLog.IsQuestFlaggedCompleted = function(id) return TEST_done[id] == true end
local npcs = {
  [1] = { name = "Wilder Thistlenettle", flags = 2, at = { 40.0, 40.0 } },
  [2] = { name = "Gunther Weller", sub = "Weapons Merchant", flags = 4224, at = { 45.0, 45.0 } },
  [3] = { name = "Marda Weller", sub = "Weapons Merchant", flags = 4224, at = { 45.1, 45.2 } },
  [4] = { name = "Caylais Moonfeather", sub = "Hippogryph Master", flags = 8192, at = { 36.3, 45.6 } },
  [5] = { name = "Den Mother", flags = 0, at = { 51.5, 38.3 }, faction = 99 },
  [6] = { name = "Old Friendly Bear", flags = 0, at = { 30.0, 30.0 }, faction = 7 },
}
local db = { NPCPointers = {}, npcFlags = { FLIGHT_MASTER = 8192, VENDOR = 128, REPAIR = 4096 },
             factionTemplate = { [99] = 1, [7] = 0 } }
for id in pairs(npcs) do db.NPCPointers[id] = true end
function db.QueryNPCSingle(id, field)
  local n = npcs[id]
  if not n then return nil end
  if field == "name" then return n.name elseif field == "subName" then return n.sub
  elseif field == "npcFlags" then return n.flags elseif field == "zoneID" then return 148
  elseif field == "spawns" then return { [148] = { n.at } } elseif field == "friendlyToFaction" then return "AH"
  elseif field == "factionID" then return n.faction end
end
local zones = { GetAreaIdByUiMapId = function(_, mapID) return mapID == 1439 and 148 or nil end,
                GetUiMapIdByAreaId = function(_, area) return area == 148 and 1439 or nil end }
QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db elseif name == "ZoneDB" then return zones end error("no module") end }
local unitName = UnitName
UnitName = function(u) if u == "npc" then return TEST_npc end return unitName(u) end
"""
SECTION = "local s = ForeverAI.Map.Section() return s and table.concat(s, ' | ') or 'nothing'"
LIST = """
  local out = {}
  for i, s in ipairs(ForeverAI.Map.List()) do out[#out + 1] = i .. (s.done and " done" or s.current and " now" or "") .. ": " .. tostring(s.waits) end
  return table.concat(out, " | ")
"""


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=80)
    s = Sim(tmp_path / "game").start()
    s.lua(WORLD)
    s.lua("ForeverAI.Npcs.BuildIndex()")
    s.slash("/fai")
    s.run(6)  # past the loading screen, and the NPC data is read
    yield s
    s.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def route(sim, marks: str, msg=1):
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = "The route.", routePlanned = true, marks = { %s } })' % (msg, marks))
    sim.run(1)


def go(sim, x, y, seconds=3):
    sim._wow.player.x, sim._wow.player.y = x / 100, y / 100
    sim.run(seconds)


def take(sim, qid, title):
    sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = {} })' % (qid, title))
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)


def talk(sim, who, event=b"GOSSIP_SHOW", close=None):
    sim.lua('TEST_npc = "%s"' % who)
    sim._wow.fire(event)
    if close:
        sim._wow.fire(close)
    sim.lua("TEST_npc = nil")
    sim.run(3)


def done(sim) -> str:
    """Which stops are done, by number: "1,3"."""
    return text(sim.lua("""
      local out = {}
      for i, s in ipairs(ForeverAI.Map.List()) do if s.done then out[#out + 1] = i end end
      return table.concat(out, ",")
    """))


WILDER = ('{ title = "1. Wilder Thistlenettle", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Oh Brother. . .", id = 167 }, '
          '{ kind = "pick_up", quest = "Collecting Memories", id = 168 } } }')
PLACE = '{ title = "2. The cave mouth", x = 60, y = 60 }'


def test_a_quest_stop_waits_for_the_quest_not_for_the_player_to_walk_up(sim):
    go(sim, 30, 30)
    route(sim, WILDER + ", " + PLACE)
    go(sim, 40, 40, 6)  # standing at the quest giver: nothing is picked up yet
    assert done(sim) == ""
    talk(sim, "Wilder Thistlenettle")  # talked to him, took nothing
    assert done(sim) == ""
    go(sim, 60, 60)  # walked on to the next stop: that one is a place, the quest giver still waits
    assert done(sim) == "2"
    go(sim, 40, 40)
    take(sim, 167, "Oh Brother. . .")  # one of the two
    assert done(sim) == "2"
    take(sim, 168, "Collecting Memories")
    assert done(sim) == "1,2" and sim.errors == []


def test_two_stops_at_one_npc_become_one_that_keeps_every_task(sim):
    """What the planner sent in the game: the stop the player asked for first, with nothing to wait for, then the
    quest's stop at the same NPC. The second was dropped with its tasks."""
    go(sim, 30, 30)
    route(sim, '{ title = "1. Wilder Thistlenettle (pick up Oh Brother, Collecting Mem", x = 40, y = 40, note = "pick up the quests" }, '
          + WILDER.replace('"1. ', '"2. ') + ", " + PLACE.replace('"2. ', '"3. '))
    assert text(sim.lua("return #ForeverAI.Map.List()")) == 2  # one stop for the place
    assert "picking up [Oh Brother. . .], picking up [Collecting Memories]" in text(sim.lua(LIST))
    go(sim, 40, 40, 6)
    assert done(sim) == ""  # reached, nothing taken: it stays
    take(sim, 167, "Oh Brother. . .")
    take(sim, 168, "Collecting Memories")
    assert done(sim) == "1"
    # the same the other way round, and a task named twice is kept once
    sim.lua("TEST_log = {} ForeverAI.Map.Clear()")
    route(sim, WILDER + ', { title = "2. Wilder Thistlenettle", x = 40.1, y = 40.1, tasks = { { kind = "pick_up", quest = "Oh Brother. . .", id = 167 }, '
          '{ kind = "turn_in", quest = "The Defias Brotherhood", id = 65 } } }', msg=2)
    assert text(sim.lua("return #ForeverAI.Map.List()")) == 1
    assert text(sim.lua(LIST)).count("picking up [Oh Brother. . .]") == 1 and "turning in [The Defias Brotherhood]" in text(sim.lua(LIST))


def test_a_quest_picked_up_and_finished_at_once_does_not_hold_the_stop_for_ever(sim):
    go(sim, 30, 30)
    route(sim, '{ title = "1. Wilder Thistlenettle", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "A Quick Word", id = 900 } } }')
    go(sim, 40, 40)
    sim.lua("TEST_done[900] = true")  # taken and handed in in one talk: it never stands in the log
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == "1"


def test_a_stop_at_a_vendor_is_done_when_the_player_has_dealt_with_him(sim):
    """A stop the player asked for ("najpierw sprzedać śmieci i naprawić"): walking past the vendor is not selling."""
    go(sim, 30, 30)
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45, note = "sell and repair" }, ' + PLACE)
    go(sim, 45, 45, 6)
    assert done(sim) == "" and "1 now: dealing with Gunther Weller" in text(sim.lua(LIST))
    go(sim, 46, 46)
    go(sim, 45, 45)  # away and back: still not done
    assert done(sim) == ""
    # the marks section tells the AI that this stop waits for what is done there
    assert "leave the route when what is to be done there is done, not when the player reaches them: Gunther Weller (repair)" in text(sim.lua(SECTION))
    talk(sim, "Gunther Weller", b"MERCHANT_SHOW")
    assert done(sim) == "1" and sim.errors == []


def test_any_npc_at_the_stop_counts_and_the_named_one_wherever_he_is(sim):
    go(sim, 30, 30)
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }')
    go(sim, 45.1, 45.2)
    talk(sim, "Marda Weller", b"MERCHANT_SHOW")  # the other weapons merchant, beside him: she repairs as well
    assert done(sim) == "1"
    sim.lua("ForeverAI.Map.Clear()")
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }', msg=2)
    go(sim, 52, 45)  # he has wandered off: 400 m from where the stop is
    talk(sim, "Gunther Weller", b"MERCHANT_SHOW")
    assert done(sim) == "1"


def test_talking_to_somebody_else_somewhere_else_is_not_it(sim):
    go(sim, 30, 30)
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }')
    go(sim, 36.3, 45.6)
    talk(sim, "Caylais Moonfeather", b"TAXIMAP_OPENED")  # the flight master, half a kilometer away
    go(sim, 45, 45)
    assert done(sim) == ""


def test_what_the_player_did_there_before_the_stop_was_set_does_not_count(sim):
    go(sim, 45, 45)
    talk(sim, "Gunther Weller", b"MERCHANT_SHOW", b"MERCHANT_CLOSED")
    sim.run(2)
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }')
    sim.run(4)
    assert done(sim) == ""
    talk(sim, "Gunther Weller", b"MERCHANT_SHOW")
    assert done(sim) == "1"


def test_a_window_open_when_the_route_was_asked_for_counts_when_it_closes(sim):
    """The player asks for the route with the vendor's window open, sells, and closes it."""
    go(sim, 45, 45)
    sim.lua('TEST_npc = "Gunther Weller"')
    sim._wow.fire(b"MERCHANT_SHOW")
    sim.run(2)
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }')
    sim.run(3)
    assert done(sim) == ""
    sim._wow.fire(b"MERCHANT_CLOSED")
    sim.run(3)
    assert done(sim) == "1"
    # a closing the game sends with no window having been open is nothing
    sim.lua("ForeverAI.Map.Clear()")
    sim.run(2)
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }', msg=2)
    sim._wow.fire(b"MERCHANT_CLOSED")
    sim._wow.fire(b"GOSSIP_CLOSED")
    sim.run(3)
    assert done(sim) == ""


def test_a_flight_master_stop_is_done_when_his_map_was_opened(sim):
    go(sim, 30, 30)
    route(sim, '{ title = "1. Caylais Moonfeather (flight master)", x = 36.3, y = 45.6, note = "open the flight path" }')
    go(sim, 36.3, 45.6, 6)
    assert done(sim) == ""
    talk(sim, "Caylais Moonfeather", b"TAXIMAP_OPENED")
    assert done(sim) == "1"


def test_a_bare_place_is_done_when_it_is_reached(sim):
    go(sim, 30, 30)
    route(sim, '{ title = "1. The cave mouth", x = 60, y = 60 }, { title = "2. Deadmines entrance", x = 70, y = 70 }')
    go(sim, 60, 60)
    assert done(sim) == "1"
    go(sim, 70, 70)
    assert done(sim) == "1,2"


def test_a_creature_to_kill_is_a_place_not_somebody_to_talk_to(sim):
    """A stop named after a hostile creature, or set for a creature: nobody to deal with there."""
    go(sim, 30, 30)
    route(sim, '{ title = "1. Den Mother", x = 51.5, y = 38.3 }')
    go(sim, 51.5, 38.3)
    assert done(sim) == "1"
    # a creature that is not hostile and is named in the stop is somebody to deal with (an NPC with no title)
    sim.lua("ForeverAI.Map.Clear()")
    route(sim, '{ title = "1. Wilder Thistlenettle", x = 40, y = 40 }', msg=2)
    go(sim, 40, 40, 5)
    assert done(sim) == ""
    talk(sim, "Wilder Thistlenettle")
    assert done(sim) == "1"


def test_without_npc_data_a_stop_falls_back_to_being_reached(sim):
    """No quest add-on, or one whose data never gets ready: the stop must not wait for ever."""
    go(sim, 30, 30)
    sim.lua("QuestieLoader = nil")
    route(sim, '{ title = "1. Gunther Weller (repair)", x = 45, y = 45 }')
    go(sim, 45, 45)
    assert done(sim) == "1"


def test_npc_data_that_comes_late_is_waited_for_a_moment(tmp_path):
    install_game(tmp_path / "game", pool=80)
    sim = Sim(tmp_path / "game").start()
    sim.lua(WORLD)
    sim.lua("TEST_pointers = QuestieLoader:ImportModule('QuestieDB').NPCPointers QuestieLoader:ImportModule('QuestieDB').NPCPointers = nil")
    sim.slash("/fai")
    sim._wow.player.x, sim._wow.player.y = 0.45, 0.45  # the player logs in standing at the vendor
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", marks = { { title = "1. Gunther Weller (repair)", x = 45, y = 45 } } })')
    sim.run(5)
    assert done(sim) == ""  # not known yet whether somebody stands there: not done by standing there
    sim.lua("QuestieLoader:ImportModule('QuestieDB').NPCPointers = TEST_pointers")  # the quest add-on is ready now
    sim.run(8)
    assert done(sim) == "" and "dealing with Gunther Weller" in text(sim.lua(LIST))
    talk(sim, "Gunther Weller", b"MERCHANT_SHOW")
    assert done(sim) == "1"
    sim.close()


def test_a_turn_in_and_the_next_quest_at_one_npc_wait_for_both(sim):
    sim.lua('table.insert(TEST_log, { id = 65, title = "The Defias Brotherhood", complete = true, objectives = {} })')
    go(sim, 30, 30)
    route(sim, '{ title = "1. Wilder Thistlenettle", x = 40, y = 40, tasks = { { kind = "turn_in", quest = "The Defias Brotherhood", id = 65 }, '
          '{ kind = "pick_up", quest = "The Defias Brotherhood", id = 132 } } }')
    go(sim, 40, 40)
    assert done(sim) == ""
    sim.lua("table.remove(TEST_log, 1)")  # turned in; the next part not taken yet
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == ""
    take(sim, 132, "The Defias Brotherhood")
    assert done(sim) == "1"


def test_the_stops_and_what_they_wait_for_come_back_after_a_reload(sim):
    go(sim, 30, 30)
    route(sim, WILDER + ', { title = "2. Gunther Weller (repair)", x = 45, y = 45 }')
    go(sim, 45, 45, 6)
    sim.reload()
    sim.lua(WORLD)
    sim.lua("ForeverAI.Npcs.BuildIndex()")
    sim._wow.player.x, sim._wow.player.y = 0.45, 0.45
    sim.run(8)
    assert done(sim) == ""  # neither the quest giver nor the vendor is done by the reload
    talk(sim, "Gunther Weller", b"MERCHANT_SHOW")
    assert done(sim) == "2"
    take(sim, 167, "Oh Brother. . .")
    take(sim, 168, "Collecting Memories")
    assert done(sim) == "1,2" and sim.errors == []


def test_two_npcs_side_by_side_are_one_stop_that_says_whose_quest_is_left(sim):
    """From the game: "Barkeep Daniels (pick up Dry Times, A Free Lunch)" stayed on the arrow after Dry Times was
    taken. A Free Lunch is Darcy's, who stands beside him: the two stops had become one, under his name."""
    sim.lua("""TomTom = {}
      function TomTom:AddWaypoint(map, x, y, opts) if opts.crazy then self.arrow = opts.title end return { title = opts.title } end
      function TomTom:RemoveWaypoint(uid) end
      function TomTom:SetCrazyArrow(uid, dist, title) self.arrow = title end""")
    go(sim, 30, 30)
    route(sim, '{ title = "1. Barkeep Daniels", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Dry Times", id = 116 } } }, '
               '{ title = "2. Darcy", x = 40.1, y = 40.2, tasks = { { kind = "pick_up", quest = "A Free Lunch", id = 129 } } }, ' + PLACE)
    what = "return ForeverAI.Map.List()[1].what"
    assert text(sim.lua("return #ForeverAI.Map.List()")) != "3" and sim.lua("return #ForeverAI.Map.List()") == 2
    assert text(sim.lua(what)) == "pick up Dry Times, A Free Lunch (Darcy)"
    go(sim, 40, 40)
    take(sim, 116, "Dry Times")
    # the stop stays for the other quest, and says only that one, with who gives it; the arrow too
    assert done(sim) == "" and text(sim.lua(what)) == "pick up A Free Lunch (Darcy)"
    assert text(sim.lua("return TomTom.arrow")) == "ForeverAI: 1. Barkeep Daniels (pick up A Free Lunch (Darcy))"
    take(sim, 129, "A Free Lunch")
    assert done(sim) == "1" and sim.errors == []


def test_a_quest_planned_whole_waits_for_its_pick_up(sim):
    """A route is made with all of a quest that can be done along it: where it is taken, what it wants, where it is
    turned in. The quest log does not hold it yet, and that is not "done": nothing of it is ticked off before the
    quest is taken."""
    go(sim, 30, 30)
    route(sim, '{ title = "1. Clerk", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Pests", id = 21 } } }, '
               '{ title = "2. Rat", x = 60, y = 60, tasks = { { kind = "objective", quest = "Pests", id = 21, label = "Rat" } } }, '
               '{ title = "3. Clerk", x = 40.5, y = 45, tasks = { { kind = "turn_in", quest = "Pests", id = 21 } } }')
    sim.run(6)
    assert done(sim) == ""
    go(sim, 60, 60, 6)  # walked to the rats without the quest: the stop stays
    assert done(sim) == ""
    go(sim, 40, 40)
    sim.lua('table.insert(TEST_log, { id = 21, title = "Pests", objectives = { { text = "0/8 Rat", finished = false } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == "1"
    sim.lua("TEST_log[#TEST_log].objectives[1].finished = true TEST_log[#TEST_log].complete = true")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == "1,2"
    sim.lua("table.remove(TEST_log) TEST_done[21] = true")  # turned in
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == "1,2,3" and sim.errors == []


def test_a_stop_the_route_comes_back_to_is_a_stop_of_its_own(sim):
    """Found by the end-to-end test: a quest taken and turned in at the same NPC, with its creature between. As one
    stop ("pick up ...; turn in ...") it held the arrow on the NPC until the quest was turned in."""
    go(sim, 30, 30)
    marks = ('{ title = "1. Clerk", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Pests", id = 21 } } }, '
             '{ title = "2. Rat", x = 60, y = 60, tasks = { { kind = "objective", quest = "Pests", id = 21, label = "Rat" } } }, '
             '{ title = "3. Clerk", x = 40, y = 40, tasks = { { kind = "turn_in", quest = "Pests", id = 21 } } }')
    route(sim, marks)
    titles = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[#out + 1] = s.title .. ':' .. tostring(s.what) end return table.concat(out, ' | ')"
    assert text(sim.lua(titles)) == "Clerk:pick up Pests | Rat:for Pests | Clerk:turn in Pests"
    go(sim, 40, 40)
    sim.lua('table.insert(TEST_log, { id = 21, title = "Pests", objectives = { { text = "0/8 Rat", finished = false } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == "1"
    # an update of the route with the same stops keeps both stops at the clerk, each for what it is for
    sim.lua('ForeverAI.Map.Merge({ %s })' % marks.replace('{ title = "1. Clerk", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Pests", id = 21 } } }, ', ""))
    sim.run(1)
    assert text(sim.lua(titles)).endswith("Rat:for Pests | Clerk:turn in Pests") and sim.errors == []


def test_a_stop_for_something_that_lies_in_one_of_several_places_says_so(sim):
    go(sim, 30, 30)
    route(sim, '{ title = "1. Glinting Mud", x = 40, y = 40, tasks = { { kind = "objective", quest = "Hilary\'s Necklace", '
               'id = 3741, label = "Hilary\'s Necklace", spots = 6 } } }, '
               '{ title = "2. Old Chest", x = 60, y = 60, tasks = { { kind = "objective", quest = "The Key", id = 7, label = "Key" } } }')
    what = "local l = ForeverAI.Map.List() return tostring(l[1].what) .. ' | ' .. tostring(l[2].what)"
    assert text(sim.lua(what)) == "for Hilary's Necklace; search around: one of 6 spots | Key for The Key"
    assert sim.errors == []


def test_stops_of_a_quest_that_is_through_are_not_put_on_the_route(sim):
    """From the game's log: an update put "Shawn, Glinting Mud, Hilary" back on the route the second after the quest
    was turned in. Whatever the app sends, a stop whose quest the game says is turned in (or, for a stop where it is
    picked up, is in the log) does not go on the map; and the app is told which quests of the route are through."""
    go(sim, 30, 30, 26)  # (past the first half minute after loading, when an empty quest log says nothing yet)
    quest_stops = ('{ title = "%d. Shawn", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Necklace", id = 3741 } } }, '
                   '{ title = "%d. Mud", x = 42, y = 42, tasks = { { kind = "objective", quest = "Necklace", id = 3741, label = "Necklace" } } }, '
                   '{ title = "%d. Hilary", x = 44, y = 44, tasks = { { kind = "turn_in", quest = "Necklace", id = 3741 } } }, ')
    route(sim, quest_stops % (1, 2, 3) + '{ title = "4. The cave mouth", x = 60, y = 60 }')
    assert sim.lua("return #ForeverAI.Map.List()") == 4 and sim.lua("return ForeverAI.Map.RouteDone()") is None
    sim.lua("TEST_done[3741] = true")  # taken, done and turned in
    sim.run(3)
    assert done(sim) == "1,2,3" and list(sim.to_py(sim.lua("return ForeverAI.Map.RouteDone()"))) == [3741]
    # the update that still has the quest's stops: only what is left goes on the map
    sim.lua('ForeverAI.Map.Merge({ %s { title = "4. The cave mouth", x = 60, y = 60 }, { title = "5. The old mill", x = 70, y = 70 } })'
            % (quest_stops % (1, 2, 3)))
    sim.lua("ForeverAI.Map.DropDone()")
    titles = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[#out + 1] = s.title end return table.concat(out, ', ')"
    assert text(sim.lua(titles)) == "The cave mouth, The old mill"
    # a whole new route with them: the same
    route(sim, quest_stops % (1, 2, 3) + '{ title = "4. The old mill", x = 70, y = 70 }', msg=3)
    assert text(sim.lua(titles)) == "The old mill"
    # a stop to pick up a quest that is in the log already is not put on it either; what is left of that quest is
    sim.lua('table.insert(TEST_log, { id = 77, title = "Rats", objectives = { { text = "0/8 Rat", finished = false } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Clerk", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Rats", id = 77 } } }, '
               '{ title = "2. Rat", x = 60, y = 60, tasks = { { kind = "objective", quest = "Rats", id = 77, label = "Rat" } } }', msg=4)
    assert text(sim.lua(titles)) == "Rat" and sim.errors == []


def test_after_a_stop_chosen_out_of_order_the_arrow_skips_a_turn_in_that_waits(sim):
    """Found by walking the game's own quests: the player chose a later stop; after it the arrow went on to a turn-in
    whose objective stop they had left behind."""
    go(sim, 30, 30)
    sim.lua('table.insert(TEST_log, { id = 12, title = "Militia", objectives = { { text = "0/8 Trapper slain", finished = false } } })')
    sim.lua('table.insert(TEST_log, { id = 13, title = "Paws", objectives = { { text = "0/8 Gnoll Paw", finished = false } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Trapper", x = 40, y = 40, tasks = { { kind = "objective", quest = "Militia", id = 12, label = "Trapper" } } }, '
               '{ title = "2. Gnoll", x = 50, y = 50, tasks = { { kind = "objective", quest = "Paws", id = 13, label = "Gnoll Paw" } } }, '
               '{ title = "3. Gryan", x = 60, y = 60, tasks = { { kind = "turn_in", quest = "Militia", id = 12 }, { kind = "turn_in", quest = "Paws", id = 13 } } }, '
               '{ title = "4. Camp", x = 70, y = 70 }')
    sim.lua("ForeverAI.Map.SetCurrent(2)")
    go(sim, 50, 50)
    sim.lua("TEST_log[2].objectives[1].finished = true TEST_log[2].complete = true")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    now = "for i, s in ipairs(ForeverAI.Map.List()) do if s.current then return s.title end end"
    assert text(sim.lua(now)) == "Camp" and sim.errors == []  # not Gryan: the trappers are still to do



def test_a_stop_whose_rest_waits_for_another_lets_the_arrow_go_on(sim):
    """Found by walking the game's own Duskwood: the player went to Jitters first, whose stop was "turn in [Raven
    Hill], then pick up [Jitters' Growling Gut]", and took [Jitters' Growling Gut] (the game lets them). The turn-in
    waits for [Raven Hill], to be picked up in Darkshire, and the arrow stayed on Jitters."""
    go(sim, 30, 30, 26)
    route(sim, '{ title = "1. Commander", x = 70, y = 40, tasks = { { kind = "pick_up", quest = "Raven Hill", id = 163 } } }, '
               '{ title = "2. Spiders", x = 60, y = 60 }, '
               '{ title = "3. Jitters", x = 20, y = 55, tasks = { { kind = "turn_in", quest = "Raven Hill", id = 163 }, '
               '{ kind = "pick_up", quest = "Growling Gut", id = 5 } } }')
    sim.lua("ForeverAI.Map.SetCurrent(3)")
    now = "for i, s in ipairs(ForeverAI.Map.List()) do if s.current then return i .. '. ' .. s.title end end"
    go(sim, 20, 55)
    assert text(sim.lua(now)) == "3. Jitters"  # nothing done there yet: the arrow stays
    sim.lua('table.insert(TEST_log, { id = 5, title = "Growling Gut", objectives = {} })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    # what is left there waits for the commander: the arrow leads there, and Jitters stays on the route
    assert text(sim.lua(now)) == "1. Commander" and done(sim) == ""
    sim.lua('table.insert(TEST_log, { id = 163, title = "Raven Hill", objectives = {}, complete = true })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    go(sim, 70, 40)
    assert done(sim) == "1"
    assert text(sim.lua(now)) == "2. Spiders" and sim.errors == []



def test_a_next_part_beside_a_turn_in_that_waits_lets_the_arrow_go_on(sim):
    """Found by walking the game's own Westfall: the player came to Gryan early and turned in what was done; left there
    were the turn-in of [The People's Militia] (its creatures at another stop) and the pick-up of its next part. The
    arrow stayed on Gryan."""
    go(sim, 30, 30, 26)
    sim.lua('table.insert(TEST_log, { id = 12, title = "Militia", objectives = { { text = "0/15 Trapper slain", finished = false } } })')
    sim.lua('table.insert(TEST_log, { id = 109, title = "Report", objectives = {}, complete = true })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Trapper", x = 70, y = 40, tasks = { { kind = "objective", quest = "Militia", id = 12, label = "Trapper" } } }, '
               '{ title = "2. Gryan", x = 20, y = 55, tasks = { { kind = "turn_in", quest = "Report", id = 109 }, '
               '{ kind = "turn_in", quest = "Militia", id = 12 }, { kind = "pick_up", quest = "Militia II", id = 13 } } }')
    sim.lua("ForeverAI.Map.SetCurrent(2)")
    now = "for i, s in ipairs(ForeverAI.Map.List()) do if s.current then return i .. '. ' .. s.title end end"
    go(sim, 20, 55)
    assert text(sim.lua(now)) == "2. Gryan"  # the report is to turn in there
    sim.lua("table.remove(TEST_log, 2) TEST_done[109] = true")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert text(sim.lua(now)) == "1. Trapper" and done(sim) == "" and sim.errors == []
    # with nothing done there yet, a quest to pick up holds the arrow: the player may take it there ...
    route(sim, '{ title = "1. Trapper", x = 70, y = 40, tasks = { { kind = "objective", quest = "Militia", id = 12, label = "Trapper" } } }, '
               '{ title = "2. Gryan", x = 20, y = 55, tasks = { { kind = "pick_up", quest = "Militia II", id = 13 }, '
               '{ kind = "turn_in", quest = "Militia", id = 12 } } }', msg=2)
    sim.lua("ForeverAI.Map.SetCurrent(2)")
    sim.run(3)
    assert text(sim.lua(now)) == "2. Gryan"
    # ... while he talks to him; once his window is closed without it, it waits for the turn-in
    sim.lua('TEST_npc = "Gryan"')  # (the game names the NPC whose window it is)
    sim._wow.fire(b"QUEST_GREETING")
    sim.run(3)
    assert text(sim.lua(now)) == "2. Gryan"
    sim._wow.fire(b"QUEST_FINISHED")
    sim.lua("TEST_npc = nil")
    sim.run(3)
    assert text(sim.lua(now)) == "1. Trapper" and sim.errors == []



def test_tracking_that_ended_for_one_quest_leaves_a_stop_another_needs(sim):
    """Found by walking the game's own Westfall: the stop "Harvest Golem" was for the okra of [Westfall Stew] and the
    modulators of [Harvesting the Harvesters]. Tracking the golems ended with the okra, and the stop with it: the arrow
    went on to turn the other quest in without its modulators."""
    go(sim, 30, 30, 26)
    sim.lua('table.insert(TEST_log, { id = 38, title = "Westfall Stew", objectives = { { text = "Okra: 3/3", finished = true } } })')
    sim.lua('table.insert(TEST_log, { id = 92911, title = "Harvesters", objectives = { { text = "Copper Modulator: 1/4", finished = false } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Harvest Golem", x = 50, y = 34, mob = "Harvest Golem", tasks = { '
               '{ kind = "objective", quest = "Westfall Stew", id = 38, label = "Okra" }, '
               '{ kind = "objective", quest = "Harvesters", id = 92911, label = "Copper Modulator" } } }, '
               '{ title = "2. Ozwin", x = 51, y = 32, tasks = { { kind = "turn_in", quest = "Harvesters", id = 92911 } } }')
    sim.lua('ForeverAI.Map.Done({ ["harvest golem"] = true })')
    sim.run(2)
    assert done(sim) == ""
    sim.lua("TEST_log[2].objectives[1].finished = true TEST_log[2].complete = true")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert done(sim) == "1" and sim.errors == []
    # a stop for one quest only goes with its tracking, as before
    route(sim, '{ title = "1. Prowler", x = 60, y = 60, mob = "Prowler", tasks = { '
               '{ kind = "objective", quest = "Westfall Stew", id = 38, label = "Okra" } } }, '
               '{ title = "2. The old mill", x = 70, y = 70 }', msg=2)
    sim.lua('ForeverAI.Map.Done({ ["prowler"] = true })')
    sim.run(2)
    assert done(sim) == "1" and sim.errors == []



def test_a_task_named_by_title_alone_is_the_one_with_its_number(sim):
    """Found by the real model on the game's own Goldshire: the AI's mark for a turn-in, by title alone, at the stop the
    planner had with the same turn-in by number: the stop said it twice."""
    go(sim, 30, 30, 26)
    sim.lua('table.insert(TEST_log, { id = 40, title = "A Fishy Peril", objectives = {}, complete = true })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Marshal Dughan", x = 42.1, y = 65.9, tasks = { { kind = "turn_in", quest = "A Fishy Peril", id = 40 } } }, '
               '{ title = "2. Marshal Dughan", x = 42.1, y = 65.9, tasks = { { kind = "turn_in", quest = "A Fishy Peril" } } }')
    what = "return ForeverAI.Map.List()[1].what"
    assert text(sim.lua(what)) == "turn in A Fishy Peril" and sim.lua("return #ForeverAI.Map.List()") == 1
    assert sim.errors == []



def test_tracking_that_ended_leaves_what_a_quest_still_to_pick_up_wants(sim):
    """Found by the seeded replays of the game's own Goldshire: the player went to the kobolds before taking [Gold Dust
    Exchange]; tracking ended with the other quest's book, and the stop with it. Taken later, the quest's gold dust had
    no stop, and the arrow led to its turn-in."""
    go(sim, 30, 30, 26)
    sim.lua('table.insert(TEST_log, { id = 91775, title = "Book Return", objectives = { { text = "Lost Book: 1/1", finished = true } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Remy", x = 42.1, y = 67.3, tasks = { { kind = "pick_up", quest = "Gold Dust Exchange", id = 47 } } }, '
               '{ title = "2. Kobold Miner", x = 40.5, y = 80.3, mob = "Kobold Miner", tasks = { '
               '{ kind = "objective", quest = "Gold Dust Exchange", id = 47, label = "Gold Dust" }, '
               '{ kind = "objective", quest = "Book Return", id = 91775, label = "Lost Book" } } }, '
               '{ title = "3. Remy", x = 42.2, y = 67.0, tasks = { { kind = "turn_in", quest = "Gold Dust Exchange", id = 47 } } }')
    sim.lua('ForeverAI.Map.Done({ ["kobold miner"] = true })')
    sim.run(2)
    assert "2" not in done(sim).split(","), done(sim)
    assert sim.errors == []



def test_a_question_right_after_login_waits_for_the_npc_data():
    """From the game: the player logged in and asked for the route a second later; the game data had no flight
    masters (the add-on was still reading the quest database), and the route walked where a flight was known."""
    import tempfile
    from pathlib import Path
    game = Path(tempfile.mkdtemp()) / "game"
    install_game(game, pool=80)
    s = Sim(game).start()
    try:
        s.lua(WORLD)
        s.slash("/fai")
        s.ask("update the route")  # at once: nothing has read the NPCs yet
        s.run(8)
        (msg,) = [m for m in s.sent_messages() if m.get("type") == "question"]
        snap = msg["snapshot"]
        # (the NPCs of the quest database are in the game data: the flight master of the zone among them)
        assert any("Caylais Moonfeather" in line and "flight master" in line for line in snap.get("zoneNPCs") or []), snap.get("zoneNPCs")
        assert any(line.startswith("flight master: Caylais Moonfeather") for line in snap.get("services") or [])
        assert "(NPC index is still loading)" not in str(snap.get("zoneNPCs")) and s.errors == []
    finally:
        s.close()



def test_the_arrow_does_not_lead_out_of_the_zone_past_stops_left_behind(sim):
    """Found by a journey on the game's own Redridge: the player chose a stop in the middle of the list; when it was
    done the arrow led on to the stop after it, the flight out of the zone, with stops of the zone before it still to
    do. The player flew away, the route sent them back, three times over."""
    go(sim, 30, 30, 26)
    route(sim, '{ title = "1. The well", x = 40, y = 40 }, { title = "2. The mill", x = 50, y = 50 }, '
               '{ title = "3. The cave", x = 60, y = 60 }, '
               '{ title = "4. The gryphon", x = 62, y = 62, tasks = { { kind = "travel", zone = "Teldrassil" } } }, '
               '{ title = "5. The village", x = 30, y = 30, zone = "Teldrassil" }')
    sim.lua("ForeverAI.Map.SetCurrent(3)")
    go(sim, 60, 60)
    now = "for i, s in ipairs(ForeverAI.Map.List()) do if s.current then return i .. '. ' .. s.title end end"
    assert done(sim) == "3" and text(sim.lua(now)) == "1. The well", text(sim.lua(now))  # not the way out
    # with nothing left behind, the arrow goes on through the way out
    go(sim, 40, 40)
    go(sim, 50, 50)
    assert done(sim) == "1,2,3" and text(sim.lua(now)) == "4. The gryphon" and sim.errors == []
