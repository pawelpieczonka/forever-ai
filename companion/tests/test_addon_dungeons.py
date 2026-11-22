"""Dungeon runs in the add-on (see test_dungeon_runs.py for the app): the dungeons of the player's DungeonJournal
add-on for the AI (dungeon_plan), and the dungeon part of a long route, whose entrance is done once the player is inside
although the game gives no position there, and whose next part waits until they come out. In the add-on simulator,
without the app."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")


# The journal as ForeverDungeonJournal 2.1 keeps it (its namespace is a global), with Blackfathom Deeps as WoW Forever
# has it, for the simulator's level 24 night elf hunter in Auberdine. Quests the character has done or holds: TEST_done,
# TEST_log.
JOURNAL = r"""
TEST_done, TEST_log = {}, {}
C_QuestLog.IsQuestFlaggedCompleted = function(id) return TEST_done[id] == true end
C_QuestLog.GetLogIndexForQuestID = function(id) for i, q in ipairs(TEST_log) do if q == id then return i end end end
local fdj = { DB = {}, QUEST_START_MAPS = {}, QUEST_PREREQ_CHAINS = {} }
ForeverDungeonJournal_NS = fdj
fdj.DB["Blackfathom Deeps"] = {
  level = "24-32", location = "Zoram Strand, Ashenvale",
  description = "A flooded night elf ruin on the Zoram Strand, occupied by naga, murlocs and Twilight's Hammer cultists.",
  entrance = { mapID = 1440, x = 0.145, y = 0.142, label = "Blackfathom Deeps Entrance",
               detail = "Dungeon entrance on the Zoram Strand" },
  quests = {
    { id = 971, name = "Knowledge in the Deeps", faction = "Alliance", level = 23, requires = 18,
      objective = "Recover the Lorgalis Manuscript from Blackfathom Deeps.",
      pickup = "Gerrig Bonegrip, Forlorn Cavern, Ironforge (50.8, 5.6)", turnin = "Gerrig Bonegrip, Forlorn Cavern, Ironforge",
      note = "The Lorgalis Manuscript is inside a Pitted Iron Chest underwater in the Pool of Ask'ar.", liveXPFallback = 2750 },
    { id = 3765, name = "The Corruption Abroad", faction = "Alliance", hideFromMainList = true, level = 24, requires = 18 },
    { id = 1275, name = "Researching the Corruption", faction = "Alliance", level = 24, requires = 18,
      objective = "Collect 8 Corrupted Brain Stems from creatures in Blackfathom Deeps.",
      pickup = "Gershala Nightwhisper, Auberdine, Darkshore (38.3, 43.1)", turnin = "Gershala Nightwhisper, Auberdine, Darkshore",
      liveXPFallback = 2400 },
    { id = 1198, name = "In Search of Thaelrid", faction = "Alliance", level = 24, requires = 18,
      objective = "Find Argent Guard Thaelrid inside Blackfathom Deeps.",
      warning = "Warning: if not completed before accepting Blackfathom Villainy, this quest becomes unavailable.",
      pickup = "Dawnwatcher Shaedlass, Craftsmen's Terrace, Darnassus", turnin = "Argent Guard Thaelrid, inside Blackfathom Deeps",
      liveXPFallback = 2400 },
    { id = 1200, name = "Blackfathom Villainy", faction = "Alliance", level = 27, requires = 18,
      objective = "Defeat Twilight Lord Kelris and recover the Head of Kelris.",
      pickup = "Argent Guard Thaelrid, inside Blackfathom Deeps", turnin = "Dawnwatcher Selgorm, Darnassus", liveXPFallback = 3300 },
    { id = 1199, name = "Twilight Falls", faction = "Alliance", level = 25, requires = 26, liveXPFallback = 2550,
      objective = "Collect 10 Twilight Pendants.", pickup = "Argent Guard Manados, Craftsmen's Terrace, Darnassus (55, 24)",
      turnin = "Argent Guard Manados, Craftsmen's Terrace, Darnassus",
      requiredItems = { { 5359, "Lorgalis Manuscript", 1, "Pitted Iron Chest, Pool of Ask'ar" } } },
    { id = 6564, name = "Allegiance to the Old Gods", faction = "Horde", level = 22, requires = 17 },
    { id = 1001, name = "Dwarves Only", faction = "Alliance", level = 24, requires = 18, unavailableRaces = { "Night Elf" } },
  },
  bosses = { { name = "Ghamoo-ra", description = "A giant turtle in the Pool of Ask'ar." }, { name = "Lady Sarevess" },
             { name = "Lorgus Jett", rare = true }, { name = "Aku'mai" } },
}
fdj.DB["The Deadmines"] = { level = "17-26", location = "Westfall", quests = {},
  entrance = { mapID = 1436, x = 0.424, y = 0.719, detail = "The cave in Moonbrook" } }
fdj.DB["Gnomeregan"] = { level = "29-38", location = "Dun Morogh", quests = {} }
fdj.DB["Ragefire Chasm"] = { level = "13-18", location = "Orgrimmar", quests = {} }
fdj.QUEST_START_MAPS[1198] = { mapID = 1457, x = 0.555, y = 0.245, label = "Dawnwatcher Shaedlass — Craftsmen's Terrace",
                               targetName = "Dawnwatcher Shaedlass" }
fdj.QUEST_START_MAPS[1275] = { mapID = 1439, x = 0.383, y = 0.431, label = "Gershala Nightwhisper — Auberdine" }
fdj.QUEST_PREREQ_CHAINS[1200] = { { id = 1198, name = "In Search of Thaelrid" } }
"""


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game").start()
    s.lua(JOURNAL)
    s.slash("/fai")
    s.run(6)  # past the loading screen
    yield s
    s.close()


def py(sim, code, *args):
    return sim.to_py(sim.lua(code, *args))


def shown(sim) -> str:
    return " | ".join(sim.texts())


def test_the_dungeons_for_the_level_and_one_dungeon_with_its_quests_for_this_character(sim):
    listed = py(sim, "return ForeverAI.Dungeons.Plan()")
    # (levels 24-32 and 17-26 for a level 24 character; not 29-38 and 13-18), the lowest first
    assert [d["name"] for d in listed["dungeons"]] == ["The Deadmines", "Blackfathom Deeps"] and listed["level"] == 24
    bfd = listed["dungeons"][1]
    # its quests for an Alliance night elf (not the Horde's, not a hidden step, not one for other races); open now:
    # all but the one that opens at 26
    assert (bfd["quests"], bfd["open"], bfd["entrance"]) == (5, 4, "Ashenvale 14.5, 14.2")

    d = py(sim, "return ForeverAI.Dungeons.Plan('blackfathom')")
    assert (d["name"], d["min"], d["max"], d["character"]) == ("Blackfathom Deeps", 24, 32, 24)
    assert d["entrance"] == {"zone": "Ashenvale", "x": 14.5, "y": 14.2, "detail": "Dungeon entrance on the Zoram Strand"}
    quests = {q["title"]: q for q in d["quests"]}
    assert list(quests) == ["Knowledge in the Deeps", "Researching the Corruption", "In Search of Thaelrid",
                            "Blackfathom Villainy", "Twilight Falls"]
    # who gives it and where: the journal's own start place (its name for the NPC, the zone of the map), else its words
    gershala = quests["Researching the Corruption"]
    assert (gershala["giver"], gershala["where"], gershala["xp"]) == ("Gershala Nightwhisper", "Darkshore 38.3, 43.1", 2400)
    assert gershala["state"] == "can be taken now" and gershala["whereToGet"] == [
        "Collect 8 Corrupted Brain Stems from creatures in Blackfathom Deeps.: inside Blackfathom Deeps (no place on a zone map)"]
    thaelrid = quests["In Search of Thaelrid"]
    assert (thaelrid["giver"], thaelrid["where"]) == ("Dawnwatcher Shaedlass", "Darnassus 55.5, 24.5")
    assert thaelrid["turnedInside"] is True and thaelrid["warning"].startswith("Warning: if not completed")
    assert "where" not in quests["Knowledge in the Deeps"] and quests["Knowledge in the Deeps"]["giver"].startswith("Gerrig Bonegrip")
    assert quests["Knowledge in the Deeps"]["note"].startswith("The Lorgalis Manuscript is inside")
    # the next part of a chain, given inside: what it needs first
    villainy = quests["Blackfathom Villainy"]
    assert villainy["givenInside"] is True and villainy["state"] == "needs other quests first"
    assert villainy["needs"] == ["[In Search of Thaelrid]"]
    falls = quests["Twilight Falls"]
    assert falls["state"] == "opens at level 26" and falls["bring"] == "Lorgalis Manuscript (Pitted Iron Chest, Pool of Ask'ar)"
    assert [b["name"] for b in d["bosses"]] == ["Ghamoo-ra", "Lady Sarevess", "Lorgus Jett", "Aku'mai"]
    assert d["bosses"][2]["rare"] is True and d["bosses"][0]["about"] == "A giant turtle in the Pool of Ask'ar."

    # done, and in the quest log; the chain's first part in the log
    sim.lua("TEST_done[1275] = true TEST_log = { 1198 }")
    quests = {q["title"]: q for q in py(sim, "return ForeverAI.Dungeons.Plan('Blackfathom Deeps')")["quests"]}
    assert quests["Researching the Corruption"]["state"] == "done"
    assert quests["In Search of Thaelrid"]["state"] == "in the quest log"
    assert quests["Blackfathom Villainy"]["needs"] == ["[In Search of Thaelrid] (in the quest log)"]
    sim.lua("TEST_done[1198] = true TEST_log = {}")
    assert {q["title"]: q for q in py(sim, "return ForeverAI.Dungeons.Plan('Blackfathom Deeps')")["quests"]}[
        "Blackfathom Villainy"]["state"] == "can be taken now"

    # no such dungeon; no journal
    assert py(sim, "return ForeverAI.Dungeons.Plan('Stratholme')")["error"] == "the DungeonJournal has no dungeon called Stratholme"
    sim.lua("ForeverDungeonJournal_NS = nil")
    assert "not installed" in py(sim, "return ForeverAI.Dungeons.Plan('Blackfathom Deeps')")["error"]
    assert sim.errors == []


def test_the_app_asks_for_a_dungeon_and_gets_it(sim):
    sim.lua('ForeverAI.HandleRequests({ { id = "9-1", op = "dungeon_plan", dungeon = "Blackfathom" } })')
    sim.run(2)
    (result,) = [m for m in sim.sent_messages() if m.get("type") == "game_result"]
    assert result["id"] == "9-1" and result["result"]["name"] == "Blackfathom Deeps"
    assert result["result"]["entrance"]["zone"] == "Ashenvale"


# ------------------------------------------------------------------------ the dungeon part of a long route
LONG = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "Your long route is ready.", routePlanned = true,
  marks = { { title = "1. Blackfathom Deeps entrance", x = 14.5, y = 14.2, zone = "Ashenvale",
              tasks = { { kind = "dungeon", zone = "Blackfathom Deeps" } } } },
  longRoute = { id = "lr1", title = "Level 24 to 27", goal = 27, current = 1, ["end"] = 27.1, card = true,
    parts = { { title = "Blackfathom Deeps", zone = "Darkshore", ["then"] = { "Ashenvale" }, dungeon = "Blackfathom Deeps",
                from = 24, to = 25.6, quests = 3, xp = 9500, mins = 90, note = "with a group of five" },
              { title = "Back in Auberdine", zone = "Darkshore", from = 25.6, to = 27.1, quests = 3, xp = 21000 } } } })"""

INSIDE = """IsInInstance = function() return true, "party" end
GetInstanceInfo = function() return "Blackfathom Deeps", "party", 1, "Normal", 5, 0, false, 48 end
TEST_position = C_Map.GetPlayerMapPosition
C_Map.GetPlayerMapPosition = function() return nil end -- (the game gives no position inside an instance)"""
OUTSIDE = """IsInInstance = function() return false, "none" end
GetInstanceInfo = function() return "Kalimdor", "none", 0, "", 0, 0, false, 1 end
C_Map.GetPlayerMapPosition = TEST_position"""


def questions(sim) -> list[str]:
    return [m["text"] for m in sim.sent_messages() if m.get("type") == "question"]


def test_the_entrance_is_done_inside_and_the_next_part_waits_until_the_player_is_out(sim):
    sim.lua(LONG)
    sim.run(1)
    seen = shown(sim)
    assert "1. Blackfathom Deeps (dungeon, Darkshore, Ashenvale)  level 24.0 -> 25.6 · 3 quests · about 1 h 30 min  on the map" in seen
    assert py(sim, "return ForeverAI.char.marks[1].tasks") == [{"kind": "dungeon", "zone": "Blackfathom Deeps"}]
    assert py(sim, "return ForeverAI.Map.List()[1].what") == "go into Blackfathom Deeps with your group"
    # at the entrance, outside: not done (the group is not in yet)
    p = sim._wow.player
    p.mapID, p.zone, p.x, p.y = 1440, "Ashenvale", 0.145, 0.142
    sim.run(3)
    assert py(sim, "return ForeverAI.Map.List()[1].done") is False
    sim.sent_messages()  # (what was sent so far)

    # inside: done, though the game gives no position there; the next part waits
    sim.lua(INSIDE)
    sim._wow.fire(b"PLAYER_ENTERING_WORLD")
    sim.run(4)
    assert py(sim, "return ForeverAI.Map.List()[1].done") is True
    assert ("Done: Blackfathom Deeps entrance. Next: Blackfathom Deeps, then the next part of the long route once you "
            "are out") in shown(sim)
    assert py(sim, "return ForeverAI.Map.LongSection()") == {"id": "lr1", "part": 1, "inside": True}
    sim.run(5)
    assert questions(sim) == []
    # the card says where the player is
    sim.click("parts")
    sim.run(1)
    assert "1. Blackfathom Deeps (dungeon, Darkshore, Ashenvale)  level 24.0 -> 25.6 · 3 quests · about 1 h 30 min  inside now" in shown(sim)
    # a /reload inside keeps it
    sim.reload()
    sim.lua(JOURNAL + INSIDE)
    sim.run(3)
    assert py(sim, "return ForeverAI.Map.DungeonHolds()") is True

    # dead, the ghost runs back from the graveyard outside: not out of the dungeon
    sim.lua(OUTSIDE + "\nUnitIsDeadOrGhost = function() return true end")
    sim._wow.fire(b"ZONE_CHANGED_NEW_AREA")
    sim.run(3)
    assert questions(sim) == [] and py(sim, "return ForeverAI.Map.DungeonHolds()") is True
    # alive outside: out, and the next part is asked for
    sim.lua("UnitIsDeadOrGhost = function() return false end")
    sim._wow.fire(b"PLAYER_UNGHOST")
    sim.run(5)
    assert questions(sim) == ["[Quest log changed] the player came out of Blackfathom Deeps, the dungeon of part 1 of the "
                              "long route. Put the next part on the map."]
    assert py(sim, "return ForeverAI.Map.LongSection()") == {"id": "lr1", "part": 1}
    assert sim.errors == []


def test_a_dungeon_is_known_by_the_games_name_for_it(sim):
    def inside(name, of):
        sim.lua(f"IsInInstance = function() return true, 'party' end GetInstanceInfo = function() return '{name}' end")
        return py(sim, "return ForeverAI.Map.InDungeon(...)", of)
    assert inside("Blackfathom Deeps", "Blackfathom Deeps") is True
    assert inside("The Deadmines", "Deadmines") is True
    assert inside("Scarlet Monastery", "Scarlet Monastery: Library") is True
    assert inside("Stormwind Stockade", "The Stockade") is True
    assert inside("Gnomeregan", "Blackfathom Deeps") is False
    sim.lua("IsInInstance = function() return false, 'none' end")
    assert py(sim, "return ForeverAI.Map.InDungeon('Blackfathom Deeps')") is False
    assert sim.errors == []
