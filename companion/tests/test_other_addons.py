"""What ForeverAI reads from three other add-ons of the player: GearQuest Forever (asked in the game), ForeverMapNotes
and Tamed (their data files, read by the app: their tables are private to them in the game)."""

import pytest

from foreverai import luadata, ways
from foreverai.mapnotes import MapNotes, areas, format_map_notes, unpack
from foreverai.petskills import PetSkills, format_pet_abilities
from foreverai.tools import format_gear_upgrades

# ------------------------------------------------------------------------------------------- Lua data files


def test_lua_tables_are_read_as_lists_and_dicts():
    text = '''
    -- a comment with a { brace
    local _, ns = ...
    ns.Other = { 1, 2 }
    ns.RareData = {
      [61] = { names = { "Thuros", "Turos M\\195\\163o" }, lvl = { 11, 11 }, elite = false, spawns = { [1429] = "286599292580", }, drops = { { 6203, 70.00 }, } }, -- end
      [79] = { names = { 'Narg "the" Taskmaster' }, note = L["Translated"], nothing = nil, hex = 0x10, neg = -2.5, },
    }
    '''
    data = luadata.assigned(text, "RareData")
    assert data[61] == {"names": ["Thuros", "Turos Mão"], "lvl": [11, 11], "elite": False,
                        "spawns": {1429: "286599292580"}, "drops": [[6203, 70.0]]}
    assert data[79] == {"names": ['Narg "the" Taskmaster'], "note": "Translated", "nothing": None, "hex": 16, "neg": -2.5}
    assert luadata.assigned(text, "Other") == [1, 2]
    assert luadata.assigned(text, "Missing") is None
    assert luadata.passed('Addon:MergeNPCs({ ["30"] = { ["coords"] = { {28.5, 67.0} } } })', "MergeNPCs") == {
        "30": {"coords": [[28.5, 67.0]]}}
    with pytest.raises(luadata.LuaError):
        luadata.assigned("ns.Broken = { 1, 2", "Broken")


def test_packed_coordinates_and_where_spots_are_dense():
    assert unpack("286599292580") == [(28.6, 59.9), (29.2, 58.0)]
    assert unpack("28659") == [] and unpack(None) == []
    spots = [(10.0, 10.0), (10.5, 11.0), (11.0, 10.5), (60.0, 60.0)]
    assert [(round(x, 1), round(y, 1), n) for x, y, n in areas(spots)] == [(10.5, 10.5, 3), (60.0, 60.0, 1)]


# ------------------------------------------------------------------------------------------- ForeverMapNotes

@pytest.fixture
def addons(tmp_path):
    data = tmp_path / "ForeverMapNotes" / "Data"
    data.mkdir(parents=True)
    (data / "Rares.lua").write_text('''local _, ns = ...
ns.RareData = {
  [462] = { names = { "Vultros" }, lvl = { 26, 26 }, elite = false, spawns = { [1436] = "368625406605", }, drops = { { 4454, 25.00 }, { 999, 5 }, } },
  [519] = { names = { "Slark" }, lvl = { 15, 15 }, elite = true, spawns = { [1436] = "470109", }, drops = { } },
}
ns.ItemData = {
  [4454] = { 2, 15, 13, -1, 3, 21, "Talon of Vultros" },
}
''', encoding="utf-8")
    (data / "Nodes.lua").write_text('''local _, ns = ...
ns.NodeData = {
  [1436] = { copper = "450200452203455198700800", chest = "300300", peacebloom = "500500", },
  [1429] = { copper = "100100", },
}
''', encoding="utf-8")
    (data / "Fishing.lua").write_text('local _, ns = ...\nns.FishData = { [1436] = { firefin = "250600", }, }\n', encoding="utf-8")
    (data / "NPCs.lua").write_text('''local _, ns = ...
ns.NPCData = {
  [1436] = {
    { "classtrainer", "A", "530530", "A Hunter Teacher", "HUNTER" },
    { "classtrainer", "A", "531530", "A Mage Teacher", "MAGE" },
    { "inn", "A", "527537", "Innkeeper Heather" },
    { "inn", "H", "100100", "Horde Innkeeper" },
    { "mailbox", "AH", "528536" },
  },
  [1453] = {
    { "inn", "A", "526657", "Innkeeper Allison" },
    { "auction", "A", "536605", "Auctioneer Fitch" },
    { "bank", "A", "577728", "Olivia Burnside" },
  },
  [1439] = {
    { "inn", "A", "100100", "Innkeeper Shaussiy" }, { "repair", "A", "110100", "Mavralyn" }, { "stable", "A", "120100", "Jaelysia" },
  },
}
''', encoding="utf-8")
    (data / "Books.lua").write_text('''local _, ns = ...
ns.BookData = {
  librarians = { { faction = "A", map = 1453, p = "376808", name = "Garion Wendell" }, { faction = "H", map = 1458, p = "734330", name = "Owen Thadd" }, },
  books = {
    { key = "a", name = "Bewitchments and Glamours", spots = { { 1436, "454704" } }, note = { "On a shelf in Moonbrook.", "pt", "es" } },
    { key = "b", name = "The Lessons of Ta'zo", faction = "H", spots = { { 1454, "387784" } }, note = { "Horde only." } },
  },
}
''', encoding="utf-8")
    (data / "Transport.lua").write_text('''local _, ns = ...
ns.TransportData = {
  { "boat", "A", 65, { 1437, "045567", "Menethil Harbor" }, { 1439, "323441", "Auberdine" } },
  { "zeppelin", "H", 95, { 1411, "510139", "Orgrimmar" }, { 1420, "606589", "Undercity" } },
}
''', encoding="utf-8")
    (data / "Flight.lua").write_text('''local _, ns = ...
ns.FlightData = {
  { 523, 1436, "566526", "A", { "Thor", "Thor", "Thor" } },
  { 1387, 1434, "325294", "H", { "Thysta" } },
}
''', encoding="utf-8")
    (data / "ZoneLevels.lua").write_text('local _, ns = ...\nns.ZoneLevels = {\n\t[1436] = { 9, 20 }, -- 35 quests\n\t[1429] = { 1, 11 },\n}\n', encoding="utf-8")
    tamed = tmp_path / "Tamed" / "src" / "data"
    tamed.mkdir(parents=True)
    (tamed / "TameableAbilities-Vanilla.lua").write_text('''local _, Addon = ...
Addon:MergeTameableAbilities({
  ["bite"] = { ["name"] = "Bite", ["learned_by"] = { "Carrion Bird", "Wolf" }, ["ranks"] = {
      { ["rank"] = 1, ["spell_id"] = 17253, ["pet_level"] = 1, ["training_cost"] = 1 },
      { ["rank"] = 2, ["spell_id"] = 17255, ["pet_level"] = 8, ["training_cost"] = 4 },
      { ["rank"] = 3, ["spell_id"] = 17256, ["pet_level"] = 16, ["training_cost"] = 7 },
      { ["rank"] = 4, ["spell_id"] = 17257, ["pet_level"] = 24, ["training_cost"] = 10 } } },
  ["screech"] = { ["name"] = "Screech", ["learned_by"] = { "Carrion Bird" }, ["ranks"] = {
      { ["rank"] = 1, ["spell_id"] = 24423, ["pet_level"] = 8, ["training_cost"] = 10 } } },
  ["prowl"] = { ["name"] = "Prowl", ["learned_by"] = { "Cat" }, ["ranks"] = {
      { ["rank"] = 1, ["spell_id"] = 24450, ["pet_level"] = 30, ["training_cost"] = 15 } } },
})
''', encoding="utf-8")
    (tamed / "TameableNPCs-Vanilla.lua").write_text('''local _, Addon = ...
Addon:MergeTameableNPCs({
  ["1"] = { ["name"] = "Wood Lurker", ["family"] = "Spider", ["diet"] = { "Meat" }, ["level_range"] = "17-18", ["zone_id"] = 38,
            ["ui_map_id"] = 1432, ["abilities"] = { { ["key"] = "bite", ["rank"] = 3 } }, ["coords"] = { {60.0, 30.0}, {65.0, 30.0}, {70.0, 30.0} } },
  ["2"] = { ["name"] = "Coyote Packleader", ["family"] = "Wolf", ["diet"] = { "Meat" }, ["level_range"] = "11-12", ["zone_id"] = 40,
            ["ui_map_id"] = 1436, ["abilities"] = { { ["key"] = "bite", ["rank"] = 3 } }, ["coords"] = { {45.0, 40.0} } },
  ["3"] = { ["name"] = "Old Grizzlegut", ["family"] = "Wolf", ["diet"] = { "Meat" }, ["level_range"] = "17", ["classification"] = "Rare",
            ["zone_id"] = 40, ["ui_map_id"] = 1436, ["abilities"] = { { ["key"] = "bite", ["rank"] = 3 } }, ["coords"] = { {40.0, 40.0} } },
  ["4"] = { ["name"] = "Mist Howler", ["family"] = "Wolf", ["diet"] = { "Meat" }, ["level_range"] = "22-23", ["zone_id"] = 331,
            ["ui_map_id"] = 1440, ["abilities"] = { { ["key"] = "bite", ["rank"] = 3 } }, ["coords"] = { {40.0, 40.0} } },
  ["5"] = { ["name"] = "Greater Fleshripper", ["family"] = "Carrion Bird", ["diet"] = { "Meat", "Fish" }, ["level_range"] = "16-17",
            ["zone_id"] = 40, ["ui_map_id"] = 1436, ["abilities"] = { { ["key"] = "bite", ["rank"] = 2 } }, ["coords"] = { {59.6, 57.0} } },
})
''', encoding="utf-8")
    # the Forever file is a patch over the Vanilla one: its fields win
    (tamed / "TameableNPCs-Forever.lua").write_text('''local _, Addon = ...
if not Addon.IS_FOREVER then return end
Addon:MergeTameableNPCs({ ["2"] = { ["level_range"] = "15-16" } })
''', encoding="utf-8")
    (tamed / "TameableAbilities-Forever.lua").write_text("local _, Addon = ...\nAddon:MergeTameableAbilities({})\n", encoding="utf-8")
    return tmp_path


SNAP = {"player": {"map": "Westfall", "zone": "Westfall", "coords": "52.9, 53.7", "faction": "Alliance", "class": "Hunter",
                   "level": 18},
        "pet": {"family": "Carrion Bird", "level": 17,
                "abilities": ["Bite (Rank 2): 35 Focus | Bite the enemy.", "Demoralizing Screech (Rank 1): 20 Focus | Blasts.",
                              "Growl (Rank 2): 15 Focus"]}}


def test_map_notes_of_a_zone(addons):
    notes = MapNotes(addons)
    text, failed = format_map_notes(notes, "", "", SNAP)
    assert not failed
    assert text.startswith("Westfall (quests level 9-20), from the player's ForeverMapNotes add-on")
    # rares, the lowest first, with what they drop by chance; an item the add-on has no row for keeps its number
    assert ("- Slark (level 15, elite, rare) — Westfall 47.0, 10.9\n"
            "- Vultros (level 26, rare) — Westfall 36.8, 62.5; 40.6, 60.5; drops: [Talon of Vultros] (rare, dagger, "
            "item level 21) 25%, item 999 5%") in text
    assert "- copper: 4 spawn spots in Westfall; the most around 45.2, 20.0 (3 close together); 70.0, 80.0" in text
    assert "- peacebloom: 1 spawn spots in Westfall" in text and "- firefin: 1 spawn spots in Westfall" in text
    # town NPCs: the player's faction and class only, a mailbox has no name
    assert "- hunter trainer: A Hunter Teacher Westfall 53.0, 53.0" in text and "Mage Teacher" not in text
    assert "- innkeeper: Innkeeper Heather Westfall 52.7, 53.7" in text and "Horde Innkeeper" not in text
    assert "- mailbox: Westfall 52.8, 53.6" in text
    assert "- [Bewitchments and Glamours] — Westfall 45.4, 70.4: On a shelf in Moonbrook." in text and "Ta'zo" not in text
    assert "- Library books are turned in to Garion Wendell — Stormwind City 37.6, 80.8" in text
    assert "- flight master Thor — Westfall 56.6, 52.6" in text


def test_map_notes_one_kind_and_the_best_zones(addons):
    notes = MapNotes(addons)
    assert format_map_notes(notes, "westfall", "ore", SNAP)[0].count("\n- ") == 1  # copper only, no herbs or chests
    text, _ = format_map_notes(notes, "", "copper", SNAP)
    assert text.splitlines()[1].startswith("- Westfall (quests level 9-20): 4 spawn spots of copper")
    assert text.splitlines()[2].startswith("- Elwynn Forest (quests level 1-11): 1 spawn spots of copper")
    assert "boat from Auberdine (Darkshore 32.3, 44.1) to Menethil Harbor, Wetlands (65 s trip)" in format_map_notes(notes, "Darkshore", "travel", SNAP)[0]
    assert format_map_notes(notes, "Nowhere", "", SNAP) == ("No map is called 'Nowhere'. Give the English name of a zone or city.", True)
    assert "nothing listed for rares" in format_map_notes(notes, "Elwynn Forest", "rares", SNAP)[0]


def test_town_npcs_of_stormwind_are_moved_onto_this_servers_map(addons):
    """The add-on's NPC list is on the old map of Stormwind. In the game: Innkeeper Allison stands at 60.4, 75.3 where
    the add-on says 52.6, 65.7; Fitch at 61.2, 71.3 (53.6, 60.5); Olivia Burnside at 64.3, 80.8 (57.7, 72.8)."""
    text, _ = format_map_notes(MapNotes(addons), "Stormwind City", "npcs", SNAP)
    assert "Innkeeper Allison Stormwind City 60.4, 75.3" in text
    assert "Auctioneer Fitch Stormwind City 61.2, 71.3" in text
    assert "Olivia Burnside Stormwind City 64.3, 80.8" in text
    assert ways.places_on_this_map("Carla Granger — Stormwind City 54.7, 56.0; Elwynn Forest 54.7, 56.0") == (
        "Carla Granger — Stormwind City 62.0, 67.8; Elwynn Forest 54.7, 56.0")


def test_town_npcs_that_stand_elsewhere_in_the_game_are_not_given(addons):
    # a map nobody has fitted: the game data of the question says where three of them really are
    snap = dict(SNAP, player=dict(SNAP["player"], map="Darkshore", zone="Darkshore", coords="37.0, 44.0"),
                services=["innkeeper: Innkeeper Shaussiy <Innkeeper> — Darkshore 37.0, 44.1 (10 m)",
                          "repair: Mavralyn <Leather Armor & Leatherworking Supplies> — Darkshore 37.0, 41.2 (90 m)"],
                zoneNPCs=["Jaelysia <Stable Master> — Darkshore 37.4, 44.3 (20 m)"])
    text, _ = format_map_notes(MapNotes(addons), "", "npcs", snap)
    assert "Innkeeper Shaussiy" not in text
    assert "its positions are not given. Use the services and zoneNPCs sections of the game data." in text


def test_map_notes_without_the_addon(tmp_path):
    text, failed = format_map_notes(MapNotes(tmp_path), "", "", SNAP)
    assert failed and "does not have the ForeverMapNotes add-on" in text


def test_the_route_planner_gets_zone_levels_and_boats(addons):
    notes = MapNotes(addons)
    assert notes.zone_levels() == {"Westfall": (9, 20), "Elwynn Forest": (1, 11)}
    assert notes.transports("A") == [{"kind": "boat", "seconds": 65, "a": ("Wetlands", (4.5, 56.7), "Menethil Harbor"),
                                      "b": ("Darkshore", (32.3, 44.1), "Auberdine")}]
    assert [t["kind"] for t in notes.transports("H")] == ["zeppelin"]
    assert notes.flight_masters("A") == [{"name": "Thor", "zone": "Westfall", "x": 56.6, "y": 52.6}]


# ------------------------------------------------------------------------------------------- Tamed

def test_pet_abilities_for_the_pet_the_hunter_has(addons):
    text, failed = format_pet_abilities(PetSkills(addons), "", "", SNAP)
    assert not failed
    assert "hunter level 18, the pet is a level 17 Carrion Bird" in text
    lines = text.splitlines()
    bite = lines.index("- Bite: the pet has rank 2")
    assert lines[bite + 1] == "    rank 3 is open now (pet level 16, 7 training points). Beasts that know it:"
    # beasts the hunter can tame (not the level 22 one); the rare one last; the Forever file moved the coyote to 15-16
    assert lines[bite + 2:bite + 5] == [
        "      Wood Lurker (Spider, level 17-18) — Loch Modan 65.0, 30.0, 3 spawn points",
        "      Coyote Packleader (Wolf, level 15-16) — Westfall 45.0, 40.0",
        "      Old Grizzlegut (Wolf, level 17, rare) — Westfall 40.0, 40.0"]
    assert lines[bite + 5] == "    next: rank 4 at pet level 24"
    # the client calls Screech "Demoralizing Screech": the pet has it
    assert "- Screech: the pet has rank 1" in lines and "    that is the highest rank the pet's level allows" in lines
    assert "Prowl" not in text  # a Carrion Bird cannot learn it
    assert lines[-2] == "Carrion Bird beasts the hunter can tame near this level (they eat Meat, Fish):"
    assert lines[-1] == "- Greater Fleshripper (Carrion Bird, level 16-17) — Westfall 59.6, 57.0"


def test_pet_abilities_orders_beasts_by_the_way_there(addons):
    class Near:
        def route(self, a, a_xy, b, b_xy):
            return (0.0 if b == a else 4000.0), []
    text, _ = format_pet_abilities(PetSkills(addons), "", "bite", SNAP, Near())
    lines = text.splitlines()
    first = lines.index("    rank 3 is open now (pet level 16, 7 training points). Beasts that know it:") + 1
    assert lines[first] == "      Coyote Packleader (Wolf, level 15-16) — Westfall 45.0, 40.0 (in the player's zone)"
    assert lines[first + 1].endswith("3 spawn points (about 4000 m of travel)")


def test_pet_abilities_one_ability_another_family_and_wrong_names(addons):
    skills = PetSkills(addons)
    assert "- Prowl — a Carrion Bird cannot learn it (families: Cat)" in format_pet_abilities(skills, "", "prowl", SNAP)[0]
    text, _ = format_pet_abilities(skills, "wolf", "", SNAP)
    assert "- Bite: the pet has rank 2" in text and "Screech" not in text
    assert "Wolf beasts the hunter can tame near this level (they eat Meat):" in text
    assert format_pet_abilities(skills, "Dragon", "", SNAP) == (
        "No pet family is called 'Dragon'. Families: Carrion Bird, Cat, Spider, Wolf.", True)
    assert format_pet_abilities(skills, "", "fireball", SNAP) == (
        "No pet ability is called 'fireball'. Abilities: Bite, Prowl, Screech.", True)
    assert format_pet_abilities(PetSkills(addons / "none"), "", "", SNAP)[1] is True


# ------------------------------------------------------------------------------------------- GearQuest Forever

def test_gear_upgrades_text_for_the_model():
    res = {"level": 18, "class": "HUNTER", "spec": "Beast Mastery", "slots": [
        {"slot": "Legs", "worn": "[Cured Leather Pants]", "picks": [
            {"rank": 1, "id": 6282, "name": "Smelting Pants", "source": "Quest reward", "quest": "The Lost Tools",
             "zone": "Loch Modan", "how": "Reward from The Lost Tools.", "where": "Wilder Thistlenettle — Stormwind City 54.7, 56.0",
             "gain": 4.5},
            {"rank": 2, "id": 237, "name": "Cured Leather Pants", "source": "Vendor", "worn": True}]},
        {"slot": "Neck", "picks": []}],
        "waiting": ["Ranged"], "tracked": ["Smelting Pants"]}
    text = format_gear_upgrades(res)
    assert text.splitlines()[0].startswith("Gear to go for, from the player's GearQuest Forever add-on (Beast Mastery Hunter, level 18)")
    assert "Legs: wearing [Cured Leather Pants]" in text
    # its Stormwind coordinates are on the old map of the city: given on this server's
    assert ("  1. [Smelting Pants] (Quest reward, quest [The Lost Tools], Loch Modan, gear score +4.5 over what is worn): "
            "Reward from The Lost Tools. Where: Wilder Thistlenettle — Stormwind City 62.0, 67.8.") in text
    assert "  2. [Cured Leather Pants] — WORN NOW (Vendor)" in text
    assert "Neck: wearing nothing\n  nothing listed for this level" in text
    assert "Not ranked yet (GearQuest is still building them; ask again in a moment): Ranged." in text
    assert "The player tracks in GearQuest: [Smelting Pants]." in text
    assert "could not be read" in format_gear_upgrades(None)
    assert "GearQuest Forever is not loaded" in format_gear_upgrades({"error": "GearQuest Forever is not loaded"})


def test_the_addon_reads_gearquest_in_the_game(tmp_path):
    pytest.importorskip("lupa")
    from addonsim import Sim, install_game
    install_game(tmp_path / "game", pool=40)
    sim = Sim(tmp_path / "game").start()
    try:
        ask = """
          local r = ForeverAI.Gear.Upgrades(%s)
          if r.error then return "error: " .. r.error end
          local out = { tostring(r.level), tostring(r.class), tostring(r.spec) }
          for _, s in ipairs(r.slots) do
            out[#out + 1] = s.slot .. "=" .. tostring(s.worn)
            for _, p in ipairs(s.picks) do
              out[#out + 1] = table.concat({ p.rank, p.name, tostring(p.source), tostring(p.quest), tostring(p.how),
                                             tostring(p.where), tostring(p.have), tostring(p.worn), tostring(p.gain) }, "|")
            end
          end
          out[#out + 1] = "waiting:" .. table.concat(r.waiting or {}, ",")
          out[#out + 1] = "tracked:" .. table.concat(r.tracked or {}, ",")
          return table.concat(out, "\\n")
        """
        assert sim.lua(ask % "nil").decode() == "error: GearQuest Forever is not loaded"
        sim.lua("""
          local pants = { id = "gen:hunter:6282", itemId = 6282, sourceType = "quest_reward", questName = "The Lost Tools",
                          instructions = "Reward from |cffffd200The Lost Tools|r.", zone = "Loch Modan", minLevel = 15, maxLevel = 19 }
          local worn = { id = "gen:hunter:237", itemId = 237, sourceType = "vendor", instructions = "Bought from Lara Moore." }
          local bow = { id = "gen:hunter:3039", itemId = 3039, sourceType = "world_drop" }
          local names = { [6282] = "Smelting Pants", [237] = "Cured Leather Pants", [3039] = "Short Ash Bow" }
          local built = {}
          GearQuest = {
            GetEffectiveLevel = function() return 18 end, GetEffectiveClass = function() return "HUNTER" end,
            GetEffectiveSpec = function() return "Beast Mastery" end,
            GetSourceLabel = function(self, kind) return ({ quest_reward = "Quest reward", vendor = "Vendor" })[kind] end,
            Log = { IsEntryObtained = function(self, id) return id == "gen:hunter:237" end },
            Tracker = { GetTrackedEntries = function() return { pants } end },
            Data = {
              BASE_SLOTS = { "Head", "Legs" },
              WarmSlotNames = function() return { "Legs", "Neck", "Ranged" } end,
              PeekTopUpgradesForSlot = function(self, slot) return slot == "Legs" and { pants, worn } or nil end,
              GetTopUpgradesForSlot = function(self, slot)
                built[#built + 1] = slot
                if slot == "Neck" then error("broken slot") end
                return { bow }
              end,
              GetInventorySlots = function(self, slot) return slot == "Legs" and { 7 } or {} end,
              GetEntryDisplayName = function(self, e) return names[e.itemId] end,
              GetEntryInstructions = function(self, e) return e.instructions end,
              NearestCoordinateSpot = function(self, id)
                if id == 6282 then return { map = "Loch Modan", x = 37.2, y = 46.94, npc = "Daryl the Youngling" } end
              end,
              GearScoreUpgradeDelta = function(self, e) if e == worn then return 0 end if e == pants then return 4.46, true end return 7, false end,
            },
          }
          GearQuestBuilt = built
          GetInventoryItemLink = function(unit, slot)
            return slot == 7 and "|cffffffff|Hitem:237::::::::18:::::|h[Cured Leather Pants]|h|r" or nil
          end
        """)
        assert sim.lua(ask % "nil").decode().split("\n") == [
            "18", "HUNTER", "Beast Mastery",
            "Legs=[Cured Leather Pants]",
            "1|Smelting Pants|Quest reward|The Lost Tools|Reward from The Lost Tools.|Daryl the Youngling — Loch Modan 37.2, 46.9|nil|nil|4.5",
            "2|Cured Leather Pants|Vendor|nil|Bought from Lara Moore.|nil|true|true|nil",
            # a slot GearQuest has not ranked yet is built on request; one that fails is left out, not an error
            "Ranged=nil", "1|Short Ash Bow|world_drop|nil|nil|nil|nil|nil|nil",
            "waiting:", "tracked:Smelting Pants"]
        # one slot, by the name a player uses
        assert sim.lua(ask % '"pants"').decode() == "error: GearQuest has no slot called pants"
        assert sim.lua(ask % '"legs"').decode().split("\n")[3] == "Legs=[Cured Leather Pants]"
        assert "Ranged=nil" not in sim.lua(ask % '"legs"').decode()
    finally:
        sim.close()


def test_town_npcs_of_a_kind_and_the_nearest_to_a_place(addons):
    """A stop for a kind of NPC on a route takes every one of that kind (on this server's map); map_notes lists the
    nearest to a place first when one is given."""
    notes = MapNotes(addons)
    assert notes.npc_spots("Stormwind City", "A", "bank") == [("Olivia Burnside", 64.3, 80.8)]
    assert notes.npc_spots("Stormwind City", "A", "Auction House") == [("Auctioneer Fitch", 61.2, 71.3)]
    assert notes.npc_spots("Stormwind City", "H", "bank") == [] and notes.npc_spots("Darnassus", "A", "bank") == []
    text, _ = format_map_notes(notes, "Stormwind City", "npcs", SNAP, near_xy=(61.0, 71.0))
    assert "auction house: Auctioneer Fitch Stormwind City 61.2, 71.3" in text
