-- addonsim --market: things to buy and sell for the made-up hunter. Auctionator is installed with the prices of a
-- full scan an hour ago (its modern AH browse scan: TimeOfLastBrowseScan), a few one-handed swords are on offer, one
-- of them only in an older scan, and the bags hold materials, junk, food, a soulbound and a quest item. The hunter
-- wields a worn sword. The item data the client has for all of them: names, types, stats, vendor prices, tooltips.
local sim = ...

-- id = { name, quality, item level, required level, class id, subclass id, type, subtype, equip slot, vendor price,
--        stats (GetItemStats), tooltip lines }
local items = {
  [2010] = { "Worn Shortsword", 1, 12, 7, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 120,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 7.1 } },
  [3101] = { "Glinting Steel Sword", 2, 25, 20, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 1830,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 14.2, ITEM_MOD_AGILITY_SHORT = 5, ITEM_MOD_STAMINA_SHORT = 4 } },
  [3102] = { "Brackwater Saber", 2, 27, 22, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPONMAINHAND", 2410,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 15.8, ITEM_MOD_AGILITY_SHORT = 7 } },
  [3103] = { "Ironwood Blade", 1, 21, 16, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 950,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 11.0 } },
  [3104] = { "Duelist's Edge", 2, 33, 28, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 3900,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 19.5, ITEM_MOD_AGILITY_SHORT = 6 } },
  [3105] = { "Cutpurse Shortsword", 2, 23, 19, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 1460,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 13.1, ITEM_MOD_AGILITY_SHORT = 4 } },
  [3106] = { "Gilded Longblade", 3, 30, 24, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 5200,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 18.0, ITEM_MOD_AGILITY_SHORT = 8, ITEM_MOD_STAMINA_SHORT = 6 } },
  [3107] = { "Militia Shortsword", 1, 9, 4, 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON", 85,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 5.6 } },
  [3108] = { "Heavy Claymore", 2, 26, 21, 2, 8, "Weapon", "Two-Handed Swords", "INVTYPE_2HWEAPON", 2600,
             { ITEM_MOD_DAMAGE_PER_SECOND_SHORT = 21.0, ITEM_MOD_STRENGTH_SHORT = 8 } },
  [2589] = { "Linen Cloth", 1, 5, 0, 7, 5, "Tradeskill", "Cloth", "", 13, nil, { "Crafting Reagent" } },
  [2592] = { "Wool Cloth", 1, 15, 0, 7, 5, "Tradeskill", "Cloth", "", 33, nil, { "Crafting Reagent" } },
  [2318] = { "Light Leather", 1, 10, 0, 7, 6, "Tradeskill", "Leather", "", 15, nil, { "Crafting Reagent" } },
  [5784] = { "Slimy Murloc Scale", 1, 18, 0, 7, 6, "Tradeskill", "Leather", "", 75, nil, { "Crafting Reagent" } },
  [3300] = { "Rabbit's Foot", 0, 1, 0, 15, 0, "Miscellaneous", "Junk", "", 105, nil, {} },
  [4865] = { "Ruined Pelt", 0, 1, 0, 15, 0, "Miscellaneous", "Junk", "", 5, nil, {} },
  [117] = { "Tough Jerky", 1, 5, 1, 0, 5, "Consumable", "Food & Drink", "", 1, nil, { "Use: Restores 61 health over 18 sec." } },
  [1322] = { "Fishliver Oil", 1, 15, 0, 0, 0, "Consumable", "Consumable", "", 68, nil, { "Soulbound" } },
  [6948] = { "Hearthstone", 1, 1, 0, 15, 0, "Miscellaneous", "Junk", "", 0, nil, { "Soulbound", "Unique" } },
  [5385] = { "Crawler Leg", 1, 1, 0, 12, 0, "Quest", "Quest", "", 0, nil, { "Quest Item" } },
}
local byName = {}
for id, i in pairs(items) do byName[i[1]] = id end

local function link(id)
  local i = items[id]
  local color = ({ [0] = "ff9d9d9d", "ffffffff", "ff1eff00", "ff0070dd" })[i[2]] or "ffffffff"
  return "|c" .. color .. "|Hitem:" .. id .. "::::::::24:::::|h[" .. i[1] .. "]|h|r"
end

local function idOf(x)
  if type(x) == "number" then return items[x] and x end
  if type(x) ~= "string" then return nil end
  return tonumber(x:match("item:(%d+)")) or tonumber(x) or byName[x]
end

C_Item.GetItemInfo = function(x)
  local id = idOf(x)
  local i = id and items[id]
  if not i then return nil end
  return i[1], link(id), i[2], i[3], i[4], i[7], i[8], i[5] == 7 and 200 or 1, i[9], 134400, i[10], i[5], i[6]
end
C_Item.GetItemInfoInstant = function(x)
  local id = idOf(x)
  local i = id and items[id]
  if not i then return nil end
  return id, i[7], i[8], i[9], 134400, i[5], i[6]
end
C_Item.GetItemStats = function(x)
  local id = idOf(x)
  return id and items[id] and items[id][11] or {}
end
C_Item.IsItemDataCachedByID = function(id) return items[id] ~= nil end
C_Item.GetDetailedItemLevelInfo = function(x) local id = idOf(x) return id and items[id] and items[id][3] end
ITEM_MOD_DAMAGE_PER_SECOND_SHORT = ITEM_MOD_DAMAGE_PER_SECOND_SHORT or "Damage Per Second"
ITEM_MOD_AGILITY_SHORT = ITEM_MOD_AGILITY_SHORT or "Agility"
ITEM_MOD_STAMINA_SHORT = ITEM_MOD_STAMINA_SHORT or "Stamina"
ITEM_MOD_STRENGTH_SHORT = ITEM_MOD_STRENGTH_SHORT or "Strength"
INVTYPE_WEAPON = INVTYPE_WEAPON or "One-Hand"
INVTYPE_WEAPONMAINHAND = INVTYPE_WEAPONMAINHAND or "Main Hand"
INVTYPE_2HWEAPON = INVTYPE_2HWEAPON or "Two-Hand"

-- the wielded sword
GetInventoryItemLink = function(unit, slot) if unit == "player" and slot == 16 then return link(2010) end end
GetInventoryItemQuality = function(unit, slot) if unit == "player" and slot == 16 then return 1 end end

-- the backpack: item id, count, soulbound
local bag = { { 6948, 1, true }, { 2589, 12 }, { 2592, 10 }, { 2318, 6 }, { 5784, 4 }, { 3300, 1 }, { 4865, 3 },
  { 117, 5 }, { 1322, 2, true }, { 5385, 4, true } }
C_Container.GetContainerItemInfo = function(b, slot)
  local s = b == 0 and bag[slot]
  if not s then return nil end
  local i = items[s[1]]
  return { hyperlink = link(s[1]), itemID = s[1], stackCount = s[2], quality = i[2], isBound = s[3] or false,
    hasNoValue = i[10] == 0 }
end
C_Container.GetContainerItemLink = function(b, slot) local s = b == 0 and bag[slot] return s and link(s[1]) end
C_Container.GetContainerNumFreeSlots = function(b) return b == 0 and 16 - #bag or 0, 0 end
C_TooltipInfo.GetBagItem = function(b, slot)
  local s = b == 0 and bag[slot]
  if not s then return nil end
  local lines = { { leftText = items[s[1]][1] } }
  for _, l in ipairs(items[s[1]][12] or {}) do lines[#lines + 1] = { leftText = l } end
  return { lines = lines }
end

-- Auctionator with the realm's prices: { id, lowest buyout in copper, days since seen, listings }
local day0 = time({ year = 2020, month = 1, day = 1, hour = 0 })
local today = math.floor((time() - day0) / 86400)
local prices = {
  { 3101, 254000, 0, 3 }, { 3102, 415000, 0, 1 }, { 3103, 89500, 0, 6 }, { 3104, 300000, 0, 2 },
  { 3105, 120000, 3, 1 },  -- only in an older scan: sold since
  { 3106, 750000, 0, 1 }, { 3107, 4500, 0, 12 }, { 3108, 220000, 0, 2 },
  { 2589, 11, 0, 340 }, { 2592, 74, 0, 248 }, { 2318, 38, 0, 120 }, { 117, 4, 0, 60 },
  { 5784, 100, 3, 8 },     -- nobody lists it any more
}
local db = {}
for _, p in ipairs(prices) do
  local d = tostring(today - p[3])
  db[tostring(p[1])] = { m = p[2], h = { [d] = p[2] }, l = {}, a = { [d] = p[4] } }
end
-- the shopping lists the add-on saved, by name; like Auctionator, a list of the same name is replaced
local lists = {}
Auctionator = { API = { v1 = {
  MultiSearchExact = function() end,
  CreateShoppingList = function(caller, name, searchStrings)
    assert(type(caller) == "string" and type(name) == "string" and type(searchStrings) == "table",
      "Usage Auctionator.API.v1.CreateShoppingList(string, string, string[])")
    local copy = {}
    for i, s in ipairs(searchStrings) do copy[i] = s end
    lists[name] = copy
  end,
  GetShoppingListItems = function(caller, name)
    assert(lists[name], "Auctionator.API.v1.GetShoppingListItems: List does not exist: " .. tostring(name))
    local copy = {}
    for i, s in ipairs(lists[name]) do copy[i] = s end
    return copy
  end,
} }, State = { CurrentRealm = "Forever" }, Constants = { SCAN_DAY_0 = day0 } }
AUCTIONATOR_PRICE_DATABASE = { __dbversion = 8, Forever = db }
AUCTIONATOR_SAVEDVARS = { TimeOfLastBrowseScan = time() - 3600 }
sim.market = { items = items, bag = bag, prices = prices, lists = lists }
