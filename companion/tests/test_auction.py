import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from foreverai.tools import auction_request, format_auction

from test_service import _read_inbox, _send, env  # noqa: F401  (fixture)

ADDON = Path(__file__).resolve().parents[2] / "addon" / "ForeverAI"


def test_auction_request_maps_filters():
    req, err = auction_request({"weapon_type": "1h_sword", "slot": "one-hand", "max_price_gold": 100,
                                "upgrades_only": True})
    assert err is None
    assert req["classIDs"] == [2] and req["subclassIDs"] == [7]
    assert req["equipLocs"] == ["INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND"]
    assert req["maxCopper"] == 1_000_000 and req["upgradesOnly"] and req["usableOnly"]
    assert req["sort"] == "upgrade" and req["limit"] == 15 and req["maxAgeDays"] == 7
    req, _ = auction_request({"query": "Linen Cloth", "category": "trade_goods", "limit": 99})
    assert req["classIDs"] == [7] and req["sort"] == "price" and req["limit"] == 30
    for bad in ({}, {"weapon_type": "laser"}, {"weapon_type": "dagger", "armor_type": "cloth"},
                {"query": "x", "max_price_gold": "lots"}, {"armor_type": "plate", "category": "weapon"}):
        req, err = auction_request(bad)
        assert req is None and err.startswith("Rejected")


def test_format_auction():
    text = format_auction({"source": "Auctionator", "realm": "Forever-EU", "lastScan": "2 h ago (full scan)",
                           "dbItems": 1200, "matched": 3, "sort": "upgrade", "upgradeMethod": "Pawn (the player's scales)",
                           "skipped": {"not usable": 2, "too high level": 0}, "notLoaded": 4, "ahOpen": True,
                           "items": [{"id": 1, "name": "Gleaming Sword", "price": "45g 0s 0c", "seen": "today",
                                      "available": 2, "quality": "uncommon", "ilvl": 25, "requiresLevel": 20,
                                      "slot": "One-Hand", "type": "One-Handed Swords", "stats": "DPS +12.5",
                                      "vsEquipped": "instead of [Rusty Sword]: DPS +4", "pawn": "+8.0% upgrade"}]})
    assert "last scan: 2 h ago (full scan)" in text and "1200 items" in text and "Left out: 2 not usable." in text
    assert "- [Gleaming Sword] (item 1) — 45g 0s 0c | seen today, 2 available" in text
    assert "Pawn: +8.0% upgrade" in text and "4 matching items" in text and "window is open" in text
    assert "failed: Auctionator is not installed" in format_auction({"error": "Auctionator is not installed"})
    assert "did not respond" in format_auction(None)


def test_search_tool_round_trip_and_card(env):  # noqa: F811
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    writer = InboxWriter(addons, "S", 1, 20, 4)
    writer.send({"kind": "answer", "final": False, "msgId": 3, "text": "", "phase": "x"})
    ctx = TurnContext({"msgId": 3}, writer, {}, threading.Event(), [])
    out = {}
    t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "search_auction_house", {
        "weapon_type": "1h_sword", "max_price_gold": 100})))
    t.start()
    deadline, req = time.time() + 5, None
    while time.time() < deadline and not req:
        req = (_read_inbox(addons, 1).get("requests") or [None])[0]
        time.sleep(0.02)
    assert req["op"] == "auction_search" and req["subclassIDs"] == [7] and req["maxCopper"] == 1_000_000
    _send(svc, {"type": "game_result", "id": req["id"], "result": {
        "source": "Auctionator", "dbItems": 50, "matched": 1, "ahOpen": False,
        "items": [{"id": 2042, "name": "Gleaming Sword", "price": "45g 0s 0c", "seen": "today"}]}})
    t.join(5)
    text, err = out["r"]
    assert not err and "[Gleaming Sword] (item 2042)" in text
    answer = "Buy **[Gleaming Sword]** for 45g."
    assert ctx.auction_card(answer) == [{"id": 2042, "name": "Gleaming Sword", "price": "45g 0s 0c"}]
    assert ctx.auction_card("Nothing worth it.") is None
    assert {"name": "Gleaming Sword", "kind": "item", "id": 2042} in svc.links(answer, ctx)


LUA_HARNESS = r"""
local addonDir = arg[1]
local now = 1000000
function time(t) if t then return 1577836800 end return now end -- day 0 = 2020-01-01
local clock = 0
function debugprofilestop() clock = clock + 0.01 return clock end
local frameT = 0
function GetTime() return frameT end
local frames = {}
function CreateFrame()
  local f = { scripts = {}, shown = true }
  function f:SetScript(k, fn) self.scripts[k] = fn end
  function f:Hide() self.shown = false end
  function f:Show() self.shown = true end
  function f:RegisterEvent() end
  frames[#frames + 1] = f
  return f
end
local today = math.floor((now - 1577836800) / 86400)
local items = {
  [100] = { "Gleaming Sword", 2, 7, "INVTYPE_WEAPON", 25, 20 },
  [101] = { "Huge Sword", 2, 7, "INVTYPE_WEAPON", 40, 45 },      -- too high level
  [102] = { "Cheap Sword", 2, 7, "INVTYPE_WEAPONMAINHAND", 10, 5 },
  [103] = { "Battle Axe", 2, 1, "INVTYPE_2HWEAPON", 25, 20 },    -- wrong type
  [104] = { "Pricey Sword", 2, 7, "INVTYPE_WEAPON", 30, 25 },    -- over budget
  [105] = { "Old Sword", 2, 7, "INVTYPE_WEAPON", 22, 18 },       -- price too old
  [106] = { "Slow Sword", 2, 7, "INVTYPE_WEAPON", 21, 18 },      -- not cached yet, arrives later
}
local cached = { [100] = true, [101] = true, [102] = true, [103] = true, [104] = true, [105] = true }
C_Item = {
  GetItemInfoInstant = function(id) local i = items[id] return id, "Weapon", "Swords", i[4], 0, i[2], i[3] end,
  GetItemInfo = function(id)
    local i = items[id]
    if not cached[id] then return nil end
    return i[1], "|cff1eff00|Hitem:" .. id .. "|h[" .. i[1] .. "]|h|r", 2, i[5], i[6], "Weapon", "One-Handed Swords"
  end,
  IsItemDataCachedByID = function(id) return cached[id] == true end,
  RequestLoadItemDataByID = function(id) cached[id] = "soon" end,
}
GetInventoryItemLink = function() return nil end
local ns = { safe = function(fn, ...) return fn(...) end, stripCodes = function(s) return s end,
  On = function() end }
ns.Collect = { ItemFacts = function(link, compare)
  local id = tonumber(link:match("item:(%d+)"))
  local i = items[id]
  return { quality = "uncommon", stats = "DPS +" .. i[5], tooLowLevel = i[6] > 30 or nil,
           vsEquipped = "instead of (empty slot): DPS +" .. i[5] }
end }
Auctionator = { API = { v1 = { MultiSearchExact = function() end } }, State = { CurrentRealm = "Forever" } }
local function day(d) return { [tostring(today - d)] = 1 } end
AUCTIONATOR_PRICE_DATABASE = { __dbversion = 6, Forever = {
  ["100"] = { m = 450000, h = day(0), a = { [tostring(today)] = 3 } },
  ["101"] = { m = 600000, h = day(1) }, ["102"] = { m = 10000, h = day(2) }, ["103"] = { m = 200000, h = day(0) },
  ["104"] = { m = 2000000, h = day(0) }, ["105"] = { m = 300000, h = day(20) }, ["106"] = { m = 350000, h = day(0) },
  ["g:100:30"] = { m = 900000, h = day(0) }, ["p:55"] = { m = 1, h = day(0) } } }
AUCTIONATOR_SAVEDVARS = { TimeOfLastReplicateScan = now - 7200 }
local chunk = assert(loadfile(addonDir .. "/Auction.lua"))
chunk("ForeverAI", ns)
local result
ns.Auction.Search({ classIDs = { 2 }, subclassIDs = { 7 }, equipLocs = { "INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND" },
  maxCopper = 1000000, maxAgeDays = 7, usableOnly = true, upgradesOnly = false, sort = "upgrade", limit = 15 },
  function(r) result = r end)
for _ = 1, 200 do
  if result then break end
  frameT = frameT + 0.05
  for _, f in ipairs(frames) do
    if f.shown and f.scripts.OnUpdate then f.scripts.OnUpdate(f, 0.016) end
  end
  if cached[106] == "soon" and frameT > 1 then cached[106] = true end -- the server answers after a second
end
assert(result, "no result")
local names = {}
for _, it in ipairs(result.items) do names[#names + 1] = it.name .. "=" .. it.price .. "@" .. it.seen end
print("ITEMS " .. table.concat(names, ","))
print("META " .. tostring(result.source) .. "|" .. tostring(result.dbItems) .. "|" .. tostring(result.matched) .. "|"
  .. tostring(result.lastScan) .. "|" .. tostring(result.skipped["too high level"]) .. "|" .. tostring(result.notLoaded))
local ok, why = ns.Auction.ShowInAuctionator({ "Gleaming Sword" })
print("SHOW " .. tostring(ok) .. "|" .. tostring(why))
"""


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="needs lua5.1")
def test_auction_lua_reads_auctionator_prices(tmp_path):
    """Auction.lua against a mocked game: filters, item data loading, sorting and the Auctionator search."""
    script = tmp_path / "harness.lua"
    script.write_text(LUA_HARNESS)
    run = subprocess.run(["lua5.1", str(script), str(ADDON)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    lines = dict(line.split(" ", 1) for line in run.stdout.strip().splitlines())
    items = lines["ITEMS"].split(",")
    # in budget, right type, recent, usable; the late-loading item is included; the empty slot makes all upgrades
    assert sorted(i.split("=")[0] for i in items) == ["Cheap Sword", "Gleaming Sword", "Slow Sword"]
    assert "Gleaming Sword=45g 0s 0c@today" in items and "Cheap Sword=1g 0s 0c@2 days ago" in items
    source, db_items, matched, last_scan, too_high, not_loaded = lines["META"].split("|")
    assert source == "Auctionator" and db_items == "9" and matched == "3" and last_scan == "2 h ago (full scan)"
    assert too_high == "1" and not_loaded == "nil"
    assert lines["SHOW"] == "false|Open the auction house first."
    json.dumps(lines)
