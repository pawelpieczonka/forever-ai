"""The walking route through the quest log: every place with something to do (objectives, turn-ins, chosen quest
givers), in the order with the shortest way, each turn-in after its own objectives. Computed here so that the model
does not have to order a dozen coordinates in its head. The model decides what belongs on the route (which quests to
leave out, which to do first or last, which new ones to pick up, where the player goes afterwards, and how they get
into the next zone); this module orders it.

A route can go on through other zones: first the stops of the player's zone, ending at the way out (a portal, a boat,
a flight master, a road), which is a stop itself, then the stops of the next zone from where the player arrives
there, and so on. The ways between zones are not in the game data: the app knows some (ways.py), the model names
the others. A part of a map with no ground way to the rest of it counts as a zone of its own."""

from __future__ import annotations

import itertools
import math
import random
import re
import statistics

from .ways import paths_inside, DANGER_LEVELS, ZONE_LEVELS, map_of, region_of, world_for

EXACT_MAX = 12  # stops up to which every order is tried (2^n * n^2 steps); more are ordered nearest-first
RESTARTS = 6  # ... and improved from this many other beginnings besides
_PLACE = re.compile(r"^(?P<name>.*?) \((?P<zone>[^()]+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)\)$")
_SOURCE = re.compile(r"^(?P<desc>.*?) — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)"
                     r"(?: \((?:(?P<m>\d+) m)?.*)?$")
_NPC_LINE = re.compile(r"— (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?) \((?P<m>\d+) m\)")
_WHERE = re.compile(r"^(.+?) (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)$")
_HOW = re.compile(r"^(kill|use|find|from object|dropped by|sold by)\s+", re.I)
# an objective's line of whereToGet: what it wants, then its sources (the name of what it wants may hold ": " itself)
_LINE = re.compile(r"^(?P<label>.*?): (?=(?:kill|use|find|from object|dropped by|sold by)\s)", re.I)


def split_line(line) -> tuple[str, str]:
    """An objective's line of whereToGet: (what it wants, its sources). "Arcane Explainer: Magical Stuff in Simple
    Words: dropped by Mother Fang (...)" wants "Arcane Explainer: Magical Stuff in Simple Words"."""
    line = str(line)
    m = _LINE.match(line)
    if m:
        return m.group("label"), line[m.end():]
    label, _, sources = line.partition(": ")
    return label, sources
# a quest's "next": "[Grove of the Ancients] (level 17) from Arch Druid Fandral Staghelm (Darnassus 34.8, 9.3)"
_FOLLOW = re.compile(r"^\[(?P<title>.+?)\](?: \(level (?P<level>\d+)\))? from (?P<places>.+)$")
_GROUP_TYPES = ("elite", "group", "dungeon", "raid")
_CREDIT = re.compile(r"^(kill |quest )?credit\b", re.I)  # the database's name for an invisible objective marker
MAP_ASPECT = (1.5, 1.0)  # a zone map is three wide and two high: the shape of distances when nothing was measured
MAX_HOPS = 24  # zones a route goes on into or through
ORDER_SLACK = 1.25  # the model's order of the zones is kept when it costs at most this many times the shortest
EXACT_ZONES = 5  # zones to go on into up to which every order of them is tried; more are taken nearest first
FLIGHT_NEAR_M, FLIGHT_NEAR_UNITS = 300, 6.0  # a flight master this close to the route is worth a word
ALONG_M, ALONG_UNITS = 250, 5.0  # the giver of a quest whose places are known may stand this far from the route
DETOUR_M, DETOUR_UNITS = 300, 9.0  # a quest taken along makes the route this much longer at most
# ... and with nothing else to do (an empty quest log): the quests around the player are the route, each of them done
# within this much walking
ALONE_M, ALONE_UNITS = 900, 25.0
ALONG_TRIES, ALONG_MAX = 12, 8  # how many quests by the route are tried, and how many are taken along at most
GRIND_KILLS = 20  # a quest that takes more kills than this is not done "on the way", however near its creatures are
WIDE_UNITS = 3.0  # map units: two stops for one creature (or one object's spots) this near are one stop
ABOVE_MAX = 2  # a quest this many levels above the character is still theirs to do along the way
NEAR_M, NEAR_UNITS, NEAR_MAX = 100, 2.0, 4  # a quest giver this close to the route is on it (metres; map units
#                                             where nothing was measured); at most this many of them
ON_THE_WAY_OUT = 1.5  # a zone left and entered again: what lies this many times the way to its exit away waits
ON_THE_WAY_OUT_MIN = 3.0  # ... and at least this far, in map units


def _xy(text) -> tuple[float, float] | None:
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", str(text or ""))
    return (float(m.group(1)), float(m.group(2))) if m else None


def _pair(value) -> tuple[float, float] | None:
    try:
        x, y = value
        return float(x), float(y)
    except (TypeError, ValueError):
        return None


def _flat(a, b) -> float:
    """The way between two points of a map, in map units north-south."""
    return math.hypot((a[0] - b[0]) * MAP_ASPECT[0], (a[1] - b[1]) * MAP_ASPECT[1])


def _travel_order(hops: list[dict], zone: str, start, world, wanted: dict | None = None) -> list[dict]:
    """The zones a route goes on into, in the order that costs the least way from where the player stands (every
    order is tried for a few zones, nearest first for more). A zone the app knows no way to keeps the place the model
    gave it, after the ones before it. ``wanted`` (zone -> places with something to do): the walk to those places
    inside each zone counts too, and the way on starts from the last of them, not from where the zone was entered."""
    searched: dict[tuple, tuple | None] = {}
    spots: dict[str, list] = {}
    for z, places in (wanted or {}).items():
        name = map_of(world.name(z))
        for xy in places:
            spots.setdefault(region_of(name, xy), []).append(tuple(xy))

    def tour(z: str, pos):
        """The walk from ``pos`` to every place of zone z with something to do, nearest first: (metres, where it ends)."""
        left, scale = list(spots.get(z) or []), world.scale(z)
        if not left or not scale or pos is None:
            return 0.0, pos
        total = 0.0
        while left:
            nxt = min(left, key=lambda p: math.hypot((p[0] - pos[0]) * scale[0], (p[1] - pos[1]) * scale[1]))
            total += math.hypot((nxt[0] - pos[0]) * scale[0], (nxt[1] - pos[1]) * scale[1])
            pos = nxt
            left.remove(nxt)
        return total, pos

    def way(cur: str, pos, to: str):
        if (cur, pos, to) not in searched:  # the orders tried share most of their steps
            searched[(cur, pos, to)] = world.route(cur, pos, to)
        return searched[(cur, pos, to)]

    def cost(order) -> float:
        """The way through the zones in this order; infinite when one of them cannot be reached that way (a ride
        that goes one way only)."""
        total, pos = tour(zone, start)
        cur = zone
        for hop in order:
            found = way(cur, pos, hop["zone"])
            if found is None:
                return math.inf
            total += found[0]
            cur, pos = hop["zone"], (found[1][-1]["arrive"] if found[1] else pos)
            inside, pos = tour(cur, pos)
            total += inside
        return total
    if len(hops) < 2:
        return hops
    given = cost(hops)
    if len(hops) <= EXACT_ZONES:
        best = min(itertools.permutations(hops), key=lambda order: (cost(order), [hops.index(h) for h in order]))
        if cost(best) == math.inf:  # a zone the app knows no way to: the model's order, with the model's way
            return hops
        # the order the model gave stands unless another is clearly shorter: it may know where the player goes on
        return hops if given <= ORDER_SLACK * cost(best) else list(best)
    if all(way(zone, start, hop["zone"]) is None for hop in hops):
        return hops
    left, out, cur, pos = list(hops), [], zone, start
    while left:
        ways = [(found, n) for found, n in ((way(cur, pos, hop["zone"]), n) for n, hop in enumerate(left)) if found]
        found, n = min(ways, key=lambda w: (w[0][0], w[1])) if ways else (None, 0)  # no way from here: as given
        hop = left.pop(n)
        out.append(hop)
        cur, pos = hop["zone"], (found[1][-1]["arrive"] if found and found[1] else pos)
    return out


def _legs(then, zone: str, start, world=None, wanted: dict | None = None,
          opened: list | None = None, keep_last: bool = False,
          ends_in: str | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """The route's parts in walking order: {"zone", "entry": where the player comes in, "via": the way out to the
    next part (None for the last), "via_title"}. The first is the player's zone from where they stand.

    ``then`` names the zones the route goes on into. The ways between them are the world's (ways.World): the road
    across a border, a boat, the tram, a flight the character has, through the zones between; the model's own via
    serves only where the world knows none. The order is the world's too, unless a zone comes twice (the model
    means a way there and back). ``wanted``: the zones with something to do; a zone named with nothing to do in it
    is only passed through when the way leads through it. Also returns the zones gone on into, as they are
    remembered for an update ({"zone"}, with the model's via where the world has none), and what was left out.
    ``opened``: the flight masters the route opens on its way, (name, zone, (x, y)): the way on from their zone can
    be a flight from them. ``keep_last``: the last zone named is where the route is to end (plan_route's ``end``),
    with or without something to do there."""
    world = world or world_for()
    for who, where, at in opened or []:  # the ones in the zone the player starts in
        if where == zone:
            world = world.with_flight(who, where, at)
    legs = [{"zone": zone, "entry": start, "via": None, "via_title": None, "known": False}]
    hops, notes = [], []
    for hop in ([then] if isinstance(then, dict) else then if isinstance(then, list) else []):
        if not isinstance(hop, dict) or not str(hop.get("zone") or "").strip():
            continue
        via, arrive = _pair(hop.get("via")), _pair(hop.get("arrive"))
        name = world.name(str(hop["zone"]).strip())
        name = region_of(map_of(name), arrive) if arrive else name  # the part of the map the player comes out in
        if hops and hops[-1]["zone"] == name:
            continue
        hops.append({"zone": name, "via": via, "arrive": arrive, "title": str(hop.get("via_title") or "").strip()[:60]})
    # A zone with nothing to do in it is not a part of the route of its own, wherever it is named: a hub the model
    # put between two zones, or a zone of a plan remembered from before the player finished it (an update planned
    # from the next zone must not lead back through it).
    if wanted is not None:
        maps = {map_of(world.name(z)) for z in wanted}
        ends = hops[-1] if keep_last and hops else None
        for hop in list(hops):
            if hop is not ends and map_of(hop["zone"]) not in maps and world.route(zone, start, hop["zone"]) is not None:
                hops.remove(hop)
                if hop["zone"] != zone:
                    notes.append(f"{hop['zone']}: nothing to do there, so it is not a part of the route of its own "
                                 "(the way leads through it when it lies between)")
    while hops and hops[0]["zone"] == zone:  # the player is there already
        hops.pop(0)
    # The stop the player wants last is in the zone they stand in, and the route goes on into others: it comes back
    # here after them (what is wanted last is last on the route, not first because it happens to be near).
    back = bool(hops) and bool(ends_in) and map_of(world.name(ends_in)) == map_of(zone) and hops[-1]["zone"] != zone \
        and world.route(hops[-1]["zone"], None, zone) is not None
    names = [hop["zone"] for hop in hops]
    if len(set(names)) == len(names) and zone not in names:  # no zone comes twice: the order is the app's to find
        # ... but the zone the route is to end in (ends_in: where the stop the player wants last is) comes last. The
        # way through the zones alone says nothing of what is done in them: "the quest in Lakeshire, then the
        # Deadmines" came out as Westfall first and Redridge after, because the border of Westfall was nearer.
        last = next((hop for hop in hops if ends_in and map_of(hop["zone"]) == map_of(world.name(ends_in))), None)
        if last is not None and len(hops) > 1:
            hops = _travel_order([hop for hop in hops if hop is not last], zone, start, world, wanted) + [last]
        else:
            hops = _travel_order(hops, zone, start, world, wanted)
    if back and hops and hops[-1]["zone"] != zone:
        hops.append({"zone": zone, "via": None, "arrive": None, "title": ""})
    targets = []
    for hop in hops:
        if len(legs) > MAX_HOPS:
            # (said, not dropped in silence: a zone named and missing from the route was put back by hand, without a
            # way into it)
            notes.append(f"{hop['zone']}: not on this route, which goes through {MAX_HOPS} zones already; "
                         "it is planned when this part is done")
            continue
        if hop["zone"] == legs[-1]["zone"]:
            continue
        # What the app knows comes before what the model recalls: the way there, through the zones between. The
        # player leaves a zone from where its stops are, not from where they came in: the way on is the cheapest
        # from any of them (the stops of a part of the route end by its way out).
        here = legs[-1]["zone"]
        froms = [legs[-1]["entry"]] + [xy for z, spots in (wanted or {}).items() if map_of(world.name(z)) == map_of(here)
                                       for xy in spots if region_of(map_of(here), xy) == here]
        ways = [w for w in (world.route(here, xy, hop["zone"]) for xy in froms[:12]) if w is not None]
        found = min(ways, key=lambda w: w[0]) if ways else None
        for step in (found[1] if found else []):
            legs[-1].update(via=step["via"], via_title=step["title"], known=True)
            legs.append({"zone": step["b"], "entry": step["arrive"], "via": None, "via_title": None, "known": False})
        if legs[-1]["zone"] != hop["zone"]:
            legs[-1].update(via=hop["via"], via_title=hop["title"])
            legs.append({"zone": hop["zone"], "entry": hop["arrive"], "via": None, "via_title": None, "known": False})
            targets.append({"zone": hop["zone"], "via": hop["via"], "title": hop["title"], "arrive": hop["arrive"]})
        else:
            targets.append({"zone": hop["zone"], "via": None, "title": "", "arrive": None})
        for who, where, at in opened or []:
            if where == hop["zone"]:
                world = world.with_flight(who, where, at)
    return legs, targets, notes


def _leg_of(legs: list[dict], zone: str, xy, since: int = 0, early: bool = False) -> int | None:
    """The part of the route a place belongs to (not before part ``since``); None when the route does not get there.
    A zone that comes twice is one the player leaves and comes back to (the city they stand in, a village behind a
    portal, an island): on the first visit what lies on the way out is done, and what is ``early`` (a quest to pick
    up, a stop the player wants first); the rest waits for the visit after it."""
    found = [k for k, leg in enumerate(legs) if leg["zone"] == zone]
    if len(found) < 2:
        return found[0] if found and found[0] >= since else None
    if map_of(zone) != zone:  # a cut-off part of a map is small and all of it can be walked: the first visit does it
        return next((k for k in found if k >= since), None)
    head, back = legs[found[0]], legs[found[-1]]
    if early and found[0] >= since:
        return found[0]
    if head["entry"] and head["via"]:
        reach = max(ON_THE_WAY_OUT * _flat(head["entry"], head["via"]), ON_THE_WAY_OUT_MIN)
        near = min(_flat(xy, head["entry"]), _flat(xy, head["via"])) <= reach
        same_side = bool(back["entry"]) and _flat(back["entry"], head["via"]) <= reach
        if near and found[0] >= since:
            return found[0]
        if near and back["entry"] and not same_side:
            return None
    return found[-1] if found[-1] >= since else None


_DETOUR = re.compile(r"^(?P<zone>.+?) (?P<ax>[\d.]+), (?P<ay>[\d.]+) → (?P<bx>[\d.]+), (?P<by>[\d.]+): through "
                     r"(?P<vx>[\d.]+), (?P<vy>[\d.]+)")
DETOUR_NEAR = 3.0  # map units: a way between two places this close to a remembered way round's ends goes round too


def _detours(snapshot: dict) -> dict[str, list[tuple]]:
    """The ways round the player walked, per map: [(a, b, via)] in map coordinates."""
    out: dict[str, list[tuple]] = {}
    for line in snapshot.get("detours") or []:
        m = _DETOUR.match(str(line))
        if m:
            f = {k: float(v) for k, v in m.groupdict().items() if k != "zone"}
            out.setdefault(m.group("zone"), []).append(((f["ax"], f["ay"]), (f["bx"], f["by"]), (f["vx"], f["vy"])))
    return out


def _round_via(p, q, detours) -> tuple | None:
    """The point the way from p to q goes round by (map coordinates), when the player walked round there before."""
    def near(u, v):
        return abs(u[0] - v[0]) <= DETOUR_NEAR and abs(u[1] - v[1]) <= DETOUR_NEAR
    for a, b, via in detours:
        if (near(p, a) and near(q, b)) or (near(p, b) and near(q, a)):
            return via
    return None


_FLIGHT = re.compile(r"^(?P<who>.+?) \[(?P<tag>[^\]]*)\] — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)(?: \(|$)")
_FLIGHT_STATE = re.compile(r"flight path (known|not discovered|not known yet)")


def _flight_masters(snapshot: dict, home: str, start, stops: list[dict], scale) -> list[str]:
    """The flight masters of the player's zone near the route (the player's spot or a stop) whose flight path the
    character has not discovered, or of which the add-on cannot tell: a line each for the model, which offers them.
    They are not put on the route."""
    found: dict[str | None, list[str]] = {"not discovered": [], None: [], "not known yet": []}
    # every flight master of the continent when the add-on lists them, else the ones of the player's zone
    lines = snapshot.get("flightMasters") or snapshot.get("zoneNPCs") or []
    seen = set()
    for line in lines:
        m = _FLIGHT.match(str(line))
        if not m or not ("flight master" in m.group("tag") or m.group("tag").startswith("flight path")):
            continue  # another NPC
        st = _FLIGHT_STATE.search(m.group("tag"))
        state = st.group(1) if st else None
        if state == "known" or (m.group("who"), m.group("zone")) in seen:
            continue
        seen.add((m.group("who"), m.group("zone")))
        zone, xy = m.group("zone"), (float(m.group("x")), float(m.group("y")))
        measured = scale if zone == home else None
        sx, sy = measured or MAP_ASPECT
        best = None
        for n, st in [(0, {"x": start[0], "y": start[1], "title": None, "zone": home})] + list(enumerate(stops, 1)):
            if st["zone"] != zone:
                continue
            d = math.hypot((xy[0] - st["x"]) * sx, (xy[1] - st["y"]) * sy)
            if best is None or d < best[0]:
                best = (d, n, st["title"])
        if best is None or best[0] > (FLIGHT_NEAR_M if measured else FLIGHT_NEAR_UNITS * MAP_ASPECT[1]):
            continue  # not by the route
        if best[1] and best[0] < (15 if measured else 0.3) and stops[best[1] - 1].get("extra"):
            continue  # he is a stop of the route already: the player asked for him
        place = f"{xy[0]:g}, {xy[1]:g}" if zone == home else f"{zone} {xy[0]:g}, {xy[1]:g}"
        if best[0] < (15 if measured else 0.3):  # the route passes him anyway (a stop there, or the way out of the zone)
            where, far = ("where you stand" if best[1] == 0 else f"at stop {best[1]} ({best[2]})"), ""
        elif measured:
            where, far = ("from you" if best[1] == 0 else f"from stop {best[1]} ({best[2]})"), f"{int(round(best[0]))} m "
        else:
            where, far = f"near stop {best[1]} ({best[2]})", ""
        found[state].append(f"{m.group('who')} ({place}), {far}{where}")
    out = []
    if found["not discovered"]:
        out.append("Flight paths not discovered by the route: " + "; ".join(found["not discovered"]) + ". Leave them off "
                   "the route and offer them in the answer (a short detour now opens the flight there); when the player "
                   "wants one, plan again with him in extra.")
    if found[None] or found["not known yet"]:  # not named: the model must not offer what the character may have
        out.append("The add-on does not know yet which flight paths the character has (no flight master opened "
                   "since): offer none; opening any flight master tells it.")
    return out


def _name(desc: str) -> str:
    """'kill Boar (level 5)' -> 'Boar'; 'dropped by Den Mother (level 19, elite, 1200 health, 18% drop chance)' ->
    'Den Mother'."""
    return re.sub(r"\s*\((?:level|rare|elite|boss)[^)]*\)|\s*\([^)]*\b(?:health|drop chance)\)", "",
                  _HOW.sub("", desc)).strip()


def _scale(snapshot: dict, start, zone: str) -> tuple[float, float] | None:
    """Meters per map unit east-west and north-south in this zone, from the distances the game itself measured from
    the player's position. A zone map is not square (Teldrassil: 46.6 m and 31.0 m per unit), so both are fitted;
    with too few measurements one value serves for both."""
    points = []  # (dx, dy, meters)

    def add(z, x, y, meters):
        dx, dy = float(x) - start[0], float(y) - start[1]
        if z == zone and math.hypot(dx, dy) >= 1 and float(meters) > 0:
            points.append((dx, dy, float(meters)))
    for q in snapshot.get("quests") or []:
        for line in (q.get("whereToGet") or []) if isinstance(q, dict) else []:
            for src in split_line(line)[1].split("; "):
                m = _SOURCE.match(src)
                if m and m.group("m"):
                    add(m.group("zone"), m.group("x"), m.group("y"), m.group("m"))
    for q in snapshot.get("nearbyQuests") or []:
        m = isinstance(q, dict) and _WHERE.match(str(q.get("where") or ""))
        d = isinstance(q, dict) and re.match(r"^(\d+) m$", str(q.get("distance") or ""))
        if m and d:
            add(m.group(1), m.group(2), m.group(3), d.group(1))
    for line in snapshot.get("zoneNPCs") or []:
        m = _NPC_LINE.search(str(line))
        if m:
            add(m.group("zone"), m.group("x"), m.group("y"), m.group("m"))
    if not points:
        return None
    # meters^2 = (sx*dx)^2 + (sy*dy)^2: least squares for sx^2 and sy^2
    a11 = sum(dx ** 4 for dx, _, _ in points)
    a12 = sum(dx * dx * dy * dy for dx, dy, _ in points)
    a22 = sum(dy ** 4 for _, dy, _ in points)
    b1 = sum(dx * dx * m * m for dx, _, m in points)
    b2 = sum(dy * dy * m * m for _, dy, m in points)
    det = a11 * a22 - a12 * a12
    if len(points) >= 3 and det > 1e-6 * a11 * a22:
        sx2, sy2 = (b1 * a22 - b2 * a12) / det, (b2 * a11 - b1 * a12) / det
        if sx2 > 0 and sy2 > 0 and 0.2 < sx2 / sy2 < 5:
            return math.sqrt(sx2), math.sqrt(sy2)
    one = statistics.median(m / math.hypot(dx, dy) for dx, dy, m in points)
    return one, one


def _order(start, nodes: list[dict], end, way=None) -> list[int]:
    """Indices of ``nodes`` in the order of the shortest path from ``start`` (ending as near to ``end`` as the way
    allows when it is given); node["after"] (indices) come before the node. Positions are node["at"]. ``way``: the
    length of the way between two positions, when it is not the straight line (a way round)."""
    def dist(a, b) -> float:
        if a is None:
            return 0.0  # no start: the path begins where it likes
        return way(a, b) if way else math.hypot(a[0] - b[0], a[1] - b[1])
    n = len(nodes)
    need = [sum(1 << j for j in node["after"]) for node in nodes]
    if n > EXACT_MAX:  # nearest first among the stops whose turn has come, then made shorter where it can be
        order, done, pos = [], 0, start
        while len(order) < n:
            ready = [i for i in range(n) if not done >> i & 1 and need[i] & done == need[i]]
            i = min(ready, key=lambda k: (dist(pos, nodes[k]["at"]), k))
            order.append(i)
            done |= 1 << i
            pos = nodes[i]["at"]
        # ... and from a few other beginnings (the same ones every time: a route must not change between two
        # plans of the same stops): the moves above stop at the first order none of them makes shorter, which is
        # not always a short one
        def length(seq: list[int]) -> float:
            total, pos = 0.0, start
            for k in seq:
                total += dist(pos, nodes[k]["at"])
                pos = nodes[k]["at"]
            return total + (dist(pos, end) if end else 0.0)
        best = _shorter(order, start, nodes, end, dist)
        rng = random.Random(n)
        for _ in range(RESTARTS):
            mixed, done = [], 0
            while len(mixed) < n:  # any order in which every stop comes after the ones it waits for
                ready = [i for i in range(n) if not done >> i & 1 and need[i] & done == need[i]]
                i = rng.choice(ready)
                mixed.append(i)
                done |= 1 << i
            other = _shorter(mixed, start, nodes, end, dist)
            if length(other) < length(best) - 1e-6:
                best = other
        return best
    d = [[dist(a["at"], b["at"]) for b in nodes] for a in nodes]
    best: dict[tuple[int, int], tuple[float, int]] = {}
    for i in range(n):
        if need[i] == 0:
            best[(1 << i, i)] = (dist(start, nodes[i]["at"]), -1)
    for mask in range(1, 1 << n):
        for last in range(n):
            cur = best.get((mask, last))
            if cur is None:
                continue
            for j in range(n):
                if mask >> j & 1 or need[j] & mask != need[j]:
                    continue
                cand = (cur[0] + d[last][j], last)
                key = (mask | 1 << j, j)
                if key not in best or cand[0] < best[key][0] - 1e-9:
                    best[key] = cand
    full = (1 << n) - 1

    def total(k: int) -> float:
        return best[(full, k)][0] + (dist(nodes[k]["at"], end) if end else 0.0)
    last = min((k for k in range(n) if (full, k) in best), key=lambda k: (total(k), k))
    order, mask = [], full
    while last != -1:
        order.append(last)
        prev = best[(mask, last)][1]
        mask ^= 1 << last
        last = prev
    return order[::-1]


def _shorter(order: list[int], start, nodes: list[dict], end, dist) -> list[int]:
    """An order of stops made shorter by the two moves that undo what nearest-first does wrong: a stop is taken out
    and put where it costs the least way, and a stretch of the path is walked the other way round. A move counts only
    when every stop still comes after the ones it waits for (node["after"]). Too many stops to try every order: this
    comes within a few percent of the shortest (measured in tests against the exact search)."""
    n = len(order)
    at = [node["at"] for node in nodes]
    between = [[dist(a, b) for b in at] for a in at]
    first = [dist(start, a) for a in at]
    last = [dist(a, end) if end else 0.0 for a in at]

    def d(a: int, b: int) -> float:  # -1: where the path starts, -2: where it is to end
        if a == -1:
            return 0.0 if b == -2 else first[b]
        return last[a] if b == -2 else between[a][b]

    def allowed(seq: list[int]) -> bool:
        seen = 0
        for i in seq:
            if any(not seen >> a & 1 for a in nodes[i]["after"]):
                return False
            seen |= 1 << i
        return True
    for _ in range(40 * n):  # (each move makes the path shorter, so this ends; the cap is for safety)
        moves = []
        for size in (1, 2, 3):  # a stop, or two or three in a row, to another place (as they are, or turned round)
            for i in range(n - size + 1):
                head, tail = order[i], order[i + size - 1]
                before, after = (order[i - 1] if i else -1), (order[i + size] if i + size < n else -2)
                gain = d(before, head) + d(tail, after) - d(before, after)
                if size > 1:
                    gain += 0.0  # (the way inside the stretch stays the same, also turned round: ways are two-way)
                if gain <= 1e-6:
                    continue
                rest = order[:i] + order[i + size:]
                for j in range(len(rest) + 1):
                    if j == i:
                        continue
                    a, b = (rest[j - 1] if j else -1), (rest[j] if j < len(rest) else -2)
                    cost = d(a, head) + d(tail, b) - d(a, b)
                    if cost < gain - 1e-6:
                        moves.append((cost - gain, size, i, j))
                    if size > 1:
                        cost = d(a, tail) + d(head, b) - d(a, b)
                        if cost < gain - 1e-6:
                            moves.append((cost - gain, -size, i, j))
        for i in range(n - 1):  # a stretch the other way round
            for j in range(i + 1, n):
                a, b = (order[i - 1] if i else -1), (order[j + 1] if j + 1 < n else -2)
                delta = d(a, order[j]) + d(order[i], b) - d(a, order[i]) - d(order[j], b)
                if delta < -1e-6:
                    moves.append((delta, 0, i, j))
        for _, kind, i, j in sorted(moves):
            if kind:
                size = abs(kind)
                rest, piece = order[:i] + order[i + size:], order[i:i + size]
                new = rest[:j] + (piece if kind > 0 else piece[::-1]) + rest[j:]
            else:
                new = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
            if allowed(new):
                order = new
                break
        else:
            break
    return order


def _merge(nodes: list[dict]) -> list[dict]:
    """One stop per place: stops with the same name at the same spot become one (two quests turned in to one NPC,
    two quests' creatures at one spawn), so the player is never sent to a place twice. Turn-ins merge only with
    turn-ins, after the objectives of all of them; objectives only with objectives."""
    def same(a: dict, b: dict) -> bool:
        # (a creature's ground, or the spots of an object, is wide: its stops for two quests a little apart are one
        # stop, not two with the way back between them)
        wide = (bool(a.get("mob")) and a.get("mob") == b.get("mob")) or any(
            t.get("spots") for t in list(a.get("tasks") or []) + list(b.get("tasks") or []))
        near = WIDE_UNITS if wide else 1.0
        return (a["title"].lower() == b["title"].lower() and a["late"] == b["late"] and a["leg"] == b["leg"]
                and a["what"].startswith("turn in") == b["what"].startswith("turn in")
                and abs(a["xy"][0] - b["xy"][0]) <= near and abs(a["xy"][1] - b["xy"][1]) <= near)

    groups: list[list[int]] = []
    for i, node in enumerate(nodes):
        group = next((g for g in groups if same(nodes[g[0]], node)), None)
        if group is None:
            groups.append([i])
        else:
            group.append(i)
    if len(groups) == len(nodes):
        return nodes
    group_of = {i: n for n, g in enumerate(groups) for i in g}
    merged = []
    for n, g in enumerate(groups):
        node = dict(nodes[g[0]])
        node["what"] = "; ".join(nodes[i]["what"] for i in g)
        node["note"] = "; ".join(dict.fromkeys(nodes[i]["note"] for i in g if nodes[i]["note"]))
        node["after"] = sorted({group_of[a] for i in g for a in nodes[i]["after"]} - {n})
        node["mob"] = next((nodes[i].get("mob") for i in g if nodes[i].get("mob")), None)
        node["tasks"] = [t for i in g for t in nodes[i].get("tasks") or []]
        node["early"] = any(nodes[i]["early"] for i in g)
        merged.append(node)
    return merged


def _wanted(snapshot: dict, home: str, skip, use, new_quests, extras, along=None) -> dict:
    """The zones with something to do for a route, each with the places of it: where the quests of the log (not the
    ones left out) are turned in and done, where the quests to pick up are offered, where the extra stops are."""
    found: dict[str, list] = {}

    def add(zone, x, y) -> None:
        found.setdefault(str(zone), []).append((float(x), float(y)))
    for q in snapshot.get("quests") or []:
        if not isinstance(q, dict) or not q.get("title") or _named(str(q["title"]), skip):
            continue
        for place in str(q.get("turnIn") or "").split(";")[:1]:
            m = _PLACE.match(place.strip())
            if m:
                add(m.group("zone"), m.group("x"), m.group("y"))
        follow = _FOLLOW.match(str(q.get("next") or ""))
        giver = follow and _PLACE.match(follow.group("places").split(";")[0].strip())
        if giver:
            add(giver.group("zone"), giver.group("x"), giver.group("y"))
        if str(q.get("status") or "").startswith("READY"):
            continue
        for line in q.get("whereToGet") or []:
            options = [_SOURCE.match(o) for o in split_line(line)[1].split("; ")]
            options = [m for m in options if m]
            named = [m for m in options if _named(_name(m.group("desc")), use)]
            if named or options:
                level = (snapshot.get("player") or {}).get("level")
                m = named[0] if named else next(o for o in options if o.group(0) == _surest([x.group(0) for x in options], level))
                add(m.group("zone"), m.group("x"), m.group("y"))
    for q in snapshot.get("nearbyQuests") or []:
        m = isinstance(q, dict) and _WHERE.match(str(q.get("where") or ""))
        title = str(q.get("title") or "") if isinstance(q, dict) else ""
        if m and (new_quests is True or _named(title, new_quests if isinstance(new_quests, list) else [])
                  or _along(q, title, along)):
            add(m.group(1), m.group(2), m.group(3))
            # ... and where it is done and turned in, when the game data says so: a city the route passes only to
            # hand a letter in is a zone with something to do
            t = _PLACE.match(str(q.get("turnIn") or "").split(";")[0].strip())
            if t:
                add(t.group("zone"), t.group("x"), t.group("y"))
            for line in q.get("whereToGet") or []:
                o = next((mm for mm in (_SOURCE.match(o) for o in split_line(line)[1].split("; ")) if mm), None)
                if o:
                    add(o.group("zone"), o.group("x"), o.group("y"))
    for e in extras or []:
        add(str(e.get("zone") or home), e["x"], e["y"])
    return found


def _gray(level: float) -> int:
    """The highest quest level that gives a character of this level nothing any more."""
    level = int(level)
    if level <= 5:
        return 0
    if level <= 39:
        return level - 5 - level // 10
    return level - 1 - level // 5


def _kills(q: dict) -> float:
    """About how many creatures a quest still to be taken has the player kill: what it asks for (the number in its
    words) by the chance of each drop. 0 when the data does not say."""
    count = max((int(n) for n in re.findall(r"\b(\d{1,2})\b", str(q.get("objective") or ""))), default=0)
    total = 0.0
    for line in q.get("whereToGet") or []:
        option = split_line(line)[1].split("; ")[0]
        chance = re.search(r"(\d+(?:\.\d+)?)% drop chance", option)
        if chance and float(chance.group(1)) > 0:
            total += (count or 1) / (float(chance.group(1)) / 100)
        elif re.match(r"\s*kill\b", option, re.I):
            total += count or 1
    return total


def _surest(options: list[str], level) -> str:
    """Of several sources of one objective (nearest first), the one this character gets it from most surely and
    quickly: not a rare, an elite or a boss, not a lone creature with one spawn point, not one above the character's
    level (three or more above counts most), not a drop in ten. Among equals the nearest."""
    def risk(option: str) -> float:
        low, total = option.lower(), 0.0
        if re.search(r"\b(rare|elite|boss)\b", low):
            total += 3
        spawns = re.search(r"(\d+) spawn points?", low)
        if spawns and int(spawns.group(1)) <= 1:
            total += 2
        lv = re.search(r"\(level (\d+)(?:-(\d+))?", low)
        if lv and isinstance(level, (int, float)):
            over = int(lv.group(2) or lv.group(1)) - int(level)
            total += 3 if over >= 3 else 1 if over >= 1 else 0
        chance = re.search(r"(\d+(?:\.\d+)?)% drop chance", low)
        if chance and float(chance.group(1)) < 15:
            total += 1
        return total
    return min(options, key=lambda o: (risk(o), options.index(o)))


def _as_taken(q: dict) -> dict:
    """A quest still to be picked up, as the quest log shows it once it is taken: everything of it to do."""
    lines = [str(line) for line in q.get("whereToGet") or []]
    return {"id": q.get("id"), "title": q.get("title"), "level": q.get("level"), "type": q.get("type"),
            "status": "in progress" if lines else "READY TO TURN IN",
            "objectives": "; ".join(f"{split_line(line)[0]} [TO DO]" for line in lines), "whereToGet": lines,
            "turnIn": q.get("turnIn") or "", "next": q.get("next"), "nextId": q.get("nextId")}


def _named(title: str, names) -> bool:
    low = title.lower()
    return any(str(n).strip("[] ").lower() == low for n in names or [] if not isinstance(n, int))


def _along(q: dict, title: str, along) -> bool:
    """Is this quest one of the quests taken along (``along``: their numbers; titles in a plan of an older version)?
    The parts of a chain often share a title: by title, part 2 was taken for part 1 when that was turned in."""
    qid = q.get("id") if isinstance(q, dict) else None
    if isinstance(qid, int) and any(isinstance(n, int) for n in along or []):
        return qid in along
    return _named(title, along)


def plan_route(snapshot: dict, new_quests: bool | list = False, skip: list | None = None, last: list | None = None,
               end: tuple[float, float] | None = None, end_title: str | None = None, first: list | None = None,
               then: dict | list | None = None, use: list | None = None, extra: list | None = None,
               world=None, avoid: list | None = None, along: list | None = None) -> dict:
    """The route (see _route), with the quests that can be done along it. ``along``: the titles of the quests taken
    along, for the update of a route that was planned with them; None for a route that is being made: then they are
    chosen here. Taken along is a quest of the character's level (up to ABOVE_MAX above, not a group's) whose giver
    stands by the route and that makes the route no more than DETOUR_M longer, with everything it wants and its
    turn-in: the route is planned with it and without it, and the two are compared. (Near the route is not enough: a
    quest taken from a guard by the road out and turned in back in the town has all its places by the route, and
    costs the way there twice.) A quest whose places the data does not have is taken when its giver stands right by
    the route: picking it up costs nothing."""
    kw = dict(new_quests=new_quests, skip=skip, last=last, end=end, end_title=end_title, first=first, then=then, use=use,
              extra=extra, world=world, avoid=avoid)
    if along is not None:
        return _route(snapshot, along=along, updating=True, **kw)

    def length(route: dict) -> tuple[float | None, float]:
        """How long the route is, and how much longer a quest taken along may make it: in metres, or, on maps whose
        size the game data does not have, in map units along the stops of each zone."""
        if route.get("meters") is not None:
            return route["meters"], DETOUR_M
        at, zone, total = _xy((snapshot.get("player") or {}).get("coords")), None, 0.0
        for stop in route.get("stops") or []:
            if at and zone in (None, stop["zone"]):
                total += math.hypot((stop["x"] - at[0]) * MAP_ASPECT[0], (stop["y"] - at[1]) * MAP_ASPECT[1])
            at, zone = (stop["x"], stop["y"]), stop["zone"]
        return total, DETOUR_UNITS
    base = _route(snapshot, along=[], **kw)
    chosen, per_leg = [], {}
    total, _ = length(base)
    alone = not base.get("stops")  # nothing in the log to do: a quest here is not a detour from anything
    for key, leg, close, title in (base.get("candidates") or [])[:ALONG_TRIES]:
        if len(chosen) >= ALONG_MAX or per_leg.get(leg, 0) >= NEAR_MAX:
            continue
        trial = _route(snapshot, along=chosen + [key], **kw)
        longer, most = length(trial)
        if alone:
            most = ALONE_M if most == DETOUR_M else ALONE_UNITS
        if (longer - total <= most and key in (trial.get("along") or [])
                and not any(str(line).startswith(f"[{title}]") for line in trial.get("left_out") or [])):
            chosen.append(key)
            per_leg[leg] = per_leg.get(leg, 0) + 1
            total = longer
    return _route(snapshot, along=chosen, **kw) if chosen else base


def _route(snapshot: dict, new_quests: bool | list = False, skip: list | None = None, last: list | None = None,
           end: tuple[float, float] | None = None, end_title: str | None = None, first: list | None = None,
           then: dict | list | None = None, use: list | None = None, extra: list | None = None,
           world=None, avoid: list | None = None, along: list | None = None, updating: bool = False,
           _again: bool = False, _went_on: bool = False) -> dict:
    """{"stops": [{"title", "what", "note", "x", "y", "zone", "part", "meters", "mob", "quest", "tasks"}],
    "left_out": [...], "meters": total, "ways": [{"zone", "via", "title", "arrive"}], "text"}; "zone" is the map a stop
    is on and "part" the zone it counts as (a cut-off part of the map, else the map);
    "mob" is the creature to look for at a stop (None for objects and NPCs to talk to);
    "tasks" is what is to be done there: {"kind": "objective" | "turn_in" | "pick_up", "quest", "label"}, or
    {"kind": "travel", "zone"} for the way into the next zone. The add-on takes a stop off the route when its tasks
    are done, not when the player reaches the place.

    ``skip``: quest titles to leave out; ``first``: quests done before everything else in their zone (what matters
    most); ``last``: quests done after everything else there (hard ones); ``new_quests``: the titles of nearbyQuests
    to pick up (True: all of them in the route's zones); ``end``: map coordinates in the route's last zone where the
    player goes afterwards, so the route finishes near them. ``use``: the sources (creatures, objects, vendors) to get
    objectives from where whereToGet has several; elsewhere the nearest is taken, and the result names the others.
    ``extra``: stops that are not in the quest log but belong on the route because the player asked for them (a
    vendor, a trainer, a flight master to open, a creature to tame): {"title", "zone", "x", "y", "note", "mob",
    "when": "first" | "last"}; they are put where they cost the least way. ``world``: the ways between zones
    (ways.World); made from the game data when not given.

    ``then``: the zones the route goes on into, in order (one dict or a list): {"zone", "via": (x, y) the way out
    of the zone before, in that zone's coordinates, "via_title", "arrive": (x, y) where the player comes out in this
    zone}. A zone's stops end at its way out, which is a stop itself; the next zone's stops follow from where the
    player arrives. The player's own zone can come again (see _leg_of).

    A stop's "meters" is the way from the stop before it (None where the zone's scale is not known)."""
    player = snapshot.get("player") or {}
    home = str(player.get("map") or player.get("zone") or "")  # the map the player is on
    start = _xy(player.get("coords"))
    nodes, left_out = [], []
    if start is None:
        return {"stops": [], "left_out": [], "meters": None, "ways": [], "targets": [],
                "text": "The player's position is not known; no route."}
    zone = region_of(home, start)  # the zone they are in: the map, or a part of it cut off from the rest
    world = world or world_for(snapshot, avoid)
    extras = [e for e in extra or [] if isinstance(e, dict) and str(e.get("title") or "").strip() and _pair((e.get("x"), e.get("y")))]
    # a flight master the player asked to have on the route is opened there: the way on can be a flight from him
    opened = []
    for line in snapshot.get("flightMasters") or []:
        m = _FLIGHT.match(str(line))
        if not m or "not discovered" not in m.group("tag"):
            continue
        fx, fy = float(m.group("x")), float(m.group("y"))
        for e in extras:
            xy = _pair((e.get("x"), e.get("y")))
            if (world.name(str(e.get("zone") or home)) == m.group("zone") and abs(xy[0] - fx) <= 1.0 and abs(xy[1] - fy) <= 1.0
                    and not _named(str(e["title"]), skip)):
                opened.append((re.sub(r"\s*<[^>]*>", "", m.group("who")).strip(), region_of(m.group("zone"), (fx, fy)), (fx, fy)))
    ends_in = next((str(e.get("zone") or "") for e in extras if e.get("when") == "last" and e.get("zone")), None)
    # (the zone a new route is named to end in is where the player goes, with or without something to do there)
    legs, targets, passed = _legs(then, zone, start, world, _wanted(snapshot, home, skip, use, new_quests, extras, along),
                                  opened, keep_last=end is not None or not updating, ends_in=ends_in)
    zones = list(dict.fromkeys(leg["zone"] for leg in legs))
    elsewhere: dict[str, int] = {}  # zones with stops that are not on this route
    several: list[str] = []  # objectives with more than one source, where use named none: the model may choose another
    picked: dict[str, str] = {}  # the source taken for each objective ("<quest id>:<label>"), as the game data names it
    chosen: set[str] = set()  # the names in use that some objective has as a source
    level = player.get("level") if isinstance(player.get("level"), (int, float)) else None

    def above(option: str) -> str:
        """A source with how far above the player its level is: what makes a creature slow and risky to kill."""
        lv = re.search(r"\(level (\d+)(?:-(\d+))?", option)
        if not lv or level is None or int(lv.group(1)) <= level:
            return option
        lo, hi = int(lv.group(1)) - int(level), int(lv.group(2) or lv.group(1)) - int(level)
        return option + f" [{lo}{'-' + str(hi) if hi != lo else ''} level{'s' if hi > 1 else ''} above the player]"
    have = {q.get("id") for q in snapshot.get("quests") or [] if isinstance(q, dict)}  # the quests in the log
    # ... and the ones the player is through with (turned in, or dropped): not to be picked up again, the next part of
    # a chain taken before the part it follows among them
    have |= {int(i) for i in list(snapshot.get("through") or []) + list(snapshot.get("routeDone") or [])
             if isinstance(i, (int, float))}

    go_on: list[tuple] = []  # zones the route goes on into by itself: (zone, after which part of it or None, quest)

    def other(z: str) -> str:
        """Counts a stop in a zone off the route; says whether the app knows the way there from the route."""
        elsewhere[z] = elsewhere.get(z, 0) + 1
        known = any(world.route(leg["zone"], leg["entry"], z) is not None for leg in legs)
        # A part of a map of the route that is cut off from the rest (an island, a village behind a portal): the
        # quest data names the map, so nothing tells the model that it is a zone of its own. The route goes on there.
        # (When the model named the zones itself, or the route on the map is being updated, the zones are the
        # plan's: nothing is added to them.)
        if known and not then and not updating and any(map_of(leg["zone"]) == map_of(z) for leg in legs):
            go_on.append((z, None, ""))
        return "another zone" + (", which the app knows the way to" if known else "")
    # the quests offered now that this route picks up as they are: a chain's next part among them needs no stop of
    # its own after the part before
    offered_now = {o["id"] for o in snapshot.get("nearbyQuests") or [] if isinstance(o, dict) and isinstance(o.get("id"), int)
                   and (new_quests is True or _named(str(o.get("title") or ""), new_quests if isinstance(new_quests, list) else [])
                        or _along(o, str(o.get("title") or ""), along))}

    def add_quest(q: dict, pick: int | None = None) -> None:
        """The stops of a quest: what is left to do for it, its turn-in, the quest that follows. ``pick``: the stop
        where the quest is picked up, for one that is not in the log yet (everything of it comes after that)."""
        if not isinstance(q, dict) or not q.get("title"):
            return
        title, status = str(q["title"]), str(q.get("status") or "")
        qid = {"id": q["id"]} if isinstance(q.get("id"), int) else {}  # the parts of a chain often share a title
        # (a quest still to be taken: nothing of it lies on a part of the route before the one it is taken on. A
        # letter for the smith of the village the player stands in was "turned in" first and picked up two zones on.)
        floor = nodes[pick]["leg"] if pick is not None else 0
        # (a quest the route is to do first: in a zone the route comes through twice, on the first visit, not left
        # for the way back)
        wanted_first = _named(title, first) and not _named(title, last)
        kind = str(q.get("type") or "").lower()
        note = ", ".join(x for x in (f"quest level {q['level']}" if q.get("level") else "",
                                     kind if kind in _GROUP_TYPES else "") if x)
        if _named(title, skip):
            left_out.append(f"[{title}]: left out as asked")
            return
        if status.startswith("FAILED"):
            left_out.append(f"[{title}]: failed, abandon it and pick it up again")
            return
        ready = status.startswith("READY")
        todo = "; ".join(p.replace("[TO DO]", "").strip() for p in str(q.get("objectives") or "").split(";")
                         if "[DONE]" not in p and p.strip())
        skill = re.search(r"[^;\[]*skill to \d+", todo, re.I)
        if skill and not ready:
            left_out.append(f"[{title}]: needs a skill, not a place ({skill.group(0).strip()})")
            return
        objectives, followers, blocked = [], [], False
        # what the quest log calls the open objectives, for the ones the database only knows as "Credit"
        every = [(re.sub(r"^\d+/\d+\s+", "", re.sub(r"\[(TO DO|DONE)\]", "", p).strip()), "[DONE]" in p)
                 for p in str(q.get("objectives") or "").split(";") if p.strip()]
        open_texts = [text for text, done in every if not done]
        lines = [str(line) for line in q.get("whereToGet") or []]
        if not ready:
            for n, line in enumerate(lines):
                label, sources = split_line(line)
                # an objective that is done is no stop (the add-on says where every objective is found, done or not)
                low = label.strip().lower()
                if (low and any(low in text.lower() for text, done in every if done)
                        and not any(low in text.lower() for text, done in every if not done)):
                    continue
                options = [o for o in sources.split("; ") if _SOURCE.match(o)]
                named = [o for o in options if _named(_name(_SOURCE.match(o).group("desc")), use)]
                chosen.update(_name(_SOURCE.match(o).group("desc")).lower() for o in named)
                key = f"{q.get('id')}:{label.strip().lower()}"
                was = str((snapshot.get("keepSources") or {}).get(key) or "") \
                    if isinstance(snapshot.get("keepSources"), dict) else ""
                if not named and was:
                    # An update keeps the source the route has for it: the nearest changes as the player walks, and
                    # the stop must not jump across the map with it. (The quest log names the few nearest sources
                    # only: the one the route took is still where it was when the log names it no more.)
                    w = _SOURCE.match(was)
                    wname = (_name(w.group("desc")) if w else was).lower()
                    named = [o for o in options if _name(_SOURCE.match(o).group("desc")).lower() == wname] \
                        or ([was] if w else [])
                taken = named[0] if named else _surest(options, level) if options else sources.split("; ")[0]
                picked[key] = taken
                m = _SOURCE.match(taken)
                if len(options) > 1 and not named:
                    several.append(f"{label} for [{title}]: {above(taken)} — taken, "
                                   + ("the nearest" if taken is options[0] else "the surest (not the nearest)") + "; also "
                                   + "; ".join(above(o) for o in options if o is not taken))
                xy = (float(m.group("x")), float(m.group("y"))) if m else None
                z = region_of(m.group("zone"), xy) if m else ""
                leg = _leg_of(legs, z, xy, floor, early=wanted_first) if m else None
                if not m:
                    left_out.append(f"[{title}]: no known place for {label}")
                    blocked = True
                elif leg is None and z in zones:
                    left_out.append(f"[{title}]: {label} in {z} ({m.group('x')}, {m.group('y')}), "
                                    f"not where the route passes through {z}")
                    blocked = True
                elif leg is None:
                    left_out.append(f"[{title}]: {label} in {z} ({m.group('x')}, {m.group('y')}), {other(z)}")
                    blocked = True
                else:
                    how = _HOW.match(m.group("desc"))
                    creature = bool(how) and how.group(1).lower() in ("kill", "find", "dropped by")
                    name, what = _name(m.group("desc")), m.group("desc")
                    if _CREDIT.match(name) and len(lines) in (len(open_texts), len(every)):
                        # An invisible marker of the database (a spot to use an item at, a place to reach): the stop
                        # is what the quest log calls that objective, and there is no creature to watch for. The
                        # game lists these spots in the order of the objectives, the ones that are done too.
                        if len(lines) == len(open_texts):
                            name = open_texts[n]
                        elif every[n][1]:
                            continue  # done already: not a place to go to
                        else:
                            name = every[n][0]
                        label, what, creature = name, name, False
                    # An object that lies in one of several places (mud on a lake bed, a chest that moves): the
                    # stop is where to search, not the spot. The list and the arrow say so (spots).
                    spots = re.search(r"(\d+) spawn points?", taken)
                    spots = int(spots.group(1)) if spots and how and how.group(1).lower() == "from object" else 0
                    around = ({"spots": spots}, f" (it lies in one of {spots} places around there: search the area)") \
                        if spots > 1 and not _CREDIT.match(_name(m.group("desc"))) else ({}, "")
                    objectives.append({"title": name, "what": f"[{title}]: {what}{around[1]}",
                                       "xy": xy, "after": [], "leg": leg,
                                       "mob": name if creature else None, "quest": title,
                                       "tasks": [{"kind": "objective", "quest": title, **qid, "label": label, **around[0]}]})
            if not objectives and not blocked:  # still to do, and nothing says where: the turn-in is not a stop yet
                left_out.append(f"[{title}]: no known place for what is left to do" + (f" ({todo})" if todo else ""))
                blocked = True
        if blocked:
            return
        first_node = len(nodes)
        # the first place is the quest's own NPC, others are stand-ins; a turn-in comes after the quest's objectives,
        # so not in a part of the route that lies before one of them
        since = max((o["leg"] for o in objectives), default=floor)
        places = [m for m in (_PLACE.match(p.strip()) for p in str(q.get("turnIn") or "").split(";")) if m]
        spots = [(m, _leg_of(legs, region_of(m.group("zone"), (float(m.group("x")), float(m.group("y")))),
                             (float(m.group("x")), float(m.group("y"))), since, early=wanted_first)) for m in places]
        on_route = [(m, leg) for m, leg in spots if leg is not None]
        if on_route:
            m, leg = on_route[0]
            turn_in = {"title": m.group("name"), "what": f"turn in [{title}]",
                       "xy": (float(m.group("x")), float(m.group("y"))), "leg": leg,
                       "after": list(range(first_node, first_node + len(objectives))),
                       "tasks": [{"kind": "turn_in", "quest": title, **qid}]}
            objectives.append(turn_in)
            # The quest that follows this one is taken where it is offered: at the same NPC it belongs to the stop of
            # the turn-in (which then stays until it is taken), elsewhere on the route it is a stop after it.
            follow = _FOLLOW.match(str(q.get("next") or ""))
            giver = follow and _PLACE.match(follow.group("places").split(";")[0].strip())
            if giver and _named(follow.group("title"), skip):
                left_out.append(f"[{follow.group('title')}]: left out as asked")
            elif giver and q.get("nextId") in offered_now:
                pass  # offered now and to be picked up as it is (named, or taken along): one stop for it, not two
            elif giver and q.get("nextId") not in have:
                then, gxy = follow.group("title"), (float(giver.group("x")), float(giver.group("y")))
                gzone = region_of(giver.group("zone"), gxy)
                task = {"kind": "pick_up", "quest": then,
                        **({"id": q["nextId"]} if isinstance(q.get("nextId"), int) else {})}
                gleg = _leg_of(legs, gzone, gxy, leg)
                if (gleg == leg and giver.group("name").lower() == m.group("name").lower()
                        and abs(gxy[0] - turn_in["xy"][0]) <= 1.0 and abs(gxy[1] - turn_in["xy"][1]) <= 1.0):
                    turn_in["what"] += f", then pick up [{then}]"
                    turn_in["tasks"].append(task)
                elif gleg is not None:
                    followers.append({"title": giver.group("name"), "what": f"pick up [{then}], which follows [{title}]",
                                      "xy": gxy, "leg": gleg, "after": [first_node + len(objectives) - 1],
                                      "tasks": [task], "late": _named(title, last), "early": False,
                                      "note": f"quest level {follow.group('level')}" if follow.group("level") else ""})
                else:
                    left_out.append(f"[{then}]: follows [{title}], from {giver.group('name')} in {gzone} "
                                    f"({giver.group('x')}, {giver.group('y')}), another zone")
        elif places:
            m = places[0]
            z = region_of(m.group("zone"), (float(m.group("x")), float(m.group("y"))))
            where = f"{m.group('name')} in {z} ({m.group('x')}, {m.group('y')})"
            if z in zones:
                left_out.append(f"[{title}]: turn in to {where}, not where the route passes through {z}"
                                + (" after that" if since else ""))
                # what the quest wants is on the route, and it is turned in in a zone the route has left by then:
                # the route comes back there, after the last zone something of it is done in
                if objectives or pick is not None:
                    go_on.append((z, since, title))
            else:
                left_out.append(f"[{title}]: turn in to {where}, {other(z)}")
        elif ready:
            left_out.append(f"[{title}]: no known place for the turn-in")
        for node in objectives:
            node.update(note=note, late=_named(title, last), early=_named(title, first) and not _named(title, last))
        if pick is not None:
            for node in objectives + followers:
                node["after"] = list(node["after"]) + [pick]
        nodes.extend(objectives + followers)

    for q in snapshot.get("quests") or []:
        add_quest(q)
    # Quests to pick up. The ones the model named (new_quests) wherever they are on the route's zones. And, because
    # taking a quest costs nothing, the ones whose giver stands next to the player or to a stop of the route, the
    # nearest few; the rest of what is offered is named in the result, for the model to choose from.
    scale = _scale(snapshot, start, home) or world.scale(home)

    def scale_of(z: str):
        """Meters per map unit in a zone: measured by the game on the player's map, else from the size of its map."""
        return scale if map_of(z) == home and scale else world.scale(z)
    # The stops the player asked for besides the quests: on the part of the route their zone is. They are put on it
    # further down, once every quest stop is there: a stop asked for at a quest's NPC is that quest's stop, not a
    # second one at the same place.
    asked = []
    for e in extras:
        xy = _pair((e.get("x"), e.get("y")))
        z = region_of(world.name(str(e.get("zone") or "").strip()) if e.get("zone") else home, xy)
        title = str(e["title"]).strip()[:60]
        leg = _leg_of(legs, region_of(map_of(z), xy), xy, early=e.get("when") == "first")
        if _named(title, skip):
            continue
        if leg is None:
            left_out.append(f"{title}: in {z} ({xy[0]:g}, {xy[1]:g}), {other(z) if z not in zones else 'not where the route passes through ' + z}")
            continue
        asked.append((e, title, xy, leg, [t for t in e.get("tasks") or [] if isinstance(t, dict)]))

    def put_asked() -> None:
        for e, title, xy, leg, tasks in asked:
            name = re.sub(r"\s*\(.*$", "", title).strip().lower()
            # the same spot, or the NPC of that name close by; on the part of the route the stop would go to, else on
            # another part through the same zone (the quest decides when its NPC is visited)
            close = [n for n in nodes if legs[n["leg"]]["zone"] == legs[leg]["zone"]
                     and ((abs(n["xy"][0] - xy[0]) <= 0.3 and abs(n["xy"][1] - xy[1]) <= 0.3)
                          or (n["title"].lower() == name and abs(n["xy"][0] - xy[0]) <= 1.0 and abs(n["xy"][1] - xy[1]) <= 1.0))]
            same = next((n for n in close if n["leg"] == leg), close[0] if close else None)
            if same:  # the route stops there already (the NPC of a quest): one stop, with what the player asked for
                # ... and with the note of the stop asked for, unless it only says again what the quests there say
                # ("pick up Oh Brother and Collecting Memories" at the giver of both)
                note = str(e.get("note") or "").strip()[:120]
                said = [re.sub(r"[^a-z0-9 ]", "", str(t.get("quest") or "").lower()).strip() for t in same["tasks"]]
                plain = re.sub(r"[^a-z0-9 ]", "", note.lower())
                if note and not any(q and q in plain for q in said):
                    same["what"] += f"; {note}"
                    same["asked_note"] = note
                same["tasks"] = same["tasks"] + tasks
                same["extra"] = True
                same["asked"] = same.get("asked", []) + [title]
                continue
            nodes.append({"title": title, "what": str(e.get("note") or "").strip()[:120] or "a stop the player asked for",
                          "xy": xy, "after": [], "leg": leg, "mob": str(e.get("mob") or "").strip()[:60] or None,
                          "quest": None, "tasks": tasks, "note": "", "late": e.get("when") == "last",
                          "early": e.get("when") == "first", "extra": True})
    anchors = [(0, start)] + [(node["leg"], node["xy"]) for node in nodes] + [(leg, xy) for _, _, xy, leg, _ in asked]
    # (where the route comes into a zone and leaves it is on the route too: a guard by the road out is on the way)
    anchors += [(i, part[key]) for i, part in enumerate(legs) for key in ("entry", "via") if part.get(key)]

    def placed(q: dict) -> bool | None:
        """Is everything the quest wants, and its turn-in, in the zones of the route? None when the data does not say
        where its places are (a quest read around the player only)."""
        spots = []
        for line in q.get("whereToGet") or []:
            m = next((mm for mm in (_SOURCE.match(o) for o in split_line(line)[1].split("; ")) if mm), None)
            if not m:
                return False  # something it wants has no known place
            spots.append((m.group("zone"), (float(m.group("x")), float(m.group("y")))))
        t = next((m for m in (_PLACE.match(part.strip()) for part in str(q.get("turnIn") or "").split(";")) if m), None)
        if t:
            spots.append((t.group("zone"), (float(t.group("x")), float(t.group("y")))))
        if not spots:
            return None
        return all(_leg_of(legs, region_of(z, at_xy), at_xy) is not None for z, at_xy in spots)

    candidates = []  # the quests that could be taken along: (how far the giver is, title, leg, giver right by the route)
    by_the_way, offered = [], []
    for q in snapshot.get("nearbyQuests") or []:
        m = isinstance(q, dict) and _WHERE.match(str(q.get("where") or ""))
        title = str(q.get("title") or "") if isinstance(q, dict) else ""
        if not m or not title or _named(title, skip) or q.get("id") in have:
            continue
        xy = (float(m.group(2)), float(m.group(3)))
        leg = _leg_of(legs, region_of(m.group(1), xy), xy, early=True)  # a quest is taken at the first chance
        if leg is None:
            continue
        node = {"title": str(q.get("giver") or title), "what": f"pick up [{title}]", "xy": xy, "after": [],
                "late": False, "early": _named(title, first), "leg": leg,
                "note": f"quest level {q['level']}" if q.get("level") else "",
                "tasks": [{"kind": "pick_up", "quest": title, **({"id": q["id"]} if isinstance(q.get("id"), int) else {})}]}
        if new_quests is True or _named(title, new_quests):
            nodes.append(node)
            if q.get("whereToGet") or q.get("turnIn"):  # the whole quest is planned, not only where it is taken
                add_quest(_as_taken(q), pick=len(nodes) - 1)
            continue
        if level is not None and isinstance(q.get("minLevel"), (int, float)) and q["minLevel"] > level:
            continue  # not yet theirs to take
        if level is not None and isinstance(q.get("level"), (int, float)) and q["level"] <= _gray(level):
            continue  # gives this character nothing any more
        measured = scale_of(legs[leg]["zone"])
        sx, sy = measured or MAP_ASPECT
        off = min((math.hypot((xy[0] - a[0]) * sx, (xy[1] - a[1]) * sy) for at, a in anchors if at == leg), default=None)
        close = off is not None and off <= (NEAR_M if measured else NEAR_UNITS)
        # What could be taken along (plan_route decides, by the way each costs): of the character's level, up to
        # ABOVE_MAX levels above and not a group's; the giver by the route, a little further when the data says where
        # the quest's places are; and those places in the zones of the route.
        known = placed(q)
        reach = close if known is None else off is not None and off <= (ALONG_M if measured else ALONG_UNITS)
        too_high = level is not None and isinstance(q.get("level"), (int, float)) and q["level"] > level + ABOVE_MAX
        # (and not a long grind: ten fish at a drop in two is twenty murlocs, which is no errand on the way)
        if (reach and known is not False and not too_high and str(q.get("type") or "").lower() not in _GROUP_TYPES
                and _kills(q) <= GRIND_KILLS):
            candidates.append((off, q["id"] if isinstance(q.get("id"), int) else title, leg, close, title))
        # the quests taken along are named (along): by plan_route for a route that is being made, by the plan of
        # the route on the map for its update, which keeps those and adds none
        near = _along(q, title, along)
        (by_the_way if near else offered).append((off if off is not None else math.inf, title, node, q, m))
    by_the_way.sort(key=lambda x: x[0])
    # the nearest few of each zone of the route (a town full of quests does not bury the route)
    taken, taken_keys, rest, per_leg = [], [], [], {}
    for item in by_the_way:
        _, title, node, q, _ = item
        if per_leg.get(node["leg"], 0) >= NEAR_MAX:
            rest.append(item)
            continue
        per_leg[node["leg"]] = per_leg.get(node["leg"], 0) + 1
        node["what"] += ", offered on the way"
        nodes.append(node)
        if q.get("whereToGet") or q.get("turnIn"):
            add_quest(_as_taken(q), pick=len(nodes) - 1)
        taken.append(title)
        taken_keys.append(q["id"] if isinstance(q.get("id"), int) else title)
    offered = sorted(rest + offered, key=lambda x: x[0])
    put_asked()
    nodes = _merge(nodes)
    rounds = _detours(snapshot)
    if go_on and not _went_on:
        named = [{"zone": hop["zone"], "via": hop.get("via"), "via_title": hop.get("title") or "", "arrive": hop.get("arrive")}
                 for hop in targets]

        def again(hops: list[dict]) -> dict:
            return _route(snapshot, new_quests=new_quests, skip=skip, last=last, end=end, end_title=end_title,
                          along=along, updating=updating, first=first, then=hops, use=use, extra=extra, world=world,
                          avoid=avoid, _went_on=True)

        def missing(route: dict) -> bool:
            return any(title and str(line).startswith(f"[{title}]: turn in") for _, _, title in go_on
                       for line in route.get("left_out") or [])
        # a cut-off part of a map of the route: after everything else; the way back to turn a quest in: after the
        # last zone named that something of the quest is done in
        smart = list(named)
        for z, after, _ in sorted(set(go_on), key=lambda g: -1 if g[1] is None else g[1], reverse=True):
            if after is None:
                at_hop = len(smart)
            else:
                held = [n for n, hop in enumerate(named) if hop["zone"] == legs[after]["zone"]]
                at_hop = held[-1] + 1 if held else 0
            if not (at_hop and smart[at_hop - 1]["zone"] == z) and not (at_hop < len(smart) and smart[at_hop]["zone"] == z):
                smart.insert(at_hop, {"zone": z, "via": None, "via_title": "", "arrive": None})
        route = again(smart)
        if missing(route):  # the visit came too early for it after all: the way back is the end of the route
            late = list(named)
            for z in dict.fromkeys(z for z, _, _ in go_on):
                if not (late and late[-1]["zone"] == z):
                    late.append({"zone": z, "via": None, "via_title": "", "arrive": None})
            other_way = again(late)
            if not missing(other_way):
                route = other_way
        return route
    # A zone with nothing to do in it, entered only to come back to the zone before it, is not on the route (the
    # zones of a route planned earlier, when the quests in one of them are done by now).
    used = {node["leg"] for node in nodes}
    for k in range(1, len(legs) - 1):
        if k not in used and legs[k + 1]["zone"] == legs[k - 1]["zone"] and not _again:
            rest = [{"zone": leg["zone"], "via": legs[n - 1]["via"], "via_title": legs[n - 1]["via_title"],
                     "arrive": leg["entry"]} for n, leg in enumerate(legs) if n >= 1 and n not in (k, k + 1)]
            return _route(snapshot, new_quests=new_quests, skip=skip, last=last, end=end, end_title=end_title, along=along,
                          updating=updating, _went_on=_went_on,
                              first=first, then=rest, use=use, extra=extra, world=world, avoid=avoid, _again=True)
    stops, total, unmeasured = [], 0, False
    for k, leg in enumerate(legs):
        nxt = legs[k + 1] if k + 1 < len(legs) else None
        index = [i for i, node in enumerate(nodes) if node["leg"] == k]
        local = {i: n for n, i in enumerate(index)}
        part = [dict(nodes[i]) for i in index]
        # what matters most first, the hard ones last; what a stop waits for in an earlier part is done by then
        for node in part:
            node["after"] = sorted(local[a] for a in node["after"] if a in local)
        for node in part:
            if node["late"]:
                node["after"] = sorted(set(node["after"]) | {n for n, o in enumerate(part) if not o["late"]})
            elif not node["early"]:
                node["after"] = sorted(set(node["after"]) | {n for n, o in enumerate(part) if o["early"]})
        measured = scale_of(leg["zone"])
        unmeasured = unmeasured or (bool(index) and not measured)
        sx, sy = measured or MAP_ASPECT

        def at(xy, sx=sx, sy=sy) -> tuple[float, float]:
            return xy[0] * sx, xy[1] * sy

        def way(a, b, measured=measured) -> int | None:
            return int(round(math.hypot(a[0] - b[0], a[1] - b[1]))) if measured and a and b else None
        for node in part:
            node["at"] = at(node["xy"])
        goal = leg["via"] if nxt else end
        pos = at(leg["entry"]) if leg["entry"] else None
        here = rounds.get(map_of(leg["zone"])) or []

        def back(p, sx=sx, sy=sy):  # map coordinates of a position
            return p[0] / sx, p[1] / sy

        def walk(p, q, here=here, sx=sx, sy=sy, at=at) -> float:  # the way between two positions, round if walked so
            via = here and _round_via(back(p), back(q), here)
            if via:
                v = at(via)
                return math.hypot(p[0] - v[0], p[1] - v[1]) + math.hypot(v[0] - q[0], v[1] - q[1])
            return math.hypot(p[0] - q[0], p[1] - q[1])
        for i in _order(pos, part, at(goal) if goal else None, walk if here else None) if part else []:
            node = part[i]
            via = pos and here and _round_via(back(pos), node["xy"], here)
            if via:  # the straight way is blocked: the arrow leads round first
                meters = way(pos, at(via))
                total += meters or 0
                stops.append({"title": "The way round", "note": "",
                              "what": "go round: the straight way is blocked (the player walked this way before)",
                              "x": via[0], "y": via[1], "zone": map_of(leg["zone"]), "part": leg["zone"],
                              "meters": meters, "mob": None, "quest": None, "tasks": []})
                pos = at(via)
            meters = way(pos, node["at"])
            total += meters or 0
            stops.append({"title": node["title"], "what": node["what"], "note": node["note"],
                          **({"asked_note": node["asked_note"]} if node.get("asked_note") else {}), "x": node["xy"][0],
                          "y": node["xy"][1], "zone": map_of(leg["zone"]), "part": leg["zone"], "meters": meters,
                          "mob": node.get("mob"), "quest": node.get("quest"), "tasks": node.get("tasks") or [],
                          **({"extra": True} if node.get("extra") else {}),
                          **({"asked": node["asked"]} if node.get("asked") else {})})
            pos = node["at"]
        last = stops[-1] if stops and stops[-1]["part"] == leg["zone"] and index else None
        if (nxt and leg["via"] and last and abs(last["x"] - leg["via"][0]) <= 0.5 and abs(last["y"] - leg["via"][1]) <= 0.5
                and not any(t.get("kind") == "travel" for t in last["tasks"])):
            # the way on starts where the last stop is (a flight master just opened): one stop for both
            # (a flight from the flight master the stop is at says where to, not his name a second time:
            # "open the flight path, then fly to Gryth Thurden, Ironforge")
            via = leg["via_title"] or "go on"
            own = re.match(re.escape(re.sub(r"\s*\(.*$", "", last["title"])) + r"\s*\((.+)\)$", via)
            # (not twice: the note of a stop the player asked for comes back from the model with what was added
            # to it the last time, "open the flight path, then fly to Thor, Westfall")
            more = f"then {own.group(1)}" if own else f"then {via}: go on to {nxt['zone']}"
            if more.lower() not in last["what"].lower():
                last["what"] += ", " + more
            last["tasks"] = last["tasks"] + [{"kind": "travel", "zone": map_of(nxt["zone"])}]
        elif nxt and leg["via"]:  # the way into the next zone is a stop: the route says how the player gets there
            meters = way(pos, at(leg["via"]))
            total += meters or 0
            stops.append({"title": leg["via_title"] or f"The way to {nxt['zone']}", "what": f"go on to {nxt['zone']}",
                          "note": "", "x": leg["via"][0], "y": leg["via"][1], "zone": map_of(leg["zone"]),
                          "part": leg["zone"], "meters": meters, "mob": None, "quest": None,
                          "tasks": [{"kind": "travel", "zone": map_of(nxt["zone"])}]})
    # Stops one right after the other at the same spot (two NPCs side by side) are one stop, as the add-on shows them:
    # the first one's name, what each is for, and whose quest it is when it is the other NPC's.
    joined = []
    for st in stops:
        prev = joined[-1] if joined else None
        if (prev and prev["zone"] == st["zone"] and abs(prev["x"] - st["x"]) <= 0.3 and abs(prev["y"] - st["y"]) <= 0.3
                and not any(t.get("kind") == "travel" for t in prev["tasks"] + st["tasks"])):
            other = re.sub(r"\s*\(.*$", "", st["title"]).strip()
            mine = re.sub(r"\s*\(.*$", "", prev["title"]).strip()
            prev["tasks"] = prev["tasks"] + [dict(t, at=other) if other.lower() != mine.lower() and t.get("quest") else t
                                             for t in st["tasks"]]
            prev["what"] = "; ".join(x for x in (prev["what"], (f"{other}: " if other.lower() != mine.lower() else "") + st["what"]) if x)
            prev["note"] = "; ".join(dict.fromkeys(x for x in (prev["note"], st["note"]) if x))
            prev["mob"] = prev["mob"] or st["mob"]
            prev["extra"] = prev.get("extra") or st.get("extra")
            continue
        joined.append(st)
    stops[:] = joined
    hops = len(legs) > 1
    if stops:
        length = (f", about {total / 1000:.1f} km" + (f" in {home}" if hops and unmeasured else
                                                      " on foot" if hops else "")) if scale else ""
        onward = "".join(f", then {leg['zone']}" for leg in legs[1:])
        ending = f", ending near {end_title or f'{end[0]:g}, {end[1]:g}'}" if end else ""
        here = zone if zone == home else f"{zone}, {home}"
        lines = [f"Route from {start[0]:g}, {start[1]:g} ({here}): {len(stops)} stops{length}{onward}{ending}."]
        for n, st in enumerate(stops, 1):
            far = "" if st["meters"] is None else (f", {st['meters']} m" if n == 1 else f", +{st['meters']} m")
            where = f"{st['x']:g}, {st['y']:g}" if st["part"] == zone else f"{st['part']} {st['x']:g}, {st['y']:g}"
            lines.append(f"{n}. {st['title']} ({where}){far}: {st['what']}" + (f" — {st['note']}" if st["note"] else ""))
    else:
        lines = [f"Nothing in the quest log has a place to walk to in {' or '.join(zones)}."]
    if several:
        lines.append("Check the sources: these objectives have more than one, and with none named in use the nearest "
                     "was taken. When another is surer or quicker for this character (a common creature near their "
                     "level with many spawn points and a good drop chance, rather than a lone one, a rare, an elite or "
                     "one well above them), call plan_route again at once with it in use, before you answer:")
        lines += ["- " + x for x in several]
    high = sorted({(st["zone"], ZONE_LEVELS[st["zone"]]) for st in stops
                   if level is not None and ZONE_LEVELS.get(st["zone"], 0) >= level + DANGER_LEVELS
                   and (st.get("extra") or any(t.get("kind") != "travel" for t in st["tasks"]))})
    if high:
        lines.append("Far above the character (level " + f"{int(level)}): " + ", ".join(f"{z} (a zone for level {n} and up)" for z, n in high)
                     + ". Stops there are on the route because they were named; leave them out unless the player asked "
                     "for exactly these.")
    lines += _flight_masters(snapshot, home, start, stops, scale)
    unknown = [str(u).strip("[] ") for u in use or [] if str(u).strip("[] ").lower() not in chosen]
    if unknown:
        lines.append("No objective of the quest log has a source called " + ", ".join(unknown)
                     + " (use names a creature, an object or a vendor as whereToGet does).")
    if taken:
        lines.append("Picked up on the way, because their givers stand by the route: "
                     + ", ".join(f"[{t}]" for t in taken) + ". Where the game data says what they want, that and the "
                     "turn-in are by the route too, and are stops of it already. Leave one out with skip when the "
                     "player should not take it.")
    if offered:
        lines.append("Also offered here, not on the route: " + "; ".join(
            f"[{title}]" + (f" (level {q['level']})" if q.get("level") else "") + f" from {q.get('giver') or '?'} "
            f"({m.group(1)} {m.group(2)}, {m.group(3)})" for _, title, _, q, m in offered[:6])
            + (". Put the ones worth taking into new_quests." if not updating else
               ". An update adds none of them: they go on the route when the player asks for them."))
    if zone != home:
        through = world.route(zone, start, home)
        lines.append(f"{zone} has no ground way to the rest of {home}: it counts as a zone of its own here, and what "
                     f"lies elsewhere in {home} is reached through "
                     + (f"{through[1][0]['b']} ({through[1][0]['title']})" if through and len(through[1]) > 1
                        else "another map") + ".")
    for visited in dict.fromkeys(leg["zone"] for leg in legs):
        inside = paths_inside(visited)
        if inside:
            lines.append(f"Paths inside {visited} that the map add-on marks (a stop beyond one is reached from where the "
                         "path starts, not straight across): " + "; ".join(inside) + ".")
    known = [k for k, leg in enumerate(legs[:-1]) if leg["known"]]
    if len(known) > 3:  # a long way: every crossing and ride is a stop above already
        lines.append("The ways between the zones are the ones the app knows (the road across each border, the rides, "
                     "the flights the character has): the stops above are the whole way, step by step.")
    else:
        for k in known:
            lines.append(f"The way from {legs[k]['zone']} into {legs[k + 1]['zone']} is the one the app knows: "
                         f"{legs[k]['via_title']}.")
    for k, leg in enumerate(legs[:-1]):
        if not leg["via"]:
            into = legs[k + 1]["zone"]
            lines.append(f"The way from {leg['zone']} into {into} is not on the route: find out where it is (the game "
                         f"data, the web) and call plan_route again with via_x, via_y and via_title "
                         f"for {into}, so that the player is not sent there without knowing how to get there.")
    if left_out or passed:
        lines += ["Not on the route:"] + ["- " + x for x in left_out + passed]
    there = world.route(zone, start, max(elsewhere, key=lambda z: (elsewhere[z], z))) if elsewhere and not hops else None
    if there and there[1]:
        nxt = max(elsewhere, key=lambda z: (elsewhere[z], z))
        lines.append(f"Stops wait in {nxt} ({elsewhere[nxt]}). The way there is known ({there[1][0]['title']}): "
                     f'call plan_route again with then=[{{"zone": "{nxt}"}}] to take the route on there, and add the '
                     "zones after it in the order the player travels. Do not set stops of another zone by hand: they "
                     "would have no way in and no order.")
        more = []
        for z in sorted(elsewhere, key=lambda z: (-elsewhere[z], z)):
            far = world.route(zone, start, z) if z != nxt else None
            if far and far[1]:
                more.append(f"{z} ({elsewhere[z]}, about {far[0] / 1000:.1f} km of way from here)")
        if more:
            lines.append(f"Stops also wait in {', '.join(more)}; {nxt} is about {there[0] / 1000:.1f} km of way from "
                         "here. Name every zone the route should go on into; the app finds their order.")
    elif elsewhere and not hops:
        nxt = max(elsewhere, key=lambda z: (elsewhere[z], z))
        lines.append(f"Stops wait in {nxt} ({elsewhere[nxt]}). To take the route on there, call plan_route again with "
                     f'then=[{{"zone": "{nxt}", "via_x": …, "via_y": …, "via_title": …, "arrive_x": …, "arrive_y": …}}]: '
                     f"via is the way out of {zone} the player takes from where they are (a portal, a boat, a flight "
                     f"master, a gate, a road), in {zone}'s coordinates, and arrive is where they come out in {nxt}; "
                     f"take them from memory or look them up, do not recall them. Do not set stops of another zone by "
                     "hand: they would have no way in and no order.")
    ways = [{"zone": legs[k + 1]["zone"], "via": leg["via"], "title": leg["via_title"], "arrive": legs[k + 1]["entry"]}
            for k, leg in enumerate(legs[:-1])]
    return {"stops": stops, "left_out": left_out, "meters": total if scale else None, "ways": ways,
            "targets": targets, "along": taken_keys, "along_titles": taken, "text": "\n".join(lines),
            "sources": picked,
            "candidates": [(key, leg, close, title) for _, key, leg, close, title in sorted(candidates, key=lambda c: c[0])]}
