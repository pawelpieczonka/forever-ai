"""Keeping the route up to date: with the option on, a change in the quest log (a quest taken, an objective done, a
quest ready or gone) makes the add-on ask the AI to update the route on the map, the way the route was made. A route
change the AI proposes by itself waits for the player's Accept. In the add-on simulator, without the app: the
messages the add-on sends are read from its screenshots."""

import re

import pytest

from addonsim import Sim, install_game
from addonsim.sim import SimError

pytest.importorskip("lupa")

# the quest log, driven by the test
QUESTS = r"""
TEST_log = {
  { id = 937, title = "The Enchanted Glade", objectives = { { text = "3/6 Bloodfeather Belt", finished = false } } },
  { id = 938, title = "Not Dead Yet", objectives = { { text = "Find Byancie", finished = false },
                                                     { text = "Speak with Dazalar", finished = false } } },
}
local function quest(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
C_QuestLog.IsComplete = function(id) local q = quest(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = quest(id) return q and q.objectives or {} end
"""
ROUTE = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true,
  marks = { { title = "1. Bloodfeather Rogue", x = 37.3, y = 37.8, mob = "Bloodfeather Rogue" },
            { title = "2. Byancie", x = 55.3, y = 56.8 } },
  track = { { name = "Bloodfeather Rogue", quest = "The Enchanted Glade" } } })"""
SECTION = "local s = ForeverAI.Map.Section() return s and table.concat(s, ' | ') or 'nothing'"


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=160)  # an unanswered request reads an inbox every two seconds
    s = Sim(tmp_path / "game").start()
    s.lua(QUESTS)
    s.slash("/fai")
    s.run(6)  # past the loading screen: the quest log the game shows now is where changes are counted from
    yield s
    s.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def shown(sim) -> str:
    return " | ".join(sim.texts())


def switch(sim):
    """The route's switch on its strip as the player sees it: "on" (green arrows), "off" (red), None when there is none."""
    for state in ("on", "off"):
        try:
            w = sim._find(f"Auto-update: {state}")  # an icon: found by its tooltip
        except SimError:
            continue
        color = sim.lua("local w = ... local r, g = w.icon:GetVertexColor() return g > r and 'green' or 'red'", w)
        assert text(color) == ("green" if state == "on" else "red")
        return state
    return None


def asked(sim) -> list[str]:
    """The questions the add-on sent since the last look, and the answer to each is delivered (nothing else waits)."""
    out = []
    for m in sim.sent_messages():
        if m.get("type") == "question":
            out.append(("auto: " if m.get("auto") else "") + m["text"])
            sim.deliver({"kind": "answer", "final": True, "msgId": m["msgId"], "text": "Updated."})
            sim.run(1)
    return out


SETTING = "New routes: update when my quests change"


def take_a_quest(sim, qid=939, title="Crown of the Earth"):
    sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = {} })' % (qid, title))
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(10)  # a quest taken is waited on a little longer: the next one often follows


def test_a_new_route_starts_as_settings_say(sim):
    """Off by default; Settings decide how a new route starts, and the strip shows how this one is."""
    assert sim.lua("return ForeverAI.db.autoRoute") is False
    sim.lua(ROUTE)
    sim.run(1)
    assert "Route (2)" in shown(sim) and switch(sim) == "off"
    take_a_quest(sim)
    assert asked(sim) == []  # off: the route stays as it is

    sim.click("Settings")
    sim.run(1)
    sim.click(SETTING)
    assert sim.lua("return ForeverAI.db.autoRoute") is True
    sim.click("Chat")
    sim.run(1)
    assert switch(sim) == "off"  # the route on the map keeps its own switch
    sim.lua(ROUTE.replace("msgId = 1", "msgId = 2"))  # also when it is marked again
    sim.run(1)
    assert switch(sim) == "off"
    sim.lua("ForeverAI.Map.Clear()")
    sim.lua(ROUTE.replace("msgId = 1", "msgId = 4"))  # a new route, with none on the map: as Settings say
    sim.run(1)
    assert switch(sim) == "on"

    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 3, text = "There.", '
            'marks = { { title = "Dazalar", x = 56.7, y = 59.5 } } })')
    sim.run(1)
    assert "Route (1)" in shown(sim) and switch(sim) is None  # one place to go is not a route
    sim.click("Settings")
    sim.run(1)
    sim.click(SETTING)
    assert sim.lua("return ForeverAI.db.autoRoute") is False


def test_each_route_has_its_own_switch(sim):
    sim.lua(ROUTE)
    sim.run(1)
    sim.click("Auto-update: off")
    sim.run(1)
    assert switch(sim) == "on"
    take_a_quest(sim)
    assert asked(sim) == ["auto: [Quest log changed] took [Crown of the Earth]. Update the route on the map."]

    sim.click("Auto-update: on")
    sim.run(1)
    assert switch(sim) == "off"
    take_a_quest(sim, 940, "The Balance of Nature")
    assert asked(sim) == []


def test_the_update_of_a_route_keeps_its_switch(sim):
    """The AI's answer to an update plans the route again: it is the same route, not a new one."""
    sim.lua(ROUTE)  # Settings: off
    sim.run(1)
    sim.click("Auto-update: off")
    take_a_quest(sim)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "Updated.", "routePlanned": True,
                 "marks": [{"title": "1. Corithras Moonrage", "x": 56.1, "y": 61.7}, {"title": "2. Byancie", "x": 55.3, "y": 56.8}]})
    sim.run(2)
    assert "Route (2)" in shown(sim) and switch(sim) == "on"

    sim.lua(ROUTE.replace("msgId = 1", "msgId = 9"))  # the route replaced by another: the player's switch stays
    sim.run(1)
    assert switch(sim) == "on"

def test_a_change_in_the_quest_log_asks_the_ai_to_update_the_route(sim):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(2)
    assert asked(sim) == []

    sim.lua('TEST_log[1].objectives[1].text = "4/6 Bloodfeather Belt"')  # progress is not a change of status
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    assert asked(sim) == []

    sim.lua('TEST_log[1].objectives[1].finished = true TEST_log[1].complete = true')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(1)
    assert asked(sim) == []  # the log changes in bursts (a turn-in and the next quest): it waits a moment
    table = 'table.insert(TEST_log, { id = 939, title = "Crown of the Earth", objectives = {} })'
    sim.lua(table)
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(10)  # a turn-in or a quest taken is waited on a little longer
    assert asked(sim) == ["auto: [Quest log changed] took [Crown of the Earth]; [The Enchanted Glade] is ready to turn "
                          "in. Update the route on the map."]
    assert "[Quest log changed]" not in shown(sim) and "Updated." in shown(sim)  # a news line, not a conversation

    sim.lua("TEST_log[2].objectives[1].finished = true")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(5)
    assert asked(sim) == ["auto: [Quest log changed] [Not Dead Yet]: Find Byancie done. Update the route on the map."]

    sim.lua("table.remove(TEST_log, 1)")
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(10)  # a turn-in or a quest taken is waited on a little longer
    assert asked(sim) == ["auto: [Quest log changed] [The Enchanted Glade] left the log. Update the route on the map."]

    # a quest chain: the next part has the same name
    sim.lua('table.remove(TEST_log, 1) table.insert(TEST_log, { id = 941, title = "Not Dead Yet", objectives = {} })')
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(10)  # a turn-in or a quest taken is waited on a little longer
    assert asked(sim) == ["auto: [Quest log changed] [Not Dead Yet] turned in, its next part taken. Update the route "
                          "on the map."]
    assert sim.errors == []


def test_only_a_planned_route_is_kept_up_to_date(sim):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "There.", marks = { { title = "Dazalar", x = 56.7, y = 59.5 } } })')
    sim.run(1)
    sim.lua('table.insert(TEST_log, { id = 939, title = "Crown of the Earth", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(6)
    assert asked(sim) == []  # one place to go is not a route

    sim.lua(ROUTE)
    sim.lua("ForeverAI.Map.Clear()")  # the player ended the route
    sim.lua('table.insert(TEST_log, { id = 940, title = "The Balance of Nature", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(6)
    assert asked(sim) == []


def test_it_waits_for_the_answer_being_written(sim):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(1)
    sim.lua('ForeverAI.Ask("Where is Byancie?")')
    sim.run(2)
    (question,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.lua('table.insert(TEST_log, { id = 939, title = "Crown of the Earth", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(8)
    assert asked(sim) == []  # the player's question has no answer yet
    sim.deliver({"kind": "answer", "final": True, "msgId": question["msgId"], "text": "There."})
    sim.run(5)
    assert asked(sim) == ["auto: [Quest log changed] took [Crown of the Earth]. Update the route on the map."]


def test_the_quest_log_filled_after_a_reload_is_not_a_change(sim):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(1)
    sim.reload()
    sim.lua("TEST_full = TEST_log or {}")
    sim.lua(QUESTS)
    sim.lua("local full = TEST_log TEST_log = {} TEST_full = full")  # the game fills the log a moment after loading
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    sim.lua("TEST_log = TEST_full")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(8)
    assert asked(sim) == []


PROPOSAL = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = %d, text = "Your route is out of date.", actions = { %s } })"""
NEW_ROUTE = """{ op = "route", id = 7, addon = "Map",
  stops = { { title = "1. Byancie", x = 55.3, y = 56.8 }, { title = "2. Strigid Screecher", x = 64.2, y = 69.2, mob = "Strigid Screecher" } },
  track = { { name = "Strigid Screecher", quest = "Taming the Beast" } } }"""


def test_a_route_the_ai_proposes_waits_for_accept(sim):
    sim.lua(ROUTE)
    sim.run(1)
    sim.click("Auto-update: off")  # the player wants this route kept up to date
    sim.lua(PROPOSAL % (2, NEW_ROUTE))
    sim.run(1)
    assert "New route" in shown(sim) and "1. Byancie" in shown(sim) and "2. Strigid Screecher" in shown(sim)
    assert "Watching for Strigid Screecher" in shown(sim)
    assert text(sim.lua(SECTION)).startswith("1. Bloodfeather Rogue")  # nothing changed yet
    sim.click("Accept")
    sim.run(1)
    assert text(sim.lua(SECTION)).startswith("1. Byancie — ") and "2. Strigid Screecher — " in text(sim.lua(SECTION))
    assert "Tracking Strigid Screecher" in shown(sim) and "Tracking Bloodfeather Rogue" not in shown(sim)
    assert sim.lua("return ForeverAI.char.routePlanned") is True  # the AI's own route: kept up to date like one
    assert switch(sim) == "on"  # a correction of the route, not a new one: the player's switch stays
    assert "Applied." in shown(sim)
    assert asked(sim) == []  # the AI sees the new route with the next question; nothing is sent now


def test_a_rejected_proposal_is_told_to_the_ai(sim):
    sim.lua(ROUTE)
    sim.run(1)
    sim.lua(PROPOSAL % (2, NEW_ROUTE))
    sim.run(1)
    sim.click("Reject")
    sim.run(1)
    section = text(sim.lua(SECTION))
    assert section.startswith("1. Bloodfeather Rogue — ") and " | 2. Byancie — " in section
    assert section.endswith(" | The player rejected the change of this route you proposed last: leave the route as it "
                            "is unless they ask.")
    sim.lua(ROUTE.replace("msgId = 1", "msgId = 3"))  # a route the player asked for: the old \"no\" no longer counts
    assert "rejected" not in text(sim.lua(SECTION))


def test_a_proposed_change_of_the_route(sim):
    sim.lua(ROUTE)
    sim.run(1)
    edit = """{ op = "route_edit", id = 8, addon = "Map", summary = "Taken off the route: Bloodfeather Rogue. The route now: 1. Byancie.",
      edit = { remove = { { title = "Bloodfeather Rogue", x = 37.3, y = 37.8 } } }, untrack = { "Bloodfeather Rogue" } }"""
    sim.lua(PROPOSAL % (2, edit))
    sim.run(1)
    assert "Change the route" in shown(sim) and "Taken off the route: Bloodfeather Rogue." in shown(sim)
    assert "Tracking Bloodfeather Rogue" in shown(sim)
    sim.click("Accept")
    sim.run(1)
    assert text(sim.lua(SECTION)).startswith("1. Byancie — ") and "Tracking Bloodfeather Rogue" not in shown(sim)
    assert sim.errors == []


NOTICES = r"""TEST_notices = {}
local add = UIErrorsFrame.AddMessage
UIErrorsFrame.AddMessage = function(self, text, ...) TEST_notices[#TEST_notices + 1] = text return add(self, text, ...) end"""


def notices(sim) -> list[str]:
    """What the add-on put on the screen (the game's notices in the upper middle)."""
    out = text(sim.lua("return table.concat(TEST_notices, '|')"))
    return out.split("|") if out else []


def test_an_update_is_a_news_line_and_an_alert_on_the_screen(sim):
    """"nie powinno być kolejnego message": the update is one short line in the chat, and the screen says when it
    starts and when it is done."""
    sim.lua(NOTICES)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(1)
    cards = shown(sim).count("Marked on the map")
    take_a_quest(sim)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert "Updating the route" in shown(sim) and "[Quest log changed]" not in shown(sim)
    assert notices(sim) == []  # an update that changes nothing is answered at once: the screen waits a moment
    sim.run(4)
    assert notices(sim) == ["ForeverAI: updating the route…"]
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "model": "Sonnet 5.5", "secs": 7.1,
                 "text": "Corithras is off the route, 2 stops left.\n\nMore advice that is not news.",
                 "routePlanned": True, "marks": [{"title": "1. Lasher Sproutling", "x": 56.6, "y": 61.3},
                                                 {"title": "2. Byancie", "x": 55.3, "y": 56.8}]})
    sim.run(2)
    s = shown(sim)
    assert "Corithras is off the route, 2 stops left." in s and "More advice" not in s and "Sonnet 5.5" not in s
    assert s.count("Marked on the map") == cards and "Route (2)" in s  # the map changed, no card for it
    assert notices(sim) == ["ForeverAI: updating the route…", "ForeverAI: route updated"]
    assert text(sim.lua("local e = ForeverAI.CurrentConv().entries return e[#e].k .. '|' .. e[#e].t")) == \
        "n|Corithras is off the route, 2 stops left."  # it stays in the conversation as a news line

    sim.click("Settings")
    sim.run(1)
    sim.click("Show on the screen when a route is updated")
    assert sim.lua("return ForeverAI.db.routeAlerts") is False
    sim.click("Chat")
    take_a_quest(sim, 940, "The Balance of Nature")
    assert len(asked(sim)) == 1 and len(notices(sim)) == 2  # updated, without the alerts
    assert sim.errors == []


def test_a_route_marked_by_hand_is_a_route(sim):
    """The real game: "oznacz mi je jeszcze raz" was answered with three mark_on_map calls instead of plan_route, and
    the route lost its switch and was no longer kept up to date."""
    sim.lua(ROUTE)
    sim.run(1)
    sim.click("Auto-update: off")
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "Marked again.", marks = { '
            '{ title = "1. Lasher Sproutling (Dewy Lasher Frond)", x = 59.5, y = 62.2 }, '
            '{ title = "2. Byancie (The Great Tree Provides)", x = 55.3, y = 56.8 }, '
            '{ title = "3. Rut\'theran Village", x = 55.4, y = 92.2 } } })')
    sim.run(1)
    assert "Route (3)" in shown(sim) and switch(sim) == "on"
    take_a_quest(sim)
    assert len(asked(sim)) == 1  # kept up to date

    sim.lua("ForeverAI.Map.Clear()")
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "There.", marks = { { title = "Dazalar", x = 56.7, y = 59.5 } } })')
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 6, text = "And there.", marksReplace = false, '
            'marks = { { title = "Byancie", x = 55.3, y = 56.8 } } })')
    sim.run(1)
    assert "Route (2)" in shown(sim) and switch(sim) == "off"  # two places to go are a route: as Settings say


def test_an_update_remembers_what_the_player_took_off(sim):
    """The update marks the route again; the stops the player took off themselves stay known to the AI."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(1)
    sim.lua('ForeverAI.Map.Remove(2, "player")')
    take_a_quest(sim)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert update["snapshot"]["marks"][-1] == "Taken off the route by the player: Byancie"
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "Updated.", "routePlanned": True,
                 "marks": [{"title": "1. Bloodfeather Rogue", "x": 37.3, "y": 37.8, "mob": "Bloodfeather Rogue"},
                           {"title": "2. Corithras Moonrage", "x": 56.1, "y": 61.7}]})
    sim.run(2)
    assert text(sim.lua(SECTION)).endswith(" | Taken off the route by the player: Byancie")
    sim.lua(ROUTE.replace("msgId = 1", "msgId = 9"))  # a route the player asks for starts clean
    assert "Taken off" not in text(sim.lua(SECTION))


def test_the_stops_that_are_done_leave_when_the_route_is_updated(sim):
    """A stop that is done stays on the list until the AI has looked at the route again."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.30
    sim.lua(ROUTE)
    sim.run(1)
    sim.lua("ForeverAI.Track.Stop()")
    sim._wow.player.x, sim._wow.player.y = 0.553, 0.568  # at Byancie, the second stop
    sim.run(4)
    assert "Route (1/2)" in shown(sim) and "2. Byancie — Darkshore 55.3, 56.8 (done)" in text(sim.lua(SECTION))
    take_a_quest(sim)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert "2. Byancie — Darkshore 55.3, 56.8 (done)" in update["snapshot"]["marks"]  # the AI is told
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "The route stays as it is."})
    sim.run(2)
    assert "Route (1)" in shown(sim) and "done" not in text(sim.lua(SECTION))
    assert text(sim.lua(SECTION)).startswith("1. Bloodfeather Rogue — ")


def test_an_update_that_gets_no_answer_gives_up_soon_and_is_asked_again(sim):
    """The real game: the app was restarted just as the update was sent, and "Updating the route…" hung with the
    Send button busy (a question waits five minutes for the app). An update is the add-on's own errand: it gives up
    after a short while, frees the window, and asks once more, because the change in the quest log still stands."""
    sim.lua(NOTICES)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(1)
    take_a_quest(sim)
    (first,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.run(50)  # nobody answers
    assert "Route not updated" in shown(sim) and sim.lua("return ForeverAI.pending == nil") is True
    assert notices(sim)[-1] == "ForeverAI: the route was not updated"
    sim.run(40)
    (again,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert again["text"] == first["text"] and again["msgId"] != first["msgId"]
    sim.run(50)  # nobody answers this time either: no third try by itself
    sim.run(60)
    assert [m for m in sim.sent_messages() if m.get("type") == "question"] == []
    take_a_quest(sim, 940, "The Balance of Nature")  # the next change in the quest log asks again, with all of it
    (later,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert "took [Crown of the Earth], [The Balance of Nature]" in later["text"]
    sim.deliver({"kind": "answer", "final": True, "msgId": later["msgId"], "text": "Updated."})
    sim.run(2)
    assert "Updated." in shown(sim)
    assert sim.errors == []


# The real game: the route was "1. Nessa Shadowsong, 2. Vesprystus", the player stood at Vesprystus (done) and took a
# quest there. The update took Nessa off (her quest was turned in), the add-on dropped the done stop, and the whole
# route was gone from the window the moment a quest was accepted.
TWO = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true,
  marks = { { title = "1. Nessa Shadowsong", x = 56.3, y = 92.4 }, { title = "2. Vesprystus (Hippogryph Master)", x = 58.4, y = 94.0 } } })"""


def _update(sim, **answer):
    """A quest is taken, the add-on asks, and the AI answers with ``answer``."""
    take_a_quest(sim, 950 + len(sim.lua("return ForeverAI.CurrentConv().entries")), "Flight to Auberdine")
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "News.", **answer})
    sim.run(2)
    return update


def _at_vesprystus(sim):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.50, 0.50
    sim.lua(TWO)
    sim._wow.player.x, sim._wow.player.y = 0.584, 0.940
    sim.run(4)
    assert "Route (1/2)" in shown(sim)


def test_an_update_never_leaves_the_window_without_the_route(sim):
    _at_vesprystus(sim)
    _update(sim, route={"remove": [{"title": "Nessa Shadowsong", "x": 56.3, "y": 92.4}]})
    # nothing is left to do, and the player sees that: the route they finished, not an empty window
    assert "Route (1/1)" in shown(sim)
    assert text(sim.lua(SECTION)) == "1. Vesprystus (Hippogryph Master) — Darkshore 58.4, 94.0 (done)"


def test_an_update_that_changes_nothing_keeps_a_finished_route(sim):
    _at_vesprystus(sim)
    sim._wow.player.x, sim._wow.player.y = 0.563, 0.924  # and Nessa too: every stop is done
    sim.run(4)
    assert "Route (2/2)" in shown(sim)
    _update(sim)
    assert "Route (2/2)" in shown(sim)
    _update(sim, routePlanned=True, marks=[{"title": "1. Laird", "x": 36.8, "y": 44.3}])  # a new route takes its place
    assert "Route (1)" in shown(sim) and text(sim.lua(SECTION)).startswith("1. Laird — ")


def test_a_stop_that_is_done_can_be_needed_again(sim):
    """Another quest at the same NPC: marking the place again makes the stop one to do, it is not refused as a
    place that is on the route already."""
    _at_vesprystus(sim)
    _update(sim, marksReplace=False,
            marks=[{"title": "Vesprystus (Flight to Auberdine)", "x": 58.4, "y": 94.0,
                    "tasks": [{"kind": "objective", "quest": "Flight to Auberdine"}]}])
    section = text(sim.lua(SECTION))
    assert "(done)" not in section and "Vesprystus (Flight to Auberdine) — Darkshore 58.4, 94.0" in section
    assert "Route (2)" in shown(sim)  # Nessa and Vesprystus: both to do, nothing twice
    sim.run(6)
    assert "(done)" not in text(sim.lua(SECTION))  # it waits for its quest now, not for the player to stand there


# a route that says what its stops are for (plan_route's): the add-on can tell what a change in the quest log means
TASK_ROUTE = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true, marks = {
  { title = "1. Bloodfeather Rogue", x = 37.3, y = 37.8,
    tasks = { { kind = "objective", quest = "The Enchanted Glade", label = "Bloodfeather Belt" } } },
  { title = "2. Sentinel Arynia", x = 38.3, y = 34.4, tasks = { { kind = "turn_in", quest = "The Enchanted Glade" } } },
  { title = "3. Byancie", x = 55.3, y = 56.8, tasks = { { kind = "objective", quest = "Not Dead Yet", label = "Find Byancie" } } } } })"""


def test_a_change_the_route_already_holds_does_not_ask_the_ai(sim):
    """"Żeby tak nie spamowało odświeżaniem": an objective done, a quest ready or turned in is what the route was made
    for. Its stops are ticked off by the add-on; the AI is asked when something is not on the route."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(TASK_ROUTE)
    sim.run(1)
    sim.lua("TEST_log[1].objectives[1].finished = true TEST_log[1].complete = true")  # the belts: ready to turn in
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    assert asked(sim) == []  # the turn-in is the next stop
    sim.lua("table.remove(TEST_log, 1)")  # turned in
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(12)
    # a turn-in opens what comes next (the next part, new quests offered there): the route is looked at again
    assert asked(sim) == ["auto: [Quest log changed] [The Enchanted Glade] left the log. Update the route on the map."]
    sim.lua("TEST_log[1].objectives[1].finished = true")  # Byancie found: the route holds nothing more for that quest
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    assert asked(sim) == ["auto: [Quest log changed] [Not Dead Yet]: Find Byancie done. Update the route on the map."]
    assert sim.errors == []


def test_quests_taken_one_after_another_are_one_update(sim):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(TASK_ROUTE)
    sim.run(1)
    for qid, title in ((939, "Crown of the Earth"), (940, "The Balance of Nature"), (941, "Twisted Hatred")):
        sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = {} })' % (qid, title))
        sim._wow.fire(b"QUEST_ACCEPTED")
        sim._wow.fire(b"QUEST_LOG_UPDATE")  # the game says both; the second must not cut the wait short
        sim.run(5)
        assert [m for m in sim.sent_messages(remove=False) if m.get("type") == "question"] == []
    sim.run(5)
    assert asked(sim) == ["auto: [Quest log changed] took [Crown of the Earth], [The Balance of Nature], "
                          "[Twisted Hatred]. Update the route on the map."]


def test_an_answer_made_before_the_last_quest_was_taken_is_not_put_on_the_map(sim):
    """"Ostatnia najnowsza aktualizacja jest przykryta przez starszą": the route on the map is always the one made
    for the quest log as it is. An update overtaken by a quest is asked again, in the same line of the chat."""
    sim.lua(NOTICES)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(TASK_ROUTE)
    sim.run(1)
    take_a_quest(sim)
    (first,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    take_a_quest(sim, 940, "The Balance of Nature")  # while the first update is being written
    assert [m for m in sim.sent_messages(remove=False) if m.get("type") == "question"] == []
    sim.deliver({"kind": "answer", "final": True, "msgId": first["msgId"], "text": "Crown of the Earth is on the route.",
                 "routePlanned": True, "marks": [{"title": "1. Old News", "x": 50, "y": 50}, {"title": "2. More", "x": 51, "y": 51}]})
    sim.run(3)
    assert "Old News" not in text(sim.lua(SECTION)) and "Crown of the Earth is on the route." not in shown(sim)
    (second,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert second["text"] == ("[Quest log changed] took [Crown of the Earth], [The Balance of Nature]. "
                              "Update the route on the map.")
    assert shown(sim).count("Updating the route") == 1  # one line, still waiting
    sim.deliver({"kind": "answer", "final": True, "msgId": second["msgId"], "text": "Both quests are on the route.",
                 "routePlanned": True, "marks": [{"title": "1. Corithras", "x": 56.1, "y": 61.7}, {"title": "2. Byancie", "x": 55.3, "y": 56.8}]})
    sim.run(3)
    assert text(sim.lua(SECTION)).startswith("1. Corithras") and "Both quests are on the route." in shown(sim)
    assert notices(sim) == ["ForeverAI: updating the route…", "ForeverAI: route updated"]  # said once
    assert asked(sim) == [] and sim.errors == []



def test_an_update_that_changes_nothing_is_quiet(sim):
    """The app answers at once when the route planned again is the route on the map: no alert on the screen, one
    dim line in the chat that the next such update writes into."""
    sim.lua(NOTICES)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(TASK_ROUTE)
    sim.run(1)
    for qid, title in ((939, "Crown of the Earth"), (940, "The Balance of Nature")):
        take_a_quest(sim, qid, title)
        (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
        sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "routeUnchanged": True,
                     "text": "Route checked: nothing to change."})
        sim.run(6)
    assert notices(sim) == [] and shown(sim).count("Route checked: nothing to change.") == 1
    assert "Route (3)" in shown(sim) and sim.errors == []


# the route as the game held it (SavedVariables): made when the owl spots of [Eyes of the Sentinels] were still called
# "Credit", the database's name, which no objective of the quest log has
CREDIT_ROUTE = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true, marks = {
  { title = "1. Credit", x = 41, y = 43.5, mob = "Credit",
    tasks = { { kind = "objective", quest = "Eyes of the Sentinels", id = 98067, label = "Credit" } } },
  { title = "2. Credit", x = 33.4, y = 16.8, mob = "Credit",
    tasks = { { kind = "objective", quest = "Eyes of the Sentinels", id = 98067, label = "Credit" } } },
  { title = "3. Sentinel Dalia Sunblade", x = 39.6, y = 89.5,
    tasks = { { kind = "turn_in", quest = "Eyes of the Sentinels", id = 98067 } } } } })"""
OWLS = """table.insert(TEST_log, { id = 98067, title = "Eyes of the Sentinels", objectives = {
  { text = "0/1 Cenarion Hold depths entrance", finished = false }, { text = "0/1 Darnassus Bank", finished = false } } })"""
DONE_STOPS = "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do if s.done then out[#out + 1] = i end end return table.concat(out, ',')"


def test_an_objective_done_at_a_stop_that_cannot_tell_asks_for_an_update(sim):
    """"Uwolniłem 1 ptaka, miałem aktualizację 1/1, a addon to zignorował": the stops could not tell which objective
    they were for, so none was ticked off; and the add-on took them as proof that the route holds the quest, so it
    did not ask either. A stop that cannot tell proves nothing: the AI is asked, and the new route can tell."""
    sim.lua(OWLS)
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(CREDIT_ROUTE)
    sim.run(1)
    sim.lua('TEST_log[3].objectives[2] = { text = "1/1 Darnassus Bank", finished = true }')  # the owl at the bank
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert update["text"] == "[Quest log changed] [Eyes of the Sentinels]: 1/1 Darnassus Bank done. Update the route on the map."
    # the route planned again names the stops as the quest log does: each is done with its own objective, unasked
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "One owl left.", "routePlanned": True,
                 "marks": [{"title": "1. Cenarion Hold depths entrance", "x": 33.4, "y": 16.8, "tasks": [
                               {"kind": "objective", "quest": "Eyes of the Sentinels", "id": 98067,
                                "label": "Cenarion Hold depths entrance"}]},
                           {"title": "2. Sentinel Dalia Sunblade", "x": 39.6, "y": 89.5, "tasks": [
                               {"kind": "turn_in", "quest": "Eyes of the Sentinels", "id": 98067}]}]})
    sim.run(3)
    sim.lua('TEST_log[3].objectives[1] = { text = "1/1 Cenarion Hold depths entrance", finished = true } TEST_log[3].complete = true')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    assert text(sim.lua(DONE_STOPS)) == "1" and asked(sim) == [] and sim.errors == []


def test_a_route_with_stops_that_cannot_tell_is_updated_after_loading(sim):
    """The route in the game was made before the stops could tell their objective; the owl was placed already. After a
    /reload the add-on must not wait for the next change in the quest log to see that this route cannot be followed."""
    sim.lua(OWLS)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(CREDIT_ROUTE)
    sim.run(1)
    sim.reload()
    sim.lua(QUESTS)
    sim.lua(OWLS)
    sim.lua('TEST_log[3].objectives[2] = { text = "1/1 Darnassus Bank", finished = true }')
    sim.slash("/fai")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(8)
    assert asked(sim) == ["auto: [Quest log changed] the stops for [Eyes of the Sentinels] do not match its objectives. "
                          "Update the route on the map."]
    sim.run(40)
    assert asked(sim) == []  # once: the answer was "Updated." without a new route, and it is not asked over and over


def test_a_stop_done_by_what_the_player_did_is_told(sim):
    """"Puściłem ptaka i znowu nie ma w ogóle aktualizacji": the stop was ticked off, but nothing said so. The game's
    exact case: the route the app sent, the objective texts as the game words them, the owl placed at the inn."""
    sim.lua(NOTICES)
    sim.lua("""table.insert(TEST_log, { id = 98067, title = "Eyes of the Sentinels", objectives = {
      { text = "1/1 Cenarion Hold depths entrance", finished = true }, { text = "1/1 Darnassus Bank", finished = true },
      { text = "0/1 Craftsman's Terrace Inn", finished = false }, { text = "0/1 City Gate", finished = false } } })""")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true, marks = {
      { title = "1. Craftsman's Terrace Inn", x = 66.5, y = 15.3, tasks = {
          { kind = "objective", quest = "Eyes of the Sentinels", id = 98067, label = "Craftsman's Terrace Inn" } } },
      { title = "2. City Gate", x = 89.1, y = 34.8, tasks = {
          { kind = "objective", quest = "Eyes of the Sentinels", id = 98067, label = "City Gate" } } },
      { title = "3. Sentinel Dalia Sunblade", x = 39.6, y = 89.5, tasks = {
          { kind = "turn_in", quest = "Eyes of the Sentinels", id = 98067 } } } } })""")
    sim.run(3)
    assert notices(sim) == []
    sim.lua("""TEST_log[3].objectives[3] = { text = "1/1 Craftsman's Terrace Inn", finished = true }""")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(8)
    assert text(sim.lua(DONE_STOPS)) == "1" and "Route (1/3)" in shown(sim)
    (said,) = notices(sim)
    assert re.fullmatch(r"ForeverAI: done: Craftsman's Terrace Inn\. Next: 2\. City Gate \(\d+ m\)", said)
    assert re.search(r"Done: Craftsman's Terrace Inn\. Next: 2\. City Gate \(\d+ m\)", shown(sim))  # and in the chat
    assert asked(sim) == []  # the route holds the rest of the quest: no AI is needed for this
    # the last objective and then the turn-in
    sim.lua("""TEST_log[3].objectives[4] = { text = "1/1 City Gate", finished = true } TEST_log[3].complete = true""")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(8)
    assert notices(sim)[-1].startswith("ForeverAI: done: City Gate. Next: 3. Sentinel Dalia Sunblade")
    sim.lua("table.remove(TEST_log, 3)")
    sim._wow.fire(b"QUEST_TURNED_IN")
    sim.run(4)
    assert notices(sim)[-1] == "ForeverAI: done: Sentinel Dalia Sunblade. The route is finished."
    sim.click("Settings")
    sim.run(1)
    sim.click("Show on the screen when a route is updated")
    assert sim.errors == []
