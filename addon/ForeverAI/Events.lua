-- ForeverAI: what just happened around the player (loot, XP, quest progress, errors, deaths, level-ups),
-- so the AI can answer "what was that?", "why can't I...?" or "how far am I with the quest?".
local ADDON, ns = ...
local E = {}
ns.Events = E

local MAX = 30
local early = {} -- events before the saved variables are ready

-- kept per character in the saved variables, so a /reload does not forget what just happened
local function store()
  if ns.char then
    ns.char.events = ns.char.events or {}
    if #early > 0 then
      for _, e in ipairs(early) do table.insert(ns.char.events, e) end
      wipe(early)
    end
    return ns.char.events
  end
  return early
end

local function add(kind, text)
  text = ns.clean(text)
  if type(text) ~= "string" or text == "" then return end
  text = ns.stripCodes and ns.stripCodes(text) or text
  local log = store()
  local last = log[#log]
  if last and last.kind == kind and last.text == text and time() - last.t < 2 then return end -- spam
  log[#log + 1] = { kind = kind, text = ns.Cut(text, 1000), t = time() }
  while #log > MAX do table.remove(log, 1) end
end

local handlers = {
  CHAT_MSG_LOOT = function(msg) add("loot", msg) end,
  CHAT_MSG_MONEY = function(msg) add("money", msg) end,
  CHAT_MSG_COMBAT_XP_GAIN = function(msg) add("xp", msg) end,
  CHAT_MSG_SKILL = function(msg)
    add("skill", msg)
    -- "Your skill in Daggers has increased to 8." -> remember the level per character
    local name, value = tostring(ns.clean(msg) or ""):match("[Yy]our skill in (.-) has increased to (%d+)")
    if name and ns.char then
      ns.char.skills = ns.char.skills or {}
      ns.char.skills[name] = tonumber(value)
    end
  end,
  CHAT_MSG_COMBAT_FACTION_CHANGE = function(msg) add("reputation", msg) end,
  UI_ERROR_MESSAGE = function(_, msg) add("error", msg) end,
  UI_INFO_MESSAGE = function(_, msg) add("info", msg) end, -- quest progress like "Young Nightsaber slain: 3/7"
  PLAYER_LEVEL_UP = function(level) add("levelUp", "Reached level " .. tostring(level)) end,
  PLAYER_DEAD = function() add("death", "Player died") end,
  QUEST_ACCEPTED = function(_, questId)
    local id = questId or _
    add("questAccepted", (C_QuestLog.GetTitleForQuestID and C_QuestLog.GetTitleForQuestID(id)) or ("quest " .. tostring(id)))
  end,
  QUEST_TURNED_IN = function(questId)
    add("questDone", (C_QuestLog.GetTitleForQuestID and C_QuestLog.GetTitleForQuestID(questId)) or ("quest " .. tostring(questId)))
  end,
  ZONE_CHANGED_NEW_AREA = function() add("zone", GetZoneText()) end,
  PLAYER_REGEN_DISABLED = function() add("combat", "Entered combat") end,
  PLAYER_REGEN_ENABLED = function() add("combat", "Left combat") end,
}

local f = CreateFrame("Frame")
for event in pairs(handlers) do pcall(f.RegisterEvent, f, event) end
f:SetScript("OnEvent", function(_, event, ...)
  local h = handlers[event]
  if h then pcall(h, ...) end
end)

function E.KnownSkills()
  local out = {}
  for name, value in pairs(ns.char and ns.char.skills or {}) do
    out[#out + 1] = string.format("%s %d (from game messages; max = 5 x level)", name, value)
  end
  table.sort(out)
  return out
end

function E.Recent()
  local log = store()
  local now, out = time(), {}
  local last, repeats = nil, 0
  for i = #log, 1, -1 do
    local e = log[i]
    local ago = math.floor(now - e.t)
    if ago > 1800 then break end -- older than 30 min is not "recent"
    if last and last.kind == e.kind and last.text == e.text then
      -- the same message again (e.g. "Spell is not ready yet." spam): one line with a counter
      repeats = repeats + 1
      out[#out] = string.format("%ss ago [%s] %s (x%d)", math.floor(now - last.t), e.kind, e.text, repeats + 1)
    else
      last, repeats = e, 0
      out[#out + 1] = string.format("%ss ago [%s] %s", ago, e.kind, e.text)
    end
  end
  return out
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.recentEvents = E.Recent
end
