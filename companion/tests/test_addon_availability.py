"""Quests the character cannot take, in the add-on (see test_quest_availability.py for the app): what the quest database
lacks is said, what a quest needs is read from Questie, and the window of an NPC who does not give the quest of a stop
is noticed. In the add-on simulator, without the app."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")


# Questie as the add-on reads it, with the quests of an evening in the Wetlands. The character is the simulator's level
# 24 hunter; the stops stand around 40, 40 of their map.
QUESTIE = r"""
-- (a quest in the log: an empty log right after a loading screen is one the game has not filled yet)
TEST_log, TEST_done, TEST_refuse = { { id = 5000, title = "The Lost Ingots", objectives = {} } }, {}, {}
local function entry(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.IsComplete = function(id) local q = entry(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = entry(id) return q and q.objectives or {} end
C_QuestLog.IsQuestFlaggedCompleted = function(id) return TEST_done[id] == true end
C_QuestLog.GetLogIndexForQuestID = function(id) for i, q in ipairs(TEST_log) do if q.id == id then return i end end end
local npcs = {
  [1] = { name = "Harlo Barnaby", spawns = { [11] = { { 40.0, 40.0 } } } },
  [2] = { name = "Captain Stoutfist", spawns = { [11] = { { 42.0, 40.0 } } } },
  [3] = { name = "Longbraid the Grim", spawns = { [11] = { { 49.8, 18.3 } } } },
  [4] = { name = "Rhag Garmason", spawns = { [11] = { { 51.1, 8.0 } } } },
  [5] = { name = "Ebenezer Rustlocke", spawns = { [11] = { { 44.0, 25.0 } } } },
}
local quests = {
  [472] = { name = "Fall of Dun Modr", questLevel = 25, requiredLevel = 20, startedBy = { { 1 } }, finishedBy = { { 3 } } },
  [98221] = { name = "From the Ashes", questLevel = 28, requiredLevel = 23, startedBy = { { 2 } }, finishedBy = { { 2 } },
              objectives = { nil, { { 9001, "Attack Plan" } } }, nextQuestInChain = 465 },
  [465] = { name = "Nek'rosh's Gambit", questLevel = 31, requiredLevel = 26, startedBy = { { 2 } }, finishedBy = { { 2 } },
            preQuestSingle = { 98221 } },
  [631] = { name = "The Thandol Span", questLevel = 30, requiredLevel = 22, startedBy = { { 4 } }, finishedBy = { { 4 } },
            nextQuestInChain = 632 },
  [632] = { name = "The Thandol Span", questLevel = 30, requiredLevel = 22, startedBy = { { 4 } }, finishedBy = { { 4 } },
            preQuestGroup = { 631, 640 } },
  [640] = { name = "Ebenezer's Report", questLevel = 29, requiredLevel = 22, startedBy = { { 5 } }, finishedBy = { { 4 } } },
  [700] = { name = "The Left Path", questLevel = 24, requiredLevel = 20, startedBy = { { 4 } }, exclusiveTo = { 701 } },
  [701] = { name = "The Right Path", questLevel = 24, requiredLevel = 20, startedBy = { { 4 } }, exclusiveTo = { 700 } },
  [800] = { name = "A Friend of Stormwind", questLevel = 24, requiredLevel = 20, startedBy = { { 4 } },
            requiredMinRep = { 72, 3000 } },
  [900] = { name = "For the Horde", questLevel = 24, requiredLevel = 20, startedBy = { { 4 } }, requiredRaces = 178 },
}
local db = { QuestPointers = {}, NPCPointers = {}, npcFlags = {} } -- (no NPC index to build: the stops are named by hand)
for id in pairs(quests) do db.QuestPointers[id] = true end
function db.GetQuest(a, b) return quests[b or a] end
function db.GetNPC(a, b) return npcs[b or a] end
function db.QueryQuestSingle(id, field) local q = quests[id] return q and q[field] end
function db.QueryNPCSingle(id, field) local n = npcs[id] return n and n[field] end
function db.QueryObjectSingle() return nil end -- (the Attack Plans of WoW Forever's own quest: not in the database)
function db.QueryItemSingle() return nil end
function db.GetQuestTagInfo() end
function db.IsDoable(id)
  local q = quests[id]
  if not q or entry(id) or TEST_done[id] or (q.requiredLevel or 0) > UnitLevel("player") then return false end
  for _, pid in ipairs(q.preQuestGroup or {}) do if not TEST_done[pid] then return false end end
  return not q.preQuestSingle or TEST_done[q.preQuestSingle[1]] == true
end
function db.IsDoableVerbose(id) return db.IsDoable(id) and "Available" or "Unavailable: Incomplete pre-quest" end
local player = { HasRequiredRace = function(races) return races ~= 178 end, HasRequiredClass = function() return true end }
local rep = { HasFactionAndReputationLevel = function(self, minRep) return minRep == nil, true, true, true end }
QuestieLoader = { ImportModule = function(_, name)
  if name == "QuestieDB" then return db elseif name == "QuestiePlayer" then return player
  elseif name == "QuestieReputation" then return rep end
  error("no module") end }
C_Map.GetAreaInfo = function(id) return id == 11 and "Wetlands" or nil end
GetFactionInfoByID = function(id) return id == 72 and "Stormwind" or nil end
local unitName = UnitName
UnitName = function(u) if u == "npc" then return TEST_npc end return unitName(u) end
-- the window of the NPC the player talks to: his quests the game data has open to the character, but TEST_refuse
local function offered()
  local out = {}
  for id, q in pairs(quests) do
    for _, nid in ipairs(q.startedBy[1]) do
      if TEST_npc and npcs[nid].name == TEST_npc and db.IsDoable(id) and not TEST_refuse[id] then
        out[#out + 1] = { title = q.name, questID = id }
      end
    end
  end
  table.sort(out, function(a, b) return a.questID < b.questID end)
  return out
end
C_GossipInfo.GetAvailableQuests = offered
GetNumAvailableQuests = function() return #offered() end
GetAvailableTitle = function(i) local q = offered()[i] return q and q.title end
"""


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=80)
    s = Sim(tmp_path / "game").start()
    s.lua(QUESTIE)
    s.lua("ForeverAI.Npcs.BuildIndex()")
    s.slash("/fai")
    s.run(6)  # past the loading screen
    yield s
    s.close()


def py(sim, code, *args):
    return sim.to_py(sim.lua(code, *args))


def test_the_add_on_says_what_the_database_lacks_and_what_a_chain_needs(sim):
    info = py(sim, "return ForeverAI.Collect.QuestInfo(98221)")
    assert info["whereToGet"] == ["Attack Plan: no known place"]
    needs = "local QDB = QuestieLoader:ImportModule('QuestieDB') return ForeverAI.Collect.ChainNeeds(QDB, ...)"
    assert py(sim, needs, 632, 631) == ["[Ebenezer's Report]"]
    sim.lua("TEST_done[640] = true")
    assert py(sim, needs, 632, 631) is None
    # one that a quest done shuts out is no next part for this character: the second result says why
    sim.lua("TEST_done[701] = true")
    shut = py(sim, "local QDB = QuestieLoader:ImportModule('QuestieDB') local a, b = ForeverAI.Collect.ChainNeeds(QDB, 700, 1) return b")
    assert shut == "[The Right Path] is done, and it shuts this one out"


def test_why_a_quest_cannot_be_taken_is_read_from_questie(sim):
    def req(qid):
        return py(sim, "return ForeverAI.Collect.QuestRequirements(...)", qid)
    stoutfist = "Captain Stoutfist (Wetlands 42.0, 40.0)"
    sim.lua('table.insert(TEST_log, { id = 98221, title = "From the Ashes", objectives = { { text = "0/4 Attack Plan", finished = false } } })')
    nek = req(465)
    assert nek["title"] == "Nek'rosh's Gambit" and "state" not in nek and nek["giver"] == stoutfist
    assert nek["reasons"] == ["it opens at level 26 (the character is 24)"]
    assert nek["before"] == [{"id": 98221, "title": "From the Ashes", "state": "in the quest log", "level": 28, "minLevel": 23,
                              "giver": stoutfist, "turnIn": stoutfist}]
    assert nek["beforeKind"] == "one of" and nek["questie"] == "Unavailable: Incomplete pre-quest"
    # all of a group: each with its state, and who gives the one still to do
    span = req(632)
    assert [(b["title"], b["state"]) for b in span["before"]] == [("The Thandol Span", "not done"), ("Ebenezer's Report", "not done")]
    assert span["before"][1]["giver"] == "Ebenezer Rustlocke (Wetlands 44.0, 25.0)" and span["beforeKind"] == "all of"
    sim.lua("TEST_done[631] = true")
    assert [b["title"] for b in req(632)["before"]] == ["Ebenezer's Report"]
    # a quest that shuts it out, the reputation, the race; and one there is nothing against
    sim.lua("TEST_done[701] = true")
    assert req(700)["reasons"] == ["[The Right Path] is done, and it shuts this one out"]
    assert req(800)["reasons"] == ["it needs Friendly with Stormwind"]
    assert req(900)["reasons"] == ["a Horde quest"]
    harlo = req(472)
    assert "reasons" not in harlo and "before" not in harlo and harlo["questie"] == "Available"
    assert req(98221)["state"] == "in the quest log" and req(12345)["error"].startswith("the quest database has no quest")
    # by title: the parts of a chain share it, so each is told
    sim.lua("TEST_res = nil ForeverAI.Collect.QuestRequirementsAsync({ title = ' The Thandol Span ' }, function(r) TEST_res = r end)")
    sim.run(1)
    res = py(sim, "return TEST_res")
    assert [q["id"] for q in res["quests"]] == [631, 632] and res["level"] == 24
    sim.lua("TEST_res = nil ForeverAI.Collect.QuestRequirementsAsync({ title = 'No Such Quest' }, function(r) TEST_res = r end)")
    sim.run(1)
    assert py(sim, "return TEST_res")["error"] == "the quest database has no quest of that name"


# ------------------------------------------------------------------------ the window of an NPC
HARLO = '{ title = "1. Harlo Barnaby", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Fall of Dun Modr", id = 472 } } }'
LONGBRAID = '{ title = "2. Longbraid the Grim", x = 60, y = 60, tasks = { { kind = "turn_in", quest = "Fall of Dun Modr", id = 472 } } }'
STOUTFIST = '{ title = "3. Captain Stoutfist", x = 42, y = 40, tasks = { { kind = "pick_up", quest = "From the Ashes", id = 98221 } } }'


def route(sim, marks, msg=1, auto=False):
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = "The route.", routePlanned = true, marks = { %s } })'
            % (msg, marks))
    sim.run(1)
    if auto:
        sim.lua("ForeverAI.Map.SetAuto(true)")
    sim.run(1)


def go(sim, x, y, seconds=3):
    sim._wow.player.x, sim._wow.player.y = x / 100, y / 100
    sim.run(seconds)


def talk(sim, who, event=b"GOSSIP_SHOW", close=b"GOSSIP_CLOSED"):
    sim.lua('TEST_npc = "%s"' % who)
    sim._wow.fire(event)
    sim._wow.fire(close)
    sim.lua("TEST_npc = nil")
    sim.run(3)


def done(sim) -> str:
    return sim.lua("local out = {} for i, s in ipairs(ForeverAI.Map.List()) do if s.done then out[#out + 1] = i end end "
                   "return table.concat(out, ',')").decode()


def asked(sim) -> list[str]:
    out = []
    for m in sim.sent_messages():
        if m.get("type") == "question":
            out.append(("auto: " if m.get("auto") else "") + m["text"])
            sim.deliver({"kind": "answer", "final": True, "msgId": m["msgId"], "text": "Updated."})
            sim.run(1)
    return out


def test_an_npc_who_does_not_give_the_quest_of_a_stop_is_noted_and_the_route_goes_on_without_it(sim):
    go(sim, 30, 30)
    route(sim, ", ".join((HARLO, LONGBRAID, STOUTFIST)), auto=True)
    asked(sim)
    go(sim, 40, 40)
    sim.lua("TEST_refuse[472] = true")  # the game data has it open to the character; this server does not give it
    talk(sim, "Harlo Barnaby")
    note = py(sim, "return ForeverAI.Map.Refused(472)")
    assert (note["npc"], note["level"], note["title"]) == ("Harlo Barnaby", 24, "Fall of Dun Modr")
    # his stop and the turn-in of the quest are done with, and the line says why; the route is updated without it
    assert done(sim) == "1,2" and sim.errors == []
    assert any(t.startswith("Done: Harlo Barnaby (does not give [Fall of Dun Modr]), Longbraid the Grim. Next: 3. Captain "
                            "Stoutfist") for t in sim.texts()), sim.texts()
    assert asked(sim) == ["auto: [Quest log changed] Harlo Barnaby does not offer [Fall of Dun Modr]. Update the route "
                          "on the map."]
    assert py(sim, "return ForeverAI.Collect.sections.notOffered()") == [
        {"id": 472, "title": "Fall of Dun Modr", "npc": "Harlo Barnaby", "level": 24}]
    assert py(sim, "return ForeverAI.Collect.QuestRequirements(472)")["notOffered"]["npc"] == "Harlo Barnaby"
    # talked to again: nothing new to say
    talk(sim, "Harlo Barnaby")
    assert asked(sim) == []
    # offered after all: no longer noted
    sim.lua("TEST_refuse[472] = nil")
    talk(sim, "Harlo Barnaby")
    assert py(sim, "return ForeverAI.Map.Refused(472)") is None


def test_a_level_gained_tries_the_quest_again(sim):
    go(sim, 40, 40)
    route(sim, HARLO)
    sim.lua("TEST_refuse[472] = true")
    talk(sim, "Harlo Barnaby")
    assert py(sim, "return ForeverAI.Map.Refused(472)") is not None
    assert asked(sim) == []  # the route's updates are off: noted, nothing asked
    sim._wow.player.level = 25
    assert py(sim, "return ForeverAI.Map.Refused(472)") is None
    assert py(sim, "return ForeverAI.Collect.sections.notOffered()") is None


def test_the_window_of_an_npc_with_quests_only_tells_too(sim):
    go(sim, 42, 40)
    route(sim, STOUTFIST + ", " + HARLO, auto=True)  # (a route with nothing left after it is not updated: it is done)
    asked(sim)
    sim.lua("TEST_refuse[98221] = true")
    talk(sim, "Captain Stoutfist", event=b"QUEST_GREETING", close=b"QUEST_FINISHED")
    assert py(sim, "return ForeverAI.Map.Refused(98221)")["npc"] == "Captain Stoutfist"
    assert asked(sim) == ["auto: [Quest log changed] Captain Stoutfist does not offer [From the Ashes]. Update the route "
                          "on the map."]


def test_a_quest_that_is_not_open_yet_is_no_refusal(sim):
    """What a route plans to open on the way: the next part of a chain at the turn-in of the one before, a quest above
    the character's level. Its NPC not offering it yet says nothing about the server."""
    go(sim, 40, 40)
    sim.lua('table.insert(TEST_log, { id = 631, title = "The Thandol Span", objectives = {}, complete = true })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    route(sim, '{ title = "1. Rhag Garmason", x = 40, y = 40, tasks = { { kind = "turn_in", quest = "The Thandol Span", id = 631 }, '
               '{ kind = "pick_up", quest = "The Thandol Span", id = 632, after = 631 } } }, '
               '{ title = "2. Captain Stoutfist", x = 41, y = 40, tasks = { { kind = "pick_up", quest = "Nek\'rosh\'s Gambit", id = 465 } } }',
          auto=True)
    asked(sim)
    talk(sim, "Rhag Garmason")
    talk(sim, "Captain Stoutfist")
    assert py(sim, "return ForeverAI.Collect.sections.notOffered()") is None
    assert asked(sim) == [] and done(sim) == "" and sim.errors == []
    # somebody else's window, or his far from the stop: not the stop's NPC
    sim.lua("TEST_refuse[472] = true")
    route(sim, HARLO, msg=2)
    talk(sim, "Captain Stoutfist")
    go(sim, 70, 70)
    talk(sim, "Harlo Barnaby")
    assert py(sim, "return ForeverAI.Map.Refused(472)") is None
