"""What the game data does not hold about getting around: the ways between maps (portals, gates) and the parts of a
map that have no ground way to the rest of it. The route planner uses it, so that a route does not depend on what
the model remembers of the world: a known way is filled in by itself, and a cut-off part of a map counts as a zone
of its own, under its own name.

The list is short and grows with what has been checked. A way that is not here is named by the model."""

from __future__ import annotations

# a part of a map with no ground way to the rest of it: {"name", "map", "box": (x1, y1, x2, y2)}
REGIONS = [
    # the port at the foot of the tree: joined to the rest of Teldrassil only through Darnassus
    {"name": "Rut'theran Village", "map": "Teldrassil", "box": (52.0, 86.0, 62.0, 100.0)},
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
