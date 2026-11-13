-- ForeverAI: game state snapshot (everything the AI may need, as plain data)
local ADDON, ns = ...
local C = {}
ns.Collect = C
local safe, clean, round, strip = ns.safe, ns.clean, ns.round, ns.stripCodes

local SLOT_NAMES = { "Head", "Neck", "Shoulder", "Shirt", "Chest", "Waist", "Legs", "Feet", "Wrist", "Hands",
  "Finger1", "Finger2", "Trinket1", "Trinket2", "Back", "MainHand", "OffHand", "Ranged", "Tabard" }

-- item quality name as the game itself names it ("Poor", "Uncommon"...)
local function qualityName(q)
  if q == nil then return nil end
  local s = _G["ITEM_QUALITY" .. tostring(q) .. "_DESC"]
  return type(s) == "string" and s:lower() or tostring(q)
end

------------------------------------------------------------------------ frame budget
-- The snapshot is gathered in the background (C.SnapshotAsync): every section runs in a coroutine and its long loops
-- call C.Budget(), which hands the rest of the work to the next frame once this frame has spent BUDGET_MS on it.
-- Called outside a coroutine (C.Snapshot), it does nothing.
local BUDGET_MS = 4
local sliceStart = 0
function C.Budget()
  if coroutine.running() and debugprofilestop() - sliceStart > BUDGET_MS then coroutine.yield() end
end
local budget = C.Budget

------------------------------------------------------------------------ real links of the player's own things (for clickable answers)
C.knownLinks = {}
C.npcCoords = {} -- quest NPC name -> {zone, x, y} (from Questie), for clickable NPC names

function C.Remember(link)
  if type(link) ~= "string" then return end
  local name = link:match("|h%[(.-)%]|h")
  if name and name ~= "" then C.knownLinks[name] = link end
end

function C.RefreshLinks()
  for slot = 1, 19 do C.Remember(GetInventoryItemLink("player", slot)) end
  for bag = 0, 5 do
    budget()
    for slot = 1, (safe(C_Container.GetContainerNumSlots, bag) or 0) do
      local info = safe(C_Container.GetContainerItemInfo, bag, slot)
      if info then C.Remember(info.hyperlink) end
    end
  end
  if C_QuestLog and C_QuestLog.GetNumQuestLogEntries and GetQuestLink then
    for i = 1, C_QuestLog.GetNumQuestLogEntries() do
      local q = C_QuestLog.GetInfo(i)
      if q and q.questID and not q.isHeader then C.Remember(safe(GetQuestLink, q.questID)) end
    end
  end
  if C_SpellBook and C_SpellBook.GetNumSpellBookSkillLines and C_Spell.GetSpellLink then
    for t = 1, C_SpellBook.GetNumSpellBookSkillLines() do
      budget()
      local li = C_SpellBook.GetSpellBookSkillLineInfo(t)
      if li then
        for k = li.itemIndexOffset + 1, li.itemIndexOffset + li.numSpellBookItems do
          local ii = C_SpellBook.GetSpellBookItemInfo(k, Enum.SpellBookSpellBank.Player)
          if ii and ii.spellID then C.Remember(safe(C_Spell.GetSpellLink, ii.spellID)) end
        end
      end
    end
  end
end

------------------------------------------------------------------------ tooltip helpers
local function tooltipText(data, maxLines)
  if not data or not data.lines then return nil end
  local out = {}
  for i, line in ipairs(data.lines) do
    if maxLines and i > maxLines then break end
    local l = clean(line.leftText) or ""
    local r = clean(line.rightText)
    if r and r ~= "" then l = l .. "  [" .. r .. "]" end
    if l ~= "" then out[#out + 1] = strip(l) end
  end
  return table.concat(out, "\n")
end

------------------------------------------------------------------------ item facts
local money -- defined further down (forward declaration)
-- what the tooltip text alone does not say clearly: quality, item level, required level, ALL stats (or that there are
-- none), vendor price, whether this character can wear/use it, and how it compares with what is equipped now
local EQUIP_SLOTS = {
  INVTYPE_HEAD = { 1 }, INVTYPE_NECK = { 2 }, INVTYPE_SHOULDER = { 3 }, INVTYPE_BODY = { 4 }, INVTYPE_CHEST = { 5 },
  INVTYPE_ROBE = { 5 }, INVTYPE_WAIST = { 6 }, INVTYPE_LEGS = { 7 }, INVTYPE_FEET = { 8 }, INVTYPE_WRIST = { 9 },
  INVTYPE_HAND = { 10 }, INVTYPE_FINGER = { 11, 12 }, INVTYPE_TRINKET = { 13, 14 }, INVTYPE_CLOAK = { 15 },
  INVTYPE_WEAPON = { 16, 17 }, INVTYPE_2HWEAPON = { 16 }, INVTYPE_WEAPONMAINHAND = { 16 }, INVTYPE_WEAPONOFFHAND = { 17 },
  INVTYPE_SHIELD = { 17 }, INVTYPE_HOLDABLE = { 17 }, INVTYPE_RANGED = { 18 }, INVTYPE_RANGEDRIGHT = { 18 },
  INVTYPE_THROWN = { 18 }, INVTYPE_RELIC = { 18 }, INVTYPE_TABARD = { 19 },
}
local NO_SKILL_NEEDED = { INVTYPE_NECK = true, INVTYPE_FINGER = true, INVTYPE_TRINKET = true, INVTYPE_CLOAK = true,
  INVTYPE_BODY = true, INVTYPE_TABARD = true, INVTYPE_HOLDABLE = true }
-- item types whose skill has another name: the skill window says "Swords" where the item says "One-Handed Swords",
-- "Plate Mail" for "Plate", "Shield" for "Shields"; anyone can equip a fishing pole or a miscellaneous item
local SKILL_OF_TYPE = { ["one-handed swords"] = "swords", ["one-handed axes"] = "axes", ["one-handed maces"] = "maces",
  plate = "plate mail", shields = "shield" }
local NO_SKILL_TYPE = { miscellaneous = true, ["fishing poles"] = true, ["fishing pole"] = true }

local statsCache = {} -- item link -> stats (an item's stats never change)
local function itemStats(link)
  local hit = statsCache[link]
  if hit then return hit end
  local fn = (C_Item and C_Item.GetItemStats) or GetItemStats
  local raw = fn and safe(fn, link)
  local out = {}
  for key, v in pairs(type(raw) == "table" and raw or {}) do
    if type(v) == "number" then
      local name = key == "RESISTANCE0_NAME" and "Armor" or _G[key] or key
      if tostring(name):lower():find("damage per second", 1, true) then name = "DPS" end
      out[ns.stripCodes(tostring(name))] = ns.round(v, 1)
    end
  end
  if next(out) then statsCache[link] = out end -- an empty result may only mean "not loaded yet"
  return out
end

-- item facts depend on the item, on what is equipped (comparison), on the level and on skills
local factsCache, unmetCache = {}, {}
do
  local f = CreateFrame("Frame")
  for _, e in ipairs({ "PLAYER_EQUIPMENT_CHANGED", "PLAYER_LEVEL_UP", "SKILL_LINES_CHANGED" }) do pcall(f.RegisterEvent, f, e) end
  f:SetScript("OnEvent", function() wipe(factsCache) wipe(unmetCache) end)
end

-- What the game itself marks as not met by this character: the lines of an item's tooltip it colours red (a
-- profession and the skill in it, a class, a reputation, a recipe already known). Read from the game's own tooltip,
-- so nothing here has to know what a requirement looks like. C.unmetFrom tells where the colours came from; it stays
-- nil while neither the tooltip data nor a tooltip of the add-on's own gave any.
local scanTip, scanTipTried
local function isRed(r, g, b) return type(r) == "number" and r > 0.9 and (g or 1) < 0.3 and (b or 1) < 0.3 end
local function unmet(link)
  local hit = unmetCache[link]
  if hit then return hit end
  local out, lines, colours = {}, 0, false
  local data = C_TooltipInfo and C_TooltipInfo.GetHyperlink and safe(C_TooltipInfo.GetHyperlink, link)
  for _, line in ipairs(type(data) == "table" and type(data.lines) == "table" and data.lines or {}) do
    lines = lines + 1
    for _, side in ipairs({ "left", "right" }) do
      local c = line[side .. "Color"]
      if type(c) == "table" then
        colours = true
        local text = clean(line[side .. "Text"])
        if text and text ~= "" and isRed(c.r, c.g, c.b) then out[#out + 1] = strip(text) end
      end
    end
  end
  if colours then
    C.unmetFrom = "tooltip data"
  else
    -- the tooltip data of this client carries no colours: a tooltip of the add-on's own, never shown, has them
    if not scanTipTried then
      scanTipTried = true
      local ok, tip = pcall(CreateFrame, "GameTooltip", "ForeverAIScanTooltip", nil, "GameTooltipTemplate")
      scanTip = ok and type(tip) == "table" and tip.SetHyperlink and tip.NumLines and tip or nil
    end
    if scanTip and pcall(function()
      scanTip:SetOwner(WorldFrame, "ANCHOR_NONE")
      scanTip:ClearLines()
      scanTip:SetHyperlink(link)
    end) then
      lines = 0
      for i = 1, safe(scanTip.NumLines, scanTip) or 0 do
        lines = lines + 1
        for _, side in ipairs({ "Left", "Right" }) do
          local fs = _G["ForeverAIScanTooltipText" .. side .. i]
          local text = fs and fs.GetText and fs.GetTextColor and (side == "Left" or (fs.IsShown and fs:IsShown()))
            and clean(fs:GetText())
          if text and text ~= "" then
            colours = true
            if isRed(fs:GetTextColor()) then out[#out + 1] = strip(text) end
          end
        end
      end
      pcall(scanTip.Hide, scanTip)
      if colours then C.unmetFrom = "hidden tooltip" end
    end
  end
  if lines == 0 or not colours then return nil end -- nothing read (the item's text is not there yet): asked again
  unmetCache[link] = out
  return out
end

local skillSetCache, skillSetAt = nil, 0
local function skillSet()
  if skillSetCache and GetTime() - skillSetAt < 5 then return skillSetCache end
  local set = {}
  if C_SkillInfo and C_SkillInfo.GetNumSkillLines then
    for i = 1, math.min(80, safe(C_SkillInfo.GetNumSkillLines) or 0) do
      local info = safe(C_SkillInfo.GetSkillLineInfo, i)
      local name = type(info) == "table" and (info.name or info.skillName)
      if name then set[name:lower()] = true end
    end
  end
  if not next(set) and GetNumSkillLines and GetSkillLineInfo then -- the Classic skill window, as S.skills reads it
    for i = 1, math.min(80, safe(GetNumSkillLines) or 0) do
      local name, header = safe(GetSkillLineInfo, i)
      if type(name) == "string" and not header then set[name:lower()] = true end
    end
  end
  skillSetCache, skillSetAt = set, GetTime()
  return set
end

function C.ItemFacts(link, compare)
  if not link then return nil end
  local cacheKey = link .. (compare and "#cmp" or "")
  if factsCache[cacheKey] then return factsCache[cacheKey] end
  local name, _, quality, ilvl, reqLevel, _, subType, _, equipLoc, _, sellPrice, classID = safe(C_Item.GetItemInfo, link)
  if not name then return nil end -- not in the client cache yet
  local f = { quality = qualityName(quality or 1), ilvl = ilvl }
  if reqLevel and reqLevel > 1 then
    f.requiresLevel = reqLevel
    if reqLevel > (UnitLevel("player") or 1) then f.tooLowLevel = true end
  end
  if sellPrice and sellPrice > 0 then f.sellPrice = money(sellPrice) elseif sellPrice == 0 then f.sellPrice = "cannot be sold" end
  local stats = itemStats(link)
  local list = {}
  for k, v in pairs(stats) do if k ~= "Armor" then list[#list + 1] = string.format("%s %s%s", k, v > 0 and "+" or "", v) end end
  table.sort(list)
  local why, noSkill = {}, nil -- why this character cannot use it
  if equipLoc and equipLoc ~= "" and equipLoc ~= "INVTYPE_NON_EQUIP_IGNORE" then
    f.stats = #list > 0 and table.concat(list, ", ") or "NONE — this item has no stats (read from the game)"
    if stats.Armor then f.armor = stats.Armor end
    -- can this character use it? armor and weapon types need the matching skill (Leather, Mail, Bows, Daggers...)
    if (classID == 2 or classID == 4) and not NO_SKILL_NEEDED[equipLoc] and subType and subType ~= "" then
      local need = subType:lower()
      local have = skillSet()
      local ok = have[need] or have[SKILL_OF_TYPE[need] or need] or NO_SKILL_TYPE[need]
      if next(have) and not ok then
        noSkill = subType
        why[#why + 1] = "missing skill: " .. subType
      end
    end
    if compare then
      local diffs = {}
      for _, slot in ipairs(EQUIP_SLOTS[equipLoc] or {}) do
        local cur = GetInventoryItemLink("player", slot)
        local cs = cur and itemStats(cur) or {}
        local d = {}
        local keys = {}
        for k in pairs(stats) do keys[k] = true end
        for k in pairs(cs) do keys[k] = true end
        for k in pairs(keys) do
          local delta = (stats[k] or 0) - (cs[k] or 0)
          if delta ~= 0 then d[#d + 1] = string.format("%s %s%s", k, delta > 0 and "+" or "", ns.round(delta, 1)) end
        end
        table.sort(d)
        diffs[#diffs + 1] = string.format("instead of %s: %s", cur and strip(cur) or "(empty slot)",
          #d > 0 and table.concat(d, ", ") or "no stat change")
      end
      if #diffs > 0 then f.vsEquipped = table.concat(diffs, " | ") end
    end
  end
  -- ... and every requirement the game marks as not met (the level has its own fact, the skill its own line above)
  local levelLine = f.tooLowLevel and type(ITEM_MIN_LEVEL) == "string" and safe(string.format, ITEM_MIN_LEVEL, reqLevel)
  local marked = unmet(link)
  for _, text in ipairs(marked or {}) do
    if text ~= levelLine and text ~= noSkill then why[#why + 1] = "not met: " .. text end
  end
  if #why > 0 then f.cannotUse = table.concat(why, "; ") end
  -- (a tooltip that could not be read this once, in a client whose tooltips are read: the facts are made again)
  if marked or not C.unmetFrom then factsCache[cacheKey] = f end
  return f
end

-- tooltip text of an item in a bag or an equipped slot, remembered per item (and durability)
local tipCache, tipCount = {}, 0
local function cachedTooltip(key, fn, a, b, maxLines)
  local t = tipCache[key]
  if t == nil then
    t = tooltipText(safe(fn, a, b), maxLines) or false
    if tipCount > 600 then wipe(tipCache) tipCount = 0 end
    tipCache[key], tipCount = t, tipCount + 1
  end
  return t or nil
end

local function gameTooltipLines()
  if not GameTooltip or not GameTooltip:IsShown() then return nil end
  local out = {}
  for i = 1, GameTooltip:NumLines() do
    local left = _G["GameTooltipTextLeft" .. i]
    local right = _G["GameTooltipTextRight" .. i]
    local l = left and clean(left:GetText()) or ""
    local r = right and right:IsShown() and clean(right:GetText()) or nil
    if r and r ~= "" then l = l .. "  [" .. r .. "]" end
    if l ~= "" then out[#out + 1] = strip(l) end
  end
  return #out > 0 and table.concat(out, "\n") or nil
end

-- a tooltip of the add-on's own window (the hint of one of its buttons) is not what the player looked at in the game
local function ownFrame(frame)
  for _ = 1, 12 do
    if type(frame) ~= "table" or not frame.GetParent then return false end
    local name = frame.GetName and frame:GetName()
    if type(name) == "string" and name:find("^ForeverAI") then return true end
    frame = frame:GetParent()
  end
  return false
end

C.lastTooltip = nil
function C.CaptureTooltip(force)
  local text = gameTooltipLines()
  if text and ownFrame(GameTooltip:GetOwner()) then return C.lastTooltip end
  if text then
    local owner = GameTooltip:GetOwner()
    local _, link = safe(GameTooltip.GetItem, GameTooltip)
    local _, unit = safe(GameTooltip.GetUnit, GameTooltip)
    C.lastTooltip = { lines = text, at = time(), item = link and strip(link), unit = unit,
      owner = owner and owner.GetName and owner:GetName() or nil }
  end
  return C.lastTooltip
end

if GameTooltip then
  GameTooltip:HookScript("OnShow", function() C_Timer.After(0, function() C.CaptureTooltip() end) end)
end

------------------------------------------------------------------------ sections
local S = {}

function S.game()
  local version, build, buildDate, toc = GetBuildInfo()
  local t = {
    version = version, build = version and (version .. "." .. tostring(build)), buildDate = buildDate, toc = toc,
    realm = GetRealmName(), locale = GetLocale(), localTime = date("%Y-%m-%d %H:%M:%S"),
  }
  local st = safe(GetServerTime)
  if st then t.serverTime = date("!%Y-%m-%d %H:%M", st) .. " UTC" end
  -- what "around" and "tracked" can know: without nameplates of all enemies the add-on sees only the target
  if ns.Track and ns.Track.SightText then t.nameplates = ns.Track.SightText() end
  if C_GameRules then
    t.hardcore = safe(C_GameRules.IsHardcoreActive)
    t.selfFound = C_PlayerInfo and C_PlayerInfo.IsSelfFoundActive and safe(C_PlayerInfo.IsSelfFoundActive) or nil
  end
  return t
end

function money(c) -- assigns the forward-declared local
  if not c then return nil end
  return string.format("%dg %ds %dc", math.floor(c / 10000), math.floor(c / 100) % 100, c % 100)
end

function S.player()
  local p = {
    name = UnitName("player"), level = UnitLevel("player"), race = UnitRace("player"),
    class = UnitClass("player"), sex = ({ "?", "male", "female" })[UnitSex("player") or 1],
    faction = UnitFactionGroup("player"), guild = safe(GetGuildInfo, "player"),
    money = money(GetMoney()), xp = UnitXP("player"), xpMax = UnitXPMax("player"), rested = GetXPExhaustion(),
    health = safe(UnitHealth, "player"), healthMax = safe(UnitHealthMax, "player"),
    power = safe(UnitPower, "player"), powerMax = safe(UnitPowerMax, "player"),
    powerType = select(2, UnitPowerType("player")), inCombat = UnitAffectingCombat("player") or nil,
    resting = IsResting() or nil, dead = UnitIsDeadOrGhost("player") or nil,
    zone = GetZoneText(), subzone = GetSubZoneText(), realZone = GetRealZoneText(),
    hearthstone = safe(GetBindLocation),
  }
  if p.xp and p.xpMax and p.xpMax > 0 then p.xpPct = math.floor(p.xp / p.xpMax * 100) end
  -- the way back to the inn: whether the hearthstone can be used now (a route may end with it)
  if p.hearthstone then
    local cooldown = (C_Container and C_Container.GetItemCooldown) or GetItemCooldown
    local start, duration = safe(cooldown, 6948)
    local left = (tonumber(start) or 0) > 0 and (start + (tonumber(duration) or 0) - GetTime()) or 0
    p.hearthstone = p.hearthstone .. (left > 1 and string.format(" (hearthstone ready in %d min)", math.ceil(left / 60))
      or " (hearthstone ready)")
  end
  if UnitOnTaxi and safe(UnitOnTaxi, "player") then p.onFlight = true end
  local inInstance, instanceType = safe(IsInInstance)
  if inInstance then
    local iname, _, _, difficultyName = safe(GetInstanceInfo)
    p.instance = string.format("%s (%s%s)", tostring(iname), tostring(instanceType), difficultyName and (", " .. difficultyName) or "")
  end
  local mapID = C_Map.GetBestMapForUnit("player")
  if mapID then
    local info = C_Map.GetMapInfo(mapID)
    p.map = info and info.name
    p.mapID = mapID
    local pos = C_Map.GetPlayerMapPosition(mapID, "player")
    if pos then p.coords = string.format("%.1f, %.1f", pos.x * 100, pos.y * 100) end
    -- a ghost asks where the body is: the game knows it on the map the player is on
    if p.dead and C_DeathInfo and C_DeathInfo.GetCorpseMapPosition then
      local corpse = safe(C_DeathInfo.GetCorpseMapPosition, mapID)
      if type(corpse) == "table" and corpse.x and corpse.y and (corpse.x > 0 or corpse.y > 0) then
        p.corpse = string.format("%s %.1f, %.1f", tostring(p.map), corpse.x * 100, corpse.y * 100)
      end
    end
  end
  if C_SpecializationInfo and C_SpecializationInfo.GetSpecialization then
    local spec = safe(C_SpecializationInfo.GetSpecialization)
    if spec and spec > 0 then
      local _, specName = safe(C_SpecializationInfo.GetSpecializationInfo, spec)
      if specName ~= p.class then p.spec = specName end -- this client names the class when there are no specs
    end
  end
  return p
end

function S.stats()
  local s = {}
  local names = { "strength", "agility", "stamina", "intellect", "spirit" }
  for i, n in ipairs(names) do
    local base, stat, pos, neg = safe(UnitStat, "player", i)
    if stat then s[n] = stat .. ((pos or 0) ~= 0 and (" (+" .. pos .. ")") or "") .. ((neg or 0) ~= 0 and (" (" .. neg .. ")") or "") end
  end
  local _, armor = safe(UnitArmor, "player")
  s.armor = armor
  local res = {}
  for i, n in ipairs({ "holy", "fire", "nature", "frost", "shadow", "arcane" }) do
    local _, total = safe(UnitResistance, "player", i)
    if total and total > 0 then res[n] = total end
  end
  if next(res) then s.resistances = res end
  local b, p, n = safe(UnitAttackPower, "player")
  if b then s.attackPower = b + (p or 0) + (n or 0) end
  local rb, rp, rn = safe(UnitRangedAttackPower, "player")
  if rb then s.rangedAttackPower = rb + (rp or 0) + (rn or 0) end
  s.meleeCrit = round(safe(GetCritChance), 2)
  s.rangedCrit = round(safe(GetRangedCritChance), 2)
  local spellCrit, spellDmg = 0, 0
  for school = 2, 7 do
    spellCrit = math.max(spellCrit, safe(GetSpellCritChance, school) or 0)
    spellDmg = math.max(spellDmg, safe(GetSpellBonusDamage, school) or 0)
  end
  s.spellCrit = round(spellCrit, 2)
  s.spellDamage = spellDmg
  s.healing = safe(GetSpellBonusHealing)
  s.dodge = round(safe(GetDodgeChance), 2)
  s.parry = round(safe(GetParryChance), 2)
  s.block = round(safe(GetBlockChance), 2)
  s.hit = safe(GetHitModifier)
  s.spellHit = safe(GetSpellHitModifier)
  local base, casting = safe(GetManaRegen)
  if base then s.manaRegen = string.format("%.1f/s (in combat %.1f/s)", base, casting or 0) end
  local minD, maxD = safe(UnitDamage, "player")
  local speed = safe(UnitAttackSpeed, "player")
  if minD then s.meleeDamage = string.format("%.0f-%.0f, speed %.2f", minD, maxD, speed or 0) end
  local rSpeed, rMin, rMax = safe(UnitRangedDamage, "player")
  if rMin and rMin > 0 then s.rangedDamage = string.format("%.0f-%.0f, speed %.2f", rMin, rMax, rSpeed or 0) end
  return s
end

function S.equipment()
  local out = {}
  for slot = 1, 19 do
    budget()
    local link = GetInventoryItemLink("player", slot)
    if link then
      local e = { slot = SLOT_NAMES[slot], item = strip(link) }
      local ilvl = C_Item.GetDetailedItemLevelInfo and safe(C_Item.GetDetailedItemLevelInfo, link)
      e.ilvl = ilvl
      local q = GetInventoryItemQuality("player", slot)
      e.quality = q and qualityName(q)
      local cur, max = GetInventoryItemDurability(slot)
      if cur and max then e.durability = cur .. "/" .. max end
      if C_TooltipInfo and C_TooltipInfo.GetInventoryItem then
        e.tooltip = cachedTooltip("inv" .. slot .. link .. (e.durability or ""), C_TooltipInfo.GetInventoryItem, "player", slot, 60)
      end
      local facts = C.ItemFacts(link)
      if facts then e.stats, e.requiresLevel, e.sellPrice = facts.stats, facts.requiresLevel, facts.sellPrice end
      out[#out + 1] = e
    else
      out[#out + 1] = { slot = SLOT_NAMES[slot], item = "(empty)" }
    end
  end
  -- the ammunition slot: a bow, gun or crossbow shoots nothing without it, and the arrows stay in the bags either way.
  -- Listed only when the game names what is in it: this client gave no link for the slot with arrows equipped, so
  -- "nothing found" does not mean that the slot is empty.
  local ammoSlot = (GetInventorySlotInfo and safe(GetInventorySlotInfo, "AmmoSlot")) or 0
  local ammo = GetInventoryItemLink("player", ammoSlot)
  if not ammo and GetInventoryItemID then
    local id = safe(GetInventoryItemID, "player", ammoSlot)
    local name = id and C_Item.GetItemNameByID and safe(C_Item.GetItemNameByID, id)
    if name then ammo = "[" .. name .. "]" end
  end
  if ammo then
    out[#out + 1] = { slot = "Ammo", item = strip(ammo), count = GetInventoryItemCount and safe(GetInventoryItemCount, "player", ammoSlot) }
  end
  return out
end

-- sum of stats from everything equipped, and a plain statement when gear gives nothing but armor
function S.gearTotals()
  local sum, sources, dps = {}, {}, {}
  for slot = 1, 19 do
    local link = GetInventoryItemLink("player", slot)
    if link then
      for k, v in pairs(itemStats(link)) do
        if k == "DPS" then
          dps[#dps + 1] = strip(link) .. " " .. v
        else
          sum[k] = (sum[k] or 0) + v
          if k ~= "Armor" then
            sources[k] = (sources[k] and (sources[k] .. ", ") or "") .. strip(link) .. " " .. (v > 0 and "+" or "") .. v
          end
        end
      end
    end
  end
  local parts = {}
  for k, v in pairs(sum) do parts[#parts + 1] = string.format("%s %s%s", k, v > 0 and "+" or "", v) end
  table.sort(parts)
  local t = { total = #parts > 0 and table.concat(parts, ", ") or "none" }
  local fromItems = {}
  for k, v in pairs(sources) do fromItems[#fromItems + 1] = k .. ": " .. v end
  table.sort(fromItems)
  if #fromItems > 0 then
    t.bySource = fromItems
  else
    t.note = "No equipped item gives stats other than armor (read from the game); Agility, Stamina etc. in stats " ..
      "come from race, class and level."
  end
  if #dps > 0 then t.weaponDps = table.concat(dps, "; ") end
  return t
end

-- what a bag item that is not equipment does: its tooltip without the name (use effect, requirements), a few lines
local function useText(link, bag, slot)
  if not (C_TooltipInfo and C_TooltipInfo.GetBagItem) then return nil end
  local full = cachedTooltip("use" .. link, C_TooltipInfo.GetBagItem, bag, slot, 9)
  return full and full:match("^[^\n]*\n(.+)$") or nil
end

local function bagItems(bags, withTooltips)
  local agg, order, free = {}, {}, 0
  for _, bag in ipairs(bags) do
    local n = safe(C_Container.GetContainerNumSlots, bag) or 0
    for slot = 1, n do
      budget()
      local info = safe(C_Container.GetContainerItemInfo, bag, slot)
      if info and info.hyperlink then
        local key = info.itemID or info.hyperlink
        if not agg[key] then
          local e = { item = strip(info.hyperlink), id = info.itemID, count = 0, quality = qualityName(info.quality or 1) }
          local _, _, _, _, _, _, _, _, _, _, unitPrice = safe(C_Item.GetItemInfo, info.hyperlink)
          if unitPrice and unitPrice > 0 and not info.hasNoValue then e.sellCopper = unitPrice end
          if info.quality == 0 then e.junk = true end
          local _, _, _, equipLoc, _, classID = C_Item.GetItemInfoInstant(info.itemID or info.hyperlink)
          if withTooltips and equipLoc and equipLoc ~= "" and equipLoc ~= "INVTYPE_NON_EQUIP_IGNORE" then
            e.tooltip = cachedTooltip("bag" .. info.hyperlink, C_TooltipInfo.GetBagItem, bag, slot, 60)
            e.facts = C.ItemFacts(info.hyperlink, true)
          elseif withTooltips then
            local _, _, _, _, _, _, _, _, _, _, sell = safe(C_Item.GetItemInfo, info.hyperlink)
            if sell and sell > 0 then e.sellPrice = money(sell) end
            e.tooltip = useText(info.hyperlink, bag, slot)
          end
          if info.isBound then e.bound = true end
          agg[key] = e
          order[#order + 1] = key
        end
        agg[key].count = agg[key].count + (info.stackCount or 1)
      elseif not info then
        free = free + 1
      end
    end
  end
  local out = {}
  for _, k in ipairs(order) do out[#out + 1] = agg[k] end
  return out, free
end

function S.bags()
  local items, free = bagItems({ 0, 1, 2, 3, 4, 5 }, true)
  return { freeSlots = free, items = items }
end

C.bankCache = nil
ns.On("BANKFRAME_OPENED", function()
  C_Timer.After(0.5, function()
    local bags = { -1 }
    for i = 6, 12 do bags[#bags + 1] = i end
    if Enum and Enum.BagIndex then
      for k, v in pairs(Enum.BagIndex) do
        if type(k) == "string" and k:find("CharacterBankTab") then bags[#bags + 1] = v end
      end
    end
    local items = bagItems(bags, false)
    ns.char.bank = { items = items, at = date("%Y-%m-%d %H:%M") }
  end)
end)

function S.bank()
  return ns.char.bank
end

function S.talents()
  if not (C_ClassTalents and C_Traits) then return nil end
  local out = {}
  local result = { trees = out }
  local function dumpConfig(cfg, label)
    local info = safe(C_Traits.GetConfigInfo, cfg)
    if not info or not info.treeIDs then return end
    for _, treeID in ipairs(info.treeIDs) do
      local t = { config = label or info.name, tree = treeID, picked = {} }
      local cur = safe(C_Traits.GetTreeCurrencyInfo, cfg, treeID, false)
      if type(cur) == "table" and cur[1] then
        t.unspent, t.earned = cur[1].quantity, cur[1].maxQuantity -- points to spend now, points earned so far
      end
      local nodes = safe(C_Traits.GetTreeNodes, treeID) or {}
      t.nodes = #nodes
      local canPick = {}
      for _, nodeID in ipairs(nodes) do
        budget()
        local n = safe(C_Traits.GetNodeInfo, cfg, nodeID)
        local rank = n and n.activeRank or 0
        local open = n and n.isAvailable and n.canPurchaseRank and rank < (n.maxRanks or 0)
        if n and (rank > 0 or open) then
          local entryID = (n.activeEntry and n.activeEntry.entryID) or (n.entryIDs and n.entryIDs[1])
          local e = entryID and safe(C_Traits.GetEntryInfo, cfg, entryID)
          local d = e and e.definitionID and safe(C_Traits.GetDefinitionInfo, e.definitionID)
          local name = d and (d.overrideName or (d.spellID and C_Spell.GetSpellName(d.spellID)))
          local label = tostring(name or ("spell " .. tostring(d and d.spellID))) .. " " .. rank .. "/" .. tostring(n.maxRanks)
          if rank > 0 then t.picked[#t.picked + 1] = label end
          if open and #canPick < 40 then -- what a point can go into right now, with what it does
            local desc = d and d.spellID and C.spellTip and C.spellTip(d.spellID, 300)
            if desc and not desc:find(" ", 1, true) then desc = nil end -- only "Passive": the game gave no text
            canPick[#canPick + 1] = label .. (desc and (": " .. desc) or "")
          end
        end
      end
      if #canPick > 0 then t.canPick = canPick end
      out[#out + 1] = t
    end
  end
  local active = safe(C_ClassTalents.GetActiveConfigID)
  if active then dumpConfig(active, "class") end
  return result
end

-- NPC names with their first known spawn (zone x, y) from Questie's database
-- the NPCs (by[1]) and objects (by[2]) that start or end a quest, with where they are: "Name (Zone x, y); ..."
local questieGivers

local function questieNPCs(questie, list)
  local names = {}
  for _, id in ipairs(list or {}) do
    local okN, npc = pcall(questie.GetNPC, questie, id)
    if not (okN and type(npc) == "table") then okN, npc = pcall(questie.GetNPC, id) end
    if okN and type(npc) == "table" and npc.name then
      local where = ""
      if type(npc.spawns) == "table" then
        for zoneID, spawns in pairs(npc.spawns) do
          local s1 = spawns and spawns[1]
          local area = C_Map.GetAreaInfo and C_Map.GetAreaInfo(zoneID)
          if s1 then
            where = string.format(" (%s %.1f, %.1f)", area or ("zone " .. zoneID), s1[1], s1[2])
            C.npcCoords[npc.name] = { zone = area, x = s1[1], y = s1[2] }
          end
          break
        end
      end
      names[#names + 1] = npc.name .. where
    end
  end
  return #names > 0 and table.concat(names, "; ") or nil
end

-- the objects (a box, a monument, a board) that start or end a quest, as questieNPCs does for NPCs
local function questieObjects(questie, list)
  local names = {}
  for _, id in ipairs(list or {}) do
    local okO, obj = pcall(questie.GetObject, questie, id)
    if not (okO and type(obj) == "table") then okO, obj = pcall(questie.GetObject, id) end
    if okO and type(obj) == "table" and obj.name then
      local where = ""
      for zoneID, spawns in pairs(type(obj.spawns) == "table" and obj.spawns or {}) do
        local s1 = spawns and spawns[1]
        local area = C_Map.GetAreaInfo and C_Map.GetAreaInfo(zoneID)
        if s1 then where = string.format(" (%s %.1f, %.1f)", area or ("zone " .. zoneID), s1[1], s1[2]) end
        break
      end
      names[#names + 1] = obj.name .. where
    end
  end
  return #names > 0 and table.concat(names, "; ") or nil
end

questieGivers = function(questie, by)
  if type(by) ~= "table" then return nil end
  local npcs, objects = questieNPCs(questie, by[1]), questie.GetObject and questieObjects(questie, by[2]) or nil
  if npcs and objects then return npcs .. "; " .. objects end
  return npcs or objects
end

local function questieDB()
  if QuestieLoader and QuestieLoader.ImportModule then
    local ok, db = pcall(QuestieLoader.ImportModule, QuestieLoader, "QuestieDB")
    if ok and type(db) == "table" then return db end
  end
end

------------------------------------------------------------------------ where to get quest objectives (Questie database)
-- For every objective: the creatures to kill, the objects to use, and for items which objects contain them, which
-- creatures drop them and which vendors sell them, with every known spawn point.
-- Second result: where the thing is when it has no place on a zone map (the database lists it under a dungeon, with
-- -1, -1 for coordinates): the name of that dungeon.
local function spawnPoints(spawns)
  local out, inside = {}, nil
  for area, pts in pairs(type(spawns) == "table" and spawns or {}) do
    for _, p in ipairs(type(pts) == "table" and pts or {}) do
      if type(p) == "table" and tonumber(p[1]) and p[1] > 0 then
        out[#out + 1] = { area = area, x = p[1], y = p[2] }
      elseif type(p) == "table" and not inside then
        inside = C_Map.GetAreaInfo and safe(C_Map.GetAreaInfo, area) or nil
      end
    end
  end
  return out, #out == 0 and inside or nil
end

local RANK = { [1] = "elite", [2] = "rare elite", [3] = "boss", [4] = "rare" }

local function span(a, b)
  a, b = tonumber(a), tonumber(b)
  if not a then return nil end
  return (b and b ~= a) and (a .. "-" .. b) or tostring(a)
end

-- a creature that gives an objective, with what tells how hard and how sure it is: its level, rank and health, and for
-- an item it drops, the chance of the drop (Questie's drop data: the private server's for quest items, else Wowhead's)
local function npcSource(QDB, npcId, how, itemId)
  local okN, name = pcall(QDB.QueryNPCSingle, npcId, "name")
  if not (okN and type(name) == "string") then return nil end
  local _, spawns = pcall(QDB.QueryNPCSingle, npcId, "spawns")
  local _, minL = pcall(QDB.QueryNPCSingle, npcId, "minLevel")
  local _, maxL = pcall(QDB.QueryNPCSingle, npcId, "maxLevel")
  local _, rank = pcall(QDB.QueryNPCSingle, npcId, "rank")
  local _, minH = pcall(QDB.QueryNPCSingle, npcId, "minLevelHealth")
  local _, maxH = pcall(QDB.QueryNPCSingle, npcId, "maxLevelHealth")
  local chance
  if itemId and type(QDB.GetItemDroprate) == "function" then
    local okD, rate = pcall(QDB.GetItemDroprate, itemId, npcId)
    chance = okD and type(rate) == "table" and tonumber(rate[1]) or nil
  end
  local facts = {}
  local lvl = span(minL, maxL)
  if lvl then facts[#facts + 1] = "level " .. lvl end
  if RANK[tonumber(rank) or 0] then facts[#facts + 1] = RANK[tonumber(rank)] end
  local hp = span(minH, maxH)
  if hp and tonumber(minH) > 0 then facts[#facts + 1] = hp .. " health" end
  if chance and chance > 0 then
    facts[#facts + 1] = (chance >= 10 and string.format("%d%%", math.floor(chance + 0.5)) or string.format("%.1f%%", chance))
      .. " drop chance"
  end
  local pts, inside = spawnPoints(spawns)
  return { how = how, name = name, level = lvl, facts = #facts > 0 and table.concat(facts, ", ") or nil,
           chance = chance, pts = pts, inside = inside }
end

local function objectSource(QDB, objId, how)
  if not QDB.QueryObjectSingle then return nil end
  local okN, name = pcall(QDB.QueryObjectSingle, objId, "name")
  if not (okN and type(name) == "string") then return nil end
  local _, spawns = pcall(QDB.QueryObjectSingle, objId, "spawns")
  local pts, inside = spawnPoints(spawns)
  return { how = how, name = name, pts = pts, inside = inside }
end

local function objectiveSources(QDB, qid)
  if not (QDB and QDB.QueryQuestSingle) then return nil end
  local okO, objs = pcall(QDB.QueryQuestSingle, qid, "objectives")
  if not (okO and type(objs) == "table") then return nil end
  local list = {}
  local function add(label, sources, credit)
    if #sources > 0 then list[#list + 1] = { label = label, sources = sources, credit = credit } end
  end
  local function id(o) return type(o) == "table" and o[1] or o end
  for _, o in ipairs(type(objs[1]) == "table" and objs[1] or {}) do -- creatures to kill
    local s = npcSource(QDB, id(o), "kill")
    if s then add(s.name, { s }) end
  end
  for _, o in ipairs(type(objs[2]) == "table" and objs[2] or {}) do -- objects to use
    local s = objectSource(QDB, id(o), "use")
    if s then add(s.name, { s }) end
  end
  for _, o in ipairs(type(objs[3]) == "table" and objs[3] or {}) do -- items to collect
    local itemId = id(o)
    local okI, itemName = pcall(QDB.QueryItemSingle, itemId, "name")
    local sources = {}
    local _, fromObjects = pcall(QDB.QueryItemSingle, itemId, "objectDrops")
    for _, oid in ipairs(type(fromObjects) == "table" and fromObjects or {}) do
      local s = objectSource(QDB, oid, "from object") if s then sources[#sources + 1] = s end
    end
    local _, droppers = pcall(QDB.QueryItemSingle, itemId, "npcDrops")
    for _, nid in ipairs(type(droppers) == "table" and droppers or {}) do
      local s = npcSource(QDB, nid, "dropped by", itemId) if s then sources[#sources + 1] = s end
    end
    local _, vendors = pcall(QDB.QueryItemSingle, itemId, "vendors")
    for _, nid in ipairs(type(vendors) == "table" and vendors or {}) do
      local s = npcSource(QDB, nid, "sold by") if s then sources[#sources + 1] = s end
    end
    add(okI and type(itemName) == "string" and itemName or ("item " .. tostring(itemId)), sources)
  end
  local kc = objs[5] -- kill credit: any of several creatures counts
  if type(kc) == "table" then
    for _, entry in ipairs(type(kc[1]) == "table" and kc or { kc }) do
      if type(entry) == "table" and type(entry[1]) == "table" then
        local sources = {}
        for _, nid in ipairs(entry[1]) do
          local s = npcSource(QDB, nid, "kill") if s then sources[#sources + 1] = s end
        end
        add(type(entry[3]) == "string" and entry[3] or "kill credit", sources, true)
      end
    end
  end
  return list
end

-- The creatures that give the same objective of a quest as ``name`` does: any Bloodfeather drops the belts, whichever
-- one a route happens to name. Returns the objective's label and the names of all of them; nil and an empty list when
-- the quest has no such objective; nothing at all while the quest database is not there.
function C.SameObjective(questID, name)
  local list = objectiveSources(questieDB(), questID)
  if not list then return nil end
  local key = tostring(name):lower()
  for _, obj in ipairs(list) do
    local names, known, has = {}, {}, false
    for _, s in ipairs(obj.sources) do
      local low = s.name:lower()
      if (s.how == "kill" or s.how == "dropped by") and not known[low] then
        known[low] = true
        names[#names + 1] = s.name
        has = has or low == key
      end
    end
    if has then return obj.label, names end
  end
  return nil, {}
end

-- the player's map position and Questie zone (area) id
local function playerSpot()
  local mapID = C_Map.GetBestMapForUnit("player")
  local pos = mapID and C_Map.GetPlayerMapPosition(mapID, "player")
  local area
  if QuestieLoader and QuestieLoader.ImportModule then
    local ok, Z = pcall(QuestieLoader.ImportModule, QuestieLoader, "ZoneDB")
    if ok and type(Z) == "table" and mapID then area = safe(Z.GetAreaIdByUiMapId, Z, mapID) end
  end
  return mapID, pos and pos.x * 100, pos and pos.y * 100, area
end

-- Where to go for a creature or an object that has many spawn points: not to the nearest single one when the rest
-- stand together elsewhere ("find another spot where there are more of them": two spawn points by the player, fifteen
-- half a kilometre south). Each point counts the points within GROUP_M of it; the place to go is the nearest point of
-- a good group (at least GROUP_MIN points, or most of the largest group), and up to two other groups are named.
-- ``kills``: how many are still to be killed there, when the quest log says (for a drop: what is still to be looted
-- over its chance). A kill or two is done at the nearest spawn point and up to five where two stand; only more than
-- that needs a place where they are dense.
local GROUP_M, GROUP_MIN, MANY_KILLS = 150, 4, 5
local function spawnGroups(pts, mapID, px, py, kills)
  local n = #pts
  if n == 0 or n > 400 then return nil end
  local size = {}
  for i = 1, n do
    budget()
    local c = 0
    for j = 1, n do
      local d = ns.MapMeters(mapID, pts[i].x, pts[i].y, pts[j].x, pts[j].y)
      if d and d <= GROUP_M then c = c + 1 end
    end
    size[i] = c
  end
  local most = 0
  for i = 1, n do most = math.max(most, size[i]) end
  local need = math.min(most, math.max(GROUP_MIN, math.ceil(most * 0.6)))
  if kills and kills <= 2 then
    need = 1
  elseif kills and kills <= MANY_KILLS then
    need = math.min(most, 2)
  end
  local best
  for i = 1, n do
    if size[i] >= need then
      local d = px and ns.MapMeters(mapID, px, py, pts[i].x, pts[i].y)
      if not best or (d and best.d and d < best.d) then best = { p = pts[i], d = d, size = size[i] } end
    end
  end
  if not best then return nil end
  -- the other groups: the fullest points away from the place chosen and from each other
  local others, taken = {}, { best.p }
  for _ = 1, 2 do
    local pick
    for i = 1, n do
      local far = size[i] >= 3
      for _, t in ipairs(taken) do
        local d = ns.MapMeters(mapID, pts[i].x, pts[i].y, t.x, t.y)
        if not d or d <= 2 * GROUP_M then far = false break end
      end
      if far and (not pick or size[i] > pick.size) then pick = { p = pts[i], size = size[i] } end
    end
    if not pick then break end
    others[#others + 1] = pick
    taken[#taken + 1] = pick.p
  end
  return best, others
end

-- How many of a source are still to be killed for an objective, from the quest log's own line ("3/15 Defias
-- Highwayman slain", "0/5 Fine Moonstalker Pelt"); for an item that drops, what is left over the drop chance (five
-- pelts at 40% are a dozen kills). nil when the quest log does not say.
local function killsLeft(obj, source, objectives)
  local label, name = tostring(obj.label or ""):lower(), tostring(source.name or ""):lower()
  for _, line in ipairs(objectives or {}) do
    local low = tostring(line):lower()
    if (label ~= "" and low:find(label, 1, true)) or (name ~= "" and low:find(name, 1, true)) then
      local have, all = low:match("(%d+)%s*/%s*(%d+)")
      local left = have and (tonumber(all) - tonumber(have)) or nil
      if left and left > 0 then
        if source.how == "dropped by" and source.chance and source.chance > 0 then
          left = math.ceil(left / (source.chance / 100))
        end
        return left
      end
    end
  end
  return nil
end

-- objective sources -> lines for the AI, nearest first, with distance in meters in the player's zone.
-- The database calls every creature of a quest a kill; ``objectives`` (the quest log's own objective texts) tell which
-- ones really are ("0/8 Befouled Webwood slain"): the others (talk to, tame, escort) only say where the creature is.
local function whereToGet(list, mapID, px, py, area, objectives)
  if not list or #list == 0 then return nil end
  local slain
  if objectives then
    slain = {}
    for _, t in ipairs(objectives) do
      local name = tostring(t):match("^%d+/%d+ (.-) slain") or tostring(t):match("^(.-) slain: %d+/%d+")
      if name then slain[name:lower()] = true end
    end
  end
  local out = {}
  for _, obj in ipairs(list) do
    local ranked = {}
    for _, s in ipairs(obj.sources) do
      local best, here, mine = nil, 0, {}
      for _, p in ipairs(s.pts) do
        if area and p.area == area then
          here = here + 1
          mine[#mine + 1] = p
          local d = px and ns.MapMeters(mapID, px, py, p.x, p.y)
          if d and (not best or d < best.d) then best = { d = d, p = p } end
        end
      end
      -- many spawn points: go where they stand together, not to the nearest lone one
      local group, others
      if best and here > 1 then
        group, others = spawnGroups(mine, mapID, px, py, killsLeft(obj, s, objectives))
        if group and group.d then best = group end
      end
      ranked[#ranked + 1] = { s = s, best = best or (s.pts[1] and { p = s.pts[1] }), here = here, others = others }
    end
    table.sort(ranked, function(a, b) return (a.best and a.best.d or 1e9) < (b.best and b.best.d or 1e9) end)
    local parts = {}
    for i = 1, math.min(5, #ranked) do
      local r = ranked[i]
      local where = r.s.inside and ("inside " .. r.s.inside .. " (no place on a zone map)") or "no known spawn point"
      if r.best then
        local zone = C_Map.GetAreaInfo and C_Map.GetAreaInfo(r.best.p.area) or ("zone " .. tostring(r.best.p.area))
        where = string.format("%s %.1f, %.1f", tostring(zone), r.best.p.x, r.best.p.y)
        if r.best.d then
          local more = ""
          if r.best.size and r.here > 1 then more = string.format(", %d of them around here", r.best.size) end
          for n, o in ipairs(r.others or {}) do
            more = more .. string.format("%s %d around %.1f, %.1f", n == 1 and ", others:" or " and", o.size, o.p.x, o.p.y)
          end
          where = where .. string.format(" (%d m, %d spawn point%s in your zone%s)", r.best.d, r.here,
            r.here == 1 and "" or "s", more)
        else
          -- asked for another zone (no distance to give): still, how many places it can be at there. One point of
          -- several is somewhere to search around, not the spot.
          local there = 0
          for _, p in ipairs(r.s.pts) do
            if p.area == r.best.p.area then there = there + 1 end
          end
          if there > 1 then where = where .. string.format(" (%d spawn points in that zone)", there) end
        end
      end
      local how = r.s.how
      if how == "kill" and slain and not obj.credit and not (slain[obj.label:lower()] or slain[r.s.name:lower()]) then
        how = "find"
      end
      local facts = r.s.facts or (r.s.level and ("level " .. r.s.level))
      parts[#parts + 1] = string.format("%s %s%s — %s", how, r.s.name, facts and (" (" .. facts .. ")") or "", where)
    end
    if #ranked > 5 then parts[#parts + 1] = string.format("and %d more", #ranked - 5) end
    out[#out + 1] = obj.label .. ": " .. table.concat(parts, "; ")
  end
  return out
end
C.WhereToGet = whereToGet

-- everything known about one quest (tagged with Alt+click): log state, Questie data, givers with coordinates
function C.QuestInfo(id)
  id = tonumber(id)
  if not id then return nil end
  local out = { id = id }
  local objectiveTexts
  out.title = C_QuestLog.GetTitleForQuestID and ns.safe(C_QuestLog.GetTitleForQuestID, id)
  local idx = C_QuestLog.GetLogIndexForQuestID and ns.safe(C_QuestLog.GetLogIndexForQuestID, id)
  if idx then
    out.inQuestLog = true
    out.complete = C_QuestLog.IsComplete and ns.safe(C_QuestLog.IsComplete, id) or nil
    local o = {}
    for _, ob in ipairs((C_QuestLog.GetQuestObjectives and ns.safe(C_QuestLog.GetQuestObjectives, id)) or {}) do
      o[#o + 1] = ns.clean(ob.text)
    end
    objectiveTexts = o
    if #o > 0 then out.objectives = table.concat(o, "; ") end
  elseif C_QuestLog.IsQuestFlaggedCompleted and ns.safe(C_QuestLog.IsQuestFlaggedCompleted, id) then
    out.alreadyDone = true
  end
  local questie = questieDB()
  if questie then
    local okQ, qd = pcall(questie.GetQuest, id)
    if not (okQ and type(qd) == "table") then okQ, qd = pcall(questie.GetQuest, questie, id) end
    if okQ and type(qd) == "table" then
      out.title = out.title or qd.name
      out.level, out.minLevel = qd.questLevel or qd.level, qd.requiredLevel
      if type(qd.objectivesText) == "table" then out.objectiveText = table.concat(qd.objectivesText, " ") end
      out.questGiver = questieGivers(questie, qd.startedBy)
      out.turnIn = questieGivers(questie, qd.finishedBy)
    end
    if not out.alreadyDone and not out.complete then
      local okS, sources = pcall(objectiveSources, questie, id)
      if okS then
        local mapID, px, py, area = playerSpot()
        out.whereToGet = whereToGet(sources, mapID, px, py, area, out.inQuestLog and objectiveTexts or nil)
      end
    end
  end
  if out.inQuestLog and C_QuestLog.SetSelectedQuest and GetQuestLogQuestText then
    local selected = C_QuestLog.GetSelectedQuest and C_QuestLog.GetSelectedQuest()
    C_QuestLog.SetSelectedQuest(id)
    out.description, out.objectiveText = GetQuestLogQuestText()
    if selected then C_QuestLog.SetSelectedQuest(selected) end
  end
  return out
end

-- Questie loads its drop tables a while after login, and the add-on reads the quest log three seconds after it:
-- sources read before that have no drop chances, and must not be kept for the session. (No drop tables in this
-- Questie, or none after two minutes: nothing to wait for.)
local loadedAt = GetTime()
local function dropsReady()
  if not (QuestieLoader and QuestieLoader.ImportModule) then return true end
  local ok, drops = pcall(QuestieLoader.ImportModule, QuestieLoader, "DropDB")
  if not ok or type(drops) ~= "table" or type(drops.GetItemDroprate) ~= "function" then return true end
  return drops.tableWowhead ~= nil or drops.tablePserver ~= nil or GetTime() - loadedAt > 120
end

-- what does not change for a quest (texts, rewards, quest giver and turn-in), read once per quest
local staticCache = {}
local function questStatic(qid, questie)
  local s = staticCache[qid]
  if s then return s end
  s = {}
  local complete = true
  if C_QuestLog.SetSelectedQuest and GetQuestLogQuestText then
    C_QuestLog.SetSelectedQuest(qid)
    local desc, objText = GetQuestLogQuestText()
    s.objectiveText = objText
    desc = type(desc) == "string" and desc:gsub("\r", "") or desc
    s.description = ns.Cut(desc, 6000) -- the whole quest text (the cap only guards against broken data)
    local xp = GetQuestLogRewardXP and safe(GetQuestLogRewardXP)
    local gold = GetQuestLogRewardMoney and safe(GetQuestLogRewardMoney)
    local rewards = {}
    if xp and xp > 0 then rewards[#rewards + 1] = xp .. " XP" end
    if gold and gold > 0 then rewards[#rewards + 1] = money(gold) end
    for r = 1, (GetNumQuestLogRewards and safe(GetNumQuestLogRewards) or 0) do
      local link = GetQuestLogItemLink and safe(GetQuestLogItemLink, "reward", r)
      if link then rewards[#rewards + 1] = strip(link) else complete = false end
    end
    local choices = {}
    for r = 1, (GetNumQuestLogChoices and safe(GetNumQuestLogChoices) or 0) do
      local link = GetQuestLogItemLink and safe(GetQuestLogItemLink, "choice", r)
      if link then choices[#choices + 1] = strip(link) else complete = false end
    end
    if #choices > 0 then rewards[#rewards + 1] = "choice: " .. table.concat(choices, " / ") end
    if #rewards > 0 then s.rewards = table.concat(rewards, ", ") end
  end
  if questie then
    local okQ, qd = pcall(questie.GetQuest, qid)
    if not (okQ and type(qd) == "table") then okQ, qd = pcall(questie.GetQuest, questie, qid) end
    if okQ and type(qd) == "table" then
      s.questGiver = questieGivers(questie, qd.startedBy)
      s.turnIn = questieGivers(questie, qd.finishedBy)
      -- the quest that follows this one in its chain: taking it at the turn-in is what a route plans for
      local nextId = tonumber(qd.nextQuestInChain)
      if nextId and nextId > 0 then
        local okN, nq = pcall(questie.GetQuest, nextId)
        if not (okN and type(nq) == "table") then okN, nq = pcall(questie.GetQuest, questie, nextId) end
        local giver = okN and type(nq) == "table" and nq.name and questieGivers(questie, nq.startedBy)
        if giver then
          s.next = { id = nextId, minLevel = tonumber(nq.requiredLevel) or 0,
            text = string.format("[%s]%s from %s", nq.name, nq.questLevel and (" (level " .. nq.questLevel .. ")") or "", giver) }
        end
      end
    end
    local okS, sources = pcall(objectiveSources, questie, qid)
    s.sources = okS and sources or nil
    if not dropsReady() then complete = false end -- read again once the drop chances are there
  end
  if complete then staticCache[qid] = s end -- reward items still loading: read again next time
  return s
end

function S.quests()
  local out = {}
  if not C_QuestLog or not C_QuestLog.GetNumQuestLogEntries then return out end
  local questie = questieDB()
  local selected = C_QuestLog.GetSelectedQuest and C_QuestLog.GetSelectedQuest()
  local mapID, px, py, area = playerSpot()
  local count = 0
  for i = 1, C_QuestLog.GetNumQuestLogEntries() do
    budget()
    local q = C_QuestLog.GetInfo(i)
    if q and not q.isHeader and q.questID then
      count = count + 1
      local qid = q.questID
      local e = { id = qid, title = q.title, level = q.level }
      -- explicit status, so the AI never has to guess
      if C_QuestLog.IsFailed and safe(C_QuestLog.IsFailed, qid) then
        e.status = "FAILED (time ran out or a condition was broken) — abandon and pick it up again"
      elseif safe(C_QuestLog.IsComplete, qid) then
        e.status = "READY TO TURN IN"
      else
        e.status = "in progress"
      end
      -- every objective with its own state
      local objs = safe(C_QuestLog.GetQuestObjectives, qid) or {}
      local o, texts = {}, {}
      for _, ob in ipairs(objs) do
        local t = clean(ob.text)
        if t then
          texts[#texts + 1] = t
          o[#o + 1] = t .. (ob.finished and " [DONE]" or " [TO DO]")
        end
      end
      e.objectives = #o > 0 and table.concat(o, "; ") or nil
      -- timed quests
      if C_QuestLog.GetTimeAllowed then
        local total, elapsed = safe(C_QuestLog.GetTimeAllowed, qid)
        if total and total > 0 then
          e.timeLeft = string.format("%d s of %d s", math.max(0, math.floor(total - (elapsed or 0))), total)
        end
      end
      -- tracked, type (elite / group / dungeon), suggested group size
      if C_QuestLog.GetQuestWatchType then e.tracked = safe(C_QuestLog.GetQuestWatchType, qid) ~= nil or nil end
      local tag = C_QuestLog.GetQuestTagInfo and safe(C_QuestLog.GetQuestTagInfo, qid)
      if type(tag) == "table" and tag.tagName then e.type = tag.tagName end
      if q.suggestedGroup and q.suggestedGroup > 1 then e.suggestedGroup = q.suggestedGroup end
      if q.frequency and q.frequency > 0 then e.repeatable = q.frequency == 1 and "daily" or "weekly" end
      local st = questStatic(qid, questie)
      e.objectiveText, e.description, e.rewards = st.objectiveText, st.description, st.rewards
      e.questGiver, e.turnIn = st.questGiver, st.turnIn
      if st.next and st.next.minLevel <= (UnitLevel("player") or 1) then -- one the character can take already
        e.next, e.nextId = st.next.text, st.next.id
      end
      if e.status == "in progress" then e.whereToGet = whereToGet(st.sources, mapID, px, py, area, texts) end
      out[#out + 1] = e
    end
  end
  if selected and C_QuestLog.SetSelectedQuest then C_QuestLog.SetSelectedQuest(selected) end
  local max = C_QuestLog.GetMaxNumQuestsCanAccept and safe(C_QuestLog.GetMaxNumQuestsCanAccept)
  table.insert(out, 1, string.format("Quest log: %d%s", count, max and ("/" .. max) or ""))
  return out
end

-- quests the player can pick up around them (from Questie's availability calculation), nearest first
local nearbyCache = { key = nil, list = {}, at = 0 }
function S.nearbyQuests()
  if not (QuestieLoader and QuestieLoader.ImportModule) then return nil end
  local okA, AQ = pcall(QuestieLoader.ImportModule, QuestieLoader, "AvailableQuests")
  local okD, QDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "QuestieDB")
  local okZ, ZoneDB = pcall(QuestieLoader.ImportModule, QuestieLoader, "ZoneDB")
  if not (okA and okD and type(AQ) == "table" and type(QDB) == "table" and type(AQ.__availableQuests) == "table") then
    return nil
  end
  local mapID = C_Map.GetBestMapForUnit("player")
  local pos = mapID and C_Map.GetPlayerMapPosition(mapID, "player")
  local px, py = pos and pos.x * 100, pos and pos.y * 100
  local playerArea = okZ and type(ZoneDB) == "table" and mapID and safe(ZoneDB.GetAreaIdByUiMapId, ZoneDB, mapID)
  local myLevel = UnitLevel("player") or 1
  local available = 0
  for _ in pairs(AQ.__availableQuests) do available = available + 1 end
  -- the Questie lookups (quest + quest giver spawns) are redone only when the zone, the level or the set of
  -- available quests changes; every question only recomputes the distances
  local key = table.concat({ tostring(playerArea), myLevel, available }, ":")
  if nearbyCache.key ~= key or GetTime() - nearbyCache.at > 300 then
    local cands, scanned = {}, 0
    for questId in pairs(AQ.__availableQuests) do
      scanned = scanned + 1
      if scanned > 250 then break end
      budget()
      local q = safe(QDB.GetQuest, questId)
      -- quests far above the player's level (e.g. level-60 Craftsman's Writs) are not "nearby quests" for them
      if type(q) == "table" and q.name and (tonumber(q.requiredLevel) or 0) <= myLevel + 3
          and (tonumber(q.questLevel) or 0) <= myLevel + 8 then
        local givers = {}
        for _, npcId in ipairs((q.startedBy and q.startedBy[1]) or {}) do
          local npc = safe(QDB.GetNPC, QDB, npcId)
          if type(npc) == "table" and type(npc.spawns) == "table" then
            for areaId, spawns in pairs(npc.spawns) do
              local s = type(spawns) == "table" and spawns[1]
              if s and s[1] then givers[#givers + 1] = { npc = npc.name, area = areaId, x = s[1], y = s[2] } end
            end
          end
        end
        -- no known quest giver (e.g. Craftsman's Writs): nothing to walk to
        if #givers > 0 then
          cands[#cands + 1] = { id = questId, title = q.name, level = q.questLevel, minLevel = q.requiredLevel, givers = givers,
            objective = type(q.objectivesText) == "table" and table.concat(q.objectivesText, " ") or nil }
        end
      end
    end
    nearbyCache.key, nearbyCache.list, nearbyCache.at = key, cands, GetTime()
  end
  local list = {}
  local meters = ns.MapSize(mapID) ~= nil
  for _, c in ipairs(nearbyCache.list) do
    local best
    for _, g in ipairs(c.givers) do
      local dist = (playerArea and g.area == playerArea and px) and
        (ns.MapMeters(mapID, px, py, g.x, g.y) or math.sqrt((g.x - px) ^ 2 + (g.y - py) ^ 2)) or nil
      if not best or (dist and (not best.dist or dist < best.dist)) then best = { g = g, dist = dist } end
    end
    local g = best.g
    local areaName = C_Map.GetAreaInfo and C_Map.GetAreaInfo(g.area) or ("zone " .. tostring(g.area))
    list[#list + 1] = { id = c.id, title = c.title, level = c.level, minLevel = c.minLevel, objective = c.objective,
      giver = g.npc, where = string.format("%s %.1f, %.1f", areaName, g.x, g.y), dist = best.dist,
      distance = best.dist and (meters and (math.floor(best.dist) .. " m") or (ns.round(best.dist, 1) .. "% of map")) or nil }
    if g.npc then C.npcCoords[g.npc] = { zone = areaName, x = g.x, y = g.y } end
  end
  table.sort(list, function(a, b) return (a.dist or 999999) < (b.dist or 999999) end)
  -- quests given in another zone matter only when there is (almost) nothing to do here, and then only a few
  local local_, far = 0, 0
  for _, e in ipairs(list) do if e.dist then local_ = local_ + 1 end end
  local out = {}
  for _, e in ipairs(list) do
    if #out >= 15 then break end
    if e.dist then
      e.dist = nil
      out[#out + 1] = e
    elseif local_ < 3 and far < 4 then
      far = far + 1
      out[#out + 1] = e
    end
  end
  return #out > 0 and out or nil
end

-- a spell as the game names it, with its rank: "Serpent Sting (Rank 2)" (a bar can hold a lower rank than the
-- spellbook has; this client does not swap it when a new rank is learned)
local function spellLabel(id)
  local name = id and C_Spell.GetSpellName and safe(C_Spell.GetSpellName, id)
  if not name then return nil end
  local sub = C_Spell.GetSpellSubtext and safe(C_Spell.GetSpellSubtext, id)
  return name .. ((sub and sub ~= "") and (" (" .. sub .. ")") or "")
end

-- the pet's spellbook: its abilities (and, in some clients, its commands), each { name, subName, spellID, actionID }
local function petBook()
  local out = {}
  if not (C_SpellBook and C_SpellBook.HasPetSpells and C_SpellBook.GetSpellBookItemInfo and Enum.SpellBookSpellBank) then
    return out
  end
  for i = 1, math.min(40, tonumber((safe(C_SpellBook.HasPetSpells))) or 0) do
    local ii = safe(C_SpellBook.GetSpellBookItemInfo, i, Enum.SpellBookSpellBank.Pet)
    if type(ii) == "table" and ii.name then out[#out + 1] = ii end
  end
  return out
end

-- the game's own tooltip of a spell, without its name: cost, range, cast time, cooldown, what it does
-- Spell descriptions: in this client the data API leaves the effect text out ("Instant | 3 min cooldown"); the game
-- fills it in only when a tooltip is actually shown. So descriptions are remembered from every spell tooltip the game
-- displays, and the spellbook is read once in the background through a hidden tooltip.
C.spellDesc = {}
do -- newly learned spells: read their descriptions too (event names differ between clients, so register safely)
  local f = CreateFrame("Frame")
  -- (UNIT_PET: a pet called brings its own spellbook)
  for _, e in ipairs({ "LEARNED_SPELL_IN_TAB", "LEARNED_SPELL_IN_SKILL_LINE", "SPELLS_CHANGED", "UNIT_PET" }) do
    pcall(f.RegisterEvent, f, e)
  end
  local pending
  f:SetScript("OnEvent", function()
    if pending then return end
    pending = true
    C_Timer.After(3, function() pending = false C.WarmSpellDescriptions() end)
  end)
end

local function tipParts(tip)
  local name = tip:GetName()
  local parts = {}
  for i = 2, math.min(40, tip:NumLines()) do
    local l = _G[name .. "TextLeft" .. i]
    local r = _G[name .. "TextRight" .. i]
    local lt = l and clean(l:GetText())
    local rt = r and r:IsShown() and clean(r:GetText())
    local t = (lt or "") .. ((rt and rt ~= "") and (" " .. rt) or "")
    t = strip(t):gsub("%s+", " ")
    if t ~= "" and t ~= " " then parts[#parts + 1] = t end
  end
  return parts
end

-- the tooltip shows this spell (a hidden tooltip that is read too early still holds the spell read before it)
function C.TipShows(tip, spellId)
  local left = _G[tostring(tip:GetName()) .. "TextLeft1"]
  local title = left and clean(left:GetText())
  local name = C_Spell.GetSpellName and safe(C_Spell.GetSpellName, spellId)
  if not name then return true end -- nothing to compare with
  return title ~= nil and strip(title) == name
end

local spellTipCache = {} -- spellId..maxLen -> text sent to the AI
local function remember(spellId, parts)
  local text = table.concat(parts, " | ")
  local old = C.spellDesc[spellId]
  if text ~= "" and (not old or #text > #old) then
    C.spellDesc[spellId] = text
    for k in pairs(spellTipCache) do if k:match("^(%d+)#") == tostring(spellId) then spellTipCache[k] = nil end end
  end
end

-- every spell tooltip the game shows (action bars, spellbook, talents, auras) is remembered
if TooltipDataProcessor and TooltipDataProcessor.AddTooltipPostCall and Enum.TooltipDataType then
  TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Spell, function(tip, data)
    local id = data and data.id
    if not id or not tip or not tip.GetName or not tip:GetName() then return end
    C_Timer.After(0.05, function()
      if tip:IsShown() and C.TipShows(tip, id) then pcall(function() remember(id, tipParts(tip)) end) end
    end)
  end)
end

-- read the whole spellbook once through a hidden tooltip, giving the game time to fill in the text
local scanTip
function C.WarmSpellDescriptions()
  if C.warming or not (C_SpellBook and C_SpellBook.GetNumSpellBookSkillLines) then return end
  scanTip = scanTip or CreateFrame("GameTooltip", "ForeverAIScanTip", nil, "GameTooltipTemplate")
  local ids = {}
  for t = 1, C_SpellBook.GetNumSpellBookSkillLines() do
    local li = C_SpellBook.GetSpellBookSkillLineInfo(t)
    if li and not li.shouldHide then
      for k = li.itemIndexOffset + 1, li.itemIndexOffset + li.numSpellBookItems do
        local ii = C_SpellBook.GetSpellBookItemInfo(k, Enum.SpellBookSpellBank.Player)
        if ii and ii.spellID and not C.spellDesc[ii.spellID] then -- only spells without a description yet
          ids[#ids + 1] = ii.spellID
          if C_Spell.RequestLoadSpellData then pcall(C_Spell.RequestLoadSpellData, ii.spellID) end
        end
      end
    end
  end
  for _, ii in ipairs(petBook()) do -- the pet's abilities too
    if ii.spellID and not C.spellDesc[ii.spellID] then ids[#ids + 1] = ii.spellID end
  end
  if #ids == 0 then return end
  C.warming = true
  local i, phase = 1, 0
  local ticker
  ticker = C_Timer.NewTicker(0.1, function()
    local id = ids[i]
    if not id then
      ticker:Cancel()
      scanTip:Hide()
      C.warming = false
      return
    end
    if phase == 0 then
      scanTip:SetOwner(WorldFrame, "ANCHOR_NONE")
      pcall(scanTip.SetSpellByID, scanTip, id)
      phase = 1
    else
      pcall(function() if C.TipShows(scanTip, id) then remember(id, tipParts(scanTip)) end end)
      i, phase = i + 1, 0
    end
  end)
end

-- the game's own tooltip of a spell, without its name: cost, range, cast time, cooldown, what it does
local function spellTip(spellId, maxLen)
  if not spellId then return nil end
  local cacheKey = spellId .. "#" .. maxLen
  local hit = spellTipCache[cacheKey]
  if hit ~= nil then return hit or nil end
  local parts = {}
  local data = C_TooltipInfo and C_TooltipInfo.GetSpellByID and safe(C_TooltipInfo.GetSpellByID, spellId)
  for i, line in ipairs(data and data.lines or {}) do
    if i > 1 and i <= 40 then
      local l, r = clean(line.leftText), clean(line.rightText)
      local t = (l or "") .. ((r and r ~= "") and (" " .. r) or "")
      t = strip(t):gsub("%s+", " ")
      if t ~= "" and t ~= " " then parts[#parts + 1] = t end
    end
  end
  local text = table.concat(parts, " | ")
  local seen = C.spellDesc[spellId]
  if seen and #seen > #text then text = seen end
  local desc = C_Spell and C_Spell.GetSpellDescription and clean(safe(C_Spell.GetSpellDescription, spellId))
  if type(desc) == "string" and desc ~= "" then
    desc = strip(desc):gsub("%s+", " ")
    if not text:find(desc:sub(1, 24), 1, true) then text = text == "" and desc or (text .. " | " .. desc) end
  end
  local out = text ~= "" and ns.Cut(text, maxLen) or false
  spellTipCache[cacheKey] = out
  return out or nil
end
C.spellTip = spellTip

-- spellbook with each spell's description from the game (so the AI knows what every ability does)
-- A spell known in several ranks is described once, at its highest rank; the lower ones are named on that line. (The
-- client keeps every rank in the book: a level 18 hunter's three Raptor Strikes, each with its full text.)
function S.spells()
  local out = {}
  if not (C_SpellBook and C_SpellBook.GetNumSpellBookSkillLines) then return out end
  local count, chars = 0, 60000 -- a safety cap for the whole spellbook; every spell normally gets its full text
  for t = 1, C_SpellBook.GetNumSpellBookSkillLines() do
    local li = C_SpellBook.GetSpellBookSkillLineInfo(t)
    if li and not li.shouldHide then
      local rows, top = {}, {}
      for k = li.itemIndexOffset + 1, li.itemIndexOffset + li.numSpellBookItems do
        budget()
        local ii = C_SpellBook.GetSpellBookItemInfo(k, Enum.SpellBookSpellBank.Player)
        if ii and ii.name and count < 200 then
          count = count + 1
          local sub = ii.spellID and C_Spell.GetSpellSubtext and safe(C_Spell.GetSpellSubtext, ii.spellID)
          local rank = type(sub) == "string" and tonumber(sub:match("^Rank (%d+)$")) or nil
          local row = { name = ii.name, sub = sub, rank = rank, id = ii.spellID, passive = ii.isPassive }
          rows[#rows + 1] = row
          if rank and (not top[ii.name] or rank > top[ii.name].rank) then top[ii.name] = row end
        end
      end
      for _, row in ipairs(rows) do
        local best = row.rank and top[row.name]
        if best and best ~= row then
          best.lower = best.lower or {}
          best.lower[#best.lower + 1] = row.rank
        else
          local label = row.name .. ((row.sub and row.sub ~= "") and (" (" .. row.sub .. ")") or "")
          local tip = chars > 0 and spellTip(row.id, 1500) or nil
          chars = chars - (tip and #tip or 0)
          row.line = { li.name or "?", label, row.passive and " [passive]" or "", tip and (": " .. tip) or "" }
        end
      end
      for _, row in ipairs(rows) do
        if row.line then
          local also = ""
          if row.lower then
            table.sort(row.lower)
            also = " [also known at rank " .. table.concat(row.lower, ", ") .. ", for less mana]"
          end
          out[#out + 1] = string.format("[%s] %s%s%s%s", row.line[1], row.line[2], row.line[3], also, row.line[4])
        end
      end
    end
  end
  return out
end

------------------------------------------------------------------------ action bars
-- Every bar the UI has (the game's own bars or a bar add-on), read from the action buttons themselves: which action
-- slot a button shows (paging included), under which key, and whether its bar is shown.
local actionButtons -- found by scanning the UI's frames; scanned again when bars change
do
  local f = CreateFrame("Frame")
  -- (not ADDON_LOADED: every answer add-on that is read fires it, and bar add-ons create their buttons before
  -- PLAYER_ENTERING_WORLD anyway)
  for _, e in ipairs({ "PLAYER_ENTERING_WORLD", "UPDATE_MULTI_ACTIONBAR", "ACTIONBAR_PAGE_CHANGED",
                       "UPDATE_BONUS_ACTIONBAR" }) do
    pcall(f.RegisterEvent, f, e)
  end
  f:SetScript("OnEvent", function() actionButtons = nil end)
end

local function buttonSlot(b)
  local a = rawget(b, "action")
  if type(a) == "number" and a > 0 then return a end
  if rawget(b, "_state_type") == "action" then return tonumber(rawget(b, "_state_action")) end -- LibActionButton
  return nil
end

local function findActionButtons()
  local list, n = {}, 0
  local f = EnumerateFrames()
  while f do
    n = n + 1
    if n % 300 == 0 then budget() end
    local ok, slot = pcall(buttonSlot, f)
    if ok and slot and not (f.IsForbidden and f:IsForbidden()) then list[#list + 1] = f end
    f = EnumerateFrames(f)
  end
  return list
end

-- a pet's ability put on the player's bar: the action may carry the pet action's id (flags in its top byte) rather
-- than the spell's
local function petSpellLabel(id)
  local low = type(id) == "number" and id % 0x1000000 or nil
  local label = low and low ~= id and spellLabel(low)
  if label then return label end
  for _, ii in ipairs(petBook()) do
    if id ~= nil and (ii.actionID == id or ii.spellID == id or (low and ii.spellID == low)) then
      return spellLabel(ii.spellID) or (ii.name .. ((ii.subName and ii.subName ~= "") and (" (" .. ii.subName .. ")") or ""))
    end
  end
  return nil
end

-- the first line of the button's own tooltip: the game names every kind of action there
local function actionTip(slot)
  local data = C_TooltipInfo and C_TooltipInfo.GetAction and safe(C_TooltipInfo.GetAction, slot)
  local first = type(data) == "table" and type(data.lines) == "table" and data.lines[1]
  local t = type(first) == "table" and clean(first.leftText)
  t = type(t) == "string" and strip(t) or nil
  return t ~= "" and t or nil
end

local named = {} -- actions named other than by their id: logged once, to learn what this client gives for them
local function actionText(slot)
  local kind, id, sub = safe(GetActionInfo, slot)
  if not kind then return nil end
  local name, pet
  if kind == "spell" then
    name = spellLabel(id)
    if not name then
      name = petSpellLabel(id)
      pet = name ~= nil
    end
    pet = pet or sub == "pet"
  elseif kind == "item" then
    name = (C_Item.GetItemNameByID and safe(C_Item.GetItemNameByID, id)) or safe(C_Item.GetItemInfo, id)
  elseif kind == "macro" then
    local mname = safe(GetMacroInfo, id)
    local mspell = GetMacroSpell and safe(GetMacroSpell, id)
    local what = mspell and spellLabel(mspell) or (GetMacroItem and safe(GetMacroItem, id))
    name = "macro " .. tostring(mname or id) .. (what and (" → " .. what) or "")
  elseif kind == "flyout" then
    local fname = GetFlyoutInfo and safe(GetFlyoutInfo, id)
    name = fname and ("flyout " .. fname)
  elseif kind == "equipmentset" then
    local set = type(id) == "string" and id or (C_EquipmentSet and C_EquipmentSet.GetEquipmentSetInfo
      and safe(C_EquipmentSet.GetEquipmentSetInfo, id))
    name = set and ("equipment set " .. set)
  elseif kind == "summonmount" then
    local mount = C_MountJournal and C_MountJournal.GetMountInfoByID and safe(C_MountJournal.GetMountInfoByID, id)
    name = mount and ("mount " .. mount)
  elseif kind == "summonpet" then
    local r = { C_PetJournal and C_PetJournal.GetPetInfoByPetID and safe(C_PetJournal.GetPetInfoByPetID, id) }
    name = (r[2] or r[8]) and ("companion " .. (r[2] or r[8]))
  end
  local how = pet and name and "pet" or nil
  if not name then name, how = actionTip(slot), "tooltip" end
  if not name then name, how = safe(GetActionText, slot), "action text" end
  if not name then name, how = tostring(kind) .. " " .. tostring(id), "unnamed" end
  local key = tostring(kind) .. "|" .. tostring(id) .. "|" .. tostring(sub)
  if how and not named[key] and ns.Log then
    named[key] = true
    ns.Log("bars", string.format("slot %d: %s %s %s = %s (%s)", slot, tostring(kind), tostring(id), tostring(sub),
      tostring(name), how))
  end
  local text = tostring(name) .. (pet and " [pet ability]" or "")
  if kind == "item" then
    local count = GetActionCount and safe(GetActionCount, slot)
    if count and count > 0 then text = text .. " x" .. count end
  end
  return text
end

-- the key shown on the button (works for every bar add-on), or its key binding
local function buttonKey(b)
  local name = b:GetName()
  local hk = rawget(b, "HotKey") or (name and _G[name .. "HotKey"])
  local t = hk and hk.GetText and clean(hk:GetText())
  if t and t ~= "" and t ~= RANGE_INDICATOR then return t end
  local cmd = rawget(b, "commandName") or rawget(b, "bindingAction")
  local key = (cmd and safe(GetBindingKey, cmd)) or (name and safe(GetBindingKey, "CLICK " .. name .. ":LeftButton"))
  return key and ((GetBindingText and safe(GetBindingText, key, "KEY_")) or key) or nil
end

function S.actionBars()
  if not GetActionInfo then return nil end
  actionButtons = actionButtons or findActionButtons()
  local bars, order, onBar = {}, {}, {}
  for _, b in ipairs(actionButtons) do
    budget()
    local ok, slot = pcall(buttonSlot, b)
    -- only buttons the player sees: hidden bars (vehicle, override, gamepad layouts) repeat the same actions
    if ok and slot and b:IsVisible() then
      local parent = b:GetParent()
      -- a bar is its buttons' parent; some interfaces wrap every button in its own container ("...Container7")
      local barName = ((parent and parent.GetName and parent:GetName()) or b:GetName() or "?"):gsub("%d+$", "")
      local bar = bars[barName]
      if not bar then
        bar = { total = 0, items = {} }
        bars[barName] = bar
        order[#order + 1] = barName
      end
      bar.total = bar.total + 1
      local text = actionText(slot)
      if text then
        onBar[slot] = true
        local key = buttonKey(b)
        local num = tonumber((b:GetName() or ""):match("(%d+)$")) or bar.total
        bar.items[#bar.items + 1] = { n = num, text = string.format("button %d%s: %s", num, key and (" [key " .. key .. "]") or "", text) }
      end
    end
  end
  local out = {}
  for _, name in ipairs(order) do
    local bar = bars[name]
    if #bar.items > 0 then -- an empty bar says nothing
      out[#out + 1] = string.format("%s — %d of %d buttons used", name, #bar.items, bar.total)
      table.sort(bar.items, function(x, y) return x.n < y.n end)
      for _, it in ipairs(bar.items) do out[#out + 1] = "  " .. it.text end
    end
  end
  -- actions the player has placed but no bar shows right now (other pages, stance bars); slots of every client fit
  -- below 200
  local extra = 0
  for slot = 1, 200 do
    if not onBar[slot] and safe(HasAction, slot) then
      local text = actionText(slot)
      if text and extra < 24 then
        extra = extra + 1
        out[#out + 1] = string.format("slot %d (not on a shown bar): %s", slot, text)
      end
    end
  end
  return out
end

local function skillLines(numFn, infoFn)
  local out, seen = {}, {}
  if not (numFn and infoFn) then return out end
  for i = 1, math.min(80, safe(numFn) or 0) do
    local r = { safe(infoFn, i) }
    local info = r[1]
    local name, header, rank, maxRank
    if type(info) == "table" then
      -- field names differ between client versions
      name = info.skillName or info.name or info.skillLineDisplayName or info.displayName
      header = info.isHeader or info.header
      rank = info.skillRank or info.rank or info.skillLineRank or info.currentRank
      maxRank = info.skillMaxRank or info.maxRank or info.skillLineMaxRank
    elseif type(info) == "string" then
      name, header, rank, maxRank = info, r[2], r[4], r[7]
    end
    -- WoW Forever lists every profession twice, in either API
    if name and not header and not seen[name] then
      seen[name] = true
      out[#out + 1] = string.format("%s %s/%s", name, tostring(rank), tostring(maxRank))
    end
  end
  return out
end

-- weapon skills, professions, languages: the first API of this client that returns anything
function S.skills()
  local out = C_SkillInfo and skillLines(C_SkillInfo.GetNumSkillLines, C_SkillInfo.GetSkillLineInfo) or {}
  if #out == 0 then out = skillLines(GetNumSkillLines, GetSkillLineInfo) end
  if #out == 0 and ns.Events and ns.Events.KnownSkills then
    out = ns.Events.KnownSkills() -- learned from "Your skill in X has increased to N" messages
  end
  if #out == 0 and GetProfessions and GetProfessionInfo then
    for _, idx in ipairs({ safe(GetProfessions) }) do
      if type(idx) == "number" then
        local name, _, rank, maxRank = safe(GetProfessionInfo, idx)
        if name then out[#out + 1] = string.format("%s %s/%s", name, tostring(rank), tostring(maxRank)) end
      end
    end
  end
  return out
end

function S.reputations()
  local out = {}
  if not (C_Reputation and C_Reputation.GetNumFactions) then return out end
  for i = 1, math.min(80, safe(C_Reputation.GetNumFactions) or 0) do
    local f = safe(C_Reputation.GetFactionDataByIndex, i)
    if f and not f.isHeader and f.name then
      local standing = _G["FACTION_STANDING_LABEL" .. tostring(f.reaction)] or tostring(f.reaction)
      local span = (f.nextReactionThreshold or 0) - (f.currentReactionThreshold or 0)
      out[#out + 1] = string.format("%s: %s (%d/%d)", f.name, standing,
        (f.currentStanding or 0) - (f.currentReactionThreshold or 0), span)
    end
  end
  return out
end

local function auras(unit, filter, max)
  local out = {}
  if not (C_UnitAuras and C_UnitAuras.GetAuraDataByIndex) then return out end
  for i = 1, max or 20 do
    local a = safe(C_UnitAuras.GetAuraDataByIndex, unit, i, filter)
    if not a then break end
    local name = clean(a.name)
    if name then
      local s = name
      if (a.applications or 0) > 1 then s = s .. " x" .. a.applications end
      if a.expirationTime and a.expirationTime > 0 then
        local left = clean(a.expirationTime)
        if left then s = s .. string.format(" (%ds)", math.max(0, math.floor(left - GetTime()))) end
      end
      out[#out + 1] = s
    end
  end
  return out
end

function S.auras()
  return { buffs = auras("player", "HELPFUL", 30), debuffs = auras("player", "HARMFUL", 15) }
end

-- The quests the character can pick up in a zone, or that belong to it (a dungeon: its quests are given elsewhere),
-- wherever the player stands: from Questie's own list of what is available to them, so quests that are done, another
-- class's, or a step of a chain not reached yet are not in it. Asked for by the AI (its zone_quests tool) when the
-- player plans to go somewhere: the "nearbyQuests" section only reaches around the player.
local ZONE_QUESTS_MAX = 30
function C.ZoneQuests(zone)
  local want = tostring(zone or ""):lower():gsub("^the ", "")
  if want == "" then return { error = "no zone named" } end
  local QDB = questieDB()
  local okA, AQ = pcall(function() return QuestieLoader:ImportModule("AvailableQuests") end)
  if not (QDB and okA and type(AQ) == "table" and type(AQ.__availableQuests) == "table") then
    return { error = "Questie is not loaded or has not worked out the available quests yet" }
  end
  local function areaName(id)
    local name = type(id) == "number" and id > 0 and C_Map.GetAreaInfo and C_Map.GetAreaInfo(id)
    return type(name) == "string" and name or nil
  end
  local function matches(name)
    name = name and name:lower():gsub("^the ", "")
    return name ~= nil and (name == want or name:find(want, 1, true) == 1 or want:find(name, 1, true) == 1)
  end
  local myLevel = UnitLevel("player") or 1
  local found, later = {}, 0
  for questId in pairs(AQ.__availableQuests) do
    local q = safe(QDB.GetQuest, questId)
    if type(q) == "table" and q.name then
      local here, anywhere
      for _, npcId in ipairs((type(q.startedBy) == "table" and q.startedBy[1]) or {}) do
        local npc = safe(QDB.GetNPC, QDB, npcId)
        for areaId, spawns in pairs(type(npc) == "table" and type(npc.spawns) == "table" and npc.spawns or {}) do
          local s, name = type(spawns) == "table" and spawns[1], areaName(areaId)
          if s and s[1] and name then
            local at = { npc = npc.name, where = string.format("%s %.1f, %.1f", name, s[1], s[2]) }
            anywhere = anywhere or at
            if matches(name) then here = here or at end
          end
        end
      end
      local about = areaName(tonumber(q.zoneOrSort)) -- the zone or dungeon the quest is filed under
      if (here or matches(about)) and (here or anywhere) then
        if (tonumber(q.requiredLevel) or 0) > myLevel then
          later = later + 1 -- not yet theirs to take
        else
          local giver = here or anywhere
          local e = { id = questId, title = q.name, level = tonumber(q.questLevel), minLevel = tonumber(q.requiredLevel),
            giver = giver.npc, where = giver.where, about = about,
            objective = type(q.objectivesText) == "table" and ns.Cut(table.concat(q.objectivesText, " "), 220) or nil,
            turnIn = questieGivers(QDB, q.finishedBy) }
          -- the quest that follows it in its chain, as the quest log section says it: a route plans for it
          local nextId = tonumber(q.nextQuestInChain)
          local nq = nextId and nextId > 0 and safe(QDB.GetQuest, nextId)
          local nextGiver = type(nq) == "table" and nq.name and questieGivers(QDB, nq.startedBy)
          if nextGiver and (tonumber(nq.requiredLevel) or 0) <= myLevel then
            e.next = string.format("[%s]%s from %s", nq.name, nq.questLevel and (" (level " .. nq.questLevel .. ")") or "", nextGiver)
            e.nextId = nextId
          end
          local _, tag = safe(QDB.GetQuestTagInfo, questId)
          if type(tag) == "string" and tag ~= "" then e.type = tag end
          local okS, sources = pcall(objectiveSources, QDB, questId)
          local lines = okS and whereToGet(sources, nil, nil, nil, nil, nil) or nil
          if lines then
            for i = #lines, 4, -1 do lines[i] = nil end
            for i, line in ipairs(lines) do lines[i] = ns.Cut(line, 240) end
            e.whereToGet = lines
          end
          if giver.npc then C.npcCoords[giver.npc] = C.npcCoords[giver.npc] or nil end
          found[#found + 1] = e
        end
      end
    end
  end
  local function byLevel(a, b)
    if (a.level or 0) ~= (b.level or 0) then return (a.level or 0) < (b.level or 0) end
    return a.title < b.title
  end
  local more = math.max(0, #found - ZONE_QUESTS_MAX)
  if more > 0 then -- too many to list: the ones furthest from the character's level go
    table.sort(found, function(a, b)
      local da, db = math.abs((a.level or myLevel) - myLevel), math.abs((b.level or myLevel) - myLevel)
      if da ~= db then return da < db end
      return byLevel(a, b)
    end)
    for i = #found, ZONE_QUESTS_MAX + 1, -1 do found[i] = nil end
  end
  table.sort(found, byLevel)
  return { zone = zone, quests = found, more = more > 0 and more or nil, later = later > 0 and later or nil,
           level = myLevel }
end

function C.UnitInfo(unit)
  if not UnitExists(unit) then return nil end
  local u = { name = safe(UnitName, unit), level = safe(UnitLevel, unit), isPlayer = UnitIsPlayer(unit) or nil }
  u.classification = safe(UnitClassification, unit)
  u.creatureType = safe(UnitCreatureType, unit)
  local reaction = safe(UnitReaction, unit, "player")
  u.reaction = reaction and (reaction >= 5 and "friendly" or reaction == 4 and "neutral" or "hostile")
  u.dead = UnitIsDead(unit) or nil
  local hp, hpMax = safe(UnitHealth, unit), safe(UnitHealthMax, unit)
  if hp and hpMax and hpMax > 0 then u.healthPct = math.floor(hp / hpMax * 100) end
  if u.isPlayer then
    u.class = safe(UnitClass, unit)
    u.race = safe(UnitRace, unit)
    u.guild = safe(GetGuildInfo, unit)
    local guid = safe(UnitGUID, unit)
    u.inspect = guid and ns.Players and ns.Players.InspectCache(guid)
  end
  local tt = C_TooltipInfo and C_TooltipInfo.GetUnit and safe(C_TooltipInfo.GetUnit, unit)
  u.tooltip = tooltipText(tt, 30)
  u.buffs = auras(unit, "HELPFUL", 10)
  u.debuffs = auras(unit, "HARMFUL", 10)
  if #u.buffs == 0 then u.buffs = nil end
  if #u.debuffs == 0 then u.debuffs = nil end
  if UnitExists(unit .. "target") then u.target = safe(UnitName, unit .. "target") end
  return u
end

-- How large every zone map is, in meters ("Elwynn Forest: 3173 x 2115"): the app's route planner measures the way on
-- maps the player is not on with it (a route that goes on into other zones). The maps do not change: read once.
local mapSizes
function S.mapSizes()
  if mapSizes then return mapSizes end
  if not (C_Map.GetMapChildrenInfo and Enum and Enum.UIMapType) then return nil end
  local top = C_Map.GetBestMapForUnit("player")
  for _ = 1, 10 do -- up to the world map
    local info = top and C_Map.GetMapInfo(top)
    if not info or info.mapType == Enum.UIMapType.World or not info.parentMapID or info.parentMapID == 0 then break end
    top = info.parentMapID
  end
  local out, seen = {}, {}
  for _, child in ipairs(top and safe(C_Map.GetMapChildrenInfo, top, Enum.UIMapType.Zone, true) or {}) do
    budget()
    local size = child.name and not seen[child.name] and ns.MapSize(child.mapID)
    if size then
      seen[child.name] = true
      out[#out + 1] = string.format("%s: %d x %d", child.name, size[1] * 0.9144 + 0.5, size[2] * 0.9144 + 0.5)
    end
  end
  table.sort(out)
  if #out > 0 then mapSizes = out end
  return mapSizes
end

function S.target() return C.UnitInfo("target") end
function S.mouseover() return C.UnitInfo("mouseover") end
-- a pet function of this client: WoW Forever has them in C_PetInfo (C_PetInfo.GetPetHappiness), older clients as globals
local function petApi(name)
  return (C_PetInfo and rawget(C_PetInfo, name)) or _G[name]
end

-- what in the bags the pet eats, the way the game decides it
local function petFood()
  local canEat = C_PetInfo and rawget(C_PetInfo, "CanPetEatItem")
  if type(canEat) ~= "function" then return nil end
  local counts, order = {}, {}
  for bag = 0, 5 do
    for slot = 1, (safe(C_Container.GetContainerNumSlots, bag) or 0) do
      budget()
      local info = safe(C_Container.GetContainerItemInfo, bag, slot)
      if info and info.itemID and info.hyperlink and safe(canEat, info.itemID) then
        local name = strip(info.hyperlink)
        if not counts[name] then order[#order + 1] = name end
        counts[name] = (counts[name] or 0) + (info.stackCount or 1)
      end
    end
  end
  if #order == 0 then return "nothing in the bags" end
  for i, name in ipairs(order) do order[i] = name .. " x" .. counts[name] end
  return table.concat(order, ", ")
end

local petLogged
function S.pet()
  local u = C.UnitInfo("pet")
  if not u then return nil end
  u.family = UnitCreatureFamily and safe(UnitCreatureFamily, "pet")
  -- a hunter's pet: happiness, loyalty, training points, diet, its abilities and its action bar
  local happiness = safe(petApi("GetPetHappiness"))
  if type(happiness) == "number" then u.happiness = ({ "unhappy", "content", "happy" })[happiness] end
  local loyalty = safe(petApi("GetPetLoyalty"))
  if loyalty then u.loyalty = loyalty end
  local total, spent = safe(petApi("GetPetTrainingPoints"))
  if type(total) == "number" then
    u.trainingPoints = string.format("%d free (%d spent)", total - (tonumber(spent) or 0), tonumber(spent) or 0)
  end
  local xp, xpMax = safe(petApi("GetPetExperience"))
  if type(xp) == "number" and type(xpMax) == "number" and xpMax > 0 then u.xp = xp .. "/" .. xpMax end
  -- the diet: names, as one list or as several values (this client returns a list)
  local diet, raw = {}, { safe(petApi("GetPetFoodTypes")) }
  for _, v in ipairs(raw) do
    for _, name in ipairs(type(v) == "table" and v or { v }) do
      if type(name) == "string" and name ~= "" then diet[#diet + 1] = name end
    end
  end
  if #diet > 0 then u.diet = table.concat(diet, ", ") end
  if not petLogged and ns.Log then -- once: what this client gives, to learn from
    petLogged = true
    ns.Log("pet", string.format("happiness %s, loyalty %s, training %s/%s, xp %s/%s, diet %s (%d values, first %s)",
      tostring(happiness), tostring(loyalty), tostring(total), tostring(spent), tostring(xp), tostring(xpMax),
      table.concat(diet, "+"), #raw, type(raw[1])))
  end
  local okFood, food = pcall(petFood)
  if okFood then u.food = food end
  local commands = {}
  if GetPetActionInfo then
    local acts = {}
    for i = 1, 10 do
      local name, _, isToken, isActive, autoAllowed, autoOn = safe(GetPetActionInfo, i)
      if name then
        if isToken then
          name = _G[name] or name
          commands[name] = true
        end
        acts[#acts + 1] = tostring(name) .. (isActive and " [active]" or "")
          .. (autoOn and " [auto-cast on]" or autoAllowed and " [auto-cast off]" or "")
      end
    end
    if #acts > 0 then u.bar = acts end
  end
  -- what each of its abilities does (the commands are on its bar)
  local abilities = {}
  for _, ii in ipairs(petBook()) do
    if ii.spellID and not commands[ii.name] then
      local label = spellLabel(ii.spellID) or ii.name
      local tip = spellTip(ii.spellID, 600)
      abilities[#abilities + 1] = label .. (ii.isPassive and " [passive]" or "") .. (tip and (": " .. tip) or "")
    end
  end
  if #abilities > 0 then u.abilities = abilities end
  return u
end

function S.tooltip()
  local now = C.CaptureTooltip()
  local t = now or C.lastTooltip
  if not t then return nil end
  return { lines = t.lines, item = t.item, unit = t.unit, ageSeconds = time() - (t.at or time()) }
end

function S.group()
  local out = {}
  local n = GetNumGroupMembers()
  if n == 0 then return nil end
  local prefix = IsInRaid() and "raid" or "party"
  for i = 1, (prefix == "raid" and n or n - 1) do
    local unit = prefix .. i
    if UnitExists(unit) then
      out[#out + 1] = string.format("%s lvl %s %s%s%s", tostring(safe(UnitName, unit)), tostring(safe(UnitLevel, unit)),
        tostring(safe(UnitClass, unit)), UnitIsConnected(unit) and "" or " (offline)", UnitIsDeadOrGhost(unit) and " (dead)" or "")
    end
  end
  return out
end

function S.nearby()
  return ns.Players and ns.Players.Nearby() or nil
end

------------------------------------------------------------------------ open windows
local function shown(name)
  local f = _G[name]
  return f and f.IsShown and f:IsShown()
end

-- NPC interactions the game reports through events (works with add-ons that replace the default windows)
local interaction = {}
do
  local map = { MERCHANT_SHOW = "merchant", MERCHANT_CLOSED = false, GOSSIP_SHOW = "gossip", GOSSIP_CLOSED = false,
    QUEST_DETAIL = "quest", QUEST_PROGRESS = "quest", QUEST_COMPLETE = "quest", QUEST_GREETING = "quest",
    QUEST_FINISHED = false, TRAINER_SHOW = "trainer", TRAINER_CLOSED = false, LOOT_OPENED = "loot", LOOT_CLOSED = false }
  local closes = { MERCHANT_CLOSED = "merchant", GOSSIP_CLOSED = "gossip", QUEST_FINISHED = "quest",
    TRAINER_CLOSED = "trainer", LOOT_CLOSED = "loot" }
  local f = CreateFrame("Frame")
  for e in pairs(map) do pcall(f.RegisterEvent, f, e) end
  f:SetScript("OnEvent", function(_, e)
    if map[e] then interaction[map[e]] = true else interaction[closes[e]] = nil end
  end)
end
function ns.MerchantOpen() return interaction.merchant or shown("MerchantFrame") or false end

function S.windows()
  local w = { open = {} }
  -- every window the game registers as a panel or as closable with Esc, whichever UI shows it
  local seen = {}
  local function check(name)
    if type(name) ~= "string" or seen[name] or name:find("^ForeverAI") then return end
    seen[name] = true
    if shown(name) then w.open[#w.open + 1] = name end
  end
  for name in pairs(UIPanelWindows or {}) do check(name) end
  for _, name in ipairs(UISpecialFrames or {}) do check(name) end
  table.sort(w.open)
  if (interaction.gossip or shown("GossipFrame")) and C_GossipInfo then
    local g = { text = safe(C_GossipInfo.GetText), options = {}, npc = safe(UnitName, "npc") }
    for _, o in ipairs(safe(C_GossipInfo.GetOptions) or {}) do g.options[#g.options + 1] = o.name end
    for _, q in ipairs(safe(C_GossipInfo.GetAvailableQuests) or {}) do g.options[#g.options + 1] = "(new quest) " .. tostring(q.title) end
    for _, q in ipairs(safe(C_GossipInfo.GetActiveQuests) or {}) do g.options[#g.options + 1] = "(active quest) " .. tostring(q.title) end
    w.gossip = g
  end
  if interaction.quest or shown("QuestFrame") then
    local q = { npc = safe(UnitName, "npc"), title = safe(GetTitleText), text = safe(GetQuestText),
      objectives = safe(GetObjectiveText), reward = safe(GetRewardText), progress = safe(GetProgressText) }
    local choices = {}
    for i = 1, (safe(GetNumQuestChoices) or 0) do
      local link = GetQuestItemLink and safe(GetQuestItemLink, "choice", i)
      C.Remember(link)
      local tip = C_TooltipInfo and C_TooltipInfo.GetQuestItem and safe(C_TooltipInfo.GetQuestItem, "choice", i)
      choices[#choices + 1] = { item = link and strip(link) or tostring(safe(GetQuestItemInfo, "choice", i)), tooltip = tooltipText(tip, 60),
        facts = C.ItemFacts(link, true) }
    end
    if #choices > 0 then q.rewardChoices = choices end
    local fixed = {}
    for i = 1, (safe(GetNumQuestRewards) or 0) do
      local link = GetQuestItemLink and safe(GetQuestItemLink, "reward", i)
      local facts = C.ItemFacts(link, true)
      fixed[#fixed + 1] = (link and strip(link) or tostring(safe(GetQuestItemInfo, "reward", i))) ..
        (facts and facts.stats and (" — " .. facts.stats .. (facts.vsEquipped and (" (" .. facts.vsEquipped .. ")") or "")) or "")
    end
    if #fixed > 0 then q.rewards = fixed end
    q.money = money(safe(GetRewardMoney))
    w.questDialog = q
  end
  if ns.Auction and ns.Auction.IsOpen() then -- auctions can be posted and cancelled now (propose_auctions)
    w.auctionHouse = { open = true, lengthsHours = ns.Market and table.concat(ns.Market.Durations(), ", ") or nil }
  end
  if ns.MerchantOpen() then
    local m = { npc = safe(UnitName, "npc"), items = {} }
    for i = 1, math.min(60, safe(GetMerchantNumItems) or 0) do
      local info = C_MerchantFrame and C_MerchantFrame.GetItemInfo and safe(C_MerchantFrame.GetItemInfo, i)
      if type(info) == "table" and info.name then
        local link = GetMerchantItemLink and safe(GetMerchantItemLink, i)
        local facts = link and C.ItemFacts(link, true)
        m.items[#m.items + 1] = string.format("%s — %s%s%s", info.name, money(info.price) or "?",
          info.isUsable == false and " (not usable)" or "",
          facts and facts.stats and (" | " .. facts.stats .. (facts.vsEquipped and (" | " .. facts.vsEquipped) or "")
            .. (facts.requiresLevel and (" | requires level " .. facts.requiresLevel) or "")) or "")
      end
    end
    -- repairs and what can be sold here (buyback is used to undo a sale)
    local canRepair = CanMerchantRepair and safe(CanMerchantRepair)
    m.canRepair = canRepair and true or false
    if canRepair and GetRepairAllCost then
      local cost, needed = safe(GetRepairAllCost)
      m.repairCost = (needed and cost and cost > 0) and money(cost) or "nothing to repair"
      m.repairCopper = needed and cost or 0
    end
    m.note = "Vendor window open: propose_vendor_action op=sell / op=repair are available."
    w.merchant = m
  end
  if interaction.trainer or shown("ClassTrainerFrame") then
    local t = { npc = safe(UnitName, "npc"), services = {} }
    for i = 1, math.min(80, safe(GetNumTrainerServices) or 0) do
      local name, sub, kind = safe(GetTrainerServiceInfo, i)
      if name and kind ~= "header" then
        local cost = safe(GetTrainerServiceCost, i)
        local lvl = GetTrainerServiceLevelReq and safe(GetTrainerServiceLevelReq, i)
        -- what it does, as the trainer's window says it (in full for what can be learned now)
        local what = GetTrainerServiceDescription and clean((safe(GetTrainerServiceDescription, i)))
        what = type(what) == "string" and strip(what):gsub("%s+", " ") or ""
        what = what ~= "" and (": " .. ns.Cut(what, kind == "available" and 400 or 160)) or ""
        t.services[#t.services + 1] = string.format("%s%s — %s, level %s, %s%s", name, (sub and sub ~= "") and (" (" .. sub .. ")") or "",
          money(cost) or "?", tostring(lvl), tostring(kind), what)
      end
    end
    w.trainer = t
  end
  if interaction.loot or shown("LootFrame") then
    local l = {}
    for i = 1, (safe(GetNumLootItems) or 0) do
      local _, name, qty = safe(GetLootSlotInfo, i)
      if name then l[#l + 1] = name .. (qty and qty > 1 and (" x" .. qty) or "") end
    end
    w.loot = l
  end
  if shown("WorldMapFrame") and WorldMapFrame.GetMapID then
    local id = WorldMapFrame:GetMapID()
    local info = id and C_Map.GetMapInfo(id)
    w.worldMap = info and info.name
  end
  if #w.open == 0 then w.open = nil end
  return w
end

function S.addons()
  return ns.Addons and ns.Addons.List() or nil
end

function S.addonChanges()
  local out = {}
  local log = ns.db.changeLog
  for i = math.max(1, #log - 4), #log do
    local e = log[i]
    out[#out + 1] = string.format("%s %s %s: %s → %s (%s)", e.at or "", e.addon or "", e.path or e.command or e.op or "",
      tostring(e.old), tostring(e.new), e.undone and "undone" or "applied")
  end
  return #out > 0 and out or nil
end

function S.chat()
  return ns.recentChat
end

ns.recentChat = {}
for _, ev in ipairs({ "CHAT_MSG_SAY", "CHAT_MSG_YELL", "CHAT_MSG_PARTY", "CHAT_MSG_PARTY_LEADER", "CHAT_MSG_GUILD",
  "CHAT_MSG_WHISPER", "CHAT_MSG_RAID", "CHAT_MSG_CHANNEL" }) do
  ns.On(ev, function(event, text, sender, _, channel, _, _, _, _, channelName, _, _, guid)
    if not (ns.db and ns.db.sections and ns.db.sections.chat) then return end
    text, sender = clean(text), clean(sender)
    if not text then return end
    table.insert(ns.recentChat, string.format("[%s] %s: %s", event:gsub("CHAT_MSG_", ""):lower(), tostring(sender), strip(text)))
    while #ns.recentChat > 20 do table.remove(ns.recentChat, 1) end
  end)
end

------------------------------------------------------------------------ snapshot
S.sinceLastAnswer = function() return ns.SeenSection and ns.SeenSection() or nil end
local ORDER = { "game", "sinceLastAnswer", "player", "progress", "stats", "gearTotals", "equipment", "talents", "quests", "marks", "detours", "routeDone", "tracked",
  "nearbyQuests", "services", "zoneNPCs", "flightMasters", "mapSizes", "nearbyMobs", "around", "recentEvents", "actionBars", "target", "mouseover", "pet", "tooltip", "group",
  "nearby", "auras", "windows", "bags", "bank", "spells", "skills", "professions", "reputations", "addons",
  "addonChanges", "chat" }
local OPTIONAL = { bags = true, bank = "bags", spells = true, actionBars = "spells", around = "nearby", skills = true, reputations = true, nearby = true,
  windows = true, addons = true, auras = true, chat = true, talents = true, quests = true, nearbyQuests = "quests",
  progress = true, professions = true }
-- dropped first when the packet is too big
local DROP_ORDER = { "chat", "professions", "reputations", "skills", "spells", "bank", "addons", "auras", "bags",
  "progress", "nearby" }
C.ORDER, C.OPTIONAL, C.DROP_ORDER = ORDER, OPTIONAL, DROP_ORDER

local function enabledSections()
  local sections = ns.db.sections
  local keys = {}
  for _, key in ipairs(ORDER) do
    local opt = OPTIONAL[key]
    local enabled = true
    if opt == true then enabled = sections[key] ~= false
    elseif type(opt) == "string" then enabled = sections[opt] ~= false end
    if key == "chat" then enabled = sections.chat == true end
    if enabled and S[key] then keys[#keys + 1] = key end
  end
  return keys
end

local function keep(snap, key, val)
  if val ~= nil and not (type(val) == "table" and next(val) == nil) then snap[key] = val end
end

local fitSize

-- everything in one go (one frame); the add-on itself uses SnapshotAsync
function C.Snapshot()
  pcall(C.RefreshLinks)
  local snap, errors = {}, {}
  for _, key in ipairs(enabledSections()) do
    local ok, val = pcall(S[key])
    if ok then keep(snap, key, val) else errors[#errors + 1] = key .. ": " .. tostring(val) end
  end
  if #errors > 0 then snap.collectErrors = errors end
  return fitSize(snap)
end

-- In the background: every section runs in its own coroutine (a failing section is reported, not fatal) and its long
-- loops yield through C.Budget, so no frame spends more than ~BUDGET_MS on the snapshot and the game never stalls.
-- done(snapshot, perf) runs once everything is gathered; perf tells where the time went.
local runner = CreateFrame("Frame")
runner:Hide()
local job

local function finish(j)
  job = nil
  runner:Hide()
  if #j.errors > 0 then j.snap.collectErrors = j.errors end
  local list, total = {}, 0
  for k, ms in pairs(j.ms) do
    total = total + ms
    list[#list + 1] = { k = k, ms = ms }
  end
  table.sort(list, function(a, b) return a.ms > b.ms end)
  local slow = {}
  for n = 1, math.min(5, #list) do slow[n] = string.format("%s %.0f", list[n].k, list[n].ms) end
  local perf = { cpuMs = math.floor(total + 0.5), frames = j.frames, wallMs = math.floor(debugprofilestop() - j.started),
                 slowest = table.concat(slow, ", ") }
  local okF, snap = pcall(fitSize, j.snap)
  local okD, err = pcall(j.done, okF and snap or j.snap, perf)
  if not okD then
    ns.lastError = "snapshot: " .. tostring(err)
    ns.collecting = false
    if ns.UI then ns.UI.FailAnswer("Could not prepare the question: " .. tostring(err)) end
  end
end

-- how long a question waits for the NPC data of the quest database (read over some frames after login): without it
-- the game data has no flight masters, no services and no NPCs of the zone
local NPC_WAIT_MS = 8000

runner:SetScript("OnUpdate", function()
  local j = job
  if not j then runner:Hide() return end
  if j.waitNpcs then
    if not (ns.Npcs and ns.Npcs.Loading and ns.Npcs.Loading()) or debugprofilestop() - j.started > NPC_WAIT_MS then
      j.waitNpcs = nil
    else
      return
    end
  end
  j.frames = j.frames + 1
  sliceStart = debugprofilestop()
  while true do
    if not j.co then
      j.i = j.i + 1
      local key = j.keys[j.i]
      if not key then finish(j) return end
      j.key = key
      j.co = coroutine.create(key == "_links" and C.RefreshLinks or S[key])
    end
    local t0 = debugprofilestop()
    local ok, val = coroutine.resume(j.co)
    j.ms[j.key] = (j.ms[j.key] or 0) + (debugprofilestop() - t0)
    if not ok then
      j.errors[#j.errors + 1] = j.key .. ": " .. tostring(val)
      j.co = nil
    elseif coroutine.status(j.co) == "dead" then
      if j.key ~= "_links" then keep(j.snap, j.key, val) end
      j.co = nil
    else
      return -- the section yielded: this frame's share is used up
    end
    if debugprofilestop() - sliceStart > BUDGET_MS then return end
  end
end)

function C.SnapshotAsync(done)
  local keys = enabledSections()
  table.insert(keys, 1, "_links")
  job = { keys = keys, i = 0, snap = {}, errors = {}, ms = {}, done = done, frames = 0, started = debugprofilestop() }
  if ns.Npcs and not ns.Npcs.ready and ns.Npcs.BuildIndex then
    pcall(ns.Npcs.BuildIndex) -- (reads on by itself; the question goes once it is through)
    job.waitNpcs = ns.Npcs.Loading and ns.Npcs.Loading() or nil
  end
  runner:Show()
end

-- size control
function fitSize(snap)
  local limit = (ns.db.maxSnapshotKB or 60) * 1024
  local okJ, json = pcall(C_EncodingUtil.SerializeJSON, snap)
  if not okJ then
    -- a section that cannot be written goes first, alone: dropping sections in their order until the rest could be
    -- written took the player's position and the quest log with it
    for k in pairs(snap) do
      if not pcall(C_EncodingUtil.SerializeJSON, { [k] = snap[k] }) then
        snap[k] = nil
        snap.collectErrors = snap.collectErrors or {}
        table.insert(snap.collectErrors, k .. ": cannot be sent")
      end
    end
    okJ, json = pcall(C_EncodingUtil.SerializeJSON, snap)
  end
  local i = 1
  while (not okJ or #json > limit) and i <= #DROP_ORDER do
    if snap[DROP_ORDER[i]] ~= nil then
      -- said to the AI: a section that is not there reads as "the character has none"
      snap.leftOut = snap.leftOut or {}
      table.insert(snap.leftOut, DROP_ORDER[i])
      snap[DROP_ORDER[i]] = nil
    end
    i = i + 1
    okJ, json = pcall(C_EncodingUtil.SerializeJSON, snap)
  end
  if not okJ then
    -- find the section that cannot be serialized and drop it
    for k in pairs(snap) do
      local okS = pcall(C_EncodingUtil.SerializeJSON, { [k] = snap[k] })
      if not okS then snap[k] = nil end
    end
  end
  return snap
end

C.sections = S
