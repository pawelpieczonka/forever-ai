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

-- the map of this name (lower case) the player is on: their map or one of the maps it lies in; nil when on neither
local function mapAround(key)
  local id = C_Map.GetBestMapForUnit("player")
  for _ = 1, 10 do
    local info = id and C_Map.GetMapInfo(id)
    if not info then return nil end
    if info.name and info.name:lower() == key then return id end
    id = (info.parentMapID and info.parentMapID ~= 0) and info.parentMapID or nil
  end
end

-- the first ``n`` bytes of a text, without cutting a character of several bytes in two
local function cut(text, n)
  if #text <= n then return text end
  while n > 0 and (text:byte(n + 1) or 0) >= 128 and text:byte(n + 1) < 192 do n = n - 1 end
  return text:sub(1, n)
end

local cache = {}
function M.FindMap(zone)
  local current = C_Map.GetBestMapForUnit("player")
  if not zone or zone == "" then return current end
  local key = zone:lower()
  if cache[key] ~= nil then return cache[key] or nil end
  local around = mapAround(key)
  if around then cache[key] = around return around end
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

-- the marks on the map now, in walking order: { title, x, y (0-100), mapID, zone, mob (the creature to find there),
--   current (the stop the player chose to head to; without one it is the first), locked (stays on the route until the
--   player unlocks it), tasks (what is to be done there: { kind = "objective" | "turn_in" | "pick_up", quest, label },
--   or { kind = "travel", zone } for the way into another zone, done when the player is in that zone;
--   back (a stop that was done and the player chose to go to again; away: they have been away from it since);
--   note (what to do there in a few words, in the AI's own words; without one the list says it from the tasks);
--   the stop leaves the route when they are done, not when the player reaches it) }
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

-- the stops still to do (a stop that is done stays on the list until the route is updated)
function M.Left()
  local n = 0
  for _, mk in ipairs(ns.char and ns.char.marks or {}) do
    if not mk.done then n = n + 1 end
  end
  return n
end

-- The stop the arrow leads to: the one the player chose in the list, else the first.
-- Stops that are done stay on the list with their number, so the numbers never shift; the arrow skips them. nil
-- when every stop is done.
local function currentIndex(saved)
  for i, mk in ipairs(saved) do
    if mk.current and not mk.done then return i end
  end
  for i, mk in ipairs(saved) do
    if not mk.done then return i end
  end
end

local function setCurrent(saved, to)
  for _, mk in ipairs(saved) do mk.current = (mk == to) or nil end
end

-- What the map and the arrow call a stop: its number in the list as it stands now, then its name. The number a title
-- was marked with is dropped: stops leave and move, and the arrow must say the same number as the list.
-- What it is for comes after it in brackets: the arrow says it while the player walks. One line, of at most ARROW_MAX
-- characters: a longer one ran across the action bars, so what is over is cut, with "..." in its place.
local ARROW_MAX = 128
local function waypointTitle(mk, i)
  local what = M.What and M.What(mk)
  local name = string.format("ForeverAI: %d. %s", i, (tostring(mk.title):gsub("^%d+[%.%)]%s*", "")))
  if #name > ARROW_MAX then return cut(name, ARROW_MAX - 3) .. "..." end
  if not what then return name end
  local room = ARROW_MAX - #name - 3 -- " (" and ")"
  if #what <= room then return name .. " (" .. what .. ")" end
  if room < 8 then return name end -- no room for a word of it: the list in the window says the rest
  return name .. " (" .. (cut(what, room - 3):gsub("[%s,;]+$", "")) .. "...)"
end

local function point(mk, i)
  local uid = mk and M.uids[mk]
  if uid and TomTom and TomTom.SetCrazyArrow then
    pcall(TomTom.SetCrazyArrow, TomTom, uid, 10, waypointTitle(mk, i or 1))
  elseif mk and not usesTomTom() and C_Map.SetUserWaypoint and UiMapPoint then
    local okSet = pcall(C_Map.SetUserWaypoint, UiMapPoint.CreateFromCoordinates(mk.mapID, mk.x / 100, mk.y / 100))
    if okSet and C_SuperTrack and C_SuperTrack.SetSuperTrackedUserWaypoint then
      pcall(C_SuperTrack.SetSuperTrackedUserWaypoint, true)
    end
  end
end

local function place(mk, first, i)
  if usesTomTom() then
    local ok, uid = pcall(TomTom.AddWaypoint, TomTom, mk.mapID, mk.x / 100, mk.y / 100,
      -- cleardistance 0: TomTom does not take the waypoint off when the player arrives; the add-on decides that
      { title = waypointTitle(mk, i or 1), persistent = false, minimap = true, world = true, crazy = first,
        cleardistance = 0 })
    if ok and uid then M.uids[mk] = uid end
  elseif first then
    point(mk, i)
  end
end

local function unplace(mk)
  local uid = M.uids[mk]
  if uid and TomTom and TomTom.RemoveWaypoint then pcall(TomTom.RemoveWaypoint, TomTom, uid) end
  M.uids[mk] = nil
end

-- a place is one stop: the same spot under any name, or the same name close by ("Corithras Moonrage" and
-- "Corithras Moonrage (Crown of the Earth)")
local function placeName(title)
  return (tostring(title):gsub("^%d+[%.%)]%s*", ""):gsub("%s*%b()%s*$", "")):lower()
end

local function samePlace(a, b)
  if a.mapID ~= b.mapID then return false end
  local dx, dy = math.abs(a.x - b.x), math.abs(a.y - b.y)
  return (dx <= 0.3 and dy <= 0.3) or (placeName(a.title) == placeName(b.title) and dx <= 1 and dy <= 1)
end

-- the AI changed what comes first on the route: the arrow leads to the first stop that is not done (a stop the player
-- had gone back to is done as it was)
local function leadToFirst(saved)
  for _, mk in ipairs(saved) do
    if mk.back then mk.done, mk.back, mk.away = true, nil, nil end
    mk.current = nil
  end
end

local function indexOf(saved, mk)
  for i, other in ipairs(saved) do
    if other == mk then return i end
  end
end

local bestSlot -- (saved, stop) -> where a stop added to the route costs the least way; below, with the player's spot

-- Adds the marks to the ones on the map (UI.MarksCard clears first unless the answer adds stops); a place that is on
-- the route already is not marked twice. ``fit``: the marks are added to the route on the map, so each goes where it
-- costs the least way (an answer marks its own route in its own order); one that lands before the stop the arrow
-- leads to takes the arrow.
-- a mark of an answer as a stop of the route; nil when the game has no map for its zone
local function entry(mk)
  local mapID = M.FindMap(mk.zone)
  if not mapID then return nil end
  local info = C_Map.GetMapInfo(mapID)
  local e = { title = tostring(mk.title), x = tonumber(mk.x) or 0, y = tonumber(mk.y) or 0, mapID = mapID,
    zone = info and info.name or mk.zone, mob = type(mk.mob) == "string" and mk.mob or nil,
    note = type(mk.note) == "string" and mk.note ~= "" and cut(mk.note, 120) or nil,
    at = time() } -- since when the stop waits: what the player did there before does not count
  for _, t in ipairs(type(mk.tasks) == "table" and mk.tasks or {}) do
    if type(t) == "table" and t.kind == "travel" and type(t.zone) == "string" then
      -- The zone as the game names it. A name the game has no map for cannot be entered: the stop is then
      -- only a place, done when it is reached, instead of waiting for ever and holding the ways after it.
      local to = M.FindMap(t.zone)
      local toInfo = to and C_Map.GetMapInfo(to)
      if toInfo and toInfo.name then
        e.tasks = e.tasks or {}
        e.tasks[#e.tasks + 1] = { kind = "travel", zone = toInfo.name }
      end
    elseif type(t) == "table" and type(t.quest) == "string" and (t.kind == "objective" or t.kind == "turn_in" or t.kind == "pick_up") then
      e.tasks = e.tasks or {}
      -- id: the quest itself; the parts of a chain often share one title
      if tonumber(t.id) and ns.char then -- the quests routes were made for: the app is told which are through
        ns.char.routeQuests = ns.char.routeQuests or {}
        ns.char.routeQuests[tonumber(t.id)] = true
      end
      e.tasks[#e.tasks + 1] = { kind = t.kind, quest = t.quest, id = tonumber(t.id),
        label = type(t.label) == "string" and t.label or nil, at = type(t.at) == "string" and t.at or nil,
        spots = tonumber(t.spots) }
    end
  end
  return e
end

-- Is everything a stop is for through already, by the game's own word: the quest turned in (flagged completed), or,
-- for a stop where it is picked up, in the quest log? Such a stop is not put on the route: an update planned from
-- data that was a moment old put the three stops of a quest back the second after it was turned in.
local function through(e)
  local log, any = nil, false
  for _, t in ipairs(e.tasks or {}) do
    if not t.quest then return false end
    any = true
    local flagged = t.id and C_QuestLog.IsQuestFlaggedCompleted and ns.safe(C_QuestLog.IsQuestFlaggedCompleted, t.id)
    if not flagged then
      if t.kind ~= "pick_up" or not M.QuestLog then return false end
      log = log or M.QuestLog()
      local has = t.id and log.quests[t.id] ~= nil
      for _, id in ipairs(not t.id and log.order or {}) do
        if log.quests[id].title == t.quest then has = true end
      end
      if not has then return false end
    end
  end
  return any
end

-- what a second stop at the same place was for is added to the one that is there. Two NPCs who stand side by side
-- are one stop too: what is the other one's to give or to take says so ("at": his name)
local function addTasks(same, e)
  local other = placeName(e.title) ~= placeName(same.title) and (tostring(e.title):gsub("^%d+[%.%)]%s*", ""):gsub("%s*%b()%s*$", "")) or nil
  for _, t in ipairs(e.tasks or {}) do
    if other and t.quest and not t.at then t.at = other end
    local have = false
    -- (a task that names its quest by title alone is the one with the number: the AI's own mark for a stop of the
    -- planner's)
    for _, o in ipairs(same.tasks or {}) do
      if o.kind == t.kind and o.quest == t.quest and (o.id == t.id or o.id == nil or t.id == nil) and o.zone == t.zone then
        have = true
        o.id = o.id or t.id
        break
      end
    end
    if not have then
      same.tasks = same.tasks or {}
      same.tasks[#same.tasks + 1] = t
    end
  end
  same.note, same.mob = same.note or e.note, same.mob or e.mob
end

function M.Apply(marks, fit)
  local placed, saved, revived = {}, list(), false
  local lead, ahead, inserted = saved[currentIndex(saved) or 0], false, false
  for _, mk in ipairs(marks or {}) do
    local e = entry(mk)
    if e and through(e) then
      if ns.Log then ns.Log("route", "not put on the route, it is through already: " .. tostring(e.title)) end
      e = nil
    end
    if e then
      local twice, again, same = false, nil, nil
      for _, other in ipairs(saved) do
        if samePlace(other, e) then
          twice, same = true, other
          if other.done then again = other end
          break
        end
      end
      -- A stop the route comes back to later is a stop of its own (a quest turned in where it was taken, with what
      -- it wants between: as one stop the arrow stayed on the NPC until the quest was turned in). The same stop is
      -- the one right before this one at the same place.
      if twice and not again and not fit and same ~= saved[#saved] then twice, same = false, nil end
      if again then -- a stop that was done is needed again (another quest at the same NPC): it is a stop to do
        again.done, again.title, again.mob, again.tasks, again.note = nil, e.title, e.mob, e.tasks, e.note
        again.back, again.away, again.at, again.npc = nil, nil, e.at, nil
        placed[#placed + 1] = again
        revived = true
      elseif twice then
        -- The place is a stop already: it stays one stop, and what the second one was for is added to it. (Dropped
        -- whole, a second stop took its tasks with it: the stop of a quest giver had nothing left to wait for and
        -- was done when the player walked up.)
        addTasks(same, e)
      elseif not twice and fit and #saved > 0 then
        local at = bestSlot(saved, e)
        table.insert(saved, at, e)
        if ns.Log then ns.Log("route", string.format("added %s as stop %d", e.title, at)) end
        placed[#placed + 1] = e
        inserted = true
        if lead and at < indexOf(saved, lead) then ahead = true end
      elseif not twice then
        saved[#saved + 1] = e
        place(e, #saved == 1, #saved)
        placed[#placed + 1] = e
      end
    end
  end
  if ahead then leadToFirst(saved) end
  if revived or inserted then M.Restore() end -- the map, the numbers and the arrow follow the list
  M.last = placed
  changed()
  return placed, usesTomTom() and "TomTom" or "map pin"
end

-- ``by``: who took the route off the map (for the add-on's log: a route that is gone must say why)
function M.Clear(by)
  if ns.Log and #list() > 0 then
    ns.Log("route", string.format("cleared (%d stops): %s", #list(), tostring(by or "not said")))
  end
  if ns.Seen and #list() > 0 and tostring(by or ""):find("^the player") then
    ns.Seen("the route was taken off the map by " .. tostring(by))
  end
  for _, mk in ipairs(list()) do unplace(mk) end
  wipe(M.uids)
  if ns.char then
    ns.char.marks, ns.char.marksRemoved, ns.char.routePlanned, ns.char.routeAuto, ns.char.routeRejected = {}, nil, nil, nil, nil
  end
  if C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
  changed()
end

-- After login, /reload or a loading screen the saved marks are on the map once: what this session already placed
-- is taken off first (the event also comes with every boat, portal and instance).
function M.Restore()
  local saved = list()
  for _, mk in ipairs(saved) do unplace(mk) end
  wipe(M.uids)
  local cur = currentIndex(saved)
  for i, mk in ipairs(saved) do
    if not mk.done then place(mk, i == cur, i) end -- a stop that is done is off the map, and stays on the list
  end
  if not cur and not usesTomTom() and C_Map.ClearUserWaypoint then pcall(C_Map.ClearUserWaypoint) end
end

-- where the player is, asked once for all marks: map, x, y (0-100)
local function playerSpot()
  local mapID = C_Map.GetBestMapForUnit("player")
  local pos = mapID and C_Map.GetPlayerMapPosition(mapID, "player")
  if pos then return mapID, pos.x * 100, pos.y * 100 end
end

-- a comes before b on a route for their quests' sake: a quest is picked up before the rest of it is done, and its
-- objectives before it is turned in
local function comesBefore(a, b)
  for _, t in ipairs(a.tasks or {}) do
    for _, u in ipairs(b.tasks or {}) do
      if t.quest and t.quest == u.quest and ((t.kind == "pick_up" and u.kind ~= "pick_up")
          or (t.kind == "objective" and u.kind == "turn_in")) then
        return true
      end
    end
  end
  return false
end

-- The way between two places on a map; between maps it is not known, and counts as long. A stop added to the route
-- goes in the gap where it adds the least way: from the player to the first stop still to do, between two of them, or
-- after the last; never after the turn-in of its own quest's objective, nor before what its turn-in waits for.
local FAR_M = 5000
bestSlot = function(saved, e)
  local mapID, px, py = playerSpot()
  local function way(a, b)
    if not a or a.mapID ~= b.mapID then return FAR_M end
    return ns.MapMeters(a.mapID, a.x, a.y, b.x, b.y) or math.sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2) * 30
  end
  local lo, hi = 0, #saved + 1 -- it goes in before stop i, lo < i <= hi
  for i, mk in ipairs(saved) do
    if not mk.done then
      if comesBefore(mk, e) then lo = i end
      if hi > #saved and comesBefore(e, mk) then hi = i end
    end
  end
  if lo >= hi then lo = 0 end -- the stops disagree among themselves: the way alone decides
  local prev = px and { mapID = mapID, x = px, y = py } or nil
  local last
  for _, mk in ipairs(saved) do
    if not mk.done then last = mk end
  end
  local at, best = hi, hi > #saved and way(last or prev, e) or math.huge
  for i, mk in ipairs(saved) do
    if not mk.done then
      local cost = way(e, mk) + (prev and (way(prev, e) - way(prev, mk)) or 0)
      if i > lo and i <= hi and cost < best then at, best = i, cost end
      prev = mk
    end
  end
  return at
end

local function metersTo(mk, mapID, px, py)
  if not px or mapID ~= mk.mapID then return nil end
  return ns.MapMeters(mapID, px, py, mk.x, mk.y)
end

------------------------------------------------------------------------ who the player has dealt with
-- A stop at an NPC with nothing of a quest to wait for (a vendor, a trainer, a flight master, a banker, a quest giver
-- the route only names) is done when the player has dealt with an NPC there, not when they walk up to him. What tells
-- is the window of an NPC: opening, or closing after it was open (a player who asked for the route with the vendor's
-- window open sells and closes it). The last few are remembered with where the player stood.
local VISIT_M, MET_KEEP = 25, 16
local NPC_WAIT_S = 20 -- how long a stop waits for the NPC data to say whether somebody stands there
local met, windowOpen = {}, {}
local askedAt = setmetatable({}, { __mode = "k" }) -- stop -> when the NPC data was first asked about it

local function noteMet(kind)
  local mapID, px, py = playerSpot()
  local name = UnitName("npc")
  -- A quest window with nobody behind it is a quest from an item the player opened: reading a letter by the trainer
  -- is not training, and the place does not count as one where an NPC was dealt with.
  if kind == "quest" and not (type(name) == "string" and name ~= "" and name ~= UnitName("player")) then return end
  if not name and UnitExists("target") and not UnitIsPlayer("target") then name = UnitName("target") end
  met[#met + 1] = { t = time(), mapID = mapID, x = px, y = py, name = type(name) == "string" and name:lower() or nil }
  while #met > MET_KEEP do table.remove(met, 1) end
end
M.NoteMet = noteMet

for kind, events in pairs({ gossip = { "GOSSIP_SHOW", "GOSSIP_CLOSED" }, merchant = { "MERCHANT_SHOW", "MERCHANT_CLOSED" },
    trainer = { "TRAINER_SHOW", "TRAINER_CLOSED" }, taxi = { "TAXIMAP_OPENED", "TAXIMAP_CLOSED" },
    bank = { "BANKFRAME_OPENED", "BANKFRAME_CLOSED" }, auction = { "AUCTION_HOUSE_SHOW", "AUCTION_HOUSE_CLOSED" },
    stable = { "PET_STABLE_SHOW", "PET_STABLE_CLOSED" }, quest = { "QUEST_DETAIL", "QUEST_FINISHED" } }) do
  ns.On(events[1], function()
    windowOpen[kind] = true
    noteMet(kind)
  end)
  ns.On(events[2], function() -- only a window that was open: the game sends some of these without one
    if windowOpen[kind] then noteMet(kind) end
    windowOpen[kind] = nil
  end)
end
for _, event in ipairs({ "QUEST_GREETING", "QUEST_PROGRESS", "QUEST_COMPLETE" }) do
  ns.On(event, function()
    windowOpen.quest = true
    noteMet("quest")
  end)
end

-- has the player dealt with an NPC at this stop since it was put on the route? (with the NPC of that name, wherever
-- he had wandered, or with any NPC where the stop is: of two weapon merchants side by side either one repairs)
local function dealtWith(mk)
  for i = #met, 1, -1 do
    local m = met[i]
    if m.t >= (mk.at or 0) then
      if m.name and type(mk.npc) == "string" and m.name == mk.npc:lower() then return true end
      local d = m.mapID == mk.mapID and m.x and ns.MapMeters(mk.mapID, m.x, m.y, mk.x, mk.y)
      if d and d <= VISIT_M then return true end
    end
  end
  return false
end

-- the creature a stop leads to, lower case: the one it was marked with, else its title without number and brackets
local function creatureOf(mk)
  return (mk.mob or (tostring(mk.title):gsub("^%d+[%.%)]%s*", ""):gsub("%s*%b()%s*$", ""))):lower()
end

------------------------------------------------------------------------ ways the land does not allow
-- The map knows no mountains, cliffs or water. Where the player had to go round on the way to a stop (the walk was much
-- longer than the straight line, and went one way round, not to and fro, and no fight came between), it is
-- remembered for every character: from where, to where, through which point. It counts once it was walked round a
-- second time, on another occasion: one walk round a hill proves nothing (the player was after a creature, or did
-- not see the path), and a stop "The way round" before every hill is worse than none. Walked straight between the
-- same two places, or taken off the route by the player, it is forgotten.
local DETOUR_MIN_M, DETOUR_RATIO, DETOUR_STRAIGHT, DETOUR_KEEP, TRAIL_MAX = 80, 1.4, 1.3, 40, 400
local DETOUR_OPEN = 1.15 -- a walk no longer than this many times the straight line was the straight way
M.DETOUR_AGAIN_S = 600 -- the second walk round counts when it comes this long after the one before
local trail -- the walk since the last stop: { mapID, pts = { {x, y} ... }, len (m), fought }

local function trailPoint(mapID, x, y)
  if not trail or trail.mapID ~= mapID or #trail.pts >= TRAIL_MAX then
    trail = { mapID = mapID, pts = { { x, y } }, len = 0 }
    return
  end
  if InCombatLockdown and InCombatLockdown() then trail.fought = true end
  local last = trail.pts[#trail.pts]
  local d = ns.MapMeters(mapID, last[1], last[2], x, y)
  if d and d >= 3 then
    trail.pts[#trail.pts + 1] = { x, y }
    trail.len = trail.len + d
  end
end

local function sameEnds(d, mapID, a, px, py)
  return d.mapID == mapID and ((math.abs(d.a[1] - a[1]) + math.abs(d.a[2] - a[2]) < 3
    and math.abs(d.b[1] - px) + math.abs(d.b[2] - py) < 3) or (math.abs(d.b[1] - a[1]) + math.abs(d.b[2] - a[2]) < 3
    and math.abs(d.a[1] - px) + math.abs(d.a[2] - py) < 3))
end

-- the walk ended at a stop: was it a way round? (own: the stop was a way round of the route itself, so the walk
-- only did what the route said and tells nothing new)
local function learnDetour(mapID, px, py, own)
  local t = trail
  trail = { mapID = mapID, pts = { { px, py } }, len = 0 }
  if own then return end
  local size = ns.MapSize(mapID)
  if not (t and size and t.mapID == mapID and not t.fought and #t.pts >= 3) then return end
  local a = t.pts[1]
  local straight = ns.MapMeters(mapID, a[1], a[2], px, py)
  if not straight or straight < DETOUR_MIN_M then return end
  -- the point of the walk farthest from the straight line, in yards
  local function yd(p) return p[1] / 100 * size[1], p[2] / 100 * size[2] end
  local ax, ay = yd(a)
  local bx, by = yd({ px, py })
  local lx, ly = bx - ax, by - ay
  local via, far = nil, -1
  for _, p in ipairs(t.pts) do
    local x, y = yd(p)
    local off = math.abs((x - ax) * ly - (y - ay) * lx) / math.sqrt(lx * lx + ly * ly)
    if off > far then via, far = p, off end
  end
  local round = ns.MapMeters(mapID, a[1], a[2], via[1], via[2]) + ns.MapMeters(mapID, via[1], via[2], px, py)
  local walked = t.len + (ns.MapMeters(mapID, t.pts[#t.pts][1], t.pts[#t.pts][2], px, py) or 0)
  local learned = round >= DETOUR_RATIO * straight and walked <= DETOUR_STRAIGHT * round
  local list = ns.db.detours or {}
  local open = not learned and walked <= DETOUR_OPEN * straight
  local times = 1
  for i = #list, 1, -1 do
    local d = list[i]
    if sameEnds(d, mapID, a, px, py) then
      if learned then -- the same way again: the newest walk stands, and counts when it is another occasion
        times = (d.n or 1) + ((time() - (d.at or 0) >= M.DETOUR_AGAIN_S) and 1 or 0)
      end
      if learned or open then table.remove(list, i) end -- walked straight: there is a way through after all
    end
  end
  if ns.Log then
    ns.Log("route", string.format("walk to the stop: straight %d m, round %d m, walked %d m: %s", straight, round, walked,
      learned and (times >= 2 and "a way round, walked again: remembered" or "a way round, once (it counts when walked again)")
      or open and "the straight way" or "not a way round"))
  end
  ns.db.detours = list
  if not learned then return end
  local info = C_Map.GetMapInfo(mapID)
  list[#list + 1] = { mapID = mapID, zone = info and info.name or "?", a = { a[1], a[2] }, b = { px, py },
    via = { via[1], via[2] }, straight = straight, round = round, at = time(), n = times }
  while #list > DETOUR_KEEP do table.remove(list, 1) end
  ns.db.detours = list
end

-- for the AI and the planner: the ways round, "Darkshore 40.0, 40.0 → 50.0, 40.0: through 45.0, 48.0 (...)"
function M.Detours()
  local out = {}
  for _, d in ipairs(ns.db and ns.db.detours or {}) do
    if (d.n or 1) >= 2 then -- walked round twice (the ones remembered from one walk, by an older version, wait too)
      out[#out + 1] = string.format("%s %.1f, %.1f → %.1f, %.1f: through %.1f, %.1f (straight %d m, round %d m)", d.zone,
        d.a[1], d.a[2], d.b[1], d.b[2], d.via[1], d.via[2], d.straight, d.round)
    end
  end
  return #out > 0 and out or nil
end

-- The player took a way round off the route: it is not needed, and not remembered.
local function forgetDetour(mk)
  local list = ns.db and ns.db.detours or {}
  for i = #list, 1, -1 do
    local d = list[i]
    if d.mapID == mk.mapID and math.abs(d.via[1] - mk.x) + math.abs(d.via[2] - mk.y) < 1 then
      table.remove(list, i)
      if ns.Log then ns.Log("route", "a way round forgotten: the player took it off the route") end
    end
  end
end

-- A stop that is there for something to do (its tasks) leaves the list when that is done, wherever the player
-- stands, and stays until then: walking past a turn-in is not turning in.
-- A stop that is only a place leaves when the player reaches it, and the arrow leads to the stop after it. Two kinds stay,
-- and the arrow with them: one whose creature is watched for (reaching the place is not the end of it, its objective
-- is), and one the player locked (until they unlock it or click another stop). A waypoint of a stop that is still on
-- the route but gone from TomTom is put back at once.
function M.Check()
  local saved = ns.char and ns.char.marks
  if not saved or #saved == 0 then return end
  local mapID, px, py = playerSpot()
  if not px then return end
  trailPoint(mapID, px, py)
  local moved, finishedNow, relabel, turned = false, {}, false, false
  local log -- the quest log, read once and only when a stop waits for its quest
  -- the ways into zones follow one another: the way back into the zone the player stands in is not taken before
  -- the way out of it
  local onTheWay = true
  for i, mk in ipairs(saved) do
    local travels = mk.tasks and mk.tasks[1] and mk.tasks[1].kind == "travel"
    if travels and not mk.done and M.inZone and not M.inZone(mk.tasks[1].zone) and i == currentIndex(saved) then
      mk.chosenOutside = true -- the arrow leads through it from outside its zone
    end
    if not mk.done and not mk.locked then
      local finished
      if mk.back then
        -- The player went back to a stop that was done: what was to be done there is done already, so being
        -- there is what finishes it, once they have been away from it (a click while standing on it keeps it).
        local d = metersTo(mk, mapID, px, py)
        if not d or d > REACHED_M then mk.away = true end
        finished = mk.away and d and d <= REACHED_M
      elseif travels and not onTheWay and not (mk.chosenOutside and i == currentIndex(saved)) then
        -- (the way the arrow leads through, from outside its zone, is done once the player is there, whatever ways
        -- come before it: they went this way)
        finished = false
      elseif mk.tasks then
        log = log or M.QuestLog()
        finished = M.TasksDone(mk, log) -- done is what counts, not being there
        -- of several things to do there, which are done already: the stop then says what is left
        if not finished and #mk.tasks > 1 then
          for _, t in ipairs(mk.tasks) do
            local did = t.quest and M.TasksDone({ tasks = { t } }, log) or nil
            if did ~= t.did then t.did, relabel = did, true end
          end
        end
        -- The arrow's stop, reached, with what can be done there done: what is left waits for another stop (the
        -- player came here before the giver of the quest it turns in). The arrow leads on, to the first stop that
        -- waits for nothing; this one stays on the route for later.
        if not finished and not travels and i == currentIndex(saved) then
          local d = metersTo(mk, mapID, px, py)
          -- (both are defined further down: looked up when the check runs)
          if d and d <= REACHED_M and M.leftWaits(saved, mk, log) then
            for n, other in ipairs(saved) do
              if n ~= i and not other.done and not M.waitsFor(saved, other) then
                setCurrent(saved, other)
                turned = true
                if ns.Log then
                  ns.Log("route", string.format("stop %d waits for another stop: the arrow leads on to %d", i, n))
                end
                break
              end
            end
          end
        end
      else
        -- a place with nothing of a quest to do there. Is somebody standing there to deal with? (asked once, when
        -- the player is on the stop's map; a stop for a creature to kill or tame is a place)
        if mk.npc == nil and mapID == mk.mapID then
          if mk.mob or not (ns.Npcs and ns.Npcs.NpcAt) then
            mk.npc = false
          else
            local who = ns.Npcs.NpcAt(mk.mapID, mk.x, mk.y, mk.title)
            askedAt[mk] = askedAt[mk] or GetTime()
            if who ~= nil then
              mk.npc = who
            elseif GetTime() - askedAt[mk] > NPC_WAIT_S then
              mk.npc = false -- the NPC data never came (the quest add-on is not ready): the stop is a place
            end
          end
        end
        local watched = ns.Track and ns.Track.Watches and ns.Track.Watches(creatureOf(mk))
        if mk.npc then
          finished = not watched and dealtWith(mk) -- walking up to him is not dealing with him
        elseif mk.npc == false then
          local d = metersTo(mk, mapID, px, py)
          finished = d and d <= REACHED_M and not watched
        end
      end
      if finished then
        local how = mk.back and "reached again" or travels and "in the next zone" or mk.tasks and "what was to be done is done"
          or mk.npc and "the player dealt with an NPC there" or "reached"
        if ns.Log then
          ns.Log("route", string.format("stop done: %d. %s (%s)", i, tostring(mk.title):gsub("^%d+[%.%)]%s*", ""), how))
        end
        if M.NoteDone then M.NoteDone(mk, how) end
        M.Finish(saved, i)
        moved = true
        -- (a stop of the route was just done: when the arrow now leads into another zone, that is not a border
        -- crossed for a moment, and the route is updated from here at once)
        M.wentThrough = true
        finishedNow[#finishedNow + 1] = (tostring(mk.title):gsub("^%d+[%.%)]%s*", ""))
      end
    end
    if travels and not mk.done then
      -- a way the player locked stays on the list; once they are through it, it is taken all the same
      if mk.locked and onTheWay and not mk.taken then
        log = log or M.QuestLog()
        mk.taken = M.TasksDone(mk, log) or nil
      end
      if not (mk.locked and mk.taken) then onTheWay = false end
    end
  end
  if M.Recheck then M.Recheck() end
  if M.Strayed then pcall(M.Strayed, mapID, saved) end
  if (relabel or turned) and not moved then M.Restore() changed() end -- the arrow and the list say what is left
  local cur = currentIndex(saved)
  if usesTomTom() and TomTom.IsValidWaypoint then -- a stop that is still to do is on the map
    for i, mk in ipairs(saved) do
      local uid = M.uids[mk]
      local ok, valid = pcall(TomTom.IsValidWaypoint, TomTom, uid)
      if not mk.done and (not uid or (ok and not valid)) then
        M.uids[mk] = nil
        place(mk, i == cur, i)
      end
    end
  end
  if moved then -- the map and the arrow follow the list
    local own = false
    for _, title in ipairs(finishedNow) do
      if placeName(title) == "the way round" then own = true end
    end
    pcall(learnDetour, mapID, px, py, own)
    M.Restore()
    changed()
    M.Tell(finishedNow)
  end
end

-- Stops were done by what the player did, without the AI: the player is told at once, with where the arrow leads now.
-- Silence looked like the add-on had not noticed.
function M.Tell(done)
  if #done == 0 or not (ns.UI and ns.UI.RouteProgress) then return end
  local saved = list()
  local cur = currentIndex(saved)
  local nextStop, meters
  if cur then
    local mapID, px, py = playerSpot()
    nextStop = string.format("%d. %s", cur, (tostring(saved[cur].title):gsub("^%d+[%.%)]%s*", "")))
    meters = metersTo(saved[cur], mapID, px, py)
  end
  pcall(ns.UI.RouteProgress, done, nextStop, meters)
end

-- Stop i is done: it stays on the list, marked done, until the route is updated (or the player takes it off), and
-- the arrow leads on: to the stop after it when the player had chosen this one, else to the first that is left.
-- Does stop mk wait for another stop of the route that is not done: one where its quest is still to be picked up,
-- or, for a turn-in, one where something of the quest is still to be done?
local function waitsFor(saved, mk)
  for _, t in ipairs(mk.tasks or {}) do
    if t.kind == "objective" or t.kind == "turn_in" then
      for _, other in ipairs(saved) do
        if other ~= mk and not other.done then
          for _, o in ipairs(other.tasks or {}) do
            local same = (t.id and o.id == t.id) or (not t.id and o.quest == t.quest)
            if same and (o.kind == "pick_up" or (t.kind == "turn_in" and o.kind == "objective")) then return true end
          end
        end
      end
    end
  end
  return false
end

-- Is all that is left to do at stop mk waiting for another stop of the route (a turn-in whose quest is picked up
-- elsewhere, or whose objectives are done elsewhere)? Something of it is left, and none of that can be done there
-- now. A quest to pick up beside a turn-in that waits may be the next part of that quest, given only once it is
-- turned in, or a quest given now: it waits with the turn-in once the player has dealt with the NPC there (did
-- something at the stop, or talked to him and closed his window) and not taken it.
local function leftWaits(saved, mk, log)
  local any, pickUp, turnInWaits, did = false, false, false, false
  for _, t in ipairs(mk.tasks or {}) do
    if t.quest and not M.TasksDone({ tasks = { t } }, log) then
      any = true
      if t.kind == "pick_up" then
        pickUp = true
      elseif waitsFor(saved, { tasks = { t } }) then
        turnInWaits = turnInWaits or t.kind == "turn_in"
      else
        return false
      end
    elseif t.quest then
      did = true
    end
  end
  if not any then return false end
  if not pickUp then return true end
  local talking = windowOpen.quest or windowOpen.gossip
  return turnInWaits and not talking and (did or dealtWith(mk))
end
M.leftWaits, M.waitsFor = leftWaits, waitsFor

function M.Finish(saved, i)
  local mk = saved[i]
  mk.done, mk.back, mk.away = true, nil, nil
  if mk.current then
    -- On to the stop after it, as the player had chosen to go this way; not to one that waits for a stop they left
    -- behind (the turn-in of a quest whose giver they passed by): then to the first stop of the route that is left.
    mk.current = nil
    for n = i + 1, #saved do
      if not saved[n].done and not waitsFor(saved, saved[n]) then
        -- ... but not through a way into another zone while stops before it are still to do (the ones the player
        -- left behind when they chose their own): the arrow then leads to the first of those
        local t = saved[n].tasks and saved[n].tasks[1]
        local behind = false
        if t and t.kind == "travel" then
          for k = 1, n - 1 do
            if not saved[k].done and not waitsFor(saved, saved[k]) then behind = true break end
          end
        end
        if not behind then saved[n].current = true end
        break
      end
    end
  end
end

-- the stops that are done leave the list (the route was just updated)
function M.DropDone()
  local saved, gone = list(), false
  for i = #saved, 1, -1 do
    if saved[i].done then
      unplace(saved[i])
      table.remove(saved, i)
      gone = true
    end
  end
  if gone then
    M.Restore()
    changed()
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
  local name = creatureOf(mk)
  for _, other in ipairs(list()) do
    local o = creatureOf(other)
    if o == name or (ns.Track.Same and ns.Track.Same(o, name)) then return end
  end
  ns.Track.Stop(name)
end

-- The objective these creatures count for is done (tracking ended by itself): their stops leave the route, wherever
-- the player stands. ``names``: lower-case names -> true.
-- Is something a stop is for known to be still to do: a turn-in or a pick-up, or an objective the quest log shows
-- open? (An objective the log does not show by name says nothing.)
local function knownOpen(mk)
  if not (mk.tasks and M.QuestLog and M.TasksDone) then return false end
  local log = M.QuestLog()
  for _, t in ipairs(mk.tasks) do
    if t.quest and not M.TasksDone({ tasks = { t } }, log) then
      if t.kind ~= "objective" then return true end
      local q = t.id and log.quests[t.id]
      for _, id in ipairs(not q and log.order or {}) do
        if log.quests[id].title == t.quest then q = log.quests[id] end
      end
      if q and M.objectiveOf and M.objectiveOf(t, q) then return true end
      -- (a quest still to be picked up on the route: what it wants there is still to do)
      if not q and M.AwaitsQuest and M.AwaitsQuest(t.quest) then return true end
    end
  end
  return false
end

function M.Done(names)
  local saved, gone, finishedNow = list(), false, {}
  for i, mk in ipairs(saved) do
    -- (a stop for these creatures that another quest still needs stays: done is what counts there)
    if names[creatureOf(mk)] and not mk.locked and not mk.done and not knownOpen(mk) then
      M.Finish(saved, i)
      gone = true
      finishedNow[#finishedNow + 1] = (tostring(mk.title):gsub("^%d+[%.%)]%s*", ""))
    end
  end
  if gone then
    refresh()
    M.Tell(finishedNow)
  end
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
  if by == "player" and placeName(mk.title) == "the way round" then forgetDetour(mk) end
  if by == "player" and not mk.done then -- a stop that was done is no news for the AI
    local gone = ns.char.marksRemoved or {}
    gone[#gone + 1] = bare(mk.title)
    while #gone > 10 do table.remove(gone, 1) end
    ns.char.marksRemoved = gone
  end
  untrack(mk)
  refresh()
end

-- The player heads to stop i (a click in the list): the order stays, the arrow leads there, and on to the stop after
-- it when it is reached.
function M.SetCurrent(i)
  local saved = list()
  if not saved[i] then return end
  for n, mk in ipairs(saved) do -- another stop chosen instead: the one the player had gone back to is done as it was
    if n ~= i and mk.back then mk.done, mk.back, mk.away = true, nil, nil end
  end
  if saved[i].done then -- going back to a stop that was done: it stays the one to go to until it is reached
    saved[i].done, saved[i].back, saved[i].away = nil, true, nil
  end
  -- a way into a zone chosen from outside it: being in that zone is going through it
  for _, mk in ipairs(saved) do mk.chosenOutside, mk.chosen = nil, nil end
  saved[i].chosen = true -- the player's own choice: an update of the route keeps the arrow on it
  local t = saved[i].tasks and saved[i].tasks[1]
  if t and t.kind == "travel" and M.inZone then saved[i].chosenOutside = not M.inZone(t.zone) or nil end
  setCurrent(saved, saved[i])
  refresh()
end

-- The AI sends the player to a stop (its edit_route go_to): as if the player had clicked it in the list.
function M.GoTo(want)
  local i = find(list(), want)
  if ns.Log then ns.Log("route", "the AI sends the player to " .. tostring(want and want.title) .. (i and "" or ": no such stop")) end
  if i then M.SetCurrent(i) end
  return i
end

-- Lock stop i, or unlock it: a locked stop stays on the route when it is reached and when an answer replaces, edits
-- or clears the route, until the player unlocks it or takes it off.
function M.Lock(i, on)
  local mk = list()[i]
  if not mk then return end
  mk.locked = on and true or nil
  if not on and mk.taken then -- a way into a zone that was taken while it was locked: done, now that it may go
    mk.taken = nil
    M.Finish(list(), i)
    refresh()
  end
  changed()
end

local function lockedStops()
  local out = {}
  for _, mk in ipairs(list()) do
    if mk.locked then out[#out + 1] = mk end
  end
  return out
end

-- after an answer replaced or cleared the route: the locked stops are on it again (a new stop at the same place
-- takes the lock over)
local function keepLocked(kept)
  if #kept == 0 then return end
  local saved = list()
  for _, mk in ipairs(kept) do
    local same
    for _, other in ipairs(saved) do
      if samePlace(other, mk) then same = other break end
    end
    if same then
      same.locked = true
    else
      mk.current = nil
      saved[#saved + 1] = mk
    end
  end
  refresh()
end

-- an answer's marks take the place of the route (the stops the player locked stay)
function M.Replace(marks)
  local kept = lockedStops()
  M.Clear("an answer's marks take its place")
  local placed, how = M.Apply(marks)
  keepLocked(kept)
  return placed, how
end

local function zonesOf(stops)
  local out = {}
  for _, mk in ipairs(stops) do
    if out[#out] ~= mk.mapID then out[#out + 1] = mk.mapID end
  end
  return table.concat(out, ">")
end

-- Can the stops be walked in this order, zone by zone: each in the zone the player is in, or in the zone the way
-- before it leads into? (A way into a zone stands in the zone it leads out of.)
local function walkable(stops)
  local here = playerSpot()
  if not here then return true end
  local cur, after = here, false -- after: the stop before was a way into another zone
  for _, mk in ipairs(stops) do
    if not mk.done then
      if after then
        cur, after = mk.mapID, false
      elseif mk.mapID ~= cur then
        return false
      end
      if mk.tasks and mk.tasks[1] and mk.tasks[1].kind == "travel" then after = true end
    end
  end
  return true
end

-- Is every quest picked up before the stops of what is done for it, and turned in after them?
local STEP = { pick_up = 1, objective = 2, turn_in = 3 }
local function inOrder(stops)
  local seen = {}
  for _, mk in ipairs(stops) do
    local low, high = {}, {}
    for _, t in ipairs(mk.tasks or {}) do
      local step, key = t.quest and STEP[t.kind], t.id or t.quest
      if step then
        low[key], high[key] = math.min(low[key] or step, step), math.max(high[key] or step, step)
      end
    end
    for key, step in pairs(low) do
      if seen[key] and step < seen[key] then return false end
    end
    for key, step in pairs(high) do seen[key] = math.max(seen[key] or step, step) end
  end
  return true
end

-- The add-on's own update of the route (a news line, not an answer the player asked for): the route on the map stays
-- the route. A stop the new plan still has is the same stop, with the plan's tasks: what the player did there, its
-- lock and the arrow on it are kept. A stop the plan no longer has leaves. A new stop goes in behind the stop the
-- plan put it behind (the plan starts where the player stands, so a stop on the way to the one they walk to comes
-- before it). The stops that stay keep the order they have on the map, unless that order cannot be walked any more:
-- the plan goes through the zones another way, or a quest would be picked up after the stops it is for.
-- ``more``: the plan has more stops than were sent (the map holds so many): a stop on the map the sent part does not
-- have may be one of the later ones, and stays.
function M.Merge(marks, more)
  local saved = list()
  local new, same, used, any = {}, {}, {}, false
  for _, mk in ipairs(marks or {}) do
    local e = entry(mk)
    if e and through(e) then
      if ns.Log then ns.Log("route", "not put on the route, it is through already: " .. tostring(e.title)) end
      e = nil
    end
    -- (the same stop is the one right before it at the same place; a place the route comes back to is another stop)
    local twice = e and new[#new] and samePlace(new[#new], e) and new[#new] or nil
    if twice then addTasks(twice, e) elseif e then new[#new + 1] = e end
  end
  -- what a stop is for, to tell two stops at one place apart (a quest taken there, and turned in there later)
  local function purpose(mk)
    local out = {}
    -- (with what an objective wants: the boars and the condors of one quest are two stops)
    for _, t in ipairs(mk.tasks or {}) do
      out[#out + 1] = tostring(t.kind) .. ":" .. tostring(t.id or t.quest or t.zone) .. ":" .. tostring(t.label or "")
    end
    table.sort(out)
    return table.concat(out, "|")
  end
  -- first the stop at that place that is for the same thing, then the stop for the same quest objective wherever it
  -- stands on that map (the planner names the spawn spots nearest to where the player is now: the stop stays where it
  -- was, and its marker does not jump about), then any stop at that place
  for pass = 1, 3 do
    for _, e in ipairs(new) do
      for _, old in ipairs(not same[e] and saved or {}) do
        local fits
        if pass == 2 then
          fits = old.mapID == e.mapID and e.tasks and e.tasks[1] and e.tasks[1].kind == "objective" and purpose(old) == purpose(e)
        else
          fits = samePlace(old, e) and (pass == 3 or purpose(old) == purpose(e))
        end
        if not old.done and not used[old] and fits then
          same[e], used[old], any = old, true, true
          break
        end
      end
    end
  end
  -- (a creature's stop keeps its place when the plan, made from somewhere else, names another of its grounds)
  for _, e in ipairs(new) do
    for _, old in ipairs(not same[e] and e.mob and saved or {}) do
      if not old.done and not used[old] and old.mapID == e.mapID and old.mob == e.mob
        and placeName(old.title) == placeName(e.title) then
        same[e], used[old], any = old, true, true
        break
      end
    end
  end
  if not any then return M.Replace(marks) end -- another route altogether
  -- what the sent part has to do, wherever: a stop of the plan's later part is kept for what the sent part has not
  local sent = {}
  for _, e in ipairs(new) do
    for _, t in ipairs(e.tasks or {}) do sent[purpose({ tasks = { t } })] = true end
  end
  local function later(old)
    if not more then return false end
    if not old.tasks or #old.tasks == 0 then return true end -- a place: the plan's later part may have it
    local left = {}
    for _, t in ipairs(old.tasks) do
      if not sent[purpose({ tasks = { t } })] then left[#left + 1] = t end
    end
    if #left == 0 then return false end -- all of it is done elsewhere now (a creature the plan took instead)
    old.tasks = left
    return true
  end
  local order, gone, placed = {}, {}, {}
  for _, old in ipairs(saved) do
    if not old.done then
      -- (a stop the player locked or went back to is theirs)
      if used[old] or old.locked or old.back or later(old) then order[#order + 1] = old else gone[#gone + 1] = old end
    end
  end
  local planned, onMap, woven, deferred = {}, {}, {}, {}
  for _, e in ipairs(new) do
    local old = same[e]
    if old then
      -- it leads to another creature (or NPC, or object) now: it goes where that one is
      if e.mob ~= old.mob or placeName(e.title) ~= placeName(old.title) then
        old.mapID, old.x, old.y, old.zone = e.mapID, e.x, e.y, e.zone or old.zone
      end
      -- (with more of the plan to come, what it was for that the sent part has nowhere stays: the plan has it
      -- later, and here it is on the way)
      local tasks = e.tasks
      if more and old.tasks then
        local other = placeName(old.title) ~= placeName(e.title)
          and (tostring(old.title):gsub("^%d+[%.%)]%s*", ""):gsub("%s*%b()%s*$", "")) or nil
        local log = M.QuestLog and M.QuestLog()
        for _, t in ipairs(old.tasks) do
          -- (not what is done already: a quest turned in since. A turn-in whose objectives come later on the route
          -- goes to a stop of its own at the end of the route on the map: the plan has it after them)
          if t.quest and not sent[purpose({ tasks = { t } })] and not (log and M.TasksDone({ tasks = { t } }, log)) then
            local kept = {}
            for k, v in pairs(t) do kept[k] = v end
            if waitsFor(saved, { tasks = { t } }) then
              kept.at = nil
              deferred[#deferred + 1] = { title = (tostring(old.title):gsub("^%d+[%.%)]%s*", "")), mapID = old.mapID,
                x = old.x, y = old.y, zone = old.zone, at = old.at, tasks = { kept } }
            else
              if tasks == e.tasks then tasks = { unpack(e.tasks or {}) } end
              kept.at = kept.at or other
              tasks[#tasks + 1] = kept
            end
          end
        end
      end
      old.title, old.tasks, old.mob, old.note = e.title, tasks, e.mob, e.note
      planned[#planned + 1] = old
    end
  end
  for _, old in ipairs(order) do
    if used[old] then onMap[#onMap + 1] = old end
    woven[#woven + 1] = old
  end
  local before
  for _, e in ipairs(new) do
    local stop = same[e]
    if not stop then
      stop = e
      table.insert(woven, before and (indexOf(woven, before) + 1) or 1, e)
      placed[#placed + 1] = e
    end
    before = stop
  end
  local kept = zonesOf(planned) == zonesOf(onMap) and inOrder(woven) and (walkable(woven) or not walkable(new))
  if not kept then -- the plan's own order
    woven = {}
    for _, e in ipairs(new) do woven[#woven + 1] = same[e] or e end
    for _, old in ipairs(order) do
      if not used[old] then woven[#woven + 1] = old end
    end
  end
  for _, mk in ipairs(deferred) do woven[#woven + 1] = mk end
  for _, mk in ipairs(gone) do unplace(mk) end
  -- (the arrow on a stop it went on to by itself follows the plan, which starts where the player is; the stop the
  -- player chose stays the one it leads to, while it is in the zone they are in: chosen before a flight elsewhere,
  -- it has no way there)
  local here = playerSpot()
  for _, mk in ipairs(woven) do
    if mk.current and (not mk.chosen or (here and mk.mapID ~= here)) then mk.current, mk.chosen = nil, nil end
  end
  local done = {}
  for _, old in ipairs(saved) do
    if old.done then done[#done + 1] = old end
  end
  wipe(saved)
  for _, mk in ipairs(done) do saved[#saved + 1] = mk end
  for _, mk in ipairs(woven) do saved[#saved + 1] = mk end
  if ns.Log then
    local names = function(stops)
      local out = {}
      for _, mk in ipairs(stops) do out[#out + 1] = bare(mk.title) end
      return #out > 0 and table.concat(out, ", ") or "none"
    end
    ns.Log("route", string.format("update: %d stops stay%s; new: %s; gone: %s", #planned,
      kept and " in their order" or ", in the plan's order (theirs cannot be walked any more)", names(placed), names(gone)))
  end
  M.last = placed
  refresh()
  return placed, usesTomTom() and "TomTom" or "map pin"
end

-- The AI's change to the route (its edit_route tool): { clear = true }, or { remove = { { title, x, y } ... },
-- order = { { title, x, y } ... }, go_to = { title, x, y } } with the stops as the "marks" section named them. Stops
-- not named in ``order`` follow in the order they had; after a new order the arrow leads to its first stop, unless
-- go_to names the one to head to.
function M.Edit(edit)
  if type(edit) ~= "table" then return end
  if edit.clear then
    local kept = lockedStops()
    M.Clear("the AI cleared the route")
    keepLocked(kept)
    return
  end
  local saved = list()
  for _, want in ipairs(type(edit.remove) == "table" and edit.remove or {}) do
    local i = find(saved, want)
    if i and not saved[i].locked then
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
  if at > 1 then leadToFirst(saved) end
  refresh()
  if type(edit.go_to) == "table" then M.GoTo(edit.go_to) end
end

-- What to do at a stop, in a few words for the list: the note it was marked with, else what its tasks say
-- ("turn in A, B; pick up C", "Dewy Lasher Frond for The Fronds", "go on to Darnassus"). nil for a stop that is
-- only a place.
local WHAT_MAX = 128
local function whatToDo(mk)
  -- A note of the player's or the AI's own says it best. One that only spells the tasks out again ("pick up [A]; pick
  -- up A and B; pick up [B]", what the planner writes for a stop of several quests) is left to the tasks: they say
  -- the same in a few words.
  local quests = false
  for _, t in ipairs(mk.tasks or {}) do
    if t.quest then quests = true end
  end
  local note = mk.note
  if note and quests then
    local low = note:lower()
    if low:find("pick up", 1, true) or low:find("turn in", 1, true) or low:find("[", 1, true) then note = nil end
  end
  if note then return note end
  local parts, at = {}, {}
  local function add(verb, name)
    if at[verb] then
      parts[at[verb]] = parts[at[verb]] .. ", " .. name
    else
      parts[#parts + 1] = verb .. " " .. name
      at[verb] = #parts
    end
  end
  -- what is done of it already is not said again (one of two quests taken): what is left is
  local left = {}
  for _, t in ipairs(mk.tasks or {}) do
    if not t.did then left[#left + 1] = t end
  end
  if #left == 0 then left = mk.tasks or {} end
  local own = placeName(mk.title)
  for _, t in ipairs(left) do
    -- (from the NPC beside the one the stop is named after)
    local quest = t.quest and (t.quest .. (t.at and t.at:lower() ~= own and (" (" .. t.at .. ")") or ""))
    if t.kind == "travel" and #mk.tasks == 1 and bare(mk.title):find("^To ") then
      -- the stop is the way itself (a pass, a gate, a tunnel): the next stop comes once the player is through
      parts[#parts + 1] = "the crossing is here: go through"
    elseif t.kind == "travel" then
      add("go on to", t.zone)
    elseif t.kind == "turn_in" then
      add("turn in", quest)
    elseif t.kind == "pick_up" then
      add(at["turn in"] and "then pick up" or "pick up", quest)
    elseif t.label and t.label:lower() ~= placeName(mk.title) and t.label:lower() ~= tostring(t.quest):lower() then
      parts[#parts + 1] = t.label .. " for " .. quest
    else -- the stop is named after what the objective wants
      add("for", quest)
    end
  end
  if #parts == 0 then return nil end
  -- what is looked for lies in one of several places there: the stop is an area to search, not the spot
  for _, t in ipairs(left) do
    if (t.spots or 0) > 1 then
      parts[#parts + 1] = string.format("search around: one of %d spots", t.spots)
      break
    end
  end
  -- (two quests of one name that want the same thing say it once)
  local said, once = {}, {}
  for _, p in ipairs(parts) do
    if not said[p] then said[p], once[#once + 1] = true, p end
  end
  local text = table.concat(once, "; ")
  return #text > WHAT_MAX and (cut(text, WHAT_MAX - 3) .. "...") or text
end

M.What = whatToDo

-- for the window: the stops in order, { title, what (to do there, or nil), zone, x, y, meters (nil in another
-- zone), current (the arrow leads here), locked }
function M.List()
  local out, saved = {}, list()
  local mapID, px, py = playerSpot()
  local cur = currentIndex(saved)
  for i, mk in ipairs(saved) do
    local waits
    for n, t in ipairs(mk.tasks or {}) do -- what the stop waits for, in words
      local what = t.kind == "turn_in" and "turning in" or t.kind == "pick_up" and "picking up" or (t.label or "an objective") .. " for"
      what = t.kind == "travel" and ("arriving in " .. t.zone) or (what .. " [" .. t.quest .. "]")
      waits = (waits and (waits .. ", ") or "") .. what
    end
    if not waits and mk.npc and not mk.done then waits = "dealing with " .. tostring(mk.npc) end
    out[i] = { title = bare(mk.title), what = whatToDo(mk), zone = mk.zone, x = mk.x, y = mk.y,
      meters = metersTo(mk, mapID, px, py),
      -- a stop the player went back to has its tasks done already: it is done when it is reached
      current = i == cur, locked = mk.locked == true, waits = not mk.back and waits or nil, done = mk.done == true }
  end
  return out
end

-- The stops of routes the player did lately, the newest last: { title, zone, what, at }. A done stop leaves the list
-- at the next update, and without this the AI took a stop the player had just done for one that fell off the route.
local DONE_KEEP, DONE_S = 10, 3 * 3600
function M.NoteDone(mk, how)
  if not ns.char then return end
  local done = ns.char.routeVisited or {}
  done[#done + 1] = { title = bare(mk.title), zone = mk.zone, what = M.What(mk) or how, at = time() }
  while #done > DONE_KEEP do table.remove(done, 1) end
  ns.char.routeVisited = done
end

-- for the AI (its own section, with a route on the map or without one): them, oldest first, with how long ago
function M.DoneLately()
  local out, now = {}, time()
  for _, d in ipairs(ns.char and ns.char.routeVisited or {}) do
    local ago = now - (tonumber(d.at) or 0)
    if ago <= DONE_S then
      out[#out + 1] = string.format("%s — %s (%s, %d min ago)", tostring(d.title), tostring(d.what or "reached"),
        tostring(d.zone or "?"), math.floor(ago / 60))
    end
  end
  return #out > 0 and out or nil
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
      mk.done and " (done)" or d and string.format(" (%d m)", d) or "")
  end
  local waiting = {}
  for _, mk in ipairs(saved) do
    if (mk.tasks or mk.npc) and not mk.done and not mk.back then waiting[#waiting + 1] = bare(mk.title) end
  end
  if #waiting > 0 then
    out[#out + 1] = "These stops leave the route when what is to be done there is done, not when the player reaches them: "
      .. table.concat(waiting, ", ")
  end
  local cur, locked, first = currentIndex(saved), {}, nil
  for i, mk in ipairs(saved) do
    if not mk.done then first = i break end
  end
  if cur and cur ~= first then
    out[#out + 1] = string.format("The player is heading to stop %d (%s) now, not to the first one.", cur, bare(saved[cur].title))
  end
  for _, mk in ipairs(saved) do
    if mk.locked then locked[#locked + 1] = bare(mk.title) end
  end
  if #locked > 0 then
    out[#out + 1] = "Locked by the player (these stops stay on the route until they unlock them): " .. table.concat(locked, ", ")
  end
  local gone = ns.char and ns.char.marksRemoved
  if gone and #gone > 0 then out[#out + 1] = "Taken off the route by the player: " .. table.concat(gone, ", ") end
  if ns.char and ns.char.routeRejected then
    out[#out + 1] = "The player rejected the change of this route you proposed last: leave the route as it is unless they ask."
  end
  return out
end

------------------------------------------------------------------------ keeping a planned route up to date
-- With the route's switch on ("auto" on its strip; a new route starts as Settings say, off by default) a change in
-- the quest log asks the AI to update the route it planned, the way it planned it. Progress (3/6 -> 4/6) is not a
-- change. A single mark is not a route: only marks that came from plan_route are kept up to date.
--
-- An update costs the player half a minute of an AI at work, so it is asked for what the route does not hold:
--   * A route that says what its stops are for (their tasks) is not asked about what it was made for: an objective
--     done or a quest ready to turn in tick their stops off by themselves. It is asked when a quest was taken or
--     turned in (which opens what comes next), or when the next step of a quest is not on the route.
--   * Changes that follow one another (three quests turned in and two taken in one village) are one update: after a
--     turn-in or a quest taken the add-on waits a little longer for the next one.
--   * The route on the map is the one made for the quest log as it is: an answer written while another quest was
--     taken is not put on the map, and the update is asked again with everything that changed.
local SETTLE_S = 1 -- the log changes in bursts (a turn-in, then the next quest): one look once it has settled
local HUB_S = 1.5 -- after a turn-in or a quest taken: the next click (the next part, a quest beside) usually follows
local CHECK_S = 3 -- the AI looks at the route the planner made this long after it, unless the quest log changes first
local checkSaid, checkTimer -- what changed since the AI last looked at the route; the timer of its look
local ALERT_S = 3.5 -- an update still without its answer after this long is told on the screen
local settleAt -- when the look is due
local sentLog -- the quest log when the update that is waited for was asked
local LOGIN_S = 30 -- right after a loading screen an empty quest log may just not be filled yet
local base, enteredAt, settle -- the quest log the route was made for; the time of the last loading screen; the timer
local RETRY_S = 30 -- an update that got no answer is asked once more after this long
local asked, retried -- the quest log before the update that is waited for; that update was asked a second time
local sentMap -- the map the player was on when the update that is waited for was asked
local held, looking -- the stop an update waits for; the stops are being looked at for an update right now
local HOLD_M, HOLD_MOB_M = 60, 150 -- how near that stop the player still is at it (a creature's ground is wide)

-- { order = { questID ... }, quests = { [questID] = { title, complete, objectives = { text or false ... } } } }
local function questLog()
  local log = { order = {}, quests = {} }
  if not (C_QuestLog and C_QuestLog.GetNumQuestLogEntries) then return log end
  for i = 1, (ns.safe(C_QuestLog.GetNumQuestLogEntries) or 0) do
    local q = ns.safe(C_QuestLog.GetInfo, i)
    if type(q) == "table" and not q.isHeader and q.questID and q.title then
      local done, texts = {}, {}
      for n, ob in ipairs(ns.safe(C_QuestLog.GetQuestObjectives, q.questID) or {}) do
        local text = ns.clean(ob.text) or ("objective " .. n)
        done[n] = ob.finished and text or false
        texts[n] = text:lower()
      end
      log.order[#log.order + 1] = q.questID
      log.quests[q.questID] = { title = q.title, complete = ns.safe(C_QuestLog.IsComplete, q.questID) and true or false,
        objectives = done, texts = texts }
    end
  end
  return log
end

M.QuestLog = questLog

-- is the player in this zone (on its map or a map inside it)?
local function inZone(zone)
  return mapAround(tostring(zone):lower()) ~= nil
end
M.inZone = inZone

-- The objective of the quest log a task is for, by the task's label: its number; nil when the label names none (a
-- stop made from a name of the database that the quest log does not use, or a mark without a label).
local function objectiveOf(t, q)
  if not (t.label and q) then return nil end
  local key = t.label:lower()
  for n, text in ipairs(q.texts) do
    if text:find(key, 1, true) then return n end
  end
end

-- the quest of the log a task is for: by its id, else by its title
local function questOf(t, log)
  if t.id then return log.quests[t.id] end
  for _, id in ipairs(log.order) do
    if log.quests[id].title == t.quest then return log.quests[id] end
  end
end

-- Is everything that was to be done at this stop done? An objective: finished, or its quest complete or gone. A
-- turn-in: the quest has left the log. A pick-up: the quest is in the log. The way into a zone: the player is in
-- that zone (the place of a portal or a boat is rarely exact, so standing there proves nothing). Right after a
-- loading screen an empty quest log proves nothing.
-- The quests the route still has a stop to pick up (that stop not done, the quest not in the log, not through with it):
-- a route is planned with the whole of such a quest, and what is to be done for it further on is not done because
-- the quest log does not hold it yet.
local function awaited(log, byTitle)
  local out = {}
  for _, mk in ipairs(list()) do
    for _, t in ipairs(not mk.done and mk.tasks or {}) do
      if t.kind == "pick_up" and t.quest then
        local has
        if t.id then has = log.quests[t.id] ~= nil else has = byTitle[t.quest] ~= nil end
        local through = t.id and C_QuestLog.IsQuestFlaggedCompleted and C_QuestLog.IsQuestFlaggedCompleted(t.id)
        if not has and not through then out[t.id or t.quest] = true end
      end
    end
  end
  return out
end

-- Does the route still have a stop where this quest (its title) is to be picked up?
function M.AwaitsQuest(title)
  for _, mk in ipairs(list()) do
    for _, t in ipairs(not mk.done and mk.tasks or {}) do
      if t.kind == "pick_up" and t.quest == title then return true end
    end
  end
  return false
end

M.objectiveOf = objectiveOf

function M.TasksDone(mk, log)
  local unsure = #log.order == 0 and GetTime() - (enteredAt or 0) < LOGIN_S -- the quest log may not be filled yet
  local byTitle = {}
  for _, id in ipairs(log.order) do byTitle[log.quests[id].title] = log.quests[id] end
  local waits -- the quests still to be picked up on the route, looked up when a task's quest is not in the log
  for _, t in ipairs(mk.tasks or {}) do
    local q
    if t.id then q = log.quests[t.id] elseif t.quest then q = byTitle[t.quest] end
    local done
    if t.kind == "travel" then
      done = inZone(t.zone) -- where the player is does not depend on the quest log
    elseif t.kind == "pick_up" and t.id and C_QuestLog.IsQuestFlaggedCompleted and C_QuestLog.IsQuestFlaggedCompleted(t.id) then
      done = true -- through with it already (picked up and turned in at once: it never shows in the log again)
    elseif unsure then
      done = false
    elseif t.kind == "pick_up" then
      done = q ~= nil
    elseif q == nil and t.quest and (t.kind == "turn_in" or t.kind == "objective") then
      waits = waits or awaited(log, byTitle)
      done = not waits[t.id or t.quest] -- gone from the log is done; not taken yet is not
    elseif t.kind == "turn_in" then
      done = q == nil
    else
      done = q == nil or q.complete
      local n = not done and objectiveOf(t, q)
      if n then done = q.objectives[n] and true or false end
    end
    if not done then return false end
  end
  return true
end

-- does the route say what its stops are for?
local function knowsTasks()
  for _, mk in ipairs(list()) do
    for _, t in ipairs(mk.tasks or {}) do
      if t.quest then return true end
    end
  end
  return false
end

-- Does the route hold the next step of this quest: a stop, not done, for its turn-in or for an objective still open?
-- A stop that cannot tell which objective it is for proves nothing, and cannot be ticked off either: the route does
-- not hold the quest then, whatever else is on it.
local function holds(title, log)
  local found = false
  for _, mk in ipairs(list()) do
    for _, t in ipairs(not mk.done and mk.tasks or {}) do
      if t.quest == title and t.kind == "turn_in" then
        found = true
      elseif t.quest == title and t.kind == "objective" then
        local q = questOf(t, log)
        if q and not q.complete and not objectiveOf(t, q) then return false end
        if not M.TasksDone({ tasks = { t } }, log) then found = true end
      end
    end
  end
  return found
end

-- the quest of a stop, not done, that cannot tell which objective it is for; nil when every stop can
local function cannotTell(log)
  for _, mk in ipairs(list()) do
    for _, t in ipairs(not mk.done and mk.tasks or {}) do
      local q = t.kind == "objective" and questOf(t, log)
      if q and not q.complete and not objectiveOf(t, q) then return q.title end
    end
  end
end

-- What changed between two looks at the quest log that the route does not hold, in words for the AI; nil when
-- nothing did. A route without tasks cannot tell: every change is told then.
local function changes(old, new)
  local took, said, gone = {}, {}, {}
  local smart = knowsTasks()
  for _, id in ipairs(old.order) do
    if not new.quests[id] then gone[old.quests[id].title] = true end
  end
  for _, id in ipairs(new.order) do
    local q, was = new.quests[id], old.quests[id]
    if ns.char and ns.char.routeDropped then ns.char.routeDropped[id] = nil end -- taken again
    if not was and gone[q.title] then -- a quest chain: the next part has the same name
      gone[q.title] = false
      said[#said + 1] = "[" .. q.title .. "] turned in, its next part taken"
    elseif not was and smart and holds(q.title, new) then
      -- taken, and the route has what comes next of it (it was planned whole, from where it is taken to where it
      -- is turned in): nothing to ask
    elseif not was then
      took[#took + 1] = "[" .. q.title .. "]"
    elseif smart and holds(q.title, new) then
      -- the next step of this quest is a stop of the route: nothing to ask
    elseif q.complete and not was.complete then
      said[#said + 1] = "[" .. q.title .. "] is ready to turn in"
    elseif not q.complete then
      for n, text in ipairs(q.objectives) do
        if text and not was.objectives[n] then said[#said + 1] = "[" .. q.title .. "]: " .. text .. " done" end
      end
    end
  end
  for _, id in ipairs(old.order) do
    local title = old.quests[id].title
    -- gone from the log, and the game does not have it as turned in: abandoned (noted for the quests of a route)
    if not new.quests[id] and ns.char and (ns.char.routeQuests or {})[id]
      and not (C_QuestLog.IsQuestFlaggedCompleted and ns.safe(C_QuestLog.IsQuestFlaggedCompleted, id)) then
      ns.char.routeDropped = ns.char.routeDropped or {}
      ns.char.routeDropped[id] = true
    end
    -- A quest that is gone: its stops are done by themselves, but a turn-in opens what comes next (the next part,
    -- new quests offered there), so the route is looked at again. The app answers at once when nothing changes.
    if not new.quests[id] and gone[title] then said[#said + 1] = "[" .. title .. "] left the log" end
  end
  if #took > 0 then table.insert(said, 1, "took " .. table.concat(took, ", ")) end
  return #said > 0 and table.concat(said, "; ") or nil
end

-- The stop the player is in the middle of: some of what is to be done there is done, some is not, and the player is
-- still there (two quests to pick up at one NPC and one taken; a turn-in done and the next part not taken yet).
local function midStop(log)
  local mapID, px, py = playerSpot()
  if not px then return nil end
  for _, mk in ipairs(list()) do
    if not mk.done and not mk.back and mk.tasks then
      local did, left = 0, 0
      for _, t in ipairs(mk.tasks) do
        if t.quest and M.TasksDone({ tasks = { t } }, log) then did = did + 1 elseif t.quest then left = left + 1 end
      end
      local d = did > 0 and left > 0 and metersTo(mk, mapID, px, py)
      if d and d <= (mk.mob and HOLD_MOB_M or HOLD_M) then return mk end
    end
  end
end

-- the route on the map was made for the quest log as it is now (a planned route arrived, or the player accepted one)
function M.RouteKnown()
  base = questLog()
end

local function wanted()
  return ns.char and ns.char.routeAuto == true and ns.char.routePlanned and M.Count() > 0
end

-- the switch of the route on the map (its strip)
function M.SetAuto(on)
  if not ns.char then return end
  ns.char.routeAuto = on and true or nil
  if on then M.RouteKnown() end -- what changed before it was on is not news
  changed()
end

-- the answer to the add-on's own update of the route: the same route planned again, its switch stays
function M.IsUpdate(msgId)
  return msgId ~= nil and msgId == M.updateMsgId
end

local function settled()
  settle, settleAt = nil, nil
  local now = questLog()
  if not base then -- what the game shows after a loading screen is where changes are counted from
    if #now.order > 0 or GetTime() - (enteredAt or 0) > LOGIN_S then
      base = now
      -- A route whose stops cannot tell which objective they are for (made by an older version, or from names the
      -- quest log does not use) cannot be followed: it is planned again now, once, not at the next change.
      local odd = wanted() and not M.repaired and cannotTell(now)
      if odd and ns.CanSend() then
        M.repaired = true
        asked, sentLog = base, now
        sentMap = playerSpot() -- (the zone it is planned from)
        M.updateMsgId = ns.Ask("[Quest log changed] the stops for [" .. odd .. "] do not match its objectives. "
          .. "Update the route on the map.", { auto = true, news = "Updating the route", fast = true })
      end
    end
    return
  end
  local said = wanted() and changes(base, now)
  if said then
    -- The route is updated in step with its stops: what the player just did ticks its stop off first, and the
    -- update waits while they are in the middle of a stop (one of its two quests taken). Asked after the first
    -- quest, it changed the route under the player's hands, and was asked again a moment later for the second.
    looking = true
    pcall(M.Check)
    looking = nil
    said = changes(base, now)
  end
  if not said then
    base, held = now, nil
    return
  end
  local mid = midStop(now)
  if mid then
    if held ~= mid and ns.Log then
      ns.Log("route", string.format("the update waits until the stop is done: %s (%s)", bare(mid.title), said))
    end
    held = mid
    return
  end
  held = nil
  if not ns.CanSend() then -- an answer is being written: look again in a moment (what changed adds up)
    settle = C_Timer.NewTimer(SETTLE_S, settled)
    return
  end
  asked, base, sentLog = base, now, now
  sentMap = playerSpot() -- (the zone it is planned from)
  if ns.Log then ns.Log("route", "update asked: " .. said) end
  checkSaid = checkSaid and (checkSaid .. "; " .. said) or said -- what the AI is to look at after the planner
  -- fast: the app's planner answers at once, the way the route was planned; the AI checks the result after
  M.updateMsgId = ns.Ask("[Quest log changed] " .. said .. ". Update the route on the map.",
    { auto = true, news = "Updating the route", fast = true })
  -- an update asked again because a quest overtook it is still the update the screen announced
  -- The screen says so after a moment: an update that changes nothing is answered at once, and is not worth a word.
  if M.updateMsgId and ns.UI.RouteAlert and not M.again then
    local id = M.updateMsgId
    C_Timer.After(ALERT_S, function()
      if M.updateMsgId == id and ns.pending and ns.pending.msgId == id then
        M.alerted = id
        ns.UI.RouteAlert("start")
      end
    end)
  end
  M.again = nil
end

-- After every look at the stops: the update that waited for a stop is asked once that stop is done, or the player
-- has walked away from it.
function M.Recheck()
  if not held or settle or looking then return end
  if not midStop(questLog()) then settle = C_Timer.NewTimer(0.3, settled) end
end

-- The player is in another zone than the stop the arrow leads to, and has been for a while (not on a flight, not in a
-- dungeon, not as a ghost): they left the route (a flight of their own, the hearthstone, a boat taken early). The route
-- is updated once for that zone, from where they are: what is to do here first, the way back to the stops left behind
-- after it. Crossing a border for a moment asks nothing.
local STRAY_S = 15
local strayMap, strayAt, strayAsked
function M.Strayed(mapID, saved)
  local cur = saved[currentIndex(saved) or 0]
  local away = wanted() and cur and mapID and cur.mapID ~= mapID
    and not (UnitOnTaxi and UnitOnTaxi("player")) and not (IsInInstance and IsInInstance())
    and not (UnitIsDeadOrGhost and UnitIsDeadOrGhost("player"))
  if not away then
    strayMap, strayAt = nil, nil
    -- (on a flight "through a way of the route" waits for the landing)
    if cur and cur.mapID == mapID then strayAsked, M.wentThrough = nil, nil end
    return
  end
  if M.wentThrough then -- through a way of the route, and the arrow leads into another zone: updated from here at once
    M.wentThrough = nil
    strayMap, strayAt, strayAsked = mapID, GetTime() - STRAY_S, nil
  elseif strayMap ~= mapID then
    strayMap, strayAt = mapID, GetTime()
    return
  end
  if strayAsked == mapID or GetTime() - strayAt < STRAY_S or settle or not base or not ns.CanSend() then return end
  strayAsked = mapID
  local info = C_Map.GetMapInfo(mapID)
  local said = "the player went to " .. tostring(info and info.name or "another zone") .. ", away from the route"
  asked, sentLog = base, questLog()
  sentMap = playerSpot() -- (the zone it is planned from)
  if ns.Log then ns.Log("route", "update asked: " .. said) end
  checkSaid = checkSaid and (checkSaid .. "; " .. said) or said
  M.updateMsgId = ns.Ask("[Quest log changed] " .. said .. ". Update the route on the map.",
    { auto = true, news = "Updating the route", fast = true })
end

-- The update got its answer: the change in the quest log is dealt with.
function M.UpdateAnswered()
  asked, retried, sentLog, sentMap = nil, nil, nil, nil
end

-- The AI looks at the route the planner just put on the map (what changed since it last looked): quietly, a moment
-- later, and only when nothing else waits. Its answer is news only when it changes the route.
local function askCheck()
  checkTimer = nil
  if not checkSaid or not wanted() then
    checkSaid = nil
    return
  end
  if settle or not ns.CanSend() then -- an update or a question goes first
    checkTimer = C_Timer.NewTimer(2, askCheck)
    return
  end
  local said = checkSaid
  checkSaid = nil
  asked, sentLog = base, base
  sentMap = playerSpot() -- (the zone it is planned from)
  if ns.Log then ns.Log("route", "check asked: " .. said) end
  if ns.UI.lastNews then ns.UI.heldNews = ns.UI.lastNews end -- it writes into the line of the update
  M.updateMsgId = ns.Ask("[Route check] " .. said .. ". Check the route on the map.",
    { auto = true, news = "Checking the route", check = true })
end

function M.ScheduleCheck()
  if checkTimer then checkTimer:Cancel() end
  checkTimer = checkSaid and C_Timer.NewTimer(CHECK_S, askCheck) or nil
end

-- Was a quest taken while the update was being written, or has the player gone into another zone? Its answer was
-- made without that quest, or from where the player is no more.
function M.UpdateIsStale()
  if not sentLog then return false end
  local here = playerSpot()
  if sentMap and here and here ~= sentMap then return true end
  for _, id in ipairs(questLog().order) do
    if not sentLog.quests[id] then return true end
  end
  return false
end

-- The answer of a stale update is not used: the update is asked again at once, with everything that changed since
-- the route was made.
function M.UpdateSuperseded()
  if ns.Log then ns.Log("route", "an update was overtaken by a quest taken or by a way into another zone: asked again") end
  if asked then base = asked end
  asked, sentLog, sentMap, M.again = nil, nil, nil, true
  if settle then settle:Cancel() end
  settle = C_Timer.NewTimer(0.5, settled)
end

-- The update got no answer (the app was not running, or was just restarted): the change in the quest log still
-- stands, so it is asked once more in a while; after that the next change in the quest log asks again, with all of it.
function M.UpdateFailed()
  if ns.Log then ns.Log("route", "an update got no answer") end
  if asked then base, asked = asked, nil end
  sentLog, M.again = nil, nil
  if retried then return end
  retried = true
  if settle then settle:Cancel() end
  settle = C_Timer.NewTimer(RETRY_S, settled)
end

local function questLogChanged(_, event)
  if checkTimer then checkTimer:Cancel() checkTimer = nil end -- the next update asks for its own check
  -- one look once the log has settled; the wait a turn-in or a taken quest asked for is not cut short by the
  -- plain updates the game sends along with it
  local wait = (event == "QUEST_TURNED_IN" or event == "QUEST_ACCEPTED") and HUB_S or SETTLE_S
  settleAt = math.max(settleAt or 0, GetTime() + wait)
  if settle then settle:Cancel() end
  settle = C_Timer.NewTimer(settleAt - GetTime(), settled)
end

do
  local f = CreateFrame("Frame")
  for _, e in ipairs({ "QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_TURNED_IN", "QUEST_REMOVED" }) do
    pcall(f.RegisterEvent, f, e)
  end
  f:SetScript("OnEvent", questLogChanged)
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
  base, enteredAt = nil, GetTime() -- the quest log is counted again from what the game shows after loading
  C_Timer.After(2, M.Restore) -- TomTom is ready by then
  if not M.ticker then
    M.ticker = C_Timer.NewTicker(2, function()
      local ok, err = pcall(M.Check)
      if not ok and err ~= M.lastError and ns.Log then
        M.lastError = err
        ns.Log("error", "Map.Check: " .. tostring(err))
      end
    end)
  end
end)

-- The quests routes were made for that the game says are turned in, and the ones the player abandoned (their
-- numbers): the app plans no stop for them. (An abandoned quest put back as one to pick up came back on the route at
-- the next update.) Taken again, a quest is in the log, and that counts first.
function M.RouteDone()
  local out = {}
  local dropped = ns.char and ns.char.routeDropped or {}
  for id in pairs(ns.char and ns.char.routeQuests or {}) do
    if (C_QuestLog.IsQuestFlaggedCompleted and ns.safe(C_QuestLog.IsQuestFlaggedCompleted, id)) or dropped[id] then
      out[#out + 1] = id
    end
  end
  table.sort(out)
  for i = 1, #out - 80 do table.remove(out, 1) end -- (the newest: quest numbers grow with the game's age)
  return #out > 0 and out or nil
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.marks = M.Section
  ns.Collect.sections.detours = M.Detours
  ns.Collect.sections.routeDone = M.RouteDone
  ns.Collect.sections.doneLately = M.DoneLately
end
