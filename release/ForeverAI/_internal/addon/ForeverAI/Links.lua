-- ForeverAI: clickable item/spell/quest/NPC links inside answers
local ADDON, ns = ...
local L = {}
ns.Links = L

local ITEM_COLOR = "|cff66ccff"
local SPELL_MAX_ID = 90000 -- classic-era spells; newer Forever spells come with ids from the knowledge base
local SPELLS_PER_FRAME = 3000

------------------------------------------------------------------------ spell name index (built in the background)
local spellIndex = {}
local rankOne = {}
local indexDone = false
function L.BuildSpellIndex()
  if indexDone or L.building or not C_Spell or not C_Spell.GetSpellName then return end
  L.building = true
  local id = 1
  local ticker
  ticker = C_Timer.NewTicker(0, function()
    local last = math.min(SPELL_MAX_ID, id + SPELLS_PER_FRAME)
    for i = id, last do
      local ok, name = pcall(C_Spell.GetSpellName, i)
      if ok and type(name) == "string" and name ~= "" then
        local have = spellIndex[name]
        if not have then
          spellIndex[name] = i
        elseif not rankOne[name] and C_Spell.GetSpellSubtext then
          -- several spells share a name (NPC versions, internal effects): prefer the player's "Rank 1"
          local okS, sub = pcall(C_Spell.GetSpellSubtext, i)
          if okS and sub == "Rank 1" then spellIndex[name], rankOne[name] = i, true end
        end
      end
    end
    id = last + 1
    if id > SPELL_MAX_ID then
      ticker:Cancel()
      indexDone, L.building = true, false
      wipe(rankOne)
      L.spellCount = 0
      for _ in pairs(spellIndex) do L.spellCount = L.spellCount + 1 end
    end
  end)
end

local function spellLink(id, name)
  local link = C_Spell.GetSpellLink and ns.safe(C_Spell.GetSpellLink, id)
  return link or ("|cff71d5ff|Hspell:" .. id .. ":0|h[" .. name .. "]|h|r")
end

------------------------------------------------------------------------ resolving names
-- order: own items/spells/quests -> ids from the knowledge base -> client item cache -> spell index -> quest NPCs
function L.Resolve(name, kind, id)
  local known = ns.Collect and ns.Collect.knownLinks and ns.Collect.knownLinks[name]
  if known then return known end
  if id then
    if kind == "item" then
      local _, link = C_Item.GetItemInfo(id)
      if link then return link end
      if C_Item.RequestLoadItemDataByID then pcall(C_Item.RequestLoadItemDataByID, id) end
      return ITEM_COLOR .. "|Hitem:" .. id .. "|h[" .. name .. "]|h|r"
    elseif kind == "spell" then
      return spellLink(id, name)
    elseif kind == "quest" then
      return "|cffffff00|Hquest:" .. id .. ":0|h[" .. name .. "]|h|r"
    end
  end
  local _, itemLink = ns.safe(C_Item.GetItemInfo, name)
  if itemLink then return itemLink end
  local own = C_Spell.GetSpellLink and ns.safe(C_Spell.GetSpellLink, name)
  if own then return own end
  local sid = spellIndex[name]
  if sid then return spellLink(sid, name) end
  local npc = ns.Collect and ns.Collect.npcCoords and ns.Collect.npcCoords[name]
  if npc then
    return string.format("|cffffd27a|Hforeverai:npc:%s|h[%s]|h|r", name, name)
  end
  return nil
end

function L.Linkify(text, links)
  if type(text) ~= "string" or text == "" then return text end
  local byName = {}
  for _, l in ipairs(links or {}) do byName[l.name] = l end
  return (text:gsub("|cff66ccff%[([^%]|]+)%]|r", function(name)
    local l = byName[name]
    local ok, link = pcall(L.Resolve, name, l and l.kind, l and l.id)
    if ok and link then return link end
    return ITEM_COLOR .. "[" .. name .. "]|r"
  end))
end

------------------------------------------------------------------------ hover / click
local function customTooltip(owner, link)
  local kind, name = link:match("^foreverai:(%w+):(.+)$")
  if kind ~= "npc" then return false end
  local c = ns.Collect.npcCoords[name]
  GameTooltip:SetOwner(owner, "ANCHOR_CURSOR_RIGHT", 12, 0)
  GameTooltip:SetText(name, 1, 0.82, 0.48)
  if c then GameTooltip:AddLine(string.format("%s %.1f, %.1f", c.zone or "", c.x, c.y), 0.8, 0.8, 0.8) end
  GameTooltip:AddLine("Click to mark on the map", 0.4, 1, 0.7)
  ns.ApplyTooltipFont()
  return true
end

function L.Attach(frame)
  if not frame.SetHyperlinksEnabled then return end
  frame:SetHyperlinksEnabled(true)
  frame:SetScript("OnHyperlinkEnter", function(self, link)
    if link:find("^foreverai:") then customTooltip(self, link) return end
    GameTooltip:SetOwner(self, "ANCHOR_CURSOR_RIGHT", 12, 0)
    if pcall(GameTooltip.SetHyperlink, GameTooltip, link) then GameTooltip:Show() else GameTooltip:Hide() end
  end)
  frame:SetScript("OnHyperlinkLeave", function() GameTooltip:Hide() end)
  frame:SetScript("OnHyperlinkClick", function(self, link, text, button)
    local kind, name = link:match("^foreverai:(%w+):(.+)$")
    if kind == "npc" then
      local c = ns.Collect.npcCoords[name]
      if c then ns.UI.MarksCard({ { title = name, x = c.x, y = c.y, zone = c.zone } }) end
      return
    end
    if SetItemRef then pcall(SetItemRef, link, text, button, self) end
  end)
end
