"""Professions: the add-on's Professions.lua against a mocked game (a Classic-style trade skill API with collapsed
headers and filters, the newer C_TradeSkillUI, no API at all, a large profession spread over frames, another player's
profession, the window closing mid-read, a trainer) and professions.py's text."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from foreverai import professions
from foreverai.prompt import SYSTEM_PROMPT, render_snapshot

ADDON = Path(__file__).resolve().parents[2] / "addon" / "ForeverAI"

LUA_HARNESS = r"""
local addonDir = arg[1]
local now, gt, clock = 1800000000, 100, 0
function time() return now end
function GetTime() return gt end
function debugprofilestop() return clock end
local function cost() clock = clock + 0.01 end -- every game API call takes a little time
local timers = {}
C_Timer = { After = function(s, fn) timers[#timers + 1] = { due = gt + s, fn = fn } end }
local frames = {}
function CreateFrame()
  local f = { events = {}, scripts = {}, shown = true }
  function f:RegisterEvent(e) self.events[e] = true end
  function f:SetScript(k, fn) self.scripts[k] = fn end
  function f:Show() self.shown = true end
  function f:Hide() self.shown = false end
  frames[#frames + 1] = f
  return f
end
local fired = {}
local function fire(event, ...)
  fired[event] = (fired[event] or 0) + 1
  for _, f in ipairs(frames) do
    if f.events[event] and f.scripts.OnEvent then f.scripts.OnEvent(f, event, ...) end
  end
end
local queued = {} -- events the game sends a moment after an API call
local frameMs, maxFrameMs, frameCount = 0, 0, 0
local function frame()
  gt = gt + 0.02
  local q = queued queued = {}
  for _, e in ipairs(q) do fire(e) end
  local due = {}
  for i = #timers, 1, -1 do if timers[i].due <= gt then table.insert(due, 1, table.remove(timers, i)) end end
  for _, t in ipairs(due) do t.fn() end
  for _, f in ipairs(frames) do
    if f.shown and f.scripts.OnUpdate then
      local c0 = clock
      f.scripts.OnUpdate(f, 0.02)
      local ms = clock - c0
      if ms > 0 then frameCount = frameCount + 1 maxFrameMs = math.max(maxFrameMs, ms) end
    end
  end
end
local function frames_(n) for _ = 1, n do frame() end end
local function out(k, v) print(k .. " " .. tostring(v)) end
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

-- the player's bags: item id -> count (and the bank on top)
local bags, bank = { [2589] = 7, [2320] = 0, [2604] = 1 }, { [2589] = 2 }
C_Item = { GetItemCount = function(id, withBank) cost() return (bags[id] or 0) + (withBank and (bank[id] or 0) or 0) end,
  GetItemNameByID = function(id) return ({ [2589] = "Linen Cloth", [2320] = "Coarse Thread", [2604] = "Red Dye",
    [2592] = "Wool Cloth" })[id] or ("Item " .. id) end }
C_Item.GetItemInfo = C_Item.GetItemNameByID

-- a Classic-style profession: headers (one collapsed by the player) with recipes
local function recipe(name, diff, item, reagents, avail, ups) return { name = name, diff = diff, item = item, reagents = reagents, avail = avail or 0, ups = ups } end
local LINEN = { { "Linen Cloth", 2589, 3 }, { "Coarse Thread", 2320, 1 } }
local tree
local function classicTree(extra)
  tree = {
    { header = "Armor", expanded = true, kids = {
      recipe("Brown Linen Vest", "medium", 2568, LINEN, 0),
      recipe("Linen Boots", "optimal", 2569, { { "Linen Cloth", 2589, 2 }, { "Red Dye", 2604, 1 } }, 1, 2),
      recipe("Linen Cloak", "trivial", 2570, LINEN, 2) } },
    { header = "Cloth", expanded = false, kids = { -- collapsed by the player
      recipe("Bolt of Linen Cloth", "trivial", 2996, { { "Linen Cloth", 2589, 2 } }, 3),
      recipe("Bolt of Woolen Cloth", "easy", 2997, { { "Wool Cloth", 2592, 3 } }, 0) } },
    { header = "Bags", expanded = true, kids = { recipe("Linen Bag", "optimal", 4238, LINEN, 0) } },
  }
  for _, h in ipairs(extra or {}) do tree[#tree + 1] = h end
end
local hidden = {} -- headers a filter hides
local function visible()
  local v = {}
  for _, h in ipairs(tree) do
    if not hidden[h.header] then
      v[#v + 1] = h
      if h.expanded then for _, r in ipairs(h.kids) do v[#v + 1] = r end end
    end
  end
  return v
end
local calls = {}
local function classicApi()
  C_TradeSkillUI = nil
  GetTradeSkillLine = function() cost() return "Tailoring", 51, 75 end
  GetNumTradeSkills = function() cost() return #visible() end
  GetTradeSkillInfo = function(i)
    cost()
    local e = visible()[i]
    if not e then return nil end
    if e.header then return e.header, "header", nil, e.expanded end
    return e.name, e.diff, e.avail, nil, nil, e.ups
  end
  GetTradeSkillItemLink = function(i) cost() local e = visible()[i] return e and e.item and ("|Hitem:" .. e.item .. "::|h[" .. e.name .. "]|h") end
  GetTradeSkillNumReagents = function(i) cost() local e = visible()[i] return e and e.reagents and #e.reagents or 0 end
  GetTradeSkillReagentInfo = function(i, k) cost() local g = visible()[i].reagents[k] return g[1], "icon", g[3], bags[g[2]] or 0 end
  GetTradeSkillReagentItemLink = function(i, k) cost() local g = visible()[i].reagents[k] return "|Hitem:" .. g[2] .. "::|h[" .. g[1] .. "]|h" end
  GetTradeSkillNumMade = function(i) cost() local e = visible()[i] return 1, e.name == "Bolt of Woolen Cloth" and 2 or 1 end
  ExpandTradeSkillSubClass = function(i)
    calls[#calls + 1] = "expand " .. i
    local e = visible()[i]
    if e and e.header then e.expanded = true queued[#queued + 1] = "TRADE_SKILL_UPDATE" end
  end
  CollapseTradeSkillSubClass = function(i)
    calls[#calls + 1] = "collapse " .. i
    local e = visible()[i]
    if e and e.header then e.expanded = false queued[#queued + 1] = "TRADE_SKILL_UPDATE" end
  end
  GetTradeSkillSubClassFilter = function() return next(hidden) == nil end
  SetTradeSkillSubClassFilter = function() calls[#calls + 1] = "SET FILTER" end
  TradeSkillOnlyShowMakeable = function() calls[#calls + 1] = "SET FILTER" end
  IsTradeSkillLinked = function() return false end
end

local ns = { char = {} }
ns.clean = function(v) return v end
ns.safe = function(fn, ...)
  if type(fn) ~= "function" then return nil end
  local r = { pcall(fn, ...) }
  if not r[1] then return nil end
  return unpack(r, 2)
end
local printed = {}
ns.print = function(...) local t = {} for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end printed[#printed + 1] = table.concat(t, " ") end
ns.Collect = { sections = {}, Budget = function() end }
assert(loadfile(addonDir .. "/Professions.lua"))("ForeverAI", ns)
local PR = ns.Professions
local function db() return ns.char.professions or {} end
local function names(list) local t = {} for _, r in ipairs(list or {}) do t[#t + 1] = type(r) == "table" and r.n or r end table.sort(t) return table.concat(t, ",") end
local function headers() local t = {} for _, h in ipairs(tree) do t[#t + 1] = h.header .. "=" .. tostring(h.expanded) end return table.concat(t, ",") end

-- 0) no profession API at all: nothing happens, nothing breaks
GetNumTradeSkills, GetTradeSkillInfo, C_TradeSkillUI = nil, nil, nil
fire("TRADE_SKILL_SHOW")
frames_(60)
out("NOAPI", tostring(next(db()) == nil) .. "," .. tostring(ns.lastError))
PR.Debug()
out("NOAPI_DEBUG", printed[1] .. " || " .. printed[2])

-- 1) Classic API: a burst of updates is one reading; the collapsed header is read and closed again
classicApi()
classicTree()
fire("TRADE_SKILL_SHOW")
for _ = 1, 20 do fire("TRADE_SKILL_UPDATE") end
frames_(80)
local t = db().Tailoring
out("CLASSIC", t.rank .. "/" .. t.maxRank .. "|" .. t.api .. "|" .. names(t.recipes) .. "|" .. names(t.gray))
out("HEADERS", headers())
out("CALLS", table.concat(calls, ","))
local vest, boots, woolen
for _, r in ipairs(t.recipes) do
  if r.n == "Brown Linen Vest" then vest = r elseif r.n == "Linen Boots" then boots = r elseif r.n == "Bolt of Woolen Cloth" then woolen = r end
end
out("VEST", vest.d .. "," .. vest.i .. "," .. vest.r[1].n .. "x" .. vest.r[1].q .. "#" .. vest.r[1].i .. "," .. vest.r[2].n .. "x" .. vest.r[2].q)
out("BOOTS", boots.d .. "," .. boots.u .. "," .. tostring(boots.c))
out("WOOLEN", woolen.d .. "," .. woolen.m[1] .. "-" .. woolen.m[2])
out("READS", fired.TRADE_SKILL_UPDATE .. "," .. PR.lastRead.expanded)
-- our own expand/collapse events did not start another reading
local at = t.at
now = now + 5
frames_(60)
out("NOREREAD", tostring(db().Tailoring.at == at))

-- 2) the snapshot counts reagents in the bags now
bags[2320] = 4
local sec = ns.Collect.sections.professions()
local sv
for _, r in ipairs(sec.Tailoring.recipes) do if r.n == "Brown Linen Vest" then sv = r end end
out("NOWCOUNT", sv.c .. "," .. sv.r[1].h .. "," .. tostring(sv.r[1].b) .. "," .. sv.r[2].h)
out("ITEMID", tostring(PR.ItemId("coarse thread")) .. "," .. tostring(PR.ItemId("Linen Bag")) .. "," .. tostring(PR.ItemId("Nope")))
print("SECTION " .. json(sec))

-- 3) a filter hides a category: the filter stays, what was read updates the cache, the rest stays from before
hidden.Bags = true
tree[1].kids[1].diff = "easy" -- the vest turned green meanwhile
calls = {}
fire("TRADE_SKILL_UPDATE")
frames_(80)
t = db().Tailoring
local vd
for _, r in ipairs(t.recipes) do if r.n == "Brown Linen Vest" then vd = r.d end end
out("FILTERED", tostring(t.partial) .. "|" .. names(t.recipes) .. "|" .. vd)
out("FILTERCALLS", table.concat(calls, ","))
hidden = {}

-- 4) a large profession: hundreds of recipes, read over many frames, never more than the budget per frame
local big = { header = "Many", expanded = true, kids = {} }
for i = 1, 300 do big.kids[i] = recipe("Big Recipe " .. i, i % 4 == 0 and "trivial" or "medium", 10000 + i, LINEN, 0) end
local closed = { header = "More", expanded = false, kids = {} }
for i = 1, 40 do closed.kids[i] = recipe("Hidden Recipe " .. i, "easy", 20000 + i, LINEN, 0) end
classicTree({ big, closed })
maxFrameMs, frameCount = 0, 0
fire("TRADE_SKILL_UPDATE")
frames_(400)
t = db().Tailoring
out("BIG", #t.recipes .. "," .. #t.gray .. "," .. frameCount .. "," .. string.format("%.2f", maxFrameMs) .. "," .. headers())

-- 5) the window closes in the middle of a reading: the old cache stays
local before = db().Tailoring.at
now = now + 100
fire("TRADE_SKILL_UPDATE")
frames_(28) -- the debounce, then a few frames of reading
fire("TRADE_SKILL_CLOSE")
frames_(100)
out("CLOSED", tostring(db().Tailoring.at == before))

-- 6) another player's profession (a link in chat) is never stored as the player's own
IsTradeSkillLinked = function() return true end
GetTradeSkillLine = function() return "Blacksmithing", 300, 300 end
fire("TRADE_SKILL_SHOW")
frames_(80)
out("LINKED", tostring(db().Blacksmithing == nil))
IsTradeSkillLinked = function() return false end

-- 7) the newer API (C_TradeSkillUI): every recipe by id whatever the filters show, unlearned ones only counted
GetNumTradeSkills, GetTradeSkillInfo, GetTradeSkillLine = nil, nil, nil
local recipes = {}
for i = 1, 450 do
  recipes[i] = { recipeID = 3000 + i, name = "Recipe " .. i, learned = i <= 300, relativeDifficulty = i % 4,
                 numAvailable = i % 3, numSkillUps = i == 2 and 3 or 1 }
end
C_TradeSkillUI = {
  IsTradeSkillReady = function() return true end,
  IsTradeSkillLinked = function() return false end,
  GetBaseProfessionInfo = function() return { professionName = "Blacksmithing", skillLevel = 1, maxSkillLevel = 1 } end,
  GetChildProfessionInfo = function() return { professionName = "Blacksmithing", parentProfessionName = "Blacksmithing", skillLevel = 132, maxSkillLevel = 150 } end,
  GetAllRecipeIDs = function() cost() local ids = {} for i, r in ipairs(recipes) do ids[i] = r.recipeID end return ids end,
  GetFilteredRecipeIDs = function() return { 3001 } end,
  GetRecipeInfo = function(id) cost() return recipes[id - 3000] end,
  GetRecipeOutputItemData = function(id) cost() return { itemID = 7000 + (id - 3000) } end,
  GetRecipeSchematic = function(id) cost() return { outputItemID = 7000 + (id - 3000), quantityMin = 1, quantityMax = 1,
    reagentSlotSchematics = { { reagents = { { itemID = 2589 } }, quantityRequired = 4, reagentType = 1, required = true },
                              { reagents = { { itemID = 9999 } }, quantityRequired = 1, reagentType = 0, required = false } } } end,
}
Enum = { CraftingReagentType = { Basic = 1, Optional = 0 } }
maxFrameMs, frameCount = 0, 0
fire("TRADE_SKILL_SHOW")
fire("TRADE_SKILL_LIST_UPDATE")
frames_(400)
local b = db().Blacksmithing
local r2
for _, r in ipairs(b.recipes) do if r.n == "Recipe 2" then r2 = r end end
out("RETAIL", b.rank .. "/" .. b.maxRank .. "," .. b.api .. "," .. #b.recipes .. "," .. #b.gray .. "," .. b.unlearned .. "," ..
  frameCount .. "," .. string.format("%.2f", maxFrameMs))
out("RETAILR", r2.d .. "," .. r2.i .. "," .. r2.u .. "," .. #r2.r .. "," .. r2.r[1].n .. "x" .. r2.r[1].q)

-- 8) a profession trainer: offers for the player's professions, without what is already learned
GetNumTrainerServices = function() return 4 end
local services = { { "Simple Kilt", "", "available", 500, "Tailoring", 75 }, { "Linen Bag", "", "used", 0, "Tailoring", 45 },
  { "Fireball", "Rank 2", "available", 100, nil, nil }, { "Red Linen Robe", "", "unavailable", 1000, "Tailoring", 90 } }
GetTrainerServiceInfo = function(i) local s = services[i] return s[1], s[2], s[3] end
GetTrainerServiceCost = function(i) return services[i][4] end
GetTrainerServiceSkillReq = function(i) return services[i][5], services[i][6], true end
GetTrainerServiceLevelReq = function() return 10 end
UnitName = function() return "Lisbeth" end
fire("TRAINER_SHOW")
frames_(30)
local tr = db().Tailoring.trainer
local offers = {}
for _, o in ipairs(tr.offers) do offers[#offers + 1] = o.n .. ":" .. o.cost .. ":" .. o.skill .. ":" .. tostring(o.ok) end
out("TRAINER", tr.npc .. "|" .. table.concat(offers, ","))

-- 9) /fai professions
printed = {}
PR.Debug()
out("DEBUG", #printed .. "##" .. table.concat(printed, "##"))
out("ERROR", tostring(ns.lastError))
"""


@pytest.fixture(scope="module")
def lua_run(tmp_path_factory):
    if not shutil.which("lua5.1"):
        pytest.skip("needs lua5.1")
    script = tmp_path_factory.mktemp("professions") / "harness.lua"
    script.write_text(LUA_HARNESS)
    run = subprocess.run(["lua5.1", str(script), str(ADDON)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    return dict(line.split(" ", 1) for line in run.stdout.strip().splitlines())


def test_professions_lua_without_api(lua_run):
    assert lua_run["NOAPI"] == "true,nil"
    api, empty = lua_run["NOAPI_DEBUG"].split(" || ")
    assert "C_TradeSkillUI no, GetTradeSkillInfo no" in api and "open a profession window once" in empty


def test_professions_lua_classic_api_with_collapsed_header(lua_run):
    rank, api, recipes, gray = lua_run["CLASSIC"].split("|")
    assert rank == "51/75" and api == "GetTradeSkillInfo"
    # the collapsed "Cloth" header was read too; gray recipes only by name
    assert recipes == "Bolt of Woolen Cloth,Brown Linen Vest,Linen Bag,Linen Boots"
    assert gray == "Bolt of Linen Cloth,Linen Cloak"
    assert lua_run["HEADERS"] == "Armor=true,Cloth=false,Bags=true"  # the player's collapsed header is closed again
    assert lua_run["CALLS"] == "expand 5,collapse 5"
    assert lua_run["VEST"] == "medium,2568,Linen Clothx3#2589,Coarse Threadx1"
    assert lua_run["BOOTS"] == "optimal,2,1" and lua_run["WOOLEN"] == "easy,1-2"
    updates, expanded = lua_run["READS"].split(",")
    assert updates == "22" and expanded == "1"  # 20 from the game, 2 from our own expand/collapse
    assert lua_run["NOREREAD"] == "true"


def test_professions_lua_counts_reagents_now(lua_run):
    can, linen, linen_bank, thread = lua_run["NOWCOUNT"].split(",")
    # 7 Linen Cloth / 3 = 2, and the thread bought after the reading counts: can make 2
    assert (can, linen, linen_bank, thread) == ("2", "7", "2", "4")
    assert lua_run["ITEMID"] == "2320,4238,nil"


def test_professions_lua_filters_stay_and_cache_merges(lua_run):
    partial, recipes, vest = lua_run["FILTERED"].split("|")
    assert partial == "a category filter"
    assert recipes == "Bolt of Woolen Cloth,Brown Linen Vest,Linen Bag,Linen Boots"  # the hidden Linen Bag is kept
    assert vest == "easy"  # what was visible is fresh
    assert "SET FILTER" not in lua_run["FILTERCALLS"]


def test_professions_lua_large_profession_spread_over_frames(lua_run):
    recipes, gray, frames, max_ms, headers = lua_run["BIG"].split(",", 4)
    assert int(recipes) == 4 + 225 + 40 and int(gray) == 2 + 75
    assert int(frames) >= 5 and float(max_ms) <= 2.5  # the budget is 2 ms; a collapsed header is read in one frame
    assert headers == "Armor=true,Cloth=false,Bags=true,Many=true,More=false"


def test_professions_lua_window_closed_and_linked(lua_run):
    assert lua_run["CLOSED"] == "true"
    assert lua_run["LINKED"] == "true"


def test_professions_lua_retail_api(lua_run):
    rank, api, recipes, gray, unlearned, frames, max_ms = lua_run["RETAIL"].split(",")
    assert rank == "132/150" and api == "C_TradeSkillUI"
    assert int(recipes) == 225 and int(gray) == 75 and unlearned == "150"
    assert int(frames) >= 3 and float(max_ms) <= 2.5
    # only the required basic reagent; the output item; 3 skill-ups
    assert lua_run["RETAILR"] == "easy,7002,3,1,Linen Clothx4"


def test_professions_lua_trainer(lua_run):
    npc, offers = lua_run["TRAINER"].split("|")
    assert npc == "Lisbeth"
    assert offers.split(",") == ["Simple Kilt:500:75:true", "Red Linen Robe:1000:90:false"]


def test_professions_lua_debug(lua_run):
    count, *lines = lua_run["DEBUG"].split("##")
    assert lines[0].startswith("profession API: C_TradeSkillUI yes")
    text = "\n".join(lines)
    assert "Tailoring 51/75:" in text and "trainer 2 offers" in text and "Blacksmithing 132/150: 225 recipes" in text
    assert "last read:" in text
    assert lua_run["ERROR"] == "nil"


def test_professions_section_from_lua_renders(lua_run):
    sec = json.loads(lua_run["SECTION"])
    text = professions.render(sec)
    assert text.startswith("Tailoring 51/75 (recipes read from its window just now")
    assert "- [Linen Boots] (item 2569) — orange (2 skill-ups per craft), can make 1 now" in text
    assert ("- [Brown Linen Vest] (item 2568) — yellow, can make 2 now: 3 [Linen Cloth] (item 2589; have 7, +2 in bank), "
            "1 [Coarse Thread] (item 2320; have 4)") in text
    assert "Gray, no skill-ups (2): Bolt of Linen Cloth, Linen Cloak" in text
    lines = text.splitlines()
    assert lines.index(next(x for x in lines if "Linen Bag" in x)) < lines.index(next(x for x in lines if "Brown Linen Vest" in x))


# ------------------------------------------------------------------------ professions.py
def test_render_professions_order_trainer_and_notes():
    sec = {"Tailoring": {"rank": 51, "maxRank": 75, "readAgo": 7200, "partial": "a category filter", "unlearned": 12,
                         "recipes": [{"n": "Green", "d": "easy", "c": 0}, {"n": "Yellow", "d": "medium", "c": 3, "m": [1, 2]},
                                     {"n": "Orange", "d": "optimal", "i": 5, "c": 1}],
                         "trainer": {"npc": "Lisbeth", "readAgo": 90000,
                                     "offers": [{"n": "Simple Kilt", "cost": 500, "skill": 75, "lvl": 10, "ok": False}]}},
           "Cooking": {"rank": 92, "maxRank": 150, "readAgo": 30, "gray": ["Charred Wolf Meat"]}}
    text = professions.render(sec)
    lines = text.splitlines()
    assert lines[0].startswith("Cooking 92/150 (recipes read from its window just now")
    assert lines[1] == "- no recipe that still gives skill-ups"
    assert lines[2] == "Gray, no skill-ups (1): Charred Wolf Meat"
    assert "Tailoring 51/75 (recipes read from its window 2 h 0 min ago" in lines[3]
    assert [x.split(" — ")[0] for x in lines[4:7]] == ["- [Orange] (item 5)", "- [Yellow]", "- [Green]"]
    assert "yellow, makes 1-2, can make 3 now" in lines[5]
    assert "12 recipes of Tailoring are not learned yet" in text and "hidden by a category filter" in text
    assert "Trainer (Lisbeth), seen 1 d 1 h ago, offers: [Simple Kilt] — 5s 0c, needs Tailoring 75, level 10, not yet" in text


def test_render_professions_no_data():
    assert professions.render(None).startswith("No profession read yet")
    assert professions.render({"x": "bad"}).startswith("No profession read yet")


def test_snapshot_renders_professions_and_prompt_describes_them():
    text = render_snapshot({"skills": ["Tailoring 51/75"], "professions": {"Tailoring": {"rank": 51, "maxRank": 75,
                                                                                         "readAgo": 0, "recipes": []}}})
    assert "## professions\nTailoring 51/75" in text and text.index("## skills") < text.index("## professions")
    low = SYSTEM_PROMPT.lower()
    assert "professions:" in low and "orange" in low and "gray" in low and "search_auction_house once with items" in low
