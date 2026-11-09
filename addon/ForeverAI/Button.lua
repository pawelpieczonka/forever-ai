-- ForeverAI: always-visible, draggable button that opens the chat (+ AddOn compartment entry)
local ADDON, ns = ...
local B = {}
ns.Button = B
local btn

function B.Init()
  if btn then return end
  local fontPath = (ChatFontNormal and ChatFontNormal:GetFont()) or "Fonts\\ARIALN.TTF"
  btn = CreateFrame("Button", "ForeverAIButton", UIParent, "BackdropTemplate")
  btn:SetSize(38, 38)
  btn:SetFrameStrata("MEDIUM")
  btn:SetFrameLevel(20)
  btn:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8x8", edgeFile = "Interface\\Buttons\\WHITE8x8", edgeSize = 1 })
  btn:SetBackdropColor(0.05, 0.06, 0.08, 0.92)
  btn:SetBackdropBorderColor(0.27, 0.86, 0.62, 0.85)
  btn.label = btn:CreateFontString(nil, "OVERLAY")
  btn.label:SetFont(fontPath, 15, "OUTLINE")
  btn.label:SetText("AI")
  btn.label:SetTextColor(0.35, 1, 0.72)
  btn.label:SetPoint("CENTER", 0, 0)
  btn.badge = btn:CreateTexture(nil, "OVERLAY")
  btn.badge:SetSize(9, 9)
  btn.badge:SetPoint("TOPRIGHT", -3, -3)
  btn.badge:SetColorTexture(1, 0.62, 0.15, 1)
  btn.badge:Hide()
  local pulse = btn.badge:CreateAnimationGroup()
  local a = pulse:CreateAnimation("Alpha")
  a:SetFromAlpha(1) a:SetToAlpha(0.25) a:SetDuration(0.6)
  pulse:SetLooping("BOUNCE")
  btn.pulse = pulse

  btn:SetMovable(true)
  btn:SetClampedToScreen(true)
  btn:RegisterForDrag("LeftButton")
  btn:RegisterForClicks("LeftButtonUp", "RightButtonUp")
  btn:SetScript("OnDragStart", function(self) self:StartMoving() end)
  btn:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    local point, _, _, x, y = self:GetPoint()
    ns.db.button.point, ns.db.button.x, ns.db.button.y = point, x, y
  end)
  btn:SetScript("OnClick", function(_, mouse)
    if mouse == "RightButton" then
      ns.UI.Show()
      ns.UI.ShowTab("settings")
    else
      ForeverAI_Toggle()
    end
  end)
  btn:SetScript("OnEnter", function(self)
    self:SetBackdropBorderColor(0.45, 1, 0.78, 1)
    GameTooltip:SetOwner(self, "ANCHOR_LEFT")
    GameTooltip:SetText("ForeverAI", 0.35, 1, 0.72)
    local s = ns.db.status or {}
    if s.provider == "claude_code" then
      GameTooltip:AddLine("Claude · " .. tostring(s.claude and s.claude.model or "?"):gsub("^claude%-", ""):gsub("%-", " "), 0.85, 0.47, 0.34)
    elseif s.provider == "gemini" then
      GameTooltip:AddLine("Gemini · " .. tostring(s.gemini and s.gemini.model or "?"), 0.54, 0.71, 0.97)
    elseif s.model then
      GameTooltip:AddLine("OpenRouter · " .. s.model, 0.8, 0.8, 0.8)
    end
    if ns.pending then GameTooltip:AddLine("Answering…", 1, 0.8, 0.3) end
    GameTooltip:AddLine("Click: open / close", 1, 1, 1)
    GameTooltip:AddLine("Right-click: settings", 0.7, 0.7, 0.7)
    GameTooltip:AddLine("Drag: move", 0.7, 0.7, 0.7)
    ns.ApplyTooltipFont()
  end)
  btn:SetScript("OnLeave", function(self)
    self:SetBackdropBorderColor(0.27, 0.86, 0.62, 0.85)
    GameTooltip:Hide()
  end)
  B.Refresh()
end

function B.Refresh()
  if not btn then return end
  local p = ns.db.button
  btn:ClearAllPoints()
  btn:SetPoint(p.point or "TOPRIGHT", UIParent, p.point or "TOPRIGHT", p.x or -24, p.y or -250)
  btn:SetShown(p.show ~= false)
end

function B.SetBadge(on)
  if not btn then return end
  btn.badge:SetShown(on)
  if on then btn.pulse:Play() else btn.pulse:Stop() end
end

function B.SetBusy(on)
  if not btn then return end
  btn.label:SetText(on and "..." or "AI")
end

-- AddOn compartment (the addons menu next to the minimap)
function ForeverAI_CompartmentClick(_, mouse)
  if mouse == "RightButton" then
    ns.UI.Show()
    ns.UI.ShowTab("settings")
  else
    ForeverAI_Toggle()
  end
end
