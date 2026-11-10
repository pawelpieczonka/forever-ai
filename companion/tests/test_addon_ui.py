"""The add-on's window (Core.lua + UI.lua) against a mocked game: it builds, the Settings tab sends no AI settings
(they are chosen in the app window) and shows the AI the app reported."""

import shutil
import subprocess
from pathlib import Path

import pytest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "ForeverAI"

LUA_HARNESS = r"""
local addonDir = arg[1]
local function noop() end

-- widgets: a method the mock does not know is a no-op (WoW methods start with a capital letter); fields stay nil
local M, all = {}, {}
local W = { __index = function(_, k)
  if M[k] then return M[k] end
  if type(k) == "string" and k:match("^%u") then return noop end
end }
local function widget(kind, parent, name)
  local w = setmetatable({ kind = kind, parent = parent, shown = true, text = "", w = 420, h = 20, scripts = {}, kids = {} }, W)
  if parent then table.insert(parent.kids, w) end
  all[#all + 1] = w
  if name then w.name = name _G[name] = w end
  return w
end
function M:GetObjectType() return self.kind end
function M:SetText(t) self.text = t ~= nil and tostring(t) or "" end
function M:GetText() return self.text end
function M:Show() self.shown = true if self.scripts.OnShow then self.scripts.OnShow(self) end end
function M:Hide() self.shown = false if self.scripts.OnHide then self.scripts.OnHide(self) end end
function M:SetShown(on) if on then self:Show() else self:Hide() end end
function M:IsShown() return self.shown end
function M:SetSize(w, h) self.w, self.h = w, h end
function M:SetWidth(w) self.w = w end
function M:SetHeight(h) self.h = h end
function M:GetWidth() return self.w end
function M:GetHeight() return self.h end
function M:GetStringWidth() return #self.text * 6 end
function M:GetStringHeight() return math.max(1, math.ceil(#self.text * 6 / (self.w > 0 and self.w or 1e9))) * 14 end
function M:SetScript(k, fn) self.scripts[k] = fn end
function M:GetScript(k) return self.scripts[k] end
function M:HookScript(k, fn)
  local old = self.scripts[k]
  self.scripts[k] = function(...) if old then old(...) end fn(...) end
end
function M:CreateFontString() return widget("FontString", self) end
function M:CreateTexture() return widget("Texture", self) end
function M:SetFontString(fs) self.fs = fs end
function M:GetFontString() return self.fs end
function M:IsEnabled() return self.enabled ~= false end
function M:SetEnabled(on) self.enabled = on end
function M:GetChecked() return self.checked end
function M:SetChecked(v) self.checked = v end
function M:GetVerticalScrollRange() return 0 end
function M:GetVerticalScroll() return 0 end
function M:GetPoint() return "CENTER", nil, "CENTER", 0, 0 end
function M:GetFont() return "Fonts\\ARIALN.TTF", 12, "" end
function M:GetParent() return self.parent end
function M:IsProtected() return false end
function M:NumLines() return 0 end
function M:GetName() return self.name end
function M:RegisterEvent() return true end

-- the game
local timers = {}
CreateFrame = function(kind, name, parent) return widget(kind, parent, name) end
CreateFont = function(name) return widget("Font", nil, name) end
ChatFontNormal, UIParent, GameTooltip, DEFAULT_CHAT_FRAME = widget("Font"), widget("Frame"), widget("GameTooltip"), widget("Frame")
UISpecialFrames, tinsert, SlashCmdList = {}, table.insert, {}
wipe = function(t) for k in pairs(t) do t[k] = nil end return t end
hooksecurefunc = function(a, b, c)
  if type(a) == "table" then
    local old = a[b]
    a[b] = function(...) local r = { old(...) } c(...) return unpack(r) end
  end
end
C_Timer = { After = function(_, fn) timers[#timers + 1] = fn end,
            NewTimer = function(_, fn) return { Cancel = noop } end }
local function flush() for _ = 1, 20 do local t = timers timers = {} if #t == 0 then return end for _, fn in ipairs(t) do fn() end end end
date, time = os.date, os.time
GetTime = function() return os.clock() end
debugprofilestop = function() return os.clock() * 1000 end
UnitName = function() return "Tester" end
GetRealmName = function() return "Forever" end
ReloadUI = noop

local ns = {}
for _, file in ipairs({ "Core.lua", "UI.lua" }) do assert(loadfile(addonDir .. "/" .. file))("ForeverAI", ns) end
local sent = {}
ns.Transport = { nextInbox = 1, InboxLeft = function() return 500 end, StartPolling = noop, StopPolling = noop,
  Send = function(msg, done) sent[#sent + 1] = msg done(true) end }
ns.Collect = { SnapshotAsync = function(done) done({}, {}) end }
ns.Button = { SetBusy = noop, SetBadge = noop, Refresh = noop }
ns.Links = { Linkify = function(s) return s end, Attach = noop }
ns.Map = { Apply = noop, Clear = noop, Show = noop }
ns.eventFrame.scripts.OnEvent(ns.eventFrame, "ADDON_LOADED", "ForeverAI")
ns.session, ns.sigPrefix = "S", "sS"
ns.UI.Init()
ns.UI.Show()
flush()

local function find(kind, pattern)
  for _, w in ipairs(all) do
    if w.kind == kind and w.text:find(pattern) then return w end
  end
end
local function descendants(w, out)
  for _, k in ipairs(w.kids) do out[#out + 1] = k descendants(k, out) end
  return out
end
local function out(key, value) print(key .. " " .. tostring(value)) end

ns.UI.ShowTab("settings")
local info = assert(find("FontString", "^In use:"), "no AI line in Settings")
out("BEFORE", info.text)
-- every control of the Settings tab, as a player would click it
local panel = info.parent
local labels = {}
for _, w in ipairs(descendants(panel, {})) do
  if (w.kind == "Button" or w.kind == "CheckButton") and w.scripts.OnClick then
    labels[#labels + 1] = w.kind == "Button" and w.text or ("[x]" .. ((w.kids[1] or {}).text or "?"))
    if w.kind == "CheckButton" then -- the game flips the box before OnClick; clicked twice, it is back
      if w.scripts.OnShow then w.scripts.OnShow(w) end
      for _ = 1, 2 do w.checked = not w.checked w.scripts.OnClick(w, "LeftButton") end
    else
      w.scripts.OnClick(w, "LeftButton")
    end
  end
end
flush()
out("CONTROLS", table.concat(labels, ","))
local types = {}
for _, m in ipairs(sent) do types[#types + 1] = m.type end
out("SENT", table.concat(types, ","))
out("SECTIONS", tostring(ns.db.sections.bags) .. "," .. tostring(ns.db.sections.chat))

-- the app answers the status request
ns.OnMessage({ kind = "status", final = true, msgId = sent[1] and sent[1].msgId,
  status = { provider = "claude_code", claude = { model = "claude-haiku-5-5", effort = "high" }, searchMode = "auto",
             model = "openai/gpt-6-luna", gemini = { model = "gemini-flash-latest" }, memory = { { id = 1, text = "likes fishing" } } } })
flush()
out("AFTER", info.text)
out("PILL", (find("FontString", "Claude|r") or {}).text)
out("PENDING", tostring(ns.pending))

-- the AI was changed in the app window: the first reply to the next question says so
ns.Ask("hi")
flush()
local q = sent[#sent]
out("ASKED", q.type)
ns.OnMessage({ kind = "answer", final = false, msgId = q.msgId, text = "", phase = "Thinking…",
  ai = { provider = "gemini", model = "openai/gpt-6-luna", searchMode = "never", claude = { model = "claude-haiku-5-5", effort = "high" },
         gemini = { model = "gemini-3.8-flash" } } })
flush()
ns.UI.ShowTab("settings")
out("AI", info.text)
out("PILL2", (find("FontString", "Gemini|r") or {}).text)
out("MEMORY", ns.db.status.memory and ns.db.status.memory[1].text)
out("STILLPENDING", tostring(ns.pending ~= nil))
-- a narrow window: the long lines wrap and the rows below move down
local wide = panel.h
panel.w = 120
ns.UI.RefreshSettings()
out("NARROW", info.w .. "," .. wide .. "," .. panel.h)
"""


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="needs lua5.1")
def test_settings_tab_sends_no_ai_settings_and_shows_the_ai_in_use(tmp_path):
    script = tmp_path / "harness.lua"
    script.write_text(LUA_HARNESS)
    run = subprocess.run(["lua5.1", str(script), str(ADDON)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr + run.stdout
    lines = dict(line.split(" ", 1) for line in run.stdout.strip().splitlines())
    assert "not known yet" in lines["BEFORE"]
    controls = lines["CONTROLS"].split(",")
    # no provider, model, thinking level, key or web search controls are left in the game
    assert controls[0] == "Refresh" and "Center window" in controls and "13" in controls and "[x]Bags & bank" in controls
    for gone in ("OpenRouter", "Gemini", "Claude Code (subscription)", "Set", "Save", "Auto", "Always", "Never", "low",
                 "max"):
        assert gone not in controls
    assert lines["SENT"] == "status"  # Refresh asks the app for the AI in use; nothing else goes out
    assert lines["SECTIONS"] == "true,false"  # the data check boxes were clicked back
    assert lines["AFTER"] == "In use: |cffe6e9ecClaude Code · haiku 5 5 · thinking high|r  ·  web search: auto"
    assert "haiku 5 5" in lines["PILL"] and lines["PENDING"] == "nil"
    assert lines["ASKED"] == "question"
    assert lines["AI"] == "In use: |cffe6e9ecGoogle Gemini · gemini-3.8-flash|r  ·  web search: off"
    assert "3.8-flash" in lines["PILL2"]
    assert lines["MEMORY"] == "likes fishing" and lines["STILLPENDING"] == "true"
    width, wide, narrow = (int(x) for x in lines["NARROW"].split(","))
    assert width == 110 and narrow > wide > 0
