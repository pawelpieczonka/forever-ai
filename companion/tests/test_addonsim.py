"""The add-on simulator (companion/addonsim): the add-on runs in it without errors, its window is laid out and drawn
the way the game does it, a question goes through the real ForeverAI app and back, and the command line works."""

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from addonsim import App, Sim, install_game
from addonsim.__main__ import main
from addonsim.text import TextEngine, parse

ADDON = Path(__file__).resolve().parents[2] / "addon" / "ForeverAI"
CLOCK = datetime(2026, 11, 10, 12, 0)


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game", clock=CLOCK).start()
    yield s
    s.close()


def _near(color, want, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(color, want, strict=False))


def test_window_builds_and_is_drawn(sim, tmp_path):
    sim.slash("/fai")
    texts = sim.texts()
    for t in ("ForeverAI", "Chat", "History", "Memory", "Settings", "How can I help?", "Send",
              "Ask… (Alt+click an item, spell or quest to tag it)", "Attach the current screen"):
        assert t in texts
    assert sim.errors == [] and sim.warnings == [] and sim.placeholders == set()
    # 470 x 600 units at 1080p (1.40625 pixels per unit), and the 16 px margin around it
    path = sim.screenshot(tmp_path / "chat.png")
    with Image.open(path) as im:
        assert abs(im.width - (round(470 * 1.40625) + 32)) <= 2 and abs(im.height - (round(600 * 1.40625) + 32)) <= 2
    full = sim.render().image()
    x0, y0, x1, y1 = sim.box("Send")
    assert _near(full.getpixel(((x0 + x1) // 2, y0 + 3)), (33, 122, 89))  # P.primary
    wx0, wy0, _, _ = sim.frame_box("ForeverAIFrame")
    assert _near(full.getpixel((wx0 + 200, wy0 + 300)), (12, 14, 18), 4)  # P.bg over the world
    # the text field shows the text that was typed, and the placeholder goes away
    sim.type("hello")
    assert "hello" in sim.texts() and "Ask… (Alt+click an item, spell or quest to tag it)" not in sim.texts()


def test_tooltip_hover_and_scroll_clipping(sim, tmp_path):
    sim.slash("/fai")
    sim.hover("x")
    assert "Close (Esc)" in sim.texts()
    tip = sim.frame_box("GameTooltip")
    win = sim.frame_box("ForeverAIFrame")
    assert tip[3] <= sim.box("x")[1] + 1  # ANCHOR_TOP: above the button
    with Image.open(sim.screenshot(tmp_path / "tip.png")) as im:  # the crop takes the tooltip in
        assert im.height > win[3] - win[1] + 32
    sim.leave()
    assert "Close (Esc)" not in sim.texts()
    # a long conversation: the chat scrolls to the newest entry, older ones are cut off by the scroll frame
    for i in range(20):
        sim.lua("ForeverAI.UI.AddSystemNote(...)", f"note {i} " + "word " * 30)
    sim.run(0.2)
    texts = sim.texts()
    assert any(t.startswith("note 19 ") for t in texts) and not any(t.startswith("note 0 ") for t in texts)
    for _ in range(6):
        sim.wheel(1)  # 50 units a notch
    texts = sim.texts()
    assert any(t.startswith("note 0 ") for t in texts) and not any(t.startswith("note 19 ") for t in texts)


def test_reload_keeps_saved_variables(sim):
    sim.slash("/fai")
    sim.click("Settings")
    sim.click("15")
    sim.click("Chat (private)")
    sim.slash("/reload")
    assert sim.reloads == 1
    assert sim.lua("return ForeverAIDB.fontSize") == 15 and sim.lua("return ForeverAIDB.sections.chat") is True
    wtf = sim.game_dir / "WTF" / "Account" / "SIMULATOR"
    assert "fontSize" in (wtf / "SavedVariables" / "ForeverAI.lua").read_text()
    assert (wtf / "Forever" / "Simtester" / "SavedVariables" / "ForeverAI.lua").exists()
    assert sim.errors == []


def test_reload_keeps_the_character(sim):
    """A /reload loads the UI again but the character is the server's: its level, XP and money stay, so the add-on's
    progress session goes on without a level-up or a money change that did not happen."""
    p = sim._wow.player
    sim.run(61)
    p.level, p.xp, p.xpMax, p.money = 25, 400, 20200, 200000
    sim._wow.fire(b"PLAYER_LEVEL_UP", 25)
    sim._wow.fire(b"PLAYER_XP_UPDATE", b"player")
    sim.run(2)
    sim.reload()
    assert sim.lua("return UnitLevel('player'), UnitXP('player'), GetMoney()") == (25, 400, 200000)
    sim.run(61)
    sim._wow.player.xp = 900
    sim._wow.fire(b"PLAYER_XP_UPDATE", b"player")
    sim.run(1)
    cur = sim.to_py(sim.lua("return ForeverAICharDB.progress.cur"))
    assert cur["reloads"] == 1 and cur["lvl0"] == 24 and cur["lvl"] == 25 and cur["levels"] == 1
    assert cur["gain"] == (18700 - 11850) + 900 and cur["money"] == 200000
    assert len(sim.to_py(sim.lua("return ForeverAICharDB.progress.levels"))) == 1
    assert sim.errors == []


def test_question_without_the_app(sim):
    """The add-on paints the question into its data block and takes a screenshot; nothing answers."""
    sim.ask("Is anyone there?")
    sim.run(1)
    sent = sim.sent_messages()
    assert [m["type"] for m in sent] == ["question"]
    q = sent[0]
    assert q["text"] == "Is anyone there?" and q["char"] == "Simtester-Forever" and q["nextInbox"] == 1
    assert q["snapshot"]["player"]["zone"] == "Darkshore" and q["snapshot"]["player"]["class"] == "Hunter"
    assert q["snapshot"]["progress"]["level"] == 24 and "collectErrors" not in q["snapshot"]
    sim.run(10)  # also long enough for the add-on's timers after login (spell index, block cells, tooltips)
    assert "No response. Is the ForeverAI app running?" in " ".join(sim.texts())
    assert sim.errors == [] and sim.warnings == []


def test_question_through_the_app(tmp_path):
    """End to end: the add-on, the screenshot with the data block, the app's watcher and service, the inbox
    add-ons and the answer in the window."""
    game, data = tmp_path / "game", tmp_path / "data"
    reply = {"text": "Turn it in to **Gwennyth Bly'Leggonde** in Auberdine.",
             "tools": [{"name": "mark_on_map", "args": {"title": "Gwennyth Bly'Leggonde", "x": 37.5, "y": 41.2}}]}
    with App(game, data, replies=[reply], pool=60) as app:
        sim = Sim(game, realtime=True, clock=CLOCK).start()
        try:
            sim.ask("Where do I turn in my quest?")
            sim.wait_answer(60)
            texts = sim.texts()
            assert "Turn it in to Gwennyth Bly'Leggonde in Auberdine." in texts
            assert "Marked on the map (map pin)" in " ".join(texts)
            assert any(t.startswith("scripted-ai") for t in texts)  # the answer's model line
            assert [m["type"] for m in app.received] == ["question"]
            assert app.received[0]["text"] == "Where do I turn in my quest?"
            assert "Tool: mark_on_map" in app.activity
            assert not list((game / "Screenshots").iterdir())  # the app read and removed the data blocks
            # the second question uses the scripted default reply; the conversation keeps both
            sim.ask("And then?")
            sim.wait_answer(60)
            assert "You asked: And then?" in "\n".join(sim.texts())
            sim.click("History")
            assert "Where do I turn in my quest?" in sim.texts()
            assert sim.errors == []
        finally:
            sim.close()


def test_text_layout():
    eng = TextEngine()
    paras = parse("|cffff0000red|r plain ||pipe |Hitem:1|h[Link]|h|nnext")
    assert [[p.text for p in para] for para in paras] == [["red", " plain ", "|", "pipe ", "[Link]"], ["next"]]
    assert paras[0][0].color == (255, 0, 0) and paras[0][1].color is None
    font = "Fonts\\ARIALN.TTF"
    one = eng.layout("word " * 20, font, 18, 0, False)
    assert len(one.lines) == 1 and one.height == 18
    wrapped = eng.layout("word " * 20, font, 18, 120, True)
    assert len(wrapped.lines) > 3 and all(ln.width <= 120.5 for ln in wrapped.lines)
    assert wrapped.height == 18 * len(wrapped.lines)
    cut = eng.layout("a long line that does not fit", font, 18, 60, False)
    assert cut.truncated and cut.lines[0].pieces[-1].text == "..." and cut.lines[0].width <= 60.5
    assert eng.layout("", font, 18, 100, True).height == 0


def test_command_line(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(["--no-app", "-o", str(out), "--clock", "2026-11-10 12:00", "-s", "open", "-s", "tab Settings",
                 "-s", "expect In use:", "-s", "shot settings", "-s", "dump"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["ok"] and report["failed"] is None
    assert report["screenshots"] == [str(out / "settings.png")] and (out / "settings.png").exists()
    assert any(t.startswith("In use:") for t in report["texts"]["texts-1"])
    assert json.loads((out / "report.json").read_text())["ok"]
    code = main(["--no-app", "-o", str(out), "-s", "open", "-s", "click No such button"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and "no visible button or field 'No such button'" in report["failed"]


# globals the add-on reads that the game does not have unless another add-on (or a newer client) provides them
ABSENT = {"Auctionator", "AUCTIONATOR_PRICE_DATABASE", "AUCTIONATOR_SAVEDVARS", "AuctionFrame", "AuctionHouseFrame",
          "InspectFrame", "LibStub", "PawnGetItemData", "PawnIsInitialized", "PawnIsItemAnUpgrade", "QuestieLoader",
          "TomTom", "issecretvalue"}
# the Classic trade skill and craft API: WoW Forever has only C_TradeSkillUI (Professions.lua uses whichever exists)
ABSENT |= {"CollapseCraftSkillLine", "CollapseTradeSkillSubClass", "ExpandCraftSkillLine", "ExpandTradeSkillSubClass",
           "GetCraftDisplaySkillLine", "GetCraftInfo", "GetCraftItemLink", "GetCraftNumReagents", "GetCraftReagentInfo",
           "GetCraftReagentItemLink", "GetNumCrafts", "GetNumTradeSkills", "GetTradeSkillInfo",
           "GetTradeSkillInvSlotFilter", "GetTradeSkillItemLink", "GetTradeSkillItemNameFilter", "GetTradeSkillLine",
           "GetTradeSkillNumMade", "GetTradeSkillNumReagents", "GetTradeSkillReagentInfo",
           "GetTradeSkillReagentItemLink", "GetTradeSkillSubClassFilter", "IsTradeSkillLinked",
           "TradeSkillFrameAvailableFilterCheckButton"}


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="needs luac5.1")
def test_every_global_the_addon_reads_is_emulated(sim):
    """A new game function in the add-on needs the simulator to know it (lua/wow.lua or lua/world.lua)."""
    read, written = set(), set()
    for f in sorted(ADDON.glob("*.lua")):
        listing = subprocess.run(["luac5.1", "-l", "-p", str(f)], capture_output=True, text=True, check=True).stdout
        for op, name in re.findall(r"\b(GETGLOBAL|SETGLOBAL)\b.*?;\s*([A-Za-z_]\w*)", listing):
            (read if op == "GETGLOBAL" else written).add(name)
    missing = sorted(n for n in read - written - ABSENT if sim.lua("return _G[...] == nil", n) is True)
    assert missing == [], "add these to addonsim/lua/world.lua or wow.lua: " + ", ".join(missing)


TOMTOM = r"""
TomTom = { points = {}, n = 0 }
function TomTom:AddWaypoint(map, x, y, opts)
  self.n = self.n + 1
  self.points[self.n] = opts.title
  if opts.crazy then self.arrow = opts.title end
  return self.n
end
function TomTom:RemoveWaypoint(uid) self.points[uid] = nil end
"""
ON_THE_MAP = r"""
local out = {}
for _, title in pairs(TomTom.points) do out[#out + 1] = title end
table.sort(out)
return table.concat(out, " | ")
"""


def test_new_marks_replace_the_earlier_ones_unless_added(sim):
    sim.slash("/fai")
    sim.lua(TOMTOM)
    mark = 'ForeverAI.UI.MarksCard({ { title = "%s", x = %s, y = %s } }, %s)'
    sim.lua(mark % ("Dazalar", 56.7, 59.5, "true"))
    sim.lua(mark % ("Byancie", 55.3, 56.8, "true"))
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 1. Byancie"
    sim.lua(mark % ("Lyreena", 57.6, 56.6, "false"))  # an extra stop: the answer said to keep the earlier marks
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 1. Byancie | ForeverAI: 2. Lyreena"
    sim.lua(mark % ("Byancie", 55.3, 56.8, "false"))  # a place that is on the route already is not marked twice
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 1. Byancie | ForeverAI: 2. Lyreena"
    sim.lua(mark % ("Dazalar", 56.7, 59.5, "nil"))  # an app that does not say: replace
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 1. Dazalar"
    assert sim.errors == []


SECTION = "local s = ForeverAI.Collect.sections.marks() return s and table.concat(s, ' | ') or 'none'"


def test_marks_stay_after_reload_and_are_done_when_reached(sim):
    sim.slash("/fai")
    sim.lua(TOMTOM + "function TomTom:SetCrazyArrow(uid, dist, title) self.arrow = title end")
    sim._wow.player.x, sim._wow.player.y = 0.40, 0.40
    sim.lua('ForeverAI.UI.MarksCard({ { title = "1. Postman", x = 45.0, y = 40.0 }, { title = "Giver", x = 50.0, y = 40.0 } })')
    both = b"1. Postman \xe2\x80\x94 Darkshore 45.0, 40.0 (299 m) | 2. Giver \xe2\x80\x94 Darkshore 50.0, 40.0 (599 m)"
    assert sim.lua(SECTION) == both
    assert sim.lua("return ForeverAI.Collect.Snapshot().marks ~= nil") is True  # goes with every question

    sim.reload()  # the game forgets waypoints; the add-on puts its marks back
    sim.lua(TOMTOM + "function TomTom:SetCrazyArrow(uid, dist, title) self.arrow = title end")
    sim.run(4)
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 1. Postman | ForeverAI: 2. Giver"
    assert sim.lua(SECTION) == both

    sim._wow.fire(b"PLAYER_ENTERING_WORLD")  # a loading screen (a boat, an instance): no second set of waypoints
    sim.run(4)
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 1. Postman | ForeverAI: 2. Giver" and sim.lua("return TomTom.n") == 4
    assert sim.lua("local n = 0 for _ in pairs(TomTom.points) do n = n + 1 end return n") == 2

    sim._wow.player.x = 0.4495  # a few steps from the first stop
    sim.run(4)
    # done: off the map, still on the list with its number, and the arrow says the number the list says
    assert sim.lua(ON_THE_MAP) == b"ForeverAI: 2. Giver" and sim.lua("return TomTom.arrow") == b"ForeverAI: 2. Giver"
    assert sim.lua(SECTION) == (b"1. Postman \xe2\x80\x94 Darkshore 45.0, 40.0 (done) | "
                                b"2. Giver \xe2\x80\x94 Darkshore 50.0, 40.0 (302 m)")

    sim.lua("ForeverAI.Map.Clear()")
    assert sim.lua(SECTION) == b"none" and sim.lua(ON_THE_MAP) == b""
    assert sim.errors == []
