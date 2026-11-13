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

def test_every_kind_of_action_on_a_bar_is_named(sim):
    """"C-3 shows a question mark, it is a pet skill": a pet's ability on the player's bar comes with an id the spell
    API does not name. Spells keep their rank (a bar can hold a lower one), and an action the add-on cannot name by its
    id takes the name from the button's own tooltip."""
    sim.slash("/fai")
    sim.lua("""
      PET_BOOK = { { name = "Growl", spellID = 2649, actionID = 5000 } }
      local box = CreateFrame("Frame", "MultiBarPetTestButtonContainer1", UIParent)
      for n = 1, 7 do
        local b = CreateFrame("Button", "MultiBarPetTestButton" .. n, box)
        b.action = 60 + n
      end
      local acts = { [61] = { "spell", 1978, "spell" }, [62] = { "spell", 0x1000000 + 17253, "pet" }, [63] = { "spell", 999 },
                     [64] = { "macro", 5 }, [65] = { "flyout", 7 }, [66] = { "summonmount", 9 }, [67] = { "spell", 5000, "pet" } }
      GetActionInfo = function(slot) local a = acts[slot] if a then return a[1], a[2], a[3] end end
      HasAction = function(slot) return acts[slot] ~= nil end
      local spells = { [1978] = "Serpent Sting", [17253] = "Bite", [2649] = "Growl" }
      C_Spell.GetSpellName = function(id) return spells[id] end
      C_Spell.GetSpellSubtext = function(id) return ({ [1978] = "Rank 1", [17253] = "Rank 2" })[id] end
      C_SpellBook.HasPetSpells = function() return #PET_BOOK, "PET" end
      C_SpellBook.GetSpellBookItemInfo = function(i, bank) if bank == Enum.SpellBookSpellBank.Pet then return PET_BOOK[i] end end
      C_TooltipInfo.GetAction = function(slot) if slot == 63 then return { lines = { { leftText = "Dash" } } } end end
      GetMacroInfo = function() return "Feed" end
      GetMacroItem = function() return "Tough Jerky" end
      GetFlyoutInfo = function() return "Traps" end
      C_MountJournal.GetMountInfoByID = function() return "Striped Nightsaber" end
    """)
    bars = text(sim.lua("return table.concat(ForeverAI.Collect.sections.actionBars(), '\\n')"))
    assert bars.splitlines() == ["MultiBarPetTestButtonContainer — 7 of 7 buttons used",
                                 "  button 1: Serpent Sting (Rank 1)", "  button 2: Bite (Rank 2) [pet ability]",
                                 "  button 3: Dash", "  button 4: macro Feed → Tough Jerky", "  button 5: flyout Traps",
                                 "  button 6: mount Striped Nightsaber", "  button 7: Growl [pet ability]"]
    # what this client gives for an action named other than by its id is logged once, to learn from
    log = text(sim.lua("return table.concat(ForeverAI.DebugLog.Lines(20), ' | ')"))
    assert "bars: slot 63: spell 999 nil = Dash (tooltip)" in log and "slot 62: spell 16794469 pet = Bite (Rank 2) (pet)" in log
    sim.lua("ForeverAI.Collect.sections.actionBars()")
    assert text(sim.lua("return table.concat(ForeverAI.DebugLog.Lines(40), ' | ')")).count("slot 63:") == 1

def test_the_pet_says_how_it_is_and_what_it_can_do(sim):
    """Happiness comes from C_PetInfo in WoW Forever (its own pet add-on reads it there; the global gave nothing),
    auto-cast is on only when the game says it is on (not when it may be), and the pet's abilities say what they do."""
    sim.lua("""
      local exists = UnitExists
      UnitExists = function(u) return u == "pet" or exists(u) end
      UnitCreatureFamily = function(u) if u == "pet" then return "Spider" end end
      C_PetInfo.GetPetHappiness = function() return 3 end
      GetPetTrainingPoints = function() return 120, 35 end
      GetPetExperience = function() return 300, 1200 end
      GetPetFoodTypes = function() return "Meat", "Fish" end
      C_PetInfo.CanPetEatItem = function(id) return id == 117 end
      C_Container.GetContainerNumSlots = function(bag) return bag == 0 and 3 or 0 end
      local bag = { { itemID = 117, hyperlink = "|cffffffff|Hitem:117::::::::13:::::::|h[Tough Jerky]|h|r", stackCount = 5 },
                    { itemID = 159, hyperlink = "|cffffffff|Hitem:159::::::::13:::::::|h[Refreshing Spring Water]|h|r", stackCount = 10 },
                    { itemID = 117, hyperlink = "|cffffffff|Hitem:117::::::::13:::::::|h[Tough Jerky]|h|r", stackCount = 2 } }
      C_Container.GetContainerItemInfo = function(b, slot) if b == 0 then return bag[slot] end end
      PET_ACTION_ATTACK, PET_ACTION_FOLLOW, PET_MODE_DEFENSIVE = "Attack", "Follow", "Defensive"
      local bar = { { "PET_ACTION_ATTACK", 1, true, false, false, false }, { "PET_ACTION_FOLLOW", 2, true, true, false, false },
                    { "Bite", 3, false, false, true, true, 17253 }, { "Growl", 4, false, false, true, false, 2649 },
                    [10] = { "PET_MODE_DEFENSIVE", 5, true, true, false, false } }
      GetPetActionInfo = function(i) local a = bar[i] if a then return unpack(a, 1, 7) end end
      local book = { { name = "Attack", actionID = 7 }, { name = "Follow", spellID = 1, actionID = 8 },
                     { name = "Bite", spellID = 17253 }, { name = "Growl", spellID = 2649 },
                     { name = "Great Stamina", spellID = 4187, isPassive = true } }
      C_SpellBook.HasPetSpells = function() return #book, "PET" end
      C_SpellBook.GetSpellBookItemInfo = function(i, b) if b == Enum.SpellBookSpellBank.Pet then return book[i] end end
      local spells = { [17253] = "Bite", [2649] = "Growl", [4187] = "Great Stamina" }
      C_Spell.GetSpellName = function(id) return spells[id] end
      C_Spell.GetSpellSubtext = function(id) return ({ [17253] = "Rank 2", [2649] = "Rank 1", [4187] = "Rank 1" })[id] end
      C_Spell.GetSpellDescription = function(id) if id == 17253 then return "Bite the enemy, causing 7 to 9 damage." end end
    """)
    pet = "local p = ForeverAI.Collect.sections.pet() return "
    assert text(sim.lua(pet + "table.concat({ p.family, p.happiness, p.trainingPoints, p.xp, p.diet, p.food }, ' ; ')")) ==         "Spider ; happy ; 85 free (35 spent) ; 300/1200 ; Meat, Fish ; [Tough Jerky] x7"
    assert text(sim.lua(pet + "table.concat(p.bar, ', ')")) ==         "Attack, Follow [active], Bite [auto-cast on], Growl [auto-cast off], Defensive [active]"
    assert text(sim.lua(pet + "table.concat(p.abilities, ' | ')")) ==         "Bite (Rank 2): Bite the enemy, causing 7 to 9 damage. | Growl (Rank 1) | Great Stamina (Rank 1) [passive]"
    assert sim.lua(pet + "p.loyalty") is None  # the game gives none: nothing is made up
    sim.lua("C_PetInfo.CanPetEatItem = function() return false end")
    assert text(sim.lua(pet + "p.food")) == "nothing in the bags"

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

def test_the_sources_of_an_objective_say_how_hard_and_how_sure_they_are(sim):
    """Den Mother or the bears: the level alone did not tell. Questie knows each creature's rank and health and the
    chance of the drop (its drop data): the AI gets them with every source."""
    sim.lua("""
      local npcs = {
        [2172] = { name = "Den Mother", minLevel = 18, maxLevel = 19, rank = 1, minLevelHealth = 1342, maxLevelHealth = 1342, at = { 51.5, 38.3 } },
        [2163] = { name = "Grizzled Thistle Bear", minLevel = 16, maxLevel = 17, rank = 0, minLevelHealth = 617, maxLevelHealth = 665, at = { 39.1, 70.0 } },
        [2164] = { name = "Rabid Thistle Bear", minLevel = 13, maxLevel = 14, at = { 45.0, 60.0 } },
      }
      local db = {}
      function db.QueryNPCSingle(id, field)
        local n = npcs[id]
        if not n then return nil end
        if field == "spawns" then return { [148] = { n.at } } end
        return n[field]
      end
      function db.QueryQuestSingle(id, field) if id == 1003 and field == "objectives" then return { nil, nil, { { 5385 } } } end end
      function db.QueryItemSingle(id, field)
        if id ~= 5385 then return nil end
        if field == "name" then return "Grizzled Scalp" elseif field == "npcDrops" then return { 2172, 2163, 2164 } end
      end
      function db.GetItemDroprate(item, npc) return ({ [2172] = { 18.2, "cmangos" }, [2163] = { 32.4, "cmangos" }, [2164] = { 0.4, "wowhead" } })[npc] end
      local zones = { GetAreaIdByUiMapId = function() return 148 end }
      QuestieLoader = { ImportModule = function(_, name) if name == "QuestieDB" then return db elseif name == "ZoneDB" then return zones end error("no module") end }
      C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or nil end
    """)
    where = text(sim.lua("return table.concat(ForeverAI.Collect.QuestInfo(1003).whereToGet, ' | ')"))
    assert "dropped by Den Mother (level 18-19, elite, 1342 health, 18% drop chance) — Darkshore 51.5, 38.3" in where
    assert "dropped by Grizzled Thistle Bear (level 16-17, 617-665 health, 32% drop chance) — Darkshore 39.1, 70.0" in where
    assert "dropped by Rabid Thistle Bear (level 13-14, 0.4% drop chance) — Darkshore 45.0, 60.0" in where

def test_many_spawn_points_send_the_player_where_they_stand_together(sim):
    """"Znajdź innego spota gdzie jest ich więcej" / "jezus oznacz te inne spawny": the data named only the nearest
    spawn point (two of them by the player, the camp with a dozen half a kilometre south)."""
    sim.lua('C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or nil end')
    where = text(sim.lua("""
      local pts = { { area = 148, x = 37.3, y = 44.3 }, { area = 148, x = 37.5, y = 44.6 } }   -- two by the player
      for i = 0, 5 do pts[#pts + 1] = { area = 148, x = 40.0 + i * 0.3, y = 60.0 + (i % 2) * 0.4 } end   -- the camp
      for i = 0, 2 do pts[#pts + 1] = { area = 148, x = 45.0 + i * 0.3, y = 80.0 } end   -- a smaller group
      local list = { { label = "Defias Highwayman", sources = { { how = "kill", name = "Defias Highwayman", level = "17-18", pts = pts } } } }
      return table.concat(ForeverAI.Collect.WhereToGet(list, 1439, 37.1, 44.1, 148, nil), " | ")
    """))
    assert where.startswith("Defias Highwayman: kill Defias Highwayman (level 17-18) — Darkshore 40.0, 60.0 (")
    assert "11 spawn points in your zone, 6 of them around here, others: 3 around 45." in where
    assert ";" not in where  # one source: the planner splits the sources of an objective at "; "
    # "Jeżeli tych mobów do zabicia jest dużo, np. powyżej 5": how many are left decides. One more kill is done at the
    # nearest spawn point; twelve more where they are dense; five pelts at a 40% chance are a dozen kills too.
    spot = """
      local pts = { { area = 148, x = 37.3, y = 44.3 }, { area = 148, x = 37.5, y = 44.6 } }
      for i = 0, 5 do pts[#pts + 1] = { area = 148, x = 40.0 + i * 0.3, y = 60.0 + (i %% 2) * 0.4 } end
      local list = { { label = "%s", sources = { { how = "%s", name = "Defias Highwayman", chance = %s, pts = pts } } } }
      return ForeverAI.Collect.WhereToGet(list, 1439, 37.1, 44.1, 148, { "%s" })[1]
    """
    assert " — Darkshore 37.3, 44.3 (" in text(sim.lua(spot % ("Defias Highwayman", "kill", "nil", "14/15 Defias Highwayman slain")))
    assert " — Darkshore 40.0, 60.0 (" in text(sim.lua(spot % ("Defias Highwayman", "kill", "nil", "3/15 Defias Highwayman slain")))
    assert " — Darkshore 37.3, 44.3 (" in text(sim.lua(spot % ("Defias Highwayman", "kill", "nil", "11/15 Defias Highwayman slain")))
    assert " — Darkshore 40.0, 60.0 (" in text(sim.lua(spot % ("Fine Pelt", "dropped by", "40", "0/5 Fine Pelt")))
    assert " — Darkshore 37.3, 44.3 (" in text(sim.lua(spot % ("Fine Pelt", "dropped by", "40", "4/5 Fine Pelt")))
    # a few spawn points close together: the nearest, as before
    few = text(sim.lua("""
      local pts = { { area = 148, x = 37.3, y = 44.3 }, { area = 148, x = 37.5, y = 44.6 }, { area = 148, x = 60.0, y = 20.0 } }
      local list = { { label = "Boar", sources = { { how = "kill", name = "Boar", level = "5", pts = pts } } } }
      return table.concat(ForeverAI.Collect.WhereToGet(list, 1439, 37.1, 44.1, 148, nil), " | ")
    """))
    assert few.startswith("Boar: kill Boar (level 5) — Darkshore 37.3, 44.3 (") and "2 of them around here" in few

def test_the_trainer_window_says_what_each_skill_does(sim):
    """"Pytam do cholery co robią i nie odpowiadasz": the list had the names and the prices only."""
    sim.lua("""
      local services = { { "Multi-Shot", "Rank 1", "available", 1600, 18, "Fires a shot at the target and up to 2 nearby enemies." },
                         { "Aimed Shot", "Rank 1", "unavailable", 2100, 20, string.rep("An aimed shot that hits hard. ", 12) } }
      GetNumTrainerServices = function() return #services end
      GetTrainerServiceInfo = function(i) return services[i][1], services[i][2], services[i][3] end
      GetTrainerServiceCost = function(i) return services[i][4] end
      GetTrainerServiceLevelReq = function(i) return services[i][5] end
      GetTrainerServiceDescription = function(i) return services[i][6] end
    """)
    sim._wow.fire(b"TRAINER_SHOW")
    sim.run(1)
    listed = sim.to_py(sim.lua("return ForeverAI.Collect.sections.windows().trainer.services"))
    assert text(listed[0]) == ("Multi-Shot (Rank 1) — 0g 16s 0c, level 18, available: Fires a shot at the target and up "
                               "to 2 nearby enemies.")
    later = text(listed[1])
    assert later.startswith("Aimed Shot (Rank 1) — 0g 21s 0c, level 20, unavailable: An aimed shot that hits hard.")
    assert len(later) < 240  # what cannot be learned yet is said in short

def test_the_sizes_of_the_zone_maps_go_to_the_planner(sim):
    sizes = [text(s) for s in sim.to_py(sim.lua("return ForeverAI.Collect.sections.mapSizes()"))]
    assert sizes[:4] == ["Ashenvale: 5273 x 3515", "Darkshore: 5989 x 3993", "Darnassus: 968 x 645", "Duskwood: 2469 x 1646"]
    assert "Teldrassil: 4656 x 3103" in sizes and "Redridge Mountains: 1985 x 1324" in sizes  # (both continents of the simulator)
    assert sizes == sorted(sizes)

def test_the_pet_section_survives_a_diet_given_as_a_list(sim):
    """In the game the whole pet section was lost: "invalid value (table) at index 1 in table for 'concat'". This
    client returns the pet's food types as one list."""
    sim.lua("""
      local exists = UnitExists
      UnitExists = function(u) return u == "pet" or exists(u) end
      C_PetInfo.GetPetHappiness = function() return 2 end
      C_PetInfo.GetPetFoodTypes = function() return { "Meat", "Fish" } end
      C_PetInfo.CanPetEatItem = function() error("no such item") end
    """)
    pet = "local p = ForeverAI.Collect.sections.pet() return "
    assert text(sim.lua(pet + "p.happiness .. '|' .. p.diet .. '|' .. tostring(p.food)")) == "content|Meat, Fish|nothing in the bags"
    assert "pet: happiness 2, " in text(sim.lua("return table.concat(ForeverAI.DebugLog.Lines(5), ' | ')"))

def test_drop_chances_read_before_questie_has_them_are_read_again(sim):
    """The add-on reads the quest log three seconds after login, Questie loads its drop tables later: the sources
    were kept for the session without a single drop chance."""
    sim.lua("""
      TEST_log = { { id = 986, title = "A Lost Master", objectives = { { text = "0/5 Fine Moonstalker Pelt", finished = false } } } }
      C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
      C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and { title = q.title, questID = q.id, isHeader = false } end
      C_QuestLog.IsComplete = function() return false end
      C_QuestLog.GetQuestObjectives = function(id) return TEST_log[1].objectives end
      local drops = { GetItemDroprate = function() return nil end }
      TEST_drops = drops
      local db = {}
      function db.QueryNPCSingle(id, field)
        if id ~= 2237 then return nil end
        if field == "name" then return "Moonstalker Sire" elseif field == "minLevel" then return 17 elseif field == "maxLevel" then return 18
        elseif field == "spawns" then return { [148] = { { 43.8, 81.8 } } } end
      end
      function db.QueryQuestSingle(id, field) if id == 986 and field == "objectives" then return { nil, nil, { { 5386 } } } end end
      function db.QueryItemSingle(id, field)
        if field == "name" then return "Fine Moonstalker Pelt" elseif field == "npcDrops" then return { 2237 } end
      end
      function db.GetItemDroprate(item, npc) return TEST_drops.tableWowhead and { 40.0, "cmangos" } or nil end
      local zones = { GetAreaIdByUiMapId = function() return 148 end }
      QuestieLoader = { ImportModule = function(_, name)
        if name == "QuestieDB" then return db elseif name == "ZoneDB" then return zones elseif name == "DropDB" then return drops end
        error("no module") end }
      C_Map.GetAreaInfo = function(id) return id == 148 and "Darkshore" or nil end
    """)
    where = "for _, q in ipairs(ForeverAI.Collect.sections.quests()) do if type(q) == 'table' then return q.whereToGet[1] end end"
    assert "dropped by Moonstalker Sire (level 17-18) — Darkshore 43.8, 81.8" in text(sim.lua(where))  # Questie not ready
    sim.lua("TEST_drops.tableWowhead = {}")  # its drop tables are loaded
    assert "dropped by Moonstalker Sire (level 17-18, 40% drop chance) — Darkshore 43.8, 81.8" in text(sim.lua(where))

ZONE_QUESTS = """
  local quests = {
    [167] = { name = "Oh Brother. . .", questLevel = 20, requiredLevel = 15, zoneOrSort = 1581, startedBy = { { 656 } },
              finishedBy = { { 656 } }, objectivesText = { "Bring the badge to Wilder Thistlenettle." } },
    [12] = { name = "The People's Militia", questLevel = 12, requiredLevel = 9, zoneOrSort = 40, startedBy = { { 234 } },
             finishedBy = { { 234 } }, objectivesText = { "Kill 15 Defias Trappers." } },
    [103] = { name = "Keeper of the Flame", questLevel = 16, requiredLevel = 30, zoneOrSort = 40, startedBy = { { 392 } },
              finishedBy = { { 392 } } },
    [900] = { name = "Elsewhere", questLevel = 14, requiredLevel = 10, zoneOrSort = 148, startedBy = { { 3 } } },
  }
  local npcs = { [656] = { name = "Wilder Thistlenettle", spawns = { [1519] = { { 70.3, 40.8 } } } },
                 [234] = { name = "Gryan Stoutmantle", spawns = { [40] = { { 56.3, 47.5 } } } },
                 [392] = { name = "Captain Grayson", spawns = { [40] = { { 30.0, 86.0 } } } },
                 [3] = { name = "Somebody", spawns = { [148] = { { 10.0, 10.0 } } } },
                 [590] = { name = "Defias Trapper", minLevel = 12, maxLevel = 13, spawns = { [40] = { { 48.0, 30.0 } } } } }
  local db = {}
  function db.GetQuest(id) return quests[id] end
  function db.GetNPC(_, id) return npcs[id] end
  function db.GetQuestTagInfo(id) if id == 167 then return 81, "Dungeon" end end
  function db.QueryNPCSingle(id, field)
    local n = npcs[id]
    if not n then return nil end
    return n[field]
  end
  function db.QueryQuestSingle(id, field) if id == 12 and field == "objectives" then return { { { 590 } } } end end
  local available = { __availableQuests = { [167] = true, [12] = true, [103] = true, [900] = true } }
  QuestieLoader = { ImportModule = function(_, name)
    if name == "QuestieDB" then return db elseif name == "AvailableQuests" then return available end
    error("no module") end }
  local areas = { [40] = "Westfall", [1519] = "Stormwind City", [1581] = "The Deadmines", [148] = "Darkshore" }
  C_Map.GetAreaInfo = function(id) return areas[id] end
"""

def test_the_quests_of_any_zone_come_from_the_game_on_request(sim):
    """"Chcę robić questy pod Deadmines i inne w pobliżu": the data only held the quests around the player (in
    Stormwind), so the AI read guides on the web for 100 seconds and named quests the player had done."""
    sim.lua(ZONE_QUESTS)
    got = sim.to_py(sim.lua('return ForeverAI.Collect.ZoneQuests("Westfall")'))
    assert [text(q["title"]) for q in got["quests"]] == ["The People's Militia"] and got["later"] == 1  # Keeper: level 30
    militia = got["quests"][0]
    assert text(militia["giver"]) == "Gryan Stoutmantle" and text(militia["where"]) == "Westfall 56.3, 47.5"
    assert text(militia["turnIn"]) == "Gryan Stoutmantle (Westfall 56.3, 47.5)"
    assert text(militia["whereToGet"][0]) == "Defias Trapper: kill Defias Trapper (level 12-13) — Westfall 48.0, 30.0"
    # a dungeon's quests are given elsewhere: they are found by the dungeon they belong to
    mines = sim.to_py(sim.lua('return ForeverAI.Collect.ZoneQuests("deadmines")'))
    (brother,) = mines["quests"]
    assert text(brother["title"]) == "Oh Brother. . ." and text(brother["where"]) == "Stormwind City 70.3, 40.8"
    assert text(brother["type"]) == "Dungeon" and text(brother["about"]) == "The Deadmines"
    assert sim.to_py(sim.lua('return ForeverAI.Collect.ZoneQuests("Moonglade")'))["quests"] in ({}, [])

def test_the_request_for_a_zones_quests_is_answered_to_the_app(sim):
    sim.slash("/fai")
    sim.lua(ZONE_QUESTS)
    sim.lua('ForeverAI.HandleRequests({ { id = "9-1", op = "zone_quests", zone = "Westfall" } })')
    sim.run(3)
    (result,) = [m for m in sim.sent_messages() if m.get("type") == "game_result"]
    assert result["id"] == "9-1" and result["result"]["quests"][0]["title"] == "The People's Militia"

def test_player_says_when_the_hearthstone_is_ready_where_the_corpse_lies_and_a_flight(sim):
    read = "local p = ForeverAI.Collect.sections.player() return tostring(p.hearthstone) .. '|' .. tostring(p.corpse) .. '|' .. tostring(p.onFlight)"
    sim.lua("GetBindLocation = function() return 'Shadowglen' end")
    assert text(sim.lua(read)) == "Shadowglen (hearthstone ready)|nil|nil"
    sim.lua("""
      C_Container.GetItemCooldown = function(id) if id == 6948 then return 1, GetTime() - 1 + 3540, 1 end return 0, 0, 1 end
      UnitOnTaxi = function() return true end
      UnitIsDeadOrGhost = function() return true end
      C_DeathInfo.GetCorpseMapPosition = function() return { x = 0.412, y = 0.587 } end
    """)
    hearth, corpse, flight = text(sim.lua(read)).split("|")
    assert hearth == "Shadowglen (hearthstone ready in 59 min)" and flight == "true"
    assert corpse.endswith(" 41.2, 58.7") and not corpse.startswith("nil")

def test_a_spell_known_in_several_ranks_is_described_once(sim):
    """A level 18 hunter's book: Raptor Strike in three ranks, each with its whole text, a quarter of the section."""
    sim.lua("""
      local book = { { "Raptor Strike", "Rank 1", 2973 }, { "Raptor Strike", "Rank 2", 14260 }, { "Raptor Strike", "Rank 3", 14261 },
                     { "Track Beasts", "", 1494 }, { "Aspect of the Hawk", "Rank 1", 13165 } }
      C_SpellBook.GetNumSpellBookSkillLines = function() return 1 end
      C_SpellBook.GetSpellBookSkillLineInfo = function() return { name = "Survival", itemIndexOffset = 0, numSpellBookItems = #book } end
      C_SpellBook.GetSpellBookItemInfo = function(k) return book[k] and { name = book[k][1], spellID = book[k][3] } end
      C_Spell.GetSpellSubtext = function(id) for _, b in ipairs(book) do if b[3] == id then return b[2] end end end
    """)
    lines = text(sim.lua("return table.concat(ForeverAI.Collect.sections.spells(), '\\n')")).split("\n")
    names = [line.split(":")[0] for line in lines]
    assert names == ["[Survival] Raptor Strike (Rank 3) [also known at rank 1, 2, for less mana]", "[Survival] Track Beasts",
                     "[Survival] Aspect of the Hawk (Rank 1)"]

def test_a_quest_target_inside_a_dungeon_says_which(sim):
    """From the game: "dropped by Sneed's Shredder (level 20, elite, 100% drop chance) — no known spawn point"."""
    got = text(sim.lua("""
      local list = { { label = "Gnoam Sprecklesprocket", sources = { { how = "dropped by", name = "Sneed's Shredder", pts = {}, inside = "The Deadmines" } } },
                     { label = "Lost Thing", sources = { { how = "dropped by", name = "Nobody", pts = {} } } } }
      return table.concat(ForeverAI.Collect.WhereToGet(list, 1436, 50, 50, 40, {}), "\\n")
    """))
    assert "dropped by Sneed's Shredder — inside The Deadmines (no place on a zone map)" in got
    assert "dropped by Nobody — no known spawn point" in got

def test_a_tooltip_of_the_addons_own_window_is_not_what_the_player_looked_at(sim):
    """From the game: tooltip = "Clear the marks on the map", the hint of the add-on's own button."""
    sim.slash("/fai")
    sim.hover("x")
    assert "Close (Esc)" in sim.texts()
    assert sim.lua("return ForeverAI.Collect.sections.tooltip() == nil") is True
    sim.leave()

def test_in_a_crowd_the_players_that_matter_are_sent_and_the_rest_counted(sim):
    """Stormwind's Trade District: forty players with guild, race and target were a tenth of the question, and the
    AI is shown fifteen names."""
    sim.lua("""
      GetPlayerInfoByGUID = function(guid)
        return "Mage", "MAGE", "Human", "Human", 2, "Player" .. guid:match("(%d+)$"), ""
      end
    """)
    for i in range(1, 31):
        sim._wow.fire(b"CHAT_MSG_SAY", b"hi", b"x", b"", b"", b"", b"", 0, 0, b"", 0, 0, f"Player-1-{i:08d}".encode())
    got = text(sim.lua("""
      local n = ForeverAI.Players.Nearby()
      return #n .. "|" .. tostring(n[#n].more) .. "|" .. tostring(n[1].race) .. "|" .. tostring(n[1].class) .. "|" .. tostring(n[#n].name)
    """))
    assert got == "21|10|nil|Mage|nil"  # twenty, the rest as a number; no race or guild for a passer-by
    from foreverai.prompt import _nearby
    people = [{"name": f"P{i}", "level": 20, "class": "Mage"} for i in range(20)] + [{"more": 10}]
    assert _nearby(people, "Me").startswith("30 players noticed nearby, levels 20-20: P0 20 Mage,")
    assert _nearby(people, "Me").endswith(" and 15 more")

def test_a_zones_quest_says_how_many_places_its_object_can_be_at(sim):
    """Asked for another zone the add-on names one place of a source and no distance; with several spawn points that
    place is somewhere to search around, and the count has to come along."""
    sim.lua(ZONE_QUESTS.replace("spawns = { [40] = { { 48.0, 30.0 } } } } }",
                                "spawns = { [40] = { { 48.0, 30.0 }, { 49.0, 31.0 }, { 47.0, 29.0 } } } } }"))
    got = sim.to_py(sim.lua('return ForeverAI.Collect.ZoneQuests("Westfall")'))
    assert text(got["quests"][0]["whereToGet"][0]) == (
        "Defias Trapper: kill Defias Trapper (level 12-13) — Westfall 48.0, 30.0 (3 spawn points in that zone)")

def test_the_ai_is_told_what_the_player_saw_and_did(sim):
    """The player: "it should see the alerts it sends and how the player reacted". A click on the shopping list card
    and a card dismissed go to the AI with the next message, once."""
    sim.slash("/fai")
    sim.run(2)
    sim.lua('ForeverAI.UI.AuctionCard({ { id = 1, name = "Gloves of the Fang", price = "1g 50s" } })')
    sim.run(1)
    sim.click("Dismiss")
    sim.lua('ForeverAI.Seen("route alert shown to the player: Done: Thor. Next: 2. Auction House")')
    sim.ask("and now?")
    sim.run(6)
    (msg,) = [m for m in sim.sent_messages() if m.get("type") == "question"]
    seen = msg["snapshot"].get("sinceLastAnswer") or []
    assert any("the player dismissed the shopping list card: Gloves of the Fang" in line for line in seen), seen
    assert any("route alert shown to the player" in line for line in seen) and all(" ago: " in line for line in seen)
    assert sim.lua("return ForeverAICharDB.seen == nil") and sim.errors == []  # told once

def test_item_facts_say_what_the_game_marks_as_not_met(sim):
    """From the game: [Green Tinted Goggles], "Requires Engineering (150)" in red in its tooltip, was recommended to a
    character without Engineering: the add-on judged "cannot use" by the armor type alone. It reads the lines the game
    colours red, from the tooltip data or, in a client whose data has no colours, from a tooltip of its own."""
    sim.lua("""
      TEST_link = "|cff1eff00|Hitem:4385::::::::20:::::|h[Green Tinted Goggles]|h|r"
      TEST_other = "|cff1eff00|Hitem:4386::::::::20:::::|h[Plain Goggles]|h|r"
      C_Item.GetItemInfo = function(link)
        local name = tostring(link):match("%[(.-)%]")
        return name, link, 2, 30, 0, "Armor", "Cloth", 1, "INVTYPE_HEAD", 0, 1410, 4
      end
      local white, red = { r = 1, g = 1, b = 1 }, { r = 1, g = 0.125, b = 0.125 }
      C_TooltipInfo.GetHyperlink = function(link)
        local lines = { { leftText = "Goggles", leftColor = { r = 0.12, g = 1, b = 0 } },
          { leftText = "Head", leftColor = white, rightText = "Cloth", rightColor = white } }
        if link == TEST_link then lines[3] = { leftText = "Requires Engineering (150)", leftColor = red } end
        return { lines = lines }
      end
    """)
    assert text(sim.lua("return ForeverAI.Collect.ItemFacts(TEST_link, true).cannotUse")) == "not met: Requires Engineering (150)"
    assert sim.lua("return ForeverAI.Collect.ItemFacts(TEST_other, true).cannotUse") is None
    assert text(sim.lua("return ForeverAI.Collect.unmetFrom")) == "tooltip data"
    # a client whose tooltip data has no colours: the add-on's own hidden tooltip is read
    sim.lua("""
      wipe = wipe or function(t) for k in pairs(t) do t[k] = nil end end
      C_TooltipInfo.GetHyperlink = function() return { lines = { { leftText = "Goggles" } } } end
      TEST_third = "|cff1eff00|Hitem:4387::::::::20:::::|h[Other Goggles]|h|r"
      ForeverAI.Collect.ItemFacts("|cff1eff00|Hitem:1::::::::20:::::|h[First]|h|r")   -- makes the tooltip
      ForeverAIScanTooltip.SetHyperlink = function(self, link)
        self:AddLine("Other Goggles", 0.12, 1, 0)
        self:AddLine("Classes: Mage", 1, 0.125, 0.125)
      end
    """)
    assert text(sim.lua("return ForeverAI.Collect.ItemFacts(TEST_third).cannotUse")) == "not met: Classes: Mage"
    assert text(sim.lua("return ForeverAI.Collect.unmetFrom")) == "hidden tooltip" and sim.errors == []

def test_requests_that_come_together_are_answered_in_one_message(sim):
    """Every message to the app is a screenshot, one a second at most: ten auction searches of one answer took 12 s,
    one round trip each. Requests the app sends together are worked on together and their results leave as one
    message; a result does not wait for a request that never answers."""
    sim.slash("/fai")
    sim.lua(ZONE_QUESTS)
    sim.lua("""
      ForeverAI.Auction = { Search = function(req, reply) TEST_reply = reply end }   -- answers later, like a search
      ForeverAI.HandleRequests({ { id = "9-1", op = "zone_quests", zone = "Westfall" },
        { id = "9-2", op = "zone_quests", zone = "Westfall" }, { id = "9-3", op = "auction_search" } })
    """)
    sim.run(0.5)
    assert sim.sent_messages() == []  # the search is still running: the two that are through wait for it
    sim.lua('TEST_reply({ items = {} })')
    sim.run(3)
    (msg,) = sim.sent_messages()
    assert msg["type"] == "game_results" and [r["id"] for r in msg["results"]] == ["9-1", "9-2", "9-3"]
    assert msg["results"][0]["result"]["quests"][0]["title"] == "The People's Militia"
    # a search that never answers holds the others back for a second, not for good
    sim.lua("""
      ForeverAI.Auction = { Search = function() end }
      ForeverAI.HandleRequests({ { id = "9-4", op = "auction_search" }, { id = "9-5", op = "zone_quests", zone = "Westfall" } })
    """)
    sim.run(4)
    (msg,) = sim.sent_messages()
    assert msg["type"] == "game_result" and msg["id"] == "9-5" and sim.errors == []

def test_two_messages_in_one_second_both_reach_the_app(sim):
    """The game names screenshots to the second: a message sent in the second of the one before took its file name,
    and the first was gone unless the app had read it already."""
    sim.slash("/fai")
    sim.lua("""
      local T = ForeverAI.Transport
      T.Send({ v = 1, type = "game_result", msgId = 71, id = "a", result = { n = 1 } }, function()
        T.Send({ v = 1, type = "game_result", msgId = 72, id = "b", result = { n = 2 } })
      end)
    """)
    sim.run(4)
    assert sorted(m["id"] for m in sim.sent_messages()) == ["a", "b"] and sim.errors == []

def test_sections_cut_to_fit_are_named(sim):
    """Game data too large to send lost its last sections in silence: the AI read "no professions" as "has none"."""
    from foreverai.prompt import render_snapshot
    sim.lua("""
      ForeverAI.db.sections.chat = true
      for i = 1, 20 do ForeverAI.recentChat[i] = "[say] Someone: " .. string.rep("x", 400) end
      ForeverAI.db.maxSnapshotKB = 1
    """)
    left = sim.to_py(sim.lua("return ForeverAI.Collect.Snapshot().leftOut"))
    assert left and left[0] == "chat", left
    text = render_snapshot({"player": {"name": "X"}, "leftOut": ["chat", "professions"]})
    assert "not sent by the game because the data was too large: chat, professions" in text and "## leftOut" not in text

def test_a_question_is_not_refused_while_a_message_of_the_add_ons_own_goes_out(sim):
    """Found by a test once a message waits for the next second: a question asked while a result for the app was on
    its way was refused with "The previous question is still being sent". It waits its turn and goes out."""
    sim.slash("/fai")
    sim.lua('ForeverAI.Transport.Send({ v = 1, type = "game_result", msgId = 81, id = "a", result = { n = 1 } })')
    assert sim.lua("return ForeverAI.Transport.sending") and not sim.lua("return ForeverAI.Transport.Busy()")
    sim.ask("where now?")
    sim.run(6)
    kinds = [m.get("type") for m in sim.sent_messages()]
    assert kinds == ["game_result", "question"], kinds
    assert "still being sent" not in "\n".join(sim.texts()) and sim.errors == []

def test_blocks_of_different_sizes_follow_each_other(sim):
    """A cell stays shown from the block before and is not shown again: a small block after a large one hides the
    rest, and a large one after it shows them again. Each message reads back whole."""
    sim.slash("/fai")
    sizes = (6000, 40, 9000, 300)
    for n, size in enumerate(sizes):
        sim.lua("""
          local n, size = ...
          local parts = {}
          for i = 1, size do parts[i] = string.char(97 + (i * 7 + n) % 26) .. tostring((i * 31) % 97) end
          ForeverAI.Transport.Send({ v = 1, type = "game_result", msgId = 90 + n, id = "m" .. n, result = { text = table.concat(parts) } })
        """, n, size)
    sim.run(8)
    got = {m["id"]: len(m["result"]["text"]) for m in sim.sent_messages()}
    assert sorted(got) == ["m0", "m1", "m2", "m3"] and got["m0"] > got["m3"] > got["m1"] and got["m2"] > got["m0"]
    assert sim.errors == []
