-- ForeverAI: window (chat, saved conversations, memory, settings)
local ADDON, ns = ...
local UI = {}
ns.UI = UI

local WHITE = "Interface\\Buttons\\WHITE8x8"
local P = {
  bg = { 0.045, 0.052, 0.066, 0.97 }, bar = { 0.07, 0.08, 0.1, 1 }, edge = { 0.2, 0.24, 0.28, 1 },
  accent = { 0.27, 0.86, 0.62, 1 }, user = { 0.12, 0.25, 0.4, 0.96 }, ai = { 0.1, 0.112, 0.138, 0.96 },
  card = { 0.22, 0.155, 0.05, 0.95 }, cardEdge = { 0.95, 0.72, 0.3, 0.75 }, claude = { 0.85, 0.47, 0.34, 0.85 },
  err = { 0.3, 0.07, 0.07, 0.95 }, errEdge = { 0.9, 0.3, 0.3, 0.6 },
  btn = { 0.13, 0.15, 0.18, 1 }, btnHover = { 0.19, 0.22, 0.26, 1 },
  primary = { 0.13, 0.48, 0.35, 1 }, primaryHover = { 0.17, 0.6, 0.44, 1 },
  danger = { 0.42, 0.12, 0.12, 1 }, dangerHover = { 0.56, 0.16, 0.16, 1 }, clear = { 0, 0, 0, 0 },
}

------------------------------------------------------------------------ fonts (the chat font covers non-English letters)
local fontPath = (ChatFontNormal and ChatFontNormal:GetFont()) or "Fonts\\ARIALN.TTF"
local F = {}
local function font(key, name, delta, r, g, b, flags)
  F[key] = F[key] or CreateFont(name)
  F[key].delta, F[key].flags = delta, flags
  F[key]:SetTextColor(r, g, b)
end
font("body", "ForeverAIBody", 0, 0.92, 0.93, 0.95)
font("small", "ForeverAISmall", -2, 0.58, 0.62, 0.68)
font("title", "ForeverAITitle", 2, 0.35, 0.95, 0.7, "")
font("head", "ForeverAIHead", -1, 0.95, 0.78, 0.36)
font("btn", "ForeverAIBtn", -1, 0.9, 0.92, 0.95)
font("dim", "ForeverAIDim", -1, 0.6, 0.64, 0.7)
local function applyFontSize(base)
  for _, f in pairs(F) do f:SetFont(fontPath, base + f.delta, f.flags or "") end
end
applyFontSize(13)

local function esc(s) return (tostring(s or ""):gsub("|", "||")) end

------------------------------------------------------------------------ widgets
local function backdrop(f, color, edge)
  f:SetBackdrop({ bgFile = WHITE, edgeFile = edge and WHITE or nil, edgeSize = edge and 1 or nil })
  f:SetBackdropColor(unpack(color))
  if edge then f:SetBackdropBorderColor(unpack(edge)) end
end

local function label(parent, text, fontObj, layer)
  local fs = parent:CreateFontString(nil, layer or "OVERLAY")
  fs:SetFontObject(fontObj or F.body)
  fs:SetJustifyH("LEFT")
  fs:SetJustifyV("TOP")
  fs:SetText(text or "")
  return fs
end

local function Btn(parent, text, w, h, onClick, style, tip)
  local b = CreateFrame("Button", nil, parent, "BackdropTemplate")
  b:SetSize(w, h)
  local base, hover = P.btn, P.btnHover
  if style == "primary" then base, hover = P.primary, P.primaryHover
  elseif style == "danger" then base, hover = P.danger, P.dangerHover
  elseif style == "ghost" then base, hover = P.clear, P.btnHover end
  b.base, b.hover, b.style = base, hover, style
  backdrop(b, base)
  local fs = b:CreateFontString(nil, "OVERLAY")
  fs:SetFontObject(style == "ghost" and F.dim or F.btn)
  fs:SetPoint("CENTER")
  b:SetFontString(fs)
  b:SetText(text)
  b.tip = tip
  b:SetScript("OnEnter", function(self)
    if self:IsEnabled() and not self.active then self:SetBackdropColor(unpack(self.hover)) end
    if self.tip then
      GameTooltip:SetOwner(self, "ANCHOR_TOP")
      GameTooltip:SetText(self.tip, 1, 1, 1, 1, true)
      ns.ApplyTooltipFont()
    end
  end)
  b:SetScript("OnLeave", function(self)
    self:SetBackdropColor(unpack(self.active and P.primary or self.base))
    GameTooltip:Hide()
  end)
  b:SetScript("OnClick", onClick)
  function b:SetActive(on)
    self.active = on
    self:SetBackdropColor(unpack(on and P.primary or self.base))
    self:GetFontString():SetFontObject((on or self.style ~= "ghost") and F.btn or F.dim)
  end
  function b:SetUsable(on)
    self:SetEnabled(on)
    self:SetAlpha(on and 1 or 0.4)
  end
  return b
end

local function Input(parent, w, placeholder)
  local holder = CreateFrame("Frame", nil, parent, "BackdropTemplate")
  holder:SetSize(w, 26)
  backdrop(holder, { 0.08, 0.09, 0.11, 1 }, P.edge)
  local e = CreateFrame("EditBox", nil, holder)
  e:SetPoint("TOPLEFT", 8, -2)
  e:SetPoint("BOTTOMRIGHT", -8, 2)
  e:SetAutoFocus(false)
  e:SetFontObject(F.body)
  e:SetTextColor(0.95, 0.95, 0.97)
  e:SetMaxLetters(2000)
  local hint = label(e, placeholder or "", F.dim)
  hint:SetPoint("LEFT", 0, 0)
  e:SetScript("OnTextChanged", function(self) hint:SetShown(self:GetText() == "") end)
  e:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
  e:SetScript("OnEditFocusGained", function() holder:SetBackdropBorderColor(unpack(P.accent)) end)
  e:SetScript("OnEditFocusLost", function() holder:SetBackdropBorderColor(unpack(P.edge)) end)
  holder.edit = e
  return holder
end

local function Check(parent, text, get, set)
  local cb = CreateFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
  cb:SetSize(22, 22)
  local l = label(cb, text, F.btn)
  l:SetPoint("LEFT", cb, "RIGHT", 2, 0)
  cb:SetScript("OnShow", function(self) self:SetChecked(get()) end)
  cb:SetScript("OnClick", function(self) set(self:GetChecked() and true or false) end)
  return cb
end

-- scroll area with mouse wheel, thin indicator and "stick to bottom"
local function Scroll(parent)
  local sf = CreateFrame("ScrollFrame", nil, parent)
  local child = CreateFrame("Frame", nil, sf)
  child:SetSize(10, 10)
  sf:SetScrollChild(child)
  sf.child = child
  sf.stick = true
  sf:EnableMouseWheel(true)
  local bar = sf:CreateTexture(nil, "OVERLAY")
  bar:SetColorTexture(1, 1, 1, 0.16)
  bar:SetWidth(3)
  local function updateBar()
    local range, h = sf:GetVerticalScrollRange(), sf:GetHeight()
    if range <= 1 or h <= 0 then bar:Hide() return end
    local th = math.max(24, h * h / (range + h))
    bar:ClearAllPoints()
    bar:SetPoint("TOPRIGHT", sf, "TOPRIGHT", 0, -(h - th) * (sf:GetVerticalScroll() / range))
    bar:SetHeight(th)
    bar:Show()
  end
  sf.UpdateBar = updateBar
  sf:SetScript("OnMouseWheel", function(self, delta)
    local range = self:GetVerticalScrollRange()
    local v = math.max(0, math.min(range, self:GetVerticalScroll() - delta * 50))
    self:SetVerticalScroll(v)
    self.stick = v >= range - 4
    updateBar()
  end)
  sf:SetScript("OnScrollRangeChanged", function(self)
    if self.stick then self:SetVerticalScroll(self:GetVerticalScrollRange()) end
    updateBar()
  end)
  sf:SetScript("OnSizeChanged", function(self, w) child:SetWidth(math.max(10, w - 8)) updateBar() end)
  function sf:ToBottom()
    self.stick = true
    C_Timer.After(0, function() self:SetVerticalScroll(self:GetVerticalScrollRange()) updateBar() end)
  end
  return sf
end

------------------------------------------------------------------------ state
local main, panels, tabs, header, pill, footer, notice, reloadBtn, sendBtn, input, screenCheck
local chat, emptyState
local entries = {}
local currentAnswer
local lastQuestion
local activeTab = "chat"

------------------------------------------------------------------------ chat entries
local function relayout()
  if not chat then return end
  local width = chat.child:GetWidth()
  if width < 50 then return end
  local y = 4
  for _, e in ipairs(entries) do
    local w, h = e.layout(width)
    e.frame:ClearAllPoints()
    if e.align == "right" then
      e.frame:SetPoint("TOPRIGHT", chat.child, "TOPRIGHT", -2, -y)
    else
      e.frame:SetPoint("TOPLEFT", chat.child, "TOPLEFT", 0, -y)
    end
    e.frame:SetSize(w, h)
    y = y + h + (e.gap or 8)
  end
  chat.child:SetHeight(math.max(y, 10))
  if emptyState then emptyState:SetShown(#entries == 0) end
end

local function bubble(kind, text)
  local f = CreateFrame("Frame", nil, chat.child, "BackdropTemplate")
  if kind == "user" then backdrop(f, P.user)
  elseif kind == "ai" then backdrop(f, P.ai)
  elseif kind == "error" then backdrop(f, P.err, P.errEdge) end
  local fs = label(f, text, kind == "meta" and F.small or F.body)
  fs:SetPoint("TOPLEFT", kind == "meta" and 2 or 10, kind == "meta" and 0 or -7)
  f.fs = fs
  ns.Links.Attach(f)
  local e = { frame = f, kind = kind, align = kind == "user" and "right" or "left", gap = kind == "ai" and 3 or 8 }
  e.layout = function(width)
    if kind == "meta" then
      fs:SetWidth(width - 6)
      return width, fs:GetStringHeight() + 2
    end
    local maxW = kind == "user" and math.floor(width * 0.84) or (width - 2)
    fs:SetWidth(maxW - 20)
    local w = maxW
    if kind == "user" then
      local natural = fs.GetUnboundedStringWidth and fs:GetUnboundedStringWidth() or fs:GetStringWidth()
      w = math.min(maxW, math.ceil(natural) + 22)
      fs:SetWidth(w - 20)
    end
    return w, fs:GetStringHeight() + 14
  end
  entries[#entries + 1] = e
  return e
end

local function card(text, edge, buttons)
  local f = CreateFrame("Frame", nil, chat.child, "BackdropTemplate")
  backdrop(f, P.card, edge or P.cardEdge)
  local fs = label(f, text)
  fs:SetPoint("TOPLEFT", 10, -8)
  local status = label(f, "", F.small)
  local e = { frame = f, kind = "card", align = "left", fs = fs, status = status, buttons = {} }
  for i, def in ipairs(buttons or {}) do
    e.buttons[i] = Btn(f, def[1], def[2], 22, function() def[3](e) end, def[4])
  end
  e.layout = function(width)
    fs:SetWidth(width - 22)
    local h = fs:GetStringHeight() + 16
    local x = 10
    for _, b in ipairs(e.buttons) do
      b:ClearAllPoints()
      b:SetPoint("TOPLEFT", f, "TOPLEFT", x, -h)
      x = x + b:GetWidth() + 6
    end
    status:ClearAllPoints()
    status:SetPoint("TOPLEFT", f, "TOPLEFT", 10, -h - 4)
    status:SetWidth(width - 22)
    return width - 2, h + 30
  end
  function e:Done(msg)
    for _, b in ipairs(self.buttons) do b:Hide() end
    self.status:SetText(msg)
  end
  entries[#entries + 1] = e
  return e
end

local function clearEntries()
  for _, e in ipairs(entries) do
    e.frame:Hide()
    e.frame:SetParent(nil)
  end
  wipe(entries)
  currentAnswer = nil
end

local function sourcesLine(sources)
  if type(sources) ~= "table" or #sources == 0 then return nil end
  local out = {}
  for i, s in ipairs(sources) do
    if i > 5 then break end
    local domain = tostring(s.url or ""):match("https?://([^/]+)") or ""
    domain = domain:gsub("^www%.", "")
    out[#out + 1] = esc(s.title or domain):sub(1, 48) .. " |cff6a7380(" .. esc(domain) .. ((s.date and s.date ~= "") and (", " .. s.date) or "") .. ")|r"
  end
  return "Sources: " .. table.concat(out, "  ·  ")
end

local function metaLine(data)
  local parts = {}
  if data.model then parts[#parts + 1] = esc((tostring(data.model):gsub("^[^/]+/", ""))) end
  if data.cost then parts[#parts + 1] = string.format("$%.4f", data.cost) end
  if data.costNote then parts[#parts + 1] = esc(data.costNote) end
  if data.secs then parts[#parts + 1] = data.secs .. " s" end
  return #parts > 0 and table.concat(parts, "  ·  ") or nil
end

------------------------------------------------------------------------ chat API (used by Core/Transport)
local function questionText(text)
  -- keep real item links the player tagged in the question, escape everything else
  return tostring(text):find("|H", 1, true) and text or esc(text)
end

function UI.AddSystemNote(text)
  bubble("meta", "|cff8fd3ff" .. esc(ns.stripCodes(text)) .. "|r")
  ns.RecordEntry({ k = "s", t = text })
  chat:ToBottom()
  relayout()
end

function UI.AddQuestion(text)
  bubble("user", questionText(text))
  ns.RecordEntry({ k = "q", t = text })
  lastQuestion = text
  chat:ToBottom()
  relayout()
end

local dots = { "", ".", "..", "..." }
local function animate(e)
  local t, i = 0, 1
  e.frame:SetScript("OnUpdate", function(_, dt)
    t = t + dt
    if t < 0.4 then return end
    t, i = 0, i % 4 + 1
    local body = e.body or ""
    e.frame.fs:SetText((body ~= "" and (body .. "\n") or "") .. "|cff7f8a99" .. esc(e.phase or "Thinking") .. dots[i] .. "|r")
  end)
end

function UI.BeginAnswer(msgId)
  local e = bubble("ai", "|cff7f8a99Sending…|r")
  e.msgId, e.phase = msgId, "Sending"
  currentAnswer = e
  animate(e)
  relayout()
end

function UI.SetPhase(text)
  if currentAnswer and not currentAnswer.done then
    currentAnswer.phase = tostring(text):gsub("…$", "")
  end
end

function UI.UpdateAnswer(data)
  local e = currentAnswer
  if not e then
    e = bubble("ai", "")
    e.msgId = data.msgId
    currentAnswer = e
    animate(e)
  end
  if data.final then
    e.done = true
    e.frame:SetScript("OnUpdate", nil)
    local text = ns.Links.Linkify(data.text or "", data.links)
    e.frame.fs:SetText(text)
    local meta = metaLine(data)
    local src = sourcesLine(data.sources)
    local metaText = table.concat({ meta or "", src or "" }, (meta and src) and "\n" or "")
    if metaText ~= "" then bubble("meta", metaText) end
    ns.RecordEntry({ k = "a", t = data.text or "", links = data.links, meta = metaText ~= "" and metaText or nil })
    for _, a in ipairs(data.actions or {}) do UI.ActionCard(a) end
    if data.marks and #data.marks > 0 then UI.MarksCard(data.marks) end
    currentAnswer = nil
  else
    e.body = ns.Links.Linkify(data.text or "", nil)
    if data.phase then e.phase = tostring(data.phase):gsub("…$", "") end
  end
  relayout()
end

function UI.FailAnswer(text)
  if currentAnswer then
    currentAnswer.frame:SetScript("OnUpdate", nil)
    currentAnswer.frame:Hide()
    for i, e in ipairs(entries) do
      if e == currentAnswer then table.remove(entries, i) break end
    end
    currentAnswer = nil
  end
  bubble("error", "|cffff9a9a" .. esc(text) .. "|r")
  ns.RecordEntry({ k = "e", t = text })
  relayout()
end

function UI.Clear()
  clearEntries()
  relayout()
end

function UI.LoadConversation()
  if not chat then return end
  clearEntries()
  local c = ns.CurrentConv()
  for _, en in ipairs(c.entries) do
    if en.k == "q" then
      bubble("user", questionText(en.t))
    elseif en.k == "a" then
      bubble("ai", ns.Links.Linkify(en.t or "", en.links))
      if en.meta then bubble("meta", en.meta) end
    elseif en.k == "e" then
      bubble("error", "|cffff9a9a" .. esc(en.t) .. "|r")
    elseif en.k == "s" then
      bubble("meta", "|cff8fd3ff" .. esc(ns.stripCodes(en.t or "")) .. "|r")
    end
  end
  chat:ToBottom()
  relayout()
  UI.RefreshConversations()
end

------------------------------------------------------------------------ cards
local function describeAction(action)
  if action.op == "abandon_quest" then return "abandon quest [" .. tostring(action.title) .. "]" end
  if action.op == "vendor_sell" then return "sell " .. #(action.items or {}) .. " item types for " .. tostring(action.total) end
  if action.op == "vendor_repair" then return "repair for " .. tostring(action.cost) end
  if action.op == "set_option" or action.op == "set_setting" then
    return string.format("set %s in %s to %s", tostring(action.optionName or action.path), tostring(action.addon),
      tostring(action.valueLabel or action.value))
  end
  if action.op == "toggle_addon" then
    return (action.value and "enable" or "disable") .. " add-on " .. tostring(action.addon)
  end
  return "command " .. tostring(action.command)
end

function UI.ActionCard(action)
  local what
  if action.op == "vendor_sell" then
    local lines = {}
    for _, it in ipairs(action.items or {}) do
      lines[#lines + 1] = string.format("• %s ×%s  |cff9aa3ad%s|r", esc(it.name or "?"), tostring(it.count or "?"), esc(it.price or ""))
    end
    what = "Sell to vendor:\n" .. table.concat(lines, "\n") .. "\n|cffffd100Total: " .. esc(action.total or "?") .. "|r"
  elseif action.op == "vendor_repair" then
    what = "Repair all equipment — cost |cffffd100" .. esc(action.cost or "?") .. "|r"
  elseif action.op == "abandon_quest" then
    what = string.format("Abandon quest |cffffff00[%s]|r\n|cffff9966Cannot be undone; the quest can be picked up again.|r",
      esc(action.title or tostring(action.questId)))
  elseif action.op == "set_option" then
    local cur = ns.Addons.Current(action)
    what = string.format("%s |cff6a7380(%s)|r\n|cff9aa3adcurrent:|r %s  →  |cff9aa3adnew:|r %s",
      esc(action.optionName or action.path), esc(action.path), esc(tostring(action.oldLabel or cur)),
      esc(tostring(action.valueLabel or action.value)))
  elseif action.op == "set_setting" then
    local cur = ns.Addons.Current(action)
    what = string.format("%s\n|cff9aa3adcurrent:|r %s  →  |cff9aa3adnew:|r %s", esc(action.path), esc(tostring(cur)), esc(tostring(action.value)))
  elseif action.op == "toggle_addon" then
    what = (action.value and "Enable" or "Disable") .. " add-on " .. esc(action.addon)
  else
    what = "Command: " .. esc(action.command)
  end
  local head = action.op == "abandon_quest" and "Quest log"
    or (action.op == "vendor_sell" or action.op == "vendor_repair") and "Vendor"
    or ("Add-on change: " .. esc(action.addon))
  local text = string.format("|cffffd27a%s|r\n%s\n%s%s", head, what, esc(action.description or ""),
    action.needsReload and "\n|cffff9966Requires /reload.|r" or "")
  local e
  e = card(text, nil, {
    { "Accept", 96, function(self)
      local ok, res = ns.Addons.Apply(action)
      ns.ActionFeedback(string.format("Accepted: %s — %s.", describeAction(action),
        ok and "done" or ("failed: " .. tostring(res))))
      if ok then
        self.entry = res
        self:Done("|cff5dff9dApplied.|r")
        if action.op == "abandon_quest" or action.op == "vendor_repair" then return end -- nothing to undo
        local undo = Btn(self.frame, "Undo", 70, 20, function(btn)
          local okU, err = ns.Addons.Undo(self.entry)
          ns.ActionFeedback(string.format("Undone: %s — %s.", describeAction(action),
            okU and "done" or ("failed: " .. tostring(err))))
          btn:Hide()
          self.status:SetText(okU and "|cffe0d060Undone.|r" or ("|cffff6060" .. esc(err) .. "|r"))
        end)
        undo:SetPoint("TOPRIGHT", self.frame, "TOPRIGHT", -8, -(self.frame:GetHeight() - 26))
        if action.needsReload then
          local rl = Btn(self.frame, "Reload UI", 96, 20, function() ReloadUI() end, "primary")
          rl:SetPoint("RIGHT", undo, "LEFT", -6, 0)
        end
      else
        self:Done("|cffff6060Failed: " .. esc(res) .. "|r")
      end
    end, "primary" },
    { "Reject", 76, function(self)
      self:Done("|cff8a93a0Rejected.|r")
      ns.ActionFeedback(string.format("Rejected: %s.", describeAction(action)))
    end },
  })
  relayout()
end

function UI.MarksCard(marks)
  local placed, how = ns.Map.Apply(marks)
  if #placed == 0 then return end
  local lines = {}
  for _, p in ipairs(placed) do
    lines[#lines + 1] = string.format("• %s  |cff9aa3ad(%s %.1f, %.1f)|r", esc(p.title), esc(p.zone or ""), p.x, p.y)
  end
  card("|cff7fd6ffMarked on the map|r |cff6a7380(" .. how .. ")|r\n" .. table.concat(lines, "\n"), { 0.3, 0.6, 0.9, 0.7 }, {
    { "Show map", 96, function() ns.Map.Show(placed[1].mapID) end, "primary" },
    { "Clear marks", 110, function(self) ns.Map.Clear() self:Done("|cff8a93a0Marks cleared.|r") end },
  })
  relayout()
end

function UI.SuggestClaude(claude)
  if not chat then return end
  local text = "|cffe58b6aClaude Code detected|r" .. (claude and claude.subscription and (" |cff9aa3ad(plan " .. esc(claude.subscription) .. ")|r") or "") ..
    "\nUse your Claude subscription instead of OpenRouter (no per-token cost)."
  card(text, P.claude, {
    { "Use Claude Code", 140, function(self)
      self:Done("Switching…")
      ns.SendSetting("set_provider", "claude_code", "Switching to Claude Code…")
    end, "primary" },
    { "Keep OpenRouter", 130, function(self)
      self:Done("Keeping OpenRouter.")
      ns.SendSetting("dismiss_claude", true)
    end },
  })
  relayout()
end

------------------------------------------------------------------------ header / footer
function UI.UpdateHeader()
  if not main then return end
  local s = ns.db.status or {}
  if s.provider == "claude_code" then
    local c = s.claude or {}
    pill:SetText("|cffe58b6aClaude|r  " .. esc((tostring(c.model or "?"):gsub("^claude%-", ""):gsub("%-", " "))) .. "  ·  " .. esc(ns.EffortName(c.effort)))
  else
    local model = s.model and esc((s.model:gsub("^[^/]+/", ""))) or "OpenRouter"
    pill:SetText("|cff7fd6ffOpenRouter|r  " .. model)
  end
  local left = ns.Transport.InboxLeft()
  local parts = {}
  if s.provider ~= "claude_code" and s.balance then
    local low = s.lowBalance and s.balance < s.lowBalance
    parts[#parts + 1] = string.format("%s$%.2f|r", low and "|cffff5555" or "|cff5dff9d", s.balance)
  elseif s.provider == "claude_code" then
    parts[#parts + 1] = "subscription"
  end
  if s.searchMode then parts[#parts + 1] = "web search: " .. ({ auto = "auto", always = "always", never = "off" })[s.searchMode] end
  if left < 15 then parts[#parts + 1] = "|cffffaa44/reload needed soon|r" end
  footer:SetText(table.concat(parts, "   "))
  reloadBtn:SetShown(left < 15)
  if panels and panels.settings:IsShown() then UI.RefreshSettings() end
  if panels and panels.memory:IsShown() then UI.RefreshMemory() end
end

local noticeTimer
function UI.Notice(text, kind)
  if not notice then ns.print(ns.stripCodes(tostring(text))) return end
  local color = ({ error = "|cffff7070", warn = "|cffffc060", ok = "|cff5dff9d" })[kind] or "|cffb0b8c4"
  notice:SetText(color .. esc(text) .. "|r")
  notice:SetAlpha(1)
  if noticeTimer then noticeTimer:Cancel() end
  noticeTimer = C_Timer.NewTimer(kind == "error" and 9 or 5, function() notice:SetText("") end)
end
UI.System = UI.Notice

function UI.UpdateBusy()
  if not sendBtn then return end
  local busy = ns.pending ~= nil or ns.collecting == true
  sendBtn:SetText(busy and "..." or "Send")
  sendBtn:SetUsable(not busy)
  if ns.Button then ns.Button.SetBusy(busy) end
end

function UI.NotifyAnswer()
  if main and not main:IsShown() then
    if ns.Button then ns.Button.SetBadge(true) end
    if PlaySound and SOUNDKIT and SOUNDKIT.TELL_MESSAGE then pcall(PlaySound, SOUNDKIT.TELL_MESSAGE) end
  end
end

function UI.PacketInfo() end

------------------------------------------------------------------------ conversations panel
local convRows = {}
function UI.RefreshConversations()
  local p = panels and panels.convs
  if not p then return end
  local list = ns.ConvList()
  local width = p.scroll.child:GetWidth()
  for i, c in ipairs(list) do
    local row = convRows[i]
    if not row then
      row = CreateFrame("Button", nil, p.scroll.child, "BackdropTemplate")
      backdrop(row, P.ai)
      row.title = label(row, "", F.body)
      row.title:SetPoint("TOPLEFT", 10, -7)
      row.sub = label(row, "", F.small)
      row.sub:SetPoint("BOTTOMLEFT", 10, 6)
      row.del = Btn(row, "x", 22, 22, function(self) ns.DeleteConversation(self:GetParent().convId) UI.RefreshConversations() end, "ghost", "Delete conversation")
      row.del:SetPoint("RIGHT", -6, 0)
      row:SetScript("OnClick", function(self) ns.OpenConversation(self.convId) end)
      row:SetScript("OnEnter", function(self) self:SetBackdropColor(unpack(P.btnHover)) end)
      row:SetScript("OnLeave", function(self) self:SetBackdropColor(unpack(self.current and P.user or P.ai)) end)
      convRows[i] = row
    end
    row.convId = c.id
    row.current = c.id == ns.char.conv
    row:SetBackdropColor(unpack(row.current and P.user or P.ai))
    row:SetSize(width, 44)
    row:ClearAllPoints()
    row:SetPoint("TOPLEFT", p.scroll.child, "TOPLEFT", 0, -(i - 1) * 50)
    row.title:SetWidth(width - 50)
    row.title:SetWordWrap(false)
    row.title:SetText(esc(c.title or "Conversation"))
    local n = 0
    for _, en in ipairs(c.entries) do if en.k == "q" then n = n + 1 end end
    row.sub:SetText(string.format("%s  ·  %d %s%s", c.updated or "", n, n == 1 and "question" or "questions", row.current and "  ·  |cff5dff9dopen|r" or ""))
    row:Show()
  end
  for i = #list + 1, #convRows do convRows[i]:Hide() end
  p.scroll.child:SetHeight(math.max(10, #list * 50))
  p.empty:SetShown(#list == 0)
end

------------------------------------------------------------------------ memory panel
local memRows = {}
function UI.RefreshMemory()
  local p = panels and panels.memory
  if not p then return end
  local mem = (ns.db.status or {}).memory
  local width = p.scroll.child:GetWidth()
  local y = 0
  for i, m in ipairs(mem or {}) do
    local row = memRows[i]
    if not row then
      row = CreateFrame("Frame", nil, p.scroll.child, "BackdropTemplate")
      backdrop(row, P.ai)
      row.text = label(row, "", F.body)
      row.text:SetPoint("TOPLEFT", 10, -7)
      row.date = label(row, "", F.small)
      row.del = Btn(row, "x", 22, 22, function(self)
        ns.SendSetting("memory_delete", self:GetParent().memId, "Deleting…")
      end, "ghost", "Delete")
      row.del:SetPoint("TOPRIGHT", -6, -6)
      memRows[i] = row
    end
    row.memId = m.id
    row.text:SetWidth(width - 50)
    row.text:SetText(esc(m.text))
    row.date:SetText(m.at or "")
    local h = row.text:GetStringHeight() + 26
    row.date:ClearAllPoints()
    row.date:SetPoint("BOTTOMLEFT", 10, 5)
    row:SetSize(width, h)
    row:ClearAllPoints()
    row:SetPoint("TOPLEFT", p.scroll.child, "TOPLEFT", 0, -y)
    row:Show()
    y = y + h + 6
  end
  for i = #(mem or {}) + 1, #memRows do memRows[i]:Hide() end
  p.scroll.child:SetHeight(math.max(10, y))
  if mem == nil then
    p.empty:SetText("Click Refresh to load memory.")
  else
    p.empty:SetText("Memory is empty.")
  end
  p.empty:SetShown(mem == nil or #mem == 0)
end

------------------------------------------------------------------------ settings panel
local S = {}
function UI.RefreshSettings()
  if not S.built then return end
  local s = ns.db.status or {}
  local claude = s.claude or {}
  local isClaude = s.provider == "claude_code"
  S.provOR:SetActive(not isClaude)
  S.provCC:SetActive(isClaude)
  S.provCC:SetUsable(claude.loggedIn == true or isClaude)
  if claude.detected then
    S.provInfo:SetText(string.format("Claude Code %s — %s%s", esc(claude.version or ""), claude.loggedIn and "signed in" or "|cffffaa44not signed in (run claude)|r",
      claude.subscription and (", plan " .. esc(claude.subscription)) or ""))
  else
    S.provInfo:SetText("Claude Code not found.")
  end
  -- models
  local models, current = {}, nil
  if isClaude then
    for _, m in ipairs(claude.models or {}) do models[#models + 1] = { id = m.id, label = m.name } end
    current = claude.model
  else
    for _, m in ipairs(s.models or {}) do
      local price = (m["in"] and m.out) and string.format("  $%.2f/%.2f", m["in"], m.out) or ""
      models[#models + 1] = { id = m.id, label = m.id:gsub("^[^/]+/", "") .. price }
    end
    current = s.model
  end
  for i, b in ipairs(S.models) do
    local m = models[i]
    if m then
      b:SetText(m.label)
      b.modelId = m.id
      b:SetActive(m.id == current)
      b:Show()
    else
      b:Hide()
    end
  end
  for _, b in ipairs(S.search) do b:SetActive(b.mode == s.searchMode) end
  for _, b in ipairs(S.effort) do b:SetActive(isClaude and b.effort == claude.effort) end
  S.keyInfo:SetText("current key: " .. esc(s.keyMask or "?") .. (s.balance and string.format("   ·   balance $%.2f", s.balance) or ""))
  for _, b in ipairs(S.fonts) do b:SetActive(b.size == ns.db.fontSize) end
  S.Layout(isClaude, #models)
end

local function buildSettings(p)
  local sc = Scroll(p)
  sc:SetPoint("TOPLEFT", 12, -6)
  sc:SetPoint("BOTTOMRIGHT", -8, 6)
  local c = sc.child
  S.scroll = sc
  local rows = {}
  local function head(text)
    local h = label(c, text, F.head)
    rows[#rows + 1] = { h, 20 }
    return h
  end
  local function row(frame, h, cond)
    rows[#rows + 1] = { frame, h, cond }
    return frame
  end

  head("AI provider")
  local prov = CreateFrame("Frame", nil, c)
  prov:SetSize(400, 24)
  S.provOR = Btn(prov, "OpenRouter", 120, 22, function() ns.SendSetting("set_provider", "openrouter", "Switching to OpenRouter…") end)
  S.provOR:SetPoint("LEFT")
  S.provCC = Btn(prov, "Claude Code (subscription)", 190, 22, function() ns.SendSetting("set_provider", "claude_code", "Switching to Claude Code…") end)
  S.provCC:SetPoint("LEFT", S.provOR, "RIGHT", 6, 0)
  row(prov, 26)
  S.provInfo = label(c, "", F.small)
  row(S.provInfo, 18)

  S.modelHead = head("Model")
  local grid = CreateFrame("Frame", nil, c)
  grid:SetSize(400, 4 * 26)
  S.models = {}
  for i = 1, 8 do
    local b = Btn(grid, "", 200, 22, function(self)
      if self.modelId then ns.SendSetting("set_model", self.modelId, "Changing model…") end
    end)
    b:SetPoint("TOPLEFT", ((i - 1) % 2) * 206, -math.floor((i - 1) / 2) * 26)
    S.models[i] = b
  end
  S.grid = row(grid, 4 * 26)

  S.effortHead = head("Thinking level")
  local eff = CreateFrame("Frame", nil, c)
  eff:SetSize(400, 24)
  S.effort = {}
  for i, e in ipairs({ "low", "medium", "high", "xhigh", "max" }) do
    local b = Btn(eff, ns.EffortName(e), 74, 22, function() ns.SendSetting("set_effort", e, "Changing thinking level…") end)
    b:SetPoint("LEFT", (i - 1) * 80, 0)
    b.effort = e
    S.effort[i] = b
  end
  S.effortRow = row(eff, 28)

  S.customHead = head("Other OpenRouter model (ID)")
  local custom = CreateFrame("Frame", nil, c)
  custom:SetSize(400, 26)
  local cin = Input(custom, 280, "e.g. openai/gpt-6-luna")
  cin:SetPoint("LEFT")
  local cset = Btn(custom, "Set", 80, 24, function()
    local id = cin.edit:GetText():gsub("%s", "")
    if id ~= "" then ns.SendSetting("set_model", id, "Changing model…") cin.edit:SetText("") cin.edit:ClearFocus() end
  end)
  cset:SetPoint("LEFT", cin, "RIGHT", 6, 0)
  S.customRow = row(custom, 30)

  S.keyHead = head("OpenRouter key")
  local keyRow = CreateFrame("Frame", nil, c)
  keyRow:SetSize(400, 26)
  local kin = Input(keyRow, 280, "sk-or-…")
  kin:SetPoint("LEFT")
  local kset = Btn(keyRow, "Save", 80, 24, function()
    local key = kin.edit:GetText():gsub("%s", "")
    if not key:match("^sk%-or%-") then UI.Notice("The key should start with sk-or-.", "warn") return end
    kin.edit:SetText("")
    kin.edit:ClearFocus()
    ns.SendSetting("set_api_key", key, "Verifying key…")
  end, "primary")
  kset:SetPoint("LEFT", kin, "RIGHT", 6, 0)
  S.keyRow = row(keyRow, 28)
  S.keyInfo = label(c, "", F.small)
  S.keyInfoRow = row(S.keyInfo, 18)

  head("Web search")
  local srch = CreateFrame("Frame", nil, c)
  srch:SetSize(400, 24)
  S.search = {}
  for i, m in ipairs({ { "auto", "Auto" }, { "always", "Always" }, { "never", "Never" } }) do
    local b = Btn(srch, m[2], 96, 22, function() ns.SendSetting("set_search_mode", m[1], "Changing web search…") end)
    b:SetPoint("LEFT", (i - 1) * 102, 0)
    b.mode = m[1]
    S.search[i] = b
  end
  row(srch, 28)

  head("Data sent to the AI")
  local data = CreateFrame("Frame", nil, c)
  data:SetSize(420, 4 * 24)
  local opts = { { "bags", "Bags & bank" }, { "quests", "Quests" }, { "talents", "Talents" }, { "spells", "Spells" },
    { "skills", "Skills" }, { "reputations", "Reputation" }, { "nearby", "Nearby players" }, { "windows", "Open windows" },
    { "auras", "Auras" }, { "addons", "Add-ons" }, { "chat", "Chat (private)" } }
  for i, o in ipairs(opts) do
    local cb = Check(data, o[2], function()
      if o[1] == "chat" then return ns.db.sections.chat == true end
      return ns.db.sections[o[1]] ~= false
    end, function(v) ns.db.sections[o[1]] = v end)
    cb:SetPoint("TOPLEFT", ((i - 1) % 3) * 140, -math.floor((i - 1) / 3) * 24)
  end
  row(data, 4 * 24)

  head("Appearance")
  local look = CreateFrame("Frame", nil, c)
  look:SetSize(420, 26)
  local fl = label(look, "Font size", F.btn)
  fl:SetPoint("LEFT")
  S.fonts = {}
  for i, size in ipairs({ 12, 13, 14, 15 }) do
    local b = Btn(look, tostring(size), 36, 22, function()
      ns.db.fontSize = size
      applyFontSize(size)
      UI.RefreshSettings()
      relayout()
    end)
    b:SetPoint("LEFT", 70 + (i - 1) * 40, 0)
    b.size = size
    S.fonts[i] = b
  end
  local showBtn = Check(look, "AI button", function() return ns.db.button.show ~= false end,
    function(v) ns.db.button.show = v ns.Button.Refresh() end)
  showBtn:SetPoint("LEFT", 240, 0)
  row(look, 30)

  local tools = CreateFrame("Frame", nil, c)
  tools:SetSize(420, 26)
  local st = Btn(tools, "Refresh", 110, 22, function() ns.RequestStatus() end)
  st:SetPoint("LEFT")
  local rp = Btn(tools, "Center window", 120, 22, function()
    main:ClearAllPoints()
    main:SetPoint("CENTER")
    ns.db.window.point, ns.db.window.x, ns.db.window.y = "CENTER", 0, 0
  end)
  rp:SetPoint("LEFT", st, "RIGHT", 6, 0)
  row(tools, 30)
  local info = label(c, "Stored only on this computer by the ForeverAI app.", F.small)
  row(info, 30)

  function S.Layout(isClaude, nModels)
    local hidden = {
      [S.effortHead] = not isClaude, [S.effortRow] = not isClaude,
      [S.customHead] = isClaude, [S.customRow] = isClaude,
      [S.keyHead] = isClaude, [S.keyRow] = isClaude, [S.keyInfoRow] = isClaude,
    }
    S.modelHead:SetText(isClaude and "Claude model" or "OpenRouter model (price per 1M tokens: in / out)")
    local width = c:GetWidth()
    local y = 0
    for _, r in ipairs(rows) do
      local f, h = r[1], r[2]
      if f == S.grid then h = math.ceil(nModels / 2) * 26 end
      if hidden[f] then
        f:Hide()
      else
        f:Show()
        f:ClearAllPoints()
        f:SetPoint("TOPLEFT", c, "TOPLEFT", 0, -y)
        if f.SetWidth and f:GetObjectType() == "FontString" then f:SetWidth(width - 10) end
        y = y + h + 4
      end
    end
    c:SetHeight(y + 10)
  end
  S.built = true
end

------------------------------------------------------------------------ tabs
function UI.ShowTab(name)
  if not panels then return end
  activeTab = name
  for key, p in pairs(panels) do p:SetShown(key == name) end
  for key, t in pairs(tabs) do t:SetActive(key == name) end
  if name == "chat" then relayout() chat:ToBottom() end
  if name == "convs" then UI.RefreshConversations() end
  if name == "memory" then UI.RefreshMemory() end
  if name == "settings" then UI.RefreshSettings() end
end

------------------------------------------------------------------------ build
local function sendCurrent()
  local text = input.edit:GetText()
  if text:gsub("%s", "") == "" then return end
  if ns.pending or ns.collecting then UI.Notice("Wait for the current answer to finish.", "warn") return end
  input.edit:SetText("")
  local withScreen = screenCheck and screenCheck:GetChecked() and true or false
  if screenCheck then screenCheck:SetChecked(false) end -- one question at a time
  ns.Ask(text, { screen = withScreen })
end

local SUGGESTIONS = {
  "Where do I turn in my quest?",
  "Which item should I upgrade first?",
  "Who is my target?",
  "What did the latest hotfixes change?",
}

local function build()
  local pos = ns.db.window
  main = CreateFrame("Frame", "ForeverAIFrame", UIParent, "BackdropTemplate")
  main:SetSize(pos.w or 470, pos.h or 600)
  main:SetPoint(pos.point or "CENTER", UIParent, pos.point or "CENTER", pos.x or 0, pos.y or 0)
  backdrop(main, P.bg, P.edge)
  main:SetFrameStrata("HIGH")
  main:SetMovable(true)
  main:SetResizable(true)
  if main.SetResizeBounds then main:SetResizeBounds(380, 420, 1000, 1100) end
  main:EnableMouse(true)
  main:SetClampedToScreen(true)
  main:Hide()
  tinsert(UISpecialFrames, "ForeverAIFrame")

  -- header (drag handle)
  local hb = CreateFrame("Frame", nil, main, "BackdropTemplate")
  hb:SetPoint("TOPLEFT", 1, -1)
  hb:SetPoint("TOPRIGHT", -1, -1)
  hb:SetHeight(34)
  backdrop(hb, P.bar)
  hb:EnableMouse(true)
  hb:RegisterForDrag("LeftButton")
  hb:SetScript("OnDragStart", function() main:StartMoving() end)
  hb:SetScript("OnDragStop", function()
    main:StopMovingOrSizing()
    local point, _, _, x, y = main:GetPoint()
    ns.db.window.point, ns.db.window.x, ns.db.window.y = point, x, y
  end)
  local title = label(hb, "ForeverAI", F.title)
  title:SetPoint("LEFT", 12, 0)
  local pillBg = CreateFrame("Frame", nil, hb, "BackdropTemplate")
  backdrop(pillBg, { 1, 1, 1, 0.05 })
  pill = label(pillBg, "", F.small)
  pill:SetPoint("CENTER")
  pillBg:SetPoint("LEFT", title, "RIGHT", 10, 0)
  pillBg:SetSize(180, 20)
  hooksecurefunc(pill, "SetText", function() pillBg:SetWidth(math.min(240, pill:GetStringWidth() + 18)) end)
  local close = Btn(hb, "x", 26, 26, function() main:Hide() end, "ghost", "Close (Esc)")
  close:SetPoint("RIGHT", -4, 0)
  local new = Btn(hb, "+ New", 66, 24, function() ns.NewConversation() end, "ghost", "New conversation")
  new:SetPoint("RIGHT", close, "LEFT", -4, 0)

  -- tabs
  tabs = {}
  local tabDefs = { { "chat", "Chat" }, { "convs", "History" }, { "memory", "Memory" }, { "settings", "Settings" } }
  local prev
  for _, t in ipairs(tabDefs) do
    local b = Btn(main, t[2], 92, 24, function() UI.ShowTab(t[1]) end, "ghost")
    if prev then b:SetPoint("LEFT", prev, "RIGHT", 2, 0) else b:SetPoint("TOPLEFT", main, "TOPLEFT", 8, -40) end
    tabs[t[1]] = b
    prev = b
  end
  local line = main:CreateTexture(nil, "ARTWORK")
  line:SetColorTexture(1, 1, 1, 0.07)
  line:SetHeight(1)
  line:SetPoint("TOPLEFT", 1, -68)
  line:SetPoint("TOPRIGHT", -1, -68)

  -- footer
  footer = label(main, "", F.small)
  footer:SetPoint("BOTTOMLEFT", 12, 8)
  notice = label(main, "", F.small)
  notice:SetJustifyH("RIGHT")
  notice:SetPoint("BOTTOMRIGHT", -26, 8)
  notice:SetPoint("LEFT", footer, "RIGHT", 10, 0)
  reloadBtn = Btn(main, "Reload UI", 96, 18, function() ReloadUI() end, "primary", "Reload the UI to continue receiving answers")
  reloadBtn:SetPoint("BOTTOMRIGHT", -24, 5)
  reloadBtn:Hide()
  local grip = CreateFrame("Button", nil, main)
  grip:SetSize(16, 16)
  grip:SetPoint("BOTTOMRIGHT", -2, 2)
  grip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
  grip:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
  grip:SetPushedTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down")
  grip:SetScript("OnMouseDown", function() main:StartSizing("BOTTOMRIGHT") end)
  grip:SetScript("OnMouseUp", function()
    main:StopMovingOrSizing()
    ns.db.window.w, ns.db.window.h = main:GetWidth(), main:GetHeight()
    local point, _, _, x, y = main:GetPoint()
    ns.db.window.point, ns.db.window.x, ns.db.window.y = point, x, y
    relayout()
  end)

  -- panels
  panels = {}
  local function panel(key)
    local p = CreateFrame("Frame", nil, main)
    p:SetPoint("TOPLEFT", 1, -70)
    p:SetPoint("BOTTOMRIGHT", -1, 24)
    p:Hide()
    panels[key] = p
    return p
  end

  -- chat
  local pc = panel("chat")
  local inputBar = CreateFrame("Frame", nil, pc)
  inputBar:SetPoint("BOTTOMLEFT", 10, 26)
  inputBar:SetPoint("BOTTOMRIGHT", -10, 26)
  inputBar:SetHeight(30)
  input = Input(inputBar, 100, "Ask… (Alt+click an item, spell or quest to tag it)")
  input:SetPoint("TOPLEFT")
  input:SetPoint("BOTTOMRIGHT", -88, 0)
  input.edit:SetScript("OnEnterPressed", function() sendCurrent() end)
  input.edit:SetScript("OnKeyDown", function(self, key)
    if key == "UP" and self:GetText() == "" and lastQuestion then self:SetText(lastQuestion) end
  end)
  sendBtn = Btn(inputBar, "Send", 82, 30, sendCurrent, "primary")
  sendBtn:SetPoint("RIGHT")
  -- under the text field: the player decides per question whether the AI sees the screen; off by default
  screenCheck = CreateFrame("CheckButton", nil, pc, "UICheckButtonTemplate")
  screenCheck:SetSize(20, 20)
  screenCheck:SetPoint("TOPLEFT", inputBar, "BOTTOMLEFT", -3, -2)
  local sl = label(screenCheck, "Attach the current screen", F.small)
  sl:SetPoint("LEFT", screenCheck, "RIGHT", 1, 1)
  screenCheck:SetHitRectInsets(0, -(sl:GetStringWidth() + 4), 0, 0) -- the label is clickable too
  screenCheck:SetScript("OnEnter", function(self)
    GameTooltip:SetOwner(self, "ANCHOR_TOP")
    GameTooltip:SetText("The AI sees your screen only with this question.", 1, 1, 1, 1, true)
    GameTooltip:AddLine("Turns off after sending.", 0.7, 0.7, 0.7, true)
    ns.ApplyTooltipFont()
  end)
  screenCheck:SetScript("OnLeave", function() GameTooltip:Hide() end)
  chat = Scroll(pc)
  chat:SetPoint("TOPLEFT", 10, -6)
  chat:SetPoint("BOTTOMRIGHT", inputBar, "TOPRIGHT", 0, 6)
  chat:HookScript("OnSizeChanged", function() C_Timer.After(0, relayout) end)
  emptyState = CreateFrame("Frame", nil, pc)
  emptyState:SetPoint("CENTER", chat, "CENTER", 0, 20)
  emptyState:SetSize(360, 220)
  local et = label(emptyState, "|cff5dff9dHow can I help?|r", F.body)
  et:SetJustifyH("CENTER")
  et:SetWidth(340)
  et:SetPoint("TOP")
  for i, s in ipairs(SUGGESTIONS) do
    local b = Btn(emptyState, s, 340, 24, function() if not ns.pending then ns.Ask(s) end end)
    b:SetPoint("TOP", et, "BOTTOM", 0, -10 - (i - 1) * 28)
  end

  -- conversations
  local pv = panel("convs")
  local newConv = Btn(pv, "+ New conversation", 140, 26, function() ns.NewConversation() end, "primary")
  newConv:SetPoint("TOPLEFT", 12, -8)
  local hint = label(pv, "Select a conversation to continue it.", F.small)
  hint:SetPoint("LEFT", newConv, "RIGHT", 10, 0)
  pv.scroll = Scroll(pv)
  pv.scroll:SetPoint("TOPLEFT", 12, -42)
  pv.scroll:SetPoint("BOTTOMRIGHT", -8, 6)
  pv.empty = label(pv, "No conversations yet.", F.dim)
  pv.empty:SetPoint("TOP", 0, -60)
  pv.scroll.stick = false

  -- memory
  local pm = panel("memory")
  local mt = label(pm, "|cffffd27aMemory|r", F.body)
  mt:SetPoint("TOPLEFT", 12, -8)
  local md = label(pm, "Saved facts used in every conversation.", F.small)
  md:SetPoint("TOPLEFT", 12, -26)
  md:SetPoint("RIGHT", -12, 0)
  local addBar = CreateFrame("Frame", nil, pm)
  addBar:SetPoint("BOTTOMLEFT", 12, 6)
  addBar:SetPoint("BOTTOMRIGHT", -12, 6)
  addBar:SetHeight(26)
  local memIn = Input(addBar, 100, "Add an entry")
  memIn:SetPoint("TOPLEFT")
  memIn:SetPoint("BOTTOMRIGHT", -84, 0)
  local function addMem()
    local t = memIn.edit:GetText()
    if t:gsub("%s", "") == "" then return end
    memIn.edit:SetText("")
    memIn.edit:ClearFocus()
    ns.SendSetting("memory_add", t, "Adding…")
  end
  memIn.edit:SetScript("OnEnterPressed", addMem)
  local addB = Btn(addBar, "Add", 78, 26, addMem, "primary")
  addB:SetPoint("RIGHT")
  local tools = CreateFrame("Frame", nil, pm)
  tools:SetPoint("BOTTOMLEFT", addBar, "TOPLEFT", 0, 6)
  tools:SetSize(300, 24)
  local refresh = Btn(tools, "Refresh", 80, 22, function() ns.SendSetting("memory_list", true, "Loading…") end)
  refresh:SetPoint("LEFT")
  local clearB
  clearB = Btn(tools, "Clear memory", 120, 22, function(self)
    if self.armed then
      self.armed = false
      self:SetText("Clear memory")
      ns.SendSetting("memory_clear", true, "Clearing…")
    else
      self.armed = true
      self:SetText("Click to confirm")
      C_Timer.After(4, function() if self.armed then self.armed = false self:SetText("Clear memory") end end)
    end
  end, "danger")
  clearB:SetPoint("LEFT", refresh, "RIGHT", 6, 0)
  pm.scroll = Scroll(pm)
  pm.scroll:SetPoint("TOPLEFT", 12, -50)
  pm.scroll:SetPoint("BOTTOMRIGHT", pm, "BOTTOMRIGHT", -8, 70)
  pm.scroll.stick = false
  pm.empty = label(pm, "", F.dim)
  pm.empty:SetPoint("TOP", 0, -90)
  pm.empty:SetWidth(360)
  pm.empty:SetJustifyH("CENTER")

  -- settings
  local ps = panel("settings")
  buildSettings(ps)

  main:SetScript("OnShow", function()
    if ns.Button then ns.Button.SetBadge(false) end
    UI.UpdateHeader()
    UI.UpdateBusy()
    UI.ShowTab(activeTab)
  end)
end

------------------------------------------------------------------------ quest under the cursor (for Alt+click tagging)
function UI.QuestLink(questId, title, level)
  questId = tonumber(questId)
  if not questId or questId <= 0 then return nil end
  title = title or (C_QuestLog.GetTitleForQuestID and C_QuestLog.GetTitleForQuestID(questId))
  if not title and ns.Collect and ns.Collect.QuestInfo then
    local info = ns.Collect.QuestInfo(questId)
    title, level = info and info.title, level or (info and info.level)
  end
  if not title then return nil end
  return string.format("|cffffff00|Hquest:%d:%d|h[%s]|h|r", questId, tonumber(level) or 0, ns.stripCodes(title))
end

-- The frame keeps its own OnClick; for the one Alt+click that tags a quest it gets a no-op that restores the original.
local function swallowNextClick(frame)
  if type(frame) ~= "table" or not frame.GetScript or not frame.SetScript then return end
  if frame.IsProtected and frame:IsProtected() then return end -- secure frames are left alone
  local ok, orig = pcall(frame.GetScript, frame, "OnClick")
  if not ok or not orig or frame.foreverAISwallowing then return end
  frame.foreverAISwallowing = true
  local function restore()
    if frame.foreverAISwallowing then
      frame.foreverAISwallowing = nil
      pcall(frame.SetScript, frame, "OnClick", orig)
    end
  end
  pcall(frame.SetScript, frame, "OnClick", function() restore() end)
  C_Timer.After(3, restore) -- the button was released elsewhere: no click came
end
UI.swallowNextClick = swallowNextClick

local function questOf(f)
  -- Questie tracker lines carry the quest object; game frames use questID / questLogIndex
  local q = rawget(f, "Quest")
  if type(q) == "table" and tonumber(q.Id) then return q.Id, q.name, q.level end
  local id = rawget(f, "questID") or rawget(f, "questId")
  if tonumber(id) then return id end
  local idx = rawget(f, "questLogIndex")
  if tonumber(idx) and C_QuestLog.GetInfo then
    local info = C_QuestLog.GetInfo(idx)
    if info and info.questID and not info.isHeader then return info.questID, info.title, info.level end
  end
  return nil
end

function UI.QuestLinkUnderCursor()
  local foci = GetMouseFoci and GetMouseFoci() or { GetMouseFocus and GetMouseFocus() }
  for _, focus in ipairs(foci or {}) do
    local f, depth = focus, 0
    while type(f) == "table" and depth < 6 do
      local id, title, level = questOf(f)
      if id then return UI.QuestLink(id, title, level), focus end
      f = f.GetParent and f:GetParent()
      depth = depth + 1
    end
  end
  -- a quest shown in the tooltip
  local data = GameTooltip:IsShown() and GameTooltip.GetTooltipData and ns.safe(GameTooltip.GetTooltipData, GameTooltip)
  if data and data.id and Enum.TooltipDataType and data.type == Enum.TooltipDataType.Quest then
    return UI.QuestLink(data.id)
  end
  return nil
end

function UI.Init()
  applyFontSize(ns.db.fontSize or 13)
  build()
  -- tagging: with the ForeverAI window open, Alt+click on an item/spell/talent inserts its link into the question
  local lastInsert, lastLink = 0, nil
  local function insertLink(link)
    if type(link) ~= "string" or not link:find("|H", 1, true) then return end
    if not (main and main:IsShown() and activeTab == "chat") then return end
    -- one click can reach several hooks with different strings for the same object: compare "item:123" / "spell:45"
    local key = link:match("|H(%a+:%-?%d+)") or link
    if key == lastLink and GetTime() - lastInsert < 0.6 then return end
    lastInsert, lastLink = GetTime(), key
    input.edit:SetFocus()
    input.edit:Insert(link .. " ")
  end
  local function altOnly() return IsAltKeyDown() and not IsShiftKeyDown() and not IsControlKeyDown() end
  -- whatever the cursor is over is shown in a tooltip: take the item or spell from it (bags, character, spellbook,
  -- talents, loot, quest rewards, action bars...)
  local function tooltipLink()
    for _, tip in ipairs({ GameTooltip }) do -- not ItemRefTooltip: it stays open after a click and would go stale
      if tip and tip:IsShown() then
        local _, itemLink = ns.safe(tip.GetItem, tip)
        if itemLink then return itemLink end
        local name, spellId = ns.safe(tip.GetSpell, tip)
        if spellId then
          local link = C_Spell.GetSpellLink and ns.safe(C_Spell.GetSpellLink, spellId)
          return link or ("|cff71d5ff|Hspell:" .. spellId .. ":0|h[" .. (name or spellId) .. "]|h|r")
        end
        local data = tip.GetTooltipData and ns.safe(tip.GetTooltipData, tip)
        if data and data.id and Enum.TooltipDataType then
          if data.type == Enum.TooltipDataType.Spell then
            local link = C_Spell.GetSpellLink and ns.safe(C_Spell.GetSpellLink, data.id)
            if link then return link end
          elseif data.type == Enum.TooltipDataType.Item then
            local _, link = C_Item.GetItemInfo(data.id)
            if link then return link end
          end
        end
      end
    end
  end
  local clicks = CreateFrame("Frame")
  if pcall(clicks.RegisterEvent, clicks, "GLOBAL_MOUSE_DOWN") then
    clicks:SetScript("OnEvent", function(_, _, button)
      if button == "LeftButton" and altOnly() then
        if not (main and main:IsShown() and activeTab == "chat") then return end -- tagging only with the chat open
        -- a quest under the cursor (Questie tracker, quest log, game tracker) wins over a tooltip left on screen
        local okQ, questLink, clicked = pcall(UI.QuestLinkUnderCursor)
        if okQ and questLink then
          insertLink(questLink)
          swallowNextClick(clicked) -- so Questie does not open the map / quest log for this click
          return
        end
        local ok, link = pcall(tooltipLink)
        if ok and link then insertLink(link) end
      end
    end)
  end
  if HandleModifiedItemClick then -- items without a tooltip on screen (e.g. links in chat)
    hooksecurefunc("HandleModifiedItemClick", function(link)
      if altOnly() then insertLink(link) end
    end)
  end
  if SetItemRef then -- Alt+click on a link in the chat
    hooksecurefunc("SetItemRef", function(link, text)
      if altOnly() and type(text) == "string" then insertLink(text) end
    end)
  end
  UI.LoadConversation()
  UI.UpdateHeader()
  UI.ShowTab("chat")
end

function UI.Show() if main then main:Show() end end
function UI.Toggle() if main then main:SetShown(not main:IsShown()) end end
function UI.SetInput(text)
  if not input then return end
  input.edit:SetText(text or "")
  input.edit:SetFocus()
  input.edit:HighlightText()
end
