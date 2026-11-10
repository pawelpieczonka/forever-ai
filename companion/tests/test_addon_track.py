"""Tracking a creature: the add-on watches the units the game shows nameplates for, puts a raid icon on a living one
with the tracked name, remembers where it last saw one, and tells the model whether one is in sight. In the add-on
simulator with a few nameplates made up for the test."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")

# nameplates and raid icons of the game, driven by the test: TEST_show({ {name=, dead=} ... }) sets what is in sight
FAKES = r"""
local units, icons = {}, {}
function TEST_show(list)
  units = {}
  for i, u in ipairs(list) do units["nameplate" .. i] = { name = u.name, dead = u.dead, guid = "Creature-0-1-2-3-" .. i .. "-" .. (u.id or i) } end
end
function TEST_icons()
  local out = {}
  for unit, icon in pairs(icons) do if units[unit] then out[#out + 1] = unit .. "=" .. icon end end
  table.sort(out)
  return table.concat(out, ",")
end
C_NamePlate.GetNamePlates = function()
  local out = {}
  for token in pairs(units) do out[#out + 1] = { namePlateUnitToken = token } end
  table.sort(out, function(a, b) return a.namePlateUnitToken < b.namePlateUnitToken end)
  return out
end
local name, dead, isPlayer, guid = UnitName, UnitIsDead, UnitIsPlayer, UnitGUID
UnitName = function(u) if units[u] then return units[u].name end return name(u) end
UnitIsDead = function(u) if units[u] then return units[u].dead or false end return dead and dead(u) end
UnitIsPlayer = function(u) if units[u] then return false end return isPlayer and isPlayer(u) end
UnitGUID = function(u) if units[u] then return units[u].guid end return guid and guid(u) end
GetRaidTargetIndex = function(u) return icons[u] end
SetRaidTarget = function(u, icon) icons[u] = (icon ~= 0) and icon or nil end
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


def test_a_living_tracked_creature_in_sight_gets_a_raid_icon(sim):
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua(TRACK)
    assert text(sim.lua(SECTION)) == "Strigid Screecher: none in sight now; not seen since it is tracked"
    sim.lua('TEST_show({ { name = "Strigid Owl" }, { name = "Strigid Screecher", dead = true }, { name = "Strigid Screecher" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_icons()")) == "nameplate3=8"  # the skull, on the living one only
    assert text(sim.lua(SECTION)) == "Strigid Screecher: 1 alive in sight now, marked with the skull raid icon"
    assert sim.lua("return ForeverAI.Collect.Snapshot().tracked ~= nil") is True  # goes with every question

    sim.lua('TEST_show({ { name = "Strigid Owl" } })')  # it left, or somebody killed it
    sim._wow.player.x = 0.45
    sim.run(1)
    assert text(sim.lua(SECTION)) == ("Strigid Screecher: none in sight now; last seen alive moments ago near "
                                      "Darkshore 40.0, 40.0 (299 m from here)")
    assert sim.errors == []


def test_stopping_takes_the_icon_off(sim):
    sim.lua(TRACK)
    sim.lua('TEST_show({ { name = "Strigid Screecher" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_icons()")) == "nameplate1=8"
    sim.lua("ForeverAI.Track.Stop()")
    assert text(sim.lua("return TEST_icons()")) == "" and text(sim.lua(SECTION)) == "nothing"
    sim.run(2)
    assert text(sim.lua("return TEST_icons()")) == ""  # and it is not put back


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


def test_tracking_survives_a_reload_and_two_creatures_get_two_icons(sim):
    sim.lua('ForeverAI.Track.Set({ { name = "Strigid Screecher" }, { name = "Befouled Webwood" } })')
    sim.reload()
    sim.lua(FAKES)
    sim.lua('TEST_show({ { name = "Befouled Webwood" }, { name = "Strigid Screecher" }, { name = "Befouled Webwood" } })')
    sim.run(1)
    assert text(sim.lua("return TEST_icons()")) == "nameplate1=7,nameplate2=8"  # the second creature gets the cross
    assert text(sim.lua(SECTION)) == ("Strigid Screecher: 1 alive in sight now, marked with the skull raid icon | "
                                      "Befouled Webwood: 2 alive in sight now, marked with the cross raid icon")
    assert sim.errors == []


ANSWER = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "Go there.",
  marks = { { title = "1. Strigid Screecher", x = 45, y = 40 }, { title = "2. Dazalar", x = 50, y = 40 } },
  track = { { name = "Strigid Screecher", quest = "Taming the Beast" } } })"""


def shown(sim) -> str:
    return " | ".join(sim.texts())


def test_strip_shows_what_goes_on_and_ends_it(sim, tmp_path):
    sim.slash("/fai")
    assert "Tracking Strigid" not in shown(sim) and "Route ·" not in shown(sim)
    sim.lua(ANSWER)
    sim.run(1)
    assert "Tracking Strigid Screecher · none in sight" in shown(sim) and "Route · 2 stops" in shown(sim)
    assert "Watching for Strigid Screecher: a living one in sight gets a raid icon." in shown(sim)
    sim.lua('TEST_show({ { name = "Strigid Screecher" }, { name = "Strigid Screecher" } })')
    sim.run(1)
    assert "Tracking Strigid Screecher · 2 in sight" in shown(sim)
    sim.screenshot(tmp_path / "strip.png")

    sim.click("x", nth=2)  # the first x closes the window; the chips follow
    sim.run(1)
    assert "Tracking Strigid" not in shown(sim) and "Route · 2 stops" in shown(sim)
    assert text(sim.lua("return TEST_icons()")) == ""
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
