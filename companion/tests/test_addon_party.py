"""A route for the party, in the add-on (see test_party_routes.py for the app): what the add-on reads of the other
players (their level, where they are, their quest log as Questie in their game shares it) and the route shared with
the ones who have ForeverAI too (sent in add-on messages, followed with a click, kept up by itself). In the add-on
simulator, without the app."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")


# A party of two: the simulator's level 24 hunter in Auberdine and Rossmann, a level 27 hunter 20 m away. Questie
# knows the quests of the evening, and QuestieComms what Rossmann's Questie sent of his quest log (TEST_remote).
PARTY = r"""
TEST_log, TEST_done, TEST_push = { { id = 7001, title = "WANTED: Murkdeep!" }, { id = 7002, title = "Encrusted Tail Fins" },
  { id = 7006, title = "The Tower of Althalaxx" } }, {}, { [7001] = true }
local function entry(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.GetLogIndexForQuestID = function(id) for i, q in ipairs(TEST_log) do if q.id == id then return i end end end
C_QuestLog.IsQuestFlaggedCompleted = function(id) return TEST_done[id] == true end
C_QuestLog.IsPushableQuest = function(id) return TEST_push[id] == true end
local npcs = { [1] = { name = "Sentinel Glynda Nal'Shea", spawns = { [148] = { { 37.7, 43.4 } } } },
               [2] = { name = "Gershala Nightwhisper", spawns = { [148] = { { 38.3, 43.1 } } } },
               [3] = { name = "Thundris Windweaver", spawns = { [148] = { { 37.4, 40.1 } } } } }
local quests = {
  [7001] = { name = "WANTED: Murkdeep!", questLevel = 23, requiredLevel = 18, startedBy = { { 1 } } },
  [7002] = { name = "Encrusted Tail Fins", questLevel = 21, requiredLevel = 16, startedBy = { { 1 } } },
  [7006] = { name = "The Tower of Althalaxx", questLevel = 26, requiredLevel = 25, startedBy = { { 3 } } },
  [1275] = { name = "Researching the Corruption", questLevel = 24, requiredLevel = 18, startedBy = { { 2 } } },
  [7010] = { name = "Beached Sea Creature", questLevel = 30, requiredLevel = 26, startedBy = { { 3 } } },
  [7011] = { name = "The Absent Minded Prospector", questLevel = 24, requiredLevel = 18, startedBy = { { 3 } },
             preQuestSingle = { 7012 } },
  [7012] = { name = "Bashal'Aran", questLevel = 20, requiredLevel = 15, startedBy = { { 3 } } },
}
local db = { QuestPointers = {}, NPCPointers = {}, npcFlags = {} }
for id in pairs(quests) do db.QuestPointers[id] = true end
function db.GetQuest(a, b) return quests[b or a] end
function db.GetNPC(a, b) return npcs[b or a] end
function db.QueryQuestSingle(id, field) local q = quests[id] return q and q[field] end
function db.QueryNPCSingle(id, field) local n = npcs[id] return n and n[field] end
function db.QueryObjectSingle() return nil end
function db.QueryItemSingle() return nil end
function db.GetQuestTagInfo() end
function db.IsDoable(id) local q = quests[id] return q and (q.requiredLevel or 0) <= UnitLevel("player") end
-- what Rossmann's Questie sent: [Murkdeep] half done, the beach crabs not, Gershala's quest done, two the player cannot take
TEST_remote = {
  [7001] = { Rossmann = { { index = 1, id = 9, type = "m", fulfilled = 0, required = 1, finished = false } } },
  [1275] = { Rossmann = { { index = 1, id = 8, type = "i", fulfilled = 8, required = 8, finished = true } } },
  [7010] = { Rossmann = { { index = 1, id = 7, type = "i", fulfilled = 1, required = 5, finished = false } } },
  [7011] = { ["Rossmann-Forever"] = {} },
}
local comms = { remoteQuestLogs = TEST_remote }
QuestieLoader = { ImportModule = function(_, name)
  if name == "QuestieDB" then return db elseif name == "QuestieComms" then return comms end
  error("no module") end }
C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or nil end
-- the party: Rossmann about 20 m south of the player
TEST_party = { name = "Rossmann", level = 27, class = "Hunter", x = 0.371, y = 0.446 }
GetNumGroupMembers = function() return TEST_party and 2 or 0 end
local unitExists, unitName, unitLevel, unitClass = UnitExists, UnitName, UnitLevel, UnitClass
local function isHim(u) return TEST_party and (u == "party1" or u == TEST_party.name) end
UnitExists = function(u) if isHim(u) then return true end return unitExists(u) end
UnitName = function(u) if isHim(u) then return TEST_party.name, nil end return unitName(u) end
UnitLevel = function(u) if isHim(u) then return TEST_party.level end return unitLevel(u) end
UnitClass = function(u) if isHim(u) then return TEST_party.class, "HUNTER", 3 end return unitClass(u) end
local connected, dead = UnitIsConnected, UnitIsDeadOrGhost
UnitIsConnected = function(u) if isHim(u) then return true end return connected(u) end
local bestMap, position = C_Map.GetBestMapForUnit, C_Map.GetPlayerMapPosition
C_Map.GetBestMapForUnit = function(u) if isHim(u) then return TEST_party.map or 1439 end return bestMap(u) end
C_Map.GetPlayerMapPosition = function(m, u)
  if isHim(u) then return m == (TEST_party.map or 1439) and { x = TEST_party.x, y = TEST_party.y } or nil end
  return position(m, u)
end
"""


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game").start()
    s.lua(PARTY)
    s.slash("/fai")
    s.run(6)  # past the loading screen
    yield s
    s.close()


def py(sim, code, *args):
    return sim.to_py(sim.lua(code, *args))


def shown(sim) -> str:
    return " | ".join(sim.texts())


def test_the_party_section_says_what_each_member_has_and_lacks(sim):
    party = py(sim, "return ForeverAI.Collect.Party()")
    (m,) = party["members"]
    assert (m["name"], m["level"], m["class"], m["questie"]) == ("Rossmann", 27, "Hunter", True)
    assert 15 <= m["meters"] <= 30  # (about 20 m south, on the player's map)
    # the player's quests he has too, with how far he is with them
    assert m["both"] == [{"title": "WANTED: Murkdeep!", "progress": "0/1"}]
    theirs = {q["title"]: q for q in m["theirs"]}
    # his quests: the player can take Gershala's (he has it done), not the one of level 26 nor one whose quest before
    # it the player has not done (Questie's data); his are sent by "Name-Realm" too
    gershala = theirs["Researching the Corruption"]
    assert gershala["canTake"] is True and gershala["done"] is True
    assert (gershala["giver"], gershala["where"]) == ("Gershala Nightwhisper", "Darkshore 38.3, 43.1")
    assert theirs["Beached Sea Creature"] == {"id": 7010, "title": "Beached Sea Creature", "level": 30, "minLevel": 26,
                                              "progress": "1/5", "giver": "Thundris Windweaver",
                                              "where": "Darkshore 37.4, 40.1", "why": "opens at level 26",
                                              "canTake": False}
    assert theirs["The Absent Minded Prospector"]["why"] == "needs [Bashal'Aran] first"
    assert m["theirs"][0]["canTake"] is True  # (the ones the player can take first)
    # the player's quests he lacks: which the game lets the player share; one opens later for him? (no: 25 <= 27)
    assert m["lacks"] == [{"title": "Encrusted Tail Fins", "share": False},
                          {"title": "The Tower of Althalaxx", "share": False}]
    sim.lua("TEST_push[7002] = true TEST_party.level = 22")
    lacks = py(sim, "return ForeverAI.Collect.Party()")["members"][0]["lacks"]
    assert lacks == [{"title": "Encrusted Tail Fins", "share": True},
                     {"title": "The Tower of Althalaxx", "share": False, "opensAt": 25}]
    # in another zone: the zone, not a distance
    sim.lua("TEST_party.map = 1440")
    far = py(sim, "return ForeverAI.Collect.Party()")["members"][0]
    assert far["zone"] == "Ashenvale" and "meters" not in far
    # it goes to the AI with every question
    assert py(sim, "return ForeverAI.Collect.sections.party()")["members"][0]["name"] == "Rossmann"
    assert sim.errors == []


def test_without_questie_in_his_game_only_the_players_quests_he_has_are_known(sim):
    sim.lua("for k in pairs(TEST_remote) do TEST_remote[k] = nil end "
            "C_QuestLog.IsUnitOnQuest = function(unit, id) return unit == 'party1' and id == 7002 end")
    (m,) = py(sim, "return ForeverAI.Collect.Party()")["members"]
    assert m["questie"] is False and m["both"] == [{"title": "Encrusted Tail Fins"}]
    assert "theirs" not in m and "lacks" not in m
    # alone, or in a raid: no party section
    sim.lua("TEST_party = nil")
    assert py(sim, "return ForeverAI.Collect.Party()") is None
    sim.lua("TEST_party = { name = 'Rossmann', level = 27, class = 'Hunter', x = 0.37, y = 0.44 } IsInRaid = function() return true end")
    assert py(sim, "return ForeverAI.Collect.Party()") is None
    assert sim.errors == []


# ------------------------------------------------------------------------ the route shared with the party
ROUTE = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = "The route for both of you.", routePlanned = true,
  shareRoute = %s, marks = {
    { title = "1. Sentinel Glynda Nal'Shea", x = 37.7, y = 43.4, zone = "Darkshore",
      tasks = { { kind = "pick_up", quest = "Encrusted Tail Fins", id = 7002 } } },
    { title = "2. Reef Crawler", x = 34.0, y = 54.0, zone = "Darkshore", mob = "Reef Crawler",
      tasks = { { kind = "objective", quest = "Encrusted Tail Fins", id = 7002, label = "Encrusted Tail Fin" } } },
    { title = "3. To Ashenvale", x = 44.0, y = 92.0, zone = "Darkshore", tasks = { { kind = "travel", zone = "Ashenvale" } } },
    { title = "4. Blackfathom Deeps entrance", x = 14.5, y = 14.2, zone = "Ashenvale", note = "the portal on the Zoram Strand" } } })"""


def sent(sim) -> list[dict]:
    out = sim.to_py(sim._wow.addonMessages) or []
    sim.lua("for k in pairs(...) do (...)[k] = nil end", sim._wow.addonMessages)
    return out


def test_a_route_planned_for_the_party_goes_to_it_and_again_when_it_changes(sim):
    sim.lua("table.remove(TEST_log, 2)")  # (the beach crabs' quest is still to be picked up)
    sim.lua(ROUTE % (2, "true"))
    sim.run(1)
    msgs = sent(sim)
    assert msgs and all(m["prefix"] == "ForeverAI" and m["channel"] == "PARTY" for m in msgs)
    assert all(len(m["text"]) <= 255 for m in msgs)
    # what went: the stops left, as places with what is to be done there
    stops = py(sim, "return ForeverAI.Share.decode(...)", "".join(m["text"].split(":", 3)[3] for m in msgs))
    assert [s["title"] for s in stops] == ["1. Sentinel Glynda Nal'Shea", "2. Reef Crawler", "3. To Ashenvale",
                                          "4. Blackfathom Deeps entrance"]
    assert stops[0]["note"] == "pick up Encrusted Tail Fins" and stops[1]["mob"] == "Reef Crawler"
    assert stops[2]["tasks"] == [{"kind": "travel", "zone": "Ashenvale"}] and stops[3]["note"] == "the portal on the Zoram Strand"
    assert (stops[3]["x"], stops[3]["y"], stops[3]["zone"]) == (14.5, 14.2, "Ashenvale")
    # a stop done: the route goes again, once, a moment later
    sim.lua('table.insert(TEST_log, { id = 7002, title = "Encrusted Tail Fins" })')
    sim.lua("ForeverAI.Map.Check()")
    sim.run(5)
    again = sent(sim)
    assert again
    left = py(sim, "return ForeverAI.Share.decode(...)", "".join(m["text"].split(":", 3)[3] for m in again))
    assert [s["title"] for s in left] == ["1. Reef Crawler", "2. To Ashenvale", "3. Blackfathom Deeps entrance"]
    # a route of the player's own: no longer shared
    sim.lua(ROUTE % (3, "false"))
    sim.run(5)
    assert sent(sim) == [] and py(sim, "return ForeverAI.char.sharing") is None
    # /fai share: the route on the map now
    sim.slash("/fai share")
    sim.run(1)
    assert sent(sim) and py(sim, "return ForeverAI.char.sharing") is True
    # alone: nothing to share with
    sim.lua("TEST_party = nil ForeverAI.char.sharing = nil")
    sim.slash("/fai share")
    sim.run(1)
    assert sent(sim) == [] and py(sim, "return ForeverAI.char.sharing") is None
    assert sim.errors == []


def deliver(sim, msgs, sender="Rossmann"):
    for m in msgs:  # (the game's strings are bytes in the simulator's Lua)
        sim._wow.fire(b"CHAT_MSG_ADDON", m["prefix"].encode(), m["text"].encode(), m["channel"].encode(), sender.encode())
    sim.run(1)


def test_a_route_a_member_shares_is_followed_with_a_click_and_kept_up(sim):
    # what Rossmann's ForeverAI sends: made here, then received as from him
    sim.lua("table.remove(TEST_log, 2)")
    sim.lua(ROUTE % (2, "true"))
    sim.run(1)
    msgs = sent(sim)
    sim.lua("ForeverAI.Map.Clear('test') ForeverAI.char.sharing = nil")
    # in another order (the game keeps it, but nothing depends on it), and somebody outside the party is not heard
    deliver(sim, msgs, sender="Stranger")
    assert "shares their route" not in shown(sim)
    deliver(sim, list(reversed(msgs)), sender="Rossmann-Forever")
    seen = shown(sim)
    assert "Rossmann shares their route (4 stops; nothing changes until you follow it)" in seen
    assert py(sim, "return ForeverAI.Map.Count()") == 0
    sim.click("Follow")
    sim.run(1)
    assert "Following Rossmann's route: its changes come by themselves." in shown(sim)
    marks = py(sim, "return ForeverAI.char.marks")
    assert [m["title"] for m in marks] == ["1. Sentinel Glynda Nal'Shea", "2. Reef Crawler", "3. To Ashenvale",
                                          "4. Blackfathom Deeps entrance"]
    assert "tasks" not in marks[0] and marks[0]["note"] == "pick up Encrusted Tail Fins"
    assert marks[2]["tasks"] == [{"kind": "travel", "zone": "Ashenvale"}]
    assert py(sim, "return ForeverAI.char.following") == "Rossmann"
    assert py(sim, "return ForeverAI.char.routePlanned") is None  # (his route: the AI here does not update it)
    # his route changes: it comes by itself
    deliver(sim, [{"prefix": "ForeverAI", "channel": "PARTY",
                   "text": "R1:4242:1/1:34.0~54.0~Darkshore~Reef Crawler~for Encrusted Tail Fins~Reef Crawler~"}])
    assert "Rossmann's route changed: 1 stop left." in shown(sim)
    assert [m["title"] for m in py(sim, "return ForeverAI.char.marks")] == ["1. Reef Crawler"]
    # his route done: so is the one followed
    deliver(sim, [{"prefix": "ForeverAI", "channel": "PARTY", "text": "R1:4243:1/1:"}])
    assert "Rossmann's route is done." in shown(sim) and py(sim, "return ForeverAI.Map.Count()") == 0
    # he leaves the party: nothing is followed any more
    sim.lua("TEST_party = nil")
    sim._wow.fire(b"GROUP_ROSTER_UPDATE")
    sim.run(1)
    assert py(sim, "return ForeverAI.char.following") is None
    assert sim.errors == []


def test_a_message_that_is_not_a_route_is_ignored(sim):
    for text in ["hello", "R1:1:2/1:x", "R1:1:1/1:", "R1:1:1/1:500~20~Darkshore~Far away~~~", "R1:1:1/99:a"]:
        sim._wow.fire(b"CHAT_MSG_ADDON", b"ForeverAI", text.encode(), b"PARTY", b"Rossmann")
    sim._wow.fire(b"CHAT_MSG_ADDON", b"Questie", b"R1:1:1/1:34.0~54.0~Darkshore~Reef Crawler~~~", b"PARTY", b"Rossmann")
    sim.run(1)
    assert "shares their route" not in shown(sim) and sim.errors == []
