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
local function waypointTitle(mk, i)
  local what = M.What and M.What(mk) -- what to do there, in brackets: the arrow says it while the player walks
  return string.format("ForeverAI: %d. %s%s", i, (tostring(mk.title):gsub("^%d+[%.%)]%s*", "")),
    what and (" (" .. what .. ")") or "")
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

-- adds the marks to the ones on the map (UI.MarksCard clears first unless the answer adds stops); a place that is on
-- the route already is not marked twice
function M.Apply(marks)
  local placed, saved, revived = {}, list(), false
  for _, mk in ipairs(marks or {}) do
    local mapID = M.FindMap(mk.zone)
    if mapID then
      local info = C_Map.GetMapInfo(mapID)
      local e = { title = tostring(mk.title), x = tonumber(mk.x) or 0, y = tonumber(mk.y) or 0, mapID = mapID,
        zone = info and info.name or mk.zone, mob = type(mk.mob) == "string" and mk.mob or nil,
        note = type(mk.note) == "string" and mk.note ~= "" and cut(mk.note, 120) or nil }
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
          e.tasks[#e.tasks + 1] = { kind = t.kind, quest = t.quest, id = tonumber(t.id),
            label = type(t.label) == "string" and t.label or nil }
        end
      end
      local twice, again = false, nil
      for _, other in ipairs(saved) do
        if samePlace(other, e) then
          twice = true
          if other.done then again = other end
          break
        end
      end
      if again then -- a stop that was done is needed again (another quest at the same NPC): it is a stop to do
        again.done, again.title, again.mob, again.tasks, again.note = nil, e.title, e.mob, e.tasks, e.note
        again.back, again.away = nil, nil
        placed[#placed + 1] = again
        revived = true
      elseif not twice then
        saved[#saved + 1] = e
        place(e, #saved == 1, #saved)
        placed[#placed + 1] = e
      end
    end
  end
  if revived then M.Restore() end -- the map and the arrow follow the list
  M.last = placed
  changed()
  return placed, usesTomTom() and "TomTom" or "map pin"
end

function M.Clear()
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

local function metersTo(mk, mapID, px, py)
  if not px or mapID ~= mk.mapID then return nil end
  return ns.MapMeters(mapID, px, py, mk.x, mk.y)
end

-- the creature a stop leads to, lower case: the one it was marked with, else its title without number and brackets
local function creatureOf(mk)
  return (mk.mob or (tostring(mk.title):gsub("^%d+[%.%)]%s*", ""):gsub("%s*%b()%s*$", ""))):lower()
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
  local moved, finishedNow = false, {}
  local log -- the quest log, read once and only when a stop waits for its quest
  -- the ways into zones follow one another: the way back into the zone the player stands in is not taken before
  -- the way out of it
  local onTheWay = true
  for i, mk in ipairs(saved) do
    local travels = mk.tasks and mk.tasks[1] and mk.tasks[1].kind == "travel"
    if not mk.done and not mk.locked then
      local finished
      if mk.back then
        -- The player went back to a stop that was done: what was to be done there is done already, so being
        -- there is what finishes it, once they have been away from it (a click while standing on it keeps it).
        local d = metersTo(mk, mapID, px, py)
        if not d or d > REACHED_M then mk.away = true end
        finished = mk.away and d and d <= REACHED_M
      elseif travels and not onTheWay then
        finished = false
      elseif mk.tasks then
        log = log or M.QuestLog()
        finished = M.TasksDone(mk, log) -- done is what counts, not being there
      else
        local d = metersTo(mk, mapID, px, py)
        finished = d and d <= REACHED_M and not (ns.Track and ns.Track.Watches and ns.Track.Watches(creatureOf(mk)))
      end
      if finished then
        M.Finish(saved, i)
        moved = true
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
function M.Finish(saved, i)
  local mk = saved[i]
  mk.done, mk.back, mk.away = true, nil, nil
  if mk.current then
    mk.current = nil
    for n = i + 1, #saved do
      if not saved[n].done then saved[n].current = true break end
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
function M.Done(names)
  local saved, gone, finishedNow = list(), false, {}
  for i, mk in ipairs(saved) do
    if names[creatureOf(mk)] and not mk.locked and not mk.done then
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
  setCurrent(saved, saved[i])
  refresh()
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
  M.Clear()
  local placed, how = M.Apply(marks)
  keepLocked(kept)
  return placed, how
end

-- The AI's change to the route (its edit_route tool): { clear = true }, or { remove = { { title, x, y } ... },
-- order = { { title, x, y } ... } } with the stops as the "marks" section named them. Stops not named in ``order``
-- follow in the order they had.
function M.Edit(edit)
  if type(edit) ~= "table" then return end
  if edit.clear then
    local kept = lockedStops()
    M.Clear()
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
  refresh()
end

-- What to do at a stop, in a few words for the list: the note it was marked with, else what its tasks say
-- ("turn in A, B; pick up C", "Dewy Lasher Frond for The Fronds", "go on to Darnassus"). nil for a stop that is
-- only a place.
local WHAT_MAX = 90
local function whatToDo(mk)
  if mk.note then return mk.note end
  local parts, at = {}, {}
  local function add(verb, name)
    if at[verb] then
      parts[at[verb]] = parts[at[verb]] .. ", " .. name
    else
      parts[#parts + 1] = verb .. " " .. name
      at[verb] = #parts
    end
  end
  for _, t in ipairs(mk.tasks or {}) do
    if t.kind == "travel" then
      add("go on to", t.zone)
    elseif t.kind == "turn_in" then
      add("turn in", t.quest)
    elseif t.kind == "pick_up" then
      add(at["turn in"] and "then pick up" or "pick up", t.quest)
    elseif t.label and t.label:lower() ~= placeName(mk.title) then
      parts[#parts + 1] = t.label .. " for " .. t.quest
    else -- the stop is named after what the objective wants
      add("for", t.quest)
    end
  end
  if #parts == 0 then return nil end
  local text = table.concat(parts, "; ")
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
    out[i] = { title = bare(mk.title), what = whatToDo(mk), zone = mk.zone, x = mk.x, y = mk.y,
      meters = metersTo(mk, mapID, px, py),
      -- a stop the player went back to has its tasks done already: it is done when it is reached
      current = i == cur, locked = mk.locked == true, waits = not mk.back and waits or nil, done = mk.done == true }
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
      mk.done and " (done)" or d and string.format(" (%d m)", d) or "")
  end
  local waiting = {}
  for _, mk in ipairs(saved) do
    if mk.tasks and not mk.done and not mk.back then waiting[#waiting + 1] = bare(mk.title) end
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
local SETTLE_S = 3 -- the log changes in bursts (a turn-in, then the next quest): one look once it has settled
local HUB_S = 8 -- after a turn-in or a quest taken: the next part, or the NPC beside, usually follows
local ALERT_S = 3.5 -- an update still without its answer after this long is told on the screen
local settleAt -- when the look is due
local sentLog -- the quest log when the update that is waited for was asked
local LOGIN_S = 30 -- right after a loading screen an empty quest log may just not be filled yet
local base, enteredAt, settle -- the quest log the route was made for; the time of the last loading screen; the timer
local RETRY_S = 30 -- an update that got no answer is asked once more after this long
local asked, retried -- the quest log before the update that is waited for; that update was asked a second time

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
function M.TasksDone(mk, log)
  local unsure = #log.order == 0 and GetTime() - (enteredAt or 0) < LOGIN_S -- the quest log may not be filled yet
  local byTitle = {}
  for _, id in ipairs(log.order) do byTitle[log.quests[id].title] = log.quests[id] end
  for _, t in ipairs(mk.tasks or {}) do
    local q
    if t.id then q = log.quests[t.id] elseif t.quest then q = byTitle[t.quest] end
    local done
    if t.kind == "travel" then
      done = inZone(t.zone) -- where the player is does not depend on the quest log
    elseif unsure then
      done = false
    elseif t.kind == "pick_up" then
      done = q ~= nil
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
    if not was and gone[q.title] then -- a quest chain: the next part has the same name
      gone[q.title] = false
      said[#said + 1] = "[" .. q.title .. "] turned in, its next part taken"
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
    -- A quest that is gone: its stops are done by themselves, but a turn-in opens what comes next (the next part,
    -- new quests offered there), so the route is looked at again. The app answers at once when nothing changes.
    if not new.quests[id] and gone[title] then said[#said + 1] = "[" .. title .. "] left the log" end
  end
  if #took > 0 then table.insert(said, 1, "took " .. table.concat(took, ", ")) end
  return #said > 0 and table.concat(said, "; ") or nil
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
        M.updateMsgId = ns.Ask("[Quest log changed] the stops for [" .. odd .. "] do not match its objectives. "
          .. "Update the route on the map.", { auto = true, news = "Updating the route" })
      end
    end
    return
  end
  local said = wanted() and changes(base, now)
  if not said then
    base = now
    return
  end
  if not ns.CanSend() then -- an answer is being written: look again in a moment (what changed adds up)
    settle = C_Timer.NewTimer(SETTLE_S, settled)
    return
  end
  asked, base, sentLog = base, now, now
  M.updateMsgId = ns.Ask("[Quest log changed] " .. said .. ". Update the route on the map.",
    { auto = true, news = "Updating the route" })
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

-- The update got its answer: the change in the quest log is dealt with.
function M.UpdateAnswered()
  asked, retried, sentLog = nil, nil, nil
end

-- Was a quest taken while the update was being written? Its answer was made without that quest.
function M.UpdateIsStale()
  if not sentLog then return false end
  for _, id in ipairs(questLog().order) do
    if not sentLog.quests[id] then return true end
  end
  return false
end

-- The answer of a stale update is not used: the update is asked again at once, with everything that changed since
-- the route was made.
function M.UpdateSuperseded()
  if asked then base = asked end
  asked, sentLog, M.again = nil, nil, true
  if settle then settle:Cancel() end
  settle = C_Timer.NewTimer(0.5, settled)
end

-- The update got no answer (the app was not running, or was just restarted): the change in the quest log still
-- stands, so it is asked once more in a while; after that the next change in the quest log asks again, with all of it.
function M.UpdateFailed()
  if asked then base, asked = asked, nil end
  sentLog, M.again = nil, nil
  if retried then return end
  retried = true
  if settle then settle:Cancel() end
  settle = C_Timer.NewTimer(RETRY_S, settled)
end

local function questLogChanged(_, event)
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
  if not M.ticker then M.ticker = C_Timer.NewTicker(2, function() pcall(M.Check) end) end
end)

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.marks = M.Section
end
