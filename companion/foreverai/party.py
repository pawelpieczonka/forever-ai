"""Routes for a party (a duo): what the add-on reads of the other players (its party section) for the planner and the
model. Each member comes with their level, class and where they are, the player's quests they have too (with how far
they are with them), their quests the player has not got (whether the player can take each, from whom), and the
player's quests they lack (whether the game lets the player share each). Questie shares the quest logs within a party
(QuestieComms); without Questie in a member's game only the player's quests they have are known."""

from __future__ import annotations

FAR_M = 150  # a member farther than this (or in another zone) is not by the player: wait for them before a hard quest
RED = 3  # a quest this many levels above a member is red for them: hard, and little use without them


def members(snapshot: dict) -> list[dict]:
    """The other players of the party, as the add-on read them."""
    party = snapshot.get("party") if isinstance(snapshot.get("party"), dict) else {}
    return [m for m in party.get("members") or [] if isinstance(m, dict) and m.get("name")]


def takeable(people: list[dict]) -> list[str]:
    """The titles of the members' quests the player can take: a route for the party picks them up (a member can share
    one instead, and the giver's stop is done once it is in the quest log)."""
    out: list[str] = []
    for m in people:
        for q in m.get("theirs") or []:
            if isinstance(q, dict) and q.get("canTake") and q.get("title") and q["title"] not in out:
                out.append(str(q["title"]))
    return out


def _where(m: dict) -> str:
    if m.get("offline"):
        return "offline"
    if isinstance(m.get("meters"), (int, float)):
        return f"{int(m['meters'])} m away" + (": wait for them" if m["meters"] > FAR_M else "")
    if m.get("zone"):
        return f"in {m['zone']}: wait for them, or meet on the way"
    return "where they are is not known"


def _on_route(route: dict) -> set[str]:
    """The quests the route picks up (their titles, lower case)."""
    return {str(t.get("quest") or "").lower() for stop in route.get("stops") or [] for t in stop.get("tasks") or []
            if t.get("kind") == "pick_up"}


def _routed(route: dict) -> set[str]:
    """Every quest the route does something for."""
    return {str(t.get("quest") or "").lower() for stop in route.get("stops") or [] for t in stop.get("tasks") or []
            if t.get("quest")}


def _both(b) -> str:
    if not isinstance(b, dict):
        return f"[{b}]"
    state = "done" if b.get("done") else b.get("progress")
    return f"[{b.get('title')}]" + (f" (they: {state})" if state else "")


def together(snapshot: dict, route: dict) -> str:
    """The planner's result for a route the party goes together: member by member, what both have, their quests on
    the route for the player too, theirs the player cannot take and why, the player's quests they lack (to share, or
    to pick up at the giver), and the route's quests that are red for them."""
    people = members(snapshot)
    if not people:
        return ("\nNo party: the party section lists nobody (the player is alone, or in a raid). The route is the "
                "player's own.")
    picked, routed = _on_route(route), _routed(route)
    # the route's quests of the quest log, with their levels
    mine = {str(q.get("title") or "").lower(): q for q in snapshot.get("quests") or []
            if isinstance(q, dict) and str(q.get("title") or "").lower() in routed}
    lines = ["\nTogether with the party (kill credit is shared by members near each other; quest items each loots "
             "for themselves):"]
    for m in people:
        head = f"- {m['name']} (level {m.get('level') or '?'} {m.get('class') or ''}".rstrip() + f", {_where(m)}"
        if m.get("dead"):
            head += ", dead now"
        both = [_both(b) for b in m.get("both") or []]
        if m.get("questie") is False:
            lines.append(head + "). Questie in their game sent nothing, so their quest log is not known; of the "
                         "player's quests they have " + (", ".join(both) if both else "none") + ".")
            continue
        lines.append(head + "; their quest log from Questie):")
        if both:
            lines.append("  both have: " + ", ".join(both))
        theirs = [q for q in m.get("theirs") or [] if isinstance(q, dict) and q.get("title")]
        can = [q for q in theirs if q.get("canTake")]
        on = [q for q in can if str(q["title"]).lower() in picked]
        off = [q for q in can if str(q["title"]).lower() not in picked]
        if on:
            lines.append("  theirs, on the route for the player too (they can share it, else the player picks it up "
                         "at the giver): " + ", ".join(f"[{q['title']}]" + (" (they have it done)" if q.get("done") else "")
                                                       for q in on))
        if off:
            lines.append("  theirs the player can take, not on this route (another zone, or no place known): "
                         + ", ".join(f"[{q['title']}]" + (f" from {q['giver']}" if q.get("giver") else "")
                                     + (f" ({q['where']})" if q.get("where") else "") for q in off))
        cannot = [q for q in theirs if not q.get("canTake") and q.get("why")]
        if cannot:
            lines.append("  theirs the player cannot take: " + ", ".join(f"[{q['title']}] ({q['why']})" for q in cannot))
        unknown = [q for q in theirs if "canTake" not in q]
        if unknown:
            lines.append("  theirs the quest data does not know: " + ", ".join(f"[{q['title']}]" for q in unknown))
        lacks = [q for q in m.get("lacks") or [] if isinstance(q, dict) and q.get("title")]
        if lacks:
            share = [q for q in lacks if q.get("share") and not q.get("opensAt")]
            giver = [q for q in lacks if not q.get("share") and not q.get("opensAt")]
            later = [q for q in lacks if q.get("opensAt")]
            bits = []
            if share:
                bits.append(", ".join(f"[{q['title']}]" for q in share) + " can be shared (Share Quest in the player's "
                            "quest log, while they are near)")
            if giver:
                bits.append(", ".join(f"[{q['title']}]" for q in giver) + " cannot be shared: they pick it up at its "
                            "giver themselves")
            if later:
                bits.append(", ".join(f"[{q['title']}] (opens at level {q['opensAt']} for them)" for q in later))
            lines.append("  the player's quests they lack: " + "; ".join(bits))
        level = m.get("level")
        if isinstance(level, (int, float)):
            red = [q for q in mine.values() if isinstance(q.get("level"), (int, float)) and q["level"] - level >= RED]
            if red:
                lines.append(f"  red for them ({RED} or more levels above them): "
                             + ", ".join(f"[{q['title']}] ({q['level']})" for q in sorted(red, key=lambda q: q["level"])))
    lines.append("Say in the answer what to share now and who picks up what at a giver, the quests red for one of "
                 "them, and to wait for a member who is far before the hard ones.")
    return "\n".join(lines)
