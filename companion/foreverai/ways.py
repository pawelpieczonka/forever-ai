"""What the game data does not hold about getting around: the ways between maps (portals, gates) and the parts of a
map that have no ground way to the rest of it. The route planner uses it, so that a route does not depend on what
the model remembers of the world: a known way is filled in by itself, and a cut-off part of a map counts as a zone
of its own, under its own name.

The list is short and grows with what has been checked. A way that is not here is named by the model."""

from __future__ import annotations

import heapq
import math
import re
from pathlib import Path

# a part of a map with no ground way to the rest of it: {"name", "map", "box": (x1, y1, x2, y2)}
REGIONS = [
    # the port at the foot of the tree: joined to the rest of Teldrassil only through Darnassus
    {"name": "Rut'theran Village", "map": "Teldrassil", "box": (52.0, 86.0, 62.0, 100.0)},
    # Sardor Isle off the coast of Feralas: a boat from the Forgotten Coast (the map add-on lists both piers)
    {"name": "Feathermoon Stronghold", "map": "Feralas", "box": (24.0, 36.0, 36.0, 56.0)},
]

# a way between two zones (or cut-off parts): each end is (zone, x, y, what the way is called when leaving from it,
# and True when the spot is near, not exact).
WAYS = [
    (("Rut'theran Village", 55.0, 89.0, "Portal to Darnassus"),
     ("Darnassus", 30.0, 41.0, "Portal to Rut'theran Village")),
    (("Darnassus", 88.0, 44.0, "The gate of Darnassus", True),
     ("Teldrassil", 38.0, 51.0, "The gate of Darnassus", True)),
    (("Rut'theran Village", 58.4, 94.0, "Hippogryph or boat to Auberdine"),
     ("Darkshore", 36.3, 45.6, "Hippogryph or boat to Rut'theran Village", True)),
]


def region_of(map_name: str, xy) -> str:
    """The zone a place counts as: the cut-off part of its map it lies in, else the map itself."""
    if xy:
        for region in REGIONS:
            x1, y1, x2, y2 = region["box"]
            if region["map"] == map_name and x1 <= xy[0] <= x2 and y1 <= xy[1] <= y2:
                return region["name"]
    return map_name


def map_of(zone: str) -> str:
    """The map a zone is drawn on (a cut-off part is on its map)."""
    return next((region["map"] for region in REGIONS if region["name"] == zone), zone)


def known_way(a: str, b: str) -> dict | None:
    """The way from zone ``a`` straight into zone ``b``: {"via": (x, y) in a, "arrive": (x, y) in b, "title"}."""
    for ends in WAYS:
        for here, there in (ends, ends[::-1]):
            if here[0] == a and there[0] == b:
                return {"via": (here[1], here[2]), "arrive": (there[1], there[2]),
                        "title": here[3] + (" (approx.)" if len(here) > 4 and here[4] else "")}
    return None


def ways_from(a: str) -> list[tuple[str, dict]]:
    """The known ways out of zone ``a``: (the zone it leads to, the way)."""
    return [(there[0], known_way(a, there[0])) for ends in WAYS for here, there in (ends, ends[::-1]) if here[0] == a]


def path(a: str, b: str, longest: int = 4) -> list[str]:
    """The zones to pass on the way from ``a`` to ``b`` over known ways, ``b`` last; empty when no such way is known."""
    seen, paths = {a}, [[a]]
    while paths:
        zones = paths.pop(0)
        if zones[-1] == b:
            return zones[1:]
        if len(zones) > longest:
            continue
        for to, _ in ways_from(zones[-1]):
            if to not in seen:
                seen.add(to)
                paths.append(zones + [to])
    return []


# ---------------------------------------------------------------------------------------------------------------
# The world as a graph. A route that leaves a zone needs the way out of it: where the road crosses into the next zone,
# the pier of a boat, the tram, a portal, a flight between two flight masters the character knows. None of this is in
# the quest data. It comes from three places:
#   - the map add-on Leatrix Maps, when the player has it: its icons file lists every zone crossing, boat, zeppelin,
#     tram and portal of this client with coordinates (read from the player's own AddOns folder, never shipped);
#   - WAYS above, for what it does not cover;
#   - the game data of the question: the flight masters whose flight path the character knows, and how large each
#     zone map is.
# The planner asks for the cheapest way between two places; walking counts in meters, a ride costs a fixed amount.

RIDE = {"walk": 0, "gate": 0, "portal": 60, "flight": 250, "tram": 300, "boat": 500, "zeppelin": 500}
DEFAULT_SIZE = (4000.0, 2667.0)  # meters across a zone map whose size the game data does not give
CITY_SIZE = (1100.0, 733.0)  # ... and across a city, which is far smaller
CITIES = ("Stormwind City", "Ironforge", "Darnassus", "Orgrimmar", "Thunder Bluff", "Undercity")
UNKNOWN_WALK = 700.0  # meters, at least, for a walk in a zone from or to a place that is not known
UNKNOWN_SHARE = 0.3  # ... and this part of the zone's width: a third of the Barrens is not 700 m
# a capital of the other faction is no place to walk through (the map add-on draws the gates of all of them)
ENEMY_CITIES = {"A": ("Orgrimmar", "Thunder Bluff", "Undercity"), "H": ("Stormwind City", "Ironforge", "Darnassus")}

# A map this server draws anew (Stormwind, with its harbour). Add-ons whose data was made from another client's maps
# give places on the old map of the zone; on this server's map a place is at (x * a + b, y * c + d). Fitted on 20
# NPCs of Stormwind that the game's own quest database and such an add-on both list: it puts each within 0.1 of a map
# unit of where the game says it stands.
REDRAWN = {"Stormwind City": (0.7736, 19.694, 0.7750, 24.388)}
_PLACE = re.compile(r"(?P<zone>" + "|".join(re.escape(z) for z in REDRAWN) + r") (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)")


def on_this_map(zone: str, x: float, y: float) -> tuple[float, float]:
    """A place given on the old map of a zone, on the map this server has."""
    fit = REDRAWN.get(zone)
    return (round(x * fit[0] + fit[1], 1), round(y * fit[2] + fit[3], 1)) if fit else (x, y)


def places_on_this_map(text: str) -> str:
    """A line from such an add-on with its "Zone x, y" places moved onto this server's maps."""
    def moved(m):
        x, y = on_this_map(m.group("zone"), float(m.group("x")), float(m.group("y")))
        return f"{m.group('zone')} {x:.1f}, {y:.1f}"
    return _PLACE.sub(moved, text)


# The game's number of each zone map (other add-ons' data files name maps by it).
MAP_IDS = {
    1411: "Durotar", 1412: "Mulgore", 1413: "The Barrens", 1416: "Alterac Mountains", 1417: "Arathi Highlands",
    1418: "Badlands", 1419: "Blasted Lands", 1420: "Tirisfal Glades", 1421: "Silverpine Forest",
    1422: "Western Plaguelands", 1423: "Eastern Plaguelands", 1424: "Hillsbrad Foothills", 1425: "The Hinterlands",
    1426: "Dun Morogh", 1427: "Searing Gorge", 1428: "Burning Steppes", 1429: "Elwynn Forest", 1430: "Deadwind Pass",
    1431: "Duskwood", 1432: "Loch Modan", 1433: "Redridge Mountains", 1434: "Stranglethorn Vale",
    1435: "Swamp of Sorrows", 1436: "Westfall", 1437: "Wetlands", 1438: "Teldrassil", 1439: "Darkshore",
    1440: "Ashenvale", 1441: "Thousand Needles", 1442: "Stonetalon Mountains", 1443: "Desolace", 1444: "Feralas",
    1445: "Dustwallow Marsh", 1446: "Tanaris", 1447: "Azshara", 1448: "Felwood", 1449: "Un'Goro Crater",
    1450: "Moonglade", 1451: "Silithus", 1452: "Winterspring", 1453: "Stormwind City", 1454: "Orgrimmar",
    1455: "Ironforge", 1456: "Thunder Bluff", 1457: "Darnassus", 1458: "Undercity",
    2521: "Zephras Isle", 2548: "Riverglades",  # this server's own zones
}
_zone_levels: dict[str, int] = {}
_transports: dict[str, list[dict]] = {}


def use_transports(by_faction: dict | None) -> None:
    """Boats and zeppelins from another add-on of the player ({"A": [{"kind", "a": (zone, (x, y), port), "b": ...}]}),
    used when the map add-on with every crossing is not installed."""
    _transports.clear()
    _transports.update(by_faction or {})


def _transport_links(faction: str) -> list[dict]:
    out = []
    for t in _transports.get(faction) or []:
        for here, there in ((t["a"], t["b"]), (t["b"], t["a"])):
            kind = "zeppelin" if t.get("kind") == "zeppelin" else "boat"
            out.append({"a": here[0], "via": here[1], "kind": kind, "b": there[0], "arrive": there[1],
                        "title": f"{kind.capitalize()} to {there[2]}, {map_of(there[0])}"})
    return out


def use_zone_levels(levels: dict | None) -> None:
    """The level each zone starts at on this server, when an add-on of the player lists them ({"Westfall": (9, 20)});
    ZONE_LEVELS is the fallback."""
    _zone_levels.clear()
    for zone, span in (levels or {}).items():
        _zone_levels[zone] = int(span[0])
# The levels a zone is for. A way on foot through a zone far above the character is no way for them: it costs
# DANGER more, so another way is taken when there is one (not Redridge to Loch Modan through the Burning Steppes).
ZONE_LEVELS = {
    "Elwynn Forest": 1, "Dun Morogh": 1, "Teldrassil": 1, "Durotar": 1, "Mulgore": 1, "Tirisfal Glades": 1,
    "Westfall": 10, "Loch Modan": 10, "Darkshore": 10, "The Barrens": 10, "Silverpine Forest": 10,
    "Redridge Mountains": 15, "Stonetalon Mountains": 15, "Ashenvale": 18, "Duskwood": 18, "Hillsbrad Foothills": 20,
    "Wetlands": 20, "Thousand Needles": 25, "Alterac Mountains": 30, "Arathi Highlands": 30, "Desolace": 30,
    "Stranglethorn Vale": 30, "Dustwallow Marsh": 35, "Badlands": 35, "Swamp of Sorrows": 35, "The Hinterlands": 40,
    "Tanaris": 40, "Feralas": 40, "Searing Gorge": 43, "Azshara": 45, "Blasted Lands": 45, "Felwood": 48,
    "Un'Goro Crater": 48, "Burning Steppes": 50, "Western Plaguelands": 51, "Eastern Plaguelands": 53,
    "Winterspring": 53, "Deadwind Pass": 55, "Silithus": 55,
}
DANGER, DANGER_LEVELS = 30000.0, 8  # the extra cost of crossing a zone that starts this many levels above the character

_BLOCK = re.compile(r"^\s*--\[\[(?P<name>.+?)\]\]\s*\[(?P<id>\d+)\]\s*=\s*\{")
_ICON = re.compile(r'^\s*(?P<off>--)?\s*\{\s*"(?P<kind>Arrow|Travel[AHN]|Portal[AHN])"\s*,\s*(?P<x>[\d.]+)\s*,\s*'
                   r"(?P<y>[\d.]+)\s*,(?P<rest>.*)\}\s*,?\s*$")
_QUOTED = re.compile(r'"([^"]*)"')
_ONTO = re.compile(r"\band onto ([^()]+)")
_BLOCKED = ("you will die", "requires key", "horde only")
_icon_links: dict[str, list[dict]] = {}
_icons: list[dict] | None = None


def _fields(text: str) -> list[str]:
    """The fields of one Lua table line, split at the commas that are not inside a string."""
    out, cur, quoted = [], "", False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return out + [cur.strip()]


def read_icons(text: str) -> list[dict]:
    """The crossings and rides of a Leatrix Maps icons file: {"map", "zone", "kind": Arrow|Travel|Portal, "side":
    A|H|N|"", "x", "y", "title", "note", "dest": map id, "off": the line is commented out there}."""
    out, zone, map_id = [], None, None
    for line in text.splitlines():
        block = _BLOCK.match(line)
        if block:
            zone, map_id = block.group("name").strip(), int(block.group("id"))
            continue
        m = _ICON.match(line)
        if not m or zone is None:
            continue
        fields = _fields(m.group("rest"))
        if len(fields) < 3 or not fields[-1].strip().isdigit():
            continue
        kind = m.group("kind")
        out.append({"map": map_id, "zone": zone, "kind": kind.rstrip("AHN") if kind != "Arrow" else kind,
                    "side": kind[-1] if kind != "Arrow" else "", "x": float(m.group("x")), "y": float(m.group("y")),
                    "title": "".join(_QUOTED.findall(fields[0])).strip(),
                    "note": "".join(_QUOTED.findall(fields[1])).strip() if len(fields) > 1 else "",
                    "dest": int(fields[-1]), "off": bool(m.group("off"))})
    return out


_DUNGEON = re.compile(r'\{\s*"(?P<kind>Dungeon|Raid)"\s*,\s*(?P<x>[\d.]+)\s*,\s*(?P<y>[\d.]+)\s*,\s*L\["(?P<name>[^"]+)"\]'
                      r'[^{}]*?,\s*(?P<lo>\d+)\s*,\s*(?P<hi>\d+)\s*\}')
_dungeons: list[dict] = []


def read_dungeons(text: str) -> list[dict]:
    """The dungeon and raid entrances of a Leatrix Maps icons file: {"name", "kind", "zone", "x", "y", "min", "max"}."""
    out, zone = [], None
    for line in text.splitlines():
        block = _BLOCK.match(line)
        if block:
            zone = block.group("name").strip()
            continue
        if zone is None or line.lstrip().startswith("--"):
            continue
        for m in _DUNGEON.finditer(line):
            out.append({"name": m.group("name"), "kind": m.group("kind").lower(), "zone": zone, "x": float(m.group("x")),
                        "y": float(m.group("y")), "min": int(m.group("lo")), "max": int(m.group("hi"))})
    return out


def dungeons(level: int | None = None, zone: str | None = None) -> list[str]:
    """The entrances the player's map add-on lists, as lines for the model: the ones for about this level (a few
    below it to a few above), or the ones in a zone."""
    out = []
    for d in _dungeons:
        if zone is not None and d["zone"].lower() != zone.lower():
            continue
        if level is not None and not (d["min"] - 4 <= level <= d["max"] + 1):
            continue
        out.append(f"{d['name']} ({d['kind']}, levels {d['min']}-{d['max']}): entrance at {d['zone']} {d['x']:g}, {d['y']:g}")
    return out


def paths_inside(zone: str) -> list[str]:
    """Where a path inside a zone begins, as the player's map add-on marks it (the way down to a cove, up a cliff):
    a place that looks near on the map and is reached only from there."""
    out = []
    for i in _icons or []:
        if i["kind"] == "Arrow" and i["dest"] == i["map"] and i["zone"] == map_of(zone) and not i["off"]:
            out.append(f"{i['title']}: the path starts at {i['zone']} {i['x']:g}, {i['y']:g}"
                       + (f" ({i['note']})" if i["note"] else ""))
    return out


def use_icons(path) -> int:
    """Read the crossings from the player's Leatrix Maps (None or a missing file: none). Returns how many were read."""
    global _icons, _dungeons
    _icon_links.clear()
    _icons, _dungeons = None, []
    try:
        if path and Path(path).is_file():
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            _icons, _dungeons = read_icons(text), read_dungeons(text)
    except OSError:
        _icons, _dungeons = None, []
    return len(_icons or [])


def _links_from_icons(faction: str) -> list[dict]:
    """One-way links between zones from the icons, for a faction ("A" or "H")."""
    if faction in _icon_links:
        return _icon_links[faction]
    icons = [i for i in _icons or [] if i["side"] in ("", "N", faction)
             and not any(word in i["note"].lower() for word in _BLOCKED)]
    names = {i["map"]: i["zone"] for i in _icons or []}
    links = []
    for icon in icons:
        if icon["dest"] not in names:
            continue
        ride = icon["kind"] != "Arrow"
        if icon["dest"] == icon["map"]:
            # a boat between two parts of one map (the coast of Feralas and Feathermoon): a way when its piers lie in
            # different parts of the map (REGIONS); an arrow within a map is a hint for the eye, not a way
            here = region_of(icon["zone"], (icon["x"], icon["y"]))
            far = next((i for i in icons if i["map"] == icon["map"] and i["dest"] == icon["map"] and i["kind"] != "Arrow"
                        and region_of(i["zone"], (i["x"], i["y"])) != here), None) if ride else None
            if far:
                links.append({"a": here, "via": (icon["x"], icon["y"]), "title": icon["title"], "kind": "boat",
                              "b": region_of(far["zone"], (far["x"], far["y"])), "arrive": (far["x"], far["y"])})
            continue
        same = [i for i in icons if i["map"] == icon["map"] and i["dest"] == icon["dest"]
                and (i["kind"] != "Arrow") == ride]
        back = [i for i in icons if i["map"] == icon["dest"] and i["dest"] == icon["map"]
                and (i["kind"] != "Arrow") == ride]
        if not back and ride:  # a boat that goes on: it lands at the pier of the harbour it comes to
            back = [i for i in icons if i["map"] == icon["dest"] and i["kind"] != "Arrow"]
        other = next((i for i in back if icon["note"] and i["note"] == icon["note"]), None)
        if other is None and back:
            other = back[min(same.index(icon), len(back) - 1)]
        one_sided = None
        if other is None and not ride and icon["note"]:
            # a passage the other zone shows only as the way to a third (the Timbermaw Hold tunnel: Felwood marks its
            # mouth as the way to Winterspring, Moonglade as the way to Felwood): the same named passage is its far end
            other = one_sided = next((i for i in icons if i["map"] == icon["dest"] and i["kind"] == "Arrow"
                                      and i["note"] == icon["note"]), None)
        title = icon["title"]
        if not ride:
            title = f"To {names[icon['dest']]}" + (f" ({icon['note']})" if icon["note"] else "")
        low = icon["title"].lower()
        kind = ("walk" if not ride else "tram" if "tram" in low else "zeppelin" if "zeppelin" in low
                else "portal" if icon["kind"] == "Portal" else "boat")
        # a line the map add-on has switched off is a guess only when nothing on the other side confirms it: a
        # crossing whose far side is marked there, leading back here, is a real one
        unsure = icon["off"] and not (other and not other["off"] and one_sided is None)
        here = region_of(icon["zone"], (icon["x"], icon["y"]))
        links.append({"a": here, "via": (icon["x"], icon["y"]),
                      "title": title + (" (approx.)" if unsure else ""), "kind": kind,
                      "b": region_of(names[icon["dest"]], (other["x"], other["y"])) if other else names[icon["dest"]],
                      "arrive": (other["x"], other["y"]) if other else None})
        if one_sided:  # and the way back through it, which no line of the file gives
            links.append({"a": region_of(names[icon["dest"]], (other["x"], other["y"])), "via": (other["x"], other["y"]),
                          "title": f"To {icon['zone']} ({icon['note']})", "kind": "walk", "b": here,
                          "arrive": (icon["x"], icon["y"])})
    # a boat that goes on to a second harbour ("Boat to Southshore, Hillsbrad and onto Auberdine, Darkshore"): staying
    # on board is one ride there, not a boat and then another from the same pier
    for link in list(links):
        onto = _ONTO.search(link["title"]) if link["kind"] != "walk" else None
        if not onto:
            continue
        want = onto.group(1).split(",")[-1].strip().lower()
        for nxt in links:
            there = map_of(nxt["b"]).lower()
            if (nxt["a"] == link["b"] and nxt["kind"] == link["kind"] and nxt["b"] != link["a"]
                    and (there.startswith(want) or want.startswith(there))):
                links.append({**link, "b": nxt["b"], "arrive": nxt["arrive"],
                              "title": link["title"] + " (stay on board at the first stop)"})
                break
    _icon_links[faction] = links
    return links


def _seed_links() -> list[dict]:
    out = []
    for ends in WAYS:
        for here, there in (ends, ends[::-1]):
            low = here[3].lower()
            kind = "boat" if "boat" in low else "portal" if "portal" in low else "gate"
            out.append({"a": here[0], "via": (here[1], here[2]), "kind": kind, "b": there[0],
                        "arrive": (there[1], there[2]),
                        "title": here[3] + (" (approx.)" if len(here) > 4 and here[4] else "")})
    return out


_FLIGHT_MASTER = re.compile(r"^(?P<who>.+?) \[(?P<tag>[^\]]*)\] — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), "
                            r"(?P<y>\d+(?:\.\d+)?)(?: \(on (?P<land>[^)]+)\)| \(|$)")
_MAP_SIZE = re.compile(r"^(?P<zone>.+): (?P<w>\d+) x (?P<h>\d+)$")


class World:
    """The ways between zones for one question: the links, the flights the character has, the sizes of the maps."""

    def __init__(self, links: list[dict], sizes: dict[str, tuple[float, float]] | None = None,
                 level: int | None = None):
        self.links = links
        self.sizes = sizes or {}
        self.level = level  # the character's level: zones far above it are not walked through
        self.enemy: tuple = ()  # the other faction's capitals: not walked through either
        # the flight masters whose path the character has: (name, zone, (x, y), continent: None = the player's)
        self.flown: list[tuple] = []
        self.paths: dict[str, str] = {}  # flight master -> his path as the flight map names it ("Town, Zone")
        self.by_zone: dict[str, list[int]] = {}
        for n, link in enumerate(links):
            self.by_zone.setdefault(link["a"], []).append(n)
        self.zones = {link["a"] for link in links} | {link["b"] for link in links}

    def with_flight(self, who: str, zone: str, xy, land: str | None = None) -> "World":
        """The world once a flight master's path is opened on the way: flights between him and the ones known on
        his continent."""
        links = list(self.links)
        for other, there, at, where in self.flown:
            if there != zone and where == land:
                links.append({"a": zone, "via": xy, "kind": "flight", "b": there, "arrive": at,
                              "title": f"{who} (fly to {self.flight_to(other, there)})"})
                links.append({"a": there, "via": at, "kind": "flight", "b": zone, "arrive": xy,
                              "title": f"{other} (fly to {self.flight_to(who, zone)})"})
        world = World(links, self.sizes, self.level)
        world.flown, world.enemy, world.paths = self.flown + [(who, zone, xy, land)], self.enemy, self.paths
        return world

    def flight_to(self, who: str, zone: str) -> str:
        """Where a flight goes, as the player finds it on the flight map: the name of the path ("Sentinel Hill,
        Westfall"), where the flight master's own name is not shown; his name and zone when the path's is not known."""
        return self.paths.get(who) or f"{who}, {map_of(zone)}"

    def name(self, zone: str) -> str:
        """A zone as the links name it: "Stormwind" is Stormwind City, whatever the letters' case."""
        low = str(zone).strip().lower()
        for known in self.zones:
            if known.lower() == low:
                return known
        starts = sorted(z for z in self.zones if z.lower().startswith(low) or low.startswith(z.lower()))
        return starts[0] if len(starts) == 1 else str(zone).strip()

    def scale(self, zone: str) -> tuple[float, float] | None:
        """Meters per map unit east-west and north-south on the map of a zone; None when its size is not known."""
        size = self.sizes.get(map_of(zone))
        return (size[0] / 100.0, size[1] / 100.0) if size else None

    def meters(self, zone: str, p, q) -> float:
        size = CITY_SIZE if map_of(zone) in CITIES else DEFAULT_SIZE
        if not p or not q:
            # one end is not known (a crossing the map add-on shows from one side only): no short cut through the zone
            return max(UNKNOWN_WALK, UNKNOWN_SHARE * (self.sizes.get(map_of(zone)) or size)[0])
        sx, sy = self.scale(zone) or (size[0] / 100.0, size[1] / 100.0)
        return math.hypot((p[0] - q[0]) * sx, (p[1] - q[1]) * sy)

    def danger(self, zone: str, goal: str) -> float:
        """What it costs more to walk through a zone on the way to another: far above the character's level, or a
        capital of the other faction."""
        if zone == goal:
            return 0.0
        if map_of(zone) in self.enemy:
            return DANGER
        low = _zone_levels.get(map_of(zone), ZONE_LEVELS.get(map_of(zone)))
        if self.level is None or low is None or low < self.level + DANGER_LEVELS:
            return 0.0
        return DANGER

    def route(self, a: str, a_xy, b: str, b_xy=None) -> tuple[float, list[dict]] | None:
        """The cheapest way from a place in zone ``a`` into zone ``b`` (to ``b_xy`` when given): its cost in meters
        (rides count as RIDE says) and the links to take in order; None when no way is known. Same zone: no links."""
        if a == b:
            return (self.meters(a, a_xy, b_xy) if b_xy else 0.0), []
        best: dict[int, float] = {}
        heap, came = [], {}
        for n in self.by_zone.get(a, []):
            cost = self.meters(a, a_xy, self.links[n]["via"]) + RIDE.get(self.links[n]["kind"], 0)
            heapq.heappush(heap, (cost, n, -1))
        goal, end = None, None
        while heap:
            cost, n, prev = heapq.heappop(heap)
            if n == -2:
                goal, end = cost, prev
                break
            if n in best:
                continue
            best[n], came[n] = cost, prev
            link = self.links[n]
            if link["b"] == b:
                heapq.heappush(heap, (cost + (self.meters(b, link["arrive"], b_xy) if b_xy else 0.0), -2, n))
            for m in self.by_zone.get(link["b"], []):
                if m not in best:
                    nxt = self.links[m]
                    walk = self.meters(link["b"], link["arrive"], nxt["via"])
                    # a ride from where one arrives is no walk through the zone (a boat on from the same harbour)
                    risk = self.danger(link["b"], b) if walk > 150 or nxt["kind"] in ("walk", "gate") else 0.0
                    heapq.heappush(heap, (cost + walk + risk + RIDE.get(nxt["kind"], 0), m, n))
        if goal is None:
            return None
        steps = []
        while end is not None and end >= 0:
            steps.append(self.links[end])
            end = came[end]
        return goal, steps[::-1]


def world_for(snapshot: dict | None = None, avoid: list | None = None) -> World:
    """The world for a question: the crossings and rides of the player's faction, the flights between the flight
    masters whose path the character knows (the flightMasters section), the map sizes (mapSizes). ``avoid``: ways
    not to take, each a part of a way's name ("Thorgrum Borrelson": no flight from or to him; "Tram": no tram) or a
    zone not to pass through."""
    snapshot = snapshot or {}
    player = snapshot.get("player") or {}
    faction = "H" if str(player.get("faction") or "").lower().startswith("h") else "A"
    icon_links = _links_from_icons(faction) or _transport_links(faction)
    covered = {(link["a"], link["b"]) for link in icon_links}
    links = list(icon_links) + [link for link in _seed_links() if (link["a"], link["b"]) not in covered]
    known, paths = [], {}
    for line in snapshot.get("flightMasters") or []:
        m = _FLIGHT_MASTER.match(str(line))
        if m and "flight path known" in m.group("tag"):
            xy = (float(m.group("x")), float(m.group("y")))
            who = re.sub(r"\s*<[^>]*>", "", m.group("who")).strip()
            known.append((who, region_of(m.group("zone"), xy), xy, m.group("land")))
            path = m.group("tag").partition("flight path known:")[2].strip()
            if path:
                paths[who] = path
    for who, zone, xy, land in known:
        for other, there, at, where in known:
            if there != zone and where == land:  # no flight crosses the sea
                links.append({"a": zone, "via": xy, "kind": "flight", "b": there, "arrive": at,
                              "title": f"{who} (fly to {paths.get(other) or other + ', ' + map_of(there)})"})
    sizes = {}
    for line in snapshot.get("mapSizes") or []:
        m = _MAP_SIZE.match(str(line))
        if m:
            sizes[m.group("zone")] = (float(m.group("w")), float(m.group("h")))
    no = [str(a).strip("[] ").lower() for a in avoid or [] if str(a).strip("[] ")]
    known = [k for k in known if not any(a in k[0].lower() for a in no)]
    links = [link for link in links if not any(a in link["title"].lower() or a in (link["a"].lower(), link["b"].lower())
                                               for a in no)]
    level = player.get("level") if isinstance(player.get("level"), (int, float)) else None
    world = World(links, sizes, int(level) if level is not None else None)
    world.flown, world.enemy, world.paths = known, ENEMY_CITIES[faction], paths
    return world


def describe_way(world: World, snapshot: dict, zone: str, xy=None) -> tuple[str, bool]:
    """The way from where the player stands to a zone (to a place in it when ``xy`` is given), as lines for the model:
    every crossing, ride and flight in order, where each is and how far the walk to it. Second value: an error."""
    player = (snapshot or {}).get("player") or {}
    home = str(player.get("map") or player.get("zone") or "")
    try:
        at = tuple(float(v) for v in str(player.get("coords") or "").split(","))[:2]
    except ValueError:
        at = ()
    if not home or len(at) != 2:
        return "The player's position is not known, so no way can be worked out.", True
    goal = world.name(zone)
    if xy:
        goal = region_of(map_of(goal), xy) if map_of(goal) == goal else goal
    start = region_of(home, at)
    if goal not in world.zones and goal != start:
        # a harbour or a town a ride goes to ("Theramore", "Menethil Harbor", "Dalaran"): the zone that ride lands in
        port = re.compile(r"\bto " + re.escape(str(zone).strip().lower()) + r"\b")
        goal = next((link["b"] for link in world.links if link["kind"] != "walk" and port.search(link["title"].lower())), goal)
    if goal not in world.zones and goal != start:
        return (f"The app knows no zone called '{zone}' (zones it has ways for: {', '.join(sorted(world.zones))}). "
                "Look the way up, and say it is not from the game data."), True
    if goal == start:
        return f"The player is in {goal} already: no crossing or ride is needed.", False
    found = world.route(start, at, goal, xy)
    if not found:
        return (f"The app knows no way from {start} to {goal} for this character (no road, boat, portal or known "
                "flight joins them in its data). Look it up, and say it is not from the game data."), True
    cost, steps = found
    lines = [f"The way from {home} {at[0]:g}, {at[1]:g} to {goal}" + (f" {xy[0]:g}, {xy[1]:g}" if xy else "")
             + f": {len(steps)} step{'s' if len(steps) != 1 else ''}. Every one is from the app's data of this server "
               "(the player's map add-on, the flight paths the character has):"]
    here, spot = start, at
    for n, step in enumerate(steps, 1):
        walk = world.meters(here, spot, step["via"]) if spot and step.get("via") else None
        far = f", {int(round(walk, -1))} m from " + ("the player" if n == 1 else "where the last step ends") if walk is not None else ""
        lands = f"; it ends at {map_of(step['b'])} {step['arrive'][0]:g}, {step['arrive'][1]:g}" if step.get("arrive") else ""
        lines.append(f"{n}. {step['title']} — {map_of(step['a'])} {step['via'][0]:g}, {step['via'][1]:g} ({step['kind']}{far})"
                     f" -> {step['b']}{lands}")
        here, spot = step["b"], step.get("arrive")
    if any(world.danger(step["b"], goal) for step in steps[:-1]):
        lines.append("Part of this way goes through a zone far above the character's level or an enemy city: say so.")
    lines.append("To put it on the map, call plan_route with this zone in then: it makes every step a stop, with what is "
                 "to do there, and keeps what the route on the map has. A step is a place to go to, not something to "
                 "do twice.")
    return "\n".join(lines), False
