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

import math
import re
import statistics

from .ways import known_way, map_of, path, region_of

EXACT_MAX = 12  # stops up to which every order is tried (2^n * n^2 steps); more are ordered nearest-first
_PLACE = re.compile(r"^(?P<name>.*?) \((?P<zone>[^()]+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)\)$")
_SOURCE = re.compile(r"^(?P<desc>.*?) — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)(?: \((?P<m>\d+) m.*)?$")
_NPC_LINE = re.compile(r"— (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?) \((?P<m>\d+) m\)")
_WHERE = re.compile(r"^(.+?) (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)$")
_HOW = re.compile(r"^(kill|use|find|from object|dropped by|sold by)\s+", re.I)
# a quest's "next": "[Grove of the Ancients] (level 17) from Arch Druid Fandral Staghelm (Darnassus 34.8, 9.3)"
_FOLLOW = re.compile(r"^\[(?P<title>.+?)\](?: \(level (?P<level>\d+)\))? from (?P<places>.+)$")
_GROUP_TYPES = ("elite", "group", "dungeon", "raid")
_CREDIT = re.compile(r"^(kill |quest )?credit\b", re.I)  # the database's name for an invisible objective marker
MAP_ASPECT = (1.5, 1.0)  # a zone map is three wide and two high: the shape of distances when nothing was measured
MAX_HOPS = 8  # zones a route goes on into
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


def _legs(then, zone: str, start) -> list[dict]:
    """The route's parts in walking order: {"zone", "entry": where the player comes in, "via": the way out to the
    next part (None for the last), "via_title"}. The first is the player's zone from where they stand."""
    legs = [{"zone": zone, "entry": start, "via": None, "via_title": None, "known": False}]
    for hop in ([then] if isinstance(then, dict) else then if isinstance(then, list) else []):
        if len(legs) > MAX_HOPS or not isinstance(hop, dict) or not str(hop.get("zone") or "").strip():
            continue
        name, via, arrive = str(hop["zone"]).strip(), _pair(hop.get("via")), _pair(hop.get("arrive"))
        title = str(hop.get("via_title") or "").strip()[:60]
        name = region_of(map_of(name), arrive) if arrive else name  # the part of the map the player comes out in
        if name == legs[-1]["zone"]:
            continue
        # what the app knows comes before what the model recalls: the way there over known ways, through the zones
        # between when the two are not joined directly
        for step in path(legs[-1]["zone"], name):
            known = known_way(legs[-1]["zone"], step)
            legs[-1].update(via=known["via"], via_title=known["title"], known=True)
            legs.append({"zone": step, "entry": known["arrive"], "via": None, "via_title": None, "known": False})
        if legs[-1]["zone"] != name:
            legs[-1].update(via=via, via_title=title)
            legs.append({"zone": name, "entry": arrive, "via": None, "via_title": None, "known": False})
    return legs


def _leg_of(legs: list[dict], zone: str, xy, since: int = 0) -> int | None:
    """The part of the route a place belongs to (not before part ``since``); None when the route does not get there.
    A zone that comes twice is one the player leaves and comes back to (a village behind a portal, an island): on
    the first visit only what lies on the way out is done. The rest waits for the visit after it, unless that one
    comes back in where the first went out: then the route never gets to the rest of the zone."""
    found = [k for k, leg in enumerate(legs) if leg["zone"] == zone]
    if len(found) < 2:
        return found[0] if found and found[0] >= since else None
    if map_of(zone) != zone:  # a cut-off part of a map is small and all of it can be walked: the first visit does it
        return next((k for k in found if k >= since), None)
    head, back = legs[found[0]], legs[found[-1]]
    if head["entry"] and head["via"]:
        reach = max(ON_THE_WAY_OUT * _flat(head["entry"], head["via"]), ON_THE_WAY_OUT_MIN)
        near = min(_flat(xy, head["entry"]), _flat(xy, head["via"])) <= reach
        same_side = bool(back["entry"]) and _flat(back["entry"], head["via"]) <= reach
        if near and found[0] >= since:
            return found[0]
        if (near and back["entry"] and not same_side) or (not near and same_side):
            return None
    return found[-1] if found[-1] >= since else None


def _name(desc: str) -> str:
    """'kill Boar (level 5)' -> 'Boar'."""
    return re.sub(r"\s*\((level|rare|elite)[^)]*\)", "", _HOW.sub("", desc)).strip()


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
            for src in str(line).split(": ", 1)[-1].split("; "):
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


def _order(start, nodes: list[dict], end) -> list[int]:
    """Indices of ``nodes`` in the order of the shortest path from ``start`` (ending as near to ``end`` as the way
    allows when it is given); node["after"] (indices) come before the node. Positions are node["at"]."""
    def dist(a, b) -> float:
        return 0.0 if a is None else math.hypot(a[0] - b[0], a[1] - b[1])  # no start: the path begins where it likes
    n = len(nodes)
    need = [sum(1 << j for j in node["after"]) for node in nodes]
    if n > EXACT_MAX:  # nearest first among the stops whose turn has come
        order, done, pos = [], 0, start
        while len(order) < n:
            ready = [i for i in range(n) if not done >> i & 1 and need[i] & done == need[i]]
            i = min(ready, key=lambda k: (dist(pos, nodes[k]["at"]), k))
            order.append(i)
            done |= 1 << i
            pos = nodes[i]["at"]
        return order
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


def _merge(nodes: list[dict]) -> list[dict]:
    """One stop per place: stops with the same name at the same spot become one (two quests turned in to one NPC,
    two quests' creatures at one spawn), so the player is never sent to a place twice. Turn-ins merge only with
    turn-ins, after the objectives of all of them; objectives only with objectives."""
    def same(a: dict, b: dict) -> bool:
        return (a["title"].lower() == b["title"].lower() and a["late"] == b["late"] and a["leg"] == b["leg"]
                and a["what"].startswith("turn in") == b["what"].startswith("turn in")
                and abs(a["xy"][0] - b["xy"][0]) <= 1.0 and abs(a["xy"][1] - b["xy"][1]) <= 1.0)

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


def _named(title: str, names) -> bool:
    low = title.lower()
    return any(str(n).strip("[] ").lower() == low for n in names or [])


def plan_route(snapshot: dict, new_quests: bool | list = False, skip: list | None = None, last: list | None = None,
               end: tuple[float, float] | None = None, end_title: str | None = None, first: list | None = None,
               then: dict | list | None = None) -> dict:
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
    player goes afterwards, so the route finishes near them.

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
        return {"stops": [], "left_out": [], "meters": None, "ways": [],
                "text": "The player's position is not known; no route."}
    zone = region_of(home, start)  # the zone they are in: the map, or a part of it cut off from the rest
    legs = _legs(then, zone, start)
    zones = list(dict.fromkeys(leg["zone"] for leg in legs))
    elsewhere: dict[str, int] = {}  # zones with stops that are not on this route
    have = {q.get("id") for q in snapshot.get("quests") or [] if isinstance(q, dict)}  # the quests in the log

    def other(z: str) -> str:
        """Counts a stop in a zone off the route; says whether the app knows the way there from the route."""
        elsewhere[z] = elsewhere.get(z, 0) + 1
        return "another zone" + (", which the app knows the way to" if any(path(leg["zone"], z) for leg in legs) else "")
    for q in snapshot.get("quests") or []:
        if not isinstance(q, dict) or not q.get("title"):
            continue
        title, status = str(q["title"]), str(q.get("status") or "")
        qid = {"id": q["id"]} if isinstance(q.get("id"), int) else {}  # the parts of a chain often share a title
        kind = str(q.get("type") or "").lower()
        note = ", ".join(x for x in (f"quest level {q['level']}" if q.get("level") else "",
                                     kind if kind in _GROUP_TYPES else "") if x)
        if _named(title, skip):
            left_out.append(f"[{title}]: left out as asked")
            continue
        if status.startswith("FAILED"):
            left_out.append(f"[{title}]: failed, abandon it and pick it up again")
            continue
        ready = status.startswith("READY")
        todo = "; ".join(p.replace("[TO DO]", "").strip() for p in str(q.get("objectives") or "").split(";")
                         if "[DONE]" not in p and p.strip())
        skill = re.search(r"[^;\[]*skill to \d+", todo, re.I)
        if skill and not ready:
            left_out.append(f"[{title}]: needs a skill, not a place ({skill.group(0).strip()})")
            continue
        objectives, followers, blocked = [], [], False
        # what the quest log calls the open objectives, for the ones the database only knows as "Credit"
        every = [(re.sub(r"^\d+/\d+\s+", "", re.sub(r"\[(TO DO|DONE)\]", "", p).strip()), "[DONE]" in p)
                 for p in str(q.get("objectives") or "").split(";") if p.strip()]
        open_texts = [text for text, done in every if not done]
        lines = [str(line) for line in q.get("whereToGet") or []]
        if not ready:
            for n, line in enumerate(lines):
                label, _, sources = line.partition(": ")
                m = _SOURCE.match(sources.split("; ")[0])
                xy = (float(m.group("x")), float(m.group("y"))) if m else None
                z = region_of(m.group("zone"), xy) if m else ""
                leg = _leg_of(legs, z, xy) if m else None
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
                    objectives.append({"title": name, "what": f"[{title}]: {what}",
                                       "xy": xy, "after": [], "leg": leg,
                                       "mob": name if creature else None, "quest": title,
                                       "tasks": [{"kind": "objective", "quest": title, **qid, "label": label}]})
            if not objectives and not blocked:  # still to do, and nothing says where: the turn-in is not a stop yet
                left_out.append(f"[{title}]: no known place for what is left to do" + (f" ({todo})" if todo else ""))
                blocked = True
        if blocked:
            continue
        first_node = len(nodes)
        # the first place is the quest's own NPC, others are stand-ins; a turn-in comes after the quest's objectives,
        # so not in a part of the route that lies before one of them
        since = max((o["leg"] for o in objectives), default=0)
        places = [m for m in (_PLACE.match(p.strip()) for p in str(q.get("turnIn") or "").split(";")) if m]
        spots = [(m, _leg_of(legs, region_of(m.group("zone"), (float(m.group("x")), float(m.group("y")))),
                             (float(m.group("x")), float(m.group("y"))), since)) for m in places]
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
            else:
                left_out.append(f"[{title}]: turn in to {where}, {other(z)}")
        elif ready:
            left_out.append(f"[{title}]: no known place for the turn-in")
        for node in objectives:
            node.update(note=note, late=_named(title, last), early=_named(title, first) and not _named(title, last))
        nodes += objectives + followers
    # Quests to pick up. The ones the model named (new_quests) wherever they are on the route's zones. And, because
    # taking a quest costs nothing, the ones whose giver stands next to the player or to a stop of the route, the
    # nearest few; the rest of what is offered is named in the result, for the model to choose from.
    scale = _scale(snapshot, start, home)
    level = player.get("level") if isinstance(player.get("level"), (int, float)) else None
    anchors = [(0, start)] + [(node["leg"], node["xy"]) for node in nodes]
    by_the_way, offered = [], []
    for q in snapshot.get("nearbyQuests") or []:
        m = isinstance(q, dict) and _WHERE.match(str(q.get("where") or ""))
        title = str(q.get("title") or "") if isinstance(q, dict) else ""
        if not m or not title or _named(title, skip) or q.get("id") in have:
            continue
        xy = (float(m.group(2)), float(m.group(3)))
        leg = _leg_of(legs, region_of(m.group(1), xy), xy)
        if leg is None:
            continue
        node = {"title": str(q.get("giver") or title), "what": f"pick up [{title}]", "xy": xy, "after": [],
                "late": False, "early": _named(title, first), "leg": leg,
                "note": f"quest level {q['level']}" if q.get("level") else "",
                "tasks": [{"kind": "pick_up", "quest": title, **({"id": q["id"]} if isinstance(q.get("id"), int) else {})}]}
        if new_quests is True or _named(title, new_quests):
            nodes.append(node)
            continue
        if level is not None and isinstance(q.get("minLevel"), (int, float)) and q["minLevel"] > level:
            continue  # not yet theirs to take
        measured = scale if map_of(legs[leg]["zone"]) == home else None
        sx, sy = measured or MAP_ASPECT
        off = min((math.hypot((xy[0] - a[0]) * sx, (xy[1] - a[1]) * sy) for at, a in anchors if at == leg), default=None)
        near = off is not None and off <= (NEAR_M if measured else NEAR_UNITS)
        (by_the_way if near else offered).append((off if off is not None else math.inf, title, node, q, m))
    by_the_way.sort(key=lambda x: x[0])
    for _, title, node, _, _ in by_the_way[:NEAR_MAX]:
        node["what"] += ", offered on the way"
        nodes.append(node)
    taken = [title for _, title, _, _, _ in by_the_way[:NEAR_MAX]]
    offered = sorted(by_the_way[NEAR_MAX:] + offered, key=lambda x: x[0])
    nodes = _merge(nodes)
    # A zone with nothing to do in it, entered only to come back to the zone before it, is not on the route (the
    # zones of a route planned earlier, when the quests in one of them are done by now).
    used = {node["leg"] for node in nodes}
    for k in range(1, len(legs) - 1):
        if k not in used and legs[k + 1]["zone"] == legs[k - 1]["zone"]:
            rest = [{"zone": leg["zone"], "via": legs[n - 1]["via"], "via_title": legs[n - 1]["via_title"],
                     "arrive": leg["entry"]} for n, leg in enumerate(legs) if n >= 1 and n not in (k, k + 1)]
            return plan_route(snapshot, new_quests=new_quests, skip=skip, last=last, end=end, end_title=end_title,
                              first=first, then=rest)
    stops, total = [], 0
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
        measured = scale if map_of(leg["zone"]) == home else None  # distances were measured on the player's map only
        sx, sy = measured or MAP_ASPECT

        def at(xy, sx=sx, sy=sy) -> tuple[float, float]:
            return xy[0] * sx, xy[1] * sy

        def way(a, b, measured=measured) -> int | None:
            return int(round(math.hypot(a[0] - b[0], a[1] - b[1]))) if measured and a and b else None
        for node in part:
            node["at"] = at(node["xy"])
        goal = leg["via"] if nxt else end
        pos = at(leg["entry"]) if leg["entry"] else None
        for i in _order(pos, part, at(goal) if goal else None) if part else []:
            node = part[i]
            meters = way(pos, node["at"])
            total += meters or 0
            stops.append({"title": node["title"], "what": node["what"], "note": node["note"], "x": node["xy"][0],
                          "y": node["xy"][1], "zone": map_of(leg["zone"]), "part": leg["zone"], "meters": meters,
                          "mob": node.get("mob"), "quest": node.get("quest"), "tasks": node.get("tasks") or []})
            pos = node["at"]
        if nxt and leg["via"]:  # the way into the next zone is a stop: the route says how the player gets there
            meters = way(pos, at(leg["via"]))
            total += meters or 0
            stops.append({"title": leg["via_title"] or f"The way to {nxt['zone']}", "what": f"go on to {nxt['zone']}",
                          "note": "", "x": leg["via"][0], "y": leg["via"][1], "zone": map_of(leg["zone"]),
                          "part": leg["zone"], "meters": meters, "mob": None, "quest": None,
                          "tasks": [{"kind": "travel", "zone": map_of(nxt["zone"])}]})
    hops = len(legs) > 1
    if stops:
        length = (f", about {total / 1000:.1f} km" + (f" in {home}" if hops else "")) if scale else ""
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
    if taken:
        lines.append("Picked up on the way, because their givers stand by the route: "
                     + ", ".join(f"[{t}]" for t in taken) + ". Leave one out with skip when the player should not take it.")
    if offered:
        lines.append("Also offered here, not on the route: " + "; ".join(
            f"[{title}]" + (f" (level {q['level']})" if q.get("level") else "") + f" from {q.get('giver') or '?'} "
            f"({m.group(1)} {m.group(2)}, {m.group(3)})" for _, title, _, q, m in offered[:6])
            + ". Put the ones worth taking into new_quests.")
    if zone != home:
        through = path(zone, home)
        lines.append(f"{zone} has no ground way to the rest of {home}: it counts as a zone of its own here, and what "
                     f"lies elsewhere in {home} is reached through "
                     + (f"{through[0]} ({known_way(zone, through[0])['title']})" if len(through) > 1 else "another map") + ".")
    for k, leg in enumerate(legs[:-1]):
        if leg["known"]:
            lines.append(f"The way from {leg['zone']} into {legs[k + 1]['zone']} is the one the app knows: "
                         f"{leg['via_title']}.")
    for k, leg in enumerate(legs[:-1]):
        if not leg["via"]:
            into = legs[k + 1]["zone"]
            lines.append(f"The way from {leg['zone']} into {into} is not on the route: find out where it is (the game "
                         f"data, the web) and call plan_route again with via_x, via_y and via_title "
                         f"for {into}, so that the player is not sent there without knowing how to get there.")
    if left_out:
        lines += ["Not on the route:"] + ["- " + x for x in left_out]
    if elsewhere and not hops and path(zone, max(elsewhere, key=lambda z: (elsewhere[z], z))):
        nxt = max(elsewhere, key=lambda z: (elsewhere[z], z))
        first = path(zone, nxt)[0]
        lines.append(f"Stops wait in {nxt} ({elsewhere[nxt]}). The way there is known ({known_way(zone, first)['title']}): "
                     f'call plan_route again with then=[{{"zone": "{nxt}"}}] to take the route on there, and add the '
                     "zones after it in the order the player travels. Do not set stops of another zone by hand: they "
                     "would have no way in and no order.")
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
            "text": "\n".join(lines)}
