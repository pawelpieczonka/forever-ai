-- ForeverAI: window (chat, saved conversations, memory, settings)
local ADDON, ns = ...
local UI = {}
ns.UI = UI

local WHITE = "Interface\\Buttons\\WHITE8x8"
-- the add-on's own picture (media/route-auto.tga): two arrows round a circle, tinted green or red
local ROUTE_AUTO_ICON = "Interface\\AddOns\\" .. ADDON .. "\\media\\route-auto"
local AUTO_ON, AUTO_OFF = { 0.36, 1, 0.62, 1 }, { 1, 0.36, 0.36, 1 }
local LOCK_ICON = "Interface\\AddOns\\" .. ADDON .. "\\media\\route-lock" -- media/route-lock.tga: a padlock
local LOCK_ON, LOCK_OFF = { 1, 0.82, 0.2, 1 }, { 1, 1, 1, 0.28 }
local P = {
  bg = { 0.045, 0.052, 0.066, 0.97 }, bar = { 0.07, 0.08, 0.1, 1 }, edge = { 0.2, 0.24, 0.28, 1 },
  accent = { 0.27, 0.86, 0.62, 1 }, user = { 0.12, 0.25, 0.4, 0.96 }, ai = { 0.1, 0.112, 0.138, 0.96 },
  card = { 0.22, 0.155, 0.05, 0.95 }, cardEdge = { 0.95, 0.72, 0.3, 0.75 },
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
  holder.edit, holder.hint = e, hint
  return holder
end

local function Check(parent, text, get, set)
  local cb = CreateFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
  cb:SetSize(22, 22)
  local l = label(cb, text, F.btn)
  l:SetPoint("LEFT", cb, "RIGHT", 2, 0)
  cb.label = l
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
local main, panels, tabs, header, pill, footer, notice, reloadBtn, sendBtn, input, screenCheck, strip
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

------------------------------------------------------------------------ news lines
-- An answer the add-on asked for by itself (the update of a route) is one short line in the chat with the arrows,
-- not a conversation: the AI's first paragraph, no model line, no card. The screen says when it starts and when it is
-- done, unless the player turned that off in Settings.
local function newsText(text)
  return string.format("|T%s:14:14:0:-1:64:64:0:64:0:64:93:255:157|t %s", ROUTE_AUTO_ICON, text)
end

local function newsLine(text)
  local line = tostring(text or ""):gsub("^%s+", ""):gsub("\n%s*\n.*$", ""):gsub("\n", " ")
  if #line > 220 then line = line:sub(1, 220):gsub("%s+%S*$", "") .. "…" end
  return line
end

function UI.RouteAlert(state)
  if ns.db.routeAlerts == false or not (UIErrorsFrame and UIErrorsFrame.AddMessage) then return end
  local text, color = "ForeverAI: updating the route…", { 0.56, 0.83, 1 }
  if state == "done" then text, color = "ForeverAI: route updated", AUTO_ON
  elseif state == "failed" then text, color = "ForeverAI: the route was not updated", AUTO_OFF end
  pcall(UIErrorsFrame.AddMessage, UIErrorsFrame, text, color[1], color[2], color[3])
end

-- A stop of the route was done by what the player did (an objective, a turn-in, a zone entered): said on the screen
-- (unless turned off in Settings) and as a line of the chat, with the stop the arrow leads to now.
function UI.RouteProgress(done, nextStop, meters)
  local text = "done: " .. table.concat(done, ", ") .. ". "
    .. (nextStop and ("Next: " .. nextStop .. (meters and string.format(" (%d m)", meters) or "")) or "The route is finished.")
  if ns.db.routeAlerts ~= false and UIErrorsFrame and UIErrorsFrame.AddMessage then
    pcall(UIErrorsFrame.AddMessage, UIErrorsFrame, "ForeverAI: " .. text, AUTO_ON[1], AUTO_ON[2], AUTO_ON[3])
  end
  local line = text:gsub("^d", "D")
  bubble("meta", newsText("|cff9aa3ad" .. esc(line) .. "|r"))
  ns.RecordEntry({ k = "n", t = line })
  chat:ToBottom()
  relayout()
end

function UI.BeginAnswer(msgId, news)
  if news then
    -- an update asked again (a quest overtook it) goes on in the line of the one before
    local e = UI.heldNews or bubble("meta", newsText("|cff7f8a99" .. esc(news) .. "…|r"))
    UI.heldNews = nil
    e.msgId, e.news, e.done = msgId, news, nil
    currentAnswer = e
    chat:ToBottom()
    relayout()
    return
  end
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
  if data.final and e.news and ns.Map and ns.Map.UpdateIsStale and ns.Map.UpdateIsStale() then
    -- A quest was taken while this update was written: the route in it is older than the quest log. It is not put
    -- on the map; the line stays as it is and the update that is asked now writes into it.
    UI.heldNews = e
    currentAnswer = nil
    ns.Map.UpdateSuperseded()
    return
  end
  if data.final then
    e.done = true
    e.frame:SetScript("OnUpdate", nil)
    if e.news and data.routeUnchanged then
      -- looked at, nothing to change: one dim line, which the next update writes into; nothing on the screen
      e.frame.fs:SetText(newsText("|cff7f8a99" .. esc(newsLine(data.text)) .. "|r"))
      UI.heldNews = e
    elseif e.news then
      local line = newsLine(data.text)
      e.frame.fs:SetText(newsText(ns.Links.Linkify(line, data.links)))
      ns.RecordEntry({ k = "n", t = line, links = data.links })
      UI.RouteAlert("done")
    else
      local text = ns.Links.Linkify(data.text or "", data.links)
      e.frame.fs:SetText(text)
      local meta = metaLine(data)
      local src = sourcesLine(data.sources)
      local metaText = table.concat({ meta or "", src or "" }, (meta and src) and "\n" or "")
      if metaText ~= "" then bubble("meta", metaText) end
      ns.RecordEntry({ k = "a", t = data.text or "", links = data.links, meta = metaText ~= "" and metaText or nil })
    end
    for _, a in ipairs(data.actions or {}) do UI.ActionCard(a) end
    -- the AI's changes to the route that is on the map come first; marks of the same answer are added to the result
    if type(data.route) == "table" and ns.Map then ns.Map.Edit(data.route) end
    if type(data.untrack) == "table" and ns.Track then
      for _, name in ipairs(data.untrack) do ns.Track.Stop(tostring(name)) end
    end
    local marked = data.marks and #data.marks > 0
    -- the creatures to watch for belong to the marks: a new set of marks brings its own (or none)
    if ns.Track and (marked or (type(data.track) == "table" and #data.track > 0)) then
      ns.Track.Set(data.track or {}, data.marksReplace == false)
    end
    if marked then
      -- A route is what plan_route marked, or any two stops (the AI also marks a route stop by stop). The route on the
      -- map keeps its switch when it is marked again, replaced or added to; with no route on the map a new one
      -- starts as Settings say.
      local hadRoute, wasAuto = ns.char.routePlanned and ns.Map.Count() > 0, ns.char.routeAuto
      -- the add-on's own update marks the same route again: what the player took off it stays known to the AI
      local tookOff = e.news and ns.char.marksRemoved or nil
      UI.MarksCard(data.marks, data.marksReplace, data.track, e.news)
      if tookOff then ns.char.marksRemoved = tookOff end
      -- the order the AI gave may name the stops this answer added: it is applied again now that they are there
      if type(data.route) == "table" and type(data.route.order) == "table" then ns.Map.Edit({ order = data.route.order }) end
      local isRoute = data.routePlanned == true or ns.Map.Count() >= 2 or (data.marksReplace == false and hadRoute)
      ns.char.routePlanned = isRoute or nil
      if not isRoute then
        ns.char.routeAuto = nil
      elseif hadRoute then
        ns.char.routeAuto = wasAuto
      else
        ns.char.routeAuto = ns.db.autoRoute == true or nil
      end
      if isRoute then ns.Map.RouteKnown() end -- it was made for the quest log as it is now
      UI.UpdateStrip()
    end
    if marked or type(data.route) == "table" then ns.char.routeRejected = nil end -- the player asked for this one
    -- The route was just looked at again by the AI: the stops that are done leave the list now. Unless nothing is
    -- left to do: then the route the player finished stays in the window (all done), not an empty space where the
    -- route was a moment ago; a new route takes its place, or the player clears it.
    if e.news and ns.Map then
      if ns.Map.Left() > 0 then ns.Map.DropDone() end
      ns.Map.UpdateAnswered()
    end
    if type(data.auction) == "table" and #data.auction > 0 then UI.AuctionCard(data.auction) end
    currentAnswer = nil
  elseif not e.news then -- a news line waits for the whole answer
    e.body = ns.Links.Linkify(data.text or "", nil)
    if data.phase then e.phase = tostring(data.phase):gsub("…$", "") end
  end
  relayout()
end

function UI.FailAnswer(text)
  if currentAnswer and currentAnswer.news then -- the news line says it, and so does the screen
    local line = "Route not updated: " .. tostring(text)
    currentAnswer.frame.fs:SetText(newsText("|cffff9a9a" .. esc(line) .. "|r"))
    ns.RecordEntry({ k = "n", t = line })
    currentAnswer = nil
    UI.RouteAlert("failed")
    if ns.Map and ns.Map.UpdateFailed then ns.Map.UpdateFailed() end
    relayout()
    return
  end
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
    elseif en.k == "n" then
      bubble("meta", newsText(ns.Links.Linkify(en.t or "", en.links)))
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

-- A change of the route the AI proposes by itself (the player did not ask for it): nothing changes until Accept. The
-- decision is not sent to the AI, which would cost an answer: the marks section of the next question shows the route
-- as it is, and says when the player rejected the proposal.
local function applyRoute(action)
  if action.op == "route" then
    -- a correction of the route on the map keeps its switch; with no planned route it starts as Settings say
    local auto = ns.char.routeAuto
    if not ns.char.routePlanned then auto = ns.db.autoRoute == true end
    ns.Map.Replace(action.stops or {})
    if ns.Track then ns.Track.Set(action.track or {}) end
    ns.char.routePlanned = true -- the AI's own route: kept up to date like any planned one
    ns.char.routeAuto = auto and true or nil
    ns.Map.RouteKnown()
    UI.UpdateStrip()
  else
    ns.Map.Edit(action.edit or {})
    if ns.Track then
      for _, name in ipairs(action.untrack or {}) do ns.Track.Stop(tostring(name)) end
    end
  end
  ns.char.routeRejected = nil
end

function UI.RouteCard(action)
  local lines, head = {}, "Change the route"
  if action.op == "route" then
    head = "New route"
    for _, s in ipairs(action.stops or {}) do
      lines[#lines + 1] = string.format("• %s  |cff9aa3ad(%s%.1f, %.1f)|r", esc(s.title), s.zone and s.zone ~= "" and (esc(s.zone) .. " ") or "",
        tonumber(s.x) or 0, tonumber(s.y) or 0)
    end
    local names = {}
    for _, t in ipairs(action.track or {}) do names[#names + 1] = esc(type(t) == "table" and t.name or t) end
    if #names > 0 then lines[#lines + 1] = "|cff9aa3adWatching for " .. table.concat(names, ", ") .. "|r" end
  else
    lines[1] = esc(action.summary or "")
  end
  card(string.format("|cff7fd6ff%s|r |cff6a7380(proposed: nothing changes until you accept)|r\n%s", head,
    table.concat(lines, "\n")), { 0.3, 0.6, 0.9, 0.7 }, {
    { "Accept", 96, function(self)
      local ok, err = pcall(applyRoute, action)
      self:Done(ok and "|cff5dff9dApplied.|r" or ("|cffff6060Failed: " .. esc(tostring(err)) .. "|r"))
    end, "primary" },
    { "Reject", 76, function(self)
      if ns.char then ns.char.routeRejected = true end
      self:Done("|cff8a93a0Rejected: the route stays as it is.|r")
    end },
  })
  relayout()
end

function UI.ActionCard(action)
  if action.op == "route" or action.op == "route_edit" then return UI.RouteCard(action) end
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
      -- A sale takes a few seconds: the AI hears about it when it is over, with what was really sold. Told at once
      -- it got the bags half sold, called the sale partial and proposed to sell what was gone a moment later.
      local selling = action.op == "vendor_sell"
      local ok, res = ns.Addons.Apply(action, selling and function(sold, all, gained, why)
        local result = string.format("%d of %d stacks sold for %s", sold, all, ns.Coins(gained))
        if self.status then self.status:SetText("|cff5dff9dSold " .. esc((result:gsub(" sold", ""))) .. ".|r") end
        if self.undo and sold > 0 then self.undo:Show() end -- buying back is for a sale that is over
        ns.ActionFeedback(string.format("Accepted: %s — %s.", describeAction(action),
          why and (result .. "; " .. why) or sold < all and (result .. "; the vendor did not take the rest")
          or ("done: " .. result)))
      end or nil)
      if not (ok and selling) then
        ns.ActionFeedback(string.format("Accepted: %s — %s.", describeAction(action),
          ok and "done" or ("failed: " .. tostring(res))))
      end
      if ok then
        self.entry = res
        self:Done(selling and "|cff5dff9dSelling…|r" or "|cff5dff9dApplied.|r")
        if action.op == "abandon_quest" or action.op == "vendor_repair" then return end -- nothing to undo
        local undo = Btn(self.frame, "Undo", 70, 20, function(btn)
          local okU, err = ns.Addons.Undo(self.entry)
          ns.ActionFeedback(string.format("Undone: %s — %s.", describeAction(action),
            okU and "done" or ("failed: " .. tostring(err))))
          btn:Hide()
          self.status:SetText(okU and "|cffe0d060Undone.|r" or ("|cffff6060" .. esc(err) .. "|r"))
        end)
        undo:SetPoint("TOPRIGHT", self.frame, "TOPRIGHT", -8, -(self.frame:GetHeight() - 26))
        if selling then -- while the stacks are still being sold, buying back would undo half of it
          undo:Hide()
          self.undo = undo
        end
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

-- replace == false: the answer adds stops to the marks of earlier answers; otherwise its marks replace them
-- quiet: only the map changes (the update of a route is a news line, not a card)
function UI.MarksCard(marks, replace, track, quiet)
  local placed, how
  if replace ~= false then placed, how = ns.Map.Replace(marks) else placed, how = ns.Map.Apply(marks) end
  if #placed == 0 or quiet then return end
  local lines = {}
  for _, p in ipairs(placed) do
    -- what to do there, in brackets after the stop: the card is what the player sees without opening the list
    local what = ns.Map.What(p)
    lines[#lines + 1] = string.format("• %s%s  |cff9aa3ad(%s %.1f, %.1f)|r", esc(p.title),
      what and (" |cffffd27a(" .. esc(what) .. ")|r") or "", esc(p.zone or ""), p.x, p.y)
  end
  local names = {}
  for _, t in ipairs(type(track) == "table" and track or {}) do
    local name = type(t) == "table" and t.name or t
    names[#names + 1] = esc(ns.Track and ns.Track.Describe(name) or name)
  end
  if #names > 0 then
    lines[#lines + 1] = "|cff9aa3adWatching for " .. table.concat(names, ", ") .. ": a living one in sight is marked on the screen and announced.|r"
  end
  -- ending the marks or the watch is on the strip above the text field: this card scrolls away
  card("|cff7fd6ffMarked on the map|r |cff6a7380(" .. how .. ")|r\n" .. table.concat(lines, "\n"), { 0.3, 0.6, 0.9, 0.7 }, {
    { "Show map", 96, function() ns.Map.Show(placed[1].mapID) end, "primary" },
  })
  relayout()
end

-- items the answer recommends from the auction search: one click shows them in Auctionator's Shopping tab
function UI.AuctionCard(items)
  local lines, names = {}, {}
  for i, it in ipairs(items) do
    lines[#lines + 1] = string.format("• %s  |cffffd100%s|r", esc(it.name or "?"), esc(it.price or ""))
    names[i] = it.name
  end
  card("|cffffd27aAuction house|r |cff6a7380(prices from the last Auctionator scan)|r\n" .. table.concat(lines, "\n"),
    { 0.95, 0.8, 0.3, 0.7 }, {
    { "Search on the AH", 130, function(self)
      local ok, err = ns.Auction.ShowInAuctionator(names)
      if ok then self:Done("|cff5dff9dShown in Auctionator (Shopping tab).|r") else UI.Notice(err, "warn") end
    end, "primary" },
    { "Dismiss", 80, function(self) self:Done("|cff8a93a0Dismissed.|r") end },
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
  elseif s.provider == "gemini" then
    pill:SetText("|cff8ab4f8Gemini|r  " .. esc((tostring((s.gemini or {}).model or "?"):gsub("^gemini%-", ""))))
  else
    local model = s.model and esc((s.model:gsub("^[^/]+/", ""))) or "OpenRouter"
    pill:SetText("|cff7fd6ffOpenRouter|r  " .. model)
  end
  local left = ns.Transport.InboxLeft()
  local parts = {}
  if (s.provider == nil or s.provider == "openrouter") and s.balance then
    local low = s.lowBalance and s.balance < s.lowBalance
    parts[#parts + 1] = string.format("%s$%.2f|r", low and "|cffff5555" or "|cff5dff9d", s.balance)
  elseif s.provider == "claude_code" then
    parts[#parts + 1] = "subscription"
  elseif s.provider == "gemini" then
    parts[#parts + 1] = "Gemini API"
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
-- The AI (provider, model, thinking level, web search, keys) is chosen in the ForeverAI app window: a change made
-- here had to go to the app as a screenshot and its reply used up an inbox. The game shows what the app reported.
local S = {}

local function aiLine(s)
  if s.provider == "claude_code" then
    local c = s.claude or {}
    return "Claude Code · " .. esc((tostring(c.model or "?"):gsub("^claude%-", ""):gsub("%-", " "))) ..
      " · thinking " .. esc(ns.EffortName(c.effort))
  elseif s.provider == "gemini" then
    return "Google Gemini · " .. esc(tostring((s.gemini or {}).model or "?"))
  elseif s.provider then
    return "OpenRouter · " .. esc(tostring(s.model or "?"))
  end
  return "|cffffc060not known yet (click Refresh)|r"
end

function UI.RefreshSettings()
  if not S.built then return end
  local s = ns.db.status or {}
  local search = ({ auto = "auto", always = "always", never = "off" })[s.searchMode or ""]
  S.aiInfo:SetText("In use: |cffe6e9ec" .. aiLine(s) .. "|r" .. (search and ("  ·  web search: " .. search) or ""))
  for _, b in ipairs(S.fonts) do b:SetActive(b.size == ns.db.fontSize) end
  S.Layout()
end

local function buildSettings(p)
  local sc = Scroll(p)
  sc:SetPoint("TOPLEFT", 12, -6)
  sc:SetPoint("BOTTOMRIGHT", -8, 6)
  local c = sc.child
  S.scroll = sc
  sc.stick = false -- a page of settings starts at its top (the chat sticks to its end)
  sc:HookScript("OnSizeChanged", function() if S.built and p:IsShown() then S.Layout() end end) -- lines rewrap
  local rows = {}
  local function head(text)
    local h = label(c, text, F.head)
    rows[#rows + 1] = { h, 20 }
    return h
  end
  local function row(frame, h)
    rows[#rows + 1] = { frame, h }
    return frame
  end

  head("AI")
  S.aiInfo = row(label(c, "", F.small), 18)
  row(label(c, "Provider, model, thinking level, web search and API keys are chosen in the ForeverAI app window " ..
    "on this computer.", F.small), 18)
  local aiTools = CreateFrame("Frame", nil, c)
  aiTools:SetSize(420, 26)
  local st = Btn(aiTools, "Refresh", 110, 22, function() ns.RequestStatus() end, nil,
    "Show the AI chosen in the app window now")
  st:SetPoint("LEFT")
  row(aiTools, 28)

  head("Data sent to the AI")
  local data = CreateFrame("Frame", nil, c)
  local opts = { { "bags", "Bags & bank" }, { "quests", "Quests" }, { "talents", "Talents" }, { "spells", "Spells" },
    { "skills", "Skills" }, { "professions", "Professions" }, { "progress", "Progress" }, { "reputations", "Reputation" },
    { "nearby", "Nearby players" }, { "windows", "Open windows" }, { "auras", "Auras" }, { "addons", "Add-ons" },
    { "chat", "Chat (private)" } }
  local checks = {}
  for i, o in ipairs(opts) do
    checks[i] = Check(data, o[2], function()
      if o[1] == "chat" then return ns.db.sections.chat == true end
      return ns.db.sections[o[1]] ~= false
    end, function(v) ns.db.sections[o[1]] = v end)
  end
  -- three per row in the default window; fewer when it is narrow or the font big, so no label is cut off
  row(data, math.ceil(#opts / 3) * 24)
  local grid = rows[#rows]
  local function layGrid(width)
    local need = 0
    for _, cb in ipairs(checks) do need = math.max(need, 34 + cb.label:GetStringWidth()) end
    local cols = width > 0 and math.max(1, math.min(3, math.floor(width / need))) or 3
    local colW = width > 0 and math.floor(width / cols) or 140
    for i, cb in ipairs(checks) do
      cb:ClearAllPoints()
      cb:SetPoint("TOPLEFT", data, "TOPLEFT", ((i - 1) % cols) * colW, -math.floor((i - 1) / cols) * 24)
    end
    grid[2] = math.ceil(#checks / cols) * 24
    data:SetSize(math.max(width, 10), grid[2])
  end

  head("Route and tracking")
  local routeRow = CreateFrame("Frame", nil, c)
  routeRow:SetSize(420, 26)
  local autoRoute = Check(routeRow, "New routes: update when my quests change", function() return ns.db.autoRoute == true end,
    function(v) ns.db.autoRoute = v end)
  autoRoute:SetPoint("LEFT")
  row(routeRow, 28)
  local alertRow = CreateFrame("Frame", nil, c)
  alertRow:SetSize(420, 26)
  local alerts = Check(alertRow, "Show on the screen when a route is updated", function() return ns.db.routeAlerts ~= false end,
    function(v) ns.db.routeAlerts = v end)
  alerts:SetPoint("LEFT")
  row(alertRow, 28)

  local farRow = CreateFrame("Frame", nil, c)
  farRow:SetSize(420, 26)
  local far = Check(farRow, "Find tracked creatures beyond nameplate range", function() return ns.db.lookByName ~= false end,
    function(v)
      ns.db.lookByName = v
      if ns.Track then ns.Track.byName = nil end -- checked again when it is turned on
    end)
  far:SetPoint("LEFT")
  row(farRow, 28)

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
  local rp = Btn(tools, "Center window", 120, 22, function()
    main:ClearAllPoints()
    main:SetPoint("CENTER")
    ns.db.window.point, ns.db.window.x, ns.db.window.y = "CENTER", 0, 0
  end)
  rp:SetPoint("LEFT")
  row(tools, 30)

  function S.Layout()
    local width = c:GetWidth()
    layGrid(width)
    local y = 0
    for _, r in ipairs(rows) do
      local f, h = r[1], r[2]
      f:ClearAllPoints()
      f:SetPoint("TOPLEFT", c, "TOPLEFT", 0, -y)
      if f:GetObjectType() == "FontString" then
        f:SetWidth(width - 10)
        h = math.max(h, f:GetStringHeight()) -- a long line wraps in a narrow window
      end
      y = y + h + 4
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

------------------------------------------------------------------------ status strip
-- What the add-on keeps doing after the answer has scrolled away: a creature it watches for, the marked route. One
-- chip each, above the text field, with a button that ends it. The strip slides open and closed.
local STRIP_ROW, STRIP_GAP, CHIP_ACTION_W, CHIP_SWITCH_W = 22, 4, 34, 22
local chips = {}

local function Chip(parent)
  local c = CreateFrame("Frame", nil, parent, "BackdropTemplate")
  c:SetHeight(STRIP_ROW)
  backdrop(c, { 1, 1, 1, 0.045 }, { 1, 1, 1, 0.09 })
  c.dot = c:CreateTexture(nil, "OVERLAY")
  c.dot:SetSize(6, 6)
  c.dot:SetPoint("LEFT", 8, 0)
  c.text = label(c, "", F.small)
  c.text:SetPoint("LEFT", c.dot, "RIGHT", 6, 0)
  c.text:SetJustifyV("MIDDLE")
  if c.text.SetWordWrap then c.text:SetWordWrap(false) end
  if c.text.SetMaxLines then c.text:SetMaxLines(1) end
  c.close = Btn(c, "x", 20, 20, function(self) if c.onClose then c.onClose() end end, "ghost")
  c.close:SetPoint("RIGHT", -1, 0)
  c.action = Btn(c, "", CHIP_ACTION_W, 20, function(self) if c.onAction then c.onAction() end end, "ghost")
  c.action:SetPoint("RIGHT", c.close, "LEFT", -2, 0)
  c.action:Hide()
  -- an on/off switch that shows its state (the route: kept up to date or not)
  c.switch = Btn(c, "", CHIP_SWITCH_W, 20, function(self) if c.onSwitch then c.onSwitch() end end, "ghost")
  c.switch.icon = c.switch:CreateTexture(nil, "OVERLAY")
  c.switch.icon:SetSize(14, 14)
  c.switch.icon:SetPoint("CENTER")
  c.switch.icon:SetTexture(ROUTE_AUTO_ICON)
  c.switch:Hide()
  return c
end

-- chips in a row, wrapping to a second row in a narrow window; returns the height they take
local function layoutChips(items, width)
  local x, row = 0, 0
  for i, it in ipairs(items) do
    local c = chips[i] or Chip(strip)
    chips[i] = c
    c.dot:SetColorTexture(unpack(it.color))
    c.text:SetWidth(0)
    c.text:SetText(it.text)
    c.close.tip, c.onClose = it.tip, it.close
    c.onAction = it.action and it.action.fn or nil
    if it.action then
      c.action:SetText(it.action.label)
      c.action.tip = it.action.tip
      c.action:Show()
    else
      c.action:Hide()
    end
    c.onSwitch = it.switch and it.switch.fn or nil
    if it.switch then
      c.switch.icon:SetVertexColor(unpack(it.switch.on and AUTO_ON or AUTO_OFF))
      c.switch.tip = it.switch.tip
      c.switch:ClearAllPoints()
      c.switch:SetPoint("RIGHT", it.action and c.action or c.close, "LEFT", -2, 0)
      c.switch:Show()
    else
      c.switch:Hide()
    end
    local fixed = 8 + 6 + 6 + 6 + 22 + (it.action and CHIP_ACTION_W + 2 or 0) + (it.switch and CHIP_SWITCH_W + 2 or 0)
    local textW = math.ceil(c.text.GetUnboundedStringWidth and c.text:GetUnboundedStringWidth() or c.text:GetStringWidth())
    local w = math.min(width, textW + fixed)
    c.text:SetWidth(w - fixed)
    if x > 0 and x + w > width then x, row = 0, row + 1 end
    c:ClearAllPoints()
    c:SetPoint("TOPLEFT", strip, "TOPLEFT", x, -row * (STRIP_ROW + STRIP_GAP))
    c:SetWidth(w)
    c:Show()
    x = x + w + 6
  end
  for i = #items + 1, #chips do chips[i]:Hide() end
  return #items > 0 and ((row + 1) * STRIP_ROW + row * STRIP_GAP + 6) or 0
end

local function slideStrip(target)
  strip.target = target
  if target > 0 then strip:Show() end
  if strip.sliding then return end
  strip.sliding = true
  strip:SetScript("OnUpdate", function(self, dt)
    local h = self.height or 0
    h = h + (self.target - h) * math.min(1, (dt or 0.016) * 16)
    if math.abs(self.target - h) < 0.5 then h = self.target end
    self.height = h
    self:SetHeight(math.max(h, 0.001))
    self:SetAlpha(self.full and self.full > 0 and math.min(1, h / self.full) or 1)
    if h == self.target then
      self.sliding = false
      self:SetScript("OnUpdate", nil)
      if h == 0 then self:Hide() end
      if chat and chat.stick then chat:ToBottom() end
    end
  end)
end

------------------------------------------------------------------------ the stops of the route
-- Opened from the route's chip: every stop in order with its distance, a button that takes it off the route and one
-- that makes it the next stop.
local routeList, routeOpen
local routeRows = {}
local ROUTE_ROW = 20

-- A row is a button: a click makes its stop the one to go to (the arrow leads there). The stop the arrow leads to is
-- lit up, with a bar at its left edge. The padlock keeps a stop on the route; x takes it off.
local function RouteRow(parent)
  local r = CreateFrame("Button", nil, parent)
  r:SetHeight(ROUTE_ROW)
  r.lit = r:CreateTexture(nil, "BACKGROUND")
  r.lit:SetAllPoints()
  r.lit:SetColorTexture(0.3, 0.6, 0.9, 0.2)
  r.bar = r:CreateTexture(nil, "BORDER")
  r.bar:SetPoint("TOPLEFT")
  r.bar:SetPoint("BOTTOMLEFT")
  r.bar:SetWidth(3)
  r.bar:SetColorTexture(0.5, 0.84, 1, 1)
  r.over = r:CreateTexture(nil, "BACKGROUND")
  r.over:SetAllPoints()
  r.over:SetColorTexture(1, 1, 1, 0.06)
  r.over:Hide()
  r:SetScript("OnEnter", function(self)
    self.over:Show()
    GameTooltip:SetOwner(self:GetParent(), "ANCHOR_TOP") -- above the list, so that it covers no stop
    GameTooltip:SetText(self.done and "Done. Click to head here again."
      or self.current and "You are heading here: the arrow leads to this stop."
      or "Click to head here: the arrow will lead to this stop.", 1, 1, 1, 1, true)
    if self.what then GameTooltip:AddLine(self.what, 1, 0.82, 0, true) end -- in full: the row cuts a long one short
    GameTooltip:AddLine(self.done and "It leaves the list when the route is updated; x takes it off now."
      or self.waits and ("Done when: " .. self.waits .. ".")
      or "Done when you reach it.", 0.7, 0.7, 0.7, true)
    GameTooltip:Show()
    ns.ApplyTooltipFont()
  end)
  r:SetScript("OnLeave", function(self)
    self.over:Hide()
    GameTooltip:Hide()
  end)
  r:SetScript("OnClick", function(self) ns.Map.SetCurrent(self.index) end)
  r.remove = Btn(r, "x", 20, 18, function() ns.Map.Remove(r.index, "player") end, "ghost", "Take this stop off the route")
  r.remove:SetPoint("RIGHT", -1, 0)
  r.lock = Btn(r, "", 20, 18, function() ns.Map.Lock(r.index, not r.locked) end, "ghost")
  r.lock:SetPoint("RIGHT", r.remove, "LEFT", -2, 0)
  r.lock.icon = r.lock:CreateTexture(nil, "OVERLAY")
  r.lock.icon:SetSize(13, 13)
  r.lock.icon:SetPoint("CENTER")
  r.lock.icon:SetTexture(LOCK_ICON)
  r.text = label(r, "", F.small)
  r.text:SetPoint("LEFT", 10, 0)
  r.text:SetPoint("RIGHT", r.lock, "LEFT", -6, 0)
  r.text:SetJustifyH("LEFT")
  r.text:SetJustifyV("MIDDLE")
  if r.text.SetWordWrap then r.text:SetWordWrap(false) end
  if r.text.SetMaxLines then r.text:SetMaxLines(1) end
  return r
end

local function updateRoute()
  if not routeList then return end
  local stops = routeOpen and ns.Map and ns.Map.List() or {}
  if #stops == 0 then routeOpen = false end -- the route is gone: the list closes with it
  for i, s in ipairs(stops) do
    local r = routeRows[i] or RouteRow(routeList)
    routeRows[i] = r
    r.index = i
    r:ClearAllPoints()
    r:SetPoint("TOPLEFT", routeList, "TOPLEFT", 0, -4 - (i - 1) * ROUTE_ROW)
    r:SetPoint("RIGHT", routeList, "RIGHT", 0, 0)
    local where = s.meters and (s.meters .. " m") or string.format("%s %.1f, %.1f", tostring(s.zone or ""), s.x, s.y)
    -- the stop the arrow leads to stands out
    r.current, r.locked, r.waits, r.done, r.what = s.current, s.locked, s.waits, s.done, s.what
    if s.done then -- done: greyed out, still on the list until the route is updated
      r.text:SetText(string.format("|cff6a7380%d. %s · done|r", i, esc(s.title)))
    else
      -- what to do there, in brackets: the list is read without the answer that planned it
      local what = s.what and ("(" .. esc(s.what) .. ") ") or ""
      r.text:SetText(string.format("|cff%s%d. %s|r |cff9aa3ad%s· %s|r", s.current and "ffffff" or "c3cad3", i, esc(s.title),
        what, where))
    end
    if s.current then r.lit:Show() r.bar:Show() else r.lit:Hide() r.bar:Hide() end
    r.lock.icon:SetVertexColor(unpack(s.locked and LOCK_ON or LOCK_OFF))
    r.lock.tip = s.locked and "Locked: this stop stays on the route, and the arrow on it, until you unlock it. Click to unlock."
      or "Lock this stop: it stays on the route when you reach it and when the route changes."
    r:Show()
  end
  for i = #stops + 1, #routeRows do routeRows[i]:Hide() end
  routeList:SetHeight(#stops > 0 and (#stops * ROUTE_ROW + 8) or 0.001)
  if #stops > 0 then routeList:Show() else routeList:Hide() end
end

function UI.UpdateStrip()
  if not strip then return end
  local items = {}
  for _, t in ipairs(ns.Track and ns.Track.State() or {}) do
    local seen = t.alive > 0 or t.near ~= nil
    items[#items + 1] = {
      color = seen and { 0.36, 1, 0.62, 1 } or { 0.85, 0.64, 0.26, 1 },
      -- how many are in sight, in brackets: "(0)", "(2)"; words only when the add-on cannot see
      text = string.format("|cff9aa3adTracking|r %s |cff%s%s|r", esc(t.label), seen and "5dff9d" or "d9a441",
        -- the add-on sees a creature only when it is targeted or pointed at: "after the fight" when the nameplates
        -- wait for the end of a fight (the game lets them be turned on only out of one), "nameplates off" when
        -- the player has turned them off
        -- "(near)": none in sight, but the game has one of that name in its range
        t.near and "(near)" or t.blind and (t.waiting and "· after the fight" or "· nameplates off")
          or string.format("(%d)", t.alive)),
      tip = "Stop tracking " .. t.label,
      close = function() ns.Track.Stop(t.name) end,
    }
  end
  local all = ns.Map and ns.Map.Count and ns.Map.Count() or 0
  if all > 0 then
    local stops = ns.Map.Left() -- still to do; the ones that are done stay on the list until the route is updated
    items[#items + 1] = {
      color = { 0.3, 0.6, 0.9, 1 },
      -- the stops in brackets, like the creatures in sight: "(3)", and "(1/3)" once some are done
      text = all > stops and string.format("|cff9aa3adRoute|r |cff7fd6ff(%d/%d)|r", all - stops, all)
        or string.format("|cff9aa3adRoute|r |cff7fd6ff(%d)|r", stops),
      tip = "Clear the marks on the map",
      close = function() ns.Map.Clear() end,
      action = {
        label = routeOpen and "hide" or "list",
        tip = routeOpen and "Hide the stops" or "Show the stops: click one to head to it, lock it or take it off",
        fn = function()
          routeOpen = not routeOpen
          UI.UpdateStrip()
        end,
      },
      -- only a route the AI planned can be kept up to date; a single mark has nothing to update
      switch = ns.char and ns.char.routePlanned and {
        on = ns.char.routeAuto == true,
        tip = ns.char.routeAuto and "Auto-update: on. Taking, finishing or dropping a quest asks the AI to update "
          .. "this route. Click to turn it off."
          or "Auto-update: off. The route stays as it is when your quests change. Click to have the AI update it.",
        fn = function() ns.Map.SetAuto(not ns.char.routeAuto) end,
      } or nil,
    }
  end
  updateRoute()
  local h = layoutChips(items, math.max(100, strip:GetWidth()))
  if h > 0 then strip.full = h end
  slideStrip(h)
end

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
  strip = CreateFrame("Frame", nil, pc)
  strip:SetPoint("BOTTOMLEFT", inputBar, "TOPLEFT", 0, 0)
  strip:SetPoint("BOTTOMRIGHT", inputBar, "TOPRIGHT", 0, 0)
  strip:SetHeight(0.001)
  strip:Hide()
  strip:SetScript("OnSizeChanged", function(self, w)
    if self.lastW ~= w then self.lastW = w C_Timer.After(0, UI.UpdateStrip) end -- chips wrap in a narrow window
  end)
  routeList = CreateFrame("Frame", nil, pc, "BackdropTemplate")
  routeList:SetPoint("BOTTOMLEFT", strip, "TOPLEFT", 0, 0)
  routeList:SetPoint("BOTTOMRIGHT", strip, "TOPRIGHT", 0, 0)
  routeList:SetHeight(0.001)
  backdrop(routeList, { 1, 1, 1, 0.03 }, { 1, 1, 1, 0.08 })
  routeList:Hide()
  do -- the distances follow the player while the list is open
    local since = 0
    routeList:SetScript("OnUpdate", function(_, dt)
      since = since + (dt or 0)
      if since >= 1 then since = 0 updateRoute() end
    end)
  end
  chat = Scroll(pc)
  chat:SetPoint("TOPLEFT", 10, -6)
  chat:SetPoint("BOTTOMRIGHT", routeList, "TOPRIGHT", 0, 6)
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
    UI.UpdateStrip()
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
