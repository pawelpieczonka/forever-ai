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
local factsCache = {}
do
  local f = CreateFrame("Frame")
  for _, e in ipairs({ "PLAYER_EQUIPMENT_CHANGED", "PLAYER_LEVEL_UP", "SKILL_LINES_CHANGED" }) do pcall(f.RegisterEvent, f, e) end
  f:SetScript("OnEvent", function() wipe(factsCache) end)
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
  if equipLoc and equipLoc ~= "" and equipLoc ~= "INVTYPE_NON_EQUIP_IGNORE" then
    f.stats = #list > 0 and table.concat(list, ", ") or "NONE — this item has no stats (read from the game)"
    if stats.Armor then f.armor = stats.Armor end
    -- can this character use it? armor and weapon types need the matching skill (Leather, Mail, Bows, Daggers...)
    if (classID == 2 or classID == 4) and not NO_SKILL_NEEDED[equipLoc] and subType and subType ~= "" then
      local need = subType:lower()
      local have = skillSet()
      local ok = have[need] or have[need .. " mail"] or (need == "miscellaneous") or (need == "shields" and have["shield"])
      if next(have) and not ok then f.cannotUse = "missing skill: " .. subType end
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
  factsCache[cacheKey] = f
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

C.lastTooltip = nil
function C.CaptureTooltip(force)
  local text = gameTooltipLines()
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
  end
  if C_SpecializationInfo and C_SpecializationInfo.GetSpecialization then
    local spec = safe(C_SpecializationInfo.GetSpecialization)
    if spec and spec > 0 then
      local _, specName = safe(C_SpecializationInfo.GetSpecializationInfo, spec)
      p.spec = specName
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
      if type(cur) == "table" then
        local free = {}
        for _, c in ipairs(cur) do free[#free + 1] = tostring(c.quantity) .. "/" .. tostring(c.maxQuantity) end
        t.unspent = table.concat(free, ", ")
      end
      local nodes = safe(C_Traits.GetTreeNodes, treeID) or {}
      t.nodes = #nodes
      for _, nodeID in ipairs(nodes) do
        local n = safe(C_Traits.GetNodeInfo, cfg, nodeID)
        if n and n.activeRank and n.activeRank > 0 and n.activeEntry then
          local e = safe(C_Traits.GetEntryInfo, cfg, n.activeEntry.entryID)
          local d = e and e.definitionID and safe(C_Traits.GetDefinitionInfo, e.definitionID)
          local name = d and (d.overrideName or (d.spellID and C_Spell.GetSpellName(d.spellID)))
          t.picked[#t.picked + 1] = tostring(name or ("spell " .. tostring(d and d.spellID))) .. " " .. n.activeRank .. "/" .. tostring(n.maxRanks)
        end
      end
      out[#out + 1] = t
    end
  end
  local active = safe(C_ClassTalents.GetActiveConfigID)
  if active then dumpConfig(active, "class") end
  result.unspentTalents = safe(GetNumUnspentTalents)
  return result
end

-- NPC names with their first known spawn (zone x, y) from Questie's database
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

local function questieDB()
  if QuestieLoader and QuestieLoader.ImportModule then
    local ok, db = pcall(QuestieLoader.ImportModule, QuestieLoader, "QuestieDB")
    if ok and type(db) == "table" then return db end
  end
end

------------------------------------------------------------------------ where to get quest objectives (Questie database)
-- For every objective: the creatures to kill, the objects to use, and for items which objects contain them, which
-- creatures drop them and which vendors sell them, with every known spawn point.
local function spawnPoints(spawns)
  local out = {}
  for area, pts in pairs(type(spawns) == "table" and spawns or {}) do
    for _, p in ipairs(type(pts) == "table" and pts or {}) do
      if type(p) == "table" and tonumber(p[1]) and p[1] > 0 then out[#out + 1] = { area = area, x = p[1], y = p[2] } end
    end
  end
  return out
end

local function npcSource(QDB, npcId, how)
  local okN, name = pcall(QDB.QueryNPCSingle, npcId, "name")
  if not (okN and type(name) == "string") then return nil end
  local _, spawns = pcall(QDB.QueryNPCSingle, npcId, "spawns")
  local _, minL = pcall(QDB.QueryNPCSingle, npcId, "minLevel")
  local _, maxL = pcall(QDB.QueryNPCSingle, npcId, "maxLevel")
  local lvl = tonumber(minL) and ((tonumber(maxL) and maxL ~= minL) and (minL .. "-" .. maxL) or tostring(minL)) or nil
  return { how = how, name = name, level = lvl, pts = spawnPoints(spawns) }
end

local function objectSource(QDB, objId, how)
  if not QDB.QueryObjectSingle then return nil end
  local okN, name = pcall(QDB.QueryObjectSingle, objId, "name")
  if not (okN and type(name) == "string") then return nil end
  local _, spawns = pcall(QDB.QueryObjectSingle, objId, "spawns")
  return { how = how, name = name, pts = spawnPoints(spawns) }
end

local function objectiveSources(QDB, qid)
  if not (QDB and QDB.QueryQuestSingle) then return nil end
  local okO, objs = pcall(QDB.QueryQuestSingle, qid, "objectives")
  if not (okO and type(objs) == "table") then return nil end
  local list = {}
  local function add(label, sources) if #sources > 0 then list[#list + 1] = { label = label, sources = sources } end end
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
      local s = npcSource(QDB, nid, "dropped by") if s then sources[#sources + 1] = s end
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
        add(type(entry[3]) == "string" and entry[3] or "kill credit", sources)
      end
    end
  end
  return list
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

-- objective sources -> lines for the AI, nearest first, with distance in meters in the player's zone
local function whereToGet(list, mapID, px, py, area)
  if not list or #list == 0 then return nil end
  local out = {}
  for _, obj in ipairs(list) do
    local ranked = {}
    for _, s in ipairs(obj.sources) do
      local best, here = nil, 0
      for _, p in ipairs(s.pts) do
        if area and p.area == area then
          here = here + 1
          local d = px and ns.MapMeters(mapID, px, py, p.x, p.y)
          if d and (not best or d < best.d) then best = { d = d, p = p } end
        end
      end
      ranked[#ranked + 1] = { s = s, best = best or (s.pts[1] and { p = s.pts[1] }), here = here }
    end
    table.sort(ranked, function(a, b) return (a.best and a.best.d or 1e9) < (b.best and b.best.d or 1e9) end)
    local parts = {}
    for i = 1, math.min(5, #ranked) do
      local r = ranked[i]
      local where = "no known spawn point"
      if r.best then
        local zone = C_Map.GetAreaInfo and C_Map.GetAreaInfo(r.best.p.area) or ("zone " .. tostring(r.best.p.area))
        where = string.format("%s %.1f, %.1f", tostring(zone), r.best.p.x, r.best.p.y)
        if r.best.d then
          where = where .. string.format(" (%d m, %d spawn point%s in your zone)", r.best.d, r.here, r.here == 1 and "" or "s")
        end
      end
      parts[#parts + 1] = string.format("%s %s%s — %s", r.s.how, r.s.name, r.s.level and (" (level " .. r.s.level .. ")") or "",
        where)
    end
    if #ranked > 5 then parts[#parts + 1] = string.format("and %d more", #ranked - 5) end
    out[#out + 1] = obj.label .. ": " .. table.concat(parts, "; ")
  end
  return out
end

-- everything known about one quest (tagged with Alt+click): log state, Questie data, givers with coordinates
function C.QuestInfo(id)
  id = tonumber(id)
  if not id then return nil end
  local out = { id = id }
  out.title = C_QuestLog.GetTitleForQuestID and ns.safe(C_QuestLog.GetTitleForQuestID, id)
  local idx = C_QuestLog.GetLogIndexForQuestID and ns.safe(C_QuestLog.GetLogIndexForQuestID, id)
  if idx then
    out.inQuestLog = true
    out.complete = C_QuestLog.IsComplete and ns.safe(C_QuestLog.IsComplete, id) or nil
    local o = {}
    for _, ob in ipairs((C_QuestLog.GetQuestObjectives and ns.safe(C_QuestLog.GetQuestObjectives, id)) or {}) do
      o[#o + 1] = ns.clean(ob.text)
    end
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
      out.questGiver = qd.startedBy and questieNPCs(questie, qd.startedBy[1])
      out.turnIn = qd.finishedBy and questieNPCs(questie, qd.finishedBy[1])
    end
    if not out.alreadyDone and not out.complete then
      local okS, sources = pcall(objectiveSources, questie, id)
      if okS then out.whereToGet = whereToGet(sources, playerSpot()) end
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
      s.questGiver = qd.startedBy and questieNPCs(questie, qd.startedBy[1])
      s.turnIn = qd.finishedBy and questieNPCs(questie, qd.finishedBy[1])
    end
    local okS, sources = pcall(objectiveSources, questie, qid)
    s.sources = okS and sources or nil
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
      local o = {}
      for _, ob in ipairs(objs) do
        local t = clean(ob.text)
        if t then o[#o + 1] = t .. (ob.finished and " [DONE]" or " [TO DO]") end
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
      if e.status == "in progress" then e.whereToGet = whereToGet(st.sources, mapID, px, py, area) end
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

-- the game's own tooltip of a spell, without its name: cost, range, cast time, cooldown, what it does
-- Spell descriptions: in this client the data API leaves the effect text out ("Instant | 3 min cooldown"); the game
-- fills it in only when a tooltip is actually shown. So descriptions are remembered from every spell tooltip the game
-- displays, and the spellbook is read once in the background through a hidden tooltip.
C.spellDesc = {}
do -- newly learned spells: read their descriptions too (event names differ between clients, so register safely)
  local f = CreateFrame("Frame")
  for _, e in ipairs({ "LEARNED_SPELL_IN_TAB", "LEARNED_SPELL_IN_SKILL_LINE", "SPELLS_CHANGED" }) do pcall(f.RegisterEvent, f, e) end
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
    C_Timer.After(0.05, function() if tip:IsShown() then pcall(function() remember(id, tipParts(tip)) end) end end)
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
      pcall(function() remember(id, tipParts(scanTip)) end)
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
function S.spells()
  local out = {}
  if not (C_SpellBook and C_SpellBook.GetNumSpellBookSkillLines) then return out end
  local count, chars = 0, 60000 -- a safety cap for the whole spellbook; every spell normally gets its full text
  for t = 1, C_SpellBook.GetNumSpellBookSkillLines() do
    local li = C_SpellBook.GetSpellBookSkillLineInfo(t)
    if li and not li.shouldHide then
      for k = li.itemIndexOffset + 1, li.itemIndexOffset + li.numSpellBookItems do
        budget()
        local ii = C_SpellBook.GetSpellBookItemInfo(k, Enum.SpellBookSpellBank.Player)
        if ii and ii.name and count < 200 then
          count = count + 1
          local sub = ii.spellID and C_Spell.GetSpellSubtext and safe(C_Spell.GetSpellSubtext, ii.spellID)
          local label = ii.name .. ((sub and sub ~= "") and (" (" .. sub .. ")") or "")
          local tip = chars > 0 and spellTip(ii.spellID, 1500) or nil
          chars = chars - (tip and #tip or 0)
          out[#out + 1] = string.format("[%s] %s%s%s", li.name or "?", label, ii.isPassive and " [passive]" or "",
            tip and (": " .. tip) or "")
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

local function actionText(slot)
  local kind, id = safe(GetActionInfo, slot)
  if not kind then return nil end
  local name
  if kind == "spell" then
    name = C_Spell.GetSpellName and safe(C_Spell.GetSpellName, id)
  elseif kind == "item" then
    name = (C_Item.GetItemNameByID and safe(C_Item.GetItemNameByID, id)) or safe(C_Item.GetItemInfo, id)
  elseif kind == "macro" then
    local mname = safe(GetMacroInfo, id)
    local mspell = GetMacroSpell and safe(GetMacroSpell, id)
    local sname = mspell and C_Spell.GetSpellName and safe(C_Spell.GetSpellName, mspell)
    name = "macro " .. tostring(mname or id) .. (sname and (" → " .. sname) or "")
  else
    name = tostring(kind) .. " " .. tostring(id)
  end
  local text = tostring(name or "?")
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
    if ok and slot then
      local parent = b:GetParent()
      local barName = (parent and parent.GetName and parent:GetName()) or ((b:GetName() or "?"):gsub("%d+$", ""))
      local bar = bars[barName]
      if not bar then
        bar = { shown = false, total = 0, items = {} }
        bars[barName] = bar
        order[#order + 1] = barName
      end
      bar.total = bar.total + 1
      local visible = b:IsVisible()
      if visible then bar.shown = true end
      local text = actionText(slot)
      if text then
        onBar[slot] = true
        local key = visible and buttonKey(b)
        local num = tonumber((b:GetName() or ""):match("(%d+)$")) or bar.total
        bar.items[#bar.items + 1] = { n = num, text = string.format("button %d%s: %s", num, key and (" [key " .. key .. "]") or "", text) }
      end
    end
  end
  local out = {}
  for _, name in ipairs(order) do
    local bar = bars[name]
    if bar.shown or #bar.items > 0 then -- hidden, empty bars (vehicle, override...) say nothing
      out[#out + 1] = string.format("%s — %s, %d of %d buttons used", name, bar.shown and "shown" or "hidden",
        #bar.items, bar.total)
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
  local out = {}
  if not (numFn and infoFn) then return out end
  for i = 1, math.min(80, safe(numFn) or 0) do
    local r = { safe(infoFn, i) }
    local info = r[1]
    if type(info) == "table" then
      -- field names differ between client versions
      local name = info.skillName or info.name or info.skillLineDisplayName or info.displayName
      local header = info.isHeader or info.header
      local rank = info.skillRank or info.rank or info.skillLineRank or info.currentRank
      local maxRank = info.skillMaxRank or info.maxRank or info.skillLineMaxRank
      if name and not header then
        out[#out + 1] = string.format("%s %s/%s", name, tostring(rank), tostring(maxRank))
      end
    elseif type(info) == "string" and not r[2] then
      out[#out + 1] = string.format("%s %s/%s", info, tostring(r[4]), tostring(r[7]))
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

function S.target() return C.UnitInfo("target") end
function S.mouseover() return C.UnitInfo("mouseover") end
function S.pet()
  local u = C.UnitInfo("pet")
  if not u then return nil end
  -- hunter pet: happiness, loyalty, diet and its action bar
  local happiness = GetPetHappiness and safe(GetPetHappiness)
  if happiness then u.happiness = ({ "unhappy", "content", "happy" })[happiness] end
  local diet = GetPetFoodTypes and { safe(GetPetFoodTypes) }
  if diet and #diet > 0 then u.eats = table.concat(diet, ", ") end
  local loyalty = GetPetLoyalty and safe(GetPetLoyalty)
  if loyalty then u.loyalty = loyalty end
  if GetPetActionInfo then
    local acts = {}
    for i = 1, 10 do
      local name, _, isToken, isActive, autoCast = safe(GetPetActionInfo, i)
      if name then
        if isToken then name = _G[name] or name end
        acts[#acts + 1] = tostring(name) .. (isActive and " [active]" or "") .. (autoCast and " [auto]" or "")
      end
    end
    if #acts > 0 then u.actions = acts end
  end
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
        t.services[#t.services + 1] = string.format("%s%s — %s, level %s, %s", name, (sub and sub ~= "") and (" (" .. sub .. ")") or "",
          money(cost) or "?", tostring(lvl), tostring(kind))
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
local ORDER = { "game", "player", "progress", "stats", "gearTotals", "equipment", "talents", "quests", "marks", "nearbyQuests",
  "zoneNPCs", "nearbyMobs", "around", "recentEvents", "actionBars", "target", "mouseover", "pet", "tooltip", "group",
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

runner:SetScript("OnUpdate", function()
  local j = job
  if not j then runner:Hide() return end
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
  runner:Show()
end

-- size control
function fitSize(snap)
  local limit = (ns.db.maxSnapshotKB or 60) * 1024
  local okJ, json = pcall(C_EncodingUtil.SerializeJSON, snap)
  local i = 1
  while (not okJ or #json > limit) and i <= #DROP_ORDER do
    snap[DROP_ORDER[i]] = nil
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
