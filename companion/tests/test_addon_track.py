"""Tracking a creature: the add-on watches the units the game shows it (nameplates, the target, the mouseover), puts
its own marker over the nameplate of a living one with the tracked name, says so with a sound and a line in the chat,
remembers where it last saw one, and tells the model whether one is in sight. In the add-on simulator with a few
nameplates made up for the test."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")

# nameplates of the game, driven by the test: TEST_show({ {name=, dead=} ... }) sets what is in sight.
# SetRaidTarget is here only to prove that it is never called: the real game blocks it for add-ons with a popup.
FAKES = r"""
local units, plates = {}, {}
function TEST_show(list)
  units = {}
  for i, u in ipairs(list) do units["nameplate" .. i] = { name = u.name, dead = u.dead, guid = "Creature-0-1-2-3-" .. (u.id and ("id-" .. u.id) or (i .. "-" .. i)) } end
end
function TEST_marked()
  local out = ForeverAI.Track.Marked()
  table.sort(out)
  return table.concat(out, ",")
end
C_NamePlate.GetNamePlates = function()
  local out = {}
  for token in pairs(units) do out[#out + 1] = { namePlateUnitToken = token } end
  table.sort(out, function(a, b) return a.namePlateUnitToken < b.namePlateUnitToken end)
  return out
end
C_NamePlate.GetNamePlateForUnit = function(u)
  if not units[u] then return nil end
  plates[u] = plates[u] or CreateFrame("Frame", nil, UIParent)
  return plates[u]
end
local name, dead, isPlayer, guid = UnitName, UnitIsDead, UnitIsPlayer, UnitGUID
UnitName = function(u) if units[u] then return units[u].name end return name(u) end
UnitIsDead = function(u) if units[u] then return units[u].dead or false end return dead and dead(u) end
UnitIsPlayer = function(u) if units[u] then return false end return isPlayer and isPlayer(u) end
UnitGUID = function(u) if units[u] then return units[u].guid end return guid and guid(u) end
TEST_sounds = 0
PlaySound = function() TEST_sounds = TEST_sounds + 1 return true end
SetRaidTarget = function() TEST_raid_icon_set = true end
"""
SECTION = "local s = ForeverAI.Track.Section() return s and table.concat(s, ' | ') or 'nothing'"
TRACK = 'ForeverAI.Track.Set({ { name = "Strigid Screecher", quest = "Taming the Beast" } })'


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game").start()
    s.lua(FAKES)
    yield s
    s.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def shown(sim) -> str:
    return " | ".join(sim.texts())


def test_a_living_tracked_creature_in_sight_is_marked_and_announced(sim):
    sim.slash("/fai")
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua(TRACK)
    assert text(sim.lua(SECTION)) == "Strigid Screecher: none in sight now; not seen since it is tracked"
    sim.lua('TEST_show({ { name = "Strigid Owl" }, { name = "Strigid Screecher", dead = true }, { name = "Strigid Screecher" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_marked()")) == "nameplate3"  # the living one only
    assert text(sim.lua(SECTION)) == "Strigid Screecher: 1 alive in sight now, highlighted on the screen"
    assert sim.lua("return TEST_sounds") == 1
    assert sim.lua("return ForeverAI.Collect.Snapshot().tracked ~= nil") is True  # goes with every question
    sim.run(5)
    assert sim.lua("return TEST_sounds") == 1  # said once, not twice a second

    sim.lua('TEST_show({ { name = "Strigid Owl" } })')  # it left, or somebody killed it
    sim._wow.player.x = 0.45
    sim.run(1)
    assert text(sim.lua("return TEST_marked()")) == ""
    assert text(sim.lua(SECTION)) == ("Strigid Screecher: none in sight now; last seen alive moments ago near "
                                      "Darkshore 40.0, 40.0 (299 m from here)")
    sim.run(20)
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')  # another one, a while later: said again
    sim.run(1)
    assert sim.lua("return TEST_sounds") == 2
    assert sim.lua("return TEST_raid_icon_set") is None  # the game blocks that call for add-ons
    assert sim.errors == []


def test_stopping_takes_the_marker_off(sim):
    sim.lua(TRACK)
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_marked()")) == "nameplate1"
    sim.lua("ForeverAI.Track.Stop()")
    assert text(sim.lua("return TEST_marked()")) == "" and text(sim.lua(SECTION)) == "nothing"
    sim.run(2)
    assert text(sim.lua("return TEST_marked()")) == ""  # and it is not put back


def test_tracking_ends_when_the_objective_is_done(sim):
    sim.lua("""
      TEST_done = false
      C_QuestLog.GetNumQuestLogEntries = function() return 1 end
      C_QuestLog.GetInfo = function(i) return { title = "Taming the Beast", questID = 6102 } end
      C_QuestLog.GetQuestObjectives = function(id) return { { text = "Tame a Strigid Screecher", finished = TEST_done } } end
      C_QuestLog.IsComplete = function(id) return TEST_done end
    """)
    sim.lua(TRACK)
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    assert text(sim.lua(SECTION)).startswith("Strigid Screecher: none in sight now")
    sim.lua("TEST_done = true")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    assert text(sim.lua(SECTION)) == "nothing"


def test_tracking_survives_a_reload_and_every_living_one_is_marked(sim):
    sim.lua('ForeverAI.Track.Set({ { name = "Strigid Screecher" }, { name = "Befouled Webwood" } })')
    sim.reload()
    sim.lua(FAKES)
    sim.lua('TEST_show({ { name = "Befouled Webwood" }, { name = "Strigid Screecher" }, { name = "Befouled Webwood" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_marked()")) == "nameplate1,nameplate2,nameplate3"
    assert text(sim.lua(SECTION)) == ("Strigid Screecher: 1 alive in sight now, highlighted on the screen | "
                                      "Befouled Webwood: 2 alive in sight now, highlighted on the screen")
    assert sim.errors == []


ANSWER = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "Go there.",
  marks = { { title = "1. Strigid Screecher", x = 45, y = 40 }, { title = "2. Dazalar", x = 50, y = 40 } },
  track = { { name = "Strigid Screecher", quest = "Taming the Beast" } } })"""


def test_strip_shows_what_goes_on_and_ends_it(sim, tmp_path):
    sim.slash("/fai")
    sim.lua('SetCVar("nameplateMaxDistance", "100")')  # nothing left to widen: no button on the chip
    assert "Tracking Strigid" not in shown(sim) and "Route (" not in shown(sim)
    sim.lua(ANSWER)
    sim.run(1)
    assert "Tracking Strigid Screecher (0)" in shown(sim) and "Route (2)" in shown(sim)
    assert "Watching for Strigid Screecher: a living one in sight is marked on the screen and announced." in shown(sim)
    sim.lua('TEST_show({ { name = "Strigid Screecher" }, { name = "Strigid Screecher" } })')
    sim.run(1)
    assert "Tracking Strigid Screecher (2)" in shown(sim) and "Strigid Screecher is in sight (2)" in shown(sim)
    sim.screenshot(tmp_path / "strip.png")

    sim.click("x", nth=2)  # the first x closes the window; the chips follow
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route (2)" in shown(sim)
    assert text(sim.lua("return TEST_marked()")) == ""
    sim.click("x", nth=2)
    sim.run(1)
    assert "Route (" not in shown(sim) and sim.lua("return ForeverAI.Map.Count()") == 0
    assert sim.errors == []


def test_new_marks_without_a_creature_end_the_tracking(sim):
    sim.slash("/fai")
    sim.lua(ANSWER)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "Turn it in.", marks = { { title = "Dazalar", x = 50, y = 40 } } })')
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route (1)" in shown(sim)
    sim.lua(ANSWER.replace("msgId = 1", "msgId = 3, marksReplace = false"))  # an extra stop: the earlier marks stay
    sim.run(1)
    assert "Tracking Strigid Screecher" in shown(sim)
    assert "Route (2)" in shown(sim)  # Dazalar is on the route already: not marked twice
    sim.slash("/fai stop")
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route (" not in shown(sim)


# The player's real game showed nameplates only for the target and for units in a fight (nameplateShowAll = 0): the
# creature stood next to them, the add-on saw nothing and the answer said "none nearby".
LOOK_AT = r"""
local exists, name, dead = UnitExists, UnitName, UnitIsDead
local seen = {}
function TEST_unit(token, unit) seen[token] = unit end
UnitExists = function(u) if seen[u] ~= nil then return seen[u] and true or false end return exists and exists(u) end
UnitName = function(u) if seen[u] then return seen[u].name end return name(u) end
UnitIsDead = function(u) if seen[u] then return seen[u].dead or false end return dead and dead(u) end
"""


def test_in_a_fight_the_addon_says_it_cannot_see_and_listens_once_it_is_over(sim):
    """The game shows nameplates only for the target and in a fight, and does not let that be changed in a fight."""
    sim.lua('SetCVar("nameplateShowEnemies", "1") SetCVar("nameplateShowAll", "0")')
    sim.lua("InCombatLockdown = function() return true end")
    sim.lua(TRACK)
    sim.run(1)
    assert text(sim.lua(SECTION)) == (
        "Strigid Screecher: not seen, but the game shows nameplates only for the target and for units in a fight "
        "(nameplates of all enemies are off), so one can be near without being seen")
    assert text(sim.lua("local s = ForeverAI.Track.State()[1] return s.name .. '|' .. s.alive .. '|' .. tostring(s.blind)")) == \
        "Strigid Screecher|0|true"
    sim.lua("InCombatLockdown = function() return false end")
    sim._wow.fire(b"PLAYER_REGEN_ENABLED")
    sim.run(1)
    assert text(sim.lua('return GetCVar("nameplateShowAll")')) == "1"
    assert text(sim.lua(SECTION)) == "Strigid Screecher: none in sight now; not seen since it is tracked"


def test_the_target_and_the_mouseover_are_seen_without_nameplates(sim):
    sim.lua('SetCVar("nameplateShowAll", "0")')
    sim.lua("InCombatLockdown = function() return true end")  # nameplates cannot be turned on yet
    sim.lua(LOOK_AT)
    sim.lua(TRACK)
    sim.lua('TEST_unit("mouseover", { name = "Strigid Screecher" })')
    sim._wow.fire(b"UPDATE_MOUSEOVER_UNIT")
    assert text(sim.lua(SECTION)) == "Strigid Screecher: 1 alive in sight now"  # no nameplate to put a marker on
    assert sim.lua("return TEST_sounds") == 1
    sim.lua('TEST_unit("mouseover", false) TEST_unit("target", { name = "Strigid Screecher", dead = true })')
    sim.run(1)
    assert text(sim.lua(SECTION)).startswith("Strigid Screecher: not seen, but the game shows nameplates only")
    assert "last seen alive moments ago" in text(sim.lua(SECTION))


def test_tracking_listens_by_itself_and_puts_the_players_settings_back(sim):
    """No button to press: while something is tracked the game shows the nameplates of all enemies, from as far as it
    allows (it caps the number itself); when tracking ends the player's own settings are back."""
    sim.slash("/fai")
    sim.lua('SetCVar("nameplateShowEnemies", "1") SetCVar("nameplateShowAll", "0") SetCVar("nameplateMaxDistance", "41")')
    sim.lua(ANSWER)
    sim.run(1)
    assert text(sim.lua('return GetCVar("nameplateShowAll") .. " " .. GetCVar("nameplateMaxDistance")')) == "1 100"
    assert "Tracking Strigid Screecher (0)" in shown(sim)
    assert "Show them" not in shown(sim) and "See farther" not in shown(sim)
    assert "the nameplates of all enemies are shown while a creature is tracked" in shown(sim)  # said once, in the chat
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')
    sim.run(1)
    assert "Tracking Strigid Screecher (1)" in shown(sim)

    sim.reload()  # still tracked after a /reload, and still the player's settings that are remembered
    sim.lua(FAKES)
    sim.run(3)
    assert text(sim.lua('return GetCVar("nameplateShowAll") .. " " .. GetCVar("nameplateMaxDistance")')) == "1 100"
    sim.slash("/fai stop")
    sim.run(1)
    assert text(sim.lua('return GetCVar("nameplateShowAll") .. " " .. GetCVar("nameplateMaxDistance")')) == "0 41"
    assert sim.errors == []


# The player's real game: the route named Bloodfeather Rogue (the nearest creature that drops the belts of
# [The Enchanted Glade]), a Bloodfeather Harpy that drops them just as well stood next to the player, and the strip
# said "none in sight".
QUESTIE = r"""
TEST_belts = 0
local npcs = { [2015] = "Bloodfeather Harpy", [2017] = "Bloodfeather Rogue", [2018] = "Bloodfeather Sorceress" }
local db = {
  QueryQuestSingle = function(id, field) if id == 937 and field == "objectives" then return { [3] = { { 5204 } } } end end,
  QueryItemSingle = function(id, field)
    if field == "name" then return "Bloodfeather Belt" end
    if field == "npcDrops" then return { 2017, 2015, 2018 } end
  end,
  QueryNPCSingle = function(id, field) if field == "name" then return npcs[id] end end,
}
QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db end error("no such module") end }
C_QuestLog.GetNumQuestLogEntries = function() return 1 end
C_QuestLog.GetInfo = function(i) return { title = "The Enchanted Glade", questID = 937 } end
C_QuestLog.GetQuestObjectives = function(id)
  return { { text = TEST_belts .. "/6 Bloodfeather Belt", finished = TEST_belts >= 6 } }
end
C_QuestLog.IsComplete = function(id) return false end
"""
BELTS = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "Go there.",
  marks = { { title = "1. Bloodfeather Rogue", x = 37.3, y = 37.8 } },
  track = { { name = "Bloodfeather Rogue", quest = "The Enchanted Glade" } } })"""


def test_every_creature_that_gives_the_objective_counts(sim):
    sim.slash("/fai")
    sim.lua(QUESTIE)
    sim.lua(BELTS)
    sim.run(1)
    assert "Tracking Bloodfeather Belt (0)" in shown(sim)
    assert ("Watching for Bloodfeather Rogue (or any other creature that counts for Bloodfeather Belt): a living one in "
            "sight is marked on the screen and announced.") in shown(sim)
    assert text(sim.lua(SECTION)) == (
        "Bloodfeather Rogue (also counts for Bloodfeather Belt: Bloodfeather Harpy, Bloodfeather Sorceress): "
        "none in sight now; not seen since it is tracked")
    sim.lua('TEST_show({ { name = "Bloodfeather Harpy" }, { name = "Strigid Owl" }, { name = "Bloodfeather Sorceress" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_marked()")) == "nameplate1,nameplate3"
    assert "Tracking Bloodfeather Belt (2)" in shown(sim)
    assert "Bloodfeather Harpy is in sight (2)" in shown(sim)  # the one that is there, by its own name
    assert text(sim.lua(SECTION)) == (
        "Bloodfeather Rogue (also counts for Bloodfeather Belt: Bloodfeather Harpy, Bloodfeather Sorceress): "
        "2 alive in sight now (Bloodfeather Harpy, Bloodfeather Sorceress), highlighted on the screen")

    sim.lua("TEST_belts = 6")  # the belts are collected: nothing left to look for, though the quest is not turned in
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    assert "Tracking Bloodfeather" not in shown(sim) and text(sim.lua(SECTION)) == "nothing"
    assert sim.errors == []


def test_a_creature_marked_without_its_quest_finds_it_in_the_log(sim):
    """mark_on_map names only the creature: the add-on looks up which quest of the log it is for."""
    sim.lua(QUESTIE)
    sim.lua('ForeverAI.Track.Set({ { name = "Bloodfeather Harpy" } })')
    sim.lua('TEST_show({ { name = "Bloodfeather Rogue" } })')
    sim.run(1)
    assert text(sim.lua(SECTION)).endswith("1 alive in sight now (Bloodfeather Rogue), highlighted on the screen")
    sim.lua("TEST_belts = 6")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    assert text(sim.lua(SECTION)) == "nothing"


def test_the_quest_database_may_come_later(sim):
    """Questie builds its database after login: until then one name is tracked, and the rest is asked for again."""
    sim.lua(QUESTIE)
    sim.lua("TEST_loader = QuestieLoader QuestieLoader = nil")
    sim.lua('ForeverAI.Track.Set({ { name = "Bloodfeather Rogue", quest = "The Enchanted Glade" } })')
    assert text(sim.lua(SECTION)) == "Bloodfeather Rogue: none in sight now; not seen since it is tracked"
    sim.lua("QuestieLoader = TEST_loader")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    assert text(sim.lua(SECTION)).startswith("Bloodfeather Rogue (also counts for Bloodfeather Belt: Bloodfeather Harpy,")


def test_the_same_creature_tracked_again_stays_as_it_was(sim):
    """The next answer often brings the same route: nothing about a creature that stays tracked starts over."""
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua(TRACK)
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')
    sim.run(1)
    sim.lua("TEST_cvars = 0 local set = SetCVar SetCVar = function(...) TEST_cvars = TEST_cvars + 1 return set(...) end")
    sim.lua(TRACK)
    sim.run(1)
    assert sim.lua("return TEST_sounds") == 1  # not announced again
    assert sim.lua("return TEST_cvars") == 0  # the nameplate settings are not put back and set again
    sim.lua("TEST_show({})")
    sim.run(1)
    sim.lua(TRACK)
    assert "last seen alive moments ago near Darkshore 40.0, 40.0" in text(sim.lua(SECTION))
    sim.lua('ForeverAI.Track.Set({ { name = "Befouled Webwood" } })')  # another creature: the old one is gone
    assert text(sim.lua(SECTION)) == "Befouled Webwood: none in sight now; not seen since it is tracked"


def test_the_strip_says_why_nothing_can_be_seen(sim):
    sim.slash("/fai")
    sim.lua(ANSWER)
    sim.run(3)
    assert shown(sim).count("the nameplates of all enemies are shown while a creature is tracked") == 1  # said once
    sim.lua('SetCVar("nameplateShowEnemies", "0")')  # the player's own key for hiding nameplates
    sim.run(1)
    assert "Tracking Strigid Screecher · nameplates off" in shown(sim)
    sim.lua("InCombatLockdown = function() return true end")
    sim._wow.fire(b"PLAYER_REGEN_DISABLED")
    sim.run(1)
    assert "Tracking Strigid Screecher · after the fight" in shown(sim)  # they come back on when the fight is over
    sim.lua("InCombatLockdown = function() return false end")
    sim._wow.fire(b"PLAYER_REGEN_ENABLED")
    sim.run(1)
    assert "Tracking Strigid Screecher (0)" in shown(sim)


def test_an_empty_quest_log_right_after_login_ends_nothing(sim):
    sim.lua(TRACK)
    sim.reload()
    sim.lua(FAKES)
    sim.lua("C_QuestLog.GetNumQuestLogEntries = function() return 0 end")  # the game has not filled it yet
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert text(sim.lua(SECTION)).startswith("Strigid Screecher:")
    sim.run(60)  # long after login an empty log is an empty log: the quest is gone
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert text(sim.lua(SECTION)) == "nothing"


def test_a_nameplate_that_comes_or_goes_is_looked_at_at_once(sim):
    sim.lua(TRACK)
    sim.run(1)
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')
    sim._wow.fire(b"NAME_PLATE_UNIT_ADDED")
    sim.run(0.1)  # in the next frame, well before the scan that runs twice a second
    assert text(sim.lua("return TEST_marked()")) == "nameplate1"
    sim.lua('TEST_show({ { name = "Strigid Owl" } })')  # the game gives the same nameplate to another unit
    sim._wow.fire(b"NAME_PLATE_UNIT_REMOVED")
    sim.run(0.1)
    assert text(sim.lua("return TEST_marked()")) == ""


# The player's real game: the route watched for Lasher Sproutling, then the AI added a mark for Wrathvine, which
# gives the same Dewy Lasher Fronds, and the strip showed "Tracking Dewy Lasher Frond (0)" twice.
def test_one_objective_is_tracked_once(sim):
    sim.slash("/fai")
    sim.lua(QUESTIE)
    sim.lua(BELTS)
    sim.run(1)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "More of them here.", marksReplace = false, '
            'marks = { { title = "Bloodfeather Harpy (Bloodfeather Belt)", x = 38.6, y = 40.5, mob = "Bloodfeather Harpy" } }, '
            'track = { { name = "Bloodfeather Harpy" } } })')
    sim.run(1)
    assert shown(sim).count("Tracking Bloodfeather Belt") == 1
    assert sim.lua("return #ForeverAI.Track.State()") == 1

    # two creatures of one objective in the same answer, and one that has nothing to do with it
    sim.lua('ForeverAI.Track.Set({ { name = "Bloodfeather Sorceress", quest = "The Enchanted Glade" }, '
            '{ name = "Bloodfeather Rogue", quest = "The Enchanted Glade" }, { name = "Strigid Owl" } })')
    names = "local out = {} for i, s in ipairs(ForeverAI.Track.State()) do out[i] = s.name end return table.concat(out, ',')"
    assert text(sim.lua(names)) == "Bloodfeather Sorceress,Strigid Owl"
    sim.lua('ForeverAI.Track.Stop("bloodfeather harpy")')  # by any name that counts for it
    assert text(sim.lua(names)) == "Strigid Owl"
    assert sim.errors == []


MAP = "local s = ForeverAI.Map.Section() return s and table.concat(s, ' | ') or 'nothing'"


def test_a_stop_with_a_watched_creature_stays_until_its_objective_is_done(sim):
    """"Oznacz mi je jeszcze raz": the stop left the map when the player reached it, with 0 of 6 collected."""
    sim.slash("/fai")
    sim.lua(QUESTIE)
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.30
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", '
            'marks = { { title = "1. Bloodfeather Rogue", x = 37.3, y = 37.8, mob = "Bloodfeather Rogue" }, '
            '{ title = "2. Sentinel Arynia", x = 38.3, y = 34.4 } }, '
            'track = { { name = "Bloodfeather Rogue", quest = "The Enchanted Glade" } } })')
    sim._wow.player.x, sim._wow.player.y = 0.373, 0.378  # at the stop: the belts are still to collect
    sim.run(5)
    assert text(sim.lua(MAP)).startswith("1. Bloodfeather Rogue — ") and "Route (2)" in shown(sim)

    sim.lua("TEST_belts = 6")  # the objective is done: the stop goes, wherever the player stands
    sim._wow.player.x = 0.36
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(3)
    assert "1. Bloodfeather Rogue — Darkshore 37.3, 37.8 (done) | 2. Sentinel Arynia — " in text(sim.lua(MAP))
    assert "Tracking" not in shown(sim).replace("Tracking:", "") and "Route (1/2)" in shown(sim)
    assert "Taken off the route by the player" not in text(sim.lua(MAP))

    sim._wow.player.x, sim._wow.player.y = 0.383, 0.344  # a stop without a creature is done when it is reached
    sim.run(5)
    assert text(sim.lua(MAP)).endswith("2. Sentinel Arynia — Darkshore 38.3, 34.4 (done)")
    assert sim.errors == []


def test_every_new_creature_is_announced_but_a_group_only_once(sim):
    """News is a creature that was not there: the next one after a kill, a group that walks in (one sound), the same
    ones found again after the player was away from them. At most one sound in five seconds."""
    sounds = lambda: sim.lua("return TEST_sounds")  # noqa: E731
    sim.lua(TRACK)
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 } })')
    sim.run(1)
    assert sounds() == 1
    # the first one is killed and another walks in a moment later, while the first is still counted in sight
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 }, { name = "Strigid Screecher", id = 2 } })')
    sim.run(1)
    assert sounds() == 1  # too soon after the last sound: it waits
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1, dead = true }, { name = "Strigid Screecher", id = 2 } })')
    sim.run(5)
    assert sounds() == 2  # and comes once the quiet time is over, because the new one is still there
    sim.run(10)
    assert sounds() == 2

    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 2 }, { name = "Strigid Screecher", id = 3 }, '
            '{ name = "Strigid Screecher", id = 4 }, { name = "Strigid Screecher", id = 5 } })')
    sim.run(2)
    assert sounds() == 3 and "Strigid Screecher is in sight (4)" in shown(sim)  # a group: one sound
    sim.run(10)
    assert sounds() == 3

    sim.lua("TEST_show({})")  # the same one out of sight for a while and back (the camera turned): not news
    sim.run(8)
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 3 } })')
    sim.run(2)
    assert sounds() == 3
    sim.lua("TEST_show({})")  # the player was away from them for a minute: found again, said again
    sim.run(70)
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 3 } })')
    sim.run(2)
    assert sounds() == 4

    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 3 }, { name = "Strigid Screecher", id = 6 } })')
    sim.run(1)  # a new one that is gone before the quiet time ends is not announced late
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 3 } })')
    sim.run(8)
    assert sounds() == 4
    assert sim.errors == []


# The player's real game: "I stand next to the same creatures and the sound plays again and again." Nameplates come
# and go with the camera and with every step a creature takes, and so does the difference between "in sight" and
# "near": none of that is news.
STAND = r"""
local seed = 7
local function rnd(n) seed = (seed * 1103515245 + 12345) % 2147483648 return seed % n end
function TEST_flicker()  -- which of the three creatures have a nameplate this second, and what the player points at
  local list = {}
  for id = 1, 3 do
    if rnd(3) > 0 then list[#list + 1] = { name = "Strigid Screecher", id = id } end
  end
  if rnd(4) == 0 then list[#list + 1] = { name = "Strigid Owl", id = 9 } end
  TEST_show(list)
end
"""


def _stand(sim, seconds=150):
    for _ in range(seconds):
        sim.lua("TEST_flicker()")
        sim.run(1)
    return sim.lua("return TEST_sounds")


def test_standing_next_to_the_same_creatures_is_silent(sim):
    sim.lua(STAND)
    sim.lua(TRACK)
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 }, { name = "Strigid Screecher", id = 2 }, '
            '{ name = "Strigid Screecher", id = 3 } })')
    sim.run(2)
    assert sim.lua("return TEST_sounds") == 1
    assert _stand(sim) == 1
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 }, { name = "Strigid Screecher", id = 4 } })')  # a new one
    sim.run(6)
    assert sim.lua("return TEST_sounds") == 2
    assert sim.errors == []


def test_standing_there_is_silent_with_the_search_by_name(sim):
    """A creature without a nameplate for a moment is "near", then "in sight" again: the same creature all along."""
    sim.lua(STAND)
    sim._wow.inRange[b"strigid screecher"] = True
    sim.lua(TRACK)
    sim.run(2)
    assert sim.lua("return TEST_sounds") == 1  # near
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 }, { name = "Strigid Screecher", id = 2 }, '
            '{ name = "Strigid Screecher", id = 3 } })')
    sim.run(6)
    assert sim.lua("return TEST_sounds") == 2  # now they are in sight: said once
    assert _stand(sim) == 2
    assert sim.errors == []


def test_standing_there_is_silent_without_the_search_by_name_and_without_ids(sim):
    """A game that gives no GUIDs, the search by name turned off: the count is all there is, and the target is not
    counted twice."""
    sim.lua(STAND)
    sim.lua("ForeverAI.db.lookByName = false UnitGUID = function() return nil end")
    sim.lua(LOOK_AT)
    sim.lua(TRACK)
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 }, { name = "Strigid Screecher", id = 2 }, '
            '{ name = "Strigid Screecher", id = 3 } })')
    sim.run(2)
    assert sim.lua("return TEST_sounds") == 1
    sim.lua('TEST_unit("target", { name = "Strigid Screecher" }) UnitIsUnit = function(a, b) return a == "target" and b == "nameplate1" end')
    sim._wow.fire(b"PLAYER_TARGET_CHANGED")
    sim.run(2)
    assert text(sim.lua(SECTION)).startswith("Strigid Screecher: 3 alive in sight now")  # the target is one of the three
    assert _stand(sim) == 1
    assert sim.errors == []


# Nameplates reach about 40 yards in this game whatever the setting says; the creatures stood farther off, in view.
# The game refuses an add-on's TargetUnit only when a unit of that name is in its range, which is much farther: the
# refusal is the answer and nothing is targeted. The game's "blocked" popup for it must never be seen.
POPUP = r"""TEST_popups, TEST_shown = 0, {}
function StaticPopup_Show(which, addon) TEST_shown[which] = true TEST_popups = TEST_popups + 1 end
function StaticPopup_Hide(which) TEST_shown[which] = nil end
function StaticPopup_Visible(which) return TEST_shown[which] end
UIParent:SetScript("OnEvent", function(_, event, addon) if event == "ADDON_ACTION_FORBIDDEN" then StaticPopup_Show(event, addon) end end)
UIParent:RegisterEvent("ADDON_ACTION_FORBIDDEN")
function TEST_seen() local n = 0 for _ in pairs(TEST_shown) do n = n + 1 end return n end"""


def test_a_creature_too_far_for_a_nameplate_is_found_by_its_name(sim):
    sim.lua(POPUP)
    sim.slash("/fai")
    sim.lua(ANSWER)
    sim.run(1)
    assert "Tracking Strigid Screecher (0)" in shown(sim)
    sim._wow.inRange[b"strigid screecher"] = True
    sim.run(2)
    assert "Tracking Strigid Screecher (near)" in shown(sim)
    assert "Strigid Screecher is near" in shown(sim) and sim.lua("return TEST_sounds") == 1
    assert text(sim.lua(SECTION)) == (
        "Strigid Screecher: Strigid Screecher is near but not in sight: the game has a unit of that name in its range, "
        "farther than nameplates reach; whether it is alive and where it stands is not known until the player is "
        "closer or targets it")
    sim.run(30)
    assert sim.lua("return TEST_sounds") == 1  # said once while it stays near
    sim.lua('TEST_show({ { name = "Strigid Screecher", id = 1 } })')  # close enough for a nameplate now
    sim.run(1)
    assert "Tracking Strigid Screecher (1)" in shown(sim) and sim.lua("return TEST_sounds") == 2
    sim.lua("TEST_show({})")
    sim._wow.inRange[b"strigid screecher"] = None
    sim.run(2)
    assert "Tracking Strigid Screecher (0)" in shown(sim)
    assert sim.lua("return TEST_seen()") == 0  # no popup was left on the screen
    sim.slash("/fai track")
    assert "looking by name: works" in shown(sim)
    sim.lua("ForeverAI.Track.Stop()")
    sim.run(1)
    assert sim.lua('return UIParent:IsEventRegistered("ADDON_ACTION_FORBIDDEN")') is True  # the game has its event back
    assert sim.errors == []


def test_a_popup_that_cannot_be_kept_away_ends_the_search_for_good(sim):
    """The real game showed "ForeverAI has been blocked..." once. If a game shows it whatever the add-on does, the
    search by name turns itself off, and stays off after a /reload."""
    sim.lua(POPUP)
    sim.lua("UIParent.UnregisterEvent = function() end StaticPopup_Hide = function() end")  # this game insists
    sim.lua(TRACK)
    sim._wow.inRange[b"strigid screecher"] = True
    sim.run(3)
    assert sim.lua("return TEST_popups") == 1  # once, never again
    assert sim.lua("return ForeverAI.db.lookByName") is False
    assert text(sim.lua(SECTION)) == "Strigid Screecher: none in sight now; not seen since it is tracked"
    sim.run(20)
    assert sim.lua("return TEST_popups") == 1


def test_the_search_by_name_can_be_turned_off(sim):
    sim.lua(POPUP)
    sim.slash("/fai")
    sim.click("Settings")
    sim.run(1)
    sim.click("Find tracked creatures beyond nameplate range")
    assert sim.lua("return ForeverAI.db.lookByName") is False
    sim.lua(TRACK)
    sim._wow.inRange[b"strigid screecher"] = True
    sim.run(3)
    assert text(sim.lua(SECTION)) == "Strigid Screecher: none in sight now; not seen since it is tracked"
    assert sim.lua("return TEST_popups") == 0


def test_a_game_that_answers_differently_is_not_asked(sim):
    sim.lua("TargetUnit = function() end")  # this game refuses nothing
    sim.lua(TRACK)
    sim._wow.inRange[b"strigid screecher"] = True
    sim.run(2)
    assert text(sim.lua(SECTION)) == "Strigid Screecher: none in sight now; not seen since it is tracked"
    sim.slash("/fai track")
    assert "looking by name: not available in this game" in shown(sim)
