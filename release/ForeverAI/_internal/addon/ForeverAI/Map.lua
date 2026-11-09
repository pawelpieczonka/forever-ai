-- ForeverAI: map markers requested by the AI (TomTom arrow when installed, otherwise the game's own waypoint)
local ADDON, ns = ...
local M = {}
ns.Map = M
M.uids = {}

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

function M.Apply(marks)
  local placed = {}
  local tomtom = TomTom and TomTom.AddWaypoint
  for i, mk in ipairs(marks or {}) do
    local mapID = M.FindMap(mk.zone)
    if mapID then
      local x, y = (tonumber(mk.x) or 0) / 100, (tonumber(mk.y) or 0) / 100
      if tomtom then
        local ok, uid = pcall(TomTom.AddWaypoint, TomTom, mapID, x, y,
          { title = "ForeverAI: " .. tostring(mk.title), persistent = false, minimap = true, world = true, crazy = i == 1 })
        if ok and uid then M.uids[#M.uids + 1] = uid end
      elseif i == 1 and C_Map.SetUserWaypoint and UiMapPoint then
        local okSet = pcall(C_Map.SetUserWaypoint, UiMapPoint.CreateFromCoordinates(mapID, x, y))
        if okSet and C_SuperTrack and C_SuperTrack.SetSuperTrackedUserWaypoint then
          pcall(C_SuperTrack.SetSuperTrackedUserWaypoint, true)
        end
      end
      local info = C_Map.GetMapInfo(mapID)
      placed[#placed + 1] = { title = mk.title, x = mk.x, y = mk.y, mapID = mapID, zone = info and info.name or mk.zone }
    end
  end
  M.last = placed
  return placed, tomtom and "TomTom" or "map pin"
end

function M.Clear()
  if TomTom and TomTom.RemoveWaypoint then
    for _, uid in ipairs(M.uids) do pcall(TomTom.RemoveWaypoint, TomTom, uid) end
  end
  wipe(M.uids)
  if C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
end

function M.Show(mapID)
  if C_Map.OpenWorldMap then
    pcall(C_Map.OpenWorldMap, mapID)
  elseif WorldMapFrame then
    if not WorldMapFrame:IsShown() and ToggleWorldMap then pcall(ToggleWorldMap) end
    if mapID then pcall(WorldMapFrame.SetMapID, WorldMapFrame, mapID) end
  end
end
