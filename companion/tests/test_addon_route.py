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
    assert "Route · 3 stops" in shown(sim)
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
    assert "Route · 2 stops" in shown(sim) and not re.search(r"Sentinel Glynda · \d+ m", shown(sim))
    assert re.search(r"2\. Old Chest · \d+ m", shown(sim))  # the stops are numbered as they stand now
    assert stops(sim) == ["1. Befouled Webwood", "2. Old Chest", "Taken off the route by the player: Sentinel Glynda"]
    assert "Tracking Befouled Webwood" in shown(sim)

    sim.click("x", nth=4)  # the stop where the creature is: nothing left to watch for there
    sim.run(1)
    assert "Tracking Befouled Webwood" not in shown(sim)
    assert stops(sim) == ["1. Old Chest", "Taken off the route by the player: Sentinel Glynda, Befouled Webwood"]
    sim.click("x", nth=3)  # the last stop: the list closes with the route
    sim.run(1)
    assert "Route ·" not in shown(sim) and text(sim.lua(SECTION)) == "nothing"
    assert sim.errors == []


def test_a_stop_can_be_made_the_next_one(sim):
    sim.click("list")
    sim.run(1)
    sim.click("next", nth=2)  # the first stop has no such button: the third stop
    sim.run(1)
    assert stops(sim) == ["1. Old Chest", "2. Befouled Webwood", "3. Sentinel Glynda"]
    assert sim.errors == []


def test_the_ai_edits_the_route(sim):
    edit = "ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = 'Done.', %s })"
    sim.lua(edit % (2, 'route = { remove = { { title = "Sentinel Glynda", x = 38.1, y = 43.2 } } }'))
    sim.run(1)
    assert stops(sim) == ["1. Befouled Webwood", "2. Old Chest"]  # the AI's own change is not "by the player"
    assert "Tracking Befouled Webwood" in shown(sim) and "Route · 2 stops" in shown(sim)

    sim.lua(edit % (3, 'route = { order = { { title = "Old Chest", x = 41, y = 45 }, '
                       '{ title = "Befouled Webwood", x = 45, y = 40 } } }'))
    assert stops(sim) == ["1. Old Chest", "2. Befouled Webwood"]

    sim.lua(edit % (4, 'untrack = { "befouled webwood" }'))
    sim.run(1)
    assert "Tracking Befouled Webwood" not in shown(sim) and "Route · 2 stops" in shown(sim)

    # an edit and an extra stop in one answer: the edit first, then the new stop
    sim.lua(edit % (5, 'route = { remove = { { title = "Old Chest", x = 41, y = 45 } } }, marksReplace = false, '
                       'marks = { { title = "Dazalar", x = 50, y = 40 } }'))
    assert stops(sim) == ["1. Befouled Webwood", "2. Dazalar"]

    sim.lua(edit % (6, "route = { clear = true }"))
    sim.run(1)
    assert text(sim.lua(SECTION)) == "nothing" and "Route ·" not in shown(sim)
    assert sim.errors == []


def test_a_new_route_forgets_what_the_player_took_off(sim):
    sim.lua('ForeverAI.Map.Remove(2, "player")')
    assert stops(sim)[-1] == "Taken off the route by the player: Sentinel Glynda"
    sim.reload()
    sim.run(3)
    assert stops(sim)[-1] == "Taken off the route by the player: Sentinel Glynda"  # kept over a /reload
    sim.lua(ROUTE.replace("msgId = 1", "msgId = 9"))
    assert stops(sim) == ["1. Befouled Webwood", "2. Sentinel Glynda", "3. Old Chest"]
