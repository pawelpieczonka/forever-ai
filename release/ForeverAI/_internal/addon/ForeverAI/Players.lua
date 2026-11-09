-- ForeverAI: players nearby (nameplates, mouseover, target, group, chat) and inspection of the target
local ADDON, ns = ...
local P = {}
ns.Players = P
local safe, clean, strip = ns.safe, ns.clean, ns.stripCodes

local seen = {}      -- guid -> info
local inspect = {}   -- guid -> {at, items, ilvl, spec}
local MAX_AGE = 15 * 60
local pendingInspect

local function note(guid, source, unit)
  guid = clean(guid)
  if type(guid) ~= "string" or not guid:find("^Player%-") then return end
  if guid == UnitGUID("player") then return end
  local e = seen[guid]
  if not e then
    local _, class, _, race, sex, name, realm = safe(GetPlayerInfoByGUID, guid)
    if not name then return end
    e = { name = name, realm = (realm and realm ~= "") and realm or nil, class = class, race = race }
    seen[guid] = e
  end
  e.lastSeen = time()
  e.source = source
  if unit and UnitExists(unit) then
    e.level = safe(UnitLevel, unit) or e.level
    e.guild = safe(GetGuildInfo, unit) or e.guild
    e.className = safe(UnitClass, unit) or e.className
    e.raceName = safe(UnitRace, unit) or e.raceName
    local reaction = safe(UnitReaction, unit, "player")
    e.hostile = reaction and reaction <= 3 or nil
    e.pvp = UnitIsPVP(unit) or nil
    local hp, hpMax = safe(UnitHealth, unit), safe(UnitHealthMax, unit)
    if hp and hpMax and hpMax > 0 then e.healthPct = math.floor(hp / hpMax * 100) end
    if UnitExists(unit .. "target") then e.targeting = safe(UnitName, unit .. "target") end
    local castName = UnitCastingInfo and safe(UnitCastingInfo, unit)
    e.casting = castName
  end
end

local function tryInspect(unit)
  if not UnitExists(unit) or not UnitIsPlayer(unit) then return end
  local guid = UnitGUID(unit)
  if not guid or (inspect[guid] and time() - inspect[guid].at < 60) then return end
  if not safe(CanInspect, unit) then return end
  if InspectFrame and InspectFrame:IsShown() then return end
  if pendingInspect and GetTime() - pendingInspect.t < 3 then return end
  pendingInspect = { guid = guid, unit = unit, t = GetTime() }
  pcall(NotifyInspect, unit)
end

function P.Init()
  ns.On("NAME_PLATE_UNIT_ADDED", function(_, unit) note(safe(UnitGUID, unit), "nameplate", unit) end)
  ns.On("UPDATE_MOUSEOVER_UNIT", function() note(safe(UnitGUID, "mouseover"), "mouseover", "mouseover") end)
  ns.On("PLAYER_TARGET_CHANGED", function()
    note(safe(UnitGUID, "target"), "target", "target")
    tryInspect("target")
  end)
  ns.On("GROUP_ROSTER_UPDATE", function()
    local n = GetNumGroupMembers()
    local prefix = IsInRaid() and "raid" or "party"
    for i = 1, n do note(safe(UnitGUID, prefix .. i), "group", prefix .. i) end
  end)
  for _, ev in ipairs({ "CHAT_MSG_SAY", "CHAT_MSG_YELL", "CHAT_MSG_CHANNEL", "CHAT_MSG_WHISPER", "CHAT_MSG_PARTY",
    "CHAT_MSG_GUILD", "CHAT_MSG_EMOTE", "CHAT_MSG_TEXT_EMOTE" }) do
    ns.On(ev, function(...)
      local guid = select(13, ...)
      note(guid, "chat")
    end)
  end
  ns.On("INSPECT_READY", function(_, guid)
    if not pendingInspect or pendingInspect.guid ~= guid then return end
    local unit = pendingInspect.unit
    if UnitGUID(unit) ~= guid then pendingInspect = nil return end
    local items = {}
    for slot = 1, 19 do
      local link = GetInventoryItemLink(unit, slot)
      if link then
        if ns.Collect then ns.Collect.Remember(link) end
        local ilvl = C_Item.GetDetailedItemLevelInfo and safe(C_Item.GetDetailedItemLevelInfo, link)
        items[#items + 1] = strip(link) .. (ilvl and (" (ilvl " .. ilvl .. ")") or "")
      end
    end
    local e = { at = time(), items = items }
    if C_PaperDollInfo and C_PaperDollInfo.GetInspectItemLevel then e.avgItemLevel = ns.round(safe(C_PaperDollInfo.GetInspectItemLevel, unit), 1) end
    if C_SpecializationInfo and C_SpecializationInfo.GetInspectSpecialization then
      local specID = safe(C_SpecializationInfo.GetInspectSpecialization, unit)
      if specID and specID > 0 and GetSpecializationInfoByID then e.spec = select(2, safe(GetSpecializationInfoByID, specID)) end
    end
    if C_PaperDollInfo and C_PaperDollInfo.GetInspectGuildInfo then
      e.guildPoints = nil
    end
    inspect[guid] = e
    pendingInspect = nil
  end)
  C_Timer.NewTicker(5, function()
    -- refresh units that are visible right now
    if C_NamePlate and C_NamePlate.GetNamePlates then
      for _, plate in ipairs(C_NamePlate.GetNamePlates() or {}) do
        local unit = plate.namePlateUnitToken or (plate.UnitFrame and plate.UnitFrame.unit)
        if unit then note(safe(UnitGUID, unit), "nameplate", unit) end
      end
    end
    local now = time()
    for guid, e in pairs(seen) do
      if now - (e.lastSeen or 0) > MAX_AGE then seen[guid] = nil end
    end
  end)
end

function P.InspectCache(guid)
  local e = inspect[guid]
  if not e then return nil end
  return { at = date("%H:%M:%S", e.at), avgItemLevel = e.avgItemLevel, spec = e.spec, items = e.items }
end

function P.Nearby()
  local list = {}
  for guid, e in pairs(seen) do list[#list + 1] = { guid = guid, e = e } end
  table.sort(list, function(a, b) return (a.e.lastSeen or 0) > (b.e.lastSeen or 0) end)
  local out = {}
  local now = time()
  for i = 1, math.min(40, #list) do
    local e, guid = list[i].e, list[i].guid
    local r = {
      name = e.name .. (e.realm and ("-" .. e.realm) or ""), class = e.className or e.class, race = e.raceName or e.race,
      level = e.level, guild = e.guild, seen = (now - (e.lastSeen or now)) .. "s ago (" .. tostring(e.source) .. ")",
      hostile = e.hostile, pvp = e.pvp, healthPct = e.healthPct, targeting = e.targeting, casting = e.casting,
    }
    local ins = P.InspectCache(guid)
    if ins then r.inspect = ins end
    out[#out + 1] = r
  end
  return out
end
