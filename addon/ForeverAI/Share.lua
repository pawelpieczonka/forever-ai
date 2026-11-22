-- ForeverAI: the route shared with the party. A route the AI planned for the party (plan_route with party) goes to the
-- other players of the party who have ForeverAI too, through the game's add-on messages; so does a route the player
-- shares with /fai share. Each of them gets a card with its stops, and Follow puts them on their map. While they
-- follow, every change of the route reaches them by itself. The stops go as places, with what is to be done there in
-- words: each player's quest log differs, so a follower's stop is done when they get there (and deal with the NPC
-- there), not by the quests of the player who shares it.
local ADDON, ns = ...
local Sh = {}
ns.Share = Sh

local PREFIX = "ForeverAI"
local CHUNK = 220 -- characters of a route in one message (the game takes 255 with the header)
local MAX_STOPS = 20
local RESEND_S = 3 -- a change of the route is sent this long after it (several changes at once go as one)
local KEEP_S = 30 -- the parts of a route that never got all there are dropped after this long

local inbox = {} -- sender -> { id, n, parts = {}, at }
local registered

local function register()
  if registered then return true end
  if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then
    registered = ns.safe(C_ChatInfo.RegisterAddonMessagePrefix, PREFIX) ~= false
  end
  return registered
end

-- the text of a field: no separators of the message, no escape codes of the game's chat
local function field(text, n)
  local s = tostring(text or ""):gsub("[|~%^\r\n]", " ")
  return s:sub(1, n or 60)
end

local function inParty() return (GetNumGroupMembers() or 0) > 1 and not (IsInRaid and IsInRaid()) end

-- the party member who sent a message (by their name, also "Name-Realm"), nil for anybody else (the player too)
local function member(sender)
  local short = tostring(sender or ""):match("^[^%-]+")
  for i = 1, 4 do
    local unit = "party" .. i
    if UnitExists(unit) and ns.safe(UnitName, unit) == short then return short end
  end
end

-- the stops of the route on the map that are left: number. title, x, y, zone, what to do there, the creature, the zone
-- a way leads into
function Sh.Stops()
  local out = {}
  for _, mk in ipairs(ns.char and ns.char.marks or {}) do
    if not mk.done and #out < MAX_STOPS then
      local t = mk.tasks and mk.tasks[1]
      out[#out + 1] = { title = (tostring(mk.title):gsub("^%d+[%.%)]%s*", "")), x = mk.x, y = mk.y, zone = mk.zone,
        note = ns.Map.What and ns.Map.What(mk) or mk.note, mob = mk.mob,
        travel = t and t.kind == "travel" and t.zone or nil }
    end
  end
  return out
end

local function encode(stops)
  local rows = {}
  for _, s in ipairs(stops) do
    rows[#rows + 1] = table.concat({ string.format("%.1f", tonumber(s.x) or 0), string.format("%.1f", tonumber(s.y) or 0),
      field(s.zone), field(s.title), field(s.note, 120), field(s.mob), field(s.travel) }, "~")
  end
  return table.concat(rows, "^")
end

local function decode(text)
  local stops = {}
  for row in tostring(text or ""):gmatch("[^%^]+") do
    local f = {}
    for v in (row .. "~"):gmatch("([^~]*)~") do f[#f + 1] = v end
    local x, y = tonumber(f[1]), tonumber(f[2])
    if x and y and x >= 0 and x <= 100 and y >= 0 and y <= 100 and (f[3] or "") ~= "" and (f[4] or "") ~= "" and #stops < MAX_STOPS then
      stops[#stops + 1] = { title = string.format("%d. %s", #stops + 1, f[4]), x = x, y = y, zone = f[3],
        note = f[5] ~= "" and f[5] or nil, mob = f[6] ~= "" and f[6] or nil,
        tasks = f[7] and f[7] ~= "" and { { kind = "travel", zone = f[7] } } or nil }
    end
  end
  return stops
end

-- the route to the party: in parts of CHUNK characters (cut between characters, never inside one)
function Sh.Send()
  if not inParty() or not register() then return false end
  local data = encode(Sh.Stops())
  local parts, at = {}, 1
  repeat
    local stop = math.min(#data, at + CHUNK - 1)
    while stop < #data and stop > at do -- (a byte 0x80-0xBF continues a character)
      local b = data:byte(stop + 1)
      if b < 128 or b >= 192 then break end
      stop = stop - 1
    end
    parts[#parts + 1] = data:sub(at, stop)
    at = stop + 1
  until at > #data
  local id = tostring(math.random(1000, 9999))
  for i, part in ipairs(parts) do
    ns.safe(C_ChatInfo.SendAddonMessage, PREFIX, string.format("R1:%s:%d/%d:%s", id, i, #parts, part), "PARTY")
  end
  if ns.Log then ns.Log("share", string.format("route sent to the party in %d message%s", #parts, #parts == 1 and "" or "s")) end
  return true
end

-- The player shares the route from now on (the AI planned it for the party, or /fai share): sent now, and again after
-- each change of it.
function Sh.Start()
  if not ns.char or #Sh.Stops() == 0 or not inParty() then return false end
  ns.char.sharing = true
  return Sh.Send()
end

function Sh.StopSharing() if ns.char then ns.char.sharing = nil end end

-- an answer marked a route: the player's own now (not the one followed), shared when it was planned for the party,
-- and no longer shared when it is a new route of their own (the add-on's update of a shared route stays shared)
function Sh.Answered(share, update)
  if not ns.char then return end
  ns.char.following = nil
  if share then
    Sh.Start()
  elseif not update then
    Sh.StopSharing()
  end
end

local pending
ns.Map.OnChange = function()
  if not (ns.char and ns.char.sharing) or pending or not inParty() then return end
  pending = true
  C_Timer.After(RESEND_S, function()
    pending = nil
    if ns.char and ns.char.sharing then Sh.Send() end
  end)
end

-- The route a member sent: a card the first time (Follow / No thanks); once followed, it replaces the stops by itself.
function Sh.Follow(from, stops)
  ns.Map.Replace(stops)
  ns.char.following, ns.char.sharing = from, nil
  ns.char.routePlanned, ns.char.routeAuto = nil, nil -- (the route is theirs: the AI here does not update it)
  if ns.UI and ns.UI.UpdateStrip then ns.UI.UpdateStrip() end
end

local function received(from, stops)
  if ns.char.following == from then
    local note
    if #stops == 0 then -- (their route is done: so is the one followed)
      ns.Map.Clear(from .. "'s route is done")
      note = string.format("%s's route is done.", from)
    else
      Sh.Follow(from, stops)
      note = string.format("%s's route changed: %d stop%s left.", from, #stops, #stops == 1 and "" or "s")
    end
    if ns.UI and ns.UI.AddSystemNote then ns.UI.AddSystemNote(note) end
  elseif #stops > 0 and ns.UI and ns.UI.SharedRouteCard then
    ns.UI.SharedRouteCard(from, stops)
  end
end

ns.On("CHAT_MSG_ADDON", function(_, prefix, text, channel, sender)
  if prefix ~= PREFIX or channel ~= "PARTY" or not ns.char then return end
  local from = member(sender)
  if not from then return end
  local id, i, n, part = tostring(text or ""):match("^R1:(%d+):(%d+)/(%d+):(.*)$")
  i, n = tonumber(i), tonumber(n)
  if not (id and i and n and n >= 1 and n <= 20 and i >= 1 and i <= n) then return end
  local box = inbox[from]
  if not box or box.id ~= id or GetTime() - box.at > KEEP_S then
    box = { id = id, n = n, parts = {}, at = GetTime() }
    inbox[from] = box
  end
  box.parts[i] = part
  for k = 1, box.n do
    if not box.parts[k] then return end
  end
  inbox[from] = nil
  received(from, decode(table.concat(box.parts)))
end)

-- out of the party: nothing is shared or followed any more
ns.On("GROUP_ROSTER_UPDATE", function()
  if not ns.char then return end
  if not inParty() then
    ns.char.sharing = nil
    ns.char.following = nil
  elseif ns.char.following and not member(ns.char.following) then
    ns.char.following = nil
  end
end)

ns.On("PLAYER_LOGIN", register)
register()

Sh.encode, Sh.decode = encode, decode
