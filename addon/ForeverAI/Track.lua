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
local QUIET_S = 5 -- at most one announcement in this time: a group that walks in is one sound
local AWAY_S = 60 -- none of them in sight for this long (10 s when the game is also asked by name): found again is news
local AWAY_NAMED_S = 10
local REMEMBER_S = 300 -- a creature seen this recently is the same creature, not a new one
local LOGIN_S = 30 -- this long after a loading screen an empty quest log may just not be filled yet
-- the picture of the marker per tracked creature (the art of the raid icons: only the picture, drawn by the add-on)
local PICTURES = { 8, 7, 1, 2, 4 }
local safe, clean = ns.safe, ns.clean

-- the tracked creatures: { name, quest (title, optional), since, seen = { t, mapID, x, y, zone },
--   also = the other creatures that give the same objective of the quest, what = that objective's name }
local function list()
  if not ns.char then return {} end
  ns.char.tracked = ns.char.tracked or {}
  return ns.char.tracked
end

-- what the last scan found, per name (lower case): { alive = n, names = the ones seen, marked = bool,
--   near = the name of one the game has in its range without a nameplate, guids = the creatures seen (GUID -> when
--   last), peak = the most seen at once (a game without GUIDs), presentAt = when one was last in sight or near,
--   news = the name to announce once the quiet time is over, saidAt = when the last announcement was made }
local sight = {}

local function changed()
  if ns.UI and ns.UI.UpdateStrip then pcall(ns.UI.UpdateStrip) end
end

local function cvar(name)
  local value -- one value, also when the game knows no such setting and returns nothing
  if C_CVar and C_CVar.GetCVar then value = safe(C_CVar.GetCVar, name) elseif GetCVar then value = safe(GetCVar, name) end
  return value
end

local function inFight() return InCombatLockdown and InCombatLockdown() and true or false end

------------------------------------------------------------------------ looking by name
-- Nameplates reach about 40 yards in this game, whatever the setting says, and only what is on the screen. The game
-- itself knows the units much farther out, and one call tells an add-on whether a unit of a given name is among
-- them: TargetUnit is reserved for the game's own interface, and the game refuses an add-on's call
-- (ADDON_ACTION_FORBIDDEN) only when there is such a unit to target. The refusal is the answer; nothing is targeted.
-- (The add-on unitscan finds rares this way.)
--
-- The game answers a refusal with the popup "ForeverAI has been blocked from an action only available to the
-- Blizzard UI". It is kept away three ways: the event is taken from the game's handler while tracking looks by name
-- (refusals that are not this call are handed on to it), a popup that is shown for this call anyway is hidden in the
-- same moment, and when that does not work either the search turns itself off for good (ns.db.lookByName).
local LOOK_S = 1 -- a creature that is not in sight is looked for by its name this often
local refused, asking, hooked = false, false, false

local function quiet(on)
  if on == (T.quiet or false) or not (UIParent and UIParent.UnregisterEvent) then return end
  if on then pcall(UIParent.UnregisterEvent, UIParent, "ADDON_ACTION_FORBIDDEN")
  else pcall(UIParent.RegisterEvent, UIParent, "ADDON_ACTION_FORBIDDEN") end
  T.quiet = on
end

local function giveUp()
  T.byName = false
  if ns.db then ns.db.lookByName = false end
  quiet(false)
end

local function hidePopups()
  if hooked or not (hooksecurefunc and StaticPopup_Show) then return end
  hooked = true
  hooksecurefunc("StaticPopup_Show", function(which)
    if not asking or (which ~= "ADDON_ACTION_FORBIDDEN" and which ~= "ADDON_ACTION_BLOCKED") then return end
    if StaticPopup_Hide then pcall(StaticPopup_Hide, which) end
    if not StaticPopup_Visible or StaticPopup_Visible(which) then giveUp() end -- it stays on the screen: never again
  end)
end

local function exists(name)
  if T.byName == false then return false end
  refused, asking = false, true
  pcall(TargetUnit, name, true)
  asking = false
  return refused
end

-- Does this game answer that way? Checked once: the player's own name must be refused, a name nobody has must not.
-- T.byName: nil not tried, true works, false not available (then only nameplates, target and mouseover count).
local function canLookByName()
  if ns.db and ns.db.lookByName == false then
    quiet(false)
    return false
  end
  if T.byName == nil then
    local me = clean(safe(UnitName, "player"))
    if not (TargetUnit and me) then
      T.byName = false
    else
      hidePopups()
      quiet(true)
      T.byName = true -- for the two questions of the check
      local works = exists(me) and not exists("Nobody Has This Name Xq")
      if T.byName then T.byName = works end -- unless a popup that stayed turned it off
    end
  end
  quiet(T.byName)
  return T.byName
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
    out[#out + 1] = { unit = unit, guid = guid, name = name:lower(), shown = name,
                      dead = clean(safe(UnitIsDead, unit)) and true or false }
  end
  if C_NamePlate and C_NamePlate.GetNamePlates then
    for _, plate in ipairs(safe(C_NamePlate.GetNamePlates) or {}) do
      local unit = plate.namePlateUnitToken or (plate.UnitFrame and plate.UnitFrame.unit)
      if unit then add(unit) end
    end
  end
  local plates = #out
  for _, unit in ipairs({ "target", "mouseover" }) do
    local twice = false
    for n = 1, UnitIsUnit and plates or 0 do
      if safe(UnitIsUnit, unit, out[n].unit) then twice = true break end
    end
    if not twice and safe(UnitExists, unit) then add(unit) end
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
local function announce(name, alive)
  local text = alive > 0 and string.format("%s is in sight%s", name, alive > 1 and (" (" .. alive .. ")") or "")
    or (name .. " is near") -- found by its name, too far for a nameplate
  if PlaySound then pcall(PlaySound, (SOUNDKIT and SOUNDKIT.RAID_WARNING) or 8959, "Master") end
  if UIErrorsFrame and UIErrorsFrame.AddMessage then pcall(UIErrorsFrame.AddMessage, UIErrorsFrame, text, 0.36, 1, 0.62) end
  ns.print("|cff5dff9d" .. text .. "|r")
end

------------------------------------------------------------------------ creatures that count for the same objective
-- A quest often takes any of several creatures: every Bloodfeather drops the belts, whichever one the route named.
-- The quest database knows them all, so one tracked creature stands for all of them.
local function logQuests()
  local out = {}
  if not (C_QuestLog and C_QuestLog.GetNumQuestLogEntries) then return out end
  for i = 1, (safe(C_QuestLog.GetNumQuestLogEntries) or 0) do
    local q = safe(C_QuestLog.GetInfo, i)
    if q and not q.isHeader and q.questID and q.title then out[#out + 1] = q end
  end
  return out
end

local sets = setmetatable({}, { __mode = "k" }) -- per tracked creature: the names that count, lower case

-- Fill in mob.also and mob.what, and mob.quest when only the creature was named. Until the quest database answers
-- (it is built after login) mob.also stays nil and this is asked again.
local function expand(mob)
  if mob.also or not (ns.Collect and ns.Collect.SameObjective) then return end
  local looked = false
  for _, q in ipairs(logQuests()) do
    if not mob.quest or q.title == mob.quest then
      local ok, label, names = pcall(ns.Collect.SameObjective, q.questID, mob.name)
      if ok and names then
        looked = true
        if #names > 0 then
          mob.quest = q.title
          mob.also = {}
          sets[mob] = nil
          for _, n in ipairs(names) do
            if n:lower() ~= mob.name:lower() then mob.also[#mob.also + 1] = n end
          end
          if #mob.also > 0 and label ~= "kill credit" then mob.what = label end
          return true
        end
      end
    end
  end
  if looked then mob.also = {} end
end

local function expandAll()
  local grew = false
  for _, mob in ipairs(list()) do
    if expand(mob) then grew = true end
  end
  return grew
end

-- the names that count for a tracked creature, lower case
local function counting(mob)
  local set = sets[mob]
  if not set then
    set = { [mob.name:lower()] = true }
    for _, n in ipairs(mob.also or {}) do set[n:lower()] = true end
    sets[mob] = set
  end
  return set
end

-- One objective is tracked once: an entry whose creature already counts for an earlier entry goes (the route watched
-- for Lasher Sproutling, then a mark came for Wrathvine, which gives the same fronds).
local function mergeSame()
  local tracked, merged = list(), false
  for j = #tracked, 2, -1 do
    local key = tracked[j].name:lower()
    for i = 1, j - 1 do
      if counting(tracked[i])[key] then
        local gone = table.remove(tracked, j)
        if gone.seen and not tracked[i].seen then tracked[i].seen = gone.seen end
        sight[key] = nil
        merged = true
        break
      end
    end
  end
  return merged
end

-- after the list changed, or the quest database came: who counts for what, and no objective twice
local function settle()
  local grew = expandAll()
  return mergeSame() or grew
end

-- is this creature watched for (by its own name or as one that counts for a tracked objective)?
function T.Watches(name)
  local key = tostring(name or ""):lower()
  for _, mob in ipairs(list()) do
    if counting(mob)[key] then return true end
  end
  return false
end

-- do these two creatures count for the same tracked objective?
function T.Same(a, b)
  a, b = tostring(a or ""):lower(), tostring(b or ""):lower()
  for _, mob in ipairs(list()) do
    local set = counting(mob)
    if set[a] and set[b] then return true end
  end
  return false
end

-- what the player is told is tracked: the objective when several creatures give it
local function label(mob) return mob.what or mob.name end

-- for the card of an answer: "Bloodfeather Rogue (or any other creature that counts for Bloodfeather Belt)"
function T.Describe(name)
  for _, mob in ipairs(list()) do
    if mob.name:lower() == tostring(name):lower() and mob.also and #mob.also > 0 then
      return string.format("%s (or any other creature that counts for %s)", mob.name, mob.what or "the same objective")
    end
  end
  return name
end

------------------------------------------------------------------------ what is news
-- The sound and the line in the chat are for news, and news is a creature that was not there:
--   * a creature the add-on has not seen lately comes into sight (the next one after a kill, a group walking in);
--   * one is in sight or near after none was for a while (the player came back to them);
--   * tracking starts and one is there.
-- Not news: the same creatures going in and out of sight (nameplates follow the camera and every step a creature
-- takes), and the change between "near" and "in sight" of creatures already known. Without GUIDs from the game the
-- count stands in for them: only more at once than ever seen here is news.
-- At most one announcement in QUIET_S; news in that time is said when it ends, if a creature is still there.
local function present(s, mob, alive, near, newcomer, newId, clock, byName)
  for id, t in pairs(s.guids) do
    if clock - t > REMEMBER_S then s.guids[id] = nil end
  end
  local here = alive > 0 or near ~= nil
  if here then
    local away = byName and AWAY_NAMED_S or AWAY_S
    if not s.presentAt or clock - s.presentAt >= away then
      s.news, s.newsId, s.peak = newcomer or s.names[1] or near or mob.name, nil, 0
    elseif newcomer and not s.news then
      s.news, s.newsId = newcomer, newId
    end
    s.presentAt = clock
  else
    s.news = nil -- gone before it was said: no late news
  end
  -- the newcomer itself left during the quiet time (not just a nameplate that blinked): the others are no news
  if s.news and s.newsId and clock - (s.guids[s.newsId] or 0) > 2 then s.news, s.newsId = nil, nil end
  if s.news and clock - (s.saidAt or -QUIET_S) >= QUIET_S then
    announce(s.news, alive)
    s.saidAt, s.news, s.newsId = clock, nil, nil
  end
end

------------------------------------------------------------------------ scanning
local lastMode -- which nameplates the game showed at the last scan, and whether a fight was on: the strip says it

function T.Scan()
  local tracked = list()
  if #tracked == 0 then
    if #markers > 0 then hideMarkers(1) end
    quiet(false) -- nothing to look for: the game's own handler has its event back
    return
  end
  local units = visible()
  local used, refresh, now, clock = 0, false, time(), GetTime()
  local byName = canLookByName()
  for i, mob in ipairs(tracked) do
    local key = mob.name:lower()
    local s = sight[key]
    if not s then
      s = { alive = 0, names = {}, guids = {}, peak = 0 }
      sight[key] = s
    end
    local was, alive, marked, counts = s.alive, 0, false, counting(mob)
    local newcomer, newId, ids = nil, nil, 0
    wipe(s.names)
    for _, u in ipairs(units) do
      if counts[u.name] and not u.dead then
        alive = alive + 1
        if not tContains(s.names, u.shown) then s.names[#s.names + 1] = u.shown end
        if u.guid then
          ids = ids + 1
          if not s.guids[u.guid] and not newcomer then newcomer, newId = u.shown, u.guid end
          s.guids[u.guid] = clock
        end
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
    -- none in sight: is one in the game's range at all? (once a second; the answer stands in between)
    local near = s.near
    if alive > 0 or not byName then
      near = nil
    elseif clock - (s.lookedAt or -LOOK_S) >= LOOK_S then
      s.lookedAt = clock
      near = exists(mob.name) and mob.name or nil
      for _, other in ipairs(not near and mob.also or {}) do
        if exists(other) then near = other break end
      end
    end
    if near ~= s.near then s.near, refresh = near, true end
    if ids < alive and alive > s.peak and s.presentAt then newcomer = newcomer or s.names[1] end -- no GUIDs: the count
    present(s, mob, alive, near, newcomer, newId, clock, byName)
    s.peak = math.max(s.peak, alive)
    if alive > 0 then
      local mapID, x, y = playerSpot()
      if x then
        local seen = mob.seen or {}
        if seen.mapID ~= mapID or not seen.zone then -- the zone's name is asked for once per zone
          local info = C_Map.GetMapInfo(mapID)
          seen.zone = info and info.name or nil
        end
        seen.t, seen.mapID, seen.x, seen.y = now, mapID, x, y
        mob.seen = seen
      end
    end
    s.alive, s.marked = alive, marked
    if was ~= alive then refresh = true end
  end
  hideMarkers(used + 1)
  local mode = T.Sight() .. (inFight() and ", in a fight" or "")
  if mode ~= lastMode then lastMode, refresh = mode, true end
  if refresh then changed() end
end

-- a quest objective that names the creature (or what it gives) is done, or its quest is complete or gone: nothing
-- left to look for
local function finished(mob, quests)
  if not mob.quest or not (C_QuestLog and C_QuestLog.GetNumQuestLogEntries) then return false end
  -- right after a loading screen the game may not have filled the quest log yet: an empty one proves nothing
  if #quests == 0 and GetTime() - (T.enteredAt or 0) < LOGIN_S then return false end
  local key, what = mob.name:lower(), mob.what and mob.what:lower()
  for _, q in ipairs(quests) do
    if q.title == mob.quest then
      if safe(C_QuestLog.IsComplete, q.questID) then return true end
      for _, ob in ipairs(safe(C_QuestLog.GetQuestObjectives, q.questID) or {}) do
        local text = clean(ob.text)
        text = text and text:lower()
        if text and (text:find(key, 1, true) or (what and text:find(what, 1, true))) then
          return ob.finished and true or false
        end
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

function T.ShowNameplates()
  if not (ns.char and SetCVar) or inFight() then return false end
  local first = ns.char.trackCVars == nil -- said once per tracking, also in a game that knows none of the settings
  local saved = ns.char.trackCVars or {}
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
  if not saved or inFight() then return end
  for name, old in pairs(saved) do
    if cvar(name) ~= old then pcall(SetCVar, name, old) end
  end
  ns.char.trackCVars = nil
end

-- the settings follow the list: on while something is tracked, back when nothing is (called again after a fight)
local function syncNameplates()
  if #list() > 0 then
    settle()
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

function T.CheckQuests()
  local tracked = list()
  local gone = settle() -- the quest database may have come since
  local quests = logQuests()
  for i = #tracked, 1, -1 do
    if finished(tracked[i], quests) then
      local names = counting(tracked[i])
      forget(i)
      gone = true
      -- nothing left to do at the stops that led to these creatures
      if ns.Map and ns.Map.Done then pcall(ns.Map.Done, names) end
    end
  end
  if gone then
    T.Scan()
    changed()
  end
end

-- Track these creatures; ``add`` keeps the ones tracked so far (an extra stop on a route that still holds). The next
-- answer often brings the same route again: a creature that stays tracked keeps what is known about it (where it was
-- last seen, that it was announced), and the nameplate settings are not touched.
function T.Set(mobs, add)
  local tracked = list()
  local old = {}
  if not add then
    for i = #tracked, 1, -1 do
      local mob = table.remove(tracked, i)
      old[mob.name:lower()] = mob
    end
  end
  for _, m in ipairs(mobs or {}) do
    local name = type(m) == "table" and m.name or m
    if type(name) == "string" and name ~= "" and #tracked < MAX then
      local key, known = name:lower(), false
      for _, t in ipairs(tracked) do if counting(t)[key] then known = true end end
      if not known then
        local mob = old[key] or { name = name, since = time() }
        old[key] = nil
        local quest = type(m) == "table" and m.quest or nil
        if quest and mob.quest ~= quest then -- another quest: the creatures that count are looked up again
          mob.quest, mob.also, mob.what = quest, nil, nil
          sets[mob] = nil
        end
        tracked[#tracked + 1] = mob
      end
    end
  end
  for key in pairs(old) do sight[key] = nil end
  settle()
  if #tracked > 0 then T.ShowNameplates() else restoreNameplates() end
  T.Scan()
  changed()
end

-- stop tracking one creature, or all of them
function T.Stop(name)
  local tracked = list()
  for i = #tracked, 1, -1 do
    if not name or counting(tracked[i])[name:lower()] then forget(i) end
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

-- what the window shows: { name, label (what is looked for: the objective when several creatures give it), alive,
-- blind (nothing seen and the game shows no nameplates of idle units), waiting (blind until the fight is over: the
-- nameplates come on then) }
function T.State()
  local out = {}
  local blind, waiting = T.Sight() ~= "all", inFight()
  for _, mob in ipairs(list()) do
    local s = sight[mob.name:lower()] or {}
    local alive = s.alive or 0
    local near = alive == 0 and s.near or nil
    out[#out + 1] = { name = mob.name, label = label(mob), alive = alive, near = near,
                      blind = blind and alive == 0 and not near, waiting = blind and alive == 0 and not near and waiting }
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
    -- several creatures count for the same objective: the line names them, and which of them are there
    local head, which = mob.name, ""
    if mob.also and #mob.also > 0 then
      local others = {}
      for n = 1, math.min(6, #mob.also) do others[n] = mob.also[n] end
      head = string.format("%s (also counts%s: %s%s)", mob.name, mob.what and (" for " .. mob.what) or "",
        table.concat(others, ", "), #mob.also > 6 and string.format(" and %d more", #mob.also - 6) or "")
      if s.names and #s.names > 0 then which = " (" .. table.concat(s.names, ", ") .. ")" end
    end
    if s.alive > 0 then
      out[i] = string.format("%s: %d alive in sight now%s%s", head, s.alive, which, s.marked and ", highlighted on the screen" or "")
    elseif s.near then
      out[i] = string.format("%s: %s is near but not in sight: the game has a unit of that name in its range, farther "
        .. "than nameplates reach; whether it is alive and where it stands is not known until the player is closer or "
        .. "targets it%s", head, s.near, last and ("; " .. last) or "")
    elseif blind then
      out[i] = string.format("%s: not seen, but %s, so one can be near without being seen%s", head, blind,
        last and ("; " .. last) or "")
    else
      out[i] = head .. ": none in sight now; " .. (last or "not seen since it is tracked")
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
  ns.print("looking by name:", ns.db and ns.db.lookByName == false and "turned off (Settings)"
    or T.byName == nil and "not tried yet (nothing is tracked)" or T.byName and "works" or "not available in this game")
  for _, line in ipairs(T.Section() or { "nothing is tracked" }) do ns.print(line) end
end

ns.On("PLAYER_ENTERING_WORLD", function()
  T.enteredAt = GetTime()
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
  -- a creature the player points at or targets is seen at once, nameplates or not; so is a nameplate that comes into
  -- range, and a marker does not stay on a nameplate the game has given to another unit
  local look, looking = CreateFrame("Frame"), false
  for _, e in ipairs({ "UPDATE_MOUSEOVER_UNIT", "PLAYER_TARGET_CHANGED", "NAME_PLATE_UNIT_ADDED", "NAME_PLATE_UNIT_REMOVED" }) do
    pcall(look.RegisterEvent, look, e)
  end
  look:SetScript("OnEvent", function() -- nameplates come and go in bursts: one look per frame
    if looking or #list() == 0 then return end
    looking = true
    C_Timer.After(0, function() looking = false pcall(T.Scan) end)
  end)
  -- anything of ours the game refuses is remembered, so that /fai track can say what it was
  local guard = CreateFrame("Frame")
  for _, e in ipairs({ "ADDON_ACTION_FORBIDDEN", "ADDON_ACTION_BLOCKED" }) do pcall(guard.RegisterEvent, guard, e) end
  guard:SetScript("OnEvent", function(_, event, addon, func)
    if addon == ADDON and asking then
      refused = true -- the answer to a look by name, not a fault
      return
    end
    if addon == ADDON then
      T.blocked = tostring(func)
      ns.lastError = "the game blocked " .. tostring(func)
    end
    if T.quiet and event == "ADDON_ACTION_FORBIDDEN" then -- the game's own handler is off: hand it on
      local handler = UIParent and UIParent.GetScript and UIParent:GetScript("OnEvent")
      if handler then pcall(handler, UIParent, event, addon, func) end
    end
  end)
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.tracked = T.Section
end
