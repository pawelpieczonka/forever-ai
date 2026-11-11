-- addonsim: the parts of the World of Warcraft client that ForeverAI uses, emulated in Lua 5.1, so the add-on runs
-- outside the game: frames with the game's anchor layout, fonts measured by the host, scripts, events, timers, the
-- add-on list (load-on-demand add-ons are read from disk when loaded), saved variables in WTF, Screenshot() and the
-- chat frame. The game data the add-on reads (a made-up character) is in world.lua.
-- addonsim/sim.py runs this file and passes `host`, the Python side: text layout, zlib, base64, JSON decoding, files,
-- rendering. Errors in add-on code are caught and recorded like the game's error frame does (sim.errors).
local host = ...
local sim = { now = 0, frames = 0, errors = {}, warnings = {}, missing = {}, chat = {}, hovered = nil,
              modifiers = {}, reloadRequested = false, screenshots = 0 }

local floor, ceil, max, min, abs = math.floor, math.ceil, math.max, math.min, math.abs
local format = string.format
local traceback = debug.traceback
local osTime, osDate = os.time, os.date

------------------------------------------------------------------------ errors
local function report(msg, trace)
  local e = { msg = tostring(msg), trace = trace or "" }
  sim.errors[#sim.errors + 1] = e
  host.log("error", e.msg .. "\n" .. e.trace)
end

-- runs add-on code the way the game does: an error is reported and the game goes on
local function call(fn, ...)
  local args, n = { ... }, select("#", ...)
  local res = { xpcall(function() return fn(unpack(args, 1, n)) end, function(err) return { err, traceback("", 2) } end) }
  if not res[1] then
    report(res[2][1], res[2][2])
    return false
  end
  return unpack(res, 1, table.maxn(res))
end
sim.call = call

function sim.warn(msg)
  if not sim.warnings[msg] then
    sim.warnings[msg] = true
    host.log("warning", msg)
  end
end

------------------------------------------------------------------------ the game's Lua additions
function debugprofilestop() return host.wall() * 1000 end
function GetTime() return sim.now end
function GetTimePreciseSec() return sim.now end
function time(t) if t then return osTime(t) end return floor(host.epoch + sim.now) end
function date(fmt, t) return osDate(fmt, t or time()) end
function GetServerTime() return time() end

for name, fn in pairs({
  strsub = string.sub, strlen = string.len, strlower = string.lower, strupper = string.upper, strfind = string.find,
  strmatch = string.match, strrep = string.rep, strbyte = string.byte, strchar = string.char, strrev = string.reverse,
  gsub = string.gsub, gmatch = string.gmatch, format = string.format, tinsert = table.insert, tremove = table.remove,
  sort = table.sort, getn = table.getn, floor = math.floor, ceil = math.ceil, abs = math.abs, sqrt = math.sqrt,
  mod = math.fmod, random = math.random, max = math.max, min = math.min, exp = math.exp, log = math.log,
}) do _G[name] = fn end

function wipe(t) for k in pairs(t) do t[k] = nil end return t end
table.wipe = wipe

function strsplit(delim, s, pieces)
  s = tostring(s)
  local set = "[" .. delim:gsub("[%]%^%-%%]", "%%%0") .. "]"
  local out, start = {}, 1
  while not (pieces and #out == pieces - 1) do
    local a, b = s:find(set, start)
    if not a then break end
    out[#out + 1] = s:sub(start, a - 1)
    start = b + 1
  end
  out[#out + 1] = s:sub(start)
  return unpack(out)
end
string.split = function(s, delim, pieces) return strsplit(delim, s, pieces) end

function strjoin(delim, ...) return table.concat({ ... }, delim) end
function strtrim(s, chars)
  chars = chars and ("[" .. chars:gsub("[%]%^%-%%]", "%%%0") .. "]") or "%s"
  return (s:gsub("^" .. chars .. "+", ""):gsub(chars .. "+$", ""))
end
string.trim = strtrim

function tostringall(...)
  local out = {}
  for i = 1, select("#", ...) do out[i] = tostring((select(i, ...))) end
  return unpack(out, 1, select("#", ...))
end

function tContains(t, v) for _, x in pairs(t) do if x == v then return true end end return false end

function CopyTable(t)
  local out = {}
  for k, v in pairs(t) do out[k] = type(v) == "table" and CopyTable(v) or v end
  return out
end

function Mixin(obj, ...)
  for i = 1, select("#", ...) do
    for k, v in pairs((select(i, ...))) do obj[k] = v end
  end
  return obj
end
function CreateFromMixins(...) return Mixin({}, ...) end

-- bit (LuaBitOp in the game): 32-bit results, signed like the game's
local function bits(f)
  local function op(a, b)
    a, b = (tonumber(a) or 0) % 4294967296, (tonumber(b) or 0) % 4294967296
    local r, p = 0, 1
    for _ = 1, 32 do
      local x, y = a % 2, b % 2
      if f(x, y) then r = r + p end
      a, b, p = (a - x) / 2, (b - y) / 2, p * 2
    end
    return r
  end
  return function(a, b, ...)
    local r = op(a, b)
    for i = 1, select("#", ...) do r = op(r, (select(i, ...))) end
    if r >= 2147483648 then r = r - 4294967296 end
    return r
  end
end
bit = {
  band = bits(function(x, y) return x == 1 and y == 1 end),
  bor = bits(function(x, y) return x == 1 or y == 1 end),
  bxor = bits(function(x, y) return x ~= y end),
  bnot = function(a) local r = -1 - (tonumber(a) or 0) return ((r + 2147483648) % 4294967296) - 2147483648 end,
  lshift = function(a, n) return bit.band((tonumber(a) or 0) * 2 ^ n, 4294967295) end,
  rshift = function(a, n) return floor(((tonumber(a) or 0) % 4294967296) / 2 ^ n) end,
}

------------------------------------------------------------------------ widgets
-- Every widget is a Lua table (the add-on stores its own fields on it); the emulator's state for it is in S[widget].
local S = {}
local widgets = {} -- every widget in creation order
local frames = {}  -- the frames among them (EnumerateFrames, drawing)
sim.S, sim.widgets = S, widgets

local gen = 0 -- layout generation: every change that can move or resize something invalidates the cached rects
local function dirty() gen = gen + 1 end

local function class(base)
  local c = {}
  c.__index = c
  if base then setmetatable(c, { __index = base }) end
  return c
end

local Object = class()
local Region = class(Object)
local Texture = class(Region)
local FontString = class(Region)
local Frame = class(Region)
local Button = class(Frame)
local CheckButton = class(Button)
local EditBox = class(Frame)
local ScrollFrame = class(Frame)
local Tooltip = class(Frame)
local Font = class(Object)
local AnimationGroup = class(Object)
local Animation = class(Object)
local MessageFrame = class(Frame)

local KIND = { Frame = Frame, Button = Button, CheckButton = CheckButton, EditBox = EditBox, ScrollFrame = ScrollFrame,
               GameTooltip = Tooltip, ScrollingMessageFrame = MessageFrame, MessageFrame = MessageFrame,
               StatusBar = Frame, Slider = Frame, Cooldown = Frame, Model = Frame, PlayerModel = Frame,
               SimpleHTML = Frame, ColorSelect = Frame, Minimap = Frame }

local function st(w)
  local s = S[w]
  if not s then error("not a widget: " .. tostring(w), 3) end
  return s
end

local function register(w, s, name)
  S[w] = s
  s.order = #widgets + 1
  widgets[s.order] = w
  if name then
    s.name = name
    _G[name] = w
  end
  return w
end

local function newState(kind, parent)
  return { kind = kind, parent = parent, shown = true, alpha = 1, points = {}, pointOrder = {}, children = {},
           regions = {} }
end

------------------------------------------------------------------------ Object
function Object:GetName() return S[self] and S[self].name end
function Object:GetDebugName() return S[self] and S[self].name or tostring(self) end
function Object:GetObjectType() return st(self).kind end
function Object:IsObjectType(t)
  local k = st(self).kind
  if k == t then return true end
  local family = { Button = { "Frame" }, CheckButton = { "Button", "Frame" }, EditBox = { "Frame" },
                   ScrollFrame = { "Frame" }, GameTooltip = { "Frame" }, Texture = { "Region" },
                   FontString = { "Region" }, Frame = { "Region" } }
  for _, f in ipairs(family[k] or {}) do if f == t then return true end end
  return t == "Object" or t == "ScriptObject"
end
function Object:IsForbidden() return false end
function Object:GetParent() return S[self] and S[self].parent end

------------------------------------------------------------------------ fonts
local fonts = {} -- name -> Font

local function fontState(path, size, flags)
  return { fontPath = path, fontSize = size, fontFlags = flags or "" }
end

-- the font of a FontString / EditBox / Font: its own, else the font object's (followed when that changes later)
local function fontOf(s)
  local guard = 0
  while s and guard < 10 do
    if s.fontPath then return s.fontPath, s.fontSize, s.fontFlags end
    s = s.fontObject and S[s.fontObject]
    guard = guard + 1
  end
  return "Fonts\\FRIZQT__.TTF", 12, ""
end

local function fieldOf(s, key, default)
  local guard = 0
  while s and guard < 10 do
    if s[key] ~= nil then return s[key] end
    s = s.fontObject and S[s.fontObject]
    guard = guard + 1
  end
  return default
end

local function fontObject(obj)
  if type(obj) == "string" then obj = _G[obj] end
  if obj and S[obj] then return obj end
  return nil
end

-- the methods every text widget and Font share
local function fontMethods(c)
  function c:SetFont(path, size, flags)
    local s = st(self)
    s.fontPath, s.fontSize, s.fontFlags = tostring(path), tonumber(size) or 12, flags or ""
    dirty()
    return true
  end
  function c:GetFont() return fontOf(st(self)) end
  function c:SetFontObject(obj)
    local s = st(self)
    s.fontObject = fontObject(obj)
    s.fontPath, s.fontSize, s.fontFlags = nil, nil, nil
    dirty()
  end
  function c:GetFontObject() return st(self).fontObject end
  function c:SetTextColor(r, g, b, a)
    st(self).color = { tonumber(r) or 1, tonumber(g) or 1, tonumber(b) or 1, tonumber(a) or 1 }
  end
  function c:GetTextColor()
    local col = fieldOf(st(self), "color", { 1, 1, 1, 1 })
    return col[1], col[2], col[3], col[4]
  end
  function c:SetJustifyH(j) st(self).justifyH = tostring(j):upper() dirty() end
  function c:GetJustifyH() return fieldOf(st(self), "justifyH", "CENTER") end
  function c:SetJustifyV(j) st(self).justifyV = tostring(j):upper() dirty() end
  function c:GetJustifyV() return fieldOf(st(self), "justifyV", "MIDDLE") end
  function c:SetShadowOffset(x, y) st(self).shadowOffset = { tonumber(x) or 0, tonumber(y) or 0 } end
  function c:GetShadowOffset()
    local o = fieldOf(st(self), "shadowOffset", { 0, 0 })
    return o[1], o[2]
  end
  function c:SetShadowColor(r, g, b, a) st(self).shadowColor = { r or 0, g or 0, b or 0, a or 1 } end
  function c:GetShadowColor()
    local col = fieldOf(st(self), "shadowColor", { 0, 0, 0, 1 })
    return col[1], col[2], col[3], col[4]
  end
  function c:SetSpacing(v) st(self).spacing = tonumber(v) or 0 dirty() end
  function c:GetSpacing() return fieldOf(st(self), "spacing", 0) end
  function c:SetIndentedWordWrap(on) st(self).indented = on and true or false end
end
fontMethods(Font)
fontMethods(FontString)
fontMethods(EditBox)

function Font:CopyFontObject(src)
  local s, o = st(self), S[fontObject(src) or {}]
  if not o then return end
  s.fontPath, s.fontSize, s.fontFlags = fontOf(o)
  s.color = fieldOf(o, "color", nil)
  s.shadowOffset, s.shadowColor = fieldOf(o, "shadowOffset", nil), fieldOf(o, "shadowColor", nil)
  dirty()
end
Font.SetFontObject = function(self, src) self:CopyFontObject(src) end

function CreateFont(name)
  if name and fonts[name] then return fonts[name] end
  local f = register(setmetatable({}, Font), { kind = "Font" }, name)
  if name then fonts[name] = f end
  return f
end

------------------------------------------------------------------------ scale and layout
local SCREEN_W, SCREEN_H = host.screen_w, host.screen_h -- physical pixels
local BASE = SCREEN_H / 768                             -- pixels per UI unit at scale 1
local SCREEN = { l = 0, b = 0, r = SCREEN_W, t = SCREEN_H }
sim.SCREEN_W, sim.SCREEN_H = SCREEN_W, SCREEN_H

local function effScale(w)
  local s = S[w]
  if not s then return 1 end
  if s.scaleGen == gen then return s.effScale end
  local v
  if s.isRegion then
    v = s.parent and effScale(s.parent) or 1
  else
    v = (s.scale or 1) * (s.parent and effScale(s.parent) or 1)
  end
  s.scaleGen, s.effScale = gen, v
  return v
end
local function ppu(w) return effScale(w) * BASE end -- pixels per unit of this widget

local measureCache = {}
-- text size in pixels: width, height, lines, truncated
local function measure(s, w, text, widthPx, wrap)
  local path, size, flags = fontOf(s)
  local px = ppu(w)
  local sizePx = size * px
  local spacingPx = fieldOf(s, "spacing", 0) * px
  local maxLines = s.maxLines or 0
  local key = table.concat({ text, path, sizePx, flags, widthPx, tostring(wrap), spacingPx, maxLines }, "\0")
  local m = measureCache[key]
  if not m then
    local tw, th, lines, trunc = host.text_size(text, path, sizePx, flags, widthPx, wrap and 1 or 0, spacingPx, maxLines)
    m = { tw, th, lines, trunc == 1 or trunc == true }
    measureCache[key] = m
  end
  return m[1], m[2], m[3], m[4]
end

local rectOf

local function horizontal(point)
  if point:find("LEFT") then return "l" elseif point:find("RIGHT") then return "r" end
  return "cx"
end
local function vertical(point)
  if point:find("TOP") then return "t" elseif point:find("BOTTOM") then return "b" end
  return "cy"
end

local function anchorXY(r, point)
  local h, v = horizontal(point), vertical(point)
  local x = h == "l" and r.l or h == "r" and r.r or (r.l + r.r) / 2
  local y = v == "t" and r.t or v == "b" and r.b or (r.b + r.t) / 2
  return x, y
end

-- the width a FontString wraps at, in pixels (0: one line, as wide as the text)
local function wrapWidthPx(w, s, l, r)
  if l and r then return r - l end
  if s.width and s.width > 0 then return s.width * ppu(w) end
  return 0
end

local function computeRect(w, s)
  local px = ppu(w)
  if s.isScreen then return SCREEN end
  if s.scrollChildOf then
    local sf = s.scrollChildOf
    local pr = rectOf(sf)
    if not pr then return nil end
    local ss, sp = S[sf], ppu(sf)
    local wpx, hpx = (s.width or 0) * px, (s.height or 0) * px
    local l = pr.l - (ss.hscroll or 0) * sp
    local t = pr.t + (ss.vscroll or 0) * sp
    return { l = l, r = l + wpx, t = t, b = t - hpx }
  end
  local e = {}
  local any = false
  for _, name in ipairs(s.pointOrder) do
    local p = s.points[name]
    local rel = p.rel
    local rr
    if rel == nil then
      rr = s.parent and rectOf(s.parent) or SCREEN
    else
      rr = rectOf(rel)
    end
    if rr then
      local ax, ay = anchorXY(rr, p.relPoint)
      e[horizontal(name)] = ax + p.x * px
      e[vertical(name)] = ay + p.y * px
      any = true
    end
  end
  if not any then return nil end
  local width
  if e.l and e.r then
    width = e.r - e.l
  elseif s.width and s.width > 0 then
    width = s.width * px
  elseif s.kind == "FontString" then
    width = (measure(s, w, s.text or "", 0, false))
  else
    width = 0
  end
  local height
  if e.t and e.b then
    height = e.t - e.b
  elseif s.height and s.height > 0 then
    height = s.height * px
  elseif s.kind == "FontString" then
    local ww = (e.l and e.r) and (e.r - e.l) or ((s.width and s.width > 0) and s.width * px or 0)
    local _, th = measure(s, w, s.text or "", ww, ww > 0 and s.wordWrap ~= false)
    height = th
  else
    height = 0
  end
  local l = e.l or (e.r and e.r - width) or (e.cx and e.cx - width / 2)
  local b = e.b or (e.t and e.t - height) or (e.cy and e.cy - height / 2)
  if not (l and b) then return nil end
  local rect = { l = l, r = l + width, b = b, t = b + height }
  if s.clamped then -- kept on the screen, like the game does
    local dx = rect.l < 0 and -rect.l or (rect.r > SCREEN_W and SCREEN_W - rect.r or 0)
    local dy = rect.b < 0 and -rect.b or (rect.t > SCREEN_H and SCREEN_H - rect.t or 0)
    rect.l, rect.r, rect.b, rect.t = rect.l + dx, rect.r + dx, rect.b + dy, rect.t + dy
  end
  return rect
end

function rectOf(w)
  local s = S[w]
  if not s then return nil end
  if s.rectGen == gen then return s.rect end
  if s.computing == gen then return nil end -- an anchor loop: the game refuses those
  s.computing = gen
  local r = computeRect(w, s)
  s.computing = nil
  s.rectGen, s.rect = gen, r
  return r
end
sim.rectOf = rectOf

------------------------------------------------------------------------ Region: points, size, visibility
local function relative(self, rel)
  if type(rel) == "string" then rel = _G[rel] end
  if rel ~= nil and not S[rel] then error("SetPoint: unknown relative frame " .. tostring(rel), 3) end
  return rel
end

local POINTS = { TOPLEFT = true, TOP = true, TOPRIGHT = true, LEFT = true, CENTER = true, RIGHT = true,
                 BOTTOMLEFT = true, BOTTOM = true, BOTTOMRIGHT = true }

function Region:SetPoint(point, a, b, c, d)
  local s = st(self)
  point = tostring(point):upper()
  if not POINTS[point] then error("SetPoint: unknown point " .. tostring(point), 2) end
  local rel, relPoint, x, y
  if type(a) == "number" then
    rel, relPoint, x, y = nil, point, a, b
  elseif a == nil and b == nil then
    rel, relPoint, x, y = nil, point, 0, 0
  else
    rel = relative(self, a)
    if type(b) == "string" then
      relPoint, x, y = b:upper(), c, d
    else
      relPoint, x, y = point, b, c
    end
  end
  if rel == self then error("SetPoint: a region cannot be anchored to itself", 2) end
  if not s.points[point] then s.pointOrder[#s.pointOrder + 1] = point end
  s.points[point] = { rel = rel, relPoint = relPoint, x = tonumber(x) or 0, y = tonumber(y) or 0 }
  dirty()
end

function Region:SetAllPoints(rel)
  rel = relative(self, rel)
  self:SetPoint("TOPLEFT", rel, "TOPLEFT", 0, 0)
  self:SetPoint("BOTTOMRIGHT", rel, "BOTTOMRIGHT", 0, 0)
end

function Region:ClearAllPoints()
  local s = st(self)
  s.points, s.pointOrder = {}, {}
  dirty()
end

function Region:ClearPoint(point)
  local s = st(self)
  point = tostring(point):upper()
  if s.points[point] then
    s.points[point] = nil
    for i, p in ipairs(s.pointOrder) do if p == point then table.remove(s.pointOrder, i) break end end
    dirty()
  end
end

function Region:GetNumPoints() return #st(self).pointOrder end

function Region:GetPoint(i)
  local s = st(self)
  local name = s.pointOrder[i or 1]
  if not name then return nil end
  local p = s.points[name]
  return name, p.rel or s.parent, p.relPoint, p.x, p.y
end

function Region:SetSize(w, h)
  local s = st(self)
  s.width, s.height = tonumber(w) or 0, tonumber(h or w) or 0
  dirty()
end
function Region:SetWidth(w) st(self).width = tonumber(w) or 0 dirty() end
function Region:SetHeight(h) st(self).height = tonumber(h) or 0 dirty() end

function Region:GetWidth()
  local s = st(self)
  local r = rectOf(self)
  if r then return (r.r - r.l) / ppu(self) end
  if s.width and s.width > 0 then return s.width end
  if s.kind == "FontString" then return (measure(s, self, s.text or "", 0, false)) / ppu(self) end
  return 0
end
function Region:GetHeight()
  local s = st(self)
  local r = rectOf(self)
  if r then return (r.t - r.b) / ppu(self) end
  if s.height and s.height > 0 then return s.height end
  return 0
end
function Region:GetSize() return self:GetWidth(), self:GetHeight() end

function Region:GetRect()
  local r = rectOf(self)
  if not r then return nil end
  local px = ppu(self)
  return r.l / px, r.b / px, (r.r - r.l) / px, (r.t - r.b) / px
end
function Region:GetLeft() local r = rectOf(self) return r and r.l / ppu(self) end
function Region:GetRight() local r = rectOf(self) return r and r.r / ppu(self) end
function Region:GetTop() local r = rectOf(self) return r and r.t / ppu(self) end
function Region:GetBottom() local r = rectOf(self) return r and r.b / ppu(self) end
function Region:GetCenter()
  local r = rectOf(self)
  if not r then return nil end
  local px = ppu(self)
  return (r.l + r.r) / 2 / px, (r.b + r.t) / 2 / px
end
function Region:IsRectValid() return rectOf(self) ~= nil end
function Region:GetEffectiveScale() return effScale(self) end
function Region:GetScale() local s = st(self) return s.isRegion and 1 or (s.scale or 1) end
function Region:IsAnchoringRestricted() return false end
function Region:IsMouseOver() return sim.hovered == self end

local function isVisible(w)
  local guard = 0
  while w and guard < 100 do
    local s = S[w]
    if not s or not s.shown then return false end
    w = s.parent
    guard = guard + 1
  end
  return true
end
sim.isVisible = isVisible

local function runScript(w, name, ...)
  local s = S[w]
  local fn = s and s.scripts and s.scripts[name]
  if fn then return call(fn, w, ...) end
end
sim.runScript = runScript

-- OnShow / OnHide for a frame whose visibility changed and for its shown descendants
local function visibilityChanged(w, script)
  local s = S[w]
  if s.isRegion then return end
  runScript(w, script)
  for _, child in ipairs(s.children) do
    if S[child].shown then visibilityChanged(child, script) end
  end
end

function Region:Show()
  local s = st(self)
  if s.shown then return end
  s.shown = true
  dirty()
  if isVisible(self) then visibilityChanged(self, "OnShow") end
end
function Region:Hide()
  local s = st(self)
  if not s.shown then return end
  local was = isVisible(self)
  s.shown = false
  dirty()
  if was then visibilityChanged(self, "OnHide") end
end
function Region:SetShown(on) if on then self:Show() else self:Hide() end end
function Region:IsShown() return st(self).shown end
function Region:IsVisible() return isVisible(self) end

function Region:SetAlpha(a) st(self).alpha = max(0, min(1, tonumber(a) or 1)) end
function Region:GetAlpha() return st(self).alpha end
local function effAlpha(w)
  local a, guard = 1, 0
  while w and guard < 100 do
    a = a * (S[w].alpha or 1)
    w = S[w].parent
    guard = guard + 1
  end
  return a
end
function Region:GetEffectiveAlpha() return effAlpha(self) end

local LAYERS = { BACKGROUND = 1, BORDER = 2, ARTWORK = 3, OVERLAY = 4, HIGHLIGHT = 5 }
function Region:SetDrawLayer(layer, sub)
  local s = st(self)
  s.layer = LAYERS[tostring(layer):upper()] and tostring(layer):upper() or "ARTWORK"
  s.sublevel = tonumber(sub) or 0
end
function Region:GetDrawLayer() local s = st(self) return s.layer, s.sublevel or 0 end

local function detach(w)
  local s = S[w]
  local p = s.parent and S[s.parent]
  if not p then return end
  local list = s.isRegion and p.regions or p.children
  for i, c in ipairs(list) do if c == w then table.remove(list, i) break end end
end

function Region:SetParent(parent)
  local s = st(self)
  if type(parent) == "string" then parent = _G[parent] end
  if parent == s.parent then return end
  local was = isVisible(self)
  detach(self)
  s.parent = parent
  if parent then
    local p = st(parent)
    local list = s.isRegion and p.regions or p.children
    list[#list + 1] = self
  end
  dirty()
  local now = isVisible(self)
  if was and not now then visibilityChanged(self, "OnHide") elseif now and not was then visibilityChanged(self, "OnShow") end
end

------------------------------------------------------------------------ animations (state only: nothing moves)
function Region:CreateAnimationGroup(name)
  local g = register(setmetatable({}, AnimationGroup), { kind = "AnimationGroup", parent = self, anims = {} }, name)
  return g
end
function AnimationGroup:CreateAnimation(kind, name)
  local s = st(self)
  local a = register(setmetatable({}, Animation), { kind = kind or "Animation", parent = self }, name)
  s.anims[#s.anims + 1] = a
  return a
end
function AnimationGroup:Play() st(self).playing = true end
function AnimationGroup:Stop() st(self).playing = false end
function AnimationGroup:Pause() st(self).playing = false end
function AnimationGroup:Finish() st(self).playing = false end
function AnimationGroup:IsPlaying() return st(self).playing == true end
function AnimationGroup:SetLooping(v) st(self).looping = v end
function AnimationGroup:GetLooping() return st(self).looping or "NONE" end
function AnimationGroup:SetScript(k, fn) st(self).scripts = st(self).scripts or {} st(self).scripts[k] = fn end
function AnimationGroup:SetToFinalAlpha() end
for _, m in ipairs({ "SetFromAlpha", "SetToAlpha", "SetDuration", "SetOrder", "SetStartDelay", "SetEndDelay",
                     "SetSmoothing", "SetOffset", "SetScale", "SetScaleFrom", "SetScaleTo", "SetChange",
                     "SetDegrees", "SetOrigin", "SetTarget", "SetChildKey" }) do
  Animation[m] = function(self, ...) st(self)[m] = { ... } end
end
function Animation:GetDuration() local d = st(self).SetDuration return d and d[1] or 0 end
function Animation:SetScript(k, fn) st(self).scripts = st(self).scripts or {} st(self).scripts[k] = fn end

------------------------------------------------------------------------ Texture
local function newRegion(class_, kind, parent, name, layer, sublevel)
  local s = newState(kind, parent)
  s.isRegion = true
  s.layer = LAYERS[tostring(layer or ""):upper()] and tostring(layer):upper() or "ARTWORK"
  s.sublevel = tonumber(sublevel) or 0
  local w = register(setmetatable({}, class_), s, name)
  local ps = st(parent)
  ps.regions[#ps.regions + 1] = w
  dirty()
  return w, s
end

function Frame:CreateTexture(name, layer, template, sublevel)
  local t, s = newRegion(Texture, "Texture", self, name, layer, sublevel)
  s.vertex = { 1, 1, 1, 1 }
  return t
end

function Texture:SetTexture(path)
  local s = st(self)
  if type(path) == "number" then path = "fileID:" .. path end
  s.texture, s.color, s.atlas = path and tostring(path) or nil, nil, nil
  return true
end
function Texture:GetTexture() local s = st(self) return s.texture end
function Texture:SetColorTexture(r, g, b, a)
  local s = st(self)
  s.color = { tonumber(r) or 0, tonumber(g) or 0, tonumber(b) or 0, tonumber(a) or 1 }
  s.texture, s.atlas = nil, nil
end
function Texture:SetAtlas(atlas, useSize)
  local s = st(self)
  s.atlas, s.texture, s.color = tostring(atlas), nil, nil
end
function Texture:GetAtlas() return st(self).atlas end
function Texture:SetVertexColor(r, g, b, a)
  st(self).vertex = { tonumber(r) or 1, tonumber(g) or 1, tonumber(b) or 1, tonumber(a) or 1 }
end
function Texture:GetVertexColor() local v = st(self).vertex return v[1], v[2], v[3], v[4] end
function Texture:SetTexCoord(...) st(self).texCoord = { ... } end
function Texture:GetTexCoord() return unpack(st(self).texCoord or { 0, 1, 0, 1 }) end
function Texture:SetDesaturated(on) st(self).desaturated = on and true or false end
function Texture:SetBlendMode(m) st(self).blend = m end
function Texture:SetRotation(r) st(self).rotation = r end
function Texture:SetGradient() end
function Texture:SetSnapToPixelGrid() end
function Texture:SetTexelSnappingBias() end
function Texture:SetHorizTile() end
function Texture:SetVertTile() end
function Texture:SetMask() end

------------------------------------------------------------------------ FontString
function Frame:CreateFontString(name, layer, template)
  local fs, s = newRegion(FontString, "FontString", self, name, layer or "ARTWORK")
  s.text = ""
  if template then s.fontObject = fontObject(template) end
  return fs
end

local function textOf(v)
  if v == nil then return "" end
  return tostring(v)
end

function FontString:SetText(text)
  local s = st(self)
  s.text = textOf(text)
  dirty()
end
function FontString:SetFormattedText(fmt, ...) self:SetText(format(fmt, ...)) end
function FontString:GetText() local t = st(self).text return t ~= "" and t or nil end
function FontString:SetWordWrap(on) st(self).wordWrap = on and true or false dirty() end
function FontString:CanWordWrap() return st(self).wordWrap ~= false end
function FontString:SetNonSpaceWrap(on) st(self).nonSpaceWrap = on and true or false end
function FontString:CanNonSpaceWrap() return st(self).nonSpaceWrap == true end
function FontString:SetMaxLines(n) st(self).maxLines = tonumber(n) or 0 dirty() end
function FontString:GetMaxLines() return st(self).maxLines or 0 end
function FontString:SetTextHeight(h) local s = st(self) s.fontPath, s.fontSize, s.fontFlags = fontOf(s) s.fontSize = h dirty() end
function FontString:SetVertexColor(r, g, b, a) self:SetTextColor(r, g, b, a) end

local function fsWrapWidth(self, s)
  local r = rectOf(self)
  if r and #s.pointOrder >= 2 then
    local left = s.points.LEFT or s.points.TOPLEFT or s.points.BOTTOMLEFT
    local right = s.points.RIGHT or s.points.TOPRIGHT or s.points.BOTTOMRIGHT
    if left and right then return r.r - r.l end
  end
  return wrapWidthPx(self, s)
end
sim.fsWrapWidth = function(w) return fsWrapWidth(w, S[w]) end

function FontString:GetUnboundedStringWidth()
  local s = st(self)
  return (measure(s, self, s.text, 0, false)) / ppu(self)
end
function FontString:GetStringWidth()
  local s = st(self)
  local ww = fsWrapWidth(self, s)
  local tw = measure(s, self, s.text, ww, ww > 0 and s.wordWrap ~= false)
  return tw / ppu(self)
end
function FontString:GetStringHeight()
  local s = st(self)
  local ww = fsWrapWidth(self, s)
  local _, th = measure(s, self, s.text, ww, ww > 0 and s.wordWrap ~= false)
  return th / ppu(self)
end
function FontString:GetNumLines()
  local s = st(self)
  local ww = fsWrapWidth(self, s)
  local _, _, lines = measure(s, self, s.text, ww, ww > 0 and s.wordWrap ~= false)
  return lines
end
function FontString:IsTruncated()
  local s = st(self)
  local ww = fsWrapWidth(self, s)
  local _, _, _, trunc = measure(s, self, s.text, ww, ww > 0 and s.wordWrap ~= false)
  return trunc
end

------------------------------------------------------------------------ Frame
local STRATA = { "WORLD", "BACKGROUND", "LOW", "MEDIUM", "HIGH", "DIALOG", "FULLSCREEN", "FULLSCREEN_DIALOG",
                 "TOOLTIP" }
local STRATA_INDEX = {}
for i, name in ipairs(STRATA) do STRATA_INDEX[name] = i end

local events = {}   -- event -> list of frames
local updaters = {} -- frames with an OnUpdate script

local function addUpdater(f)
  for _, x in ipairs(updaters) do if x == f then return end end
  updaters[#updaters + 1] = f
end
local function removeUpdater(f)
  for i, x in ipairs(updaters) do if x == f then table.remove(updaters, i) return end end
end

function Frame:SetScript(name, fn)
  local s = st(self)
  s.scripts = s.scripts or {}
  s.scripts[name] = fn
  if name == "OnUpdate" then if fn then addUpdater(self) else removeUpdater(self) end end
end
function Frame:GetScript(name) local s = st(self) return s.scripts and s.scripts[name] end
function Frame:HasScript(name) return true end
function Frame:HookScript(name, fn)
  local old = self:GetScript(name)
  self:SetScript(name, old and function(...) old(...) fn(...) end or fn)
end

function Frame:RegisterEvent(event)
  local s = st(self)
  s.events = s.events or {}
  if s.events[event] then return true end
  s.events[event] = true
  events[event] = events[event] or {}
  table.insert(events[event], self)
  return true
end
Frame.RegisterUnitEvent = Frame.RegisterEvent
function Frame:UnregisterEvent(event)
  local s = st(self)
  if not (s.events and s.events[event]) then return end
  s.events[event] = nil
  for i, f in ipairs(events[event] or {}) do if f == self then table.remove(events[event], i) break end end
end
function Frame:UnregisterAllEvents()
  for event in pairs(st(self).events or {}) do self:UnregisterEvent(event) end
end
function Frame:IsEventRegistered(event) local s = st(self) return s.events and s.events[event] == true or false end
function Frame:RegisterAllEvents() end

function sim.fire(event, ...)
  local list = events[event]
  if not list then return end
  local copy = {}
  for i, f in ipairs(list) do copy[i] = f end
  for _, f in ipairs(copy) do runScript(f, "OnEvent", event, ...) end
end

function Frame:SetFrameStrata(strata)
  strata = tostring(strata):upper()
  if STRATA_INDEX[strata] then st(self).strata = strata end
end
local function strataOf(w)
  local guard = 0
  while w and guard < 100 do
    local s = S[w]
    if s.strata then return s.strata end
    w = s.parent
    guard = guard + 1
  end
  return "MEDIUM"
end
function Frame:GetFrameStrata() return strataOf(self) end
local function levelOf(w)
  local s = S[w]
  if s.level then return s.level end
  if s.parent and not S[s.parent].isRegion then return levelOf(s.parent) + 1 end
  return 0
end
function Frame:SetFrameLevel(l) st(self).level = tonumber(l) or 0 end
function Frame:GetFrameLevel() return levelOf(self) end
function Frame:Raise() end
function Frame:Lower() end
function Frame:SetToplevel() end
function Frame:SetFixedFrameStrata() end
function Frame:SetFixedFrameLevel() end

function Frame:SetScale(v) st(self).scale = tonumber(v) or 1 dirty() end
function Frame:EnableMouse(on) st(self).mouse = on and true or false end
function Frame:IsMouseEnabled() return st(self).mouse == true end
function Frame:EnableMouseWheel(on) st(self).wheel = on and true or false end
function Frame:IsMouseWheelEnabled() return st(self).wheel == true end
function Frame:SetMouseClickEnabled(on) st(self).mouse = on and true or false end
function Frame:SetMouseMotionEnabled() end
function Frame:EnableKeyboard(on) st(self).keyboard = on and true or false end
function Frame:SetPropagateKeyboardInput() end
function Frame:SetMovable(on) st(self).movable = on and true or false end
function Frame:IsMovable() return st(self).movable == true end
function Frame:SetResizable(on) st(self).resizable = on and true or false end
function Frame:IsResizable() return st(self).resizable == true end
function Frame:SetResizeBounds(...) st(self).resizeBounds = { ... } end
function Frame:SetMinResize() end
function Frame:SetMaxResize() end
function Frame:SetClampedToScreen(on) st(self).clamped = on and true or false dirty() end
function Frame:IsClampedToScreen() return st(self).clamped == true end
function Frame:SetClampRectInsets() end
function Frame:RegisterForDrag(...) st(self).drag = { ... } end
function Frame:StartMoving() st(self).moving = true end
function Frame:StartSizing() st(self).sizing = true end
function Frame:StopMovingOrSizing() local s = st(self) s.moving, s.sizing = false, false end
function Frame:SetUserPlaced() end
function Frame:IsUserPlaced() return false end
function Frame:SetDontSavePosition() end
function Frame:SetHitRectInsets(...) st(self).hitInsets = { ... } end
function Frame:GetHitRectInsets() return unpack(st(self).hitInsets or { 0, 0, 0, 0 }) end
function Frame:SetID(id) st(self).id = id end
function Frame:GetID() return st(self).id or 0 end
function Frame:IsProtected() return false end
function Frame:SetClipsChildren(on) st(self).clips = on and true or false end
function Frame:DoesClipChildren() return st(self).clips == true end
function Frame:SetHyperlinksEnabled(on) st(self).hyperlinks = on and true or false end
function Frame:GetHyperlinksEnabled() return st(self).hyperlinks == true end
function Frame:SetAttribute(k, v) local s = st(self) s.attributes = s.attributes or {} s.attributes[k] = v end
function Frame:GetAttribute(k) local s = st(self) return s.attributes and s.attributes[k] end
function Frame:GetChildren() return unpack(st(self).children) end
function Frame:GetNumChildren() return #st(self).children end
function Frame:GetRegions() return unpack(st(self).regions) end
function Frame:GetNumRegions() return #st(self).regions end
function Frame:SetFlattensRenderLayers() end
function Frame:SetIgnoreParentScale() end
function Frame:SetIgnoreParentAlpha() end

-- BackdropTemplate (the game mixes these methods into the frame)
local Backdrop = {}
function Backdrop:SetBackdrop(info)
  local s = st(self)
  s.backdrop = info and { bgFile = info.bgFile, edgeFile = info.edgeFile, edgeSize = tonumber(info.edgeSize) or 0,
                          insets = info.insets, tile = info.tile } or nil
  s.bgColor = s.bgColor or { 1, 1, 1, 1 }
  s.borderColor = s.borderColor or { 1, 1, 1, 1 }
end
function Backdrop:GetBackdrop() return st(self).backdrop end
function Backdrop:SetBackdropColor(r, g, b, a) st(self).bgColor = { r or 1, g or 1, b or 1, a or 1 } end
function Backdrop:GetBackdropColor() local c = st(self).bgColor or { 1, 1, 1, 1 } return unpack(c) end
function Backdrop:SetBackdropBorderColor(r, g, b, a) st(self).borderColor = { r or 1, g or 1, b or 1, a or 1 } end
function Backdrop:GetBackdropBorderColor() local c = st(self).borderColor or { 1, 1, 1, 1 } return unpack(c) end
function Backdrop:ApplyBackdrop() end
function Backdrop:ClearBackdrop() st(self).backdrop = nil end
sim.Backdrop = Backdrop

------------------------------------------------------------------------ Button / CheckButton
function Button:SetFontString(fs) st(self).fontString = fs end
function Button:GetFontString() return st(self).fontString end
function Button:SetText(text)
  local s = st(self)
  if not s.fontString then
    s.fontString = self:CreateFontString(nil, "OVERLAY", s.normalFont or "GameFontNormal")
    s.fontString:SetPoint("CENTER")
  end
  s.fontString:SetText(text)
end
function Button:SetFormattedText(fmt, ...) self:SetText(format(fmt, ...)) end
function Button:GetText() local fs = st(self).fontString return fs and fs:GetText() end
function Button:GetTextWidth() local fs = st(self).fontString return fs and fs:GetStringWidth() or 0 end
function Button:GetTextHeight() local fs = st(self).fontString return fs and fs:GetStringHeight() or 0 end
function Button:SetNormalFontObject(f)
  local s = st(self)
  s.normalFont = f
  if s.fontString then s.fontString:SetFontObject(f) end
end
function Button:SetHighlightFontObject() end
function Button:SetDisabledFontObject() end
function Button:GetNormalFontObject() return st(self).normalFont end
function Button:Enable() st(self).disabled = false end
function Button:Disable() st(self).disabled = true end
function Button:SetEnabled(on) st(self).disabled = not on end
function Button:IsEnabled() return not st(self).disabled end
function Button:RegisterForClicks(...) st(self).clicks = { ... } end
function Button:LockHighlight() st(self).lockHighlight = true end
function Button:UnlockHighlight() st(self).lockHighlight = false end
function Button:SetButtonState(state) st(self).buttonState = state end
function Button:GetButtonState() return st(self).buttonState or "NORMAL" end
function Button:SetMotionScriptsWhileDisabled(on) st(self).motionDisabled = on end

local function stateTexture(slot, layer)
  local function set(self, tex)
    local s = st(self)
    local t = s[slot]
    if type(tex) == "table" and S[tex] then
      s[slot] = tex
      return
    end
    if not t then
      t = self:CreateTexture(nil, layer)
      t:SetAllPoints(self)
      s[slot] = t
    end
    if tex then t:SetTexture(tex) end
  end
  return set, function(self) return st(self)[slot] end
end
Button.SetNormalTexture, Button.GetNormalTexture = stateTexture("normalTexture", "ARTWORK")
Button.SetPushedTexture, Button.GetPushedTexture = stateTexture("pushedTexture", "ARTWORK")
Button.SetHighlightTexture, Button.GetHighlightTexture = stateTexture("highlightTexture", "HIGHLIGHT")
Button.SetDisabledTexture, Button.GetDisabledTexture = stateTexture("disabledTexture", "ARTWORK")
CheckButton.SetCheckedTexture, CheckButton.GetCheckedTexture = stateTexture("checkedTexture", "OVERLAY")
CheckButton.SetDisabledCheckedTexture, CheckButton.GetDisabledCheckedTexture =
  stateTexture("disabledCheckedTexture", "OVERLAY")

function CheckButton:SetChecked(on) st(self).checked = on and true or false end
function CheckButton:GetChecked() return st(self).checked == true end

-- a mouse click the way the game delivers it
function Button:Click(button)
  local s = st(self)
  if s.disabled then return end
  button = button or "LeftButton"
  runScript(self, "PreClick", button, false)
  if s.kind == "CheckButton" then s.checked = not s.checked end
  runScript(self, "OnClick", button, false)
  runScript(self, "PostClick", button, false)
end

------------------------------------------------------------------------ EditBox
local focused -- the EditBox with keyboard focus

function EditBox:SetText(text)
  local s = st(self)
  local t = textOf(text)
  if s.maxLetters and s.maxLetters > 0 and #t > s.maxLetters then t = t:sub(1, s.maxLetters) end
  s.text = t
  s.cursor = #t
  dirty()
  runScript(self, "OnTextChanged", false)
end
function EditBox:GetText() return st(self).text end
function EditBox:Insert(text)
  local s = st(self)
  local t = textOf(text)
  local c = s.cursor or #s.text
  s.text = s.text:sub(1, c) .. t .. s.text:sub(c + 1)
  s.cursor = c + #t
  dirty()
  runScript(self, "OnTextChanged", true)
end
function EditBox:GetNumLetters() return #(st(self).text:gsub("[\128-\191]", "")) end
function EditBox:SetFocus()
  if focused == self then return end
  local old = focused
  focused = self
  if old then runScript(old, "OnEditFocusLost") end
  runScript(self, "OnEditFocusGained")
end
function EditBox:ClearFocus()
  if focused ~= self then return end
  focused = nil
  runScript(self, "OnEditFocusLost")
end
function EditBox:HasFocus() return focused == self end
function EditBox:SetAutoFocus(on) st(self).autoFocus = on and true or false end
function EditBox:SetMaxLetters(n) st(self).maxLetters = tonumber(n) or 0 end
function EditBox:GetMaxLetters() return st(self).maxLetters or 0 end
function EditBox:SetMaxBytes(n) st(self).maxBytes = tonumber(n) or 0 end
function EditBox:SetNumeric(on) st(self).numeric = on and true or false end
function EditBox:SetMultiLine(on) st(self).multiLine = on and true or false end
function EditBox:IsMultiLine() return st(self).multiLine == true end
function EditBox:HighlightText(a, b) st(self).highlight = { a or 0, b or -1 } end
function EditBox:SetCursorPosition(n) st(self).cursor = tonumber(n) or 0 end
function EditBox:GetCursorPosition() local s = st(self) return s.cursor or #s.text end
function EditBox:SetTextInsets(l, r, t, b) st(self).insets = { l or 0, r or 0, t or 0, b or 0 } dirty() end
function EditBox:GetTextInsets() return unpack(st(self).insets or { 0, 0, 0, 0 }) end
function EditBox:Enable() st(self).disabled = false end
function EditBox:Disable() st(self).disabled = true end
function EditBox:SetEnabled(on) st(self).disabled = not on end
function EditBox:IsEnabled() return not st(self).disabled end
function EditBox:SetBlinkSpeed() end
function EditBox:SetHistoryLines() end
function EditBox:AddHistoryLine() end
function EditBox:SetCountInvisibleLetters() end
function EditBox:SetAltArrowKeyMode() end

------------------------------------------------------------------------ ScrollFrame
function ScrollFrame:SetScrollChild(child)
  local s = st(self)
  if s.scrollChild then S[s.scrollChild].scrollChildOf = nil end
  s.scrollChild = child
  if child then
    local c = st(child)
    c.scrollChildOf = self
    if c.parent ~= self then child:SetParent(self) end
  end
  dirty()
end
function ScrollFrame:GetScrollChild() return st(self).scrollChild end
local function scrollRange(self, axis)
  local s = st(self)
  local child = s.scrollChild
  if not child then return 0 end
  if axis == "v" then return max(0, child:GetHeight() * effScale(child) / effScale(self) - self:GetHeight()) end
  return max(0, child:GetWidth() * effScale(child) / effScale(self) - self:GetWidth())
end
function ScrollFrame:GetVerticalScrollRange() return scrollRange(self, "v") end
function ScrollFrame:GetHorizontalScrollRange() return scrollRange(self, "h") end
function ScrollFrame:SetVerticalScroll(v)
  st(self).vscroll = tonumber(v) or 0
  dirty()
  runScript(self, "OnVerticalScroll", st(self).vscroll)
end
function ScrollFrame:GetVerticalScroll() return st(self).vscroll or 0 end
function ScrollFrame:SetHorizontalScroll(v)
  st(self).hscroll = tonumber(v) or 0
  dirty()
  runScript(self, "OnHorizontalScroll", st(self).hscroll)
end
function ScrollFrame:GetHorizontalScroll() return st(self).hscroll or 0 end
function ScrollFrame:UpdateScrollChildRect() end

------------------------------------------------------------------------ CreateFrame
local templates = {}
sim.templates = templates

function CreateFrame(kind, name, parent, template, id)
  local class_ = KIND[kind]
  if not class_ then error("CreateFrame: unknown frame type " .. tostring(kind), 2) end
  if type(parent) == "string" then parent = _G[parent] end
  if parent ~= nil and not S[parent] then error("CreateFrame: the parent is not a frame", 2) end
  local s = newState(kind, parent)
  s.id = id
  local f = setmetatable({}, class_)
  register(f, s, name)
  frames[#frames + 1] = f
  s.frameIndex = #frames
  if parent then
    local ps = st(parent)
    ps.children[#ps.children + 1] = f
  end
  if kind == "Button" or kind == "CheckButton" then s.mouse = true end
  if kind == "EditBox" then
    s.text, s.mouse, s.keyboard = "", true, true
    s.justifyH = "LEFT"
  end
  if kind == "GameTooltip" then s.lines = {} end
  dirty()
  for t in tostring(template or ""):gmatch("[^,%s]+") do
    local apply = templates[t]
    if apply then
      apply(f)
    else
      sim.warn("template not emulated: " .. t)
    end
  end
  return f
end

templates.BackdropTemplate = function(f) Mixin(f, Backdrop) end
templates.TooltipBackdropTemplate = templates.BackdropTemplate
templates.UICheckButtonTemplate = function(f)
  f:SetSize(32, 32)
  f:SetNormalTexture("Interface\\Buttons\\UI-CheckBox-Up")
  f:SetPushedTexture("Interface\\Buttons\\UI-CheckBox-Down")
  f:SetHighlightTexture("Interface\\Buttons\\UI-CheckBox-Highlight")
  f:SetCheckedTexture("Interface\\Buttons\\UI-CheckBox-Check")
  f:SetDisabledCheckedTexture("Interface\\Buttons\\UI-CheckBox-Check-Disabled")
  local text = f:CreateFontString(S[f].name and (S[f].name .. "Text") or nil, "ARTWORK", "GameFontNormalSmall")
  text:SetPoint("LEFT", f, "RIGHT", -2, 1)
  f.text, f.Text = text, text
end
templates.UIPanelButtonTemplate = function(f)
  Mixin(f, Backdrop)
  f:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8x8", edgeFile = "Interface\\Buttons\\WHITE8x8", edgeSize = 1 })
  f:SetBackdropColor(0.45, 0.05, 0.03, 1)
  f:SetBackdropBorderColor(0.8, 0.6, 0.2, 1)
  f:SetNormalFontObject("GameFontNormal")
  sim.warn("UIPanelButtonTemplate is drawn as a plain red button")
end
templates.InputBoxTemplate = function(f)
  f:SetFontObject("ChatFontNormal")
  sim.warn("InputBoxTemplate is drawn without its border art")
end
templates.SecureActionButtonTemplate = function() end

------------------------------------------------------------------------ the screen, fonts and standard frames
local function screenFrame(name)
  local f = CreateFrame("Frame", name)
  S[f].isScreen = true
  return f
end
UIParent = screenFrame("UIParent")
S[UIParent].strata = "MEDIUM"
WorldFrame = screenFrame("WorldFrame")
S[WorldFrame].strata = "WORLD"

local function stdFont(name, path, size, r, g, b, shadow, flags)
  local f = CreateFont(name)
  f:SetFont(path, size, flags or "")
  f:SetTextColor(r, g, b, 1)
  if shadow then f:SetShadowOffset(1, -1) f:SetShadowColor(0, 0, 0, 1) end
  return f
end
local FRIZ, ARIALN = "Fonts\\FRIZQT__.TTF", "Fonts\\ARIALN.TTF"
stdFont("ChatFontNormal", ARIALN, 14, 1, 1, 1, true)
stdFont("ChatFontSmall", ARIALN, 12, 1, 1, 1, true)
stdFont("GameFontNormal", FRIZ, 12, 1, 0.82, 0, true)
stdFont("GameFontHighlight", FRIZ, 12, 1, 1, 1, true)
stdFont("GameFontDisable", FRIZ, 12, 0.5, 0.5, 0.5, true)
stdFont("GameFontNormalSmall", FRIZ, 10, 1, 0.82, 0, true)
stdFont("GameFontHighlightSmall", FRIZ, 10, 1, 1, 1, true)
stdFont("GameFontNormalLarge", FRIZ, 16, 1, 0.82, 0, true)
stdFont("GameFontHighlightLarge", FRIZ, 16, 1, 1, 1, true)
stdFont("GameTooltipText", FRIZ, 12, 1, 1, 1, false)
stdFont("GameTooltipTextSmall", FRIZ, 10, 1, 1, 1, false)
stdFont("GameTooltipHeaderText", FRIZ, 14, 1, 1, 1, false)
stdFont("NumberFontNormal", ARIALN, 14, 1, 1, 1, false, "OUTLINE")
stdFont("SystemFont_Shadow_Med1", FRIZ, 12, 1, 0.82, 0, true)

UISpecialFrames, UIPanelWindows = {}, {}
SlashCmdList = {}

-- the chat frame: the newest lines at the bottom left of the screen
DEFAULT_CHAT_FRAME = CreateFrame("ScrollingMessageFrame", "ChatFrame1", UIParent)
DEFAULT_CHAT_FRAME:SetPoint("BOTTOMLEFT", UIParent, "BOTTOMLEFT", 32, 110)
DEFAULT_CHAT_FRAME:SetSize(430, 120)
SELECTED_CHAT_FRAME = DEFAULT_CHAT_FRAME
local chatText = DEFAULT_CHAT_FRAME:CreateFontString(nil, "ARTWORK", "ChatFontNormal")
chatText:SetPoint("BOTTOMLEFT")
chatText:SetWidth(430)
chatText:SetJustifyH("LEFT")
chatText:SetJustifyV("BOTTOM")
local chatLines = {}
function MessageFrame:AddMessage(text, r, g, b)
  text = textOf(text)
  sim.chat[#sim.chat + 1] = text
  host.log("chat", text)
  if r and g and b then text = format("|cff%02x%02x%02x", r * 255, g * 255, b * 255) .. text .. "|r" end
  chatLines[#chatLines + 1] = text
  while #chatLines > 8 do table.remove(chatLines, 1) end
  chatText:SetText(table.concat(chatLines, "\n"))
end
function MessageFrame:Clear() wipe(chatLines) chatText:SetText("") end
function MessageFrame:SetMaxLines() end
function MessageFrame:SetFading() end
function MessageFrame:SetInsertMode() end
ChatFrame1 = DEFAULT_CHAT_FRAME

-- the game's notices in the upper middle of the screen ("Out of range"); add-ons put their own lines there too
-- the game's own dialogs ("X has been blocked from an action only available to the Blizzard UI"): none is shown here
function StaticPopup_Show(which) end
function StaticPopup_Hide(which) end
function StaticPopup_Visible(which) return nil end

UIErrorsFrame = CreateFrame("Frame", "UIErrorsFrame", UIParent)
UIErrorsFrame:SetPoint("TOP", UIParent, "TOP", 0, -122)
UIErrorsFrame:SetSize(512, 60)
local noticeText = UIErrorsFrame:CreateFontString(nil, "ARTWORK", "ChatFontNormal")
noticeText:SetPoint("TOP")
function UIErrorsFrame:AddMessage(text, r, g, b)
  text = textOf(text)
  host.log("notice", text)
  if r and g and b then text = format("|cff%02x%02x%02x", r * 255, g * 255, b * 255) .. text .. "|r" end
  noticeText:SetText(text) -- the newest one; the game fades them out
end

function print(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
  DEFAULT_CHAT_FRAME:AddMessage(table.concat(parts, " "))
end

-- "Screen captured" (the add-on stops it from showing while it sends a question)
ActionStatus = CreateFrame("Frame", "ActionStatus", UIParent)
ActionStatus:SetFrameStrata("TOOLTIP")
ActionStatus:SetSize(400, 30)
ActionStatus:SetPoint("TOP", UIParent, "TOP", 0, -160)
ActionStatus.Text = ActionStatus:CreateFontString(nil, "ARTWORK", "GameFontHighlightLarge")
ActionStatus.Text:SetPoint("CENTER")
ActionStatus.Text:SetTextColor(1, 0.82, 0)
ActionStatus:Hide()
ActionStatus:RegisterEvent("SCREENSHOT_SUCCEEDED")
ActionStatus:RegisterEvent("SCREENSHOT_FAILED")
ActionStatus:SetScript("OnEvent", function(self, event)
  self.Text:SetText(event == "SCREENSHOT_SUCCEEDED" and "Screen captured" or "Screen capture failed")
  self:Show()
  self.hideAt = sim.now + 2
end)
ActionStatus:SetScript("OnUpdate", function(self) if self.hideAt and sim.now >= self.hideAt then self:Hide() end end)

------------------------------------------------------------------------ GameTooltip
local TIP_PAD, TIP_WRAP = 10, 280

templates.GameTooltipTemplate = function(tip)
  Mixin(tip, Backdrop)
  tip:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8x8", edgeFile = "Interface\\Buttons\\WHITE8x8", edgeSize = 1 })
  tip:SetBackdropColor(0.02, 0.02, 0.05, 0.92)
  tip:SetBackdropBorderColor(0.6, 0.6, 0.65, 1)
  tip:SetFrameStrata("TOOLTIP")
  tip:SetClampedToScreen(true)
  tip:Hide()
end

local function newTooltip(name) return CreateFrame("GameTooltip", name, UIParent, "GameTooltipTemplate") end

local function tipLine(self, i)
  local s = st(self)
  local name = s.name or "SimTooltip"
  local line = s.lines[i]
  if not line then
    local left = self:CreateFontString(name .. "TextLeft" .. i, "ARTWORK", i == 1 and "GameTooltipHeaderText" or "GameTooltipText")
    local right = self:CreateFontString(name .. "TextRight" .. i, "ARTWORK", "GameTooltipText")
    left:SetJustifyH("LEFT")
    right:SetJustifyH("RIGHT")
    line = { left = left, right = right }
    s.lines[i] = line
  end
  return line
end

local function tipLayout(self)
  local s = st(self)
  local n = s.numLines or 0
  local width = s.minWidth or 0
  for i = 1, n do
    local l = s.lines[i]
    local lw = l.left:GetUnboundedStringWidth()
    if l.wrap and lw > TIP_WRAP then lw = TIP_WRAP end
    local rw = l.right:GetText() and (l.right:GetUnboundedStringWidth() + 20) or 0
    width = max(width, lw + rw)
  end
  local y = TIP_PAD
  for i = 1, n do
    local l = s.lines[i]
    l.left:ClearAllPoints()
    l.left:SetPoint("TOPLEFT", self, "TOPLEFT", TIP_PAD, -y)
    l.left:SetWidth(l.wrap and width or 0)
    l.left:Show()
    if l.right:GetText() then
      l.right:ClearAllPoints()
      l.right:SetPoint("TOPRIGHT", self, "TOPRIGHT", -TIP_PAD, -y)
      l.right:Show()
    else
      l.right:Hide()
    end
    y = y + l.left:GetStringHeight() + 2
  end
  for i = n + 1, #s.lines do s.lines[i].left:Hide() s.lines[i].right:Hide() end
  self:SetSize(width + 2 * TIP_PAD, y + TIP_PAD - 2)
end

local ANCHORS = {
  ANCHOR_TOP = { "BOTTOM", "TOP" }, ANCHOR_BOTTOM = { "TOP", "BOTTOM" },
  ANCHOR_LEFT = { "BOTTOMRIGHT", "TOPLEFT" }, ANCHOR_RIGHT = { "BOTTOMLEFT", "TOPRIGHT" },
  ANCHOR_TOPLEFT = { "BOTTOMLEFT", "TOPLEFT" }, ANCHOR_TOPRIGHT = { "BOTTOMRIGHT", "TOPRIGHT" },
  ANCHOR_BOTTOMLEFT = { "TOPRIGHT", "BOTTOMLEFT" }, ANCHOR_BOTTOMRIGHT = { "TOPLEFT", "BOTTOMRIGHT" },
  ANCHOR_CURSOR = { "BOTTOMLEFT", "CENTER" }, ANCHOR_CURSOR_RIGHT = { "BOTTOMLEFT", "CENTER" },
}

function Tooltip:SetOwner(owner, anchor, x, y)
  local s = st(self)
  s.owner, s.anchor = owner, anchor or "ANCHOR_LEFT"
  self:ClearLines()
  self:ClearAllPoints()
  local a = ANCHORS[s.anchor]
  if a and owner then self:SetPoint(a[1], owner, a[2], x or 0, y or 0) end
end
function Tooltip:GetOwner() return st(self).owner end
function Tooltip:IsOwned(f) return st(self).owner == f end
function Tooltip:ClearLines()
  local s = st(self)
  s.numLines = 0
  for _, l in ipairs(s.lines) do l.left:SetText("") l.right:SetText("") l.wrap = false end
end
function Tooltip:AddLine(text, r, g, b, wrap)
  local s = st(self)
  s.numLines = (s.numLines or 0) + 1
  local l = tipLine(self, s.numLines)
  l.left:SetText(text)
  l.left:SetTextColor(r or 1, g or 0.82, b or 0)
  l.right:SetText("")
  l.wrap = wrap == true or wrap == 1
  tipLayout(self)
end
function Tooltip:AddDoubleLine(left, right, lr, lg, lb, rr, rg, rb)
  self:AddLine(left, lr, lg, lb)
  local l = st(self).lines[st(self).numLines]
  l.right:SetText(right)
  l.right:SetTextColor(rr or 1, rg or 0.82, rb or 0)
  tipLayout(self)
end
function Tooltip:SetText(text, r, g, b, a, wrap)
  self:ClearLines()
  self:AddLine(text, r or 1, g or 1, b or 1, wrap)
  self:Show()
end
function Tooltip:AppendText(text)
  local s = st(self)
  if s.numLines and s.numLines > 0 then
    local l = s.lines[s.numLines].left
    l:SetText((l:GetText() or "") .. textOf(text))
    tipLayout(self)
  end
end
function Tooltip:NumLines() return st(self).numLines or 0 end
function Tooltip:SetMinimumWidth(w) st(self).minWidth = tonumber(w) or 0 end
function Tooltip:SetPadding() end
function Tooltip:FadeOut() self:Hide() end
function Tooltip:SetHyperlink(link)
  local kind, id = tostring(link):match("^(%a+):(%-?%d+)")
  if not kind then error("SetHyperlink: unknown link " .. tostring(link), 2) end
  self:ClearLines()
  self:AddLine(kind:sub(1, 1):upper() .. kind:sub(2) .. " " .. id, 1, 1, 1)
  self:AddLine("(the simulator has no game data for links)", 0.6, 0.6, 0.6, true)
end
function Tooltip:GetItem() return nil end
function Tooltip:GetSpell() return nil end
function Tooltip:GetUnit() return nil end
function Tooltip:GetTooltipData() return nil end
function Tooltip:SetUnit() end
function Tooltip:SetBagItem() end
function Tooltip:SetInventoryItem() end
local tipShow = Region.Show
function Tooltip:Show()
  tipLayout(self)
  tipShow(self)
end
local tipHide = Region.Hide
function Tooltip:Hide()
  tipHide(self)
  st(self).owner = nil
end

GameTooltip = newTooltip("GameTooltip")
ItemRefTooltip = newTooltip("ItemRefTooltip")

------------------------------------------------------------------------ timers
local timers = {}
local timerSeq = 0

local Timer = {}
Timer.__index = Timer
function Timer:Cancel() self._cancelled = true end
function Timer:IsCancelled() return self._cancelled == true end

local function addTimer(delay, fn, interval, iterations, obj)
  timerSeq = timerSeq + 1
  local t = obj or setmetatable({}, Timer)
  t._due, t._fn, t._interval, t._left, t._seq = sim.now + max(0, tonumber(delay) or 0), fn, interval, iterations, timerSeq
  timers[#timers + 1] = t
  return t
end

C_Timer = {
  After = function(delay, fn)
    if type(fn) ~= "function" then error("C_Timer.After: a function is needed", 2) end
    addTimer(delay, fn)
  end,
  NewTimer = function(delay, fn)
    local t = addTimer(delay, fn)
    t._pass = true
    return t
  end,
  NewTicker = function(interval, fn, iterations)
    local t = addTimer(interval, fn, tonumber(interval) or 0, iterations)
    t._pass = true
    return t
  end,
}

local function runTimers()
  local due = {}
  local keep = {}
  for _, t in ipairs(timers) do
    if t._cancelled then -- dropped
    elseif t._due <= sim.now then due[#due + 1] = t
    else keep[#keep + 1] = t end
  end
  timers = keep
  table.sort(due, function(a, b) if a._due ~= b._due then return a._due < b._due end return a._seq < b._seq end)
  for _, t in ipairs(due) do
    if not t._cancelled then
      if t._pass then call(t._fn, t) else call(t._fn) end
      if t._interval and not t._cancelled then
        if t._left then t._left = t._left - 1 end
        if not t._left or t._left > 0 then
          t._due = sim.now + t._interval
          timers[#timers + 1] = t
        end
      end
    end
  end
end

------------------------------------------------------------------------ the frame loop
local queued = {} -- events the client sends on the next frame

function sim.queue(event, ...)
  queued[#queued + 1] = { event, ... }
end

-- OnSizeChanged / OnScrollRangeChanged, the way the game's layout pass sends them before a frame is drawn
local function settle()
  for _ = 1, 10 do
    local changed = false
    for _, f in ipairs(frames) do
      local s = S[f]
      if s.scripts and s.scripts.OnSizeChanged then
        local r = rectOf(f)
        if r then
          local w, h = f:GetWidth(), f:GetHeight()
          if not s.lastSize or abs(s.lastSize[1] - w) > 0.01 or abs(s.lastSize[2] - h) > 0.01 then
            s.lastSize = { w, h }
            runScript(f, "OnSizeChanged", w, h)
            changed = true
          end
        end
      end
      if s.kind == "ScrollFrame" and s.scrollChild and rectOf(f) then
        local v, hz = scrollRange(f, "v"), scrollRange(f, "h")
        if not s.lastRange or abs(s.lastRange[1] - v) > 0.01 or abs(s.lastRange[2] - hz) > 0.01 then
          s.lastRange = { v, hz }
          runScript(f, "OnScrollRangeChanged", hz, v)
          changed = true
        end
      end
    end
    if not changed then return end
  end
end
sim.settle = settle

function sim.tick(dt)
  sim.now = sim.now + dt
  sim.frames = sim.frames + 1
  local q = queued
  queued = {}
  for _, e in ipairs(q) do sim.fire(unpack(e)) end
  runTimers()
  local list = {}
  for i, f in ipairs(updaters) do list[i] = f end
  for _, f in ipairs(list) do
    if isVisible(f) then runScript(f, "OnUpdate", dt) end
  end
  settle()
end

------------------------------------------------------------------------ add-ons
local addons, addonByName = {}, {}
sim.addons = addons

local function addonInfo(nameOrIndex)
  if type(nameOrIndex) == "number" then return addons[nameOrIndex] end
  return addonByName[tostring(nameOrIndex or ""):lower()]
end

-- the add-ons found in Interface/AddOns when the UI loaded (the host reads the folder and the .toc files)
function sim.setAddons(list)
  for i = 1, #list do
    local a = list[i]
    local info = { index = i, name = a.name, dir = a.dir, files = {}, meta = {}, sv = {}, svChar = {},
                   lod = a.lod == true, deps = {}, enabled = true }
    for j = 1, #a.files do info.files[j] = a.files[j] end
    for k, v in pairs(a.meta) do info.meta[k] = v end
    for j = 1, #a.sv do info.sv[j] = a.sv[j] end
    for j = 1, #a.svChar do info.svChar[j] = a.svChar[j] end
    for j = 1, #a.deps do info.deps[j] = a.deps[j] end
    addons[i] = info
    addonByName[a.name:lower()] = info
  end
end

local function runFile(info, file, ns)
  local path = info.dir .. "/" .. file
  local src, err = host.read_file(path)
  if not src then report("cannot read " .. path .. ": " .. tostring(err)) return end
  local chunk, cerr = loadstring(src, "@Interface/AddOns/" .. info.name .. "/" .. file)
  if not chunk then report(cerr) return end
  call(chunk, info.name, ns)
end

local function loadSaved(info, perChar)
  local path = host.saved_path(info.name, perChar and 1 or 0)
  local src = host.read_file(path)
  if not src then return end
  local env = {}
  local chunk, err = loadstring(src, "@WTF/" .. info.name .. ".lua")
  if not chunk then report("saved variables of " .. info.name .. ": " .. tostring(err)) return end
  setfenv(chunk, env)
  local ok, e = pcall(chunk)
  if not ok then report("saved variables of " .. info.name .. ": " .. tostring(e)) return end
  return env
end

local function loadAddOn(info)
  if info.loaded then return true end
  for _, dep in ipairs(info.deps) do
    local d = addonInfo(dep)
    if not d then return false, "DEP_MISSING" end
    if not d.loaded then
      local ok, why = loadAddOn(d)
      if not ok then return false, "DEP_" .. tostring(why) end
    end
  end
  info.loaded = true
  local ns = {}
  for _, file in ipairs(info.files) do
    if file:lower():match("%.lua$") then
      runFile(info, file, ns)
    elseif file:lower():match("%.xml$") then
      sim.warn("XML files are not loaded by the simulator: " .. info.name .. "/" .. file)
    end
  end
  -- saved variables come after the add-on's files and before ADDON_LOADED, as in the game
  local account, char = nil, nil
  if #info.sv > 0 then account = loadSaved(info, false) or {} end
  if #info.svChar > 0 then char = loadSaved(info, true) or {} end
  for _, var in ipairs(info.sv) do _G[var] = account[var] end
  for _, var in ipairs(info.svChar) do _G[var] = char[var] end
  sim.fire("ADDON_LOADED", info.name)
  return true
end

function sim.loadAddOns()
  for _, info in ipairs(addons) do
    if not info.lod and info.enabled then loadAddOn(info) end
  end
end

-- saved variables, written the way the game writes them
local function serialize(v, indent, out, seen)
  local t = type(v)
  if t == "string" then
    out[#out + 1] = format("%q", v)
  elseif t == "number" then
    out[#out + 1] = (v == floor(v) and abs(v) < 1e15) and format("%d", v) or format("%.17g", v)
  elseif t == "boolean" then
    out[#out + 1] = tostring(v)
  elseif t == "table" then
    if seen[v] then out[#out + 1] = "nil" return end
    seen[v] = true
    local keys = {}
    for k, x in pairs(v) do
      local kt, xt = type(k), type(x)
      if (kt == "string" or kt == "number" or kt == "boolean")
        and (xt == "string" or xt == "number" or xt == "boolean" or xt == "table") then keys[#keys + 1] = k end
    end
    table.sort(keys, function(a, b)
      if type(a) == type(b) and type(a) ~= "boolean" then return a < b end
      return type(a) == "number" or (type(a) == "string" and type(b) == "boolean")
    end)
    out[#out + 1] = "{\n"
    for _, k in ipairs(keys) do
      out[#out + 1] = indent .. "\t["
      serialize(k, "", out, seen)
      out[#out + 1] = "] = "
      serialize(v[k], indent .. "\t", out, seen)
      out[#out + 1] = ",\n"
    end
    out[#out + 1] = indent .. "}"
    seen[v] = nil
  else
    out[#out + 1] = "nil"
  end
end

function sim.saveVariables()
  for _, info in ipairs(addons) do
    if info.loaded then
      for _, group in ipairs({ { info.sv, 0 }, { info.svChar, 1 } }) do
        if #group[1] > 0 then
          local out = {}
          for _, var in ipairs(group[1]) do
            out[#out + 1] = var .. " = "
            serialize(_G[var], "", out, {})
            out[#out + 1] = "\n"
          end
          host.write_file(host.saved_path(info.name, group[2]), table.concat(out))
        end
      end
    end
  end
end

C_AddOns = {}
function C_AddOns.GetNumAddOns() return #addons end
function C_AddOns.GetAddOnInfo(nameOrIndex)
  local info = addonInfo(nameOrIndex)
  if not info then
    if type(nameOrIndex) == "number" then error("AddOn index must be in the range of 1 to " .. #addons, 2) end
    return tostring(nameOrIndex), nil, nil, false, "MISSING", "INSECURE", false
  end
  local reason = (not info.enabled) and "DISABLED" or (info.lod and not info.loaded and "DEMAND_LOADED") or nil
  return info.name, info.meta.Title or info.name, info.meta.Notes, info.enabled, reason, "INSECURE", false
end
function C_AddOns.DoesAddOnExist(name) return addonInfo(name) ~= nil end
function C_AddOns.IsAddOnLoaded(nameOrIndex)
  local info = addonInfo(nameOrIndex)
  return info and info.loaded == true or false, info and info.loaded == true or false
end
function C_AddOns.IsAddOnLoadOnDemand(nameOrIndex)
  local info = addonInfo(nameOrIndex)
  return info and info.lod or false
end
function C_AddOns.GetAddOnMetadata(nameOrIndex, field)
  local info = addonInfo(nameOrIndex)
  return info and info.meta[field]
end
function C_AddOns.GetAddOnEnableState(name, character)
  local info = addonInfo(name)
  return info and info.enabled and 2 or 0
end
function C_AddOns.EnableAddOn(name) local info = addonInfo(name) if info then info.enabled = true end end
function C_AddOns.DisableAddOn(name) local info = addonInfo(name) if info then info.enabled = false end end
function C_AddOns.SaveAddOns() end
function C_AddOns.GetAddOnDependencies(name) local info = addonInfo(name) return unpack(info and info.deps or {}) end
function C_AddOns.LoadAddOn(name)
  local info = addonInfo(name)
  if not info then return false, "MISSING" end
  if not info.enabled then return false, "DISABLED" end
  return loadAddOn(info)
end
-- the old global names
GetNumAddOns, GetAddOnInfo, IsAddOnLoaded, LoadAddOn, GetAddOnMetadata =
  C_AddOns.GetNumAddOns, C_AddOns.GetAddOnInfo, C_AddOns.IsAddOnLoaded, C_AddOns.LoadAddOn, C_AddOns.GetAddOnMetadata

------------------------------------------------------------------------ encoding
local escapes = { ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f", ["\n"] = "\\n", ["\r"] = "\\r",
                  ["\t"] = "\\t" }
local function jsonString(s)
  return '"' .. s:gsub('[%c"\\]', function(c) return escapes[c] or format("\\u%04x", c:byte()) end) .. '"'
end

local function jsonEncode(v, out, depth)
  local t = type(v)
  if depth > 64 then error("SerializeJSON: the table is nested too deeply", 0) end
  if t == "nil" then
    out[#out + 1] = "null"
  elseif t == "boolean" then
    out[#out + 1] = v and "true" or "false"
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then error("SerializeJSON: a number is not finite", 0) end
    out[#out + 1] = (v == floor(v) and abs(v) < 1e15) and format("%d", v) or format("%.14g", v)
  elseif t == "string" then
    out[#out + 1] = jsonString(v)
  elseif t == "table" then
    local n, count = #v, 0
    for _ in pairs(v) do count = count + 1 end
    if n > 0 and n == count then
      out[#out + 1] = "["
      for i = 1, n do
        if i > 1 then out[#out + 1] = "," end
        jsonEncode(v[i], out, depth + 1)
      end
      out[#out + 1] = "]"
    else
      out[#out + 1] = "{"
      local first = true
      for k, x in pairs(v) do
        local kt = type(k)
        if kt ~= "string" and kt ~= "number" then error("SerializeJSON: a key is a " .. kt, 0) end
        if not first then out[#out + 1] = "," end
        first = false
        out[#out + 1] = jsonString(tostring(k))
        out[#out + 1] = ":"
        jsonEncode(x, out, depth + 1)
      end
      out[#out + 1] = "}"
    end
  else
    error("SerializeJSON: cannot serialize a " .. t, 0)
  end
end

Enum = Enum or {}
Enum.CompressionMethod = { Deflate = 0, Zlib = 1, Gzip = 2 }
Enum.CompressionLevel = { Default = 0, OptimizeForSpeed = 1, OptimizeForSize = 2 }

C_EncodingUtil = {
  SerializeJSON = function(v)
    local out = {}
    jsonEncode(v, out, 0)
    return table.concat(out)
  end,
  DeserializeJSON = function(s)
    local ok, v = host.json_decode(tostring(s))
    if not ok then error("DeserializeJSON: " .. tostring(v), 2) end
    return v
  end,
  CompressString = function(s, method, level) return host.compress(tostring(s), tonumber(method) or 0) end,
  DecompressString = function(s, method)
    local ok, v = host.decompress(tostring(s), tonumber(method) or 0)
    if not ok then error("DecompressString: " .. tostring(v), 2) end
    return v
  end,
  EncodeBase64 = function(s) return host.b64encode(tostring(s)) end,
  DecodeBase64 = function(s)
    local ok, v = host.b64decode(tostring(s))
    if not ok then error("DecodeBase64: " .. tostring(v), 2) end
    return v
  end,
}

------------------------------------------------------------------------ console variables, screenshots, UI
local cvars = { screenshotFormat = "jpeg", screenshotQuality = "3", uiScale = "1", useUiScale = "0",
                autoLootDefault = "0", Sound_EnableAllSound = "1", scriptErrors = "1" }
sim.cvars = cvars
function GetCVar(name) return cvars[name] end
function SetCVar(name, value)
  if name == "screenshotFormat" and not ({ tga = 1, png = 1, jpeg = 1, jpg = 1 })[tostring(value)] then return false end
  cvars[name] = value ~= nil and tostring(value) or nil
  return true
end
function GetCVarBool(name) return cvars[name] == "1" end
function GetCVarDefault(name) return nil end
C_CVar = { GetCVar = GetCVar, SetCVar = SetCVar, GetCVarBool = GetCVarBool }

function GetPhysicalScreenSize() return SCREEN_W, SCREEN_H end
function GetScreenWidth() return UIParent:GetWidth() end
function GetScreenHeight() return UIParent:GetHeight() end

-- the game writes Screenshots/WoWScrnShot_MMDDYY_HHMMSS.<ext> of the frame being drawn and reports it on the next one
function Screenshot()
  local fmt = cvars.screenshotFormat
  local ext = (fmt == "tga" and "tga") or (fmt == "png" and "png") or "jpg"
  local name = date("WoWScrnShot_%m%d%y_%H%M%S") .. "." .. ext
  settle()
  local ok = host.screenshot(name, ext, tonumber(cvars.screenshotQuality) or 3)
  sim.screenshots = sim.screenshots + 1
  sim.queue(ok and "SCREENSHOT_SUCCEEDED" or "SCREENSHOT_FAILED")
end
TakeScreenshot = Screenshot

function ReloadUI() sim.reloadRequested = true end
function PlaySound() return true end
function PlaySoundFile() return true end
function StopSound() end
SOUNDKIT = { TELL_MESSAGE = 3081, IG_MAINMENU_OPTION_CHECKBOX_ON = 856, IG_MAINMENU_OPTION_CHECKBOX_OFF = 857 }

function EnumerateFrames(f)
  if f == nil then return frames[1] end
  local s = S[f]
  return s and s.frameIndex and frames[s.frameIndex + 1] or nil
end

function GetMouseFoci() return sim.hovered and { sim.hovered } or {} end
function GetMouseFocus() return sim.hovered end
function IsAltKeyDown() return sim.modifiers.alt == true end
function IsShiftKeyDown() return sim.modifiers.shift == true end
function IsControlKeyDown() return sim.modifiers.ctrl == true end
function IsModifierKeyDown() return IsAltKeyDown() or IsShiftKeyDown() or IsControlKeyDown() end
function GetBindingKey() return nil end
function GetBindingText(key) return key end
function InCombatLockdown() return false end

function hooksecurefunc(a, b, c)
  local tbl, name, hook = _G, a, b
  if type(a) == "table" then tbl, name, hook = a, b, c end
  local old = tbl[name]
  if type(old) ~= "function" then error("hooksecurefunc: " .. tostring(name) .. " is not a function", 2) end
  tbl[name] = function(...)
    local res = { old(...) }
    hook(...)
    return unpack(res, 1, table.maxn(res))
  end
end
function securecall(fn, ...)
  if type(fn) == "string" then fn = _G[fn] end
  return fn(...)
end
function issecure() return false end

------------------------------------------------------------------------ the player at the keyboard
local function stripCodes(s)
  s = tostring(s or "")
  s = s:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|cn[^:]*:", ""):gsub("|r", "")
  s = s:gsub("|H.-|h(.-)|h", "%1"):gsub("|T.-|t", ""):gsub("|A.-|a", ""):gsub("||", "|")
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end
sim.stripCodes = stripCodes

local function clipOf(w)
  -- the part of the screen a widget can be seen in (scroll frames cut off their scroll child)
  local l, b, r, t = 0, 0, SCREEN_W, SCREEN_H
  local guard = 0
  local cur = w
  while cur and guard < 100 do
    local s = S[cur]
    local clipper = s.scrollChildOf or (s.clips and cur ~= w and cur) or nil
    if clipper then
      local cr = rectOf(clipper)
      if cr then l, b, r, t = max(l, cr.l), max(b, cr.b), min(r, cr.r), min(t, cr.t) end
    end
    cur = s.parent
    guard = guard + 1
  end
  return l, b, r, t
end
sim.clipOf = clipOf

local function onScreen(w)
  local r = rectOf(w)
  if not r then return false end
  local l, b, rr, t = clipOf(w)
  return min(r.r, rr) > max(r.l, l) and min(r.t, t) > max(r.b, b)
end

-- the texts a player sees on a widget, for finding it by its label
local function labelsOf(w)
  local s = S[w]
  local out = {}
  if s.name then out[#out + 1] = s.name end
  if s.fontString then out[#out + 1] = stripCodes(S[s.fontString].text) end
  for _, r in ipairs(s.regions) do
    local rs = S[r]
    if rs.kind == "FontString" and rs.shown and rs.text ~= "" then out[#out + 1] = stripCodes(rs.text) end
  end
  if s.kind == "EditBox" then out[#out + 1] = stripCodes(s.text) end
  if type(rawget(w, "tip")) == "string" then out[#out + 1] = stripCodes(w.tip) end -- an icon button: by its tooltip
  return out
end

local CLICKABLE = { Button = true, CheckButton = true, EditBox = true }

function sim.find(label, nth, kinds)
  label = stripCodes(label)
  local lower = label:lower()
  local exact, loose = {}, {}
  for _, f in ipairs(frames) do
    local s = S[f]
    if (kinds or CLICKABLE)[s.kind] and isVisible(f) and onScreen(f) then
      for _, text in ipairs(labelsOf(f)) do
        if text == label then exact[#exact + 1] = f break
        elseif text:lower() == lower or (#lower > 2 and text:lower():find(lower, 1, true)) then
          loose[#loose + 1] = f
          break
        end
      end
    end
  end
  local list = #exact > 0 and exact or loose
  return list[nth or 1], #list
end

function sim.click(w, button)
  local s = st(w)
  button = button or "LeftButton"
  if s.kind == "EditBox" then w:SetFocus() return true end
  if s.disabled then return false, "the button is disabled" end
  runScript(w, "OnMouseDown", button)
  w:Click(button)
  runScript(w, "OnMouseUp", button)
  return true
end

function sim.hover(w)
  if sim.hovered and sim.hovered ~= w then sim.leave() end
  sim.hovered = w
  local s = st(w)
  if not s.disabled or s.motionDisabled or s.kind ~= "Button" then runScript(w, "OnEnter", true) end
end
function sim.leave()
  local w = sim.hovered
  sim.hovered = nil
  if w then runScript(w, "OnLeave", true) end
end

function sim.focusedEditBox() return focused end
function sim.firstEditBox()
  for _, f in ipairs(frames) do
    if S[f].kind == "EditBox" and isVisible(f) and onScreen(f) then return f end
  end
end

function sim.typeText(text)
  local box = focused or sim.firstEditBox()
  if not box then return false, "no text field is visible" end
  box:SetFocus()
  box:Insert(text)
  return true
end

function sim.key(key)
  key = tostring(key):upper()
  local box = focused
  if box then
    if key == "ENTER" then runScript(box, "OnEnterPressed")
    elseif key == "ESCAPE" then runScript(box, "OnEscapePressed")
    elseif key == "TAB" then runScript(box, "OnTabPressed")
    else
      runScript(box, "OnKeyDown", key)
      runScript(box, "OnKeyUp", key)
    end
    return true
  end
  if key == "ESCAPE" then -- closes the windows listed in UISpecialFrames
    for _, name in ipairs(UISpecialFrames) do
      local f = _G[name]
      if f and S[f] and f:IsShown() then f:Hide() return true end
    end
  end
  return true
end

function sim.wheel(delta, nth)
  local list = {}
  for _, f in ipairs(frames) do
    local s = S[f]
    if s.kind == "ScrollFrame" and s.wheel and isVisible(f) and onScreen(f) then list[#list + 1] = f end
  end
  local f = list[nth or 1]
  if not f then return false, "no scroll area is visible" end
  runScript(f, "OnMouseWheel", delta)
  return true
end

function sim.slash(line)
  local cmd, rest = tostring(line):match("^(/%S+)%s*(.-)$")
  if not cmd then return false, "not a slash command: " .. tostring(line) end
  cmd = cmd:lower()
  if cmd == "/reload" or cmd == "/rl" then ReloadUI() return true end
  for key, fn in pairs(SlashCmdList) do
    local i = 1
    while _G["SLASH_" .. key .. i] do
      if tostring(_G["SLASH_" .. key .. i]):lower() == cmd then
        call(fn, rest, nil)
        return true
      end
      i = i + 1
    end
  end
  DEFAULT_CHAT_FRAME:AddMessage("Type '/help' for a listing of a few commands.", 1, 1, 0)
  return false, "unknown slash command " .. cmd
end

-- what is on the screen as text, top to bottom (for checks without looking at a picture)
function sim.visibleTexts()
  local out = {}
  for _, w in ipairs(widgets) do
    local s = S[w]
    local text = (s.kind == "FontString" and s.text) or (s.kind == "EditBox" and s.text) or nil
    if text and text ~= "" and isVisible(w) and onScreen(w) then
      local r = rectOf(w)
      out[#out + 1] = { y = SCREEN_H - r.t, x = r.l, text = text, kind = s.kind }
    end
  end
  table.sort(out, function(a, b) if abs(a.y - b.y) > 2 then return a.y < b.y end return a.x < b.x end)
  return out
end

------------------------------------------------------------------------ drawing
-- Emits what is on the screen back to front through host.draw (pixels, y down). Frames are drawn by strata, level and
-- creation; inside a frame the backdrop first, then its textures and texts by draw layer.
local function textureVisible(w, s, owner)
  if s.layer == "HIGHLIGHT" and sim.hovered ~= owner then
    local os = S[owner]
    if not (os and os.lockHighlight) then return false end
  end
  local os = S[owner]
  if not os then return true end
  if w == os.highlightTexture then return sim.hovered == owner or os.lockHighlight == true end
  if w == os.pushedTexture then return false end
  if w == os.checkedTexture then return os.checked == true and not os.disabled end
  if w == os.disabledCheckedTexture then return os.checked == true and os.disabled == true end
  if w == os.disabledTexture then return os.disabled == true end
  if w == os.normalTexture then return not (os.disabled and os.disabledTexture) end
  return true
end

local function emitRegion(w, alpha, cl, cb, cr, ct)
  local s = S[w]
  if not s.shown then return end
  local r = rectOf(w)
  if not r then return end
  local a = alpha * (s.alpha or 1)
  local x0, y0, x1, y1 = r.l, SCREEN_H - r.t, r.r, SCREEN_H - r.b
  local c0, c1, c2, c3 = cl, SCREEN_H - ct, cr, SCREEN_H - cb
  if s.kind == "Texture" then
    if not textureVisible(w, s, s.parent) then return end
    local v = s.vertex or { 1, 1, 1, 1 }
    if s.color then
      host.draw("rect", x0, y0, x1, y1, c0, c1, c2, c3, s.color[1] * v[1], s.color[2] * v[2], s.color[3] * v[3],
        s.color[4] * v[4] * a)
    elseif s.texture or s.atlas then
      local tc = s.texCoord or { 0, 1, 0, 1 }
      host.draw("tex", x0, y0, x1, y1, c0, c1, c2, c3, v[1], v[2], v[3], v[4] * a, s.texture or ("atlas:" .. s.atlas),
        tc[1] or 0, tc[2] or 1, tc[3] or 0, tc[4] or 1)
    end
  elseif s.kind == "FontString" then
    if s.text == "" or (s.layer == "HIGHLIGHT" and sim.hovered ~= s.parent) then return end
    local path, size, flags = fontOf(s)
    local px = ppu(w)
    local col = fieldOf(s, "color", { 1, 1, 1, 1 })
    local so = fieldOf(s, "shadowOffset", { 0, 0 })
    local sc = fieldOf(s, "shadowColor", { 0, 0, 0, 1 })
    local ww = fsWrapWidth(w, s)
    host.draw("text", x0, y0, x1, y1, c0, c1, c2, c3, col[1], col[2], col[3], (col[4] or 1) * a, s.text, path,
      size * px, flags, fieldOf(s, "justifyH", "CENTER"), fieldOf(s, "justifyV", "MIDDLE"),
      (ww > 0 and s.wordWrap ~= false) and 1 or 0, fieldOf(s, "spacing", 0) * px, s.maxLines or 0, ww,
      so[1] * px, -so[2] * px, sc[1], sc[2], sc[3], (sc[4] or 1) * a, 0)
  end
end

local function emitFrame(f, alpha)
  local s = S[f]
  local r = rectOf(f)
  local cl, cb, cr, ct = clipOf(f)
  if r and s.backdrop then
    local x0, y0, x1, y1 = r.l, SCREEN_H - r.t, r.r, SCREEN_H - r.b
    local c0, c1, c2, c3 = cl, SCREEN_H - ct, cr, SCREEN_H - cb
    local bg, bc, bd = s.bgColor or { 1, 1, 1, 1 }, s.borderColor or { 1, 1, 1, 1 }, s.backdrop
    local px = ppu(f)
    local ins = bd.insets
    if bd.bgFile then
      local il, ir, it, ib = 0, 0, 0, 0
      if type(ins) == "table" then il, ir, it, ib = (ins.left or 0) * px, (ins.right or 0) * px, (ins.top or 0) * px, (ins.bottom or 0) * px end
      local kind = tostring(bd.bgFile):lower():find("white8x8", 1, true) and "rect" or "tex"
      if kind == "rect" then
        host.draw("rect", x0 + il, y0 + it, x1 - ir, y1 - ib, c0, c1, c2, c3, bg[1], bg[2], bg[3], (bg[4] or 1) * alpha)
      else
        host.draw("tex", x0 + il, y0 + it, x1 - ir, y1 - ib, c0, c1, c2, c3, bg[1], bg[2], bg[3], (bg[4] or 1) * alpha,
          bd.bgFile, 0, 1, 0, 1)
      end
    end
    if bd.edgeFile and (bd.edgeSize or 0) > 0 then
      host.draw("edge", x0, y0, x1, y1, c0, c1, c2, c3, bc[1], bc[2], bc[3], (bc[4] or 1) * alpha,
        max(1, bd.edgeSize * px), bd.edgeFile)
    end
  end
  local regs = {}
  for i, w in ipairs(s.regions) do regs[i] = w end
  table.sort(regs, function(a, b)
    local sa, sb = S[a], S[b]
    local la, lb = LAYERS[sa.layer] or 3, LAYERS[sb.layer] or 3
    if la ~= lb then return la < lb end
    if (sa.sublevel or 0) ~= (sb.sublevel or 0) then return (sa.sublevel or 0) < (sb.sublevel or 0) end
    return sa.order < sb.order
  end)
  for _, w in ipairs(regs) do emitRegion(w, alpha, cl, cb, cr, ct) end
  if s.kind == "EditBox" and r then
    local ins = s.insets or { 0, 0, 0, 0 }
    local px = ppu(f)
    local path, size, flags = fontOf(s)
    local col = fieldOf(s, "color", { 1, 1, 1, 1 })
    local x0, y0, x1, y1 = r.l + ins[1] * px, SCREEN_H - r.t + ins[3] * px, r.r - ins[2] * px, SCREEN_H - r.b - ins[4] * px
    local c0, c1, c2, c3 = max(cl, r.l), max(SCREEN_H - ct, SCREEN_H - r.t), min(cr, r.r), min(SCREEN_H - cb, SCREEN_H - r.b)
    if s.text ~= "" then
      host.draw("text", x0, y0, x1, y1, c0, c1, c2, c3, col[1], col[2], col[3], (col[4] or 1) * alpha, s.text, path,
        size * px, flags, fieldOf(s, "justifyH", "LEFT"), s.multiLine and "TOP" or "MIDDLE", s.multiLine and 1 or 0,
        0, 0, s.multiLine and (x1 - x0) or 0, 0, 0, 0, 0, 0, 0, 1)
    end
    if focused == f then
      host.draw("cursor", x0, y0, x1, y1, c0, c1, c2, c3, col[1], col[2], col[3], alpha, s.text, path, size * px,
        flags)
    end
  end
end

function sim.draw()
  settle()
  local list = {}
  for _, f in ipairs(frames) do
    if isVisible(f) and not S[f].isScreen then list[#list + 1] = f end
  end
  local strata, level = {}, {}
  for _, f in ipairs(list) do
    strata[f] = STRATA_INDEX[strataOf(f)] or 4
    level[f] = levelOf(f)
  end
  table.sort(list, function(a, b)
    if strata[a] ~= strata[b] then return strata[a] < strata[b] end
    if level[a] ~= level[b] then return level[a] < level[b] end
    return S[a].order < S[b].order
  end)
  for _, f in ipairs(list) do emitFrame(f, effAlpha(f)) end
end

-- the part of the screen a frame and everything shown on it cover (pixels, y down), overflow included
function sim.boundsPx(f)
  local l, b, r, t
  local function add(w)
    local s = S[w]
    if not s.shown then return end
    local rr = rectOf(w)
    if rr then
      local cl, cb, cr, ct = clipOf(w)
      local x0, x1, y0, y1 = max(rr.l, cl), min(rr.r, cr), max(rr.b, cb), min(rr.t, ct)
      if x1 > x0 and y1 > y0 then
        l, r = l and min(l, x0) or x0, r and max(r, x1) or x1
        b, t = b and min(b, y0) or y0, t and max(t, y1) or y1
      end
    end
    if not s.isRegion then
      for _, c in ipairs(s.children) do add(c) end
      for _, c in ipairs(s.regions) do add(c) end
    end
  end
  add(f)
  if not l then return nil end
  return l, SCREEN_H - t, r, SCREEN_H - b
end

return sim
