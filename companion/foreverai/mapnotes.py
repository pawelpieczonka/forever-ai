"""What the player's ForeverMapNotes add-on knows about the world: rare creatures and what they drop, ore veins, herbs,
chests, fishing pools, town NPCs, library books, boats and zeppelins, flight masters, the levels of the zones.

Its tables are private to it in the game, so the app reads its data files from the player's AddOns folder (nothing of
it is shipped with ForeverAI). Coordinates there are packed as XXXYYY, tenths of a percent of the zone map."""

from __future__ import annotations

import re
from pathlib import Path

from . import luadata
from .ways import MAP_IDS, map_of, on_this_map, region_of

ADDON = "ForeverMapNotes"
ORES = ("copper", "tin", "silver", "iron", "gold", "mithril", "truesilver", "darkiron", "thorium", "richthorium",
        "incendicite", "bloodstone")
CHESTS = ("chest", "strongbox")
NPC_KINDS = {"classtrainer": "class trainer", "proftrainer": "profession trainer", "inn": "innkeeper", "bank": "bank",
             "auction": "auction house", "mailbox": "mailbox", "repair": "repair", "stable": "stable master"}
WHAT = ("rares", "ore", "herbs", "chests", "fishing", "npcs", "books", "travel")
QUALITY = {0: "poor", 1: "common", 2: "uncommon", 3: "rare", 4: "epic"}
ARMOR = {1: "cloth", 2: "leather", 3: "mail", 4: "plate", 6: "shield"}
WEAPON = {0: "one-handed axe", 1: "two-handed axe", 2: "bow", 3: "gun", 4: "one-handed mace", 5: "two-handed mace",
          6: "polearm", 7: "one-handed sword", 8: "two-handed sword", 10: "staff", 13: "fist weapon", 15: "dagger",
          16: "thrown", 18: "crossbow", 19: "wand", 20: "fishing pole"}
SLOT = {1: "head", 2: "neck", 3: "shoulder", 5: "chest", 6: "waist", 7: "legs", 8: "feet", 9: "wrist", 10: "hands",
        11: "ring", 12: "trinket", 16: "cloak", 20: "chest", 23: "off-hand"}
CELL = 8.0  # map units: spots this close together are one place to gather at


def unpack(packed) -> list[tuple[float, float]]:
    """"XXXYYYXXXYYY" -> [(x, y), ...] in map percent."""
    text = str(packed or "")
    return [(int(text[i:i + 3]) / 10.0, int(text[i + 3:i + 6]) / 10.0) for i in range(0, len(text) - 5, 6)
            if text[i:i + 6].isdigit()]


def areas(points: list[tuple[float, float]], most: int = 2) -> list[tuple[float, float, int]]:
    """The places where the most points are together: (x, y, how many), the fullest first."""
    cells: dict[tuple[int, int], list] = {}
    for x, y in points:
        cells.setdefault((int(x // CELL), int(y // CELL)), []).append((x, y))
    out = []
    for group in sorted(cells.values(), key=len, reverse=True)[:most]:
        out.append((sum(p[0] for p in group) / len(group), sum(p[1] for p in group) / len(group), len(group)))
    return out


class MapNotes:
    """The data files of the add-on, each read when first asked for."""

    FILES = {"rares": ("Rares", "RareData"), "items": ("Rares", "ItemData"), "nodes": ("Nodes", "NodeData"),
             "fish": ("Fishing", "FishData"), "npcs": ("NPCs", "NPCData"), "books": ("Books", "BookData"),
             "transport": ("Transport", "TransportData"), "flight": ("Flight", "FlightData"),
             "levels": ("ZoneLevels", "ZoneLevels")}

    def __init__(self, addons_dir):
        self.folder = Path(addons_dir) / ADDON / "Data" if addons_dir else None
        self._tables: dict[str, object] = {}

    def installed(self) -> bool:
        return bool(self.folder and (self.folder / "Rares.lua").is_file())

    def table(self, key: str):
        if key not in self._tables:
            file, name = self.FILES[key]
            data = None
            if self.folder:
                try:
                    data = luadata.assigned(luadata.read(self.folder / f"{file}.lua"), name)
                except luadata.LuaError:
                    data = None
            self._tables[key] = data if data is not None else {}
        return self._tables[key]

    # ---------------------------------------------------------------- for the route planner
    def zone_levels(self) -> dict[str, tuple[int, int]]:
        """The quest levels of each zone on this server: {"Westfall": (9, 20)}."""
        out = {}
        for map_id, span in (self.table("levels") or {}).items():
            if map_id in MAP_IDS and isinstance(span, list) and len(span) >= 2:
                out[MAP_IDS[map_id]] = (int(span[0]), int(span[1]))
        return out

    def transports(self, faction: str) -> list[dict]:
        """Boats and zeppelins: {"kind", "seconds", "a": (zone, (x, y), port), "b": ...} for a faction (A or H)."""
        out = []
        for row in self.table("transport") or []:
            if not isinstance(row, list) or len(row) < 5 or faction not in str(row[1]):
                continue
            ends = []
            for end in row[3:5]:
                spot = unpack(end[1])[:1] if isinstance(end, list) and len(end) >= 3 else []
                if spot and end[0] in MAP_IDS:
                    ends.append((region_of(MAP_IDS[end[0]], spot[0]), spot[0], str(end[2])))
            if len(ends) == 2:
                out.append({"kind": str(row[0]), "seconds": row[2], "a": ends[0], "b": ends[1]})
        return out

    def flight_masters(self, faction: str) -> list[dict]:
        """Every flight master of a faction: {"name", "zone", "x", "y"}."""
        out = []
        for row in self.table("flight") or []:
            if isinstance(row, list) and len(row) >= 5 and faction in str(row[3]) and row[1] in MAP_IDS:
                spot = unpack(row[2])[:1]
                if spot:
                    name = row[4][0] if isinstance(row[4], list) and row[4] else str(row[0])
                    out.append({"name": str(name), "zone": MAP_IDS[row[1]], "x": spot[0][0], "y": spot[0][1]})
        return out

    # ---------------------------------------------------------------- lines for the model
    def _item(self, item_id) -> str:
        row = (self.table("items") or {}).get(item_id)
        if not isinstance(row, list) or len(row) < 7:
            return f"item {item_id}"
        kind = ""
        if row[0] == 2:
            kind = WEAPON.get(row[1], "weapon")
        elif row[0] == 4:
            kind = " ".join(x for x in (ARMOR.get(row[1], ""), SLOT.get(row[2], "")) if x) or "armor"
        about = ", ".join(x for x in (QUALITY.get(row[4], ""), kind, f"item level {row[5]}" if row[5] else "") if x)
        return f"[{row[6]}]" + (f" ({about})" if about else "")

    def rares(self, zone: str) -> list[str]:
        map_id = _map_id(zone)
        out = []
        for row in (self.table("rares") or {}).values():
            spots = unpack((row.get("spawns") or {}).get(map_id)) if isinstance(row, dict) else []
            if not spots:
                continue
            low, high = (row.get("lvl") or [0, 0])[:2]
            level = f"level {low}" if low == high else f"level {low}-{high}"
            places = "; ".join(f"{x:.1f}, {y:.1f}" for x, y in spots[:4]) + (f" and {len(spots) - 4} more" if len(spots) > 4 else "")
            drops = sorted((d for d in row.get("drops") or [] if isinstance(d, list) and len(d) >= 2),
                           key=lambda d: -d[1])[:5]
            loot = "; drops: " + ", ".join(f"{self._item(d[0])} {d[1]:g}%" for d in drops) if drops else ""
            out.append((low, f"{row['names'][0]} ({level}{', elite' if row.get('elite') else ''}, rare) — {zone} "
                             f"{places}{loot}"))
        return [line for _, line in sorted(out)]

    def _spots(self, table: str, zone: str, kinds=None) -> list[str]:
        out = []
        for kind, packed in sorted(((self.table(table) or {}).get(_map_id(zone)) or {}).items()):
            if kinds is not None and (kind in kinds) is False:
                continue
            spots = unpack(packed)
            if spots:
                best = "; ".join(f"{x:.1f}, {y:.1f} ({n} close together)" if n > 1 else f"{x:.1f}, {y:.1f}"
                                 for x, y, n in areas(spots))
                out.append(f"{kind}: {len(spots)} spawn spots in {zone}; the most around {best}")
        return out

    def nodes(self, zone: str, what: str) -> list[str]:
        every = (self.table("nodes") or {}).get(_map_id(zone)) or {}
        herbs = [k for k in every if k not in ORES and k not in CHESTS]
        kinds = {"ore": ORES, "chests": CHESTS, "herbs": herbs}.get(what)
        return self._spots("nodes", zone, kinds)

    def fishing(self, zone: str) -> list[str]:
        return self._spots("fish", zone)

    def best_zones(self, kind: str) -> list[str]:
        """Where a kind of node (copper, peacebloom, firefin...) has the most spawn spots."""
        found = []
        for table in ("nodes", "fish"):
            for map_id, kinds in (self.table(table) or {}).items():
                spots = unpack(kinds.get(kind)) if isinstance(kinds, dict) else []
                if spots and map_id in MAP_IDS:
                    found.append((len(spots), MAP_IDS[map_id], spots))
        levels = self.zone_levels()
        out = []
        for count, zone, spots in sorted(found, key=lambda f: -f[0])[:6]:
            x, y, n = areas(spots, 1)[0]
            span = levels.get(zone)
            out.append(f"{zone}{f' (quests level {span[0]}-{span[1]})' if span else ''}: {count} spawn spots of {kind}; "
                       f"the most around {x:.1f}, {y:.1f} ({n} close together)")
        return out

    def kinds(self) -> list[str]:
        """Every kind of node and pool the add-on lists."""
        out = set()
        for table in ("nodes", "fish"):
            for kinds in (self.table(table) or {}).values():
                out.update(kinds if isinstance(kinds, dict) else ())
        return sorted(out)

    def npc_spots(self, zone: str, faction: str, label: str) -> list[tuple[str, float, float]]:
        """Every town NPC of a zone of one kind, as npcs() names the kinds ("repair", "bank", "leatherworking
        trainer"...): (name, x, y) on this server's map."""
        out = []
        for row in (self.table("npcs") or {}).get(_map_id(zone)) or []:
            if not isinstance(row, list) or len(row) < 3 or faction not in str(row[1]):
                continue
            kind, extra = str(row[0]), str(row[4]) if len(row) > 4 else ""
            name = NPC_KINDS.get(kind, kind)
            if kind == "classtrainer":
                name = f"{extra.lower()} trainer"
            elif kind == "proftrainer":
                name = f"{extra} trainer"
            if name.lower() != label.lower():
                continue
            spot = [on_this_map(zone, *p) for p in unpack(row[2])[:1]]
            if spot:
                out.append((str(row[3]) if len(row) > 3 else "", spot[0][0], spot[0][1]))
        return out

    def npcs(self, zone: str, faction: str, cls: str = "", near=None) -> list[str]:
        """Town NPCs of a zone for a faction: the trainer of the class, profession trainers, inn, bank, auction house,
        mailboxes, stable; of each kind the ones nearest to ``near`` (x, y) when the player is in the zone."""
        groups: dict[str, list] = {}
        for row in (self.table("npcs") or {}).get(_map_id(zone)) or []:
            if not isinstance(row, list) or len(row) < 3 or faction not in str(row[1]):
                continue
            kind, extra = str(row[0]), str(row[4]) if len(row) > 4 else ""
            if kind == "classtrainer" and cls and extra.upper() != cls.upper():
                continue
            spot = [on_this_map(zone, *p) for p in unpack(row[2])[:1]]  # this list is on other clients' maps
            if not spot:
                continue
            label = NPC_KINDS.get(kind, kind)
            if kind == "classtrainer":
                label = f"{extra.lower()} trainer"
            elif kind == "proftrainer":
                label = f"{extra} trainer"
            groups.setdefault(label, []).append((spot[0], str(row[3]) if len(row) > 3 else ""))
        out = []
        for label in sorted(groups):
            rows = groups[label]
            if near:
                rows = sorted(rows, key=lambda r: (r[0][0] - near[0]) ** 2 + (r[0][1] - near[1]) ** 2)
            shown = "; ".join(f"{name + ' ' if name else ''}{zone} {x:.1f}, {y:.1f}" for (x, y), name in rows[:2])
            out.append(f"{label}: {shown}" + (f" (and {len(rows) - 2} more)" if len(rows) > 2 else ""))
        return out

    def books(self, faction: str, zone: str = "") -> list[str]:
        data = self.table("books") or {}
        out = []
        for row in data.get("books") or [] if isinstance(data, dict) else []:
            if not isinstance(row, dict) or (row.get("faction") and row["faction"] != faction):
                continue
            where = []
            for spot in row.get("spots") or []:
                if isinstance(spot, list) and len(spot) >= 2 and spot[0] in MAP_IDS:
                    where += [f"{MAP_IDS[spot[0]]} {x:.1f}, {y:.1f}" for x, y in unpack(spot[1])[:1]]
            if zone and not any(w.lower().startswith(zone.lower() + " ") for w in where):
                continue
            note = row.get("note")
            note = note[0] if isinstance(note, list) and note else ""
            out.append(f"[{row.get('name')}] — {'; '.join(where) or 'place not known'}"
                       + (" (approximate)" if row.get("approx") else "") + (f": {note}" if note else ""))
        if out:
            for lib in data.get("librarians") or []:
                if isinstance(lib, dict) and lib.get("faction") == faction and lib.get("map") in MAP_IDS:
                    spot = unpack(lib.get("p"))[:1]
                    if spot:
                        out.append(f"Library books are turned in to {lib.get('name')} — {MAP_IDS[lib['map']]} "
                                   f"{spot[0][0]:.1f}, {spot[0][1]:.1f}")
        return out

    def travel(self, zone: str, faction: str, links: list | None = None) -> list[str]:
        """Rides out of a zone and its flight masters. ``links``: the ways the route planner has (from the map add-on
        that knows this server's boats); without them, this add-on's own list of boats and zeppelins."""
        out = []
        for link in links or []:
            if _same(map_of(link["a"]), zone) and link["kind"] in ("boat", "zeppelin", "tram", "portal") and link.get("via"):
                out.append(f"{link['title']} — {zone} {link['via'][0]:.1f}, {link['via'][1]:.1f} (to {link['b']})")
        for t in self.transports(faction) if not out else ():
            for here, there in ((t["a"], t["b"]), (t["b"], t["a"])):
                if _same(here[0], zone):
                    out.append(f"{t['kind']} from {here[2]} ({zone} {here[1][0]:.1f}, {here[1][1]:.1f}) to {there[2]}, "
                               f"{there[0]} ({t['seconds']} s trip)")
        for f in self.flight_masters(faction):
            if _same(f["zone"], zone):
                out.append(f"flight master {f['name']} — {zone} {f['x']:.1f}, {f['y']:.1f}")
        return out


def _same(a: str, b: str) -> bool:
    return str(a).strip().lower() == str(b).strip().lower()


def _map_id(zone: str):
    low = str(zone).strip().lower()
    return next((i for i, name in MAP_IDS.items() if name.lower() == low), None)


def zone_named(zone: str) -> str | None:
    """A zone as the maps name it ("stormwind" -> Stormwind City); None when no map has that name."""
    low = str(zone).strip().lower()
    if not low:
        return None
    names = list(MAP_IDS.values())
    exact = [n for n in names if n.lower() == low]
    starts = exact or [n for n in names if n.lower().startswith(low) or low.startswith(n.lower())
                       or n.lower().removeprefix("the ") == low]
    return starts[0] if len(starts) == 1 else None


_PLACED = re.compile(r"^(?:[\w ]+: )?(?P<who>[^<\[—]+?)(?: <[^>]*>)?(?: \[[^\]]*\])* — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), "
                     r"(?P<y>\d+(?:\.\d+)?)")


def _npcs_misplaced(notes: MapNotes, zone: str, snapshot: dict) -> bool:
    """Whether the add-on's town NPCs of a zone stand elsewhere than the game says. Its NPC list was made from another
    client's maps; where this server redrew a map every position is off (Stormwind's are moved onto the new map,
    ways.REDRAWN; this catches a map nobody has fitted yet). The game data of the question holds the real places
    of the NPCs around the player: the same names are compared."""
    real = {}
    for key in ("services", "zoneNPCs"):
        for line in (snapshot or {}).get(key) or []:
            m = _PLACED.match(str(line))
            if m and _same(m.group("zone"), zone):
                real[m.group("who").strip().lower()] = (float(m.group("x")), float(m.group("y")))
    off = near = 0
    for row in (notes.table("npcs") or {}).get(_map_id(zone)) or []:
        spot = [on_this_map(zone, *p) for p in unpack(row[2])[:1]] if isinstance(row, list) and len(row) > 3 else []
        at = real.get(str(row[3]).lower()) if spot else None
        if at:
            far = abs(at[0] - spot[0][0]) + abs(at[1] - spot[0][1]) > 4.0
            off, near = off + far, near + (not far)
    return off + near >= 3 and off > near


def format_map_notes(notes: MapNotes, zone: str, what: str, snapshot: dict, links: list | None = None,
                     near_xy: tuple[float, float] | None = None) -> tuple[str, bool]:
    """The map_notes tool: text for the model, and whether it is an error. ``near_xy``: of each kind of town NPC the
    ones nearest to this place of the zone (else to the player, when they are in it)."""
    if not notes.installed():
        return ("The player does not have the ForeverMapNotes add-on installed, so this cannot be read. Answer from "
                "the game data and the web."), True
    player = (snapshot or {}).get("player") or {}
    faction = "H" if str(player.get("faction") or "").lower().startswith("h") else "A"
    what = str(what or "").strip().lower()
    kinds = notes.kinds()
    asked = str(zone or "").strip()
    if what in kinds and not asked:
        lines = notes.best_zones(what)
        return (f"Zones with the most {what} (spawn spots, not all up at once):\n" + "\n".join(f"- {x}" for x in lines)
                if lines else f"No zone lists {what}."), False
    name = zone_named(asked or str(player.get("map") or player.get("zone") or ""))
    if not name:
        return f"No map is called '{asked}'. Give the English name of a zone or city.", True
    here = _same(name, str(player.get("map") or player.get("zone") or ""))
    near = near_xy
    if here and near is None:
        try:
            near = tuple(float(v) for v in str(player.get("coords") or "").split(","))[:2]
            near = near if len(near) == 2 else None
        except ValueError:
            near = None
    cls = str(player.get("class") or "").replace(" ", "").upper()
    span = notes.zone_levels().get(name)
    parts: list[tuple[str, list[str]]] = []
    misplaced = what in ("", "npcs") and _npcs_misplaced(notes, name, snapshot)
    if what in kinds:
        table = "fish" if what in (notes.table("fish") or {}).get(_map_id(name), {}) else "nodes"
        parts.append((what, notes._spots(table, name, (what,))))
    else:
        want = [what] if what in WHAT else list(WHAT)
        for kind in want:
            if kind == "npcs" and misplaced:
                continue
            lines = {"rares": lambda: notes.rares(name), "ore": lambda: notes.nodes(name, "ore"),
                     "herbs": lambda: notes.nodes(name, "herbs"), "chests": lambda: notes.nodes(name, "chests"),
                     "fishing": lambda: notes.fishing(name), "npcs": lambda: notes.npcs(name, faction, cls, near),
                     "books": lambda: notes.books(faction, name if what != "books" or asked else ""),
                     "travel": lambda: notes.travel(name, faction, links)}[kind]()
            parts.append((kind, lines))
    head = f"{name}{f' (quests level {span[0]}-{span[1]})' if span else ''}, from the player's ForeverMapNotes add-on"
    out = [head + " (spawn spots are where a thing can be, not where it is now):"]
    for kind, lines in parts:
        if lines:
            out.append(f"{kind}:")
            out += [f"- {line}" for line in lines]
    if misplaced:
        out.append(f"Town NPCs: the add-on draws them on another map of {name} than this server has, so its positions "
                   "are not given. Use the services and zoneNPCs sections of the game data"
                   + ("." if here else " once the player is there."))
    if len(out) == 1:
        return f"{head}: nothing listed" + (f" for {what}" if what else "") + ".", False
    if what and what not in WHAT and what not in kinds:
        out.append(f"('{what}' is not a kind it knows; kinds: {', '.join(WHAT)}, or a node: {', '.join(kinds)})")
    return "\n".join(out), False
