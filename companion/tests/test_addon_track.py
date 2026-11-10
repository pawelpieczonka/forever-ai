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
  for i, u in ipairs(list) do units["nameplate" .. i] = { name = u.name, dead = u.dead, guid = "Creature-0-1-2-3-" .. i .. "-" .. (u.id or i) } end
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
    assert "Tracking Strigid" not in shown(sim) and "Route ·" not in shown(sim)
    sim.lua(ANSWER)
    sim.run(1)
    assert "Tracking Strigid Screecher · none in sight" in shown(sim) and "Route · 2 stops" in shown(sim)
    assert "Watching for Strigid Screecher: a living one in sight is marked on the screen and announced." in shown(sim)
    sim.lua('TEST_show({ { name = "Strigid Screecher" }, { name = "Strigid Screecher" } })')
    sim.run(1)
    assert "Tracking Strigid Screecher · 2 in sight" in shown(sim) and "Strigid Screecher is in sight (2)" in shown(sim)
    sim.screenshot(tmp_path / "strip.png")

    sim.click("x", nth=2)  # the first x closes the window; the chips follow
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route · 2 stops" in shown(sim)
    assert text(sim.lua("return TEST_marked()")) == ""
    sim.click("x", nth=2)
    sim.run(1)
    assert "Route ·" not in shown(sim) and sim.lua("return ForeverAI.Map.Count()") == 0
    assert sim.errors == []


def test_new_marks_without_a_creature_end_the_tracking(sim):
    sim.slash("/fai")
    sim.lua(ANSWER)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "Turn it in.", marks = { { title = "Dazalar", x = 50, y = 40 } } })')
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route · 1 stop" in shown(sim)
    sim.lua(ANSWER.replace("msgId = 1", "msgId = 3, marksReplace = false"))  # an extra stop: the earlier marks stay
    sim.run(1)
    assert "Tracking Strigid Screecher" in shown(sim) and "Route · 3 stops" in shown(sim)
    sim.slash("/fai stop")
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route ·" not in shown(sim)


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
    assert "Tracking Strigid Screecher · none in sight" in shown(sim)
    assert "Show them" not in shown(sim) and "See farther" not in shown(sim)
    assert "the nameplates of all enemies are shown while a creature is tracked" in shown(sim)  # said once, in the chat
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')
    sim.run(1)
    assert "Tracking Strigid Screecher · 1 in sight" in shown(sim)

    sim.reload()  # still tracked after a /reload, and still the player's settings that are remembered
    sim.lua(FAKES)
    sim.run(3)
    assert text(sim.lua('return GetCVar("nameplateShowAll") .. " " .. GetCVar("nameplateMaxDistance")')) == "1 100"
    sim.slash("/fai stop")
    sim.run(1)
    assert text(sim.lua('return GetCVar("nameplateShowAll") .. " " .. GetCVar("nameplateMaxDistance")')) == "0 41"
    assert sim.errors == []


def test_probe_tells_what_the_game_shows_of_an_object_next_to_the_player(sim):
    """/fai probe: a look at every way the game might show an add-on a herb, a vein or a chest (it has no nameplate).
    One of them is the "soft target": the game picks the nearest thing the player can interact with."""
    sim.lua("""
      SetCVar("SoftTargetInteract", "0")
      local exists, name, guid = UnitExists, UnitName, UnitGUID
      UnitExists = function(u) if u == "softinteract" then return GetCVar("SoftTargetInteract") == "3" end return exists(u) end
      UnitName = function(u) if u == "softinteract" then return "Silverleaf" end return name(u) end
      UnitGUID = function(u) if u == "softinteract" then return "GameObject-0-4444-1-2-1617-00006AF3" end return guid and guid(u) end
      C_VignetteInfo = { GetVignettes = function() return { "v1" } end,
                         GetVignetteInfo = function(id) return { name = "Battered Chest", type = 0 } end }
    """)
    sim.lua('SetCVar("SoftTargetInteractArc", "0") SetCVar("SoftTargetInteractRange", "10")')
    sim.slash("/fai probe")
    sim.run(2)
    assert text(sim.lua('return GetCVar("SoftTargetInteract") .. GetCVar("SoftTargetInteractArc")')) == "32"  # all around
    sim.run(8)
    lines = text(sim.lua("return table.concat(ForeverAI.char.lastProbe, ' || ')"))
    assert "softinteract with the game's own setting (SoftTargetInteract 0): nothing" in lines
    assert "soft target for 8 s: Silverleaf (GameObject, id 1617)" in lines
    assert "vignettes (1): Battered Chest" in lines
    # the player's settings are back
    assert text(sim.lua('return GetCVar("SoftTargetInteract") .. " " .. GetCVar("SoftTargetInteractArc") .. " " '
                        '.. GetCVar("SoftTargetInteractRange")')) == "0 0 10"
    assert "probe" in text(sim.lua("return ForeverAI.Collect.Snapshot().game.probe and 'probe' or 'none'"))
    assert sim.errors == []
