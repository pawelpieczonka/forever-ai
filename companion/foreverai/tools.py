"""Function tools offered to the model and their execution."""

from __future__ import annotations

import itertools
import json
import re
from typing import Any

from .kb.store import KB
from .ways import places_on_this_map

ALLOWED_OPS = ("set_option", "set_setting", "toggle_addon", "slash_command")

# auction house filters -> the game's numeric item classes (Enum.ItemClass), subclasses and equip slots
AH_SLOTS = {
    "head": ["INVTYPE_HEAD"], "neck": ["INVTYPE_NECK"], "shoulder": ["INVTYPE_SHOULDER"], "back": ["INVTYPE_CLOAK"],
    "chest": ["INVTYPE_CHEST", "INVTYPE_ROBE"], "wrist": ["INVTYPE_WRIST"], "hands": ["INVTYPE_HAND"],
    "waist": ["INVTYPE_WAIST"], "legs": ["INVTYPE_LEGS"], "feet": ["INVTYPE_FEET"], "finger": ["INVTYPE_FINGER"],
    "trinket": ["INVTYPE_TRINKET"],
    "one-hand": ["INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND"],  # fits the main hand, wielded with one hand
    "main-hand": ["INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND"],
    "off-hand": ["INVTYPE_WEAPON", "INVTYPE_WEAPONOFFHAND", "INVTYPE_SHIELD", "INVTYPE_HOLDABLE"],
    "two-hand": ["INVTYPE_2HWEAPON"], "shield": ["INVTYPE_SHIELD"], "held-in-off-hand": ["INVTYPE_HOLDABLE"],
    "ranged": ["INVTYPE_RANGED", "INVTYPE_RANGEDRIGHT", "INVTYPE_THROWN", "INVTYPE_RELIC"],
    "bag": ["INVTYPE_BAG"], "any": [],
}
AH_WEAPONS = {
    "1h_sword": [7], "2h_sword": [8], "sword": [7, 8], "1h_axe": [0], "2h_axe": [1], "axe": [0, 1],
    "1h_mace": [4], "2h_mace": [5], "mace": [4, 5], "dagger": [15], "fist": [13], "staff": [10], "polearm": [6],
    "bow": [2], "gun": [3], "crossbow": [18], "wand": [19], "thrown": [16], "fishing_pole": [20],
}
AH_ARMOR = {"cloth": [1], "leather": [2], "mail": [3], "plate": [4], "shield": [6], "misc": [0], "relic": [7, 8, 9, 11]}
AH_CATEGORIES = {"weapon": 2, "armor": 4, "consumable": 0, "container": 1, "gem": 3, "reagent": 5, "projectile": 6,
                 "trade_goods": 7, "enhancement": 8, "recipe": 9, "quiver": 11, "quest": 12, "misc": 15, "any": -1}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the local WoW Forever knowledge base (the Forever pages of warcraft.wiki.gg, official "
                           "Blizzard news, hotfixes and developer notes). Use short English keywords "
                           "(item, quest, NPC, zone, class or system names). Returns the best matching pages in full, "
                           "with URL and date. Content Forever did not change is usually not in it.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_addon_change",
            "description": "Propose a change to another installed addon. Nothing is applied until the player clicks "
                           "Accept in the game (with Undo). FIRST find the setting with addon_options. "
                           "op=set_option (preferred): set an option found by addon_options — path exactly as returned, "
                           "value of the option's type (toggle: true/false, range: number in min..max, select: one of "
                           "the listed value KEYS). op=set_setting: raw saved variable from addon_options.savedVariables "
                           "(only for addons without an options tree). op=toggle_addon: enable/disable an addon "
                           "(value true/false). op=slash_command: run an addon command that performs an action.",
            "parameters": {
                "type": "object",
                "properties": {
                    "addon": {"type": "string", "description": "Addon folder name"},
                    "op": {"type": "string", "enum": list(ALLOWED_OPS)},
                    "path": {"type": "string", "description": "For set_option/set_setting: path exactly as returned "
                                                              "by addon_options"},
                    "value": {"description": "New value (boolean, number or string)"},
                    "command": {"type": "string", "description": "For slash_command"},
                    "description": {"type": "string", "description": "In the player's language: what the change does and why"},
                    "needs_reload": {"type": "boolean"},
                },
                "required": ["addon", "op", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a LASTING, IMPORTANT fact about the player for future sessions: chosen build/spec (briefly: "
                           "class/spec, key talents, stat priority, source URL), long-term goal, stable preference "
                           "(e.g. answer style), main professions. Do NOT save: one-off questions, momentary state "
                           "(level, gold, location, current quest — that is in the game data), things already saved. "
                           "Most questions need no save. In the player's language, max ~300 characters.",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "Delete an entry from the player's memory by id (ids are listed under \"Memory about the player\"). "
                           "Use when an entry is outdated or contradicts a new one (the player changed build/goal, reached "
                           "a goal, asks to remove it), or is trivia. To update an entry: delete the old one and save a new one.",
            "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a web page the player gave (e.g. a build, guide, talent calculator, news). Returns the "
                           "page text (no scripts). Use for links in the player's message or from search results.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
]

TOOLS.append({
    "type": "function",
    "function": {
        "name": "mark_on_map",
        "description": "Mark a point on the player's map (waypoint with arrow). Use when the question is about a place: "
                       "quest turn-in NPC, mobs, herbs, dungeon entrance, trainer. Coordinates 0-100 as in the game "
                       "(from the quests/zoneNPCs/nearbyMobs data or the wiki). zone = English zone name; "
                       "omit for the player's current zone. Can be called several times (up to 12 points). "
                       "The marks of one answer replace the marks of your earlier answers; add=true keeps those and "
                       "adds this point (an extra stop on a route that still holds); the add-on puts an added "
                       "stop where it costs the least way on the route, and the arrow goes to it when it comes first.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short name of the point"},
                "x": {"type": "number"}, "y": {"type": "number"},
                "zone": {"type": "string"},
                "add": {"type": "boolean", "description": "true: keep the marks of earlier answers and add this one"},
                "quest": {"type": "string", "description": "The quest this stop is for (its title), with task: the "
                                                            "stop then stays on the route until that is done, "
                                                            "instead of leaving when the player reaches the place"},
                "note": {"type": "string", "description": "What to do there in a few words, in English like the "
                                                           "add-on's window: shown in "
                                                           "brackets after the stop in the player's route list. Leave "
                                                           "it out when quest and task say it: the list then says "
                                                           "'turn in …' or 'pick up …' by itself"},
                "task": {"type": "string", "enum": ["objective", "turn_in", "pick_up"],
                         "description": "What the player does there for that quest: an objective of it, turning it "
                                        "in, or picking it up"},
                "mob": {"type": "string", "description": "When the mark is where a creature is to be found: its exact "
                                                          "name. The game then watches for it: a living one in sight is "
                                                          "marked on the screen and announced with a sound, and the "
                                                          "tracked section tells you whether one is there"},
            },
            "required": ["title", "x", "y"],
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "edit_route",
        "description": "Change the route that is on the player's map (the marks section) without marking it again: "
                       "take stops off, put them in another order, clear it, or stop watching for a creature (the "
                       "tracked section). Name a stop by its title or by its number in the marks section. Put "
                       "everything into one call. The player sees the change on the map and in the add-on's window at "
                       "once. To add a stop, call mark_on_map with add=true; for a whole new route, plan_route.",
        "parameters": {
            "type": "object",
            "properties": {
                "remove": {"type": "array", "items": {"type": "string"}, "description": "Stops to take off the route"},
                "order": {"type": "array", "items": {"type": "string"},
                          "description": "Stops in the order to walk them; the stops you do not name follow in the "
                                         "order they had"},
                "clear": {"type": "boolean", "description": "true: take every stop off the map"},
                "propose": {"type": "boolean",
                            "description": "true when the player did not ask for this change and you think the route "
                                           "no longer fits: the player gets it as a card to accept, and the route "
                                           "stays as it is until then"},
                "go_to": {"type": "string",
                          "description": "The stop the player heads to now (its title or number; also a stop marked in "
                                         "this answer): the arrow leads there. Otherwise it leads to the first stop "
                                         "that is not done"},
                "stop_tracking": {"type": "array", "items": {"type": "string"},
                                  "description": "Creatures to stop watching for, by the name that starts their line "
                                                 "in the tracked section; [\"all\"] for every one. A stop taken off "
                                                 "the route takes the watch for its creature with it"},
            },
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "plan_route",
        "description": "The route through the player's quest log: every objective and turn-in with a known place, in "
                       "the order with the shortest way, each turn-in after its own objectives, with the distances, "
                       "and what is left out and why (another zone, needs a skill, no known place). It covers the "
                       "player's zone and, with then, the zones the route goes on into, each through the way the "
                       "player really takes there (a portal, a boat, a flight, a road), which becomes a stop of its "
                       "own. It also marks the stops on the map in that order (up to 12), replacing earlier marks. "
                       "Use it whenever the player asks for a route, a path, an order for their quests or \"what is "
                       "the best way to do these\"; do not order the stops yourself, and do not set the stops of "
                       "another zone by hand. You decide what the route holds: read the quests first (text, level "
                       "against the player's, elite or group, escort, what you advised earlier) and say so with "
                       "skip, first, last, use, extra, avoid, new_quests, then and end.",
        "parameters": {
            "type": "object",
            "properties": {
                "use": {"type": "array", "items": {"type": "string"},
                        "description": "Where an objective has more than one source in whereToGet: the one to get it "
                                       "from (the creature, object or vendor, by name), when it is surer or quicker "
                                       "for this character than the nearest, which is taken otherwise"},
                "skip": {"type": "array", "items": {"type": "string"},
                         "description": "Quest titles to leave out: quests the player cannot or should not do now "
                                        "(an elite or group quest alone, far above their level, one you advised against)"},
                "first": {"type": "array", "items": {"type": "string"},
                          "description": "Quest titles to do before everything else in their zone, also when that "
                                         "makes the way longer: what the player gains most from having early (a class "
                                         "quest that gives an ability or a pet skill, a quest that opens others, one "
                                         "with a time limit)"},
                "last": {"type": "array", "items": {"type": "string"},
                         "description": "Quest titles to do after everything else (the hard ones: by then the player "
                                        "may have a level more or company)"},
                "then": {"type": "array",
                         "description": "The zones the route goes on into after the player's zone, when quests or "
                                        "stops the player asked for lead there: only the zones with something to do, "
                                        "in the order the player goes when they gave one (a zone can come again after "
                                        "another, the player's own too). The app finds the way to each (the road across the border, "
                                        "a boat, the tram, a portal, a flight the character has, through the zones "
                                        "between) and puts them in the order that costs the least way, unless yours "
                                        "is about as short. The player's own zone may come again after another one, when the "
                                        "rest of it cannot be reached on foot from where they stand (a village "
                                        "behind a portal, an island): then only what lies on the way out is done "
                                        "first, and the rest when the route is back",
                         "items": {"type": "object", "properties": {
                             "zone": {"type": "string", "description": "English name of the zone, as in the game "
                                                                       "data or as plan_route named it"},
                             "via_x": {"type": "number", "description": "The way out of the zone before this one, in "
                                                                        "THAT zone's coordinates: the portal, boat, "
                                                                        "flight master or road the player takes. "
                                                                        "Leave via and arrive out when plan_route "
                                                                        "said that it knows the way"},
                             "via_y": {"type": "number"},
                             "via_title": {"type": "string", "description": "What it is (the portal, boat, gate or road)"},
                             "arrive_x": {"type": "number", "description": "Where the player comes out in this zone, "
                                                                           "in this zone's coordinates"},
                             "arrive_y": {"type": "number"}}, "required": ["zone"]}},
                "extra": {"type": "array",
                          "description": "Stops that are not quests but belong on the route because the player asked "
                                         "for them or needs them (an NPC to deal with, a place, a creature). The app "
                                         "puts each where it costs the least way and keeps it on the route, through "
                                         "every update and every new plan, until the player has been there; one the "
                                         "player no longer wants is left out by its name in skip. Coordinates from the "
                                         "game data or the knowledge base, never a guess",
                          "items": {"type": "object", "properties": {
                              "title": {"type": "string", "description": "Who or what is there"},
                              "zone": {"type": "string", "description": "English name of the zone it is in"},
                              "x": {"type": "number"}, "y": {"type": "number"},
                              "note": {"type": "string", "description": "What to do there, in a few words"},
                              "mob": {"type": "string", "description": "The creature to watch for there, if it is one"},
                              "when": {"type": "string", "enum": ["first", "last"],
                                       "description": "Only when the player wants it so: first = before everything "
                                                      "else in its zone; last = the last stop of the whole route "
                                                      "(the route comes back to its zone for it)"}},
                              "required": ["title", "zone", "x", "y"]}},
                "avoid": {"type": "array", "items": {"type": "string"},
                          "description": "Ways the route must not take, when the player says one is not there or "
                                         "does not want it: the name of a flight master whose flight path the "
                                         "character does not have although the data says so, 'Tram', 'Boat', or a "
                                         "zone not to pass through"},
                "new_quests": {"type": "array", "items": {"type": "string"},
                               "description": "Titles of quests to pick up that plan_route would not take by "
                                              "itself: the ones the player asked for by name or kind, and a "
                                              "dungeon's quests. The quests that cost no way are taken along without "
                                              "being named here; 'take what you can on the way' is those, nothing more"},
                "propose": {"type": "boolean",
                            "description": "true when the player did not ask for a route and you think the one on the "
                                           "map no longer fits: the new route goes to the player as a card to accept, "
                                           "and the map stays as it is until then"},
                "end_x": {"type": "number", "description": "Where the player goes after these quests, in the "
                                                             "coordinates of the route's last zone (the road to the "
                                                             "next zone, a flight master), when that zone itself is "
                                                             "not on the route with then: the route ends near it"},
                "end_y": {"type": "number"},
                "end_title": {"type": "string", "description": "What is at end_x, end_y"},
                "mark": {"type": "boolean", "description": "false: only compute, do not mark (default true)"},
            },
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "addon_options",
        "description": "Search the settings of an installed add-on IN THE GAME (live): its real options tree (the same as "
                       "the add-on's options window) — name, description, type, CURRENT value and allowed values; for "
                       "add-ons without an options tree: saved variables. Also returns the add-on's slash commands. "
                       "ALWAYS use it when the player asks how to set/enable/disable something in an add-on or how a "
                       "feature works — then propose a concrete change (propose_addon_change, op=set_option) or explain "
                       "from the options. query = a few ENGLISH keywords of what the setting is about.",
        "parameters": {
            "type": "object",
            "properties": {
                "addon": {"type": "string", "description": "Add-on folder name from the addons list"},
                "query": {"type": "string", "description": "English keywords; empty = overview of the options"},
            },
            "required": ["addon"],
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "zone_quests",
        "description": "The quests this character can pick up in a zone, or that belong to a dungeon, read from the game "
                       "(its quest database knows what is done, what the class can take and where each chain stands): "
                       "title, level, who gives it and where, what it asks for, where its objectives are and where it "
                       "is turned in, plus the dungeon entrances of the zone. The nearbyQuests section only reaches "
                       "around the player: call this when they ask what there is to do somewhere, or for the quests "
                       "of a dungeon (they are given elsewhere), instead of searching the web for quest lists. Not "
                       "before plan_route for the zones of a route: plan_route reads those itself, takes along the "
                       "quests that cost no way and names the rest in its result.",
        "parameters": {
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": "English name of the zone or dungeon"},
            },
            "required": ["zone"],
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "gear_upgrades",
        "description": "The best items this character can go for now, slot by slot, from the player's GearQuest Forever "
                       "add-on (it ranks gear for the class, spec, level and faction on this server): what is worn, the "
                       "top picks, where each comes from (quest, vendor, creature, dungeon, profession) and where that "
                       "is. Call it when the player asks what gear to get, whether something is an upgrade worth a trip, "
                       "or what to do next for their equipment; then name the pick and its source, and offer the route. "
                       "Works only when the addons section lists GearQuestForever as loaded.",
        "parameters": {
            "type": "object",
            "properties": {
                "slot": {"type": "string", "description": "One slot (Head, Neck, Shoulder, Back, Chest, Wrist, Hands, "
                                                          "Waist, Legs, Feet, Finger, Trinket, MainHand, SecondaryHand, "
                                                          "Ranged); leave out for every slot"},
            },
        },
    },
})

TOOLS.append({
    "type": "function",
    "function": {
        "name": "map_notes",
        "description": "What the player's ForeverMapNotes add-on knows about a zone of this server: rare creatures with "
                       "their spawn spots and drops, ore veins, herbs and chests (how many spawn spots and where they "
                       "are densest), fishing pools, town NPCs (class and profession trainers, inn, bank, auction "
                       "house, mailbox, repair, stable), library books and where they are turned in, boats, zeppelins "
                       "and flight masters. With no zone and a node name (copper, peacebloom, firefin...) it names the "
                       "zones with the most of it. Call it for \"where do I farm, mine or pick X\", \"which rares are "
                       "here and what do they drop\", \"where is a mailbox or bank in X\", library books; do not search "
                       "the web for these. Works only when the addons section lists ForeverMapNotes.",
        "parameters": {
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": "English name of the zone or city; leave out for the "
                                                          "player's zone (or, with a node name in what, for the "
                                                          "best zones)"},
                "what": {"type": "string", "description": "rares, ore, herbs, chests, fishing, npcs, books, travel, or "
                                                          "one node or pool (copper, tin, peacebloom, firefin...); "
                                                          "leave out for everything in the zone"},
            },
        },
    },
})

TOOLS.append({
    "type": "function",
    "function": {
        "name": "pet_abilities",
        "description": "For a hunter: the abilities a pet learns from tamed beasts (Bite, Claw, Dash, Screech...), from "
                       "the player's Tamed add-on: which the pet's family can learn, the highest rank open at the "
                       "pet's level against the rank the pet has, and which beasts to tame to learn it, the easiest to "
                       "reach first, with coordinates. With a family it also lists beasts of that family to tame at "
                       "this level and what they eat. Call it for \"what can my pet learn\", \"where do I get Bite 3\", "
                       "\"which pet should I tame\"; do not answer these from memory. Works only when the addons "
                       "section lists Tamed.",
        "parameters": {
            "type": "object",
            "properties": {
                "family": {"type": "string", "description": "Pet family in English (Cat, Boar, Owl...); leave out for "
                                                            "the family of the pet the hunter has out"},
                "ability": {"type": "string", "description": "One ability in English (Bite, Claw...); leave out for "
                                                             "all the family can learn"},
            },
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "way_to",
        "description": "How to get from where the player stands to another zone, city or island: every step in order "
                       "(the road across a border, a boat or zeppelin and its pier, the tram, a portal, a flight between "
                       "flight masters the character knows), each with its coordinates and the walk to it. It comes "
                       "from the app's data of this server, whose boats and portals differ from the original game: "
                       "call it for \"how do I get to X\", \"which boat\", \"where is the portal\", and before you "
                       "describe any way between zones in words. For a route with quest stops use plan_route, which "
                       "puts the same steps between the stops.",
        "parameters": {
            "type": "object",
            "properties": {
                "zone": {"type": "string", "description": "English name of the zone, city or island to get to"},
                "x": {"type": "number", "description": "A place in that zone (map coordinates), when known"},
                "y": {"type": "number"},
                "avoid": {"type": "array", "items": {"type": "string"},
                          "description": "Ways not to take, as in plan_route (a flight master, 'Tram', a zone)"},
            },
            "required": ["zone"],
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "propose_auctions",
        "description": "While the auction house window is OPEN (windows.auctionHouse): propose posting items from the "
                       "bags. You name the items; the app looks each one up on the auction house right now and sets "
                       "the price by its own rules (the lowest price of the market less a copper when several "
                       "sellers confirm it; no price when there is one offer or none; nothing that a vendor pays "
                       "about as much for), then shows a card the player accepts item by item. This auction house "
                       "sells by the piece, at a price for one: there are no stack sizes to choose. Call it when the "
                       "player asks to put things on the auction house, in one call with all the items worth "
                       "selling there (trade goods, materials, unbound gear), not quest items or things they use. "
                       "Give price_copper only when the player named a price themselves.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "description": "Items from the bags section",
                          "items": {"type": "object", "properties": {
                              "item": {"type": "string", "description": "Item name or id from bags"},
                              "price_copper": {"type": "number", "description": "Price of ONE piece in copper, only "
                                                                                "when the player set it"},
                              "count": {"type": "integer", "description": "How many pieces, only when the player said "
                                                                        "a number; left out, all of them go (do not "
                                                                        "keep some back on your own)"}},
                              "required": ["item"]}},
                "hours": {"type": "number", "description": "How long, when the player said (the game takes the "
                                                           "nearest of its lengths); leave out otherwise"},
                "reason": {"type": "string", "description": "In the player's language, briefly"},
            },
            "required": ["items"],
        },
    },
})

TOOLS.append({
    "type": "function",
    "function": {
        "name": "my_auctions",
        "description": "The character's own auctions, read from the open auction house: each with a number, the item, "
                       "the buyout, a bid if there is one and the time left; sold ones are marked. Call it when the "
                       "player asks what they have up or wants one taken down.",
        "parameters": {"type": "object", "properties": {}},
    },
})

TOOLS.append({
    "type": "function",
    "function": {
        "name": "propose_auction_cancel",
        "description": "Propose taking down auctions of the character's own (auction house window open): by the numbers "
                       "my_auctions gave, or by item name for every auction of that item. The player confirms each on "
                       "a card; the deposit of a cancelled auction is lost. Use it when the player asks to cancel, or "
                       "to repost at another price (cancel first, then propose_auctions once the items are back in "
                       "the bags).",
        "parameters": {
            "type": "object",
            "properties": {
                "auctions": {"type": "array", "items": {"type": ["integer", "string"]},
                             "description": "Numbers from my_auctions, or item names"},
                "reason": {"type": "string", "description": "In the player's language, briefly"},
            },
            "required": ["auctions"],
        },
    },
})


def format_gear_upgrades(res: dict | None) -> str:
    """Game result of gear_upgrades -> text for the model."""
    if not res:
        return "The game did not respond in time; the gear list could not be read."
    if res.get("error"):
        return f"Reading the gear list failed: {res['error']}. Judge upgrades from the equipment and item stats in the game data."
    who = " ".join(str(x) for x in (res.get("spec"), str(res.get("class") or "").title()) if x)
    lines = [f"Gear to go for, from the player's GearQuest Forever add-on ({who or 'this character'}, level {res.get('level', '?')}); "
             "rank 1 is the best in the slot at this level:"]
    for slot in res.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        lines.append(f"{slot.get('slot')}: wearing {slot.get('worn') or 'nothing'}")
        for p in slot.get("picks") or []:
            if not isinstance(p, dict):
                continue
            state = " — WORN NOW" if p.get("worn") else " — already owned" if p.get("have") else ""
            gain = f", gear score {p['gain']:+g} over what is worn" if isinstance(p.get("gain"), (int, float)) else ""
            src = ", ".join(str(x) for x in (p.get("source"), f"quest [{p['quest']}]" if p.get("quest") else "",
                                             p.get("profession"), p.get("zone")) if x)
            # (its coordinates are on other clients' maps: Stormwind's are moved onto this server's)
            where = places_on_this_map(str(p["where"])) if p.get("where") else ""
            lines.append(f"  {p.get('rank')}. [{p.get('name')}]{state} ({src}{gain})"
                         + (f": {p['how']}" if p.get("how") else "") + (f" Where: {where}." if where else ""))
        if not slot.get("picks"):
            lines.append("  nothing listed for this level")
    if res.get("waiting"):
        lines.append("Not ranked yet (GearQuest is still building them; ask again in a moment): "
                     + ", ".join(str(s) for s in res["waiting"]) + ".")
    if res.get("tracked"):
        lines.append("The player tracks in GearQuest: " + ", ".join(f"[{t}]" for t in res["tracked"]) + ".")
    return "\n".join(lines)


def format_zone_quests(zone: str, res: dict | None, entrances: list[str]) -> str:
    """Game result of zone_quests -> text for the model."""
    if not res:
        return "The game did not respond in time; the quests of the zone could not be read."
    if res.get("error"):
        return f"Reading the quests of {zone} failed: {res['error']}"
    quests = [q for q in res.get("quests") or [] if isinstance(q, dict) and q.get("title")]
    level = res.get("level")
    lines = [f"Quests the character{f' (level {level})' if level else ''} can pick up for {zone}: {len(quests)}"
             + (f" (and {res['more']} more, not listed)" if res.get("more") else "") + "."]
    for q in quests:
        kind = ", ".join(str(x) for x in (f"level {q['level']}" if q.get("level") else "", q.get("type") or "") if x)
        about = f" [belongs to {q['about']}]" if q.get("about") and str(q["about"]).lower() != zone.lower() else ""
        lines.append(f"- [{q['title']}]{f' ({kind})' if kind else ''}{about} from {q.get('giver') or '?'} ({q.get('where') or '?'})"
                     + (f": {q['objective']}" if q.get("objective") else ""))
        for where in q.get("whereToGet") or []:
            lines.append(f"    {where}")
        if q.get("turnIn"):
            lines.append(f"    turn in: {q['turnIn']}")
    if res.get("later"):
        lines.append(f"{res['later']} more open at a higher level than the character has.")
    if entrances:
        lines.append("Entrances in this zone: " + "; ".join(entrances) + ".")
    if quests:
        lines.append("To put some on the route, name their titles in plan_route's new_quests (and the zone in then): "
                     "the route then picks each up at its giver. Quests already in the log are not listed here.")
    return "\n".join(lines)


def format_addon_options(addon: str, res: dict | None) -> str:
    """Game result of addon_options -> text for the model."""
    if not res:
        return "The game did not respond in time; the add-on options could not be read."
    if res.get("error"):
        return f"Reading the options of {addon} failed: {res['error']}"
    lines = []
    opts = res.get("options") or []
    if opts:
        lines.append(f"Options of {addon} (matching: {res.get('matched', len(opts))}, shown: {len(opts)}). "
                     "To change one: propose_addon_change op=set_option, path exactly as below.")
        for o in opts:
            bits = [f"- path={o.get('path')}", f"[{o.get('type')}]", f"\"{o.get('name')}\""]
            if "value" in o:
                bits.append(f"current={json.dumps(o.get('value'), ensure_ascii=False)}")
            if o.get("values"):
                vals = ", ".join(f"{k}={v}" for k, v in o["values"].items())
                bits.append(f"allowed: {vals}")
            if o.get("min") is not None or o.get("max") is not None:
                bits.append(f"range {o.get('min')}..{o.get('max')}")
            if o.get("disabled"):
                bits.append("(currently disabled)")
            if o.get("desc"):
                bits.append(f"— {o['desc']}")
            lines.append(" ".join(bits))
    elif res.get("apps"):
        lines.append(f"{addon} has an options tree ({', '.join(res['apps'])}), but nothing matches the query; "
                     "try other English keywords or an empty query.")
    raw = res.get("savedVariables") or []
    if raw:
        lines.append(res.get("note") or "Saved variables (op=set_setting):")
        for e in raw:
            lines.append(f"- path={e.get('path')} current={json.dumps(e.get('value'), ensure_ascii=False)}")
    if not opts and not raw and not res.get("apps"):
        lines.append(f"No options found for {addon} (no options tree and no matching saved variables).")
    if res.get("slashCommands"):
        lines.append("Add-on commands: " + ", ".join(res["slashCommands"][:12]))
    return chr(10).join(lines)


def remember_options(known: dict, addon: str, res: dict | None) -> None:
    """Keep what the game reported, so a following set_option/set_setting proposal can be validated."""
    for o in (res or {}).get("options") or []:
        if o.get("path"):
            known[(addon.lower(), str(o["path"]))] = o
    for e in (res or {}).get("savedVariables") or []:
        if e.get("path"):
            known[(addon.lower(), str(e["path"]))] = {"raw": True, "value": e.get("value")}


TOOLS.append({
    "type": "function",
    "function": {
        "name": "propose_quest_abandon",
        "description": "Propose abandoning a quest from the player's quest log (e.g. FAILED, too hard, not needed, full "
                       "quest log). Nothing happens until the player clicks Accept in the game; abandoning cannot be "
                       "undone (the quest can be picked up again from the NPC). quest_id exactly from the quests section.",
        "parameters": {
            "type": "object",
            "properties": {
                "quest_id": {"type": "integer"},
                "reason": {"type": "string", "description": "In the player's language: why abandon it"},
            },
            "required": ["quest_id", "reason"],
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "propose_vendor_action",
        "description": "While the vendor window is OPEN (windows.merchant): op=sell proposes selling items from the bags "
                       "(items = names or ids from the bags section; whole stacks are sold), op=repair proposes repairing "
                       "all equipment. The player confirms with a click; a sale can be undone (buyback). Do not propose "
                       "selling quest items, materials useful for the player's professions or gear worth equipping. "
                       "The app checks each item against the auction prices the player's auction add-on last saw and "
                       "keeps back what is not junk (uncommon or better, gear with stats, anything that sells for "
                       "clearly more on the auction house): the result says what was kept and why, tell the player.",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["sell", "repair"]},
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "For sell: item names or ids from bags"},
                "player_named": {"type": "boolean", "description": "true ONLY when the player named these very items "
                                                                   "to sell (\"sell the green belt\"): then nothing "
                                                                   "is kept back. Never for \"sell the junk\"."},
                "reason": {"type": "string", "description": "In the player's language, briefly: why"},
            },
            "required": ["op", "reason"],
        },
    },
})


TOOLS.append({
    "type": "function",
    "function": {
        "name": "search_auction_house",
        "description": "Search the auction house prices IN THE GAME: the player's auction add-on (Auctionator) keeps the "
                       "lowest buyout of every item from its last scan; items the last full scan did not see are no "
                       "longer listed and are left out. Returns matching items, best first for gear, with price, how "
                       "many days ago it was seen, stats, required level, the comparison with the equipped item "
                       "(vsEquipped) and, with the Pawn add-on, the upgrade in percent and the item's value. Use it "
                       "for every question about buying on the AH (upgrades, best item for a budget, cheapest "
                       "materials, prices). Give filters, not only a name: what the player said of the kind of item, "
                       "the slot and the budget each has its filter (the best come first, each says whether it beats "
                       "the equipped item); upgrades_only true only when the player asks for upgrades. For the prices "
                       "of KNOWN items (a profession's reagents and crafted items, a shopping list) give items "
                       "instead: exact names or item ids, up to 40 in one call; each comes back with its price or why "
                       "it has none. What to sell from the bags: compare_sell_prices.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},  # one plain type: every provider takes it
                          "description": "Exact item names or item ids (ids from the "
                                         "professions or bags data are the surest), up to 40. Other filters are "
                                         "ignored with items."},
                "query": {"type": "string", "description": "English words that must be in the item name (optional)"},
                "slot": {"type": "string", "enum": list(AH_SLOTS)},
                "weapon_type": {"type": "string", "enum": list(AH_WEAPONS)},
                "armor_type": {"type": "string", "enum": list(AH_ARMOR)},
                "category": {"type": "string", "enum": list(AH_CATEGORIES),
                             "description": "Item class for non-gear searches (materials, potions, bags, recipes)"},
                "max_price_gold": {"type": "number"},
                "min_price_gold": {"type": "number"},
                "usable_only": {"type": "boolean", "description": "Only items the player can use now (default true)"},
                "upgrades_only": {"type": "boolean", "description": "Only upgrades over the equipped item (only when "
                                                                    "the player asks for upgrades)"},
                "max_age_days": {"type": "integer", "description": "Only prices seen in the last N days (default 7)"},
                "sort": {"type": "string", "enum": ["upgrade", "price", "ilvl"]},
                "limit": {"type": "integer", "description": "Max items returned (default 15, max 30)"},
            },
        },
    },
})

TOOLS.append({
    "type": "function",
    "function": {
        "name": "auctionator_list",
        "description": "The player's shopping list for the auction house: one list, kept by the app through the "
                       "conversation and shown as the Auction house card under the answer, where one click searches "
                       "all of it in Auctionator's Shopping tab or saves it as an Auctionator shopping list. items "
                       "adds what this answer recommends buying (not what you only mention or advise against), and "
                       "any items the player wants a list, a filter or a search in Auctionator for; what earlier "
                       "answers put on the list stays on it. Give each item's own name as the game shows it (the "
                       "item, not the quest or the task that wants it). An item leaves the list when the player has "
                       "it or when you name it in remove (the player does not want it, or you now advise against "
                       "it); one the last scan has no price for stays on it, marked so. The app prices the whole list "
                       "from the newest scan; the result gives every item on it with its price now, the names the "
                       "price data does not know (check those) and what left the list and why. An item the character "
                       "cannot use (a requirement the game marks as not met) stays off the list, and the result says "
                       "what it requires. Write the answer from this result. Call it in every answer that recommends "
                       "buying something on the auction house or changes what the player should buy.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "Exact item names or item ids to put on the list, the best pick first"},
                "remove": {"type": "array", "items": {"type": "string"},
                           "description": "Items to take off the list (names or ids)"},
                "name": {"type": "string",
                         "description": "A few words in English naming the list (what it is for); it is saved in "
                                        "Auctionator under this name. Leave it out to keep the list's name."},
                "include_unusable": {"type": "boolean",
                                     "description": "Also put on the list the items of this call the character "
                                                    "cannot use (the result names them and what they require). Only "
                                                    "when the player asked for such an item knowing that. Default "
                                                    "false: they stay off the list."},
            },
        },
    },
})


AH_EXACT_MAX = 40

# Tools that only look something up (in the knowledge base, on the web, in the game) and change nothing: a client that
# runs such calls at once may do so. The ones that ask the game then go to it together, and it answers them together,
# instead of a round trip through the screenshot channel for each.
READ_ONLY_TOOLS = frozenset({"search_knowledge_base", "fetch_url", "addon_options", "zone_quests", "gear_upgrades",
                             "map_notes", "pet_abilities", "way_to", "my_auctions", "search_auction_house",
                             "compare_sell_prices"})


def _exact_item(x: Any) -> dict | None:
    """'Linen Cloth', '[Linen Cloth]', 2589, '2589', 'Linen Cloth (item 2589)' or {"id"/"name"} -> {"id"} or {"name"}."""
    if isinstance(x, dict):
        x = x.get("id") or x.get("name")
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return {"id": int(x)} if int(x) > 0 else None
    if not isinstance(x, str):
        return None
    text = x.strip()
    m = re.search(r"\bitem[: ]+(\d+)", text, re.I)
    if m:
        return {"id": int(m.group(1))}
    text = text.strip("[] ")
    if text.isdigit():
        return {"id": int(text)} if int(text) > 0 else None
    return {"name": text[:80]} if text else None


def auction_request(args: dict) -> tuple[dict | None, str | None]:
    """Model arguments -> the game's auction_search request (numeric item classes, equip slots, copper).
    Returns (request, error)."""
    items = args.get("items")
    if items not in (None, "", []):
        if isinstance(items, (str, int)):
            items = [items]
        if not isinstance(items, list):
            return None, "Rejected: items must be a list of item names or ids."
        if len(items) > AH_EXACT_MAX:
            return None, f"Rejected: at most {AH_EXACT_MAX} items per call; split the list."
        exact, seen = [], set()
        for x in items:
            it = _exact_item(x)
            key = json.dumps(it, sort_keys=True).lower() if it else None
            if it and key not in seen:
                seen.add(key)
                exact.append(it)
        if not exact:
            return None, "Rejected: items must be item names (e.g. 'Linen Cloth') or item ids."
        try:
            age = max(1, min(30, int(args.get("max_age_days") or 30)))
        except (TypeError, ValueError):
            age = 30
        return {"items": exact, "maxAgeDays": age}, None

    def gold(name):
        v = args.get(name)
        if v in (None, ""):
            return None
        try:
            return max(0, int(round(float(v) * 10000)))
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number of gold")

    try:
        max_c, min_c = gold("max_price_gold"), gold("min_price_gold")
    except ValueError as e:
        return None, f"Rejected: {e}."
    req: dict[str, Any] = {"query": str(args.get("query") or "").strip()[:60]}
    classes: set[int] = set()
    subclasses: list[int] = []
    for key, table, cls in (("weapon_type", AH_WEAPONS, 2), ("armor_type", AH_ARMOR, 4)):
        val = str(args.get(key) or "").strip().lower()
        if val:
            if val not in table:
                return None, f"Rejected: unknown {key} {val!r}; allowed: {', '.join(table)}."
            classes.add(cls)
            subclasses += table[val]
    if len(classes) > 1:
        return None, "Rejected: give weapon_type or armor_type, not both."
    cat = str(args.get("category") or "").strip().lower()
    if cat and cat != "any":
        if cat not in AH_CATEGORIES:
            return None, f"Rejected: unknown category {cat!r}."
        if classes and AH_CATEGORIES[cat] not in classes:
            return None, "Rejected: the category does not match the weapon/armor type."
        classes.add(AH_CATEGORIES[cat])
    slot = str(args.get("slot") or "").strip().lower()
    if slot and slot != "any":
        if slot not in AH_SLOTS:
            return None, f"Rejected: unknown slot {slot!r}; allowed: {', '.join(AH_SLOTS)}."
        req["equipLocs"] = AH_SLOTS[slot]
    if classes:
        req["classIDs"] = sorted(classes)
    if subclasses:
        req["subclassIDs"] = subclasses
    if not (req["query"] or classes or req.get("equipLocs")):
        return None, "Rejected: give a query (item name words) or filters (slot, weapon_type, armor_type, category)."
    if max_c is not None:
        req["maxCopper"] = max_c
    if min_c:
        req["minCopper"] = min_c

    def number(name, default, lo, hi):
        try:
            return max(lo, min(hi, int(args.get(name) if args.get(name) is not None else default)))
        except (TypeError, ValueError):
            return default
    req["usableOnly"] = args.get("usable_only") is not False
    req["upgradesOnly"] = args.get("upgrades_only") is True
    req["maxAgeDays"] = number("max_age_days", 7, 1, 30)
    req["limit"] = number("limit", 15, 1, 30)
    req["sort"] = args.get("sort") if args.get("sort") in ("upgrade", "price", "ilvl") else (
        "upgrade" if classes & {2, 4} or req.get("equipLocs") else "price")
    return req, None


def format_auction(res: dict | None) -> str:
    """Game result of auction_search -> text for the model."""
    if not res:
        return "The game did not respond in time; the auction prices could not be read."
    if res.get("error"):
        return f"Auction house search failed: {res['error']}"
    items = res.get("items") or []
    head = [f"Auction house prices from {res.get('source', 'the auction add-on')}"
            + (f" (realm {res['realm']})" if res.get("realm") else "")
            + (f"; last scan: {res['lastScan']}" if res.get("lastScan") else "") + "."]
    if res.get("exact"):
        head.append(f"Prices of the requested items: the lowest buyout when each was last seen on offer (the last "
                    f"full scan or a search since, in the last {res.get('maxAgeDays', 30)} days); listings may have "
                    "sold or changed since.")
        lines = []
        for it in items:
            seen = it.get("seen")
            lines.append(f"- [{it.get('name')}] (item {it.get('id')}) — {it.get('price', '?')}"
                         + (f" | seen {seen}" if seen else "")
                         + (f", {it['available']} available" if it.get("available") else "")
                         + "".join(f" | {flag}: {it[flag]}" for flag in ("cannotUse", "tooLowLevel") if it.get(flag)))
        for m in res.get("missing") or []:
            ident = f"[{m.get('name')}]" if m.get("name") else "?"
            if m.get("id"):
                ident += f" (item {m['id']})"
            gone = (f" (last seen {m.get('lastSeen', '?')} at {m['lastPrice']}; that offer is gone)"
                    if m.get("lastPrice") else "")
            lines.append(f"- {ident}: {m.get('why') or 'no price'}{gone}")
        if not lines:
            lines.append("No prices found.")
        if res.get("ahOpen"):
            lines.append("The auction house window is open: the card of auctionator_list searches its items in "
                         "Auctionator at a click.")
        return "\n".join(head + lines)
    head.append(f"{res.get('dbItems', '?')} items in the price data, {res.get('matched', len(items))} match the filters, "
                f"showing {len(items)} (sorted by {res.get('sort', 'upgrade')}). A price is the lowest buyout when the "
                "item was last seen on offer (the last full scan or a search since); listings may have sold or "
                "changed since.")
    if res.get("upgradeMethod"):
        head.append(f"Upgrade check: {res['upgradeMethod']}.")
        if res.get("sort", "upgrade") == "upgrade" and str(res["upgradeMethod"]).startswith("Pawn"):
            head.append("Order: the upgrades first (the biggest first), then the other items by their Pawn value in "
                        "the player's scale (higher is better; compare with the equipped item's value).")
    if res.get("skipped"):
        head.append("Left out: " + ", ".join(f"{n} {why}" for why, n in sorted(res["skipped"].items()) if n) + ".")
    unusable = [u for u in res.get("unusable") or [] if isinstance(u, dict) and u.get("name")]
    if unusable:
        more = int((res.get("skipped") or {}).get("not usable") or 0) - len(unusable)
        head.append("Left out because this character cannot use them: "
                    + "; ".join(f"[{u['name']}] {u.get('price') or '?'} ({u.get('why') or 'cannot use'})" for u in unusable)
                    + (f"; and {more} more" if more > 0 else "") + ". They are not picks for this character. When one "
                    "of them is what the player asked about, or would have been the pick without its requirement, say "
                    "that it was left out and what it requires. usable_only=false returns them in full, for when the "
                    "player asks for such items.")
    if res.get("usableCheck") == "skills only":
        head.append("Only armor and weapon skills were checked: the game gave no item requirements (a profession, a "
                    "class, a reputation) for these items, so say that a pick may have one.")
    if res.get("notLoaded"):
        head.append(f"{res['notLoaded']} matching items are not in the game's item cache yet; searching again in a "
                    "moment finds more.")
    if not items:
        head.append("No matching items. Widen the filters (price, age, usable_only) or tell the player to open the "
                    "auction house and scan with Auctionator.")
    lines = []
    for it in items:
        bits = [f"- [{it.get('name')}] (item {it.get('id')}) — {it.get('price', '?')}"]
        seen = it.get("seen")
        if seen:
            bits.append(f"seen {seen}" + (f", {it['available']} available" if it.get("available") else ""))
        desc = ", ".join(str(x) for x in (it.get("quality"), it.get("ilvl") and f"ilvl {it['ilvl']}",
                                          it.get("requiresLevel") and f"requires level {it['requiresLevel']}",
                                          it.get("slot"), it.get("type")) if x)
        if desc:
            bits.append(desc)
        if it.get("stats"):
            bits.append(f"stats: {it['stats']}")
        if it.get("vsEquipped"):
            bits.append(f"vs equipped: {it['vsEquipped']}")
        if it.get("pawn"):
            bits.append(f"Pawn: {it['pawn']}")
        for flag in ("cannotUse", "tooLowLevel"):
            if it.get(flag):
                bits.append(f"{flag}: {it[flag]}")
        lines.append(" | ".join(bits))
    if res.get("ahOpen"):
        lines.append("The auction house window is open: the card of auctionator_list searches its items in Auctionator at a click.")
    return "\n".join(head + lines)


TOOLS.append({
    "type": "function",
    "function": {
        "name": "compare_sell_prices",
        "description": "Where the items in the player's bags sell for more: what a vendor pays next to the auction "
                       "house's lowest buyout from the last scan of the player's auction add-on, after the auction "
                       "house's 5% cut, sorted into lists (better at a vendor, worth the auction house, soulbound, "
                       "vendors do not buy). Call it once for every question about what to sell at a vendor or on "
                       "the auction house; it prices the whole bags in one call.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "Optional: only these bag items (names or ids from the bags section)"},
            },
        },
    },
})

AH_CUT = 0.05  # the auction house keeps 5% of a sale
# the auction house is worth a listing (the trip, the wait and a deposit lost when nothing sells) only when the whole
# stack brings at least this much more there than at a vendor: 10% and 50 copper
AH_GAIN_SHARE, AH_GAIN_COPPER = 0.10, 50
SELL_PRICE_MAX = 100  # items priced in one game call (what Auction.lua takes)


def _bag_name(it: dict) -> str:
    return str(it.get("item") or "").strip("[] ")


def sell_price_request(snapshot: dict, args: dict) -> tuple[list[dict], dict | None, str | None]:
    """The bag items to price and the game's auction_search request for those the auction house can take.
    Returns (entries, request or None, error). Quest items and items without any sale value are left out."""
    bags = (snapshot.get("bags") or {}).get("items") if isinstance(snapshot.get("bags"), dict) else None
    if not bags:
        return [], None, "Rejected: the game data has no bags section (the bags are empty or were not read)."
    only = args.get("items")
    if isinstance(only, (str, int)):
        only = [only]
    want = {str(x).strip("[] ").lower() for x in only} if isinstance(only, list) and only else None
    entries, skipped = [], []
    for it in bags:
        if not isinstance(it, dict) or not it.get("item"):
            continue
        name = _bag_name(it)
        if want is not None and name.lower() not in want and str(it.get("id")) not in want:
            continue
        tip = str(it.get("tooltip") or "")
        sell = int(it.get("sellCopper") or 0)
        if not sell and (it.get("bound") or "Conjured Item" in tip):
            skipped.append(name)  # quest items, the Hearthstone, conjured food: nothing to sell
            continue
        entries.append({"id": it.get("id"), "name": name, "count": int(it.get("count") or 1), "sell": sell,
                        "bound": bool(it.get("bound")), "junk": bool(it.get("junk")),
                        "material": "Crafting Reagent" in tip, "quality": it.get("quality")})
    if want is not None and not entries:
        return [], None, "Rejected: none of these items is in the bags with a sale value: " + ", ".join(map(str, only))
    priced = [e for e in entries if not e["bound"] and e.get("id")][:SELL_PRICE_MAX]
    req = {"items": [{"id": int(e["id"])} for e in priced], "maxAgeDays": 30} if priced else None
    if skipped and want is None:
        entries.append({"skipped": skipped})
    return entries, req, None


def _each(copper: int, count: int) -> str:
    return f" ({money(copper)} each)" if count > 1 else ""


def format_sell_prices(entries: list[dict], res: dict | None) -> str:
    """Bag items with their vendor and auction prices -> the lists for the model."""
    skipped = next((e["skipped"] for e in entries if "skipped" in e), [])
    entries = [e for e in entries if "skipped" not in e]
    ah_error = None
    if not res:
        ah_error = "the game did not respond in time"
    elif res.get("error"):
        ah_error = str(res["error"])
    prices = {int(it["id"]): it for it in (res or {}).get("items") or [] if it.get("id") and it.get("copper")}
    missing = {int(m["id"]): m for m in (res or {}).get("missing") or [] if m.get("id")}
    groups: dict[str, list[tuple[int, str]]] = {"vendor": [], "ah": [], "bound": [], "nobuy": []}
    totals = {"vendor": 0, "ah": 0, "bound": 0}
    for e in entries:
        n, sell = e["count"], e["sell"]
        label = f"[{e['name']}]" + (f" x{n}" if n > 1 else "")
        notes = ", ".join(x for x in ("junk" if e["junk"] else "", "crafting material" if e["material"] else "",
                                      e["quality"] if e["quality"] in ("uncommon", "rare", "epic") else "") if x)
        label += f" ({notes})" if notes else ""
        vendor = f"vendor {money(sell * n)}{_each(sell, n)}" if sell else ""
        if e["bound"]:
            groups["bound"].append((sell * n, f"- {label} — {vendor}"))
            totals["bound"] += sell * n
            continue
        p = prices.get(int(e["id"])) if e.get("id") else None
        if p:
            net = int(int(p["copper"]) * (1 - AH_CUT))
            seen = ", ".join(x for x in (f"seen {p['seen']}" if p.get("seen") else "",
                                         f"{p['available']} listed" if p.get("available") else "") if x)
            ah = f"AH {money(net * n)} after the cut{_each(net, n)}" + (f", {seen}" if seen else "")
            if not sell:
                groups["nobuy"].append((net * n, f"- {label} — {ah}; vendors do not buy it"))
            elif net * n - sell * n >= max(AH_GAIN_COPPER, sell * n * AH_GAIN_SHARE):
                groups["ah"].append((net * n - sell * n, f"- {label} — {ah}; {vendor}"))
                totals["ah"] += net * n
            else:
                groups["vendor"].append((sell * n, f"- {label} — {vendor}; {ah}"))
                totals["vendor"] += sell * n
            continue
        m = missing.get(int(e["id"])) if e.get("id") else None
        if ah_error:
            why = "auction price not known"
        elif m and m.get("lastPrice"):
            why = f"not listed on the AH in the last scan (last seen {m.get('lastSeen', '?')} at {m['lastPrice']})"
        elif m:
            why = "nobody listed it on the AH in the last 30 days"
        else:  # beyond what one call prices, or an add-on from before /reload that prices fewer items
            why = "auction price not checked"
        if sell:
            groups["vendor"].append((sell * n, f"- {label} — {vendor}; {why}"))
            totals["vendor"] += sell * n
        else:
            groups["nobuy"].append((0, f"- {label} — vendors do not buy it; {why}"))
    head = [f"The player's bag items: what a vendor pays and what the auction house pays after its {int(AH_CUT * 100)}% "
            f"cut (the lowest buyout in {(res or {}).get('source') or 'the auction add-on'}"
            + (f"; last scan: {res['lastScan']}" if res and res.get("lastScan") else "") + "). An item is worth "
            f"the auction house when the whole stack brings at least {int(AH_GAIN_SHARE * 100)}% and "
            f"{money(AH_GAIN_COPPER)} more there; a listing also costs a deposit that is lost when it does not sell."]
    if ah_error:
        head.append(f"Auction prices could not be read ({ah_error}): only the vendor prices are known.")
    titles = {"vendor": "What a vendor pays (not compared with the auction house)" if ah_error else
              "Better sold to a vendor", "ah": "Worth the auction house",
              "bound": "Soulbound (only a vendor buys these)", "nobuy": "Vendors do not buy these"}
    out = head
    for key in ("vendor", "ah", "bound", "nobuy"):
        rows = sorted(groups[key], key=lambda r: -r[0])
        if not rows:
            continue
        total = f"; together {money(totals[key])}" if totals.get(key) else ""
        out.append(f"\n{titles[key]} ({len(rows)}{total}):")
        out += [r[1] for r in rows]
    if skipped:
        out.append("\nNot priced (quest items, conjured items, no sale value): " + ", ".join(f"[{s}]" for s in skipped))
    out.append("\nThese lists only compare prices. Before you tell the player to sell an item, check that this character "
               "has no use for it (Bags and selling); leave those out and say in one line which ones you kept.")
    return "\n".join(out)


def validate_vendor(args: dict, snapshot: dict, guard=None) -> tuple[dict | None, str]:
    """``guard(bag item)`` -> (why it is kept back or None, why it may go): the check against auction prices."""
    merchant = ((snapshot.get("windows") or {}).get("merchant")) if isinstance(snapshot.get("windows"), dict) else None
    if not merchant:
        return None, "Rejected: the vendor window is not open (no windows.merchant). Tell the player to talk to a vendor."
    op = args.get("op")
    reason = str(args.get("reason") or "").strip()[:300]
    if op == "repair":
        if not merchant.get("canRepair"):
            return None, "Rejected: this vendor cannot repair (canRepair = false). Point to a [repair] vendor from zoneNPCs."
        if not merchant.get("repairCopper"):
            return None, "Rejected: nothing to repair."
        action = {"id": next(_ids), "op": "vendor_repair", "addon": "Vendor", "cost": merchant.get("repairCost"),
                  "description": reason}
        return action, f"Repair for {merchant.get('repairCost')} proposed; waiting for the player to accept."
    if op != "sell":
        return None, "Rejected: op must be sell or repair."
    bags = ((snapshot.get("bags") or {}).get("items")) or []
    by_id = {int(b["id"]): b for b in bags if isinstance(b, dict) and b.get("id")}
    by_name = {str(b.get("item", "")).strip("[]").lower(): b for b in bags if isinstance(b, dict)}
    picked, problems, total = [], [], 0
    for want in args.get("items") or []:
        b = by_id.get(int(want)) if isinstance(want, int) or str(want).isdigit() else by_name.get(str(want).strip("[] ").lower())
        if not b:
            problems.append(f"{want}: not in the bags")
            continue
        if not b.get("sellCopper"):
            problems.append(f"{b.get('item')}: vendors do not buy it")
            continue
        if any(p["id"] == b.get("id") for p in picked):
            continue
        kept, why = guard(b) if guard and args.get("player_named") is not True else (None, None)
        if kept:
            problems.append(f"{b.get('item')}: KEPT, {kept}")
            continue
        value = int(b["sellCopper"]) * int(b.get("count") or 1)
        total += value
        picked.append({"id": b.get("id"), "name": b.get("item"), "count": b.get("count"), "price": money(value),
                       "why": why})
    if not picked:
        return None, "Rejected: nothing to sell. " + "; ".join(problems)
    action = {"id": next(_ids), "op": "vendor_sell", "addon": "Vendor", "items": picked, "total": money(total),
              "description": reason}
    note = (" Not sold: " + "; ".join(problems) + ".") if problems else ""
    sold = "; ".join(f"{p['name']} ×{p['count']} for {p['price']}" + (f" ({p['why']})" if p.get("why") else "") for p in picked)
    return action, (f"Sale of {len(picked)} item types for {money(total)} proposed; waiting for the player to accept: "
                    f"{sold}.{note}")


def validate_abandon(args: dict, snapshot: dict) -> tuple[dict | None, str]:
    try:
        qid = int(args.get("quest_id"))
    except (TypeError, ValueError):
        return None, "Rejected: quest_id must be a number from the quests section."
    quests = {int(q["id"]): q for q in (snapshot.get("quests") or []) if isinstance(q, dict) and q.get("id")}
    q = quests.get(qid)
    if not q:
        return None, f"Rejected: quest {qid} is not in the player's quest log (quests section)."
    reason = str(args.get("reason") or "").strip()
    if not reason:
        return None, "Rejected: give a reason."
    action = {"id": next(_ids), "op": "abandon_quest", "addon": "Quest log", "questId": qid,
              "title": q.get("title"), "description": reason[:400]}
    return action, f"Abandoning [{q.get('title')}] proposed; waiting for the player to accept in the game."


def money(copper) -> str:
    try:
        c = int(round(float(copper)))
    except (TypeError, ValueError):
        return "?"
    g, s, c = c // 10000, c // 100 % 100, c % 100
    parts = ([f"{g}g"] if g else []) + ([f"{s}s"] if s or g else []) + [f"{c}c"]
    return " ".join(parts)


def validate_mark(args: dict) -> tuple[dict | None, str]:
    try:
        x, y = float(args.get("x")), float(args.get("y"))
    except (TypeError, ValueError):
        return None, "Rejected: x and y must be numbers 0-100."
    if 0 < x <= 1 and 0 < y <= 1:  # model gave 0-1 fractions
        x, y = x * 100, y * 100
    if not (0 <= x <= 100 and 0 <= y <= 100):
        return None, "Rejected: coordinates outside 0-100."
    title = str(args.get("title") or "Point").strip()[:60]
    mark = {"title": title, "x": round(x, 1), "y": round(y, 1), "zone": str(args.get("zone") or "").strip()[:60]}
    note = str(args.get("note") or "").strip()
    if note.startswith("(") and note.endswith(")") and not set("()") & set(note[1:-1]):
        note = note[1:-1].strip()  # the list puts it in brackets itself; brackets inside a note stay whole
    note = note[:60].rstrip()
    if note:
        mark["note"] = note  # what to do there, shown in brackets after the stop in the add-on's list
    return mark, f"Marked on the map: {title} ({mark['x']}, {mark['y']})."


def run_fetch(url: str) -> str:
    from .web import fetch
    return fetch(url)

_ids = itertools.count(1)


def next_action_id() -> int:
    """An id for a proposal card (the add-on keeps track of cards by it)."""
    return next(_ids)


def _flatten(prefix: str, value: Any, out: dict) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out[prefix] = value


def _check_option(opt: dict, new: Any) -> tuple[Any, str | None]:
    """Check a new value against an option described by the game. Returns (value, error)."""
    kind = opt.get("type")
    if opt.get("disabled"):
        return None, "this option is currently disabled in the add-on"
    if kind == "execute":
        return None, "this is an action button, not a setting"
    if kind == "toggle":
        if isinstance(new, str) and new.lower() in ("true", "false"):
            new = new.lower() == "true"
        if not isinstance(new, bool):
            return None, "the value must be true/false"
    elif kind == "range":
        if isinstance(new, str):
            try:
                new = float(new)
            except ValueError:
                return None, "the value must be a number"
        if isinstance(new, bool) or not isinstance(new, (int, float)):
            return None, "the value must be a number"
        lo, hi = opt.get("min"), opt.get("max")
        if (lo is not None and new < lo) or (hi is not None and new > hi):
            return None, f"the value is outside {lo}..{hi}"
        if float(new).is_integer():
            new = int(new)
    elif kind == "select":
        values = opt.get("values") or {}
        if values and str(new) not in values:
            by_label = {str(v).lower(): k for k, v in values.items()}
            if str(new).lower() in by_label:
                new = by_label[str(new).lower()]
            else:
                return None, f"allowed values (keys): {list(values)[:20]}"
        if isinstance(new, str) and new.lstrip("-").isdigit() and isinstance(opt.get("value"), (int, float)):
            new = int(new)
    elif kind == "multiselect":
        values = opt.get("values") or {}
        if isinstance(new, str):  # "Bags=true, Repair=false"
            pairs = [p.split("=", 1) for p in new.replace(";", ",").split(",") if "=" in p]
            new = {k.strip(): v.strip().lower() in ("true", "1", "on", "tak") for k, v in pairs}
        if not isinstance(new, dict) or not new:
            return None, "the value must be a set of {key: true/false}"
        fixed = {}
        for k, v in new.items():
            key = str(k)
            if values and key not in values:
                by_label = {str(lbl).lower(): kk for kk, lbl in values.items()}
                key = by_label.get(key.lower(), key)
                if key not in values:
                    return None, f"unknown choice {k!r}; allowed keys: {list(values)[:20]}"
            if isinstance(v, str):
                v = v.lower() in ("true", "1", "on", "tak")
            fixed[key] = bool(v)
        new = fixed
    elif kind in ("input", "keybinding"):
        if not isinstance(new, str):
            return None, "the value must be text"
    return new, None


def validate_proposal(args: dict, snapshot: dict, known: dict | None = None) -> tuple[dict | None, str]:
    """Return (action, message_for_model). ``known`` maps (addon lower, path) -> option/variable the game reported
    through addon_options in this answer."""
    known = known or {}
    addon = str(args.get("addon") or "").strip()
    op = args.get("op")
    desc = str(args.get("description") or "").strip()
    if op not in ALLOWED_OPS:
        return None, f"Rejected: unknown operation {op!r}."
    installed = {str(a.get("name")) for a in (snapshot.get("addons") or []) if isinstance(a, dict)}
    if installed and addon not in installed:
        return None, f"Rejected: add-on {addon!r} is not installed."
    if not desc:
        return None, "Rejected: the change has no description."
    action = {"id": next(_ids), "addon": addon, "op": op, "description": desc[:600],
              "needsReload": bool(args.get("needs_reload"))}
    path = str(args.get("path") or "").strip()
    if op == "set_option":
        opt = known.get((addon.lower(), path))
        if not opt or opt.get("raw"):
            near = [p for (a, p), o in known.items() if a == addon.lower() and not o.get("raw")][:10]
            return None, (f"Rejected: unknown option {path!r} of {addon}. Call addon_options (addon, keywords) first "
                          f"and use the path exactly as returned. Known: {near}")
        value, err = _check_option(opt, args.get("value"))
        if err:
            return None, f"Rejected: {err}."
        values = opt.get("values") or {}
        if opt.get("type") == "multiselect":
            def onoff(state: dict) -> str:
                return ", ".join(f"{values.get(k, k)}: {'on' if v else 'off'}" for k, v in state.items())
            old_state = opt.get("value") if isinstance(opt.get("value"), dict) else {}
            old_label = onoff({k: old_state.get(k, False) for k in value})
            new_label = onoff(value)
        else:
            old_label = values.get(str(opt.get("value"))) if values else None
            new_label = values.get(str(value)) if values else None
        action.update({"app": opt.get("app"), "path": path, "old": opt.get("value"), "value": value,
                       "optionName": opt.get("name") or path, "oldLabel": old_label, "valueLabel": new_label})
        return action, f"Proposal #{action['id']} is waiting for the player to accept in the game."
    if op == "set_setting":
        flat: dict = {p: o.get("value") for (a, p), o in known.items() if a == addon.lower() and o.get("raw")}
        settings = snapshot.get("addonSettings") or {}
        if isinstance(settings, dict) and settings.get("addon") == addon:
            _flatten("", settings.get("vars") or {}, flat)
        if not flat:
            return None, (f"Rejected: unknown saved variables of {addon}. Call addon_options first; if the add-on "
                          f"has an options tree, use op=set_option.")
        if path not in flat:
            near = [p for p in flat if path.split(".")[-1].lower() in p.lower()][:8]
            return None, f"Rejected: path {path!r} does not exist. Similar: {near}"
        old = flat[path]
        new = args.get("value")
        if isinstance(old, bool):
            if not isinstance(new, bool):
                return None, "Rejected: the value must be true/false."
        elif isinstance(old, (int, float)):
            if isinstance(new, bool) or not isinstance(new, (int, float)):
                return None, "Rejected: the value must be a number."
        elif isinstance(old, str):
            if not isinstance(new, str):
                return None, "Rejected: the value must be text."
        else:
            return None, "Rejected: only simple values (bool/number/text) can be changed."
        action.update({"path": path, "old": old, "value": new})
    elif op == "toggle_addon":
        if not isinstance(args.get("value"), bool):
            return None, "Rejected: toggle_addon needs value true/false."
        action.update({"value": args["value"], "needsReload": True})
    elif op == "slash_command":
        cmd = str(args.get("command") or "").strip()
        if not cmd.startswith("/") or "\n" in cmd or len(cmd) > 200:
            return None, "Rejected: invalid command."
        if cmd.split()[0].lower() in ("/run", "/script", "/dump", "/console", "/reload", "/logout", "/quit", "/camp"):
            return None, "Rejected: this command is blocked."
        action.update({"command": cmd})
    return action, f"Proposal #{action['id']} is waiting for the player to accept in the game."


KB_PAGES = 3            # best matching pages returned whole
KB_PAGE_CHARS = 15000   # safety cap per page (the longest Forever pages are ~12k characters)


def _has_content(text: str) -> bool:
    """Sections like "Patch changes: -" or a bare "[Elinks-item: 1]" say nothing."""
    return len(re.sub(r"\[Elinks[^\]]*\]|[\s\-|]", "", text or "")) >= 20


def run_kb_search(kb: KB | None, query: str) -> tuple[str, list[dict]]:
    """The best matching pages, each in full (all sections), so the model never works from fragments."""
    if kb is None:
        return "The knowledge base is not available.", []
    pages = []
    for h in kb.search(query, k=30):
        if _has_content(h.text) and all(p.url != h.url for p in pages):
            pages.append(h)
        if len(pages) >= KB_PAGES:
            break
    if not pages:
        return "No results in the local knowledge base.", []
    parts = []
    for h in pages:
        full = kb.page_text(h.url) or h.text
        if len(full) > KB_PAGE_CHARS:
            full = full[:KB_PAGE_CHARS] + "\n(page continues; search with more specific words for the rest)"
        parts.append(f"[{h.title} | {h.url} | updated {h.ts[:10]}]\n{full}")
    return "\n\n".join(parts), [{"title": h.title, "url": h.url, "date": h.ts[:10]} for h in pages]
