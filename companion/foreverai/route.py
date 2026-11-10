"""The walking route through the quest log: every place with something to do (objectives, turn-ins, chosen quest
givers), in the order with the shortest way, each turn-in after its own objectives. Computed here so that the model
does not have to order a dozen coordinates in its head. The model decides what belongs on the route (which quests to
leave out, which to do last, which new ones to pick up, where the player goes afterwards); this module orders it."""

from __future__ import annotations

import math
import re
import statistics

EXACT_MAX = 12  # stops up to which every order is tried (2^n * n^2 steps); more are ordered nearest-first
_PLACE = re.compile(r"^(?P<name>.*?) \((?P<zone>[^()]+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)\)$")
_SOURCE = re.compile(r"^(?P<desc>.*?) — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)(?: \((?P<m>\d+) m.*)?$")
_NPC_LINE = re.compile(r"— (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?) \((?P<m>\d+) m\)")
_WHERE = re.compile(r"^(.+?) (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)$")
_HOW = re.compile(r"^(kill|use|find|from object|dropped by|sold by)\s+", re.I)
_GROUP_TYPES = ("elite", "group", "dungeon", "raid")


def _xy(text) -> tuple[float, float] | None:
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", str(text or ""))
    return (float(m.group(1)), float(m.group(2))) if m else None


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
        return math.hypot(a[0] - b[0], a[1] - b[1])
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


def _named(title: str, names) -> bool:
    low = title.lower()
    return any(str(n).strip("[] ").lower() == low for n in names or [])


def plan_route(snapshot: dict, new_quests: bool | list = False, skip: list | None = None, last: list | None = None,
               end: tuple[float, float] | None = None, end_title: str | None = None) -> dict:
    """{"stops": [{"title", "what", "note", "x", "y", "zone", "meters", "mob", "quest"}], "left_out": [...],
    "meters": total, "text"}; "mob" is the creature to look for at a stop (None for objects and NPCs to talk to).

    ``skip``: quest titles to leave out; ``last``: quests done after everything else (hard ones); ``new_quests``: the
    titles of nearbyQuests to pick up (True: all of this zone); ``end``: map coordinates in this zone where the player
    goes afterwards, so the route finishes near them. A stop's "meters" is the way from the stop before it (None when
    the zone's scale is not known)."""
    player = snapshot.get("player") or {}
    zone = str(player.get("map") or player.get("zone") or "")
    start = _xy(player.get("coords"))
    nodes, left_out = [], []
    if start is None:
        return {"stops": [], "left_out": [], "meters": None, "text": "The player's position is not known; no route."}
    for q in snapshot.get("quests") or []:
        if not isinstance(q, dict) or not q.get("title"):
            continue
        title, status = str(q["title"]), str(q.get("status") or "")
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
        objectives, blocked = [], False
        if not ready:
            for line in q.get("whereToGet") or []:
                label, _, sources = str(line).partition(": ")
                m = _SOURCE.match(sources.split("; ")[0])
                if not m:
                    left_out.append(f"[{title}]: no known place for {label}")
                    blocked = True
                elif m.group("zone") != zone:
                    left_out.append(f"[{title}]: {label} in {m.group('zone')} ({m.group('x')}, {m.group('y')}), another zone")
                    blocked = True
                else:
                    how = _HOW.match(m.group("desc"))
                    creature = bool(how) and how.group(1).lower() in ("kill", "find", "dropped by")
                    objectives.append({"title": _name(m.group("desc")), "what": f"[{title}]: {m.group('desc')}",
                                       "xy": (float(m.group("x")), float(m.group("y"))), "after": [],
                                       "mob": _name(m.group("desc")) if creature else None, "quest": title})
            if not objectives and not blocked:  # still to do, and nothing says where: the turn-in is not a stop yet
                left_out.append(f"[{title}]: no known place for what is left to do" + (f" ({todo})" if todo else ""))
                blocked = True
        if blocked:
            continue
        first = len(nodes)
        places = [_PLACE.match(p.strip()) for p in str(q.get("turnIn") or "").split(";")]
        here = [m for m in places if m and m.group("zone") == zone]
        if here:
            m = here[0]  # the first one is the quest's own NPC; others are stand-ins
            objectives.append({"title": m.group("name"), "what": f"turn in [{title}]",
                               "xy": (float(m.group("x")), float(m.group("y"))),
                               "after": list(range(first, first + len(objectives)))})
        elif places and places[0]:
            m = places[0]
            left_out.append(f"[{title}]: turn in to {m.group('name')} in {m.group('zone')} "
                            f"({m.group('x')}, {m.group('y')}), another zone")
        elif ready:
            left_out.append(f"[{title}]: no known place for the turn-in")
        for node in objectives:
            node.update(note=note, late=_named(title, last))
        nodes += objectives
    if new_quests:
        for q in snapshot.get("nearbyQuests") or []:
            m = isinstance(q, dict) and _WHERE.match(str(q.get("where") or ""))
            wanted = new_quests is True or _named(str(q.get("title") or ""), new_quests)
            if m and m.group(1) == zone and wanted and not _named(str(q.get("title") or ""), skip):
                nodes.append({"title": str(q.get("giver") or q.get("title")), "what": f"pick up [{q.get('title')}]",
                              "xy": (float(m.group(2)), float(m.group(3))), "after": [], "late": False,
                              "note": f"quest level {q['level']}" if q.get("level") else ""})
    early = [i for i, node in enumerate(nodes) if not node["late"]]
    for node in nodes:
        if node["late"]:
            node["after"] = sorted(set(node["after"]) | set(early))
    scale = _scale(snapshot, start, zone)
    sx, sy = scale or (1.0, 1.0)

    def at(xy) -> tuple[float, float]:
        return xy[0] * sx, xy[1] * sy
    for node in nodes:
        node["at"] = at(node["xy"])
    stops, pos, total = [], at(start), 0
    for i in _order(at(start), nodes, at(end) if end else None) if nodes else []:
        node = nodes[i]
        meters = int(round(math.hypot(node["at"][0] - pos[0], node["at"][1] - pos[1]))) if scale else None
        total += meters or 0
        stops.append({"title": node["title"], "what": node["what"], "note": node["note"], "x": node["xy"][0],
                      "y": node["xy"][1], "zone": zone, "meters": meters, "mob": node.get("mob"),
                      "quest": node.get("quest")})
        pos = node["at"]
    if stops:
        length = f", about {total / 1000:.1f} km" if scale else ""
        ending = f", ending near {end_title or f'{end[0]:g}, {end[1]:g}'}" if end else ""
        lines = [f"Route from {start[0]:g}, {start[1]:g} ({zone}): {len(stops)} stops{length}{ending}."]
        for n, s in enumerate(stops, 1):
            way = "" if s["meters"] is None else (f", {s['meters']} m" if n == 1 else f", +{s['meters']} m")
            lines.append(f"{n}. {s['title']} ({s['x']:g}, {s['y']:g}){way}: {s['what']}"
                         + (f" — {s['note']}" if s["note"] else ""))
    else:
        lines = [f"Nothing in the quest log has a place to walk to in {zone}."]
    if left_out:
        lines += ["Not on the route:"] + ["- " + x for x in left_out]
    return {"stops": stops, "left_out": left_out, "meters": total if scale else None, "text": "\n".join(lines)}
