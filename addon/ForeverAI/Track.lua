-- ForeverAI: tracked creatures. A mark on the map says where a creature usually is; tracking says whether one is
-- there. The add-on can only see what the game shows it: units with a nameplate (a few dozen meters around the
-- player). While a creature is tracked, a living one in sight gets a raid icon over its head, the place where one was
-- last seen is remembered, and the AI gets both with every question (section "tracked").
local ADDON, ns = ...
local T = {}
ns.Track = T

local MAX = 5
local SCAN_S = 0.5
-- one raid icon per tracked creature, the most visible ones first
local ICONS = { { 8, "skull" }, { 7, "cross" }, { 1, "star" }, { 2, "circle" }, { 4, "triangle" } }
local safe, clean = ns.safe, ns.clean

-- the tracked creatures: { name, quest (title, optional), since, seen = { t, mapID, x, y, zone } }
local function list()
  if not ns.char then return {} end
  ns.char.tracked = ns.char.tracked or {}
  return ns.char.tracked
end

-- what the last scan found, per name (lower case): { alive = n, unit = token carrying the icon, icon = name }
local sight = {}

local function changed()
  if ns.UI and ns.UI.UpdateStrip then pcall(ns.UI.UpdateStrip) end
end

local function cvar(name)
  if C_CVar and C_CVar.GetCVar then return safe(C_CVar.GetCVar, name) end
  return GetCVar and safe(GetCVar, name) or nil
end

-- How much the game lets the add-on see. A game set to show nameplates only for the target and for units in a fight
-- (the default of this client) shows nothing of a creature that just stands there: "blind". Then "none in sight"
-- would be a lie, so the add-on says that it cannot tell.
function T.Sight()
  if cvar("nameplateShowEnemies") == "0" then return "off" end
  if cvar("nameplateShowAll") == "0" then return "fights" end
  return "all"
end

-- the units the game shows: every nameplate, and the player's target and mouseover (seen without nameplates too)
local function nameplates()
  local out, known = {}, {}
  local function add(unit)
    if safe(UnitIsPlayer, unit) then return end
    local name = clean(safe(UnitName, unit))
    if not name then return end
    local guid = clean(safe(UnitGUID, unit))
    if guid and known[guid] then return end
    if guid then known[guid] = true end
    out[#out + 1] = { unit = unit, name = name:lower(), dead = clean(safe(UnitIsDead, unit)) and true or false }
  end
  if C_NamePlate and C_NamePlate.GetNamePlates then
    for _, plate in ipairs(safe(C_NamePlate.GetNamePlates) or {}) do
      local unit = plate.namePlateUnitToken or (plate.UnitFrame and plate.UnitFrame.unit)
      if unit then add(unit) end
    end
  end
  for _, unit in ipairs({ "target", "mouseover" }) do
    if safe(UnitExists, unit) then add(unit) end
  end
  return out
end

local function playerSpot()
  local mapID = C_Map.GetBestMapForUnit("player")
  local pos = mapID and C_Map.GetPlayerMapPosition(mapID, "player")
  if pos then return mapID, pos.x * 100, pos.y * 100 end
end

-- the game's own raid icon over a unit's head; a client that does not let an add-on do this just says no
local function setIcon(unit, icon)
  if not SetRaidTarget then return false end
  if GetRaidTargetIndex and safe(GetRaidTargetIndex, unit) == icon then return true end
  return (pcall(SetRaidTarget, unit, icon))
end

local function clearIcon(icon)
  if not (SetRaidTarget and GetRaidTargetIndex) then return end
  for _, u in ipairs(nameplates()) do
    if safe(GetRaidTargetIndex, u.unit) == icon then pcall(SetRaidTarget, u.unit, 0) end
  end
end

function T.Scan()
  local tracked = list()
  if #tracked == 0 then return end
  local plates = nameplates()
  local before = {}
  for key, s in pairs(sight) do before[key] = s.alive end
  for i, mob in ipairs(tracked) do
    local key = mob.name:lower()
    local icon = ICONS[i]
    local alive, carrier, first = 0, nil, nil
    for _, u in ipairs(plates) do
      if u.name == key and not u.dead then
        alive = alive + 1
        first = first or u.unit
        if icon and GetRaidTargetIndex and safe(GetRaidTargetIndex, u.unit) == icon[1] then carrier = u.unit end
      end
    end
    local marked = false
    if alive > 0 and icon then marked = carrier ~= nil or setIcon(first, icon[1]) end
    sight[key] = { alive = alive, icon = marked and icon[2] or nil }
    if alive > 0 then
      local mapID, x, y = playerSpot()
      if x then
        local info = C_Map.GetMapInfo(mapID)
        mob.seen = { t = time(), mapID = mapID, x = x, y = y, zone = info and info.name or nil }
      end
    end
    if before[key] ~= alive then changed() end
  end
end

-- a quest objective that names the creature is done, or its quest is complete or gone: nothing left to look for
local function finished(mob)
  if not mob.quest or not (C_QuestLog and C_QuestLog.GetNumQuestLogEntries) then return false end
  local key = mob.name:lower()
  for i = 1, (safe(C_QuestLog.GetNumQuestLogEntries) or 0) do
    local q = safe(C_QuestLog.GetInfo, i)
    if q and not q.isHeader and q.title == mob.quest and q.questID then
      if safe(C_QuestLog.IsComplete, q.questID) then return true end
      for _, ob in ipairs(safe(C_QuestLog.GetQuestObjectives, q.questID) or {}) do
        local text = clean(ob.text)
        if text and text:lower():find(key, 1, true) then return ob.finished and true or false end
      end
      return false
    end
  end
  return true -- the quest left the log
end

-- The player can let the game show the nameplates of all enemies while something is tracked (a button on the strip);
-- their own setting comes back when tracking ends.
local NAMEPLATE_CVARS = { "nameplateShowEnemies", "nameplateShowAll" }

function T.ShowNameplates()
  if InCombatLockdown and InCombatLockdown() then
    ns.print("The game does not let nameplates be switched in a fight. Try again after it.")
    return false
  end
  if not (ns.char and SetCVar) then return false end
  ns.char.trackCVars = ns.char.trackCVars or {}
  for _, name in ipairs(NAMEPLATE_CVARS) do
    local old = cvar(name)
    if old ~= nil and old ~= "1" then
      if ns.char.trackCVars[name] == nil then ns.char.trackCVars[name] = old end
      pcall(SetCVar, name, "1")
    end
  end
  T.Scan()
  changed()
  return true
end

local function restoreNameplates()
  local saved = ns.char and ns.char.trackCVars
  if not saved or not next(saved) then return end
  if InCombatLockdown and InCombatLockdown() then return end -- tried again when tracking ends the next time
  for name, old in pairs(saved) do pcall(SetCVar, name, old) end
  ns.char.trackCVars = nil
end

local function forget(i)
  local tracked = list()
  local mob = table.remove(tracked, i)
  if not mob then return end
  sight[mob.name:lower()] = nil
  -- the icons follow the order of the list: take all of ours off, the next scan puts them back where they belong
  for _, icon in ipairs(ICONS) do clearIcon(icon[1]) end
  if #tracked == 0 then restoreNameplates() end
end

function T.CheckQuests()
  local tracked = list()
  local gone = false
  for i = #tracked, 1, -1 do
    if finished(tracked[i]) then forget(i) gone = true end
  end
  if gone then changed() end
end

-- track these creatures; ``add`` keeps the ones tracked so far (an extra stop on a route that still holds)
function T.Set(mobs, add)
  local tracked = list()
  if not add then
    for i = #tracked, 1, -1 do forget(i) end
  end
  for _, m in ipairs(mobs or {}) do
    local name = type(m) == "table" and m.name or m
    if type(name) == "string" and name ~= "" and #tracked < MAX then
      local known = false
      for _, t in ipairs(tracked) do if t.name:lower() == name:lower() then known = true end end
      if not known then
        tracked[#tracked + 1] = { name = name, quest = type(m) == "table" and m.quest or nil, since = time() }
      end
    end
  end
  T.Scan()
  changed()
end

-- stop tracking one creature, or all of them
function T.Stop(name)
  local tracked = list()
  for i = #tracked, 1, -1 do
    if not name or tracked[i].name:lower() == name:lower() then forget(i) end
  end
  changed()
end

local function ago(t)
  local s = time() - (t or time())
  if s < 60 then return "moments ago" end
  if s < 3600 then return math.floor(s / 60) .. " min ago" end
  return math.floor(s / 3600) .. " h ago"
end

-- what the window shows: { name, alive, icon, blind (nothing seen and the game shows no nameplates of idle units) }
function T.State()
  local out = {}
  local blind = T.Sight() ~= "all"
  for _, mob in ipairs(list()) do
    local s = sight[mob.name:lower()] or {}
    local alive = s.alive or 0
    out[#out + 1] = { name = mob.name, alive = alive, icon = s.icon, blind = blind and alive == 0 }
  end
  return out
end

local BLIND = {
  fights = "the game shows nameplates only for the target and for units in a fight (nameplates of all enemies are off)",
  off = "the game shows no nameplates of enemies",
}

-- for the game section: what "around" and "tracked" can know
function T.SightText()
  local mode = T.Sight()
  if mode == "all" then return "of all enemies in range" end
  return mode == "off" and "off (the add-on sees only the target and the mouseover)"
    or "only for the target and units in a fight (the add-on does not see creatures that just stand there)"
end

-- for the AI: is a living one in sight right now, and where was the last one seen
function T.Section()
  local tracked = list()
  if #tracked == 0 then return nil end
  T.Scan()
  local mapID, px, py = playerSpot()
  local blind = BLIND[T.Sight()]
  local out = {}
  for i, mob in ipairs(tracked) do
    local s = sight[mob.name:lower()] or { alive = 0 }
    local last
    if mob.seen then
      local d = (px and mob.seen.mapID == mapID) and ns.MapMeters(mapID, px, py, mob.seen.x, mob.seen.y)
      last = string.format("last seen alive %s near %s %.1f, %.1f%s", ago(mob.seen.t), tostring(mob.seen.zone or "?"),
        mob.seen.x, mob.seen.y, d and string.format(" (%d m from here)", d) or "")
    end
    if s.alive > 0 then
      out[i] = string.format("%s: %d alive in sight now%s", mob.name, s.alive,
        s.icon and (", marked with the " .. s.icon .. " raid icon") or "")
    elseif blind then
      out[i] = string.format("%s: not seen, but %s, so one can be near without being seen%s", mob.name, blind,
        last and ("; " .. last) or "")
    else
      out[i] = mob.name .. ": none in sight now; " .. (last or "not seen since it is tracked")
    end
  end
  return out
end

-- /fai track: what the add-on sees right now, in the chat (for checking it in the real game)
function T.Debug()
  local units = nameplates()
  local names = {}
  for _, u in ipairs(units) do names[#names + 1] = u.name .. (u.dead and " (dead)" or "") end
  ns.print("nameplates:", T.SightText(), "| nameplateShowEnemies", tostring(cvar("nameplateShowEnemies")), "nameplateShowAll",
    tostring(cvar("nameplateShowAll")), "nameplateMaxDistance", tostring(cvar("nameplateMaxDistance")))
  ns.print("units seen (" .. #units .. "):", #names > 0 and table.concat(names, ", ") or "none")
  ns.print("raid icons:", SetRaidTarget and "SetRaidTarget exists" or "no SetRaidTarget", "| target icon",
    tostring(GetRaidTargetIndex and safe(GetRaidTargetIndex, "target")))
  for _, line in ipairs(T.Section() or { "nothing is tracked" }) do ns.print(line) end
end

ns.On("PLAYER_ENTERING_WORLD", function()
  if not T.ticker then T.ticker = C_Timer.NewTicker(SCAN_S, function() pcall(T.Scan) end) end
end)

do -- the quest log changes in bursts: look once they have settled
  local pending
  local function soon()
    if pending then return end
    pending = true
    C_Timer.After(1, function() pending = false pcall(T.CheckQuests) end)
  end
  local f = CreateFrame("Frame")
  for _, e in ipairs({ "QUEST_LOG_UPDATE", "QUEST_TURNED_IN", "QUEST_REMOVED" }) do pcall(f.RegisterEvent, f, e) end
  f:SetScript("OnEvent", soon)
  -- a creature the player points at or targets is seen at once, nameplates or not
  local look = CreateFrame("Frame")
  for _, e in ipairs({ "UPDATE_MOUSEOVER_UNIT", "PLAYER_TARGET_CHANGED" }) do pcall(look.RegisterEvent, look, e) end
  look:SetScript("OnEvent", function() pcall(T.Scan) end)
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.tracked = T.Section
end
