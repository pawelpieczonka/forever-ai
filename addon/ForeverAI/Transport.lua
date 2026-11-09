-- ForeverAI: transport without clipboard or keypresses
--  out: JSON -> Zlib -> a nearly black block of pixels in the top-left corner (values 0-15, 4 bits per color channel)
--       for one frame -> Screenshot() (TGA) -> companion decodes the file
--  in:  companion keeps the newest reply state in ForeverAI_InboxNNN/data.lua (LoadOnDemand addons) ahead of us;
--       the addon reads one inbox every few seconds with C_AddOns.LoadAddOn (each inbox once per UI session).
local ADDON, ns = ...
local T = {}
ns.Transport = T

local GRID_W, MAX_ROWS, CELL = 128, 64, 1
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
local FIRST_DELAY, STEP, SLOW_STEP, SLOW_AFTER, GIVE_UP = 2.0, 2.0, 4.0, 60, 300
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
    staleReads = 0
    ns.Stage(data.final and "showing the final answer" or "showing the answer")
    local t0 = debugprofilestop()
    local ok, err = pcall(ns.OnMessage, data)
    ns.Step(data.final and "showFinalAnswer" or "showAnswer", debugprofilestop() - t0)
    ns.Stage("waiting for the answer")
    if not ok then ns.lastError = "OnMessage: " .. tostring(err) end
    nextReadAt = now + math.max(1.0, tonumber(data.retryAfter) or STEP)
  else
    staleReads = staleReads + 1
    local waited = now - startedAt
    if pend and staleReads == 3 then
      ns.UI.SetPhase("No response. Is the ForeverAI app running?")
    end
    if waited > GIVE_UP then
      polling = false
      ns.pending = nil
      ns.UI.FailAnswer("No response. Start the ForeverAI app and ask again.")
      return
    end
    nextReadAt = now + (waited > SLOW_AFTER and SLOW_STEP or STEP)
  end
  ns.UI.UpdateHeader()
end)

function T.StartPolling()
  startedAt = GetTime()
  staleReads = 0
  nextReadAt = startedAt + FIRST_DELAY
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
local PAINT_BUDGET_MS = 4
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
      t:Show()
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
  if cb then cb(ok, err) end
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
    -- previous one would get its file name, so the block waits (hidden) for the next second
    local sending, waitFrom = current, debugprofilestop()
    local function show()
      if current ~= sending then return end
      if sending.lastShot and time() <= sending.lastShot then
        C_Timer.After(0.02, show)
        return
      end
      if sending.lastShot then ns.Step("waitNextSecond", debugprofilestop() - waitFrom) end
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
  current.lastShot = time()  -- the file is written: its name holds this second or an earlier one
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
    if done then done(false, "the previous question is still being sent") end
    return
  end
  local t0 = debugprofilestop()
  ns.Stage("packing the question")
  local okJ, json = pcall(C_EncodingUtil.SerializeJSON, msg)
  if not okJ then
    if done then done(false, "JSON: " .. tostring(json)) end
    return
  end
  local okC, data = pcall(C_EncodingUtil.CompressString, json, Enum.CompressionMethod.Zlib)
  if not okC then
    if done then done(false, "compression: " .. tostring(data)) end
    return
  end
  T.timings.encodeMs = math.floor(debugprofilestop() - t0)
  ns.Step("packQuestion", T.timings.encodeMs)
  local count = math.ceil(#data / CAPACITY)
  if count > 8 then
    if done then done(false, "the question with game data is too large; turn off some data in Settings") end
    return
  end
  queue = {}
  for i = 1, count do
    queue[i] = { msgId = msg.msgId, index = i - 1, count = count, chunk = data:sub((i - 1) * CAPACITY + 1, i * CAPACITY) }
  end
  T.lastPacket = { bytes = #data, json = #json, frames = count }
  ns.Step("packetJsonKB", #json / 1024)
  ns.Step("packetZipKB", #data / 1024)
  ns.Step("blocks", count)
  T.sending = true
  current = { done = done }
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
