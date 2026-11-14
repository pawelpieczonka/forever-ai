-- ForeverAI: the gear to go for next, read from the GearQuest Forever add-on when the player has it. It ranks, for the
-- character's class, spec, level and faction, the best items of each slot and knows where each comes from (a quest, a
-- vendor, a creature, a profession) with coordinates. Asked for by the AI (its gear_upgrades tool); read-only.
local ADDON, ns = ...
local G = {}
ns.Gear = G

local BUDGET_MS = 250 -- building the lists of slots GearQuest has not ranked yet may take this long in one frame
local PICKS = 3

local function call(obj, name, ...)
  local fn = obj and obj[name]
  if type(fn) ~= "function" then return nil end
  local res = { pcall(fn, obj, ...) }
  if not res[1] then return nil end
  return unpack(res, 2)
end

local function text(v)
  if type(v) ~= "string" or v == "" then return nil end
  return ns.Cut(ns.stripCodes(v), 220)
end

local function slotOf(wanted, name)
  if not wanted or wanted == "" then return true end
  local a, b = wanted:lower():gsub("[%s%-_]", ""), name:lower()
  return a == b or b:find(a, 1, true) ~= nil or a:find(b, 1, true) ~= nil
    or (a == "offhand" and b == "secondaryhand") or (a == "weapon" and b == "mainhand")
    or (a == "ring" and b == "finger") or (a == "cloak" and b == "back") or (a == "belt" and b == "waist")
    or (a == "boots" and b == "feet") or (a == "gloves" and b == "hands") or (a == "bracers" and b == "wrist")
end

-- What is worn in a slot now: "[Name]", or two of them for rings and trinkets.
local function worn(D, slotName)
  local out = {}
  for _, inv in ipairs(call(D, "GetInventorySlots", slotName) or {}) do
    local link = GetInventoryItemLink("player", inv)
    local name = link and link:match("%[(.-)%]")
    if name then out[#out + 1] = "[" .. name .. "]" end
  end
  return #out > 0 and table.concat(out, ", ") or nil
end

local function pick(GQ, D, entry, slotName, rank)
  local spot = call(D, "NearestCoordinateSpot", entry.itemId)
  local where
  if type(spot) == "table" and spot.map and spot.x and spot.y then
    where = string.format("%s %.1f, %.1f", tostring(spot.map), spot.x, spot.y)
    if type(spot.npc) == "string" and spot.npc ~= "" then where = spot.npc .. " — " .. where end
  end
  local delta, compared = call(D, "GearScoreUpgradeDelta", entry, slotName)
  local have = (GQ.Log and call(GQ.Log, "IsEntryObtained", entry.id)) and true or nil
  local how = text(call(D, "VendorBoughtLine", entry)) or text(call(D, "GetEntryInstructions", entry)) or text(entry.instructions)
  return {
    rank = rank, id = entry.itemId, name = text(call(D, "GetEntryDisplayName", entry)) or ("item " .. tostring(entry.itemId)),
    source = text(call(GQ, "GetSourceLabel", entry.sourceType)) or entry.sourceType, how = how,
    zone = text(entry.zone), npc = text(entry.npc), quest = text(entry.questName), profession = text(entry.profession),
    levels = (entry.minLevel and entry.maxLevel) and (entry.minLevel .. "-" .. entry.maxLevel) or nil,
    where = where, have = have, worn = (type(delta) == "number" and delta == 0 and compared ~= false) and true or nil,
    gain = (type(delta) == "number" and compared and delta ~= 0) and ns.round(delta, 1) or nil,
  }
end

-- { level, class, spec, slots = { { slot, worn, picks = { ... } } }, tracked = { names }, waiting = { slots } }
-- ``slot``: one slot ("Head", "ranged", "ring"), or nil for all of them.
function G.Upgrades(slot)
  local GQ = _G.GearQuest
  local D = GQ and GQ.Data
  if not (D and type(D.GetTopUpgradesForSlot) == "function") then
    return { error = "GearQuest Forever is not loaded" }
  end
  local names = call(D, "WarmSlotNames")
  if type(names) ~= "table" or #names == 0 then names = D.BASE_SLOTS or {} end
  local out = { level = call(GQ, "GetEffectiveLevel") or UnitLevel("player"), class = call(GQ, "GetEffectiveClass"),
                spec = text(call(GQ, "GetEffectiveSpec")), slots = {}, waiting = {} }
  local started = debugprofilestop()
  for _, slotName in ipairs(names) do
    if slotOf(slot, slotName) then
      local list = call(D, "PeekTopUpgradesForSlot", slotName)
      if type(list) ~= "table" then
        if debugprofilestop() - started < BUDGET_MS then
          list = call(D, "GetTopUpgradesForSlot", slotName)
        else
          out.waiting[#out.waiting + 1] = slotName
        end
      end
      if type(list) == "table" then
        local row = { slot = slotName, worn = worn(D, slotName), picks = {} }
        for i = 1, math.min(#list, PICKS) do
          if type(list[i]) == "table" and list[i].itemId then row.picks[#row.picks + 1] = pick(GQ, D, list[i], slotName, i) end
        end
        out.slots[#out.slots + 1] = row
      end
    end
  end
  if #out.waiting == 0 then out.waiting = nil end
  local tracked = GQ.Tracker and call(GQ.Tracker, "GetTrackedEntries")
  if type(tracked) == "table" and #tracked > 0 then
    out.tracked = {}
    for i = 1, math.min(#tracked, 10) do
      out.tracked[i] = text(call(D, "GetEntryDisplayName", tracked[i])) or tostring(tracked[i].itemId)
    end
  end
  if #out.slots == 0 and not out.waiting then
    out.error = slot and slot ~= "" and ("GearQuest has no slot called " .. tostring(slot)) or "GearQuest lists nothing for this character yet"
  end
  return out
end
