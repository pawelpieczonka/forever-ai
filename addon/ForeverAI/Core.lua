-- ForeverAI: core (namespace, saved variables, events, conversations, requests)
local ADDON, ns = ...
ForeverAI = ns
ns.version = "0.6.0"

BINDING_HEADER_FOREVERAI = "ForeverAI"
BINDING_NAME_FOREVERAI_TOGGLE = "Show/hide ForeverAI"
BINDING_NAME_FOREVERAI_ASK_TOOLTIP = "Ask about the object under the cursor"

ns.defaults = {
  sections = { bags = true, spells = true, skills = true, reputations = true, nearby = true, windows = true,
               addons = true, auras = true, chat = false, talents = true, quests = true, progress = true,
               professions = true },
  maxSnapshotKB = 200,
  window = { point = "CENTER", x = 260, y = 40, w = 470, h = 600 },
  button = { show = true, point = "TOPRIGHT", x = -24, y = -250 },
  fontSize = 13,
}
local MAX_CONVS, MAX_ENTRIES = 25, 50

local isSecret = issecretvalue or function() return false end
ns.isSecret = isSecret

function ns.clean(v)
  if v == nil then return nil end
  local ok, secret = pcall(isSecret, v)
  if ok and secret then return nil end
  return v
end

-- only a safety cap for very long texts: cuts on a character boundary (never inside a multi-byte letter)
function ns.Cut(s, n)
  if type(s) ~= "string" or #s <= n then return s end
  return (s:sub(1, n):gsub("[\192-\255][\128-\191]*$", "")) .. "..."
end

-- The game's tooltip font lacks many accented letters: our own tooltips switch to the chat font while
-- they are shown and restore the original fonts when the tooltip hides (GameTooltip is shared with everything).
local tipOriginal = {}
function ns.ApplyTooltipFont(tip)
  tip = tip or GameTooltip
  local path = (ChatFontNormal and ChatFontNormal:GetFont()) or "Fonts\\ARIALN.TTF"
  local name = tip:GetName()
  for i = 1, tip:NumLines() do
    for _, side in ipairs({ "Left", "Right" }) do
      local fs = _G[name .. "Text" .. side .. i]
      if fs then
        local f, size, flags = fs:GetFont()
        if f and f ~= path then
          tipOriginal[fs] = tipOriginal[fs] or { f, size, flags }
          fs:SetFont(path, size, flags or "")
        end
      end
    end
  end
  if not tip.foreverAIFontHook then
    tip.foreverAIFontHook = true
    tip:HookScript("OnHide", function()
      for fs, o in pairs(tipOriginal) do fs:SetFont(o[1], o[2], o[3] or "") end
      wipe(tipOriginal)
    end)
  end
  tip:Show()
end

-- world size of a map in yards (does not change, so it is asked once per map)
local mapSize = {}
function ns.MapSize(mapID)
  local s = mapSize[mapID]
  if s == nil then
    local ok, w, h = pcall(C_Map.GetMapWorldSize, mapID)
    s = (ok and w and h and w > 0 and h > 0) and { w, h } or false
    mapSize[mapID] = s
  end
  return s or nil
end

-- distance between two map points (0-100 coordinates) in meters, the way TomTom shows it (world yards x 0.9144)
function ns.MapMeters(mapID, x1, y1, x2, y2)
  if not (mapID and x1 and y1 and x2 and y2 and C_Map.GetMapWorldSize) then return nil end
  local s = ns.MapSize(mapID)
  if not s then return nil end
  local dx, dy = (x2 - x1) / 100 * s[1], (y2 - y1) / 100 * s[2]
  return math.floor(math.sqrt(dx * dx + dy * dy) * 0.9144 + 0.5)
end

local EFFORT_NAMES = { low = "low", medium = "medium", high = "high", xhigh = "very high", max = "max" }
function ns.EffortName(e) return EFFORT_NAMES[e or ""] or tostring(e or "?") end

function ns.safe(fn, ...)
  if type(fn) ~= "function" then return nil end
  local res = { pcall(fn, ...) }
  if not res[1] then return nil end
  for i = 2, #res do res[i] = ns.clean(res[i]) end
  return unpack(res, 2)
end

function ns.round(v, n)
  if type(v) ~= "number" then return v end
  local m = 10 ^ (n or 1)
  return math.floor(v * m + 0.5) / m
end

function ns.stripCodes(s)
  if type(s) ~= "string" then return s end
  s = s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|cn[^:]*:", ""):gsub("|r", "")
  s = s:gsub("|H.-|h(.-)|h", "%1"):gsub("|T.-|t", ""):gsub("|A.-|a", "")
  return s
end

function ns.print(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  DEFAULT_CHAT_FRAME:AddMessage("|cff33ff99ForeverAI|r " .. table.concat(parts, " "))
end

local function merge(dst, src)
  for k, v in pairs(src) do
    if type(v) == "table" then
      if type(dst[k]) ~= "table" then dst[k] = {} end
      merge(dst[k], v)
    elseif dst[k] == nil then
      dst[k] = v
    end
  end
end

------------------------------------------------------------------------ events
local frame = CreateFrame("Frame")
ns.eventFrame = frame
local handlers = {}
function ns.On(event, fn)
  if not handlers[event] then
    handlers[event] = {}
    frame:RegisterEvent(event)
  end
  table.insert(handlers[event], fn)
end
frame:SetScript("OnEvent", function(_, event, ...)
  local list = handlers[event]
  if not list then return end
  for _, fn in ipairs(list) do
    local ok, err = pcall(fn, event, ...)
    if not ok then ns.lastError = tostring(err) end
  end
end)

local function newConvId()
  return string.format("c%x%03x", time(), math.random(0, 0xfff))
end

ns.On("ADDON_LOADED", function(_, name)
  if name ~= ADDON then return end
  ForeverAIDB = ForeverAIDB or {}
  merge(ForeverAIDB, ns.defaults)
  ForeverAIDB.changeLog = ForeverAIDB.changeLog or {}
  ForeverAIDB.addonSV = ForeverAIDB.addonSV or {}
  ForeverAIDB.status = ForeverAIDB.status or {}
  ForeverAIDB.pendingDeletes = ForeverAIDB.pendingDeletes or {}
  ForeverAIDB.prices = nil           -- removed feature (auction price cache)
  ForeverAIDB.maxSnapshotKB = math.max(ForeverAIDB.maxSnapshotKB or 0, ns.defaults.maxSnapshotKB) -- old limit dropped data
  ForeverAIDB.sections.screen = nil  -- the screen is now chosen per message, next to Send
  ForeverAIDB.block = nil            -- timing test setting, no longer used
  ForeverAIDB.probe = nil            -- a one-time check of the game's profession API, typed in the chat
  ForeverAICharDB = ForeverAICharDB or {}
  local char = ForeverAICharDB
  char.convs = char.convs or {}
  char.conv = char.conv or newConvId()
  -- migrate the single history of v0.1 into a saved conversation
  if type(char.history) == "table" and #char.history > 0 then
    local c = { id = char.conv, created = date("%Y-%m-%d %H:%M"), updated = date("%Y-%m-%d %H:%M"), entries = {} }
    for _, h in ipairs(char.history) do
      if h.kind == "question" then
        c.entries[#c.entries + 1] = { k = "q", t = h.text }
        c.title = c.title or tostring(h.text):sub(1, 60)
      elseif h.kind == "answer" then
        c.entries[#c.entries + 1] = { k = "a", t = h.text }
      end
    end
    char.convs[char.conv] = c
  end
  char.history = nil
  ns.db, ns.char = ForeverAIDB, ForeverAICharDB
end)

ns.On("PLAYER_LOGIN", function()
  ns.session = string.format("%x%04x", time(), math.random(0, 0xffff))
  ns.sigPrefix = "s" .. ns.session
  if ns.Collect then pcall(ns.Collect.RefreshLinks) end
  C_Timer.After(8, function() if ns.Links then ns.Links.BuildSpellIndex() end end)
  C_Timer.After(5, function() if ns.Collect.WarmSpellDescriptions then ns.Collect.WarmSpellDescriptions() end end)
  C_Timer.After(3, function() if ns.Collect then pcall(ns.Collect.sections.quests) end end) -- fills NPC coords
  if ns.Transport then ns.Transport.Init() end
  if ns.Players then ns.Players.Init() end
  if ns.UI then ns.UI.Init() end
  if ns.Button then ns.Button.Init() end
end)

function ns.NewMsgId()
  return math.random(1, 2147483646)
end

------------------------------------------------------------------------ conversations (saved per character)
function ns.CharKey()
  return (UnitName("player") or "?") .. "-" .. (GetRealmName() or "?")
end

function ns.CurrentConv()
  local id = ns.char.conv
  local c = ns.char.convs[id]
  if not c then
    c = { id = id, created = date("%Y-%m-%d %H:%M"), updated = date("%Y-%m-%d %H:%M"), entries = {} }
    ns.char.convs[id] = c
  end
  return c
end

local function pruneConvs()
  local list = {}
  for _, c in pairs(ns.char.convs) do list[#list + 1] = c end
  if #list <= MAX_CONVS then return end
  table.sort(list, function(a, b) return (a.updated or "") > (b.updated or "") end)
  for i = MAX_CONVS + 1, #list do
    if list[i].id ~= ns.char.conv then ns.char.convs[list[i].id] = nil end
  end
end

function ns.RecordEntry(entry)
  local c = ns.CurrentConv()
  entry.at = date("%H:%M")
  table.insert(c.entries, entry)
  while #c.entries > MAX_ENTRIES do table.remove(c.entries, 1) end
  if entry.k == "q" and not c.title then c.title = tostring(entry.t):sub(1, 60) end
  c.updated = date("%Y-%m-%d %H:%M")
  pruneConvs()
end

function ns.ConvList()
  local list = {}
  for _, c in pairs(ns.char.convs) do
    if #c.entries > 0 then list[#list + 1] = c end
  end
  table.sort(list, function(a, b) return (a.updated or "") > (b.updated or "") end)
  return list
end

function ns.NewConversation()
  if ns.pending then ns.UI.Notice("Wait for the current answer to finish.", "warn") return end
  ns.char.conv = newConvId()
  ns.UI.LoadConversation()
  ns.UI.ShowTab("chat")
end

function ns.OpenConversation(id)
  if ns.pending then ns.UI.Notice("Wait for the current answer to finish.", "warn") return end
  if not ns.char.convs[id] then return end
  ns.char.conv = id
  ns.UI.LoadConversation()
  ns.UI.ShowTab("chat")
end

function ns.DeleteConversation(id)
  if ns.pending and id == ns.char.conv then ns.UI.Notice("Wait for the current answer to finish.", "warn") return end
  ns.char.convs[id] = nil
  table.insert(ns.db.pendingDeletes, id)
  if id == ns.char.conv then
    ns.char.conv = newConvId()
    ns.UI.LoadConversation()
  end
end

------------------------------------------------------------------------ public entry points
function ForeverAI_Toggle()
  if ns.UI then ns.UI.Toggle() end
end

function ForeverAI_AskAboutTooltip()
  if ns.Collect then ns.Collect.CaptureTooltip(true) end
  if ns.UI then
    ns.UI.Show()
    ns.UI.ShowTab("chat")
    local tip = ns.Collect and ns.Collect.lastTooltip
    local first = tip and tip.lines and tip.lines:match("^[^\n]+")
    ns.UI.SetInput(first and ("What is " .. first .. " and is it useful for me?") or "")
  end
end

SLASH_FOREVERAI1 = "/fai"
SLASH_FOREVERAI2 = "/foreverai"
SlashCmdList.FOREVERAI = function(msg)
  msg = (msg or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if msg == "" then
    ForeverAI_Toggle()
  elseif msg == "status" then
    ns.RequestStatus()
  elseif msg == "new" then
    ns.NewConversation()
  elseif msg == "button" then
    ns.db.button.show = not ns.db.button.show
    ns.Button.Refresh()
  elseif msg == "stop" then -- ends what the add-on keeps doing: the watch for creatures and the marks on the map
    if ns.Track then ns.Track.Stop() end
    if ns.Map then ns.Map.Clear() end
    ns.print("Tracking stopped and marks cleared.")
  elseif msg == "progress" then
    if ns.Progress then ns.Progress.Debug() end
  elseif msg == "professions" then
    if ns.Professions then ns.Professions.Debug() end
  elseif msg == "debug" then
    local t = ns.Transport and ns.Transport.timings or {}
    ns.print("session", ns.session, "inbox", ns.Transport and ns.Transport.nextInbox, "/", ns.Transport and ns.Transport.poolSize,
      "pending", tostring(ns.pending and ns.pending.msgId), "lastError", tostring(ns.lastError))
    local p = ns.lastPerf or {}
    ns.print(string.format("game data %s ms over %s frames (slowest: %s), capture %s ms (%s), packet %s B",
      tostring(p.cpuMs), tostring(p.frames), tostring(p.slowest), tostring(t.shotMs), tostring(t.format),
      tostring(ns.Transport.lastPacket and ns.Transport.lastPacket.bytes)))
    local m = ns.MeterSummary()
    ns.print("slow frames:", m.slowFrames, "| longest steps (ms):", m.stepMaxMs)
  else
    ns.UI.Show()
    ns.UI.ShowTab("chat")
    ns.Ask(msg)
  end
end

------------------------------------------------------------------------ requests
function ns.BaseMessage(kind)
  local msg = {
    v = 1, type = kind, msgId = ns.NewMsgId(), session = ns.session, sigPrefix = ns.sigPrefix,
    nextInbox = ns.Transport.nextInbox, conv = ns.char.conv, sentAt = time(), addonVersion = ns.version,
    char = ns.CharKey(),
  }
  if #ns.db.pendingDeletes > 0 then
    msg.deleteConvs = {}
    for i, id in ipairs(ns.db.pendingDeletes) do msg.deleteConvs[i] = id end
  end
  return msg
end

function ns.CanSend()
  if ns.pending then return false, "Wait for the current answer to finish." end
  if ns.collecting or ns.Transport.sending then return false, "The previous question is still being sent." end
  if ns.Transport.InboxLeft() < 3 then
    return false, "Type /reload to continue."
  end
  return true
end

function ns.Request(msg, label)
  local ok, why = ns.CanSend()
  if not ok then
    ns.UI.Notice(why, "warn")
    return false
  end
  ns.pending = { msgId = msg.msgId, kind = msg.type, started = GetTime(), label = label }
  ns.Transport.Send(msg, function(sent, err)
    if not sent then
      ns.pending = nil
      if label == "question" then ns.UI.FailAnswer("Could not send: " .. tostring(err))
      else ns.UI.Notice("Could not send: " .. tostring(err), "error") end
      return
    end
    if msg.deleteConvs then wipe(ns.db.pendingDeletes) end
    if label == "question" then ns.UI.SetPhase("Sent…") end
    ns.Transport.StartPolling()
  end)
  ns.UI.UpdateBusy()
  return true
end

-- things the player tagged in the question (Alt+click): their full tooltips and facts
local function taggedInfo(text)
  local tagged = {}
  for hyperlink, name in text:gmatch("|H(.-)|h%[(.-)%]|h") do
    if #tagged >= 8 then break end
    local entry = { link = hyperlink, name = name }
    local data = C_TooltipInfo and C_TooltipInfo.GetHyperlink and ns.safe(C_TooltipInfo.GetHyperlink, hyperlink)
    if data and data.lines then
      local lines = {}
      for i, line in ipairs(data.lines) do
        if i > 60 then break end
        local l = ns.clean(line.leftText) or ""
        local r = ns.clean(line.rightText)
        if r and r ~= "" then l = l .. "  [" .. r .. "]" end
        if l ~= "" then lines[#lines + 1] = ns.stripCodes(l) end
      end
      entry.tooltip = table.concat(lines, "\n")
    end
    local questId = tonumber(hyperlink:match("^quest:(%d+)") or "")
    if questId and ns.Collect.QuestInfo then entry.quest = ns.Collect.QuestInfo(questId) end
    local itemId = tonumber(hyperlink:match("^item:(%d+)") or "")
    if itemId and ns.Collect.ItemFacts then
      entry.facts = ns.Collect.ItemFacts(hyperlink, true) -- "item:..." strings work with C_Item.GetItemInfo
    end
    tagged[#tagged + 1] = entry
  end
  return tagged
end

------------------------------------------------------------------------ frame meter
-- From sending a question until its answer is complete: every frame slower than 20 ms is recorded with the step
-- ForeverAI was in (and the longest time of each measured step), so a stall can be traced to its cause. The summary
-- of a send goes with the next question (app log "game data: ...", /fai debug).
local meter = { stage = "idle", slow = {}, steps = {}, active = false, untilT = 0 }
function ns.Stage(name) meter.stage = name end
function ns.Step(name, ms) meter.steps[name] = math.max(meter.steps[name] or 0, ms) end

local meterFrame, lastFrame = CreateFrame("Frame"), nil
meterFrame:SetScript("OnUpdate", function()
  local t = debugprofilestop()
  if meter.active then
    if lastFrame and t - lastFrame > 20 then
      table.insert(meter.slow, { ms = t - lastFrame, stage = meter.stage })
      table.sort(meter.slow, function(a, b) return a.ms > b.ms end)
      while #meter.slow > 4 do table.remove(meter.slow) end
    end
    if not (ns.collecting or ns.pending or ns.Transport.sending) and GetTime() > meter.untilT then meter.active = false end
    if ns.pending or ns.collecting then meter.untilT = GetTime() + 2 end
  end
  lastFrame = t
end)

function ns.MeterSummary()
  local frames, steps = {}, {}
  for _, s in ipairs(meter.slow) do frames[#frames + 1] = string.format("%d ms (%s)", s.ms, s.stage) end
  for k, v in pairs(meter.steps) do steps[#steps + 1] = string.format("%s %d", k, v) end
  table.sort(steps)
  return { slowFrames = #frames > 0 and table.concat(frames, ", ") or "none over 20 ms",
           stepMaxMs = table.concat(steps, ", ") }
end

local function meterStart()
  ns.lastSend = ns.MeterSummary()
  wipe(meter.slow)
  wipe(meter.steps)
  meter.active, meter.untilT = true, GetTime() + 2
end

-- opts.auto: a note from the game (player action); opts.screen: attach the current screen (player's choice only)
function ns.Ask(text, opts)
  opts = opts or {}
  text = (text or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if text == "" then return end
  local ok, why = ns.CanSend()
  if not ok then ns.UI.Notice(why, "warn") return end
  local t0 = debugprofilestop()
  meterStart()
  ns.Stage("showing the question")
  local msg = ns.BaseMessage("question")
  msg.text = ns.stripCodes(text)
  msg.wantScreen = opts.screen == true or nil
  if opts.auto then
    msg.auto = true
    ns.UI.AddSystemNote(text)
  else
    ns.UI.AddQuestion(text)
  end
  ns.UI.BeginAnswer(msg.msgId)
  ns.UI.SetPhase("Reading game data…")
  ns.collecting = true
  ns.UI.UpdateBusy()
  ns.Step("showQuestion", debugprofilestop() - t0)
  ns.Stage("game data")
  -- the game data is gathered over several frames, so the game never stalls
  ns.Collect.SnapshotAsync(function(snap, perf)
    ns.collecting = false
    msg.snapshot = snap
    perf.previousSend = ns.lastSend
    msg.perf = perf
    ns.lastPerf = perf
    ns.Stage("tagged items")
    local okT, tagged = pcall(taggedInfo, text)
    if okT and #tagged > 0 then snap.taggedInQuestion = tagged end
    if not ns.Request(msg, "question") then ns.UI.FailAnswer("Could not send the question.") end
  end)
end

------------------------------------------------------------------------ the player's decisions go back to the AI
-- Accept / Reject / Undo on a proposal card wakes the AI up: it gets what happened in the game (with a fresh
-- snapshot) and continues the conversation. Clicks within ~1 s are sent together.
local feedback, feedbackTimer = {}, nil
function ns.FlushFeedback()
  feedbackTimer = nil
  if #feedback == 0 then return end
  if not ns.CanSend() then -- an answer is still being written: try again a bit later
    feedbackTimer = C_Timer.NewTimer(1.5, ns.FlushFeedback)
    return
  end
  local text = "[Player action] " .. table.concat(feedback, " ")
  wipe(feedback)
  ns.Ask(text, { auto = true })
end

function ns.ActionFeedback(line)
  feedback[#feedback + 1] = line
  if feedbackTimer then feedbackTimer:Cancel() end
  feedbackTimer = C_Timer.NewTimer(1.2, ns.FlushFeedback)
end

function ns.RequestStatus()
  local msg = ns.BaseMessage("status")
  if ns.Request(msg, "status") then ns.UI.Notice("Refreshing…") end
end

function ns.SendSetting(kind, value, label)
  local msg = ns.BaseMessage(kind)
  msg.value = value
  if ns.Request(msg, kind) and label then ns.UI.Notice(label) end
end

------------------------------------------------------------------------ work the AI asks the game to do
-- (e.g. reading another add-on's options); results go back as their own frames while the answer is being written
ns.handledRequests = {}
local resultQueue = {}
local function flushResults()
  if #resultQueue == 0 then return end
  if ns.Transport.sending then C_Timer.After(0.25, flushResults) return end
  local msg = table.remove(resultQueue, 1)
  ns.Transport.Send(msg, function(ok, err)
    if not ok then ns.lastError = "game_result: " .. tostring(err) end
    if #resultQueue > 0 then C_Timer.After(0.1, flushResults) end
  end)
end

function ns.HandleRequests(list)
  for _, req in ipairs(list) do
    if type(req) == "table" and req.id and not ns.handledRequests[req.id] then
      ns.handledRequests[req.id] = true
      local function reply(result)
        resultQueue[#resultQueue + 1] = { v = 1, type = "game_result", msgId = ns.NewMsgId(), session = ns.session,
                                          id = req.id, result = result }
        flushResults()
      end
      local ok, err = pcall(function()
        if req.op == "addon_options" and ns.Addons then
          reply(ns.Addons.FindOptions(req.addon, req.query))
        elseif req.op == "auction_search" and ns.Auction then
          ns.Auction.Search(req, reply) -- replies when the search is done (it runs over several frames)
        else
          reply({ error = "unknown request " .. tostring(req.op) })
        end
      end)
      if not ok then reply({ error = tostring(err) }) end
    end
  end
end

------------------------------------------------------------------------ incoming
function ns.OnMessage(data)
  if type(data) ~= "table" then return end
  if type(data.requests) == "table" and not data.final then ns.HandleRequests(data.requests) end
  local pendingKind = ns.pending and ns.pending.kind
  if data.status then
    if type(data.status.addonSV) == "table" then
      ns.db.addonSV = data.status.addonSV
      data.status.addonSV = nil
    end
    ns.db.status = data.status
    ns.UI.UpdateHeader()
  elseif type(data.ai) == "table" then -- the AI in use (chosen in the app window) comes with the first reply
    for k, v in pairs(data.ai) do ns.db.status[k] = v end
    ns.UI.UpdateHeader()
  end
  if data.kind == "answer" then
    ns.UI.UpdateAnswer(data)
    if data.final then
      ns.pending = nil
      ns.UI.NotifyAnswer()
    end
  elseif data.kind == "status" then
    if data.text then ns.UI.Notice(data.text, "ok") end
    if data.final then ns.pending = nil end
  elseif data.kind == "error" then
    if pendingKind == "question" then ns.UI.FailAnswer(data.text or "Error")
    else ns.UI.Notice(data.text or "Error", "error") end
    if data.final then ns.pending = nil end
  end
  if not ns.pending then ns.Transport.StopPolling() end
  ns.UI.UpdateBusy()
end
