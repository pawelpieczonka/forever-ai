"""The walking route through the quest log: every place with something to do (objectives, turn-ins, on request quest
givers), in the order with the shortest way, each turn-in after its own objectives. Computed here so that the model
does not have to order a dozen coordinates in its head; it reads the result and tells it to the player."""

from __future__ import annotations

import math
import re
import statistics

EXACT_MAX = 12  # stops up to which every order is tried (2^n * n^2 steps); more are ordered nearest-first
_PLACE = re.compile(r"^(?P<name>.*?) \((?P<zone>[^()]+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)\)$")
_SOURCE = re.compile(r"^(?P<desc>.*?) — (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?)(?: \((?P<m>\d+) m.*)?$")
_NPC_LINE = re.compile(r"— (?P<zone>.+?) (?P<x>\d+(?:\.\d+)?), (?P<y>\d+(?:\.\d+)?) \((?P<m>\d+) m\)")
_HOW = re.compile(r"^(kill|use|find|from object|dropped by|sold by)\s+", re.I)


def _xy(text) -> tuple[float, float] | None:
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)", str(text or ""))
    return (float(m.group(1)), float(m.group(2))) if m else None


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _name(desc: str) -> str:
    """'kill Boar (level 5)' -> 'Boar'."""
    return re.sub(r"\s*\((level|rare|elite)[^)]*\)", "", _HOW.sub("", desc)).strip()


def _scale(snapshot: dict, start, zone: str) -> float | None:
    """Meters per map unit in this zone, from distances the game itself measured from the player's position."""
    pairs = []

    def add(z, x, y, meters):
        units = _dist(start, (float(x), float(y)))
        if z == zone and units >= 1 and meters:
            pairs.append(float(meters) / units)
    for q in snapshot.get("quests") or []:
        for line in (q.get("whereToGet") or []) if isinstance(q, dict) else []:
            for src in str(line).split(": ", 1)[-1].split("; "):
                m = _SOURCE.match(src)
                if m and m.group("m"):
                    add(m.group("zone"), m.group("x"), m.group("y"), m.group("m"))
    for q in snapshot.get("nearbyQuests") or []:
        m = isinstance(q, dict) and re.match(r"^(.+?) (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)$", str(q.get("where") or ""))
        d = isinstance(q, dict) and re.match(r"^(\d+) m$", str(q.get("distance") or ""))
        if m and d:
            add(m.group(1), m.group(2), m.group(3), d.group(1))
    for line in snapshot.get("zoneNPCs") or []:
        m = _NPC_LINE.search(str(line))
        if m:
            add(m.group("zone"), m.group("x"), m.group("y"), m.group("m"))
    return statistics.median(pairs) if pairs else None


def _order(start, nodes: list[dict]) -> list[int]:
    """Indices of ``nodes`` in the order of the shortest open path from ``start``; node["after"] (indices) come first."""
    n = len(nodes)
    need = [sum(1 << j for j in node["after"]) for node in nodes]
    if n > EXACT_MAX:  # nearest first among the stops whose objectives are done
        order, done, pos = [], 0, start
        while len(order) < n:
            ready = [i for i in range(n) if not done >> i & 1 and need[i] & done == need[i]]
            i = min(ready, key=lambda k: (_dist(pos, nodes[k]["xy"]), k))
            order.append(i)
            done |= 1 << i
            pos = nodes[i]["xy"]
        return order
    d = [[_dist(a["xy"], b["xy"]) for b in nodes] for a in nodes]
    best: dict[tuple[int, int], tuple[float, int]] = {}
    for i in range(n):
        if need[i] == 0:
            best[(1 << i, i)] = (_dist(start, nodes[i]["xy"]), -1)
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
    last = min((k for k in range(n) if (full, k) in best), key=lambda k: (best[(full, k)][0], k))
    order, mask = [], full
    while last != -1:
        order.append(last)
        prev = best[(mask, last)][1]
        mask ^= 1 << last
        last = prev
    return order[::-1]


def plan_route(snapshot: dict, new_quests: bool = False) -> dict:
    """{"stops": [{"title", "what", "x", "y", "zone", "meters"}], "left_out": [...], "meters": total, "text": ...};
    "meters" of a stop is the way from the stop before it (None when the zone's scale is not known)."""
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
        tag = " (elite quest)" if str(q.get("type") or "").lower() in ("elite", "group", "dungeon", "raid") else ""
        if status.startswith("FAILED"):
            left_out.append(f"[{title}]: failed, abandon it and pick it up again")
            continue
        skill = re.search(r"[^;\[]*skill to \d+", str(q.get("objectives") or ""), re.I)
        if skill and not status.startswith("READY"):
            left_out.append(f"[{title}]: needs a skill, not a place ({skill.group(0).strip()})")
            continue
        objectives, blocked = [], False
        if not status.startswith("READY"):
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
                    objectives.append({"title": _name(m.group("desc")), "what": f"[{title}]: {m.group('desc')}{tag}",
                                       "xy": (float(m.group("x")), float(m.group("y"))), "after": []})
        if blocked:
            continue
        first = len(nodes)
        nodes += objectives
        places = [_PLACE.match(p.strip()) for p in str(q.get("turnIn") or "").split(";")]
        here = [m for m in places if m and m.group("zone") == zone]
        if here:
            m = here[0]  # the first one is the quest's own NPC; others are stand-ins
            nodes.append({"title": m.group("name"), "what": f"turn in [{title}]",
                          "xy": (float(m.group("x")), float(m.group("y"))),
                          "after": list(range(first, first + len(objectives)))})
        elif places and places[0]:
            m = places[0]
            left_out.append(f"[{title}]: turn in to {m.group('name')} in {m.group('zone')} "
                            f"({m.group('x')}, {m.group('y')}), another zone")
    if new_quests:
        for q in snapshot.get("nearbyQuests") or []:
            m = isinstance(q, dict) and re.match(r"^(.+?) (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)$", str(q.get("where") or ""))
            if m and m.group(1) == zone:
                nodes.append({"title": str(q.get("giver") or q.get("title")), "what": f"pick up [{q.get('title')}]",
                              "xy": (float(m.group(2)), float(m.group(3))), "after": []})
    scale = _scale(snapshot, start, zone)
    stops, pos, total = [], start, 0
    for i in _order(start, nodes) if nodes else []:
        node = nodes[i]
        meters = int(round(_dist(pos, node["xy"]) * scale)) if scale else None
        total += meters or 0
        stops.append({"title": node["title"], "what": node["what"], "x": node["xy"][0], "y": node["xy"][1],
                      "zone": zone, "meters": meters})
        pos = node["xy"]
    if stops:
        length = f", about {total / 1000:.1f} km" if scale else ""
        lines = [f"Route from {start[0]:g}, {start[1]:g} ({zone}): {len(stops)} stops{length}."]
        for n, s in enumerate(stops, 1):
            way = "" if s["meters"] is None else (f", {s['meters']} m" if n == 1 else f", +{s['meters']} m")
            lines.append(f"{n}. {s['title']} ({s['x']:g}, {s['y']:g}){way}: {s['what']}")
    else:
        lines = [f"Nothing in the quest log has a place to walk to in {zone}."]
    if left_out:
        lines += ["Not on the route:"] + ["- " + x for x in left_out]
    return {"stops": stops, "left_out": left_out, "meters": total if scale else None, "text": "\n".join(lines)}
