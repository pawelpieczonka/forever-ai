"""Function tools offered to the model and their execution."""

from __future__ import annotations

import itertools
import json
import re
from typing import Any

from .kb.store import KB

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
                    "addon": {"type": "string", "description": "Addon folder name, e.g. Questie"},
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
                       "(from the quests/zoneNPCs/nearbyMobs data or the wiki). zone = English zone name (e.g. "
                       "'Teldrassil'); omit for the player's current zone. Can be called several times (up to 12 points). "
                       "The marks of one answer replace the marks of your earlier answers; add=true keeps those and "
                       "adds this point (an extra stop on a route that still holds).",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short name of the point, e.g. 'Conservator Ilthalaine'"},
                "x": {"type": "number"}, "y": {"type": "number"},
                "zone": {"type": "string"},
                "add": {"type": "boolean", "description": "true: keep the marks of earlier answers and add this one"},
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
        "description": "The walking route through the player's quest log in their current zone: every objective and "
                       "turn-in with a known place, in the order with the shortest way, each turn-in after its own "
                       "objectives, with the distances, and what is left out and why (another zone, needs a skill, "
                       "no known place). It also marks the stops on the map in that order (up to 12), replacing "
                       "earlier marks. Use it whenever the player asks for a route, a path, an order for their "
                       "quests or \"what is the best way to do these\"; do not order the stops yourself. You decide "
                       "what the route holds: read the quests first (text, level against the player's, elite or "
                       "group, escort, what you advised earlier) and say so with skip, last, new_quests and end.",
        "parameters": {
            "type": "object",
            "properties": {
                "skip": {"type": "array", "items": {"type": "string"},
                         "description": "Quest titles to leave out: quests the player cannot or should not do now "
                                        "(an elite or group quest alone, far above their level, one you advised against)"},
                "last": {"type": "array", "items": {"type": "string"},
                         "description": "Quest titles to do after everything else (the hard ones: by then the player "
                                        "may have a level more or company)"},
                "new_quests": {"type": "array", "items": {"type": "string"},
                               "description": "Titles of nearbyQuests worth picking up on the way: only the ones you "
                                              "recommend, not all of them"},
                "end_x": {"type": "number", "description": "Where the player goes after these quests, in this "
                                                             "zone's coordinates (the road to the next zone, a flight master, "
                                                             "the town for the turn-ins elsewhere): the route ends near it"},
                "end_y": {"type": "number"},
                "end_title": {"type": "string", "description": "What is at end_x, end_y, e.g. 'the road to Darnassus'"},
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
                       "from the options. query = a few ENGLISH keywords, e.g. 'tomtom arrow waypoint' or 'minimap icon'.",
        "parameters": {
            "type": "object",
            "properties": {
                "addon": {"type": "string", "description": "Add-on folder name from the addons list, e.g. Questie"},
                "query": {"type": "string", "description": "English keywords; empty = overview of the options"},
            },
            "required": ["addon"],
        },
    },
})


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
                       "selling quest items, materials useful for the player's professions or gear worth equipping.",
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["sell", "repair"]},
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "For sell: item names (e.g. 'Ruined Pelt') or ids from bags"},
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
                       "lowest buyout of every item from its last scan. Returns matching items with price, how many days "
                       "ago it was seen, stats, required level, the comparison with the equipped item (vsEquipped) and, "
                       "with the Pawn add-on, the upgrade in percent. Use it for every question about buying on the AH "
                       "(upgrades, best item for a budget, cheapest materials, prices). Give filters, not only a name: "
                       "e.g. 'best one-handed sword up to 100 gold' = weapon_type 1h_sword, slot one-hand, "
                       "max_price_gold 100, upgrades_only true. For the prices of KNOWN items (a profession's reagents "
                       "and crafted items, a shopping list) give items instead: exact names or item ids, up to 40 in "
                       "one call; each comes back with its price or why it has none.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"},  # one plain type: every provider takes it
                          "description": "Exact item names (e.g. 'Linen Cloth') or item ids (e.g. '2589'; ids from the "
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
                "upgrades_only": {"type": "boolean", "description": "Only upgrades over the equipped item"},
                "max_age_days": {"type": "integer", "description": "Only prices seen in the last N days (default 7)"},
                "sort": {"type": "string", "enum": ["upgrade", "price", "ilvl"]},
                "limit": {"type": "integer", "description": "Max items returned (default 15, max 30)"},
            },
        },
    },
})


AH_EXACT_MAX = 40


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
        head.append(f"Prices of the requested items: the lowest buyout of the last scan that saw each item (prices "
                    f"seen in the last {res.get('maxAgeDays', 30)} days); listings may have sold or changed since.")
        lines = []
        for it in items:
            seen = it.get("seen")
            lines.append(f"- [{it.get('name')}] (item {it.get('id')}) — {it.get('price', '?')}"
                         + (f" | seen {seen}" if seen else "")
                         + (f", {it['available']} available" if it.get("available") else ""))
        for m in res.get("missing") or []:
            ident = f"[{m.get('name')}]" if m.get("name") else "?"
            if m.get("id"):
                ident += f" (item {m['id']})"
            lines.append(f"- {ident}: {m.get('why') or 'no price'}")
        if not lines:
            lines.append("No prices found.")
        if res.get("ahOpen"):
            lines.append("The auction house window is open: the game offers to search the items you recommend in "
                         "Auctionator.")
        return "\n".join(head + lines)
    head.append(f"{res.get('dbItems', '?')} items in the price data, {res.get('matched', len(items))} match the filters, "
                f"showing {len(items)} (sorted by {res.get('sort', 'upgrade')}). A price is the lowest buyout of the last "
                "scan that saw the item; listings may have sold or changed since.")
    if res.get("upgradeMethod"):
        head.append(f"Upgrade check: {res['upgradeMethod']}.")
    if res.get("skipped"):
        head.append("Left out: " + ", ".join(f"{n} {why}" for why, n in sorted(res["skipped"].items()) if n) + ".")
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
        lines.append("The auction house window is open: the game offers to search the items you recommend in Auctionator.")
    return "\n".join(head + lines)


def validate_vendor(args: dict, snapshot: dict) -> tuple[dict | None, str]:
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
        value = int(b["sellCopper"]) * int(b.get("count") or 1)
        total += value
        picked.append({"id": b.get("id"), "name": b.get("item"), "count": b.get("count"), "price": money(value)})
    if not picked:
        return None, "Rejected: nothing to sell. " + "; ".join(problems)
    action = {"id": next(_ids), "op": "vendor_sell", "addon": "Vendor", "items": picked, "total": money(total),
              "description": reason}
    note = (" Skipped: " + "; ".join(problems)) if problems else ""
    return action, f"Sale of {len(picked)} item types for {money(total)} proposed; waiting for the player to accept.{note}"


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
    return mark, f"Marked on the map: {title} ({mark['x']}, {mark['y']})."


def run_fetch(url: str) -> str:
    from .web import fetch
    return fetch(url)

_ids = itertools.count(1)


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
