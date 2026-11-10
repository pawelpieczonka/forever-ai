-- ForeverAI: tracked creatures. A mark on the map says where a creature usually is; tracking says whether one is
-- there. The add-on can only see what the game shows it: units with a nameplate, the player's target and the
-- mouseover. While a creature is tracked, a living one in sight gets the add-on's own marker over its nameplate, a
-- sound and a line in the chat say that it is there, the place where one was last seen is remembered, and the AI gets
-- all of it with every question (section "tracked").
--
-- The game's raid icons are not used: SetRaidTarget is reserved for the game's own interface, and calling it shows
-- the player "ForeverAI has been blocked from an action only available to the Blizzard UI".
local ADDON, ns = ...
local T = {}
ns.Track = T

local MAX = 5
local SCAN_S = 0.5
local MAX_MARKERS = 8
local SAY_AGAIN_S = 15 -- a creature that was out of sight this long is announced again
-- the picture of the marker per tracked creature (the art of the raid icons: only the picture, drawn by the add-on)
local PICTURES = { 8, 7, 1, 2, 4 }
local safe, clean = ns.safe, ns.clean

-- the tracked creatures: { name, quest (title, optional), since, seen = { t, mapID, x, y, zone } }
local function list()
  if not ns.char then return {} end
  ns.char.tracked = ns.char.tracked or {}
  return ns.char.tracked
end

-- what the last scan found, per name (lower case): { alive = n, marked = bool, goneAt = time it left sight }
local sight = {}

local function changed()
  if ns.UI and ns.UI.UpdateStrip then pcall(ns.UI.UpdateStrip) end
end

local function cvar(name)
  local value -- one value, also when the game knows no such setting and returns nothing
  if C_CVar and C_CVar.GetCVar then value = safe(C_CVar.GetCVar, name) elseif GetCVar then value = safe(GetCVar, name) end
  return value
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
local function visible()
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

------------------------------------------------------------------------ markers over nameplates
local markers = {} -- frames, reused; markers[i].unit is the unit it sits on while shown

local function marker(i)
  local m = markers[i]
  if m then return m end
  m = CreateFrame("Frame", nil, UIParent)
  m:SetSize(34, 34)
  m.glow = m:CreateTexture(nil, "BACKGROUND")
  m.glow:SetPoint("CENTER")
  m.glow:SetSize(58, 58)
  m.glow:SetTexture("Interface\\Cooldown\\star4")
  if m.glow.SetBlendMode then m.glow:SetBlendMode("ADD") end
  m.glow:SetVertexColor(0.36, 1, 0.62, 0.9)
  m.icon = m:CreateTexture(nil, "ARTWORK")
  m.icon:SetAllPoints()
  local t = 0
  m:SetScript("OnUpdate", function(self, dt) -- a slow pulse, so that it catches the eye
    t = t + (dt or 0.016)
    local wave = 0.5 + 0.5 * math.sin(t * 5)
    self.glow:SetAlpha(0.35 + 0.55 * wave)
    self:SetScale(1 + 0.12 * wave)
  end)
  markers[i] = m
  return m
end

local function hideMarkers(from)
  for i = from, #markers do
    markers[i].unit = nil
    markers[i]:Hide()
  end
end

-- the units that carry a marker now
function T.Marked()
  local out = {}
  for _, m in ipairs(markers) do if m.unit and m:IsShown() then out[#out + 1] = m.unit end end
  return out
end

------------------------------------------------------------------------ announcing
local function announce(mob, alive)
  local text = string.format("%s is in sight%s", mob.name, alive > 1 and (" (" .. alive .. ")") or "")
  if PlaySound then pcall(PlaySound, (SOUNDKIT and SOUNDKIT.RAID_WARNING) or 8959, "Master") end
  if UIErrorsFrame and UIErrorsFrame.AddMessage then pcall(UIErrorsFrame.AddMessage, UIErrorsFrame, text, 0.36, 1, 0.62) end
  ns.print("|cff5dff9d" .. text .. "|r")
end

------------------------------------------------------------------------ scanning
function T.Scan()
  local tracked = list()
  if #tracked == 0 then
    if #markers > 0 then hideMarkers(1) end
    return
  end
  local units = visible()
  local used = 0
  for i, mob in ipairs(tracked) do
    local key = mob.name:lower()
    local before = sight[key] or { alive = 0 }
    local alive, marked = 0, false
    for _, u in ipairs(units) do
      if u.name == key and not u.dead then
        alive = alive + 1
        local plate = C_NamePlate and C_NamePlate.GetNamePlateForUnit and safe(C_NamePlate.GetNamePlateForUnit, u.unit)
        if plate and used < MAX_MARKERS then
          used = used + 1
          local m = marker(used)
          m:SetParent(plate)
          m:ClearAllPoints()
          m:SetPoint("BOTTOM", plate, "TOP", 0, 10)
          m.icon:SetTexture("Interface\\TargetingFrame\\UI-RaidTargetingIcon_" .. (PICTURES[i] or 8))
          m.unit = u.unit
          m:Show()
          marked = true
        end
      end
    end
    local now = time()
    local goneAt, told = before.goneAt, before.told
    if alive > 0 then
      if before.alive == 0 and (not told or not goneAt or now - goneAt >= SAY_AGAIN_S) then
        announce(mob, alive)
        told = true
      end
      goneAt = nil
      local mapID, x, y = playerSpot()
      if x then
        local info = C_Map.GetMapInfo(mapID)
        mob.seen = { t = now, mapID = mapID, x = x, y = y, zone = info and info.name or nil }
      end
    elseif before.alive > 0 then
      goneAt = now
    end
    sight[key] = { alive = alive, marked = marked, goneAt = goneAt, told = told }
    if before.alive ~= alive then changed() end
  end
  hideMarkers(used + 1)
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

------------------------------------------------------------------------ nameplates while tracking
-- Tracking listens by itself: while something is tracked the game shows the nameplates of all enemies, from as far
-- as it allows, and the player's own settings come back when tracking ends. The game does not let these settings be
-- changed in a fight, so both wait for its end.
local WANTED = { { "nameplateShowEnemies", "1" }, { "nameplateShowAll", "1" }, { "nameplateMaxDistance", "100" } }

local function inFight() return InCombatLockdown and InCombatLockdown() and true or false end

function T.ShowNameplates()
  if not (ns.char and SetCVar) or inFight() then return false end
  local saved = ns.char.trackCVars or {}
  local first = next(saved) == nil
  for _, want in ipairs(WANTED) do
    local name, value = want[1], want[2]
    local old = cvar(name)
    if old ~= nil and saved[name] == nil then saved[name] = old end
    if old ~= nil and old ~= value and not (name == "nameplateMaxDistance" and (tonumber(old) or 0) >= 100) then
      pcall(SetCVar, name, value) -- the game caps the distance at what it allows
    end
  end
  ns.char.trackCVars = saved
  if first then
    ns.print("Tracking: the nameplates of all enemies are shown while a creature is tracked. Your own settings come back when it ends.")
  end
  return true
end

local function restoreNameplates()
  local saved = ns.char and ns.char.trackCVars
  if not saved or not next(saved) or inFight() then return end
  for name, old in pairs(saved) do
    if cvar(name) ~= old then pcall(SetCVar, name, old) end
  end
  ns.char.trackCVars = nil
end

-- the settings follow the list: on while something is tracked, back when nothing is (called again after a fight)
local function syncNameplates()
  if #list() > 0 then
    if T.ShowNameplates() then pcall(T.Scan) changed() end
  else
    restoreNameplates()
  end
end

------------------------------------------------------------------------ the list
local function forget(i)
  local tracked = list()
  local mob = table.remove(tracked, i)
  if not mob then return end
  sight[mob.name:lower()] = nil
  hideMarkers(1) -- the next scan puts back the ones that still belong
  if #tracked == 0 then restoreNameplates() end
end
T.SyncNameplates = syncNameplates

function T.CheckQuests()
  local tracked = list()
  local gone = false
  for i = #tracked, 1, -1 do
    if finished(tracked[i]) then forget(i) gone = true end
  end
  if gone then
    T.Scan()
    changed()
  end
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
  if #tracked > 0 then T.ShowNameplates() end
  T.Scan()
  changed()
end

-- stop tracking one creature, or all of them
function T.Stop(name)
  local tracked = list()
  for i = #tracked, 1, -1 do
    if not name or tracked[i].name:lower() == name:lower() then forget(i) end
  end
  T.Scan()
  changed()
end

local function ago(t)
  local s = time() - (t or time())
  if s < 60 then return "moments ago" end
  if s < 3600 then return math.floor(s / 60) .. " min ago" end
  return math.floor(s / 3600) .. " h ago"
end

-- what the window shows: { name, alive, blind (nothing seen and the game shows no nameplates of idle units) }
function T.State()
  local out = {}
  local blind = T.Sight() ~= "all"
  for _, mob in ipairs(list()) do
    local s = sight[mob.name:lower()] or {}
    local alive = s.alive or 0
    out[#out + 1] = { name = mob.name, alive = alive, blind = blind and alive == 0 }
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
  if mode == "all" then
    local range = tonumber(cvar("nameplateMaxDistance"))
    return "of all enemies" .. (range and string.format(" within %d yards", range) or " in range")
  end
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
      out[i] = string.format("%s: %d alive in sight now%s", mob.name, s.alive, s.marked and ", highlighted on the screen" or "")
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
  local units = visible()
  local names = {}
  for _, u in ipairs(units) do names[#names + 1] = u.name .. (u.dead and " (dead)" or "") end
  ns.print("nameplates:", T.SightText(), "| nameplateShowEnemies", tostring(cvar("nameplateShowEnemies")), "nameplateShowAll",
    tostring(cvar("nameplateShowAll")), "nameplateMaxDistance", tostring(cvar("nameplateMaxDistance")))
  ns.print("units seen (" .. #units .. "):", #names > 0 and table.concat(names, ", ") or "none")
  ns.print("markers shown:", #T.Marked(), "| blocked by the game:", tostring(T.blocked or "nothing"))
  for _, line in ipairs(T.Section() or { "nothing is tracked" }) do ns.print(line) end
end

-- /fai probe: a look at every way the game might show an add-on something that has no nameplate (a herb, a vein, a
-- chest). Printed in the chat and sent along with the next question (game.probe), to learn what this client offers.
function T.Probe()
  local out = {}
  local function say(line)
    out[#out + 1] = line
    ns.print(line)
  end
  local function unitLine(unit)
    if not safe(UnitExists, unit) then return "nothing" end
    local guid = clean(safe(UnitGUID, unit)) or ""
    local kind, id = guid:match("^(%a+)%-"), select(6, strsplit("-", guid))
    return string.format("%s (%s%s)", tostring(clean(safe(UnitName, unit))), tostring(kind or "?"), id and (", id " .. id) or "")
  end
  for _, unit in ipairs({ "target", "mouseover", "softenemy", "softfriend" }) do say(unit .. ": " .. unitLine(unit)) end
  -- the "soft target": the game itself picks the nearest thing the player can interact with, objects included
  local own = cvar("SoftTargetInteract")
  say(string.format("softinteract with the game's own setting (SoftTargetInteract %s): %s", tostring(own), unitLine("softinteract")))
  local settings = {}
  for _, name in ipairs({ "SoftTargetInteractRange", "SoftTargetInteractArc", "SoftTargetNameplateInteract",
                          "SoftTargetIconGameObject", "SoftTargetIconInteract" }) do
    settings[#settings + 1] = name .. " " .. tostring(cvar(name))
  end
  say("soft target settings: " .. table.concat(settings, ", "))
  -- vignettes: what the game draws on the minimap by itself (rare creatures, treasures)
  local names = {}
  if C_VignetteInfo and C_VignetteInfo.GetVignettes then
    for _, id in ipairs(safe(C_VignetteInfo.GetVignettes) or {}) do
      local info = safe(C_VignetteInfo.GetVignetteInfo, id)
      names[#names + 1] = tostring(type(info) == "table" and info.name or id)
    end
  end
  say(string.format("vignettes (%d): %s", #names, #names > 0 and table.concat(names, ", ") or "none"))
  -- nameplates by kind: an object with a nameplate would show as GameObject
  local kinds = {}
  if C_NamePlate and C_NamePlate.GetNamePlates then
    for _, plate in ipairs(safe(C_NamePlate.GetNamePlates) or {}) do
      local unit = plate.namePlateUnitToken or (plate.UnitFrame and plate.UnitFrame.unit)
      local kind = unit and (clean(safe(UnitGUID, unit)) or ""):match("^(%a+)%-") or "?"
      kinds[kind] = (kinds[kind] or 0) + 1
    end
  end
  local parts = {}
  for kind, n in pairs(kinds) do parts[#parts + 1] = kind .. " " .. n end
  table.sort(parts)
  say("nameplates by kind: " .. (#parts > 0 and table.concat(parts, ", ") or "none"))
  local tip = ns.Collect and ns.Collect.CaptureTooltip and ns.Collect.CaptureTooltip()
  say("tooltip: " .. (tip and tostring(tip.lines):gsub("\n", " / ") or "none"))
  local tracking = {}
  if C_Minimap and C_Minimap.GetNumTrackingTypes and C_Minimap.GetTrackingInfo then
    for i = 1, (safe(C_Minimap.GetNumTrackingTypes) or 0) do
      local info = safe(C_Minimap.GetTrackingInfo, i)
      if type(info) == "table" and info.active then tracking[#tracking + 1] = tostring(info.name) end
    end
  end
  say("minimap tracking on: " .. (#tracking > 0 and table.concat(tracking, ", ") or "none or not readable"))
  if ns.char then ns.char.lastProbe = out end
  -- The same look with the soft target turned on, all around the player and as far as the game allows, for eight
  -- seconds: everything it picks in that time is listed. The player's settings come back afterwards.
  if own == nil or inFight() or not SetCVar then
    say("soft target for 8 s: not tried (" .. (own == nil and "this game has no such setting" or "in a fight") .. ")")
    return
  end
  local wide = { { "SoftTargetInteract", "3" }, { "SoftTargetInteractArc", "2" }, { "SoftTargetInteractRange", "60" } }
  local before = {}
  for _, w in ipairs(wide) do
    before[w[1]] = cvar(w[1])
    if before[w[1]] ~= nil then pcall(SetCVar, w[1], w[2]) end
  end
  say("soft target turned on all around (range now " .. tostring(cvar("SoftTargetInteractRange")) .. "): walk past the thing for 8 s…")
  local seen, order, ticks = {}, {}, 0
  local ticker
  ticker = C_Timer.NewTicker(0.5, function()
    ticks = ticks + 1
    local line = unitLine("softinteract")
    if line ~= "nothing" and not seen[line] then
      seen[line] = true
      order[#order + 1] = line
    end
    if ticks >= 16 then
      ticker:Cancel()
      say("soft target for 8 s: " .. (#order > 0 and table.concat(order, "; ") or "nothing"))
      for name, old in pairs(before) do pcall(SetCVar, name, old) end
    end
  end)
end

ns.On("PLAYER_ENTERING_WORLD", function()
  if not T.ticker then T.ticker = C_Timer.NewTicker(SCAN_S, function() pcall(T.Scan) end) end
  C_Timer.After(2, function() pcall(syncNameplates) end) -- what was tracked before a /reload is listened for again
end)
ns.On("PLAYER_REGEN_ENABLED", function() pcall(syncNameplates) end) -- the fight is over: the settings can change now

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
  -- anything of ours the game refuses is remembered, so that /fai track can say what it was
  local guard = CreateFrame("Frame")
  for _, e in ipairs({ "ADDON_ACTION_FORBIDDEN", "ADDON_ACTION_BLOCKED" }) do pcall(guard.RegisterEvent, guard, e) end
  guard:SetScript("OnEvent", function(_, _, addon, func)
    if addon == ADDON then
      T.blocked = tostring(func)
      ns.lastError = "the game blocked " .. tostring(func)
    end
  end)
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.tracked = T.Section
end
