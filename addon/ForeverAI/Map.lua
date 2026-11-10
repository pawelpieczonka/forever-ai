-- ForeverAI: map markers requested by the AI (TomTom arrow when installed, otherwise the game's own waypoint).
-- The marks are kept per character: they come back after a /reload (the game and TomTom forget them), a stop leaves
-- the list when the player reaches it, and what is left goes to the AI with every question (section "marks").
local ADDON, ns = ...
local M = {}
ns.Map = M
M.uids = {}          -- mark (a table of the saved list) -> its TomTom waypoint
local REACHED_M = 12 -- a stop this close is done

local function rootMap()
  local id = C_Map.GetBestMapForUnit("player") or (C_Map.GetFallbackWorldMapID and C_Map.GetFallbackWorldMapID())
  for _ = 1, 10 do
    local info = id and C_Map.GetMapInfo(id)
    if not info or not info.parentMapID or info.parentMapID == 0 then return id end
    id = info.parentMapID
  end
  return id
end

local cache = {}
function M.FindMap(zone)
  local current = C_Map.GetBestMapForUnit("player")
  if not zone or zone == "" then return current end
  local key = zone:lower()
  if cache[key] ~= nil then return cache[key] or nil end
  local id = current
  for _ = 1, 10 do -- the current map or one of its parents
    local info = id and C_Map.GetMapInfo(id)
    if not info then break end
    if info.name and info.name:lower() == key then cache[key] = id return id end
    id = (info.parentMapID and info.parentMapID ~= 0) and info.parentMapID or nil
  end
  local root = rootMap()
  local found
  for _, m in ipairs((root and ns.safe(C_Map.GetMapChildrenInfo, root, nil, true)) or {}) do
    if m.name and m.name:lower() == key then
      if not found or (Enum.UIMapType and m.mapType == Enum.UIMapType.Zone) then found = m.mapID end
    end
  end
  cache[key] = found or false
  return found
end

-- the marks on the map now, in walking order: { title, x, y (0-100), mapID, zone, mob (the creature to find there) }
local function list()
  if not ns.char then return {} end
  ns.char.marks = ns.char.marks or {}
  return ns.char.marks
end

local function usesTomTom() return TomTom and TomTom.AddWaypoint end

-- the window shows how many stops are marked
local function changed()
  if ns.UI and ns.UI.UpdateStrip then pcall(ns.UI.UpdateStrip) end
end

function M.Count()
  return ns.char and ns.char.marks and #ns.char.marks or 0
end

-- the arrow leads to the first stop
local function point(mk)
  local uid = mk and M.uids[mk]
  if uid and TomTom and TomTom.SetCrazyArrow then
    pcall(TomTom.SetCrazyArrow, TomTom, uid, 10, "ForeverAI: " .. tostring(mk.title))
  elseif mk and not usesTomTom() and C_Map.SetUserWaypoint and UiMapPoint then
    local okSet = pcall(C_Map.SetUserWaypoint, UiMapPoint.CreateFromCoordinates(mk.mapID, mk.x / 100, mk.y / 100))
    if okSet and C_SuperTrack and C_SuperTrack.SetSuperTrackedUserWaypoint then
      pcall(C_SuperTrack.SetSuperTrackedUserWaypoint, true)
    end
  end
end

local function place(mk, first)
  if usesTomTom() then
    local ok, uid = pcall(TomTom.AddWaypoint, TomTom, mk.mapID, mk.x / 100, mk.y / 100,
      { title = "ForeverAI: " .. tostring(mk.title), persistent = false, minimap = true, world = true, crazy = first })
    if ok and uid then M.uids[mk] = uid end
  elseif first then
    point(mk)
  end
end

local function unplace(mk)
  local uid = M.uids[mk]
  if uid and TomTom and TomTom.RemoveWaypoint then pcall(TomTom.RemoveWaypoint, TomTom, uid) end
  M.uids[mk] = nil
end

-- adds the marks to the ones on the map (UI.MarksCard clears first unless the answer adds stops)
function M.Apply(marks)
  local placed, saved = {}, list()
  for _, mk in ipairs(marks or {}) do
    local mapID = M.FindMap(mk.zone)
    if mapID then
      local info = C_Map.GetMapInfo(mapID)
      local e = { title = tostring(mk.title), x = tonumber(mk.x) or 0, y = tonumber(mk.y) or 0, mapID = mapID,
        zone = info and info.name or mk.zone, mob = type(mk.mob) == "string" and mk.mob or nil }
      saved[#saved + 1] = e
      place(e, #saved == 1)
      placed[#placed + 1] = e
    end
  end
  M.last = placed
  changed()
  return placed, usesTomTom() and "TomTom" or "map pin"
end

function M.Clear()
  for _, mk in ipairs(list()) do unplace(mk) end
  wipe(M.uids)
  if ns.char then ns.char.marks, ns.char.marksRemoved = {}, nil end
  if C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
  changed()
end

-- After login, /reload or a loading screen the saved marks are on the map once: what this session already placed
-- is taken off first (the event also comes with every boat, portal and instance).
function M.Restore()
  local saved = list()
  for _, mk in ipairs(saved) do unplace(mk) end
  wipe(M.uids)
  for i, mk in ipairs(saved) do place(mk, i == 1) end
end

-- where the player is, asked once for all marks: map, x, y (0-100)
local function playerSpot()
  local mapID = C_Map.GetBestMapForUnit("player")
  local pos = mapID and C_Map.GetPlayerMapPosition(mapID, "player")
  if pos then return mapID, pos.x * 100, pos.y * 100 end
end

local function metersTo(mk, mapID, px, py)
  if not px or mapID ~= mk.mapID then return nil end
  return ns.MapMeters(mapID, px, py, mk.x, mk.y)
end

-- a stop the player has reached leaves the list; the arrow moves on to the first stop that is left
function M.Check()
  local saved = ns.char and ns.char.marks
  if not saved or #saved == 0 then return end
  local mapID, px, py = playerSpot()
  if not px then return end
  local reached = false
  for i = #saved, 1, -1 do
    local d = metersTo(saved[i], mapID, px, py)
    if d and d <= REACHED_M then
      unplace(saved[i])
      table.remove(saved, i)
      reached = true
    end
  end
  if reached then
    changed()
    if saved[1] then point(saved[1]) elseif not usesTomTom() and C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
  end
end

------------------------------------------------------------------------ changing the route
-- The stops are numbered as they stand now, whatever number their title was given when they were marked.
local function bare(title) return (tostring(title):gsub("^%d+[%.%)]%s*", "")) end

-- after a change the waypoints and the arrow follow the list
local function refresh()
  M.Restore()
  if #list() == 0 and not usesTomTom() and C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
  changed()
end

-- a stop that is gone takes the watch for its creature with it, unless another stop is for the same creature
local function untrack(mk)
  if not ns.Track then return end
  local name = (mk.mob or bare(mk.title)):lower()
  for _, other in ipairs(list()) do
    if (other.mob or bare(other.title)):lower() == name then return end
  end
  ns.Track.Stop(name)
end

-- the stop with this title (its number does not count) and, when given, this place; looked for from ``from`` on
local function find(saved, want, from)
  if type(want) ~= "table" then return nil end
  local key, x, y = bare(want.title or ""):lower(), tonumber(want.x), tonumber(want.y)
  for i = from or 1, #saved do
    local mk = saved[i]
    if bare(mk.title):lower() == key and (not x or not y or (math.abs(mk.x - x) < 0.06 and math.abs(mk.y - y) < 0.06)) then
      return i
    end
  end
end

-- Take stop i off the route. by == "player": the AI is told (section "marks"), so that it does not put it back.
function M.Remove(i, by)
  local saved = list()
  local mk = saved[i]
  if not mk then return end
  unplace(mk)
  table.remove(saved, i)
  if by == "player" then
    local gone = ns.char.marksRemoved or {}
    gone[#gone + 1] = bare(mk.title)
    while #gone > 10 do table.remove(gone, 1) end
    ns.char.marksRemoved = gone
  end
  untrack(mk)
  refresh()
end

-- the player goes to stop i next: it moves to the top, the others keep their order
function M.Next(i)
  local saved = list()
  if not saved[i] or i <= 1 then return end
  table.insert(saved, 1, table.remove(saved, i))
  refresh()
end

-- The AI's change to the route (its edit_route tool): { clear = true }, or { remove = { { title, x, y } ... },
-- order = { { title, x, y } ... } } with the stops as the "marks" section named them. Stops not named in ``order``
-- follow in the order they had.
function M.Edit(edit)
  if type(edit) ~= "table" then return end
  if edit.clear then M.Clear() return end
  local saved = list()
  for _, want in ipairs(type(edit.remove) == "table" and edit.remove or {}) do
    local i = find(saved, want)
    if i then
      local mk = table.remove(saved, i)
      unplace(mk)
      untrack(mk)
    end
  end
  local at = 1
  for _, want in ipairs(type(edit.order) == "table" and edit.order or {}) do
    local i = find(saved, want, at)
    if i then
      table.insert(saved, at, table.remove(saved, i))
      at = at + 1
    end
  end
  refresh()
end

-- for the window: the stops in order, { title, zone, x, y, meters (nil in another zone) }
function M.List()
  local out = {}
  local mapID, px, py = playerSpot()
  for i, mk in ipairs(list()) do
    out[i] = { title = bare(mk.title), zone = mk.zone, x = mk.x, y = mk.y, meters = metersTo(mk, mapID, px, py) }
  end
  return out
end

-- for the AI: what is marked now, in order, with the distance when the stop is in the player's zone, and the stops
-- the player took off themselves
function M.Section()
  local saved = list()
  if #saved == 0 then return nil end
  local out = {}
  local mapID, px, py = playerSpot()
  for i, mk in ipairs(saved) do
    local d = metersTo(mk, mapID, px, py)
    out[i] = string.format("%d. %s — %s %.1f, %.1f%s", i, bare(mk.title), tostring(mk.zone or "?"), mk.x, mk.y,
      d and string.format(" (%d m)", d) or "")
  end
  local gone = ns.char and ns.char.marksRemoved
  if gone and #gone > 0 then out[#out + 1] = "Taken off the route by the player: " .. table.concat(gone, ", ") end
  return out
end

function M.Show(mapID)
  if C_Map.OpenWorldMap then
    pcall(C_Map.OpenWorldMap, mapID)
  elseif WorldMapFrame then
    if not WorldMapFrame:IsShown() and ToggleWorldMap then pcall(ToggleWorldMap) end
    if mapID then pcall(WorldMapFrame.SetMapID, WorldMapFrame, mapID) end
  end
end

ns.On("PLAYER_ENTERING_WORLD", function()
  C_Timer.After(2, M.Restore) -- TomTom is ready by then
  if not M.ticker then M.ticker = C_Timer.NewTicker(2, function() pcall(M.Check) end) end
end)

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.marks = M.Section
end
