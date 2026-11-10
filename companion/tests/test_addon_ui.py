"""The add-on's window in the add-on simulator (the whole add-on, as the game loads it): it builds, the Settings tab
sends no AI settings (they are chosen in the app window) and shows the AI the app reported."""

from datetime import datetime

from addonsim import Sim, install_game

# every Button and CheckButton on the Settings tab, in the order they were made, by their label
CONTROLS = r"""
local info
local function find(f)
  for _, r in ipairs({ f:GetRegions() }) do
    if r:GetObjectType() == "FontString" and (r:GetText() or ""):find("^In use:") then return r end
  end
end
local f = EnumerateFrames()
while f and not info do info = find(f) f = EnumerateFrames(f) end
local out = {}
local function walk(frame)
  for _, c in ipairs({ frame:GetChildren() }) do
    local kind = c:GetObjectType()
    if kind == "Button" then
      out[#out + 1] = c:GetText() or "?"
    elseif kind == "CheckButton" then
      local label
      for _, r in ipairs({ c:GetRegions() }) do
        if r:GetObjectType() == "FontString" and r:GetText() then label = r:GetText() end
      end
      out[#out + 1] = "[x]" .. (label or "?")
    end
    walk(c)
  end
end
walk(info:GetParent())
return table.concat(out, "\n")
"""


# the grid of data check boxes: how many, its height, how low its lowest box reaches, and whether the next heading
# ("Appearance") starts below it
GRID = r"""
local grid
local f = EnumerateFrames()
while f and not grid do
  if f:GetObjectType() == "CheckButton" then
    for _, r in ipairs({ f:GetRegions() }) do
      if r:GetObjectType() == "FontString" and r:GetText() == "Professions" then grid = f:GetParent() end
    end
  end
  f = EnumerateFrames(f)
end
local boxes, lowest = 0, 0
for _, c in ipairs({ grid:GetChildren() }) do
  if c:GetObjectType() == "CheckButton" then
    boxes = boxes + 1
    lowest = math.max(lowest, grid:GetTop() - c:GetBottom())
  end
end
local appearance
for _, r in ipairs({ grid:GetParent():GetRegions() }) do
  if r:GetObjectType() == "FontString" and r:GetText() == "Appearance" then appearance = r end
end
return boxes, grid:GetHeight(), lowest, appearance:GetTop() <= grid:GetBottom()
"""


def _ai_line(sim):
    return next(t for t in sim.texts() if t.startswith("In use:"))


def test_settings_tab_sends_no_ai_settings_and_shows_the_ai_in_use(tmp_path):
    install_game(tmp_path / "game", pool=40)
    sim = Sim(tmp_path / "game", clock=datetime(2026, 11, 10, 12, 0)).start()
    sim.slash("/fai")
    sim.click("Settings")
    assert "not known yet" in _ai_line(sim)
    controls = sim.lua(CONTROLS).decode().split("\n")
    # no provider, model, thinking level, key or web search controls are left in the game
    assert controls[0] == "Refresh" and "Center window" in controls and "13" in controls
    assert "[x]Bags & bank" in controls
    for gone in ("OpenRouter", "Gemini", "Claude Code (subscription)", "Set", "Save", "Auto", "Always", "Never", "low",
                 "max"):
        assert gone not in controls
    # every control, as a player clicks it; the data check boxes twice, so they end as they were
    for label in controls:
        if label.startswith("[x]"):
            sim.click(label[3:])
            sim.click(label[3:])
        else:
            sim.click(label)
    sim.run(1)
    sent = sim.sent_messages()
    assert [m["type"] for m in sent] == ["status"]  # Refresh asks the app for the AI in use; nothing else goes out
    sections = sim.lua("local s = ForeverAIDB.sections return s.bags, s.chat, s.progress, s.professions")
    assert sections == (True, False, True, True)  # the data check boxes were clicked back
    assert "[x]Progress" in controls and "[x]Professions" in controls
    # the data grid grows with its check boxes: the last row fits and the next heading comes below it
    boxes, grid_h, lowest, below = sim.lua(GRID)
    assert boxes == 13 and grid_h == 5 * 24 and lowest <= grid_h and below is True
    # the progress box: off stops the section, on again restores it
    sim.click("Progress")
    assert sim.lua("return ForeverAIDB.sections.progress") is False
    sim.click("Progress")
    # /fai progress and /fai professions print what was collected; they are not questions
    chat = len(sim.chat)
    sim.slash("/fai progress")
    sim.slash("/fai   professions ")
    sim.run(1)
    assert len(sim.chat) > chat and sim.sent_messages() == []

    # the app answers the status request (through the inboxes, as the app does)
    sim.deliver({"kind": "status", "final": True, "msgId": sent[0]["msgId"],
                 "status": {"provider": "claude_code", "claude": {"model": "claude-haiku-5-5", "effort": "high"},
                            "searchMode": "auto", "model": "openai/gpt-6-luna",
                            "gemini": {"model": "gemini-flash-latest"},
                            "memory": [{"id": 1, "text": "likes fishing"}]}})
    sim.run(4)
    assert _ai_line(sim) == "In use: Claude Code · haiku 5 5 · thinking high  ·  web search: auto"
    assert "Claude  haiku 5 5  ·  high" in sim.texts()  # the header
    assert sim.lua("return ForeverAI.pending") is None

    # the AI was changed in the app window: the first reply to the next question says so
    sim.ask("hi")
    sim.run(1)
    q = sim.sent_messages()[-1]
    assert q["type"] == "question" and q["text"] == "hi"
    sim.deliver({"kind": "answer", "final": False, "msgId": q["msgId"], "text": "", "phase": "Thinking…",
                 "ai": {"provider": "gemini", "model": "openai/gpt-6-luna", "searchMode": "never",
                        "claude": {"model": "claude-haiku-5-5", "effort": "high"},
                        "gemini": {"model": "gemini-3.8-flash"}}})
    sim.run(4)
    assert "Gemini  3.8-flash" in sim.texts()
    sim.click("Settings")
    assert _ai_line(sim) == "In use: Google Gemini · gemini-3.8-flash  ·  web search: off"
    assert sim.lua("return ForeverAIDB.status.memory[1].text") == b"likes fishing"
    assert sim.lua("return ForeverAI.pending ~= nil") is True

    # a narrow window: the long lines wrap and the rows below move down
    content = "local c = ... return c:GetWidth(), c:GetHeight()"
    info_width = """
      local f = EnumerateFrames()
      while f do
        for _, r in ipairs({ f:GetRegions() }) do
          if r:GetObjectType() == "FontString" and (r:GetText() or ""):find("^In use:") then
            return r:GetWidth(), r:GetParent()
          end
        end
        f = EnumerateFrames(f)
      end"""
    width, child = sim.lua(info_width)
    child_w, wide = sim.lua(content, child)
    assert abs(width - (child_w - 10)) < 0.01
    sim.lua("ForeverAIFrame:SetSize(250, 600)")
    sim.step(2)
    width, _ = sim.lua(info_width)
    child_w, narrow = sim.lua(content, child)
    assert abs(width - (child_w - 10)) < 0.01 and narrow > wide > 0
    assert sim.errors == []
    sim.close()
