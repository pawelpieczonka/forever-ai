-- ForeverAI: NPCs around the player straight from the game data (not from the screenshot):
--  * zoneNPCs — every NPC with a role in the player's zone (vendors <General Goods>, repair, trainers, innkeeper,
--    flight master, banker, auctioneer, stable master...), from Questie's database, with coordinates, nearest first;
--  * around — units the game shows nameplates for right now (mobs and NPCs in view): level, type, hostility.
local ADDON, ns = ...
local N = {}
ns.Npcs = N

local safe, clean = ns.safe, ns.clean
local PER_FRAME = 1500
local function budget() ns.Collect.Budget() end

------------------------------------------------------------------------ zone index (built once in the background)
local byZone = {}       -- areaId -> { npcId, ... } for NPCs with a role or a title
local flightIds = {}    -- npcId, ... of every flight master
local mobsByZone = {}   -- areaId -> { npcId, ... } for every other creature (mobs, critters, plain NPCs)
local zoneSpawns = {}   -- areaId -> decoded spawn points of those creatures (built on first use per zone)
N.ready, N.count = false, 0

local function questie()
  if not (QuestieLoader and QuestieLoader.ImportModule) then return nil end
  local okD, QDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "QuestieDB")
  local okZ, ZDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "ZoneDB")
  if okD and type(QDB) == "table" then return QDB, okZ and type(ZDB) == "table" and ZDB or nil end
end

local function hasBit(flags, b)
  if bit and bit.band then return bit.band(flags, b) == b end
  return math.floor(flags / b) % 2 == 1
end

-- flag bits that mean "this NPC does something for players" (vendor, trainer, repair, innkeeper, flight master...)
local function roleBits(QDB)
  local out = {}
  for name, b in pairs(QDB.npcFlags or {}) do
    if type(b) == "number" and b > 2 and name ~= "GOSSIP" and name ~= "QUEST_GIVER" then out[#out + 1] = b end
  end
  return out
end

local function hasRole(flags, bits)
  if flags <= 2 then return false end
  for _, b in ipairs(bits) do if hasBit(flags, b) then return true end end
  return false
end

function N.BuildIndex()
  if N.ready or N.building then return end
  local QDB = questie()
  if not (QDB and QDB.NPCPointers and QDB.QueryNPCSingle) then
    C_Timer.After(5, N.BuildIndex) -- Questie is still loading its database
    return
  end
  N.building = true
  local bits = roleBits(QDB)
  local ids = {}
  for id in pairs(QDB.NPCPointers) do ids[#ids + 1] = id end
  local i = 1
  local ticker
  ticker = C_Timer.NewTicker(0, function()
    local last = math.min(#ids, i + PER_FRAME - 1)
    for k = i, last do
      local id = ids[k]
      local okF, flags = pcall(QDB.QueryNPCSingle, id, "npcFlags")
      local okS, sub = pcall(QDB.QueryNPCSingle, id, "subName")
      flags = okF and tonumber(flags) or 0
      local titled = okS and type(sub) == "string" and sub ~= ""
      if QDB.npcFlags and QDB.npcFlags.FLIGHT_MASTER and hasBit(flags, QDB.npcFlags.FLIGHT_MASTER) then
        flightIds[#flightIds + 1] = id
      end
      if titled or hasRole(flags, bits) then
        local okZ, zone = pcall(QDB.QueryNPCSingle, id, "zoneID")
        local okP, spawns = pcall(QDB.QueryNPCSingle, id, "spawns")
        local zones = {}
        if okZ and tonumber(zone) and zone > 0 then zones[zone] = true end
        if okP and type(spawns) == "table" then for z in pairs(spawns) do zones[z] = true end end
        for z in pairs(zones) do
          byZone[z] = byZone[z] or {}
          table.insert(byZone[z], id)
        end
        N.count = N.count + 1
      else
        local okZ, zone = pcall(QDB.QueryNPCSingle, id, "zoneID")
        if okZ and tonumber(zone) and zone > 0 then
          mobsByZone[zone] = mobsByZone[zone] or {}
          table.insert(mobsByZone[zone], id)
        end
      end
    end
    i = last + 1
    if i > #ids then
      ticker:Cancel()
      N.ready, N.building = true, false
    end
  end)
end

local function roles(QDB, flags)
  local out = {}
  for name, b in pairs(QDB.npcFlags or {}) do
    if type(b) == "number" and b > 2 and name ~= "GOSSIP" and hasBit(flags, b) then
      out[#out + 1] = (name:lower():gsub("_", " "))
    end
  end
  table.sort(out)
  return out
end

local function playerPos(ZDB)
  local mapID = C_Map.GetBestMapForUnit("player")
  local pos = mapID and C_Map.GetPlayerMapPosition(mapID, "player")
  local area = ZDB and mapID and safe(ZDB.GetAreaIdByUiMapId, ZDB, mapID)
  return area, pos and pos.x * 100, pos and pos.y * 100, mapID
end

-- distance in meters (like TomTom); falls back to map percent when the map size is unknown
local function meters(mapID, x1, y1, x2, y2)
  local m = ns.MapMeters and ns.MapMeters(mapID, x1, y1, x2, y2)
  if m then return m, true end
  return math.floor(math.sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2) * 10 + 0.5) / 10, false
end
local function fmtDist(d, isMeters) return isMeters and (d .. " m") or (d .. "% of map") end

-- NPCs with a role in a zone (Questie data does not change): read once per zone, distances are added per question
local zoneRows = {}
local function zoneRowsFor(QDB, area)
  if zoneRows[area] then return zoneRows[area] end
  local faction = UnitFactionGroup("player") == "Horde" and "H" or "A"
  local rows = {}
  for _, id in ipairs(byZone[area] or {}) do
    budget()
    local okN, name = pcall(QDB.QueryNPCSingle, id, "name")
    local okF, friendly = pcall(QDB.QueryNPCSingle, id, "friendlyToFaction")
    if okN and name and not (okF and type(friendly) == "string" and friendly ~= "" and not friendly:find(faction, 1, true)) then
      local _, spawns = pcall(QDB.QueryNPCSingle, id, "spawns")
      local pts = {}
      for _, s in ipairs(type(spawns) == "table" and spawns[area] or {}) do
        if s[1] and s[1] > 0 then pts[#pts + 1] = s end
      end
      if #pts > 0 then
        local _, sub = pcall(QDB.QueryNPCSingle, id, "subName")
        local _, flags = pcall(QDB.QueryNPCSingle, id, "npcFlags")
        rows[#rows + 1] = { name = name, sub = type(sub) == "string" and sub ~= "" and sub or nil,
                            roles = roles(QDB, tonumber(flags) or 0), pts = pts }
      end
    end
  end
  zoneRows[area] = rows
  return rows
end

------------------------------------------------------------------------ flight paths the character knows
-- The game says which flight paths the character knows only while a flight master is open: they are remembered then,
-- with the flight master who stood at the current one (ns.char.taxi = { known = { [path] = true },
-- masters = { [flight master] = path }, at }). Paths are named "Town, Zone".
-- This client's old functions (NumTaxiNodes, TaxiNodeGetType) list every node of the continent, the ones the
-- character has never seen too, and none as "NONE": all eighteen looked known, with three on the flight map. So a path
-- is known only when the game says the character can fly there (or stands there), and what an older version of the
-- add-on remembered (no v) counts for nothing but the flight masters the character has stood at.
local TAXI_V = 2
local function taxiKnown()
  local taxi = ns.char and ns.char.taxi
  if type(taxi) ~= "table" then return nil end
  if taxi.v ~= TAXI_V then
    local known = {}
    for _, path in pairs(type(taxi.masters) == "table" and taxi.masters or {}) do known[path] = true end
    taxi = { known = known, masters = taxi.masters or {}, at = taxi.at, v = TAXI_V }
    ns.char.taxi = taxi
  end
  return taxi
end

local function readTaxi()
  if not ns.char then return end
  local old = taxiKnown()
  local known, masters, seen, current = {}, (old and old.masters) or {}, {}, nil
  local mapID = GetTaxiMapID and safe(GetTaxiMapID)
  local nodes = mapID and C_TaxiMap and C_TaxiMap.GetAllTaxiNodes and safe(C_TaxiMap.GetAllTaxiNodes, mapID)
  local state = Enum and Enum.FlightPathState
  if type(nodes) == "table" and #nodes > 0 and type(state) == "table" then
    for _, node in ipairs(nodes) do
      local name = type(node) == "table" and clean(node.name)
      if name then
        seen[#seen + 1] = name .. "=" .. tostring(node.state)
        if node.state == state.Current or node.state == state.Reachable then known[name] = true end
        if node.state == state.Current then current = name end
      end
    end
  elseif NumTaxiNodes and TaxiNodeName and TaxiNodeGetType then
    for i = 1, (tonumber((safe(NumTaxiNodes))) or 0) do
      local name, kind = clean((safe(TaxiNodeName, i))), (safe(TaxiNodeGetType, i))
      if name then
        seen[#seen + 1] = name .. "=" .. tostring(kind)
        if kind == "CURRENT" or kind == "REACHABLE" then known[name] = true end
        if kind == "CURRENT" then current = name end
      end
    end
  end
  local npc = current and clean((safe(UnitName, "npc")))
  if npc then masters[npc] = current end
  if ns.Log then ns.Log("taxi", string.format("flight map %s: %s", tostring(mapID), table.concat(seen, ", "))) end
  if next(known) then
    -- a flight map shows one continent: what is known elsewhere stays known
    for path in pairs((old and old.known) or {}) do
      local shown = false
      for _, line in ipairs(seen) do
        if line:sub(1, #path + 1) == path .. "=" then shown = true break end
      end
      if not shown then known[path] = true end
    end
    ns.char.taxi = { known = known, masters = masters, at = time(), v = TAXI_V }
  end
end
ns.On("TAXIMAP_OPENED", function() pcall(readTaxi) end)

-- Has the character discovered this flight master's path? "known", "not discovered", or nil when the add-on cannot
-- tell: no flight master opened yet, or a zone with more than one flight master and a known path in it.
-- the character's flight paths that lie in a zone, named as the flight map shows them
local function pathsIn(taxi, zoneName)
  -- a path is named "Town, Zone", the zone often in short ("Refuge Pointe, Arathi", "Stormwind, Elwynn",
  -- "Ironforge, Dun Morogh"): it lies in the flight master's zone when either name begins the zone's
  local zone, found = tostring(zoneName or ""):lower(), {}
  for path in pairs(taxi.known or {}) do
    local town, where = path:lower():match("^(.-),%s*(.+)$")
    town, where = town or path:lower(), where or ""
    if zone ~= "" and (zone:sub(1, #where) == where and where ~= "" or zone:sub(1, #town) == town
        or where:sub(1, #zone) == zone) then
      found[#found + 1] = path
    end
  end
  return found
end

function N.FlightState(name, zoneName, mastersInZone)
  local taxi = taxiKnown()
  if not taxi then return nil end
  if taxi.masters and taxi.masters[name] then return "known" end
  if #pathsIn(taxi, zoneName) == 0 then return "not discovered" end
  if mastersInZone == 1 then return "known" end
end

-- The name of a flight master's path on the flight map ("Sentinel Hill, Westfall"): what the player picks there, where
-- the flight master's own name is nowhere to be seen. Known for the ones the character has stood at, and for the only
-- flight master of a zone with one known path; nil otherwise.
function N.FlightPath(name, zoneName, mastersInZone)
  local taxi = taxiKnown()
  if not taxi then return nil end
  if taxi.masters and taxi.masters[name] then return taxi.masters[name] end
  local found = pathsIn(taxi, zoneName)
  if #found == 1 and mastersInZone == 1 then return found[1] end
end

-- The flight masters of the player's faction in every zone of the player's continent, with whether the character
-- knows their flight path: a route that goes on into another zone passes them too. Of the other continents only the
-- ones the character knows, marked "(on Kalimdor)": a route that crosses the sea flies on from where the boat lands.
function N.FlightMasters()
  local QDB, ZDB = questie()
  if not (QDB and N.ready) then return nil end
  local function continent(mapID)
    for _ = 1, 10 do
      local info = mapID and C_Map.GetMapInfo(mapID)
      if not info then return nil end
      if Enum and Enum.UIMapType and info.mapType == Enum.UIMapType.Continent then return mapID end
      mapID = info.parentMapID ~= 0 and info.parentMapID or nil
    end
  end
  local here = continent(C_Map.GetBestMapForUnit("player"))
  local faction = UnitFactionGroup("player") == "Horde" and "H" or "A"
  local rows, perZone = {}, {}
  for _, id in ipairs(flightIds) do
    local okN, name = pcall(QDB.QueryNPCSingle, id, "name")
    local okF, friendly = pcall(QDB.QueryNPCSingle, id, "friendlyToFaction")
    if okN and name and not (okF and type(friendly) == "string" and friendly ~= "" and not friendly:find(faction, 1, true)) then
      local _, sub = pcall(QDB.QueryNPCSingle, id, "subName")
      local _, spawns = pcall(QDB.QueryNPCSingle, id, "spawns")
      for area, pts in pairs(type(spawns) == "table" and spawns or {}) do
        local p = type(pts) == "table" and pts[1]
        local mapID = ZDB and ZDB.GetUiMapIdByAreaId and safe(ZDB.GetUiMapIdByAreaId, ZDB, area)
        local zone = C_Map.GetAreaInfo and C_Map.GetAreaInfo(area)
        if p and p[1] and p[1] > 0 and zone then
          local land = here and mapID and continent(mapID) or here
          local info = land and land ~= here and C_Map.GetMapInfo(land)
          rows[#rows + 1] = { name = name, sub = type(sub) == "string" and sub ~= "" and sub or nil, zone = zone, x = p[1], y = p[2],
                              far = land ~= here, land = info and info.name or nil }
          perZone[zone] = (perZone[zone] or 0) + 1
        end
      end
    end
  end
  table.sort(rows, function(a, b) return a.zone < b.zone or (a.zone == b.zone and a.name < b.name) end)
  local out = {}
  for _, r in ipairs(rows) do
    local state = N.FlightState(r.name, r.zone, perZone[r.zone]) or "not known yet"
    local path = state == "known" and N.FlightPath(r.name, r.zone, perZone[r.zone])
    if path then state = state .. ": " .. path end -- "flight path known: Sentinel Hill, Westfall"
    if not r.far or path or state == "known" then
      out[#out + 1] = string.format("%s%s [flight path %s] — %s %.1f, %.1f%s", r.name, r.sub and (" <" .. r.sub .. ">") or "",
        state, r.zone, r.x, r.y, r.far and (" (on " .. (r.land or "another continent") .. ")") or "")
    end
  end
  return #out > 0 and out or nil
end

-- NPCs with a role in the player's zone, nearest first
function N.ZoneNPCs(limit)
  local QDB, ZDB = questie()
  if not QDB then return nil end
  if not N.ready then
    N.BuildIndex()
    return { "(NPC index is still loading)" }
  end
  local area, px, py, mapID = playerPos(ZDB)
  if not (area and byZone[area]) then return nil end
  local rows = {}
  for _, z in ipairs(zoneRowsFor(QDB, area)) do
    local best
    for _, s in ipairs(z.pts) do
      local d, isM = 999999, false
      if px then d, isM = meters(mapID, px, py, s[1], s[2]) end
      if not best or d < best.d then best = { x = s[1], y = s[2], d = d, m = isM } end
    end
    rows[#rows + 1] = { d = best.d, m = best.m, name = z.name, sub = z.sub, roles = z.roles, x = best.x, y = best.y }
  end
  -- in a town the nearest few dozen were guards, recruiters and emissaries with a title and no role: there the
  -- list is for what an NPC does (the quest givers come with the quests)
  if IsResting and IsResting() then
    local useful = {}
    for _, r in ipairs(rows) do
      if #r.roles > 0 then useful[#useful + 1] = r end
    end
    rows = useful
  end
  table.sort(rows, function(a, b) return a.d < b.d end)
  local zoneName = C_Map.GetAreaInfo and C_Map.GetAreaInfo(area) or ""
  local masters = 0
  for _, r in ipairs(rows) do
    if tContains(r.roles, "flight master") then masters = masters + 1 end
  end
  local out = {}
  for k = 1, math.min(limit or 45, #rows) do
    local r = rows[k]
    local roles = r.roles
    local state = tContains(roles, "flight master") and N.FlightState(r.name, zoneName, masters)
    local path = state == "known" and N.FlightPath(r.name, zoneName, masters)
    if path then state = state .. ": " .. path end
    if state then -- whether the character has this flight path, and its name on the flight map
      roles = { unpack(r.roles) }
      for i, role in ipairs(roles) do
        if role == "flight master" then roles[i] = "flight master, flight path " .. state end
      end
    end
    out[k] = string.format("%s%s%s — %s %.1f, %.1f (%s)", r.name, r.sub and (" <" .. r.sub .. ">") or "",
      #roles > 0 and (" [" .. table.concat(roles, ", ") .. "]") or "", zoneName, r.x, r.y, fmtDist(r.d, r.m))
    if ns.Collect and ns.Collect.npcCoords then ns.Collect.npcCoords[r.name] = { zone = zoneName, x = r.x, y = r.y } end
  end
  return out
end

------------------------------------------------------------------------ the services this character uses
-- The nearest of each in the player's zone: the trainer of their class, of their professions and (a hunter) of their
-- pet, the stable master, repair, ammunition, general goods, innkeeper, bank, auctioneer, flight master. The list of
-- every NPC with a role is cut to the nearest few dozen: in a city the hunter trainer is rarely among them.
local CRAFT = { leatherworking = "leatherwork", skinning = "skinn", cooking = "cook", ["first aid"] = "physician",
  fishing = "fish", tailoring = "tailor", blacksmithing = "smith", engineering = "engineer", alchemy = "alchem",
  herbalism = "herbalis", mining = "min", enchanting = "enchant" }

function N.Services()
  local QDB, ZDB = questie()
  if not (QDB and N.ready) then return nil end
  local area, px, py, mapID = playerPos(ZDB)
  if not (area and byZone[area]) then return nil end
  local class, classFile = UnitClass("player")
  class = type(class) == "string" and class:lower() or ""
  local kinds = { { class .. " trainer", function(sub) return sub == class .. " trainer" end } }
  if classFile == "HUNTER" then
    kinds[#kinds + 1] = { "pet trainer", function(sub) return sub == "pet trainer" end }
    kinds[#kinds + 1] = { "stable master", function(sub, has) return has.stablemaster or sub == "stable master" end }
    kinds[#kinds + 1] = { "ammunition", function(sub, has)
      return has.vendor and (sub:find("bow", 1, true) or sub:find("gun", 1, true) or sub:find("fletcher", 1, true)
        or sub:find("ammunition", 1, true)) ~= nil end }
  end
  for _, line in ipairs(ns.Collect and ns.Collect.sections.skills and ns.Collect.sections.skills() or {}) do
    local craft = tostring(line):match("^(.-) %d+/%d+$")
    local stem = craft and CRAFT[craft:lower()]
    if stem then
      kinds[#kinds + 1] = { craft:lower() .. " trainer", function(sub, has)
        if not has.trainer then return false end
        return sub:find(stem, 1, true) ~= nil or (stem == "physician" and sub:find("first aid", 1, true) ~= nil)
      end }
    end
  end
  for _, role in ipairs({ "repair", "innkeeper", "banker", "auctioneer", "flight master" }) do
    kinds[#kinds + 1] = { role, function(_, has) return has[role] end }
  end
  kinds[#kinds + 1] = { "general goods", function(sub, has) return has.vendor and sub:find("general goods", 1, true) ~= nil end }
  local zoneName = C_Map.GetAreaInfo and C_Map.GetAreaInfo(area) or ""
  local out = {}
  for _, kind in ipairs(kinds) do
    local best
    for _, z in ipairs(zoneRowsFor(QDB, area)) do
      local has = {}
      for _, role in ipairs(z.roles) do has[role] = true end
      if kind[2]((z.sub or ""):lower(), has) then
        for _, s in ipairs(z.pts) do
          local d, isM = 999999, false
          if px then d, isM = meters(mapID, px, py, s[1], s[2]) end
          if not best or d < best.d then best = { z = z, x = s[1], y = s[2], d = d, m = isM } end
        end
      end
    end
    if best then
      local state = kind[1] == "flight master" and N.FlightState(best.z.name, zoneName, 1)
      out[#out + 1] = string.format("%s: %s%s%s — %s %.1f, %.1f%s", kind[1], best.z.name,
        best.z.sub and (" <" .. best.z.sub .. ">") or "", state and (" [flight path " .. state .. "]") or "", zoneName,
        best.x, best.y, px and (" (" .. fmtDist(best.d, best.m) .. ")") or "")
      if ns.Collect and ns.Collect.npcCoords then
        ns.Collect.npcCoords[best.z.name] = { zone = zoneName, x = best.x, y = best.y }
      end
    end
  end
  return #out > 0 and out or nil
end

------------------------------------------------------------------------ creatures around the player (game database)
-- Works without nameplates: Questie's database knows where every creature spawns, so the mobs whose spawn points are
-- around the player's position are listed with level, rank, hostility and distance.
local RANK = { [1] = "elite", [2] = "rare elite", [3] = "boss", [4] = "rare" }

local function spawnsOf(QDB, area)
  if zoneSpawns[area] then return zoneSpawns[area] end
  local list = {}
  for _, id in ipairs(mobsByZone[area] or {}) do
    budget()
    local okP, spawns = pcall(QDB.QueryNPCSingle, id, "spawns")
    local pts = okP and type(spawns) == "table" and spawns[area]
    if type(pts) == "table" and #pts > 0 then
      local _, name = pcall(QDB.QueryNPCSingle, id, "name")
      local _, minL = pcall(QDB.QueryNPCSingle, id, "minLevel")
      local _, maxL = pcall(QDB.QueryNPCSingle, id, "maxLevel")
      local _, rank = pcall(QDB.QueryNPCSingle, id, "rank")
      local _, factionID = pcall(QDB.QueryNPCSingle, id, "factionID")
      if name then
        -- faction template -> enemy group mask: 1 = hostile to all players, 2 = to Alliance, 4 = to Horde
        local enemy = QDB.factionTemplate and tonumber(factionID) and QDB.factionTemplate[tonumber(factionID)]
        list[#list + 1] = { id = id, name = name, minL = tonumber(minL), maxL = tonumber(maxL), rank = tonumber(rank),
                            enemy = enemy, pts = pts }
      end
    end
  end
  zoneSpawns[area] = list
  return list
end

-- creatures that matter for the player's unfinished quests: kill targets, kill credit, and who drops quest items
-- (looked up again only when the unfinished objectives change)
local targetsKey, targetsByNpc, targetsText
local function questTargets(QDB)
  local byNpc, text = {}, {}
  if not (C_QuestLog and C_QuestLog.GetNumQuestLogEntries) then return byNpc, text end
  local open, keyParts = {}, {}
  for i = 1, C_QuestLog.GetNumQuestLogEntries() do
    local q = C_QuestLog.GetInfo(i)
    if q and not q.isHeader and q.questID and not (C_QuestLog.IsComplete and C_QuestLog.IsComplete(q.questID)) then
      open[#open + 1] = q
      keyParts[#keyParts + 1] = q.questID
      for _, ob in ipairs(C_QuestLog.GetQuestObjectives(q.questID) or {}) do
        if ob.text and not ob.finished then
          text[#text + 1] = { t = ob.text:lower(), quest = q.title }
          keyParts[#keyParts + 1] = ob.text
        end
      end
    end
  end
  local key = table.concat(keyParts, "|")
  if key == targetsKey then return targetsByNpc, targetsText end
  local function mark(npcId, title) if tonumber(npcId) and not byNpc[tonumber(npcId)] then byNpc[tonumber(npcId)] = title end end
  for _, q in ipairs(open) do
    budget()
    do
      local okO, objs = pcall(QDB.QueryQuestSingle, q.questID, "objectives")
      if okO and type(objs) == "table" then
        for _, o in ipairs(type(objs[1]) == "table" and objs[1] or {}) do mark(type(o) == "table" and o[1] or o, q.title) end
        for _, o in ipairs(type(objs[3]) == "table" and objs[3] or {}) do
          local itemId = type(o) == "table" and o[1] or o
          local okD, drops = pcall(QDB.QueryItemSingle, itemId, "npcDrops")
          for _, npcId in ipairs(okD and type(drops) == "table" and drops or {}) do mark(npcId, q.title) end
        end
        local kc = objs[5]
        if type(kc) == "table" then
          for _, entry in ipairs(type(kc[1]) == "table" and kc or { kc }) do
            if type(entry) == "table" and type(entry[1]) == "table" then
              for _, npcId in ipairs(entry[1]) do mark(npcId, q.title) end
            end
          end
        end
      end
    end
  end
  targetsKey, targetsByNpc, targetsText = key, byNpc, text
  return byNpc, text
end

-- (a capital has no creatures to fight: the database lists its children, guards and festival visitors there)
local CAPITALS = { [1453] = true, [1454] = true, [1455] = true, [1456] = true, [1457] = true, [1458] = true }
-- Is there an NPC at this place of a map, somebody to deal with (a vendor, a trainer, a flight master, a banker, a
-- quest giver)? Its name; false when nobody stands there (a cave mouth, a crossing, a creature to kill); nil while
-- the NPC data is still being read (ask again). ``title``: what the stop is called: an NPC named in it counts from a
-- little further off, and a creature without a role or a title counts only when it is named and not hostile.
local AT_UNITS, NAMED_UNITS = 0.4, 1.5
function N.NpcAt(mapID, x, y, title)
  local QDB, ZDB = questie()
  if not QDB then return false end
  if not N.ready then
    N.BuildIndex()
    return nil
  end
  local area = ZDB and mapID and safe(ZDB.GetAreaIdByUiMapId, ZDB, mapID)
  if not (area and x and y) then return false end
  local low = tostring(title or ""):lower()
  local best, bestD
  local function try(name, pts, mustBeNamed)
    local named = low ~= "" and low:find(tostring(name):lower(), 1, true) ~= nil
    if mustBeNamed and not named then return end
    for _, s in ipairs(pts) do
      if s[1] and s[2] then
        local dx, dy = math.abs(s[1] - x), math.abs(s[2] - y)
        local reach = named and NAMED_UNITS or AT_UNITS
        if dx <= reach and dy <= reach then
          local d = dx + dy - (named and 10 or 0)
          if not bestD or d < bestD then best, bestD = name, d end
        end
      end
    end
  end
  for _, z in ipairs(zoneRowsFor(QDB, area)) do try(z.name, z.pts, false) end
  if not best then
    local playerBit = UnitFactionGroup("player") == "Horde" and 4 or 2
    for _, m in ipairs(spawnsOf(QDB, area)) do
      local hostile = m.enemy and (hasBit(m.enemy, 1) or hasBit(m.enemy, playerBit))
      if not hostile then try(m.name, m.pts, true) end
    end
  end
  return best or false
end

function N.NearbyMobs(radius, limit)
  local QDB, ZDB = questie()
  if not QDB then return nil end
  if not N.ready then N.BuildIndex() return nil end
  local area, px, py, mapID = playerPos(ZDB)
  if not (area and px) or CAPITALS[mapID] then return nil end
  radius = radius or 400 -- meters
  local playerBit = UnitFactionGroup("player") == "Horde" and 4 or 2
  local targets, objectives = questTargets(QDB)
  local rows = {}
  for _, m in ipairs(spawnsOf(QDB, area)) do
    budget()
    local best, near = nil, 0
    local isM = true
    for _, p in ipairs(m.pts) do
      if p[1] and p[1] > 0 then
        local d, inMeters = meters(mapID, px, py, p[1], p[2])
        isM = inMeters
        local limit = inMeters and radius or 8
        if d <= limit then near = near + 1 end
        if not best or d < best.d then best = { d = d, x = p[1], y = p[2] } end
      end
    end
    if best and best.d <= (isM and radius or 8) then
      local quest = targets[m.id]
      if not quest then
        local lname = m.name:lower()
        for _, o in ipairs(objectives) do if o.t:find(lname, 1, true) then quest = o.quest break end end
      end
      rows[#rows + 1] = { m = m, d = best.d, isM = isM, x = best.x, y = best.y, near = near, quest = quest }
    end
  end
  table.sort(rows, function(a, b)
    if (a.quest ~= nil) ~= (b.quest ~= nil) then return a.quest ~= nil end -- quest targets first
    return a.d < b.d
  end)
  local out = {}
  for k = 1, math.min(limit or 20, #rows) do
    local r, m = rows[k], rows[k].m
    local lvl = m.minL and (m.maxL and m.maxL ~= m.minL and (m.minL .. "-" .. m.maxL) or tostring(m.minL)) or "?"
    local attitude = "?"
    if m.enemy then attitude = (hasBit(m.enemy, 1) or hasBit(m.enemy, playerBit)) and "hostile" or "not hostile" end
    out[k] = string.format("%s, level %s%s, %s — nearest %.1f, %.1f (%s, %d spawn point%s within %s)%s",
      m.name, lvl, RANK[m.rank or 0] and (", " .. RANK[m.rank]) or "", attitude, r.x, r.y, fmtDist(r.d, r.isM), r.near,
      r.near == 1 and "" or "s",
      r.isM and (radius .. " m") or "8% of map", r.quest and (" [quest target: " .. r.quest .. "]") or "")
  end
  return #out > 0 and out or nil
end

------------------------------------------------------------------------ units in view (nameplates)
local REACTION = { "hostile", "hostile", "unfriendly", "neutral", "friendly", "friendly", "friendly", "friendly" }

function N.Around(limit)
  if not (C_NamePlate and C_NamePlate.GetNamePlates) then return nil end
  local QDB = questie()
  local out, seen = {}, {}
  for _, plate in ipairs(safe(C_NamePlate.GetNamePlates) or {}) do
    local unit = plate.namePlateUnitToken or (plate.UnitFrame and plate.UnitFrame.unit)
    if unit and not safe(UnitIsPlayer, unit) then
      local name = clean(safe(UnitName, unit))
      if name then
        local level = clean(safe(UnitLevel, unit))
        local reaction = clean(safe(UnitReaction, unit, "player"))
        local ctype = clean(safe(UnitCreatureType, unit))
        local class = clean(safe(UnitClassification, unit))
        local dead = clean(safe(UnitIsDead, unit))
        local guid = clean(safe(UnitGUID, unit))
        local npcId = guid and tonumber((select(6, strsplit("-", guid))))
        local sub
        if QDB and npcId then
          local okS, s = pcall(QDB.QueryNPCSingle, npcId, "subName")
          if okS and type(s) == "string" and s ~= "" then sub = s end
        end
        local key = name .. tostring(level)
        seen[key] = (seen[key] or 0) + 1
        if seen[key] == 1 then
          out[#out + 1] = { name = name, sub = sub, level = level, reaction = reaction, ctype = ctype, class = class,
                            dead = dead, n = 1 }
        else
          for _, e in ipairs(out) do if e.name .. tostring(e.level) == key then e.n = e.n + 1 end end
        end
      end
    end
  end
  local lines = {}
  for k = 1, math.min(limit or 25, #out) do
    local e = out[k]
    lines[k] = string.format("%s%s%s, level %s, %s%s%s%s", e.n > 1 and (e.n .. "× ") or "", e.name,
      e.sub and (" <" .. e.sub .. ">") or "", tostring(e.level or "?"), REACTION[e.reaction or 4] or "?",
      e.ctype and (", " .. e.ctype) or "", (e.class and e.class ~= "normal") and (", " .. e.class) or "",
      e.dead and ", dead" or "")
  end
  if #lines == 0 then return nil end
  return lines
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.zoneNPCs = function() return N.ZoneNPCs(45) end
  ns.Collect.sections.flightMasters = function() return N.FlightMasters() end
  ns.Collect.sections.services = function() return N.Services() end
  ns.Collect.sections.around = function() return N.Around(25) end
  ns.Collect.sections.nearbyMobs = function() return N.NearbyMobs(400, 20) end
end

-- Is the NPC data on its way: being read now, or the quest database is there and may still be loading its own data
-- (the first while after a loading screen)? A question asked then waits for it. With no quest database, or one that
-- never comes up, nothing waits.
local LOADING_S = 40
local loadedAt
function N.Loading()
  if N.ready then return false end
  if N.building then return true end
  return QuestieLoader ~= nil and GetTime() - (loadedAt or GetTime()) < LOADING_S
end

local f = CreateFrame("Frame")
f:RegisterEvent("PLAYER_LOGIN")
f:SetScript("OnEvent", function()
  loadedAt = GetTime()
  C_Timer.After(2, N.BuildIndex) -- (soon: the first question after login needs it)
end)
