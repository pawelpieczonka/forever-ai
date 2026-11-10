"""Progress over time: the add-on's Progress.lua against a mocked game (sampling, throttling, sessions across /reload
and relog, the history cap, money causes) and progress.py's math and text."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from foreverai import progress
from foreverai.prompt import SYSTEM_PROMPT, render_snapshot

ADDON = Path(__file__).resolve().parents[2] / "addon" / "ForeverAI"

LUA_HARNESS = r"""
local addonDir = arg[1]
-- the clocks: time() is the computer's clock, GetTime() the game's
local now = os.time({ year = 2026, month = 11, day = 10, hour = 12, min = 0, sec = 0 })
local gt = 5000
function time() return now end
function GetTime() return gt end
date = os.date
local timers, tickers = {}, {}
C_Timer = {
  After = function(s, fn) timers[#timers + 1] = { due = gt + s, fn = fn } end,
  NewTicker = function(s, fn) local t = { every = s, due = gt + s, fn = fn } tickers[#tickers + 1] = t return t end,
}
local frames = {}
function CreateFrame()
  local f = { events = {}, scripts = {} }
  function f:RegisterEvent(e) self.events[e] = true end
  function f:SetScript(k, fn) self.scripts[k] = fn end
  frames[#frames + 1] = f
  return f
end
local repairHooks = {}
function hooksecurefunc(name, fn) if name == "RepairAllItems" then repairHooks[#repairHooks + 1] = fn end end
function RepairAllItems(guild) for _, h in ipairs(repairHooks) do h(guild) end end
Enum = { PlayerInteractionType = { Merchant = 5, TaxiNode = 6, Trainer = 7, MailInfo = 17, Auctioneer = 21 } }
COMBATLOG_XPGAIN_FIRSTPERSON = "%s dies, you gain %d experience."

-- the character
local me = { level = 20, xp = 1000, xpMax = 23200, rested = 0, money = 10000, zone = "Redridge Mountains", afk = false }
function UnitLevel() return me.level end
function UnitXP() return me.xp end
function UnitXPMax() return me.xpMax end
function GetXPExhaustion() return me.rested > 0 and me.rested or nil end
function GetMoney() return me.money end
function GetRealZoneText() return me.zone end
function GetZoneText() return me.zone end
function UnitIsAFK() return me.afk end
function GetMaxPlayerLevel() return 60 end

local SV = {} -- ForeverAICharDB: survives a /reload and a relog
local ns, printed
local function load()
  frames, tickers, timers = {}, {}, {}
  printed = {}
  ns = { char = SV }
  ns.clean = function(v) return v end
  ns.safe = function(fn, ...)
    if type(fn) ~= "function" then return nil end
    local r = { pcall(fn, ...) }
    if not r[1] then return nil end
    return unpack(r, 2)
  end
  ns.print = function(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end printed[#printed + 1] = table.concat(t, " ") end
  ns.Collect = { sections = {}, Budget = function() end }
  assert(loadfile(addonDir .. "/Progress.lua"))("ForeverAI", ns)
end
local function fire(event, ...)
  for _, f in ipairs(frames) do
    if f.events[event] and f.scripts.OnEvent then f.scripts.OnEvent(f, event, ...) end
  end
end
-- time goes on second by second; timers and tickers run when due
local function advance(seconds)
  for _ = 1, seconds do
    now, gt = now + 1, gt + 1
    local due = {}
    for i = #timers, 1, -1 do if timers[i].due <= gt then table.insert(due, 1, table.remove(timers, i)) end end
    for _, t in ipairs(due) do t.fn() end
    for _, t in ipairs(tickers) do if t.due <= gt then t.due = gt + t.every t.fn() end end
  end
end
local function out(k, v) print(k .. " " .. tostring(v)) end
-- logging out: the UI is gone, nothing of it runs until the next login
local function logout() fire("PLAYER_LOGOUT") timers, tickers = {}, {} end
local function P() return SV.progress end

-- JSON for the Python side
local function json(v)
  local t = type(v)
  if t == "table" then
    if #v > 0 or next(v) == nil then
      local parts = {}
      for _, x in ipairs(v) do parts[#parts + 1] = json(x) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, x in pairs(v) do parts[#parts + 1] = string.format("%q", tostring(k)) .. ":" .. json(x) end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif t == "string" then return string.format("%q", v)
  elseif t == "boolean" or t == "number" then return tostring(v) end
  return "null"
end

load()
-- 1) login
fire("PLAYER_ENTERING_WORLD", true, false)
out("LOGIN", #P().samples .. "," .. tostring(P().cur ~= nil) .. "," .. P().cur.start)
out("ZONE_FIRST", P().samples[1]:match("|(.*)$"))

-- 2) XP every 10 seconds for 5 minutes: about one sample a minute, the last change is kept
for i = 1, 30 do
  me.xp = me.xp + 100
  fire("PLAYER_XP_UPDATE", "player")
  advance(10)
end
advance(70)
out("THROTTLED", #P().samples)
out("GAIN", P().cur.gain)
local last = P().samples[#P().samples]
out("LASTXP", last:match("^%d+ %d+ %d+ (%d+)"))

-- 3) a level-up: the rest of level 20 counts, then the XP at 21
me.xp = 23000
fire("PLAYER_XP_UPDATE", "player")
advance(61)
me.level, me.xp, me.xpMax = 21, 300, 25200
fire("PLAYER_LEVEL_UP", 21)
advance(2)
out("LEVELUP", P().cur.levels .. "," .. P().cur.gain .. "," .. #P().levels .. "," .. P().levels[1].level)

-- 4) money: each change with its cause
local function money(delta) me.money = me.money + delta fire("PLAYER_MONEY") end
fire("MERCHANT_SHOW") money(5000) RepairAllItems() money(-3000) RepairAllItems(true) money(-200) fire("MERCHANT_CLOSED")
advance(5)
fire("CHAT_MSG_MONEY", "You loot 27 Copper") money(27)
advance(5)
money(55) fire("CHAT_MSG_MONEY", "Your share of the loot is 55 Copper.") -- the message right after the money
advance(1)
fire("QUEST_TURNED_IN", 123, 450, 1000) money(1000)
advance(5)
fire("TAXIMAP_OPENED") fire("TAXIMAP_CLOSED") advance(1) money(-150)
advance(10)
fire("MAIL_SHOW") money(20000) fire("MAIL_CLOSED")
fire("AUCTION_HOUSE_SHOW") money(-400) fire("AUCTION_HOUSE_CLOSED")
fire("PLAYER_INTERACTION_MANAGER_FRAME_SHOW", 7) money(-1000) fire("PLAYER_INTERACTION_MANAGER_FRAME_HIDE", 7)
advance(10)
money(-77)
advance(2)
local function sorted(t) local list = {} for k, v in pairs(t) do list[#list + 1] = k .. "=" .. v end table.sort(list) return table.concat(list, ",") end
out("EARNED", sorted(P().cur.earned))
out("SPENT", sorted(P().cur.spent))
out("QUESTS", P().cur.quests .. "," .. P().cur.questXp)

-- 5) kills and deaths
fire("CHAT_MSG_COMBAT_XP_GAIN", "Dire Condor dies, you gain 127 experience.")
fire("CHAT_MSG_COMBAT_XP_GAIN", "Experience gained: 450.")
fire("PLAYER_DEAD")
out("COUNTS", P().cur.kills .. "," .. P().cur.deaths)

-- 6) AFK: logged in, but not playing
ns.Progress.Heartbeat()
local act0, online0 = P().cur.act, P().online
me.afk = true
advance(600)
ns.Progress.Heartbeat()
me.afk = false
out("AFK", (P().cur.act - act0) .. "," .. (P().online - online0))
advance(120)
ns.Progress.Heartbeat()
out("BACK", P().cur.act - act0)

-- 7) /played (the player typed it)
fire("TIME_PLAYED_MSG", 90000, 1800)
out("PLAYED", P().played.total .. "," .. P().played.level .. "," .. P().played.lvl)

-- 8) /reload: the same session goes on
local start = P().cur.start
logout()
local before = #P().samples
advance(8)
load()
fire("PLAYER_ENTERING_WORLD", false, true)
out("RELOAD", tostring(P().cur.start == start) .. "," .. P().cur.reloads .. "," .. #P().sessions .. "," .. (#P().samples - before))
fire("PLAYER_ENTERING_WORLD", false, false) -- a loading screen later in the same UI: nothing new
out("LOADING", P().cur.reloads)

-- the snapshot section of the running session
me.xp = me.xp + 5000
fire("PLAYER_XP_UPDATE", "player")
advance(65)
local sec = ns.Collect.sections.progress()
print("SECTION " .. json(sec))

-- 9) logout and a login two hours later: a new session, the old one becomes a summary
logout()
local oldGain = P().cur.gain
advance(7200)
load()
fire("PLAYER_ENTERING_WORLD", true, false)
local s1 = P().sessions[#P().sessions]
out("RELOG", #P().sessions .. "," .. tostring(P().cur.start ~= start) .. "," .. tostring(s1.stop ~= nil) .. "," ..
  tostring(s1.gain == oldGain) .. "," .. tostring(s1.last == nil and s1.xp == nil))

-- 10) without the game's flags: a short break is a /reload, a long one a new session
logout()
advance(30)
load()
fire("PLAYER_ENTERING_WORLD")
out("NOFLAGS_SHORT", #P().sessions .. "," .. P().cur.reloads)
me.xp = me.xp + 10
fire("PLAYER_XP_UPDATE", "player")
advance(61)
logout()
advance(600)
load()
fire("PLAYER_ENTERING_WORLD")
out("NOFLAGS_LONG", #P().sessions)
-- a session in which nothing happened leaves no summary
logout()
advance(3600)
load()
fire("PLAYER_ENTERING_WORLD", true, false)
out("EMPTY", #P().sessions)

-- 11) the history stays bounded: sessions, samples and their age
for i = 1, 60 do
  me.money = me.money + 1
  fire("PLAYER_MONEY")
  advance(120)
  logout()
  advance(3600)
  load()
  fire("PLAYER_ENTERING_WORLD", true, false)
end
out("CAP_SESSIONS", #P().sessions)
local oldest = tonumber(P().samples[1]:match("^%d+"))
out("CAP_AGE", tostring(now - oldest <= 3 * 86400))
for i = 1, 1700 do me.money = me.money + 1 P().cur.money = 0 ns.Progress.Sample(true) end
out("CAP_SAMPLES", #P().samples)
local sec2 = ns.Collect.sections.progress()
out("SNAP", #sec2.samples .. "," .. #sec2.sessions .. "," .. #(sec2.levels or {}) .. "," .. #json(sec2))

-- 12) /fai progress
ns.Progress.Debug()
out("DEBUG", #printed .. "##" .. table.concat(printed, "##"))
"""


@pytest.fixture(scope="module")
def lua_run(tmp_path_factory):
    if not shutil.which("lua5.1"):
        pytest.skip("needs lua5.1")
    script = tmp_path_factory.mktemp("progress") / "harness.lua"
    script.write_text(LUA_HARNESS)
    run = subprocess.run(["lua5.1", str(script), str(ADDON)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    return dict(line.split(" ", 1) for line in run.stdout.strip().splitlines())


def test_progress_lua_samples_and_throttles(lua_run):
    count, has_session, _ = lua_run["LOGIN"].split(",")
    assert count == "1" and has_session == "true"
    assert lua_run["ZONE_FIRST"] == "Redridge Mountains"  # the first sample of a session names the zone
    # 30 XP events in 5 minutes: one sample a minute, not 30; the last change is kept by the queued sample
    assert 5 <= int(lua_run["THROTTLED"]) <= 7
    assert lua_run["GAIN"] == "3000" and lua_run["LASTXP"] == "4000"


def test_progress_lua_level_up(lua_run):
    levels, gain, ups, level = lua_run["LEVELUP"].split(",")
    # 3000 + (23000 - 4000) on level 20 + (23200 - 23000) to the level-up + 300 on level 21
    assert levels == "1" and gain == str(3000 + 19000 + 200 + 300) and ups == "1" and level == "21"


def test_progress_lua_money_causes(lua_run):
    earned = dict(x.split("=") for x in lua_run["EARNED"].split(","))
    spent = dict(x.split("=") for x in lua_run["SPENT"].split(","))
    assert earned == {"vendor": "5000", "loot": "82", "quest": "1000", "mail": "20000"}
    # the guild-paid repair (RepairAllItems(true)) is the vendor's; the late change without a window is "other"
    assert spent == {"repair": "3000", "vendor": "200", "flight": "150", "auction": "400", "trainer": "1000", "other": "77"}
    assert lua_run["QUESTS"] == "1,450"


def test_progress_lua_counts_afk_and_played(lua_run):
    assert lua_run["COUNTS"] == "1,1"  # "Experience gained" is not a kill
    act, online = (int(x) for x in lua_run["AFK"].split(","))
    assert act == 0 and online == 600  # AFK is logged-in time, not playing time
    assert int(lua_run["BACK"]) == 120
    assert lua_run["PLAYED"] == "90000,1800,21"


def test_progress_lua_sessions_across_reload_and_relog(lua_run):
    same, reloads, sessions, new_samples = lua_run["RELOAD"].split(",")
    assert same == "true" and reloads == "1" and sessions == "0" and int(new_samples) >= 1
    assert lua_run["LOADING"] == "1"
    assert lua_run["RELOG"] == "1,true,true,true,true"
    assert lua_run["NOFLAGS_SHORT"] == "1,1"
    assert lua_run["NOFLAGS_LONG"] == "2"
    assert lua_run["EMPTY"] == "2"


def test_progress_lua_history_is_bounded(lua_run):
    assert lua_run["CAP_SESSIONS"] == "40"
    assert lua_run["CAP_AGE"] == "true"
    assert lua_run["CAP_SAMPLES"] == "1500"
    samples, sessions, levels, size = (int(x) for x in lua_run["SNAP"].split(","))
    assert samples <= 34 and sessions == 8 and size < 6000  # a few KB in every question


def test_progress_lua_debug_command(lua_run):
    count, *lines = lua_run["DEBUG"].split("##")
    assert int(count) == 4
    assert lines[0].startswith("session since") and "money earned" in lines[1] and "stored:" in lines[2]
    assert lines[3].startswith("played:")


def test_progress_section_from_lua_renders(lua_run):
    """End to end: the section the add-on builds becomes the model's text."""
    sec = json.loads(lua_run["SECTION"])
    assert sec["session"]["reloads"] == 1 and sec["level"] == 21 and len(sec["samples"]) >= 3
    text = progress.render(sec)
    assert "Now: level 21" in text and "1 /reload, same session" in text
    assert "earned" in text and "vendor" in text and "repair" in text
    assert "Played:" in text and "Time to level" in text


# ------------------------------------------------------------------------ progress.py
def _t(hour, minute=0, day=10):
    return datetime(2026, 11, day, hour, minute).timestamp()


def _row(t, act, lvl, xp, xp_max=23200, rested=0, cash=10000):
    return [t, act, lvl, xp, xp_max, rested, cash]


def test_step_xp_across_level_ups():
    a = progress._sample(_row(0, 0, 20, 20000))
    assert progress.step_xp(a, progress._sample(_row(60, 60, 20, 21000))) == 1000
    assert progress.step_xp(a, progress._sample(_row(60, 60, 21, 500, 25200))) == 3200 + 500
    # two levels between samples: the level in between comes from the table
    assert progress.step_xp(a, progress._sample(_row(60, 60, 22, 100, 27300))) == 3200 + 25200 + 100
    assert progress.step_xp(a, progress._sample(_row(60, 60, 20, 100))) == 0  # never negative


def test_pace_leaves_out_afk_gaps():
    # with the add-on's playing time: 1 hour of play over 3 hours of wall time
    rows = [progress._sample(r) for r in (_row(0, 0, 20, 0), _row(1800, 1800, 20, 6000), _row(9000, 1800, 20, 6000),
                                          _row(10800, 3600, 20, 12000))]
    xp, play, _ = progress.pace(rows)
    assert (xp, play) == (12000, 3600) and progress.per_hour(xp, play) == 12000
    # without it (older data): gaps over 5 minutes are breaks
    rows = [progress._sample(r) for r in ([0, None, 20, 0, 23200, 0, 0], [240, None, 20, 1000, 23200, 0, 0],
                                          [3840, None, 20, 1100, 23200, 0, 0], [4080, None, 20, 2100, 23200, 0, 0])]
    xp, play, _ = progress.pace(rows)
    assert xp == 2100 and play == 480
    # the last 45 minutes of play only
    rows = [progress._sample(_row(i * 60, i * 60, 20, i * 100)) for i in range(0, 121)]
    xp, play, _ = progress.pace(rows, 45 * 60)
    assert play == 45 * 60 and xp == 4500


def test_xp_to_level_with_table_and_known_levels():
    assert progress.xp_to_level(20, 1000, 23200, 21) == 22200
    assert progress.xp_to_level(20, 1000, 23200, 22) == 22200 + 25200
    assert progress.xp_to_level(20, 1000, 23200, 22, {21: 30000}) == 22200 + 30000  # what the game showed wins
    assert progress.xp_to_level(20, 1000, 23200, 20) == 0
    assert progress.table_scale(20, 23200) == 1.0 and progress.table_scale(20, 46400) == 2.0
    assert progress.xp_to_level(20, 0, 46400, 22, scale=2.0) == 46400 + 50400
    assert sum(progress.CLASSIC_XP) == 4_084_700
    assert progress.targets(20, 60) == [21, 22, 23, 25, 30, 35, 40, 45, 50, 55, 60]
    assert progress.targets(58, 60) == [59, 60]


def _section(**over):
    start = _t(10)
    sec = {
        "now": _t(12), "level": 20, "xp": 11200, "xpMax": 23200, "rested": 0, "money": 30000, "maxLevel": 60,
        "online": 20000, "since": _t(9, day=1),
        "session": {"start": start, "act": 5400, "gain": 18000, "questXp": 4000, "lvl0": 19, "lvl": 20, "money0": 10000,
                    "money": 30000, "levels": 1, "kills": 80, "quests": 5, "deaths": 1, "reloads": 1,
                    "earned": {"loot": 15000, "vendor": 9000}, "spent": {"repair": 4000}, "zones": {"Redridge Mountains": 4000,
                                                                                                     "Lakeshire": 900}},
        "samples": [_row(start + i * 600, i * 450, 20, 1200 + i * 833, cash=10000 + i * 1666) for i in range(13)],
        "sessions": [{"start": _t(20, day=9), "stop": _t(23, day=9), "act": 9000, "gain": 30000, "lvl0": 18, "lvl": 19,
                      "money0": 5000, "money": 10000, "kills": 120, "quests": 9, "deaths": 0,
                      "earned": {"loot": 8000}, "spent": {"trainer": 3000}, "zones": {"Westfall": 7000}},
                     {"start": _t(8), "stop": _t(9), "act": 3000, "gain": 6000, "lvl0": 19, "lvl": 19, "money0": 9000,
                      "money": 10000, "kills": 30, "quests": 1, "deaths": 0, "earned": {"loot": 1000}}],
        "levels": [{"level": 19, "at": _t(21, day=9), "online": 10000}, {"level": 20, "at": _t(10, 30), "online": 18000}],
        "played": {"total": 86400, "level": 600, "at": _t(11), "online": 19000, "lvl": 20},
    }
    sec.update(over)
    return sec


def test_render_session_pace_and_time_to_level():
    text = progress.render(_section())
    assert "Now: level 20, 11,200 / 23,200 XP (48%), 12,000 XP to level 21; no rested XP; money 3g 0s 0c." in text
    assert "This session: since 10:00 (2 h 0 min logged in, 1 h 30 min playing; AFK and breaks left out; 1 /reload, same session)" in text
    assert "XP +18,000 (4,000 from quests), 12,000 XP/h" in text
    assert "level 19 -> 20" in text and "money +2g 0s 0c net" in text
    assert "earned 2g 40s 0c (loot 1g 50s 0c, vendor 90s 0c)" in text and "spent 40s 0c (repair 40s 0c)" in text
    assert "kills 80, quests 5, deaths 1" in text and "zones: Redridge Mountains 1 h 6 min, Lakeshire 15 min" in text
    assert "Gold: +2g 0s 0c net, +1g 33s 33c per hour of play." in text
    # 12,000 XP to level 21 at 12,000 XP/h
    assert "Time to level (playing time at this session's pace, 12,000 XP/h): 21: ~1 h 0 min; 22:" in text
    assert "Approximate" in text and "Classic XP table" in text


def test_render_lately_recent_last_session_and_today():
    text = progress.render(_section())
    assert "Lately (the last 45 min of play)" in text
    assert "Earlier sessions of the last 7 days: 2, 3 h 20 min playing, XP +36,000 (10,800 XP/h)" in text
    assert "Last session: 2026-11-10 08:00–09:00 (1 h 0 min logged in, 50 min playing): XP +6,000" in text
    # today: the morning session and the current one
    assert "Today (2026-11-10): 2 sessions since 08:00; 2 h 20 min playing; XP +24,000; levels +1" in text
    assert "earned 2g 50s 0c (loot 1g 60s 0c, vendor 90s 0c)" in text and "kills 110, quests 6, deaths 1" in text


def test_render_rested_and_played():
    text = progress.render(_section(rested=6000))
    assert "rested bonus 6,000 XP" in text
    assert "Rested XP doubles the XP from kills until the 6,000 bonus is used up" in text and "~30 min sooner" in text
    assert "Played: 1 d 0 h in total, 26 min at level 20 (from /played on 2026-11-10 11:00, plus 16 min logged in since)." in text
    assert "Time per level (logged in, counted by ForeverAI): 19 -> 20 2 h 13 min." in text
    # a /played reading from an earlier level gives no time at this level
    text = progress.render(_section(played={"total": 86400, "level": 600, "at": _t(9), "online": 19000, "lvl": 19}))
    assert "Played: 1 d 0 h in total (from /played" in text and "at level 20" not in text
    text = progress.render(_section(played=None))
    assert "Played: not known (the player has not typed /played" in text and "5 h 33 min counted by ForeverAI" in text


def test_render_uses_recent_pace_for_a_short_session_and_scales_the_table():
    short = _section(xpMax=46400, xp=0)
    short["session"] = {**short["session"], "act": 300, "gain": 1000}
    short["samples"] = short["samples"][:2]
    text = progress.render(short)
    assert "at the pace of the last 7 days, 10,800 XP/h" in text
    assert "scaled by 2.00 to this server's XP for level 20" in text
    assert "XP/h" not in text.split("\n")[2]  # 5 minutes say nothing about the pace of this session


def test_render_no_data_max_level_and_bad_input():
    assert progress.render(None).startswith("No progress history yet")
    assert progress.render({}).startswith("No progress history yet")
    text = progress.render({"now": _t(12), "level": 60, "xp": 0, "xpMax": 0, "maxLevel": 60, "money": 5,
                            "session": {"start": _t(11), "act": 120, "gain": 0, "lvl0": 60, "lvl": 60}})
    assert "level 60 (maximum level)" in text and "Time to level" not in text
    text = progress.render({"now": _t(12), "level": 5, "xp": 10, "xpMax": 2800, "session": {"start": _t(12), "act": 0}})
    assert "not enough playing time recorded yet" in text
    # rubbish in the samples is skipped
    sec = _section(samples=[["x"], None, _row(_t(10), 0, 20, 0), "bad"])
    assert "Now: level 20" in progress.render(sec)


def test_snapshot_renders_progress_and_prompt_describes_it():
    text = render_snapshot({"player": {"level": 20}, "progress": _section()})
    assert "## progress\nNow: level 20" in text
    assert text.index("## player") < text.index("## progress")
    assert "progress:" in SYSTEM_PROMPT and "approximate" in SYSTEM_PROMPT.lower()
