-- ForeverAI: the player's progress over time (XP, levels, money, sessions), kept per character, so the AI can answer
-- "how long until level 40 at this pace?", "how much gold did I make today, and where did it go?" or "what did I do
-- last session?". Only small numbers are stored, and every list has a fixed cap.
local ADDON, ns = ...
local P = {}
ns.Progress = P

local THROTTLE_S = 60     -- at most one sample a minute (plus every level-up, login and logout)
local TICK_S = 60         -- playing time is counted in steps of about a minute
local MAX_STEP_S = 150    -- a longer step (game frozen, computer asleep) is logged-in time, but not playing
local RELOAD_GAP_S = 120  -- without the game's login/reload flags: a shorter break is a /reload
local DETAIL_DAYS = 3     -- detailed samples are kept this long; older sessions only as summaries
local MAX_SAMPLES = 1500
local MAX_SESSIONS = 40
local MAX_LEVELS = 80
local MAX_ZONES = 5       -- per session summary
local CONTEXT_S = 3       -- a window closed this long ago still explains a money change
local SNAP_SAMPLES = 32   -- the current session in the snapshot, downsampled
local SNAP_SESSIONS = 8
local SNAP_LEVELS = 12

local safe = function(fn, ...) return ns.safe(fn, ...) end

------------------------------------------------------------------------ storage (ForeverAICharDB.progress)
local function db()
  local c = ns.char
  if not c then return nil end
  local d = c.progress
  if type(d) ~= "table" or d.v ~= 1 then
    d = { v = 1, samples = {}, sessions = {}, levels = {}, online = 0, since = time() }
    c.progress = d
  end
  return d
end
P.DB = db

local function zoneName()
  return safe(GetRealZoneText) or safe(GetZoneText) or "?"
end

local function newSession(now)
  local lvl, money = UnitLevel("player") or 1, GetMoney() or 0
  return { start = now, last = now, lvl0 = lvl, xp0 = UnitXP("player") or 0, money0 = money, lvl = lvl,
           xp = UnitXP("player") or 0, xpMax = UnitXPMax("player") or 0, money = money, gain = 0, questXp = 0, act = 0,
           levels = 0, kills = 0, quests = 0, deaths = 0, reloads = 0, earned = {}, spent = {}, zones = {} }
end

local function topZones(z)
  local list = {}
  for name, s in pairs(z or {}) do list[#list + 1] = { name, s } end
  table.sort(list, function(a, b) return a[2] > b[2] end)
  local out = {}
  for i = 1, math.min(MAX_ZONES, #list) do out[list[i][1]] = list[i][2] end
  return out
end

-- the open session becomes a summary (its detailed samples stay for DETAIL_DAYS)
local function closeSession(d)
  local s = d.cur
  d.cur = nil
  if not s then return end
  local stop = s.last or s.start
  if stop - s.start < 60 and s.gain == 0 and not next(s.earned) and not next(s.spent) then return end -- nothing happened
  s.stop, s.last, s.xp, s.xpMax, s.rested = stop, nil, nil, nil, nil
  s.zones = topZones(s.zones)
  table.insert(d.sessions, s)
  while #d.sessions > MAX_SESSIONS do table.remove(d.sessions, 1) end
end

local function prune(d, now)
  local list, cutoff = d.samples, now - DETAIL_DAYS * 86400
  local n, first = #list, 1
  while first <= n and (n - first + 1 > MAX_SAMPLES or (tonumber(list[first]:match("^%d+")) or 0) < cutoff) do
    first = first + 1
  end
  if first > 1 then
    local keep = {}
    for i = first, n do keep[#keep + 1] = list[i] end
    d.samples = keep
  end
end

------------------------------------------------------------------------ time: logged in and playing
-- online: every second logged in (what /played counts); act: the session's playing time, without AFK and freezes
local function tick(d, now)
  local s = d.cur
  if not s then return end
  local dt = now - (s.last or now)
  if dt <= 0 then return end
  d.online = (d.online or 0) + dt
  if dt <= MAX_STEP_S and not safe(UnitIsAFK, "player") then
    s.act = s.act + dt
    local z = zoneName()
    s.zones[z] = (s.zones[z] or 0) + dt
  end
  s.last = now
end

------------------------------------------------------------------------ samples
local lastSampleAt, sampleQueued = -1e9, false

-- XP gained since the last sample; a level-up takes its own sample, so at most one level lies in between
local function xpGain(s, lvl, xp)
  if lvl == s.lvl then return math.max(0, xp - (s.xp or xp)) end
  if lvl > s.lvl then return math.max(0, (s.xpMax or 0) - (s.xp or 0)) + xp end
  return 0
end

function P.Sample(force)
  local d = db()
  if not (d and d.cur) then return end
  local now = time()
  tick(d, now)
  local s = d.cur
  local lvl, xp, xpMax = UnitLevel("player") or s.lvl, UnitXP("player") or 0, UnitXPMax("player") or 0
  local rested, money = GetXPExhaustion() or 0, GetMoney() or 0
  local gained = xpGain(s, lvl, xp)
  -- rested XP grows slowly while resting: only a step of 1% of the level is worth a sample
  local changed = force or gained > 0 or lvl ~= s.lvl or money ~= s.money or xpMax ~= s.xpMax
    or math.abs(rested - (s.rested or rested)) >= math.max(1, xpMax / 100) or (rested == 0) ~= ((s.rested or 0) == 0)
  s.gain = s.gain + gained
  if lvl > s.lvl then s.levels = s.levels + (lvl - s.lvl) end
  s.lvl, s.xp, s.xpMax, s.money = lvl, xp, xpMax, money
  if changed then s.rested = rested end
  lastSampleAt = GetTime()
  if not changed then return end
  local line = string.format("%d %d %d %d %d %d %d", now, s.act, lvl, xp, xpMax, rested, money)
  local zone = zoneName()
  if zone ~= d.lastZone then
    line = line .. "|" .. zone
    d.lastZone = zone
  end
  d.samples[#d.samples + 1] = line
  prune(d, now)
end

-- events ask for a sample; at most one a minute is taken, and the last change of a minute is never lost
local function want()
  if not (ns.char and ns.char.progress and ns.char.progress.cur) then return end
  local wait = THROTTLE_S - (GetTime() - lastSampleAt)
  if wait <= 0 then P.Sample() return end
  if sampleQueued then return end
  sampleQueued = true
  C_Timer.After(wait, function()
    sampleQueued = false
    P.Sample()
  end)
end
P.Want = want

------------------------------------------------------------------------ sessions: login to logout, a /reload continues
local started = false
function P.Begin(isLogin, isReload)
  local d = db()
  if not d then return end
  local now, cur = time(), d.cur
  local continues
  if isReload == true then continues = cur ~= nil
  elseif isLogin == true then continues = false
  else continues = cur ~= nil and now - (cur.last or 0) < RELOAD_GAP_S end
  if continues then
    cur.reloads = (cur.reloads or 0) + 1
  else
    closeSession(d)
    d.cur = newSession(now)
  end
  d.lastZone = nil
  P.lastMoney = GetMoney()
  started = true
  P.Sample(true)
end

------------------------------------------------------------------------ money: what each change came from
-- windows open (or closed a moment ago) and short-lived hints tell the cause; without one it is "other"
local openAt, closedAt, hint = {}, {}, {}
local function opened(cause) openAt[cause], closedAt[cause] = GetTime(), nil end
local function closed(cause)
  if openAt[cause] then closedAt[cause] = GetTime() end
  openAt[cause] = nil
end
local function hinted(cause, seconds) hint[cause] = GetTime() + (seconds or CONTEXT_S) end

function P.Cause(delta)
  local now = GetTime()
  if delta < 0 and (hint.repair or 0) > now then
    hint.repair = nil -- one repair is one payment
    return "repair"
  end
  for _, c in ipairs({ "quest", "loot" }) do
    if (hint[c] or 0) > now then return c end
  end
  local best, bestAt
  for c, t in pairs(openAt) do
    if not bestAt or t > bestAt then best, bestAt = c, t end
  end
  if best then return best end
  for c, t in pairs(closedAt) do
    if now - t <= CONTEXT_S and (not bestAt or t > bestAt) then best, bestAt = c, t end
  end
  return best
end

local function book(cause, delta)
  local d = db()
  local s = d and d.cur
  if not s or delta == 0 then return end
  local into = delta > 0 and s.earned or s.spent
  into[cause] = (into[cause] or 0) + math.abs(delta)
end

local function moneyChanged()
  local m = GetMoney()
  if not m then return end
  local delta = m - (P.lastMoney or m)
  P.lastMoney = m
  if delta ~= 0 and started then
    local cause = P.Cause(delta)
    if cause then
      book(cause, delta)
    else -- the loot message may come right after the money: look again in a moment
      C_Timer.After(0.6, function() book(P.Cause(delta) or "other", delta) end)
    end
  end
  want()
end

------------------------------------------------------------------------ events
local WINDOWS = {
  MERCHANT_SHOW = "vendor", MERCHANT_CLOSED = "vendor", MAIL_SHOW = "mail", MAIL_CLOSED = "mail",
  AUCTION_HOUSE_SHOW = "auction", AUCTION_HOUSE_CLOSED = "auction", TRAINER_SHOW = "trainer", TRAINER_CLOSED = "trainer",
  TAXIMAP_OPENED = "flight", TAXIMAP_CLOSED = "flight", LOOT_OPENED = "loot", LOOT_CLOSED = "loot",
  TRADE_SHOW = "trade", TRADE_CLOSED = "trade", QUEST_COMPLETE = "quest", QUEST_FINISHED = "quest",
  GUILDBANKFRAME_OPENED = "guild bank", GUILDBANKFRAME_CLOSED = "guild bank",
  BARBER_SHOP_OPEN = "barber", BARBER_SHOP_CLOSE = "barber",
}
local CLOSING = { MERCHANT_CLOSED = true, MAIL_CLOSED = true, AUCTION_HOUSE_CLOSED = true, TRAINER_CLOSED = true,
  TAXIMAP_CLOSED = true, LOOT_CLOSED = true, TRADE_CLOSED = true, QUEST_FINISHED = true, GUILDBANKFRAME_CLOSED = true,
  BARBER_SHOP_CLOSE = true }
-- newer clients also report NPC windows as interaction types
local INTERACTIONS = { Merchant = "vendor", MailInfo = "mail", Auctioneer = "auction", Trainer = "trainer",
  TaxiNode = "flight", TradePartner = "trade", GuildBanker = "guild bank", Barber = "barber" }
local interactionCause = {}
if Enum and Enum.PlayerInteractionType then
  for name, cause in pairs(INTERACTIONS) do
    local v = Enum.PlayerInteractionType[name]
    if v then interactionCause[v] = cause end
  end
end

-- "%s dies, you gain %d experience." -> a kill (the part before the first comma, in the client's language)
local killHead = ((COMBATLOG_XPGAIN_FIRSTPERSON or "%s dies, you gain %d experience."):match("^(.-),") or "%s dies")
killHead = killHead:gsub("%%%d+%$", "%%") -- "%1$s" -> "%s"
local KILL = "^" .. killHead:gsub("[%(%)%.%+%-%*%?%[%]%^%$]", "%%%0"):gsub("%%s", ".+"):gsub("%%d", "%%d+")

local function counter(field, n)
  local d = db()
  if d and d.cur then d.cur[field] = (d.cur[field] or 0) + (n or 1) end
end

local handlers = {
  PLAYER_ENTERING_WORLD = function(isLogin, isReload)
    if not started then P.Begin(isLogin, isReload) end
  end,
  PLAYER_LOGOUT = function() P.Sample(true) end,
  PLAYER_XP_UPDATE = function(unit) if unit == nil or unit == "player" then want() end end,
  UPDATE_EXHAUSTION = function() want() end,
  PLAYER_MONEY = moneyChanged,
  PLAYER_LEVEL_UP = function(level)
    -- the new level and XP limit are not readable at once: the level-up sample comes a moment later
    C_Timer.After(1, function()
      P.Sample(true)
      local d = db()
      if not d then return end
      d.levels[#d.levels + 1] = { level = tonumber(level) or UnitLevel("player"), at = time(), online = d.online }
      while #d.levels > MAX_LEVELS do table.remove(d.levels, 1) end
    end)
  end,
  TIME_PLAYED_MSG = function(total, levelTime)
    local d = db()
    if not (d and tonumber(total)) then return end
    tick(d, time())
    d.played = { total = tonumber(total), level = tonumber(levelTime), at = time(), online = d.online,
                 lvl = UnitLevel("player") }
  end,
  CHAT_MSG_MONEY = function() hinted("loot", 1.5) end,
  CHAT_MSG_COMBAT_XP_GAIN = function(msg)
    msg = ns.clean(msg)
    if type(msg) == "string" and msg:find(KILL) then counter("kills") end
  end,
  QUEST_TURNED_IN = function(_, xpReward)
    hinted("quest")
    counter("quests")
    if tonumber(xpReward) then counter("questXp", tonumber(xpReward)) end
  end,
  PLAYER_DEAD = function() counter("deaths") end,
  PLAYER_INTERACTION_MANAGER_FRAME_SHOW = function(kind) if interactionCause[kind] then opened(interactionCause[kind]) end end,
  PLAYER_INTERACTION_MANAGER_FRAME_HIDE = function(kind) if interactionCause[kind] then closed(interactionCause[kind]) end end,
}
for event, cause in pairs(WINDOWS) do
  handlers[event] = CLOSING[event] and function() closed(cause) end or function() opened(cause) end
end

local f = CreateFrame("Frame")
for event in pairs(handlers) do pcall(f.RegisterEvent, f, event) end
f:SetScript("OnEvent", function(_, event, ...)
  local h = handlers[event]
  if h then
    local ok, err = pcall(h, ...)
    if not ok then ns.lastError = "progress " .. event .. ": " .. tostring(err) end
  end
end)
P.handlers = handlers

if hooksecurefunc then
  pcall(hooksecurefunc, "RepairAllItems", function(guild) if not guild then hinted("repair") end end)
end

-- playing time goes on between samples
local function heartbeat()
  local d = ns.char and ns.char.progress
  if started and d and d.cur then tick(d, time()) end
end
if C_Timer and C_Timer.NewTicker then C_Timer.NewTicker(TICK_S, heartbeat) end
P.Heartbeat = heartbeat

------------------------------------------------------------------------ snapshot section
local function parse(line)
  local t, act, lvl, xp, xpMax, rested, money = line:match("^(%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%-?%d+)")
  if not t then return nil end
  return { tonumber(t), tonumber(act), tonumber(lvl), tonumber(xp), tonumber(xpMax), tonumber(rested), tonumber(money) }
end

-- first, last, both sides of every level-up and evenly spread samples in between
local function downsample(list, n)
  if #list <= n then return list end
  local keep, out = { [1] = true, [#list] = true }, {}
  for i = 2, #list do
    if list[i][3] ~= list[i - 1][3] then keep[i - 1], keep[i] = true, true end
  end
  local step = (#list - 1) / (n - 1)
  for k = 0, n - 1 do keep[math.floor(1 + k * step + 0.5)] = true end
  for i = 1, #list do if keep[i] then out[#out + 1] = list[i] end end
  return out
end

local SUMMARY = { "start", "stop", "act", "gain", "questXp", "lvl0", "lvl", "xp0", "money0", "money", "levels", "kills",
  "quests", "deaths", "reloads" }
local function summary(s)
  local out = {}
  for _, k in ipairs(SUMMARY) do out[k] = s[k] end
  if next(s.earned or {}) then out.earned = s.earned end
  if next(s.spent or {}) then out.spent = s.spent end
  local z = topZones(s.zones)
  if next(z) then out.zones = z end
  return out
end

function P.Section()
  local d = db()
  if not (d and d.cur) then return nil end
  local now = time()
  tick(d, now)
  local s = d.cur
  local out = {
    now = now, level = UnitLevel("player"), xp = UnitXP("player"), xpMax = UnitXPMax("player"),
    rested = GetXPExhaustion() or 0, money = GetMoney(), maxLevel = safe(GetMaxPlayerLevel), online = d.online,
    since = d.since, played = d.played,
  }
  out.session = summary(s)
  local mine = {}
  for i = #d.samples, 1, -1 do
    if i % 50 == 0 and ns.Collect and ns.Collect.Budget then ns.Collect.Budget() end
    local smp = parse(d.samples[i])
    if not smp or smp[1] < s.start then break end
    table.insert(mine, 1, smp)
  end
  if #mine > 0 then out.samples = downsample(mine, SNAP_SAMPLES) end
  local past = {}
  for i = #d.sessions, math.max(1, #d.sessions - SNAP_SESSIONS + 1), -1 do past[#past + 1] = summary(d.sessions[i]) end
  if #past > 0 then out.sessions = past end
  local levels = {}
  for i = math.max(1, #d.levels - SNAP_LEVELS + 1), #d.levels do levels[#levels + 1] = d.levels[i] end
  if #levels > 0 then out.levels = levels end
  return out
end

------------------------------------------------------------------------ /fai progress
local function gold(c)
  c = math.floor(math.abs(tonumber(c) or 0))
  local g, s, cu = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  if g > 0 then return string.format("%dg %ds %dc", g, s, cu) end
  if s > 0 then return string.format("%ds %dc", s, cu) end
  return cu .. "c"
end

local function duration(sec)
  sec = math.floor(tonumber(sec) or 0)
  if sec >= 86400 then return string.format("%dd %dh", math.floor(sec / 86400), math.floor(sec / 3600) % 24) end
  if sec >= 3600 then return string.format("%dh %dmin", math.floor(sec / 3600), math.floor(sec / 60) % 60) end
  return string.format("%dmin", math.floor(sec / 60))
end

local function byCause(t)
  local list, total = {}, 0
  for c, v in pairs(t or {}) do list[#list + 1] = { c, v } total = total + v end
  table.sort(list, function(a, b) return a[2] > b[2] end)
  local parts = {}
  for _, e in ipairs(list) do parts[#parts + 1] = e[1] .. " " .. gold(e[2]) end
  return gold(total) .. (#parts > 0 and (" (" .. table.concat(parts, ", ") .. ")") or "")
end

function P.Debug()
  local d = db()
  if not (d and d.cur) then ns.print("progress: not started yet") return end
  local now = time()
  tick(d, now)
  local s = d.cur
  ns.print(string.format("session since %s (%s ago), playing %s, reloads %d: XP +%d (quests %d), levels +%d, kills %d, " ..
    "quests %d, deaths %d", date("%H:%M", s.start), duration(now - s.start), duration(s.act), s.reloads or 0, s.gain,
    s.questXp or 0, s.levels, s.kills or 0, s.quests or 0, s.deaths or 0))
  ns.print("money earned " .. byCause(s.earned) .. " | spent " .. byCause(s.spent))
  local oldest = d.samples[1] and tonumber(d.samples[1]:match("^%d+"))
  ns.print(string.format("stored: %d samples%s, %d past sessions, %d level-ups; logged in %s counted by ForeverAI",
    #d.samples, oldest and (" since " .. date("%m-%d %H:%M", oldest)) or "", #d.sessions, #d.levels, duration(d.online)))
  local p = d.played
  if p then
    local level = p.lvl == UnitLevel("player") and (", " .. duration((p.level or 0) + (d.online - p.online)) .. " this level") or ""
    ns.print(string.format("played: %s total%s (read %s)", duration(p.total + (d.online - p.online)), level,
      date("%m-%d %H:%M", p.at)))
  else
    ns.print("played: not known yet (type /played once; ForeverAI never asks the game itself)")
  end
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.progress = P.Section
end
