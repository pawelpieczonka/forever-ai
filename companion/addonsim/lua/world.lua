-- addonsim: the game data the add-on reads, for a made-up character (a level 24 Night Elf hunter in Auberdine,
-- Darkshore) with empty bags, no quests, no target and no group. Game functions the simulator does not fill return
-- nothing (nil, or 0 for counts), the way an empty game would; the ones called are listed in sim.missing.
local sim = ...

local player = { name = "Simtester", realm = "Forever", level = 24, race = "Night Elf", raceFile = "NightElf",
                 raceId = 4, class = "Hunter", classFile = "HUNTER", classId = 3, sex = 3, faction = "Alliance",
                 guid = "Player-5826-0A1B2C3D", money = 123456, xp = 11850, xpMax = 18700,
                 health = 812, healthMax = 812, power = 640, powerMax = 705,
                 zone = "Darkshore", subzone = "Auberdine", mapID = 1439, x = 0.371, y = 0.441 }
sim.player = player

-- TargetUnit is reserved for the game's own interface. The game refuses an add-on's call (ADDON_ACTION_FORBIDDEN),
-- but only when there is a unit of that name to target: the refusal tells an add-on that the unit is in range.
sim.inRange = {} -- names (lower case) of units in the game's range, set by tests
function TargetUnit(name)
  name = tostring(name)
  if name == player.name or sim.inRange[name:lower()] then
    sim.fire("ADDON_ACTION_FORBIDDEN", "ForeverAI", "TargetUnit()")
  end
end

local function empty(name)
  return name:match("^GetNum") and function() return 0 end or function() return nil end
end

-- a namespace of the game API: the functions written here, and the others return nothing
local function namespace(nsName, impl)
  return setmetatable(impl or {}, { __index = function(t, k)
    if type(k) ~= "string" or not k:match("^%u") then return nil end
    sim.missing[nsName .. "." .. k] = true
    local fn = empty(k)
    rawset(t, k, fn)
    return fn
  end })
end

local function isPlayer(unit) return unit == "player" end

------------------------------------------------------------------------ the client
function GetBuildInfo() return "1.60.1", "70862", "Nov 3 2026", 16001 end
function GetLocale() return "enUS" end
function GetRealmName() return player.realm end
function GetNormalizedRealmName() return player.realm end
function IsLoggedIn() return true end

------------------------------------------------------------------------ the player and other units
function UnitName(unit) if isPlayer(unit) then return player.name end return nil end
function UnitFullName(unit) if isPlayer(unit) then return player.name, player.realm end return nil end
function UnitExists(unit) return isPlayer(unit) end
function UnitGUID(unit) if isPlayer(unit) then return player.guid end return nil end
function UnitIsUnit(a, b) return a == b and UnitExists(a) end
function UnitLevel(unit) if isPlayer(unit) then return player.level end return 0 end
function UnitEffectiveLevel(unit) return UnitLevel(unit) end
function UnitRace(unit) if isPlayer(unit) then return player.race, player.raceFile, player.raceId end return nil end
function UnitClass(unit) if isPlayer(unit) then return player.class, player.classFile, player.classId end return nil end
function UnitSex(unit) if isPlayer(unit) then return player.sex end return nil end
function UnitFactionGroup(unit) if isPlayer(unit) then return player.faction, player.faction end return nil end
function UnitHealth(unit) if isPlayer(unit) then return player.health end return 0 end
function UnitHealthMax(unit) if isPlayer(unit) then return player.healthMax end return 0 end
function UnitPower(unit) if isPlayer(unit) then return player.power end return 0 end
function UnitPowerMax(unit) if isPlayer(unit) then return player.powerMax end return 0 end
function UnitPowerType(unit) return 0, "MANA" end
function UnitXP(unit) return player.xp end
function UnitXPMax(unit) return player.xpMax end
function GetXPExhaustion() return nil end
function UnitAffectingCombat() return false end
function UnitIsDeadOrGhost() return false end
function UnitIsDead() return false end
function UnitIsGhost() return false end
function UnitIsPlayer(unit) return isPlayer(unit) end
function UnitIsConnected(unit) return isPlayer(unit) end
function UnitIsPVP() return false end
function UnitIsAFK() return false end
function UnitInParty() return false end
function UnitInRaid() return nil end
function UnitClassification(unit) return UnitExists(unit) and "normal" or nil end
function UnitCreatureType() return nil end
function UnitReaction() return nil end
function UnitCastingInfo() return nil end
function UnitChannelInfo() return nil end
function UnitStat(unit, i)
  local stats = { 38, 92, 61, 37, 43 }
  return stats[i], stats[i], 0, 0
end
function UnitArmor() return 1104, 1104, 1104, 0, 0 end
function UnitResistance(unit, school) return 0, 0, 0, 0 end
function UnitAttackPower() return 94, 0, 0 end
function UnitRangedAttackPower() return 188, 0, 0 end
function UnitDamage() return 41, 57, 0, 0, 0, 0, 1 end
function UnitAttackSpeed() return 2.4, 0 end
function UnitRangedDamage() return 2.8, 52, 71, 0, 0, 1 end
function GetCritChance() return 6.21 end
function GetRangedCritChance() return 8.4 end
function GetSpellCritChance() return 2.1 end
function GetSpellBonusDamage() return 0 end
function GetSpellBonusHealing() return 0 end
function GetDodgeChance() return 7.3 end
function GetParryChance() return 0 end
function GetBlockChance() return 0 end
function GetHitModifier() return 0 end
function GetSpellHitModifier() return 0 end
function GetManaRegen() return 4.6, 1.2 end
function GetMoney() return player.money end
function IsResting() return true end
function GetGuildInfo() return nil end
function IsInInstance() return false, "none" end
function GetInstanceInfo() return "Kalimdor", "none", 0, "", 0, 0, false, 1, 0 end
function IsInGroup() return false end
function IsInRaid() return false end
function GetNumGroupMembers() return 0 end
function GetBindLocation() return "Auberdine" end
function GetZoneText() return player.zone end
function GetRealZoneText() return player.zone end
function GetSubZoneText() return player.subzone end
function GetMinimapZoneText() return player.subzone end
function GetPlayerInfoByGUID(guid)
  if guid == player.guid then return player.class, player.classFile, player.race, player.raceFile, player.sex, player.name, player.realm end
  return nil
end
function GetUnitName(unit) return UnitName(unit) end
function CanInspect() return false end
function GetNumUnspentTalents() return 0 end
function GetMaxPlayerLevel() return 60 end
function GetItemCount() return 0 end
function GetProfessions() return nil end

-- the skill window's lines (name, isHeader, rank, maxRank); WoW Forever 1.60.1 lists every profession twice in a row
local skillLines = {
  { "Class Skills", true }, { "Beast Mastery", false, 1, 1 }, { "Marksmanship", false, 1, 1 }, { "Survival", false, 1, 1 },
  { "Professions", true }, { "Leatherworking", false, 84, 150 }, { "Leatherworking", false, 84, 150 },
  { "Skinning", false, 112, 150 }, { "Skinning", false, 112, 150 },
  { "Secondary Skills", true }, { "Cooking", false, 60, 75 }, { "Cooking", false, 60, 75 },
  { "First Aid", false, 75, 150 }, { "First Aid", false, 75, 150 },
  { "Weapon Skills", true }, { "Bows", false, 118, 120 }, { "Daggers", false, 64, 120 }, { "Swords", false, 1, 120 },
  { "Armor Proficiencies", true }, { "Cloth", false, 1, 1 }, { "Leather", false, 1, 1 },
  { "Languages", true }, { "Language: Common", false, 300, 300 }, { "Language: Darnassian", false, 300, 300 },
}
function GetNumSkillLines() return #skillLines end
function GetSkillLineInfo(i)
  local l = skillLines[i]
  if not l then return nil end
  return l[1], l[2], true, l[3] or 0, 0, 0, l[4] or 0, false, 0, 0, 0, 0, ""
end

------------------------------------------------------------------------ the map
local maps = {
  [947] = { mapID = 947, name = "Azeroth", mapType = 1, parentMapID = 0, size = { 48000, 32000 } },
  [1414] = { mapID = 1414, name = "Kalimdor", mapType = 2, parentMapID = 947, size = { 17500, 11666 } },
  [1439] = { mapID = 1439, name = "Darkshore", mapType = 3, parentMapID = 1414, size = { 6549.9, 4366.6 } },
  [1438] = { mapID = 1438, name = "Teldrassil", mapType = 3, parentMapID = 1414, size = { 5091.7, 3393.8 } },
  [1457] = { mapID = 1457, name = "Darnassus", mapType = 3, parentMapID = 1414, size = { 1058.3, 705.7 } },
  [1440] = { mapID = 1440, name = "Ashenvale", mapType = 3, parentMapID = 1414, size = { 5766.7, 3843.7 } },
  -- (the other continent, for routes through several zones: tests/journey.py)
  [1415] = { mapID = 1415, name = "Eastern Kingdoms", mapType = 2, parentMapID = 947, size = { 35199.9, 23466.6 } },
  [1429] = { mapID = 1429, name = "Elwynn Forest", mapType = 3, parentMapID = 1415, size = { 3470.8, 2314.6 } },
  [1436] = { mapID = 1436, name = "Westfall", mapType = 3, parentMapID = 1415, size = { 3500.0, 2333.3 } },
  [1433] = { mapID = 1433, name = "Redridge Mountains", mapType = 3, parentMapID = 1415, size = { 2170.8, 1447.9 } },
  [1431] = { mapID = 1431, name = "Duskwood", mapType = 3, parentMapID = 1415, size = { 2700.0, 1800.0 } },
  [1453] = { mapID = 1453, name = "Stormwind City", mapType = 3, parentMapID = 1415, size = { 1737.5, 1158.3 } },
}

local function mapInfo(id)
  local m = maps[tonumber(id) or 0]
  if not m then return nil end
  return { mapID = m.mapID, name = m.name, mapType = m.mapType, parentMapID = m.parentMapID, flags = 0 }
end

local userWaypoint
C_Map = namespace("C_Map", {
  GetBestMapForUnit = function(unit) if isPlayer(unit) then return player.mapID end return nil end,
  GetMapInfo = mapInfo,
  GetPlayerMapPosition = function(mapID, unit)
    if isPlayer(unit) and mapID == player.mapID then
      return { x = player.x, y = player.y, GetXY = function(self) return self.x, self.y end }
    end
    return nil
  end,
  GetMapWorldSize = function(id) local m = maps[tonumber(id) or 0] if m then return m.size[1], m.size[2] end return 0, 0 end,
  GetMapChildrenInfo = function(id, mapType, all)
    local out = {}
    for _, m in pairs(maps) do
      local p, isChild = m.parentMapID, false
      while p and p ~= 0 do
        if p == id then isChild = true break end
        p = maps[p] and maps[p].parentMapID
        if not all then break end
      end
      if isChild and (not mapType or m.mapType == mapType) then out[#out + 1] = mapInfo(m.mapID) end
    end
    table.sort(out, function(a, b) return a.mapID < b.mapID end)
    return out
  end,
  GetFallbackWorldMapID = function() return 947 end,
  SetUserWaypoint = function(point) userWaypoint = point end,
  GetUserWaypoint = function() return userWaypoint end,
  HasUserWaypoint = function() return userWaypoint ~= nil end,
  ClearUserWaypoint = function() userWaypoint = nil end,
  OpenWorldMap = function() end,
})
UiMapPoint = { CreateFromCoordinates = function(mapID, x, y) return { uiMapID = mapID, position = { x = x, y = y } } end }
C_SuperTrack = namespace("C_SuperTrack", { SetSuperTrackedUserWaypoint = function() end })
WorldMapFrame = CreateFrame("Frame", "WorldMapFrame", UIParent)
WorldMapFrame:Hide()
function WorldMapFrame:SetMapID(id) self.mapID = id end
function WorldMapFrame:GetMapID() return self.mapID or player.mapID end
function ToggleWorldMap() WorldMapFrame:SetShown(not WorldMapFrame:IsShown()) end

------------------------------------------------------------------------ everything else is empty
C_QuestLog = namespace("C_QuestLog", {
  GetNumQuestLogEntries = function() return 0, 0 end,
  GetInfo = function() return nil end,
  GetTitleForQuestID = function() return nil end,
  GetQuestObjectives = function() return {} end,
  IsComplete = function() return false end,
  IsOnQuest = function() return false end,
  GetLogIndexForQuestID = function() return nil end,
  GetSelectedQuest = function() return 0 end,
})
C_Item = namespace("C_Item", {
  GetItemInfo = function() return nil end,
  GetItemInfoInstant = function() return nil end,
  GetItemCount = function() return 0 end,
  RequestLoadItemDataByID = function() end,
})
C_Container = namespace("C_Container", {
  GetContainerNumSlots = function(bag) return bag == 0 and 16 or 0 end,
  GetContainerItemInfo = function() return nil end,
  GetContainerItemLink = function() return nil end,
  GetContainerNumFreeSlots = function(bag) return bag == 0 and 16 or 0, 0 end,
})
C_Spell = namespace("C_Spell", {
  GetSpellName = function() return nil end,
  GetSpellInfo = function() return nil end,
  GetSpellLink = function() return nil end,
  GetSpellDescription = function() return nil end,
  GetSpellSubtext = function() return nil end,
})
C_SpellBook = namespace("C_SpellBook", {
  GetNumSpellBookSkillLines = function() return 0 end,
  GetSpellBookItemInfo = function() return nil end,
  HasPetSpells = function() return nil end,
})
C_TooltipInfo = namespace("C_TooltipInfo", { GetHyperlink = function() return nil end })
ITEM_MIN_LEVEL = "Requires Level %d" -- the text of a tooltip's level line, as the game words it
C_UnitAuras = namespace("C_UnitAuras", { GetAuraDataByIndex = function() return nil end })
C_Reputation = namespace("C_Reputation", { GetNumFactions = function() return 0 end })
C_SkillInfo = namespace("C_SkillInfo")
C_GossipInfo = namespace("C_GossipInfo", { GetOptions = function() return {} end, GetAvailableQuests = function() return {} end,
                                          GetActiveQuests = function() return {} end, GetText = function() return nil end })
C_MerchantFrame = namespace("C_MerchantFrame")
C_NamePlate = namespace("C_NamePlate", { GetNamePlates = function() return {} end })
C_PaperDollInfo = namespace("C_PaperDollInfo")
C_PlayerInfo = namespace("C_PlayerInfo", { IsSelfFoundActive = function() return false end })
C_GameRules = namespace("C_GameRules", { IsHardcoreActive = function() return false end })
C_SpecializationInfo = namespace("C_SpecializationInfo", { GetSpecialization = function() return nil end })
C_ClassTalents = namespace("C_ClassTalents")
C_Traits = namespace("C_Traits")
C_FriendList = namespace("C_FriendList")
C_ChatInfo = namespace("C_ChatInfo")
C_BattleNet = namespace("C_BattleNet")
C_CurrencyInfo = namespace("C_CurrencyInfo")
C_TradeSkillUI = namespace("C_TradeSkillUI")
C_PetJournal = namespace("C_PetJournal")
C_MountJournal = namespace("C_MountJournal")
C_PetInfo = namespace("C_PetInfo")
C_DeathInfo = namespace("C_DeathInfo")
C_AuctionHouse = namespace("C_AuctionHouse")
ItemLocation = { CreateFromBagAndSlot = function(self, bag, slot) return { bag = bag, slot = slot } end }
Enum.ItemCommodityStatus = { Unknown = 0, Item = 1, Commodity = 2 }
Enum.AuctionStatus = { Active = 0, Sold = 1 }
C_TaxiMap = namespace("C_TaxiMap", { GetAllTaxiNodes = function() return nil end })
C_EquipmentSet = namespace("C_EquipmentSet")
TooltipDataProcessor = { AddTooltipPostCall = function() end, AddLinePostCall = function() end }

Enum.UIMapType = { Cosmic = 0, World = 1, Continent = 2, Zone = 3, Dungeon = 4, Micro = 5, Orphan = 6 }
Enum.TooltipDataType = { Item = 0, Spell = 1, Unit = 2, Corpse = 3, Object = 4, Currency = 5, BattlePet = 6,
                         UnitAura = 7, AzeriteEssence = 8, CompanionPet = 9, Mount = 10, PetAction = 11,
                         Achievement = 12, EnhancedConduit = 13, EquipmentSet = 14, InstanceLock = 15,
                         PvPBrawl = 16, RecipeRankInfo = 17, Totem = 18, Toy = 19, CorruptionCleanser = 20,
                         MinimapMouseover = 21, Flyout = 22, Quest = 23, QuestPartyProgress = 24, Macro = 25 }
Enum.BagIndex = { Keyring = -2, Bank = -1, Backpack = 0, Bag_1 = 1, Bag_2 = 2, Bag_3 = 3, Bag_4 = 4 }
Enum.SpellBookSpellBank = { Player = 0, Pet = 1 }
Enum.FlightPathState = { Current = 0, Reachable = 1, Unreachable = 2 }
Enum.ItemQuality = { Poor = 0, Common = 1, Uncommon = 2, Rare = 3, Epic = 4, Legendary = 5, Artifact = 6, Heirloom = 7 }
Enum.PowerType = { Mana = 0, Rage = 1, Focus = 2, Energy = 3 }

RANGE_INDICATOR = "●"
for q, name in pairs({ [0] = "Poor", "Common", "Uncommon", "Rare", "Epic", "Legendary", "Artifact", "Heirloom" }) do
  _G["ITEM_QUALITY" .. q .. "_DESC"] = name
end
COMBATLOG_XPGAIN_FIRSTPERSON = "%s dies, you gain %d experience."
NORMAL_FONT_COLOR = { r = 1, g = 0.82, b = 0 }
HIGHLIGHT_FONT_COLOR = { r = 1, g = 1, b = 1 }

-- the game's global functions the add-on can call; without data they return nothing
for _, name in ipairs({
  "AbandonQuest", "BuybackItem", "NumTaxiNodes", "TaxiNodeGetType", "TaxiNodeName", "CanMerchantRepair", "GetActionCount", "GetActionInfo", "GetBuybackItemInfo",
  "GetInventoryItemDurability", "GetInventoryItemLink", "GetInventoryItemQuality", "GetItemStats", "GetLootSlotInfo",
  "GetMacroInfo", "GetMacroSpell", "GetMerchantItemLink", "GetMerchantNumItems", "GetNumBuybackItems",
  "GetNumLootItems", "GetNumQuestChoices", "GetNumQuestLogChoices", "GetNumQuestLogRewards", "GetNumQuestRewards",
  "GetNumTrainerServices", "GetObjectiveText", "GetPetActionInfo", "GetPetFoodTypes",
  "GetPetHappiness", "GetPetLoyalty", "GetProfessionInfo", "GetProgressText", "GetQuestItemInfo", "GetQuestItemLink",
  "GetQuestLink", "GetQuestLogItemLink", "GetQuestLogQuestText", "GetQuestLogRewardMoney", "GetQuestLogRewardXP",
  "GetQuestText", "GetRepairAllCost", "GetRewardMoney", "GetRewardText",
  "GetSpecializationInfoByID", "GetTitleText", "GetTrainerServiceCost", "GetTrainerServiceInfo",
  "GetTrainerServiceLevelReq", "GetTrainerServiceSkillReq", "HasAction", "NotifyInspect", "RepairAllItems", "SetAbandonQuest", "GetNumFactions",
  "GetFactionInfo", "GetNumSpellTabs", "GetSpellTabInfo", "GetSpellBookItemName", "GetContainerNumSlots",
  "GetInventoryItemCount", "GetInventoryItemID", "GetInventorySlotInfo",
  "GetActionText", "GetFlyoutInfo", "GetMacroItem", "UnitCreatureFamily", "GetTaxiMapID", "GetTrainerServiceDescription",
  "UnitOnTaxi", "GetItemCooldown",
}) do
  if _G[name] == nil then _G[name] = empty(name) end
end
StaticPopupDialogs = StaticPopupDialogs or {}
function HandleModifiedItemClick(link) return false end
function SetItemRef(link, text, button, chatFrame) end
