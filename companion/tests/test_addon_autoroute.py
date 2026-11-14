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


SETTING = "Update new routes when my quests change"


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
    assert notices(sim) == ["ForeverAI: updating the route…"]  # no answer for a while: the screen says it is coming
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
    sim.click("Show route updates on screen")
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
    # (the AI is still told that Byancie was done: off the list, not fallen off the route)
    assert text(sim.lua("return table.concat(ForeverAI.Map.DoneLately() or {}, ' | ')")).startswith("Byancie — ")


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
    """Three quests taken at one NPC, one click after another: one update."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(TASK_ROUTE)
    sim.run(1)
    for qid, title in ((939, "Crown of the Earth"), (940, "The Balance of Nature"), (941, "Twisted Hatred")):
        sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = {} })' % (qid, title))
        sim._wow.fire(b"QUEST_ACCEPTED")
        sim._wow.fire(b"QUEST_LOG_UPDATE")  # the game says both; the second must not cut the wait short
        sim.run(0.6)
    sim.run(3)
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
        sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = {} })' % (qid, title))
        sim._wow.fire(b"QUEST_ACCEPTED")
        sim.run(2)
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
    sim.click("Show route updates on screen")
    assert sim.errors == []


def test_a_quest_taken_changes_the_route_at_once_and_the_ai_checks_it_after(sim):
    """"To powinno iść insta, być szybkie, smooth": the update came eight seconds after the quest was taken and then
    waited for a model. The app's planner answers at once; the AI looks at the result a moment later, and only a
    change of its own is news."""
    sim.lua(NOTICES)
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(TASK_ROUTE)
    sim.run(1)
    sim.lua('table.insert(TEST_log, { id = 939, title = "Crown of the Earth", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(2.5)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert update["fast"] is True and update["text"] == "[Quest log changed] took [Crown of the Earth]. Update the route on the map."
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "Route updated: + Corithras Moonrage.",
                 "routePlanned": True, "check": True, "marks": [{"title": "1. Corithras Moonrage", "x": 56.1, "y": 61.7},
                                                                {"title": "2. Byancie", "x": 55.3, "y": 56.8}]})
    sim.run(3)
    assert text(sim.lua(SECTION)).startswith("1. Corithras Moonrage") and "Route updated: + Corithras Moonrage." in shown(sim)
    assert notices(sim) == ["ForeverAI: route updated"]  # at once: no "updating…" first
    # a moment later the AI checks it: nothing on the screen, and the line stays as it is when it agrees
    sim.run(4)
    (check,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert check.get("check") is True and not check.get("fast")
    assert check["text"] == "[Route check] took [Crown of the Earth]. Check the route on the map."
    sim.deliver({"kind": "answer", "final": True, "msgId": check["msgId"], "text": "Route checked: nothing to change.",
                 "routeUnchanged": True})
    sim.run(3)
    assert shown(sim).count("Route updated: + Corithras Moonrage.") == 1 and "Route checked" not in shown(sim)
    assert notices(sim) == ["ForeverAI: route updated"] and text(sim.lua(SECTION)).startswith("1. Corithras Moonrage")
    # a check that changes the route: its words take the line, and the screen says so
    sim.lua('table.insert(TEST_log, { id = 941, title = "Twisted Hatred", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(2.5)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "Route updated: + Xethorr.",
                 "routePlanned": True, "check": True, "marks": [{"title": "1. Xethorr", "x": 51.4, "y": 50.0},
                                                                {"title": "2. Corithras Moonrage", "x": 56.1, "y": 61.7}]})
    sim.run(6.5)
    (check,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.deliver({"kind": "answer", "final": True, "msgId": check["msgId"], "routePlanned": True,
                 "text": "Twisted Hatred is an elite quest: left off the route.",
                 "marks": [{"title": "1. Corithras Moonrage", "x": 56.1, "y": 61.7}]})
    sim.run(3)
    assert "Twisted Hatred is an elite quest: left off the route." in shown(sim) and "Route updated: + Xethorr." not in shown(sim)
    assert text(sim.lua(SECTION)).startswith("1. Corithras Moonrage") and notices(sim)[-1] == "ForeverAI: route updated"
    assert sim.errors == []


def test_a_stop_added_for_an_objective_goes_before_its_turn_in(sim):
    """In the game the bears for Buzzbox 525 went in after the Buzzbox (that is where they cost the least way, on the way
    back north): the route had the player turn the quest in before having the scalps."""
    sim._wow.player.x, sim._wow.player.y = 0.522, 0.369  # by Den Mother, Darkshore
    sim.lua('table.insert(TEST_log, { id = 1003, title = "Buzzbox 525", objectives = { { text = "0/4 Grizzled Scalp", '
            'finished = false } } })')
    sim.run(3)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true, marks = { '
            '{ title = "1. Buzzbox 525", x = 41.4, y = 80.6, tasks = { { kind = "turn_in", quest = "Buzzbox 525" } } }, '
            '{ title = "2. Hippogryph Master", x = 36.3, y = 45.6 } } })')
    sim.run(1)
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 2, text = "Bears.", marksReplace = false, marks = { '
            '{ title = "Grizzled Thistle Bears", x = 39.1, y = 70.0, tasks = { { kind = "objective", quest = "Buzzbox 525" } } } } })')
    sim.run(1)
    stops = [line.split(" — ")[0] for line in text(sim.lua(SECTION)).split(" | ") if line[:1].isdigit()]
    assert stops == ["1. Grizzled Thistle Bears", "2. Buzzbox 525", "3. Hippogryph Master"]
    # a stop that is not for a quest goes where the way alone puts it: on the way back north, after the Buzzbox
    sim.lua('ForeverAI.UI.UpdateAnswer({ final = true, msgId = 3, text = "Camp.", marksReplace = false, marks = { '
            '{ title = "Camp", x = 38.5, y = 66.0 } } })')
    sim.run(1)
    stops = [line.split(" — ")[0] for line in text(sim.lua(SECTION)).split(" | ") if line[:1].isdigit()]
    assert stops == ["1. Grizzled Thistle Bears", "2. Buzzbox 525", "3. Camp", "4. Hippogryph Master"]
    assert sim.errors == []


# ---------------------------------------------------------------- in step with the stops of the route
# The player's words: "Route się aktualizuje nie po wykonaniu checkpointa, ale po wykonaniu np. jednej przyjętej misji.
# To powinno być synchro z tym wykonywaniem checkpointów, a nie swoim życiem. I jeżeli auto route nie musi, to niech nie
# usuwa starych punktów, tylko wplata nowe w obecny plan; względem obecnego checkpointa powinien to liczyć i pozycji
# gracza." From the game's log: "update asked: took [Collecting Memories]" two seconds before "stop done: 1. Wilder
# Thistlenettle", and a second update a moment later for the second quest of the same stop.
PICKUPS = """ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true, marks = {
  { title = "1. Wilder Thistlenettle", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Oh Brother. . .", id = 167 },
                                                                { kind = "pick_up", quest = "Collecting Memories", id = 168 } } },
  { title = "2. The cave mouth", x = 60, y = 60 }, { title = "3. The old mill", x = 70, y = 70 } } })"""
STOPS = ("local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[#out + 1] = s.title .. (s.done and ' (done)' or '') "
         ".. (s.current and ' <' or '') end return table.concat(out, ', ')")
WILDER = {"title": "Wilder Thistlenettle", "x": 40, "y": 40, "tasks": [
    {"kind": "pick_up", "quest": "Oh Brother. . .", "id": 167}, {"kind": "pick_up", "quest": "Collecting Memories", "id": 168}]}


def _accept(sim, qid, title):
    sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = {} })' % (qid, title))
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim._wow.fire(b"QUEST_LOG_UPDATE")


def _pickups(sim, x=0.40, y=0.40):
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = x, y
    sim.lua(PICKUPS)
    sim.run(1)


def test_an_update_waits_until_the_stop_the_player_is_at_is_done(sim):
    _pickups(sim)
    _accept(sim, 168, "Collecting Memories")  # one of the two quests of this stop
    sim.run(10)
    assert asked(sim) == [] and "Wilder Thistlenettle <" in text(sim.lua(STOPS))
    _accept(sim, 167, "Oh Brother. . .")
    sim.run(5)
    assert text(sim.lua(STOPS)).startswith("Wilder Thistlenettle (done), The cave mouth <")
    # one update, for the stop as a whole
    assert asked(sim) == ["auto: [Quest log changed] took [Collecting Memories], [Oh Brother. . .]. "
                          "Update the route on the map."]
    sim.run(6)
    assert asked(sim) == [] and sim.errors == []


def test_an_update_does_not_wait_for_a_stop_the_player_walked_away_from(sim):
    _pickups(sim)
    _accept(sim, 168, "Collecting Memories")
    sim.run(6)
    assert asked(sim) == []
    sim._wow.player.x, sim._wow.player.y = 0.50, 0.45  # left him with the other quest not taken
    sim.run(5)
    assert asked(sim) == ["auto: [Quest log changed] took [Collecting Memories]. Update the route on the map."]
    assert "Wilder Thistlenettle <" in text(sim.lua(STOPS))  # the stop still waits for the other quest


def test_a_quest_taken_away_from_every_stop_is_asked_at_once(sim):
    _pickups(sim, 0.20, 0.20)
    _accept(sim, 950, "Flight to Auberdine")
    sim.run(4)
    assert asked(sim) == ["auto: [Quest log changed] took [Flight to Auberdine]. Update the route on the map."]


def test_an_update_weaves_its_new_stops_into_the_route_on_the_map(sim):
    _pickups(sim, 0.20, 0.20)
    sim.lua("ForeverAI.Map.Lock(2, true) ForeverAI.Map.SetCurrent(3)")  # the player's own: a lock, and where they walk to
    sim.run(1)
    assert text(sim.lua(STOPS)) == "Wilder Thistlenettle, The cave mouth, The old mill <"
    # the plan, made from where the player stands, has the stops in another order, a new one, and no cave mouth
    _update(sim, routePlanned=True, marks=[
        {"title": "1. The old mill", "x": 70, "y": 70}, {"title": "2. The lake", "x": 65, "y": 65},
        dict(WILDER, title="3. Wilder Thistlenettle"), {"title": "4. Flight master", "x": 36.3, "y": 45.6}])
    # the stops that stay are where they were, the arrow too; each new stop comes after the one the plan put it behind
    assert text(sim.lua(STOPS)) == "Wilder Thistlenettle, Flight master, The cave mouth, The old mill <, The lake"
    assert "Locked by the player (these stops stay on the route until they unlock them): The cave mouth" in text(sim.lua(SECTION))
    # a stop the plan no longer has leaves (it is not locked now)
    sim.lua("ForeverAI.Map.Lock(3, false)")
    _update(sim, routePlanned=True, marks=[
        {"title": "1. The old mill", "x": 70, "y": 70}, {"title": "2. The lake", "x": 65, "y": 65},
        dict(WILDER, title="3. Wilder Thistlenettle"), {"title": "4. Flight master", "x": 36.3, "y": 45.6}])
    assert text(sim.lua(STOPS)) == "Wilder Thistlenettle, Flight master, The old mill <, The lake" and sim.errors == []


def test_an_update_keeps_what_the_player_did_at_a_stop(sim):
    """The stop of a quest giver with one quest taken is the same stop after the update: it waits for the other one."""
    _pickups(sim, 0.20, 0.20)
    _accept(sim, 168, "Collecting Memories")  # taken somewhere else (shared by a friend)
    sim.run(4)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "News.", "routePlanned": True, "marks": [
        dict(WILDER, title="1. Wilder Thistlenettle"), {"title": "2. The cave mouth", "x": 60, "y": 60},
        {"title": "3. The old mill", "x": 70, "y": 70}]})
    sim.run(2)
    assert text(sim.lua(STOPS)) == "Wilder Thistlenettle <, The cave mouth, The old mill"
    _accept(sim, 167, "Oh Brother. . .")
    sim.run(5)
    assert text(sim.lua(STOPS)).startswith("Wilder Thistlenettle (done)")


def test_an_order_that_cannot_be_walked_any_more_gives_way_to_the_plans(sim):
    """A new quest is picked up at a new stop, and what it wants is at a stop the route had before: kept in its old
    place, that stop would wait for a quest the player does not have yet."""
    _pickups(sim, 0.20, 0.20)
    _update(sim, routePlanned=True, marks=[
        {"title": "1. The old mill", "x": 70, "y": 70},
        {"title": "2. Elder", "x": 66, "y": 66, "tasks": [{"kind": "pick_up", "quest": "Lost Tools", "id": 9}]},
        {"title": "3. Wilder Thistlenettle", "x": 40, "y": 40, "tasks": WILDER["tasks"] + [
            {"kind": "objective", "quest": "Lost Tools", "id": 9, "label": "Toolbox"}]},
        {"title": "4. The cave mouth", "x": 60, "y": 60}])
    assert text(sim.lua(STOPS)) == "The old mill <, Elder, Wilder Thistlenettle, The cave mouth" and sim.errors == []


def test_a_quest_planned_whole_is_no_news_when_it_is_taken(sim):
    """A route made with all of a quest (where it is taken, what it wants, where it is turned in) has what comes next
    the moment the quest is in the log: the AI is not asked. A quest the route only says to pick up still asks."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 1, text = "The route.", routePlanned = true, marks = {
      { title = "1. Clerk", x = 40, y = 40, tasks = { { kind = "pick_up", quest = "Pests", id = 21 } } },
      { title = "2. Rat", x = 60, y = 60, tasks = { { kind = "objective", quest = "Pests", id = 21, label = "Rat" } } },
      { title = "3. Clerk", x = 40, y = 40, tasks = { { kind = "turn_in", quest = "Pests", id = 21 } } },
      { title = "4. Elder", x = 70, y = 70, tasks = { { kind = "pick_up", quest = "A Letter", id = 22 } } } } })""")
    sim.run(1)
    sim.lua('table.insert(TEST_log, { id = 21, title = "Pests", objectives = { { text = "0/8 Rat", finished = false } } })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(6)
    assert asked(sim) == [] and "Clerk (done)" in text(sim.lua(STOPS))
    _accept(sim, 22, "A Letter")  # the route has nothing of it but the stop it is taken at
    sim.run(6)
    assert asked(sim) == ["auto: [Quest log changed] took [A Letter]. Update the route on the map."] and sim.errors == []


def test_leaving_the_route_for_another_zone_asks_for_an_update_once(sim):
    """Found by walking a route: the player flew away from a zone with stops still to do there; nothing in the quest
    log changed, so no update was asked, and the arrow kept pointing back. Crossing a border for a moment, a flight,
    a dungeon ask nothing; staying asks once for that zone."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(1)
    asked(sim)
    sim._wow.player.mapID, sim._wow.player.zone = 1440, b"Ashenvale"
    sim.run(6)
    sim._wow.player.mapID, sim._wow.player.zone = 1439, b"Darkshore"  # back at once
    sim.run(20)
    assert asked(sim) == []
    sim.lua("UnitOnTaxi = function() return true end")
    sim._wow.player.mapID, sim._wow.player.zone = 1440, b"Ashenvale"
    sim.run(30)
    assert asked(sim) == []  # on a flight
    sim.lua("UnitOnTaxi = function() return false end")
    sim.run(20)
    assert asked(sim) == ["auto: [Quest log changed] the player went to Ashenvale, away from the route. Update the route on the map."]
    sim.run(40)
    assert asked(sim) == [] and sim.errors == []  # once for that zone


def test_an_update_cut_to_the_stops_the_map_holds_keeps_the_others(sim):
    """Found by walking the game's own Westfall: a plan longer than the map holds was cut to its first stops; planned
    from elsewhere, the turn-ins at its end were left out of the cut, and the update took them off the route."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.lua(PICKUPS)
    sim.run(1)
    marks = [{"title": "1. The old mill", "x": 70, "y": 70}, {"title": "2. The lake", "x": 65, "y": 65}]
    sim.lua("ForeverAI.Map.Merge(..., true)", marks)
    sim.run(2)
    titles = text(sim.lua(STOPS))
    assert "Wilder Thistlenettle" in titles and "The cave mouth" in titles and "The lake" in titles, titles
    # without more to come, what the plan does not have leaves
    sim.lua("ForeverAI.Map.Merge(...)", [{"title": "1. The old mill", "x": 70, "y": 70}])
    sim.run(1)
    assert "The cave mouth" not in text(sim.lua(STOPS)) and sim.errors == []



def test_an_update_cut_to_the_map_drops_a_stop_whose_task_moved(sim):
    """Found by walking the game's own Goldshire: an update took [Rough Wolf Pelts] from the Prowlers to the Gray
    Forest Wolves. The plan was longer than the map holds, the old Prowler stop was kept as one of its later stops,
    and it stood after the turn-in of the quest it was for."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.run(1)
    first = [{"title": "1. Helene", "x": 30, "y": 30, "tasks": [{"kind": "pick_up", "quest": "Rough Wolf Pelts", "id": 33}]},
             {"title": "2. Prowler", "x": 75, "y": 76, "mob": "Prowler", "tasks": [{"kind": "objective", "quest": "Rough Wolf Pelts", "id": 33, "label": "Rough Wolf Pelt"}]},
             {"title": "3. Helene", "x": 30, "y": 30, "tasks": [{"kind": "turn_in", "quest": "Rough Wolf Pelts", "id": 33}]},
             {"title": "4. The old mill", "x": 70, "y": 70}]
    sim.lua("ForeverAI.UI.UpdateAnswer(...)", {"final": True, "msgId": 50, "text": "x", "routePlanned": True, "marks": first})
    sim.run(2)
    # the update: the wolves instead, and the map holds only the first part of the plan
    again = [{"title": "1. Helene", "x": 30, "y": 30, "tasks": [{"kind": "pick_up", "quest": "Rough Wolf Pelts", "id": 33}]},
             {"title": "2. Gray Forest Wolf", "x": 74, "y": 69, "mob": "Gray Forest Wolf", "tasks": [
                 {"kind": "objective", "quest": "Taming the Beast", "id": 34, "label": "Gray Forest Wolf"},
                 {"kind": "objective", "quest": "Rough Wolf Pelts", "id": 33, "label": "Rough Wolf Pelt"}]},
             {"title": "3. Helene", "x": 30, "y": 30, "tasks": [{"kind": "turn_in", "quest": "Rough Wolf Pelts", "id": 33}]}]
    sim.lua("ForeverAI.Map.Merge(...)", again, True)  # (what an update's answer does: UI.MarksCard)
    sim.run(2)
    titles = text(sim.lua(STOPS))
    assert "Prowler" not in titles and "Gray Forest Wolf" in titles, titles
    assert "The old mill" in titles and sim.errors == []  # (a place: the plan's later part may have it)



def test_an_update_that_names_another_creature_moves_the_stop_there(sim):
    """Found by walking the game's own Goldshire: an update named Kobold Geomancers for the gold dust instead of Kobold
    Miners; the stop took the new name and kept the miners' ground. And a stop for the same objective a little
    further off is the one kept, not a stop for something else at the new spot."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.run(1)
    for qid, title, what in ((47, "Gold Dust Exchange", "Gold Dust"), (99, "Slimy Menace", "Murloc"),
                             (112, "Collecting Kelp", "Crystal Kelp Frond")):
        sim.lua('table.insert(TEST_log, { id = %d, title = "%s", objectives = { { text = "%s: 0/8", finished = false } } })'
                % (qid, title, what))
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(1)
    dust = {"kind": "objective", "quest": "Gold Dust Exchange", "id": 47, "label": "Gold Dust"}
    slime = {"kind": "objective", "quest": "Slimy Menace", "id": 99, "label": "Murloc"}
    kelp = {"kind": "objective", "quest": "Collecting Kelp", "id": 112, "label": "Crystal Kelp Frond"}
    first = [{"title": "1. Kobold Miner", "x": 36, "y": 82.7, "mob": "Kobold Miner", "tasks": [dust]},
             {"title": "2. Murloc", "x": 53.8, "y": 65.5, "mob": "Murloc", "tasks": [slime]},
             {"title": "3. Murloc", "x": 56.7, "y": 66.3, "mob": "Murloc", "tasks": [kelp]},
             {"title": "4. Remy", "x": 42.1, "y": 67.3, "tasks": [{"kind": "turn_in", "quest": "Gold Dust Exchange", "id": 47}]}]
    sim.lua("ForeverAI.UI.UpdateAnswer(...)", {"final": True, "msgId": 50, "text": "x", "routePlanned": True, "marks": first})
    sim.run(2)
    again = [{"title": "1. Kobold Geomancer", "x": 63.5, "y": 58.3, "mob": "Kobold Geomancer", "tasks": [dust]},
             {"title": "2. Murloc", "x": 57.0, "y": 67.2, "mob": "Murloc", "tasks": [slime]},
             {"title": "3. Murloc Streamrunner", "x": 58.3, "y": 67.9, "mob": "Murloc Streamrunner", "tasks": [kelp]},
             {"title": "4. Remy", "x": 42.1, "y": 67.3, "tasks": [{"kind": "turn_in", "quest": "Gold Dust Exchange", "id": 47}]}]
    sim.lua("ForeverAI.Map.Merge(...)", again)
    sim.run(2)
    where = ("local out = {} for _, s in ipairs(ForeverAICharDB.marks or {}) do if not s.done then out[#out + 1] = "
             "string.format('%s %.1f,%.1f', tostring(s.title):gsub('^%d+[%.%)]%s*', ''), s.x, s.y) end end "
             "return table.concat(out, ' | ')")
    stops = text(sim.lua(where))
    assert "Kobold Geomancer 63.5,58.3" in stops, stops  # where the geomancers are
    assert "Murloc 53.8,65.5" in stops, stops  # the slime's murlocs: the stop that was for them, where it was
    assert "Murloc Streamrunner 58.3,67.9" in stops and sim.errors == [], stops



def test_a_stop_matched_by_place_keeps_what_the_plan_has_later(sim):
    """Found by walking the game's own Goldshire: Ma Stonefield and "Auntie" Bernice Stonefield stand side by side.
    An update planned Ma early and the turn-in at Bernice after the stops the map holds; the new stop took Bernice's
    place with its own tasks only, and the turn-in of [Goldtooth] was off the map."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.lua('table.insert(TEST_log, { id = 87, title = "Goldtooth", objectives = {}, complete = true })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(1)
    first = [{"title": "1. Kobold Miner", "x": 40.5, "y": 80.3},
             {"title": '2. "Auntie" Bernice Stonefield', "x": 34.5, "y": 84.2, "tasks": [
                 {"kind": "pick_up", "quest": "Princess Must Die!", "id": 88}, {"kind": "turn_in", "quest": "Goldtooth", "id": 87}]}]
    sim.lua("ForeverAI.UI.UpdateAnswer(...)", {"final": True, "msgId": 50, "text": "x", "routePlanned": True, "marks": first})
    sim.run(2)
    again = [{"title": "1. Kobold Miner", "x": 40.5, "y": 80.3},
             {"title": "2. Ma Stonefield", "x": 34.7, "y": 84.5, "tasks": [{"kind": "pick_up", "quest": "Princess Must Die!", "id": 88}]}]
    sim.lua("ForeverAI.Map.Merge(...)", again, True)
    sim.run(2)
    what = "for _, s in ipairs(ForeverAI.Map.List()) do if s.title == 'Ma Stonefield' then return s.what end end"
    assert text(sim.lua(what)) == 'pick up Princess Must Die!; turn in Goldtooth ("Auntie" Bernice Stonefield)', text(sim.lua(what))
    # turned in since: not kept
    sim.lua("ForeverAI.Map.Merge(...)", first)
    sim.run(1)
    sim.lua("table.remove(TEST_log)")  # turned in: it left the log
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(2)
    sim.lua("ForeverAI.Map.Merge(...)", again, True)
    sim.run(2)
    assert text(sim.lua(what)) == "pick up Princess Must Die!", text(sim.lua(what))
    # the whole plan sent: what it does not have is gone
    sim.lua("ForeverAI.Map.Merge(...)", again)
    sim.run(2)
    assert text(sim.lua(what)) == "pick up Princess Must Die!" and sim.errors == []



def test_an_update_planned_in_another_zone_is_asked_again(sim):
    """Found by the seeded replays of the game's own Redridge: the update was asked in Elwynn and planned from there;
    before its answer came the player had gone on into Westfall. The answer went on the map, and the arrow led to a
    stop in Elwynn from Westfall."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim.lua(ROUTE)
    sim.run(2)
    sim.lua('table.insert(TEST_log, { id = 939, title = "Crown of the Earth", objectives = {} })')
    sim._wow.fire(b"QUEST_ACCEPTED")
    sim.run(10)
    (update,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    sim._wow.player.mapID, sim._wow.player.zone = 1457, b"Darnassus"  # through a portal while it is written
    sim.run(1)
    sim.deliver({"kind": "answer", "final": True, "msgId": update["msgId"], "text": "Route updated.", "routePlanned": True,
                 "marks": [{"title": "1. Corithras Moonrage", "x": 56.1, "y": 61.7}]})
    sim.run(10)
    again = [m for m in sim.sent_messages() if m.get("type") == "question"]
    assert again and "Crown of the Earth" in again[0]["text"], again  # asked again, from where the player is
    assert "Corithras Moonrage" not in text(sim.lua(STOPS)) and sim.errors == []



def test_after_an_update_the_arrow_follows_the_plan_unless_the_player_chose(sim):
    """Found by the seeded replays of the game's own Redridge: after the player's own stop the arrow went on to the
    stop after it; the update, planned from where the player was, put the stops left behind first. The arrow stayed
    on the stop after the player's own, and led them away from the plan."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.run(1)
    first = [{"title": "1. Rogue", "x": 37.3, "y": 37.8}, {"title": "2. Byancie", "x": 55.3, "y": 56.8},
             {"title": "3. Smith", "x": 60, "y": 60}, {"title": "4. Camp", "x": 70, "y": 70}]
    sim.lua("ForeverAI.UI.UpdateAnswer(...)", {"final": True, "msgId": 50, "text": "x", "routePlanned": True, "marks": first})
    sim.run(2)
    sim.lua("ForeverAI.Map.SetCurrent(3)")  # their own way: the smith first
    sim._wow.player.x, sim._wow.player.y = 0.60, 0.60
    sim.run(4)
    now = "for i, s in ipairs(ForeverAI.Map.List()) do if s.current then return s.title end end"
    assert text(sim.lua(now)) == "Camp"  # on to the stop after it
    # the update, planned from the smith: the stops left behind first
    plan = [{"title": "1. Byancie", "x": 55.3, "y": 56.8}, {"title": "2. Rogue", "x": 37.3, "y": 37.8},
            {"title": "3. Camp", "x": 70, "y": 70}]
    sim.lua("ForeverAI.Map.Merge(...)", plan)
    sim.run(2)
    assert text(sim.lua(now)) in ("Byancie", "Rogue"), text(sim.lua(now))
    # a stop the player clicked stays the arrow's
    sim.lua("for i, s in ipairs(ForeverAI.Map.List()) do if s.title == 'Camp' then ForeverAI.Map.SetCurrent(i) end end")
    sim.lua("ForeverAI.Map.Merge(...)", plan)
    sim.run(2)
    assert text(sim.lua(now)) == "Camp" and sim.errors == []



def test_through_a_way_into_a_zone_the_route_has_nothing_next_in_asks_at_once(sim):
    """Found by the seeded replays of the game's own Redridge: back in the zone, the player went out again through the
    last way of the route; the stops left were in the zone behind them, and the arrow led there with no way. The
    update came only after the quarter of a minute the add-on waits when a border is crossed for a moment."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.mapID, sim._wow.player.zone = 1457, b"Darnassus"
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.41
    sim.run(2)
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus", tasks = { { kind = "turn_in", quest = "The Enchanted Glade" } } },
      { title = "2. Portal back", x = 30, y = 41, zone = "Darnassus", tasks = { { kind = "travel", zone = "Darkshore" } } } } })""")
    sim.run(2)
    sim.sent_messages()
    sim.lua("ForeverAI.Map.SetCurrent(2)")  # out again before Jocaste
    sim.run(1)
    sim._wow.player.mapID, sim._wow.player.zone = 1439, b"Darkshore"
    sim._wow.player.x, sim._wow.player.y = 0.37, 0.44
    sim.run(6)
    asked = [m.get("text", "") for m in sim.sent_messages() if m.get("type") == "question"]
    assert any("away from the route" in t for t in asked), asked
    assert sim.errors == []



def test_a_stop_chosen_before_a_flight_elsewhere_gives_way_to_the_plan(sim):
    """Found by the seeded replays of the game's own Redridge: the player clicked a stop in Redridge, then flew to
    Westfall; the update planned from Westfall, and the arrow stayed on the stop they had chosen, in Redridge, with no
    way there."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.mapID, sim._wow.player.zone = 1457, b"Darnassus"
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.41
    sim.run(2)
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus" },
      { title = "2. Sister Aquinne", x = 28.9, y = 45.8, zone = "Darnassus" } } })""")
    sim.run(2)
    sim.lua("ForeverAI.Map.SetCurrent(2)")
    sim._wow.player.mapID, sim._wow.player.zone = 1439, b"Darkshore"  # a flight elsewhere
    sim._wow.player.x, sim._wow.player.y = 0.37, 0.44
    sim.run(1)
    sim.lua("ForeverAI.Map.Merge(...)", [
        {"title": "1. Portal to Darnassus", "x": 37.1, "y": 44.1, "zone": "Darkshore", "tasks": [{"kind": "travel", "zone": "Darnassus"}]},
        {"title": "2. Jocaste", "x": 40.4, "y": 8.6, "zone": "Darnassus"},
        {"title": "3. Sister Aquinne", "x": 28.9, "y": 45.8, "zone": "Darnassus"}])
    sim.run(2)
    now = "for i, s in ipairs(ForeverAI.Map.List()) do if s.current then return s.title end end"
    assert text(sim.lua(now)) == "Portal to Darnassus", text(sim.lua(now))
    assert sim.errors == []


def test_a_flight_of_the_route_out_of_order_asks_the_update_on_landing(sim):
    """Found by a live journey on the game's own Redridge: the player took the route's flight early; on landing the
    stops left were in the zone behind them, and the update came only a quarter of a minute later: the add-on had
    let go of "through a way of the route" while the flight was still on."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.mapID, sim._wow.player.zone = 1457, b"Darnassus"
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.41
    sim.run(2)
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus", tasks = { { kind = "turn_in", quest = "The Enchanted Glade" } } },
      { title = "2. Hippogryph master", x = 30, y = 41, zone = "Darnassus", tasks = { { kind = "travel", zone = "Darkshore" } } } } })""")
    sim.run(2)
    sim.sent_messages()
    sim.lua("ForeverAI.Map.SetCurrent(2)")
    sim.run(1)
    sim.lua("UnitOnTaxi = function() return true end")
    sim._wow.player.mapID, sim._wow.player.zone = 1439, b"Darkshore"
    sim._wow.player.x, sim._wow.player.y = 0.37, 0.44
    sim.run(5)  # still on the flight
    sim.lua("UnitOnTaxi = function() return false end")
    sim.run(4)  # landed
    asked = [m.get("text", "") for m in sim.sent_messages() if m.get("type") == "question"]
    assert any("away from the route" in t for t in asked), asked
    assert sim.errors == []



def test_a_stop_matched_by_place_does_not_keep_a_turn_in_whose_objective_comes_later(sim):
    """Found by the seeded replays of the game's own Goldshire: the update planned Ma Stonefield early (pick up), the
    turn-in of [Goldtooth] beside her after the stops the map holds. Kept on her stop, the turn-in came before the
    stop for Goldtooth himself."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.x, sim._wow.player.y = 0.20, 0.20
    sim.lua('table.insert(TEST_log, { id = 87, title = "Goldtooth", objectives = { { text = "Bernice\'s Necklace: 0/1", finished = false } } })')
    sim._wow.fire(b"QUEST_LOG_UPDATE")
    sim.run(1)
    first = [{"title": "1. Goldtooth", "x": 41.7, "y": 78.0, "mob": "Goldtooth", "tasks": [
                 {"kind": "objective", "quest": "Goldtooth", "id": 87, "label": "Bernice's Necklace"}]},
             {"title": "2. Ma Stonefield", "x": 34.7, "y": 84.5, "tasks": [
                 {"kind": "pick_up", "quest": "Princess Must Die!", "id": 88}, {"kind": "turn_in", "quest": "Goldtooth", "id": 87}]}]
    sim.lua("ForeverAI.UI.UpdateAnswer(...)", {"final": True, "msgId": 50, "text": "x", "routePlanned": True, "marks": first})
    sim.run(2)
    again = [{"title": "1. Ma Stonefield", "x": 34.7, "y": 84.5, "tasks": [{"kind": "pick_up", "quest": "Princess Must Die!", "id": 88}]},
             {"title": "2. Goldtooth", "x": 41.7, "y": 78.0, "mob": "Goldtooth", "tasks": [
                 {"kind": "objective", "quest": "Goldtooth", "id": 87, "label": "Bernice's Necklace"}]}]
    sim.lua("ForeverAI.Map.Merge(...)", again, True)
    sim.run(2)
    what = "for _, s in ipairs(ForeverAI.Map.List()) do if s.title == 'Ma Stonefield' then return s.what end end"
    assert text(sim.lua(what)) == "pick up Princess Must Die!", text(sim.lua(what))
    # the turn-in stays on the map, after the stop for Goldtooth
    order = "local o = {} for _, s in ipairs(ForeverAI.Map.List()) do if not s.done then o[#o + 1] = s.title .. ':' .. tostring(s.what) end end return table.concat(o, ' | ')"
    stops = text(sim.lua(order)).split(" | ")
    assert stops[-1] == "Ma Stonefield:turn in Goldtooth" and any(x.startswith("Goldtooth:") for x in stops[:-1]), stops
    assert sim.errors == []



def test_the_arrow_moved_on_into_another_zone_asks_the_update_at_once(sim):
    """Found by a journey on the game's own Redridge: the player left the zone through a way of the route with a stop
    left behind in it; when the stops beyond were done the arrow led back to it, with no way there, and the update
    came only a quarter of a minute later."""
    sim.lua("ForeverAI.db.autoRoute = true")
    sim._wow.player.mapID, sim._wow.player.zone = 1439, b"Darkshore"
    sim._wow.player.x, sim._wow.player.y = 0.30, 0.41
    sim.run(2)
    sim.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "The route.", routePlanned = true, marks = {
      { title = "1. Jocaste", x = 40.4, y = 8.6, zone = "Darnassus" },
      { title = "2. The shore", x = 31, y = 42, zone = "Darkshore" } } })""")
    sim.run(2)
    sim.sent_messages()
    sim.lua("ForeverAI.Map.SetCurrent(2)")
    sim._wow.player.x, sim._wow.player.y = 0.31, 0.42  # the shore is reached: the arrow leads to Jocaste, elsewhere
    sim.run(6)
    asked = [m.get("text", "") for m in sim.sent_messages() if m.get("type") == "question"]
    assert any("away from the route" in t for t in asked), asked
    assert sim.errors == []
