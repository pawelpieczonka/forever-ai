"""What the add-on collects for the model, checked in the add-on simulator with small pieces of game state: the gaps
and the noise found in real conversations (ammunition, what a bag item does, empty action bars, "kill" on objectives
that are not kills, talents that can be picked, a spell description from the wrong tooltip, every profession
listed twice)."""

import pytest

from addonsim import Sim, install_game

pytest.importorskip("lupa")


@pytest.fixture
def sim(tmp_path):
    install_game(tmp_path / "game", pool=40)
    s = Sim(tmp_path / "game").start()
    yield s
    s.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def test_ammunition_slot_is_part_of_the_equipment(sim):
    last = "local e = ForeverAI.Collect.sections.equipment() local a = e[#e] return a.slot .. '|' .. a.item .. '|' .. tostring(a.count)"
    # the game says nothing about the slot (the real client gave no link with arrows equipped): no line, and
    # certainly no claim that the slot is empty
    assert text(sim.lua(last)) == "Tabard|(empty)|nil"
    sim.lua("""
      GetInventorySlotInfo = function(name) return name == "AmmoSlot" and 0 or nil end
      GetInventoryItemLink = function(unit, slot)
        if slot == 0 then return "|cffffffff|Hitem:2515::::::::11:::::|h[Sharp Arrow]|h|r" end
      end
      GetInventoryItemCount = function(unit, slot) return slot == 0 and 1387 or 0 end
    """)
    assert text(sim.lua(last)) == "Ammo|[Sharp Arrow]|1387"
    sim.lua("""
      GetInventoryItemLink = function() return nil end                      -- a client without a link for the slot
      GetInventoryItemID = function(unit, slot) return slot == 0 and 2515 or nil end
      C_Item.GetItemNameByID = function(id) return id == 2515 and "Sharp Arrow" or nil end
    """)
    assert text(sim.lua(last)) == "Ammo|[Sharp Arrow]|1387"


def test_bag_items_say_what_they_do(sim):
    sim.lua("""
      local link = "|cffffffff|Hitem:2304::::::::11:::::|h[Light Armor Kit]|h|r"
      C_Container.GetContainerNumSlots = function(bag) return bag == 0 and 1 or 0 end
      C_Container.GetContainerItemInfo = function(bag, slot)
        return { hyperlink = link, itemID = 2304, stackCount = 2, quality = 1 }
      end
      C_Item.GetItemInfo = function() return "Light Armor Kit", link, 1, 5, 1, "Consumable", "Other", 10, "", 0, 15, 0 end
      C_Item.GetItemInfoInstant = function() return 2304, "Consumable", "Other", "", 0, 0, 0 end
      C_TooltipInfo.GetBagItem = function(bag, slot)
        return { lines = { { leftText = "Light Armor Kit" }, { leftText = "Requires Level 1" },
          { leftText = "Use: Permanently increase the armor value of an item worn on the chest, legs, hands or feet by 8." } } }
      end
    """)
    item = "local i = ForeverAI.Collect.sections.bags().items[1] return i.item .. '|' .. i.count .. '|' .. tostring(i.tooltip)"
    assert text(sim.lua(item)) == ("[Light Armor Kit]|2|Requires Level 1\nUse: Permanently increase the armor value of an "
                                   "item worn on the chest, legs, hands or feet by 8.")


def test_action_bars_list_only_what_is_on_them(sim):
    sim.lua("""
      for n = 1, 12 do
        local box = CreateFrame("Frame", "MultiBarTestButtonContainer" .. n, UIParent)
        local b = CreateFrame("Button", "MultiBarTestButton" .. n, box)
        b.action = 100 + n
      end
      local hidden = CreateFrame("Frame", "GamepadTestBar", UIParent)
      hidden:Hide()
      local hb = CreateFrame("Button", "GamepadTestBarButton1", hidden)
      hb.action = 102                                   -- the same action as a shown button
      local hb2 = CreateFrame("Button", "GamepadTestBarButton2", hidden)
      hb2.action = 150                                  -- an action no shown bar has
      local names = { [102] = "Arcane Shot", [105] = "Serpent Sting", [150] = "Feign Death" }
      GetActionInfo = function(slot) if names[slot] then return "spell", slot end end
      HasAction = function(slot) return names[slot] ~= nil end
      C_Spell.GetSpellName = function(id) return names[id] end
    """)
    bars = text(sim.lua("return table.concat(ForeverAI.Collect.sections.actionBars(), '\\n')"))
    assert bars.splitlines() == ["MultiBarTestButtonContainer — 2 of 12 buttons used",
                                 "  button 2: Arcane Shot", "  button 5: Serpent Sting",
                                 "slot 150 (not on a shown bar): Feign Death"]


def test_skills_are_listed_once(sim):
    sim.lua("""
      local lines = { "Leatherworking", "Leatherworking", "Skinning", "Skinning", "Bows" }
      C_SkillInfo.GetNumSkillLines = function() return #lines end
      C_SkillInfo.GetSkillLineInfo = function(i) return { skillName = lines[i], skillRank = 3 * i, skillMaxRank = 75 } end
    """)
    assert text(sim.lua("return table.concat(ForeverAI.Collect.sections.skills(), ' | ')")) == \
        "Leatherworking 3/75 | Skinning 9/75 | Bows 15/75"


def test_skills_from_the_classic_api_are_listed_once(sim):
    # the player's question on WoW Forever 1.60.1 had "Skinning 130/150", "Skinning 130/150", "Tailoring 51/75", ...
    sim.lua("""
      local lines = { { "Class Skills", 1 }, { "Arcane", nil, 1, 1 }, { "Professions", 1 },
                      { "Skinning", nil, 130, 150 }, { "Skinning", nil, 130, 150 },
                      { "Tailoring", nil, 51, 75 }, { "Tailoring", nil, 51, 75 },
                      { "Secondary Skills", 1 }, { "Cooking", nil, 92, 150 }, { "Fishing", nil, 56, 75 },
                      { "Cooking", nil, 92, 150 }, { "Fishing", nil, 56, 75 }, { "Wands", nil, 97, 100 } }
      GetNumSkillLines = function() return #lines end
      GetSkillLineInfo = function(i)
        local l = lines[i]
        return l[1], l[2], 1, l[3] or 0, 0, 0, l[4] or 0, nil, 0, 0, 0, 0, ""
      end
    """)
    assert text(sim.lua("return table.concat(ForeverAI.Collect.sections.skills(), ' | ')")) == \
        "Arcane 1/1 | Skinning 130/150 | Tailoring 51/75 | Cooking 92/150 | Fishing 56/75 | Wands 97/100"


def test_skills_keep_the_cap_of_80_lines(sim):
    sim.lua("""
      GetNumSkillLines = function() return 200 end
      GetSkillLineInfo = function(i) return "Skill " .. math.ceil(i / 2), nil, 1, i, 0, 0, 300 end
    """)
    # the first 80 rows of the client, each name once, in the client's order
    assert text(sim.lua("return table.concat(ForeverAI.Collect.sections.skills(), '\\n')")).splitlines() == \
        [f"Skill {n} {2 * n - 1}/300" for n in range(1, 41)]


def test_the_simulated_hunter_lists_each_skill_once(sim):
    # the simulator's client repeats each profession the way the game does
    assert text(sim.lua("return select(1, GetSkillLineInfo(8)) .. '|' .. select(1, GetSkillLineInfo(9))")) == \
        "Skinning|Skinning"
    skills = text(sim.lua("return table.concat(ForeverAI.Collect.sections.skills(), '\\n')")).splitlines()
    assert skills == ["Beast Mastery 1/1", "Marksmanship 1/1", "Survival 1/1", "Leatherworking 84/150",
                      "Skinning 112/150", "Cooking 60/75", "First Aid 75/150", "Bows 118/120", "Daggers 64/120",
                      "Swords 1/120", "Cloth 1/1", "Leather 1/1", "Language: Common 300/300",
                      "Language: Darnassian 300/300"]


def test_only_kill_objectives_say_kill(sim):
    where = """
      local list = { { label = "Strigid Screecher", sources = { { how = "kill", name = "Strigid Screecher", level = "7-8", pts = {} } } },
                     { label = "Befouled Webwood", sources = { { how = "kill", name = "Befouled Webwood", level = "10-11", pts = {} } } },
                     { label = "Mature Fel Moss", sources = { { how = "dropped by", name = "Xethorr", level = "12", pts = {} } } } }
      return table.concat(ForeverAI.Collect.WhereToGet(list, nil, nil, nil, nil, %s), " | ")
    """
    # the quest log says what has to be slain; the database calls every creature of a quest a kill
    slain = '{ "Tame a Strigid Screecher", "0/8 Befouled Webwood slain", "0/1 Mature Fel Moss" }'
    assert text(sim.lua(where % slain)) == (
        "Strigid Screecher: find Strigid Screecher (level 7-8) — no known spawn point | "
        "Befouled Webwood: kill Befouled Webwood (level 10-11) — no known spawn point | "
        "Mature Fel Moss: dropped by Xethorr (level 12) — no known spawn point")
    # no quest log entry to compare with (a quest that is only offered): as the database says
    assert text(sim.lua(where % "nil")).startswith("Strigid Screecher: kill Strigid Screecher")


def test_talents_that_can_be_picked_now(sim):
    sim.lua("""
      C_ClassTalents.GetActiveConfigID = function() return 7 end
      C_Traits.GetConfigInfo = function(cfg) return { name = "x", treeIDs = { 1091 } } end
      C_Traits.GetTreeCurrencyInfo = function() return { { quantity = 2, maxQuantity = 2 } } end
      C_Traits.GetTreeNodes = function() return { 11, 12, 13 } end
      local nodes = {
        [11] = { isAvailable = true, canPurchaseRank = true, activeRank = 0, maxRanks = 5, entryIDs = { 111 } },
        [12] = { isAvailable = false, canPurchaseRank = false, activeRank = 0, maxRanks = 5, entryIDs = { 121 } },
        [13] = { isAvailable = true, canPurchaseRank = true, activeRank = 3, maxRanks = 5, entryIDs = { 131 },
                 activeEntry = { entryID = 131 } },
      }
      C_Traits.GetNodeInfo = function(cfg, id) return nodes[id] end
      C_Traits.GetEntryInfo = function(cfg, id) return { definitionID = id * 10 } end
      local spells = { [1110] = "Endurance Training", [1210] = "Bestial Wrath", [1310] = "Improved Aspect of the Hawk" }
      C_Traits.GetDefinitionInfo = function(def) return { spellID = def } end
      C_Spell.GetSpellName = function(id) return spells[id] end
      C_Spell.GetSpellDescription = function(id)                             -- the real client: often only "Passive"
        return id == 1110 and "Increases the Health of your pet by 3%." or "Passive"
      end
      GetNumUnspentTalents = function() return 0 end
    """)
    t = "local t = ForeverAI.Collect.sections.talents() local tree = t.trees[1] "
    assert sim.lua(t + "return tree.unspent") == 2 and sim.lua(t + "return t.unspentTalents") is None
    assert text(sim.lua(t + "return table.concat(tree.picked, ' | ')")) == "Improved Aspect of the Hawk 3/5"
    assert text(sim.lua(t + "return table.concat(tree.canPick, ' | ')")) == (
        "Endurance Training 0/5: Increases the Health of your pet by 3%. | Improved Aspect of the Hawk 3/5")


def test_spell_description_only_from_its_own_tooltip(sim):
    sim.lua("""
      FakeTipTextLeft1 = { GetText = function() return "Aspect of the Monkey" end }
      FakeTip = { GetName = function() return "FakeTip" end }
      C_Spell.GetSpellName = function(id) return id == 1 and "Aspect of the Monkey" or "Track Beasts" end
    """)
    assert sim.lua("return ForeverAI.Collect.TipShows(FakeTip, 1)") is True
    assert sim.lua("return ForeverAI.Collect.TipShows(FakeTip, 2)") is False  # still shows the spell read before it


ITEM_TYPES = """
  -- id = { name, class id, subclass id, type, subtype, equip slot }
  local kinds = {
    [1] = { "Glinting Steel Sword", 2, 7, "Weapon", "One-Handed Swords", "INVTYPE_WEAPON" },
    [2] = { "Heavy Claymore", 2, 8, "Weapon", "Two-Handed Swords", "INVTYPE_2HWEAPON" },
    [3] = { "Woodsman Hatchet", 2, 0, "Weapon", "One-Handed Axes", "INVTYPE_WEAPON" },
    [4] = { "Bandit Knife", 2, 15, "Weapon", "Daggers", "INVTYPE_WEAPON" },
    [5] = { "Strong Fishing Pole", 2, 20, "Weapon", "Fishing Poles", "INVTYPE_2HWEAPON" },
    [6] = { "Chainmail Vest", 4, 3, "Armor", "Mail", "INVTYPE_CHEST" },
    [7] = { "Padded Boots", 4, 2, "Armor", "Leather", "INVTYPE_FEET" },
    [8] = { "Iron Breastplate", 4, 4, "Armor", "Plate", "INVTYPE_CHEST" },
    [9] = { "Wooden Shield", 4, 6, "Armor", "Shields", "INVTYPE_SHIELD" },
  }
  C_Item.GetItemInfo = function(link)
    local id = tonumber(tostring(link):match("item:(%d+)"))
    local k = kinds[id]
    if not k then return nil end
    return k[1], link, 2, 20, 15, k[4], k[5], 1, k[6], 0, 100, k[2], k[3]
  end
  function CannotUse()
    local out = {}
    for id = 1, #kinds do
      local f = ForeverAI.Collect.ItemFacts("|cff1eff00|Hitem:" .. id .. "::::|h[" .. kinds[id][1] .. "]|h|r")
      out[#out + 1] = kinds[id][1] .. "=" .. tostring(f.cannotUse)
    end
    return table.concat(out, "|")
  end
"""


def test_weapons_need_the_skill_the_skill_window_names(sim):
    """The skill window says "Swords" where a one-handed sword says "One-Handed Swords": the add-on told the model a
    mage with Swords could not wear any one-handed sword, and the auction search left all of them out."""
    sim.lua(ITEM_TYPES)
    # the simulated hunter (the Classic skill window): Bows, Daggers, Swords, Cloth, Leather
    assert text(sim.lua("return CannotUse()")).split("|") == [
        "Glinting Steel Sword=nil", "Heavy Claymore=missing skill: Two-Handed Swords",
        "Woodsman Hatchet=missing skill: One-Handed Axes", "Bandit Knife=nil", "Strong Fishing Pole=nil",
        "Chainmail Vest=missing skill: Mail", "Padded Boots=nil", "Iron Breastplate=missing skill: Plate",
        "Wooden Shield=missing skill: Shields"]


def test_weapon_skills_from_the_skill_info_api(sim):
    # a client with C_SkillInfo, as WoW Forever 1.60.1 (the mage's "Swords 1/105"): its names come first, the
    # Classic window is not asked
    sim.lua("""
      local lines = { "Axes", "Plate Mail", "Shield", "Mail", "Swords" }
      C_SkillInfo.GetNumSkillLines = function() return #lines end
      C_SkillInfo.GetSkillLineInfo = function(i) return { skillName = lines[i], skillRank = 1, skillMaxRank = 1 } end
    """)
    sim.lua(ITEM_TYPES)
    assert text(sim.lua("return CannotUse()")).split("|") == [
        "Glinting Steel Sword=nil", "Heavy Claymore=missing skill: Two-Handed Swords", "Woodsman Hatchet=nil",
        "Bandit Knife=missing skill: Daggers", "Strong Fishing Pole=nil", "Chainmail Vest=nil",
        "Padded Boots=missing skill: Leather", "Iron Breastplate=nil", "Wooden Shield=nil"]
