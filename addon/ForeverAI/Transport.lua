-- ForeverAI: transport without clipboard or keypresses
--  out: JSON -> Zlib -> a nearly black block of pixels in the top-left corner (values 0-15, 4 bits per color channel)
--       for one frame -> Screenshot() (TGA) -> companion decodes the file
--  in:  companion keeps the newest reply state in ForeverAI_InboxNNN/data.lua (LoadOnDemand addons) ahead of us;
--       the addon reads one inbox every few seconds with C_AddOns.LoadAddOn (each inbox once per UI session).
local ADDON, ns = ...
local T = {}
ns.Transport = T

-- A block holds up to 24 KB: a question with all the game data is 13-17 KB packed, so it goes out as one block. With
-- half the rows it took two, and the second one waits for the next second (screenshots are named to the second):
-- half a second on average, up to a whole one, on every question.
local GRID_W, MAX_ROWS, CELL = 128, 128, 1
local HEADER = 18
local CAPACITY = GRID_W * MAX_ROWS * 3 / 2 - HEADER -- 12 bits per cell

T.nextInbox = 1
T.poolSize = 0
T.sending = false

------------------------------------------------------------------------ inbox pool
local function inboxName(n) return string.format("ForeverAI_Inbox%03d", n) end

function T.Init()
  local function exists(name)
    if C_AddOns.DoesAddOnExist then
      local ok, res = pcall(C_AddOns.DoesAddOnExist, name)
      if ok then return res end
    end
    local ok, _, _, _, _, reason = pcall(C_AddOns.GetAddOnInfo, name)
    return ok and reason ~= "MISSING"
  end
  local n = 0
  while n < 5000 and exists(inboxName(n + 1)) do n = n + 1 end
  T.poolSize = n
  T.nextInbox = 1
  -- skip inboxes that are somehow already loaded in this UI session
  while T.nextInbox <= T.poolSize and C_AddOns.IsAddOnLoaded(inboxName(T.nextInbox)) do
    T.nextInbox = T.nextInbox + 1
  end
  C_Timer.After(4, T.PrepareBlock)
end

function T.InboxLeft()
  return math.max(0, T.poolSize - T.nextInbox + 1)
end

-- called from ForeverAI_InboxNNN/data.lua
function ForeverAI_Receive(n, b64)
  local okJ, data = pcall(function()
    local json = C_EncodingUtil.DecodeBase64(b64)
    return C_EncodingUtil.DeserializeJSON(json)
  end)
  if not okJ or type(data) ~= "table" then
    ns.lastError = "inbox " .. tostring(n) .. " decode failed: " .. tostring(data)
    return
  end
  if data.session ~= ns.session then
    ns.lastError = "inbox " .. tostring(n) .. " from another session"
    return
  end
  T.received = data
end

------------------------------------------------------------------------ reading inboxes on a schedule
-- The game indexes addon files only when the UI loads, so there is no "ready" signal; instead the companion keeps
-- the newest reply state in all inboxes ahead of us and we read one inbox every few seconds.
local FIRST_DELAY, STEP, SLOW_STEP, SLOW_AFTER, GIVE_UP = 1.5, 2.0, 4.0, 60, 300
-- How soon the next look is, by how long the question has waited. Every look uses up one inbox of this UI session, so
-- the looks are close together while most answers arrive and further apart for the long ones, where a second more is
-- a small part of the wait. Measured on 696 answers (half of them there within 13 s): a look every 3 s showed an
-- answer 1.4 s after it was ready, this pace 0.9 s after, for 10 looks an answer in place of 6.5.
local function pace(waited)
  if waited < 12 then return 1.5 elseif waited < 30 then return 2.0 end
  return 3.0
end
local heard = false -- the app has replied to the request that is waited for
local pollFrame = CreateFrame("Frame")
local polling, nextReadAt, startedAt, staleReads = false, 0, 0, 0

local function readInbox(n)
  T.received = nil
  ns.Stage("loading the answer add-on")
  local t0 = debugprofilestop()
  local loaded, reason = C_AddOns.LoadAddOn(inboxName(n))
  ns.Step("loadAnswerAddOn", debugprofilestop() - t0)
  ns.Stage("waiting for the answer")
  T.nextInbox = n + 1
  if not loaded then
    ns.lastError = "LoadAddOn " .. inboxName(n) .. ": " .. tostring(reason)
    return nil
  end
  local data = T.received
  T.received = nil
  return data
end

pollFrame:SetScript("OnUpdate", function()
  if not polling then return end
  local now = GetTime()
  if now < nextReadAt then return end
  if T.nextInbox > T.poolSize then
    polling = false
    ns.pending = nil
    ns.UI.FailAnswer("Type /reload and ask again.")
    return
  end
  local data = readInbox(T.nextInbox)
  local pend = ns.pending
  if data and pend and data.msgId == pend.msgId then
    staleReads, heard = 0, true
    ns.Stage(data.final and "showing the final answer" or "showing the answer")
    local t0 = debugprofilestop()
    local ok, err = pcall(ns.OnMessage, data)
    ns.Step(data.final and "showFinalAnswer" or "showAnswer", debugprofilestop() - t0)
    ns.Stage("waiting for the answer")
    if not ok then ns.lastError = "OnMessage: " .. tostring(err) end
    -- the app asks for a sooner look when the end is near (text is coming in) or it waits for the game
    nextReadAt = now + math.max(1.0, math.min(tonumber(data.retryAfter) or STEP, pace(now - startedAt)))
  else
    staleReads = staleReads + 1
    local waited = now - startedAt
    if pend and staleReads == 3 then
      ns.UI.SetPhase("No response. Is the ForeverAI app running?")
    end
    -- a request of the add-on's own (the update of a route) does not hold the window for minutes when the app is
    -- not there: without any reply it gives up after its own, shorter time
    if waited > GIVE_UP or (not heard and pend and pend.giveUp and waited > pend.giveUp) then
      polling = false
      ns.pending = nil
      ns.UI.FailAnswer("No response. Start the ForeverAI app and ask again.")
      return
    end
    nextReadAt = now + (waited > SLOW_AFTER and SLOW_STEP or STEP)
  end
  ns.UI.UpdateHeader()
end)

function T.StartPolling(first) -- first: seconds to the first look, for an answer the app gives at once
  startedAt = GetTime()
  staleReads, heard = 0, false
  nextReadAt = startedAt + (first or FIRST_DELAY)
  polling = true
end

function T.StopPolling()
  polling = false
end

------------------------------------------------------------------------ outbound frames
local paint -- frame with the pixel grid

local function buildPaint()
  local _, physH = GetPhysicalScreenSize()
  local f = CreateFrame("Frame", "ForeverAIDataFrame", nil) -- no parent: visible even with hidden UI (Alt+Z)
  f:SetFrameStrata("TOOLTIP")
  f:SetFrameLevel(9999)
  f:SetScale(768 / physH)
  f:SetPoint("TOPLEFT", WorldFrame, "TOPLEFT", 0, 0)
  f:SetSize((GRID_W + 2) * CELL, (MAX_ROWS + 2) * CELL)
  f.bg = f:CreateTexture(nil, "BACKGROUND")
  f.bg:SetPoint("TOPLEFT")
  f.bg:SetColorTexture(0, 0, 0, 1)
  f.cells = {}
  f:Hide()
  return f
end

local function cell(i)
  local t = paint.cells[i]
  if not t then
    t = paint:CreateTexture(nil, "ARTWORK")
    t:SetSize(CELL, CELL)
    local r, c = math.floor((i - 1) / GRID_W), (i - 1) % GRID_W
    t:SetPoint("TOPLEFT", paint, "TOPLEFT", (c + 1) * CELL, -(r + 1) * CELL)
    if t.SetSnapToPixelGrid then t:SetSnapToPixelGrid(false) t:SetTexelSnappingBias(0) end
    paint.cells[i] = t
  end
  return t
end

-- all cells of the block are created in the background after login (a few hundred per frame), so the first question
-- does not have to create thousands of textures at once
function T.PrepareBlock()
  if not paint then paint = buildPaint() end
  local total, i = GRID_W * MAX_ROWS, #paint.cells
  local ticker
  ticker = C_Timer.NewTicker(0, function()
    local last = math.min(total, i + 400)
    for k = i + 1, last do cell(k):Hide() end
    i = last
    if i >= total then ticker:Cancel() end
  end)
end

local function u32(n)
  return string.char(math.floor(n / 16777216) % 256, math.floor(n / 65536) % 256, math.floor(n / 256) % 256, n % 256)
end

local function u16(n)
  return string.char(math.floor(n / 256) % 256, n % 256)
end

-- Coloring thousands of cells takes ~90 ms, so it is done over several frames while the block is still hidden
-- (PAINT_BUDGET_MS per frame); done() runs when the block is complete. Only then is it shown and captured.
local PAINT_BUDGET_MS = 8
local painter = CreateFrame("Frame") -- OnUpdate runs every frame (a C_Timer ticker does not)
painter:Hide()
local function paintChunk(msgId, index, count, chunk, done)
  if not paint then paint = buildPaint() end
  paint:Hide()
  local stream = "FAI2" .. u32(msgId) .. string.char(index, count, CELL, 0) .. u16(GRID_W) .. u32(#chunk) .. chunk
  local pad = (3 - #stream % 3) % 3
  if pad > 0 then stream = stream .. string.rep("\0", pad) end
  local ncells = #stream * 2 / 3 -- three 4-bit values per cell
  local rows = math.ceil(ncells / GRID_W)
  local total, i = rows * GRID_W, 0
  local shown = paint.shownCells or 0 -- the cells the block before left shown: they need no Show() again
  local function step()
    local t0 = debugprofilestop()
    while i < total do
      i = i + 1
      local t = cell(i)
      if i <= ncells then
        -- cell i holds nibbles 3i-2 .. 3i of the stream (high nibble of each byte first)
        local k = 3 * i - 2
        local b1, b2 = stream:byte(math.floor((k + 1) / 2), math.floor((k + 3) / 2))
        local n1, n2, n3
        if k % 2 == 1 then
          n1, n2, n3 = math.floor(b1 / 16), b1 % 16, math.floor(b2 / 16)
        else
          n1, n2, n3 = b1 % 16, math.floor(b2 / 16), b2 % 16
        end
        t:SetColorTexture(n1 / 255, n2 / 255, n3 / 255, 1)
      else
        t:SetColorTexture(0, 0, 0, 1) -- the rest of the last row
      end
      if i > shown then
        t:Show()
        paint.shownCells = i
      end
      if debugprofilestop() - t0 > PAINT_BUDGET_MS then return false end
    end
    for k = total + 1, (paint.shownCells or 0) do paint.cells[k]:Hide() end -- only what the last block showed
    paint.shownCells = total
    paint.bg:SetSize((GRID_W + 2) * CELL, (rows + 2) * CELL)
    return true
  end
  if step() then done() return end
  painter:SetScript("OnUpdate", function()
    if step() then
      painter:SetScript("OnUpdate", nil)
      painter:Hide()
      done()
    end
  end)
  painter:Show()
end

-- how long the block is on screen (frames are counted by the block's own OnUpdate, which runs only while shown)
local shownAt, shownFrames = 0, 0
local function showBlock()
  shownAt, shownFrames = debugprofilestop(), 0
  paint:SetScript("OnUpdate", function() shownFrames = shownFrames + 1 end)
  paint:Show()
end
local function hideBlock()
  if paint and paint:IsShown() then
    paint:Hide()
    ns.Step("blockOnScreenMs", debugprofilestop() - shownAt)
    ns.Step("blockOnScreenFrames", shownFrames)
  end
end

local SHOT_TIMEOUT = 10
local queue, savedCVars, current = nil, nil, nil
local captureFrame = CreateFrame("Frame")
-- Messages that came while another was on its way: sent after it, in order. The add-on's own messages (a result for
-- the app, its log) never hold a question of the player back: T.Busy() is true only for a message of the player's.
local BACKGROUND = { game_result = true, game_results = true, debug = true }
local waiting = {}
local function pump()
  if T.sending then return end
  local nxt = table.remove(waiting, 1)
  if nxt then T.Send(nxt.msg, nxt.done) end
end
function T.Busy()
  if current and not current.background then return true end
  for _, w in ipairs(waiting) do
    if not BACKGROUND[w.msg.type] then return true end
  end
  return false
end

local function restoreCVars()
  if savedCVars then
    pcall(SetCVar, "screenshotFormat", savedCVars.fmt)
    pcall(SetCVar, "screenshotQuality", savedCVars.q)
    savedCVars = nil
  end
  if ActionStatus and T.actionStatusUnhooked then
    pcall(ActionStatus.RegisterEvent, ActionStatus, "SCREENSHOT_SUCCEEDED")
    pcall(ActionStatus.RegisterEvent, ActionStatus, "SCREENSHOT_FAILED")
    T.actionStatusUnhooked = false
  end
end

local function finish(ok, err)
  hideBlock()
  restoreCVars()
  T.sending = false
  local cb = current and current.done
  current, queue = nil, nil
  local okCb, e = true, nil
  if cb then okCb, e = pcall(cb, ok, err) end
  pump()
  if not okCb then error(e, 0) end
end

local function shootNext()
  if not queue or #queue == 0 then
    finish(true)
    return
  end
  local item = table.remove(queue, 1)
  ns.Stage("drawing the data block (hidden)")
  local t0 = debugprofilestop()
  paintChunk(item.msgId, item.index, item.count, item.chunk, function()
    ns.Step("drawBlockTotal", debugprofilestop() - t0)
    if not current then return end
    -- measured in the game: one frame for the block to render, and it can be hidden the frame after the capture
    -- (it is then on screen for a single frame); the capture still holds it
    local function capture()
      if not current then return end
      local sending = current
      local shot = (sending.shots or 0) + 1
      sending.shots, sending.waiting = shot, true
      sending.shotStart = debugprofilestop()
      ns.Stage("screenshot")
      local ok, err = pcall(Screenshot)
      if not ok then finish(false, err) return end
      C_Timer.After(0, hideBlock)
      -- the game normally reports the screenshot within a second; without the report the send must not stay stuck
      C_Timer.After(SHOT_TIMEOUT, function()
        if current == sending and sending.shots == shot and sending.waiting then
          finish(false, "the game did not take the screenshot")
        end
      end)
    end
    -- the game names screenshots to the second (WoWScrnShot_MMDDYY_HHMMSS): a frame taken in the same second as the
    -- previous one would get its file name, so the block waits (hidden) for the next second. The previous one may be
    -- of another message (a result for the app right before a question): the app may not have read it yet.
    local sending, waitFrom = current, debugprofilestop()
    local waited = false
    local function show()
      if current ~= sending then return end
      if T.lastShot and time() <= T.lastShot then
        waited = true
        C_Timer.After(0.02, show)
        return
      end
      if waited then ns.Step("waitNextSecond", debugprofilestop() - waitFrom) end
      ns.Stage("data block on screen")
      showBlock()
      C_Timer.After(0, capture)
    end
    show()
  end)
end

T.timings = {}
captureFrame:RegisterEvent("SCREENSHOT_SUCCEEDED")
captureFrame:RegisterEvent("SCREENSHOT_FAILED")
captureFrame:RegisterEvent("PLAYER_LOGOUT")
captureFrame:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_LOGOUT" then
    restoreCVars()  -- the player's screenshot settings are never saved changed, even when leaving mid-send
    return
  end
  if not current or not current.waiting then return end
  current.waiting = false
  T.lastShot = time()  -- the file is written: its name holds this second or an earlier one
  hideBlock()
  T.timings.shotMs = math.floor(debugprofilestop() - (current.shotStart or debugprofilestop()))
  ns.Step("screenshot", T.timings.shotMs)
  ns.Stage("waiting for the answer")
  if event == "SCREENSHOT_FAILED" then
    finish(false, "the game could not pass the question to the app")
    return
  end
  C_Timer.After(0, shootNext)
end)

function T.Send(msg, done)
  if T.sending then
    waiting[#waiting + 1] = { msg = msg, done = done }
    return
  end
  local function fail(why)
    if done then pcall(done, false, why) end
    pump()
  end
  local t0 = debugprofilestop()
  ns.Stage("packing the question")
  local okJ, json = pcall(C_EncodingUtil.SerializeJSON, msg)
  if not okJ then fail("JSON: " .. tostring(json)) return end
  local okC, data = pcall(C_EncodingUtil.CompressString, json, Enum.CompressionMethod.Zlib)
  if not okC then fail("compression: " .. tostring(data)) return end
  T.timings.encodeMs = math.floor(debugprofilestop() - t0)
  ns.Step("packQuestion", T.timings.encodeMs)
  local count = math.ceil(#data / CAPACITY)
  if count > 8 then fail("the question with game data is too large; turn off some data in Settings") return end
  queue = {}
  for i = 1, count do
    queue[i] = { msgId = msg.msgId, index = i - 1, count = count, chunk = data:sub((i - 1) * CAPACITY + 1, i * CAPACITY) }
  end
  T.lastPacket = { bytes = #data, json = #json, frames = count }
  ns.Step("packetJsonKB", #json / 1024)
  ns.Step("packetZipKB", #data / 1024)
  ns.Step("blocks", count)
  T.sending = true
  current = { done = done, background = BACKGROUND[msg.type] }
  savedCVars = { fmt = GetCVar("screenshotFormat"), q = GetCVar("screenshotQuality") }
  -- TGA is written without compression (faster than PNG); both are lossless, the companion reads either
  pcall(SetCVar, "screenshotFormat", "tga")
  if GetCVar("screenshotFormat") ~= "tga" then pcall(SetCVar, "screenshotFormat", "png") end
  T.timings.format = GetCVar("screenshotFormat")
  pcall(SetCVar, "screenshotQuality", "10")
  if ActionStatus and ActionStatus.IsEventRegistered and ActionStatus:IsEventRegistered("SCREENSHOT_SUCCEEDED") then
    pcall(ActionStatus.UnregisterEvent, ActionStatus, "SCREENSHOT_SUCCEEDED")
    pcall(ActionStatus.UnregisterEvent, ActionStatus, "SCREENSHOT_FAILED")
    T.actionStatusUnhooked = true
  end
  shootNext()
end
