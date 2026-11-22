"""The long route: the way to a level (or for longer than one quest log lasts), kept by the app in a file per
character, so that it outlives the answer, the conversation and a restart. It is made of parts, in order. A part is
the quests of one place (a town and its surroundings, a zone; a dungeon run), with the way there: when its turn
comes the route planner puts it on the map as plan_route would, and when it is done the app puts the next part on
the map by itself.

The model builds the long route part by part (the long_route tool): it can hold far more than the map's stops, and
the model does not have to write it out in one answer. The app checks every quest against the game data (the quest
log, the quests around the player, the zones read with zone_quests), works out what each quest gives at the level
the character will have when they turn it in, and from that the level after each part, so that "from 25 to 29"
really ends at 29.

The XP of a quest is Questie's (the base XP of the quest at its own level, reduced once the character is six or more
levels above it, as the game does); the creatures killed on the way are counted as a share of the quests' XP, the
player's own share over their last sessions when the add-on has seen enough of them."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from . import config
from .progress import MIN_PACE_S, duration, level_xp, recent, table_scale
from .route import refusal

FILE_NAME = "long_routes.json"
MAX_PARTS = 12
MAX_QUESTS = 14  # quests one part names; more would not fit on the map at once
TITLE_MAX, NOTE_MAX, SUMMARY_MAX = 60, 240, 600
MAX_LEVEL = 60
# the XP of creatures killed on the way, as a share of the quests' XP: the player's own when the add-on has seen this
# much of their play (quests and creatures together), else an assumed one, on the low side so that the route rather
# overshoots its level than falls short of it
SHARE_DEFAULT = 0.4
SHARE_MIN, SHARE_MAX = 0.15, 1.2
SHARE_SAMPLE_XP = 15000
SHARE_SESSIONS = 20
TYPICAL_XP_PER_LEVEL = 80  # a quest whose XP the data does not have: about this much per quest level (2000 at 25)
ABOVE_RED = 3  # a quest this many levels above the character when its part comes is hard alone
# what the app keeps of each quest of a part, as the game listed it: enough to plan it when the part's turn comes
QUEST_KEYS = ("id", "title", "level", "minLevel", "xp", "type", "giver", "where", "objective", "whereToGet", "turnIn",
              "next", "nextId", "nextXp", "nextNeeds", "goals", "startsWhenTaken")
_FOLLOW = re.compile(r"^\[(?P<title>.+?)\](?: \(level (?P<level>\d+)\))? from (?P<giver>.+?) \((?P<where>[^()]+)\)$")
_ZONE_AT = re.compile(r"(?:—|\() *(?P<zone>[A-Z][^—(),:]*?) \d+(?:\.\d+)?, \d+(?:\.\d+)?")
_REWARD_XP = re.compile(r"(\d[\d,]*) XP")


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def norm(title: Any) -> str:
    """A quest title as the model and the game data may write it: no brackets, no case, single spaces."""
    return re.sub(r"\s+", " ", str(title or "").strip().strip("[]").strip()).lower()


def _clip(text: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def _titles(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [_clip(v.get("title") if isinstance(v, dict) else v, 80) for v in value or []
            if _clip(v.get("title") if isinstance(v, dict) else v, 80)] if isinstance(value, list) else []


# ------------------------------------------------------------------------ XP
def _mult(qlevel: int, plevel: int) -> int:
    """Tenths of a quest's XP the character gets: all of it up to five levels above the quest, then 2 tenths less
    per level, down to one tenth (cmangos, as Questie has it)."""
    return min(10, max(1, 2 * (qlevel - plevel) + 20))


def _round(xp: float) -> int:
    if xp <= 100:
        return int(5 * math.floor((xp + 2) / 5))
    if xp <= 500:
        return int(10 * math.floor((xp + 5) / 10))
    if xp <= 1000:
        return int(25 * math.floor((xp + 12) / 25))
    return int(50 * math.floor((xp + 25) / 50))


def quest_xp(base: float, qlevel: Any, plevel: float) -> int:
    """What a quest gives a character of level ``plevel``: its base XP (at the quest's own level), reduced when the
    character is six or more levels above it."""
    if not base or base <= 0:
        return 0
    if not isinstance(qlevel, (int, float)) or qlevel <= 0:
        return int(base)
    return _round(base * _mult(int(qlevel), int(plevel)) / 10)


def base_xp(q: dict, plevel: int) -> tuple[float, bool]:
    """(the base XP of a quest, whether it is only estimated). From the game's quest data (zone_quests: xp), from
    what the quest log says it gives now (rewards: "1750 XP", at the character's level), else a typical quest of its
    level."""
    level = q.get("level") if isinstance(q.get("level"), (int, float)) else None
    if _num(q.get("xp")) > 0:
        return _num(q.get("xp")), False
    m = _REWARD_XP.search(str(q.get("rewards") or ""))
    if m:
        now = float(m.group(1).replace(",", ""))
        return (now * 10 / _mult(int(level), plevel) if level else now), False
    return TYPICAL_XP_PER_LEVEL * (level or plevel), True


class Level:
    """The character's level and their XP into it, carried forward through the route."""

    def __init__(self, level: int, xp: float, xp_max: float, max_level: int = MAX_LEVEL):
        self.level, self.xp, self.max = int(level), float(xp or 0), int(max_level or MAX_LEVEL)
        self.scale = table_scale(self.level, xp_max)
        self.known = {self.level: float(xp_max)} if xp_max else {}

    def need(self) -> float:
        return level_xp(self.level, self.known, self.scale)

    def add(self, gain: float) -> None:
        self.xp += max(0.0, gain)
        while self.level < self.max and self.xp >= self.need():
            self.xp -= self.need()
            self.level += 1

    def value(self) -> float:
        return float(self.max) if self.level >= self.max else self.level + self.xp / self.need()

    def to(self, goal: int) -> float:
        """XP still needed to reach level ``goal``."""
        if self.level >= goal:
            return 0.0
        need = self.need() - self.xp
        for lv in range(self.level + 1, goal):
            need += level_xp(lv, self.known, self.scale)
        return max(0.0, need)


def kill_share(snapshot: dict) -> tuple[float, str]:
    """(the XP of creatures as a share of the quests' XP, where that figure comes from)."""
    pr = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
    rows = [pr.get("session")] + list(pr.get("sessions") or [])[:SHARE_SESSIONS]
    rows = [r for r in rows if isinstance(r, dict)]
    gain = sum(_num(r.get("gain")) for r in rows)
    quests = sum(_num(r.get("questXp")) for r in rows)
    if gain >= SHARE_SAMPLE_XP and quests > 0:
        share = min(SHARE_MAX, max(SHARE_MIN, (gain - quests) / quests))
        return share, (f"the player's own pace: over their last {len(rows)} sessions {round(100 * (gain - quests) / gain)}% "
                       "of their XP came from creatures")
    return SHARE_DEFAULT, "assumed (the add-on has not seen enough of the player's play yet to know their own)"


def pace(snapshot: dict) -> float | None:
    """The player's XP per hour of play over the last days, when the add-on has seen enough of it."""
    pr = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
    xp, play, _, _ = recent(pr, _num(pr.get("now"), time.time()))
    s = pr.get("session") if isinstance(pr.get("session"), dict) else {}
    xp, play = xp + _num(s.get("gain")), play + _num(s.get("act"))
    return xp / play * 3600 if play >= MIN_PACE_S and xp > 0 else None


# ------------------------------------------------------------------------ the quests a route can name
def done_ids(snapshot: dict) -> set[int]:
    """The quests the character is through with: the game says they are turned in (routeDone), or the app saw them
    leave the quest log (through)."""
    out = set()
    for key in ("routeDone", "through"):
        out |= {int(i) for i in snapshot.get(key) or [] if isinstance(i, (int, float))}
    return out


def log_quests(snapshot: dict) -> list[dict]:
    return [q for q in snapshot.get("quests") or [] if isinstance(q, dict) and q.get("title")]


def quest_zones(q: dict) -> list[str]:
    """The zones a quest of the log has something in (its objectives' places, its turn-in)."""
    out = []
    for text in list(q.get("whereToGet") or []) + [q.get("turnIn") or "", q.get("where") or ""]:
        for m in _ZONE_AT.finditer(str(text)):
            z = m.group("zone").strip()
            if z and z not in out:
                out.append(z)
    m = re.match(r"^(.+?) \d+(?:\.\d+)?, \d+(?:\.\d+)?$", str(q.get("where") or ""))
    if m and m.group(1).strip() not in out:
        out.append(m.group(1).strip())
    return out


def lookup(title: str, snapshot: dict, offered: dict) -> tuple[dict | None, str]:
    """The quest of this title the character can do: (the quest as the app keeps it, where it was found). In the
    quest log first, then among the quests the game offers (zone_quests, around the player), then the next part of a
    chain the log or the offered quests lead to. None when the data has no such quest."""
    want = norm(title)
    for q in log_quests(snapshot):
        if norm(q["title"]) == want:
            return dict({k: q[k] for k in QUEST_KEYS if q.get(k) is not None}, inLog=True,
                        **({"rewards": q["rewards"]} if q.get("rewards") else {}),
                        **({"zones": quest_zones(q)} if quest_zones(q) else {})), "the quest log"
    found = [q for q in offered.values() if isinstance(q, dict) and norm(q.get("title")) == want]
    found += [q for q in snapshot.get("nearbyQuests") or [] if isinstance(q, dict) and norm(q.get("title")) == want
              and not any(q.get("id") == f.get("id") for f in found)]
    if found:
        # (the parts of a chain can share a title: the game offers the part that is open now)
        q = min(found, key=lambda f: (_num(f.get("minLevel"), 0), _num(f.get("level"), 0)))
        return {k: q[k] for k in QUEST_KEYS if q.get(k) is not None}, "the quests the game offers"
    for q in list(log_quests(snapshot)) + list(offered.values()):
        m = _FOLLOW.match(str(q.get("next") or "")) if isinstance(q, dict) else None
        if m and norm(m.group("title")) == want:
            return ({"title": m.group("title"), "giver": m.group("giver").strip(), "where": m.group("where").strip(),
                     **({"id": q["nextId"]} if isinstance(q.get("nextId"), int) else {}),
                     **({"level": int(m.group("level"))} if m.group("level") else {}),
                     **({"xp": q["nextXp"]} if _num(q.get("nextXp")) > 0 else {}),
                     **({"needs": [str(n) for n in q["nextNeeds"]]} if q.get("nextNeeds") else {}),
                     "after": str(q.get("title"))}, f"the chain of [{q.get('title')}]")
    return None, ""


# ------------------------------------------------------------------------ parts
def make_part(raw: dict, snapshot: dict, offered: dict, taken: set[str]) -> tuple[dict | None, list[str]]:
    """A part as the model gives it -> (the part as the app keeps it, what is wrong with it). Quests the data does
    not have are left out of it, with a word why; ``taken``: the quests earlier parts already hold."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return None, ["A part is an object with title, zone and quests."]
    title, zone = _clip(raw.get("title"), TITLE_MAX), _clip(raw.get("zone"), 60)
    if not zone:
        return None, [f"Part {title or '?'} has no zone: give the zone its quests are in (its English name)."]
    title = title or zone
    then = [z for z in (_clip(z, 60) for z in (raw.get("then") if isinstance(raw.get("then"), list) else []))
            if z and z.lower() != zone.lower()][:6]
    done = done_ids(snapshot)
    quests, seen = [], set()
    for t in _titles(raw.get("quests"))[:MAX_QUESTS + 6]:
        key = norm(t)
        if key in seen:
            continue
        seen.add(key)
        q, where = lookup(t, snapshot, offered)
        if q is None:
            problems.append(f"[{t}] is left out of part \"{title}\": it is not in the quest log, around the player or "
                            f"among the quests read with zone_quests. Read its zone with zone_quests (up_to_level = "
                            f"the goal) and add it again, or leave it out.")
            continue
        if isinstance(q.get("id"), int) and q["id"] in done and not q.get("inLog"):
            problems.append(f"[{q['title']}] is left out: the character has done it already.")
            continue
        if key in taken:
            problems.append(f"[{q['title']}] is left out of part \"{title}\": an earlier part has it already.")
            continue
        refused = None if q.get("inLog") else refusal(snapshot, q)
        if refused:  # (the game data says it can be taken; its NPC did not give it)
            problems.append(f"[{q['title']}] is left out of part \"{title}\": {refused}. quest_requirements says "
                            "what stands in its way; do not plan it again.")
            continue
        # the next part of a chain that needs another quest done too: that one comes first, in this part or before
        missing = [n for n in q.get("needs") or [] if not any(norm(x) in taken | seen for x in re.findall(r"\[(.+?)\]", n))]
        if missing:
            problems.append(f"[{q['title']}] needs {' and '.join(missing)} done first: put that quest into this part "
                            "(before it) or an earlier one, or the part goes past an NPC who has nothing to give.")
        if len(quests) >= MAX_QUESTS:
            problems.append(f"[{q['title']}] is left out: a part holds at most {MAX_QUESTS} quests. Put the rest into "
                            "a part of their own.")
            continue
        quests.append(q)
    extra = [e for e in (raw.get("extra") if isinstance(raw.get("extra"), list) else []) if isinstance(e, dict)][:10]
    part = {"title": title, "zone": zone, "quests": quests,
            **({"then": then} if then else {}),
            **({"note": _clip(raw.get("note"), NOTE_MAX)} if _clip(raw.get("note"), NOTE_MAX) else {}),
            **({"extra": extra} if extra else {})}
    for key in ("skip", "first", "last", "use", "avoid"):
        if _titles(raw.get(key)):
            part[key] = _titles(raw.get(key))[:20]
    if not quests and not extra:
        problems.append(f"Part \"{title}\" has nothing to do: no quest of it is known and it has no stop. It is not "
                        "added.")
        return None, problems
    return part, problems


def new_route(goal: int, title: str, summary: str, snapshot: dict, conv: str, skip: list[str]) -> dict:
    player = snapshot.get("player") or {}
    return {"id": f"{int(time.time() * 1000):x}", "goal": int(goal), "title": _clip(title, TITLE_MAX) or f"To level {goal}",
            **({"summary": _clip(summary, SUMMARY_MAX)} if _clip(summary, SUMMARY_MAX) else {}),
            **({"skip": skip[:30]} if skip else {}),
            "conv": conv, "made": int(time.time()), "fromLevel": player.get("level"), "parts": [], "current": 0}


def taken_titles(route: dict, upto: int | None = None) -> set[str]:
    """The quests the parts hold (the first ``upto`` of them)."""
    return {norm(q.get("title")) for part in route.get("parts", [])[:upto] for q in part.get("quests") or []}


# ------------------------------------------------------------------------ the levels it leads to
def estimate(route: dict, snapshot: dict) -> dict:
    """What the parts still to do give, in order, from where the character is now: for each part the level when it
    begins and when it ends, the XP of its quests and of the creatures on the way; and the level the route ends at."""
    player = snapshot.get("player") or {}
    pr = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
    level = int(_num(player.get("level"), 1)) or 1
    lv = Level(level, _num(player.get("xp")), _num(player.get("xpMax")), int(_num(pr.get("maxLevel"), MAX_LEVEL)))
    share, share_note = kill_share(snapshot)
    rested = _num(player.get("rested"))
    done = done_ids(snapshot)
    in_log = {q.get("id") for q in log_quests(snapshot)}
    rate = pace(snapshot)
    parts, warnings = [], []
    for i, part in enumerate(route.get("parts") or []):
        if part.get("done") or i < int(route.get("current") or 0):
            parts.append({"done": True})
            continue
        start, qxp, guessed, left = lv.value(), 0.0, [], 0
        for q in part.get("quests") or []:
            if isinstance(q.get("id"), int) and q["id"] in done and q["id"] not in in_log:
                continue  # turned in already: the character's XP has it
            left += 1
            base, guess = base_xp(q, level)
            if guess:
                guessed.append(str(q.get("title")))
            minimum = q.get("minLevel")
            if isinstance(minimum, (int, float)) and minimum > lv.level and not q.get("inLog"):
                warnings.append(f"[{q.get('title')}] opens at level {int(minimum)}, and in part {i + 1} the character "
                                f"is expected at {lv.value():.1f}: move it to a later part.")
            qlevel = q.get("level")
            if (isinstance(qlevel, (int, float)) and qlevel - lv.level >= ABOVE_RED
                    and str(q.get("type") or "").lower() not in ("elite", "group", "dungeon", "raid")):
                warnings.append(f"[{q.get('title')}] is level {int(qlevel)}, {int(qlevel - lv.level)} above the "
                                f"character in part {i + 1}: hard alone.")
            gain = quest_xp(base, qlevel, lv.level)
            qxp += gain
            lv.add(gain)
        kills = qxp * share
        bonus = min(rested, kills)
        rested -= bonus
        lv.add(kills + bonus)
        entry = {"from": start, "to": lv.value(), "quests": qxp, "kills": kills + bonus, "left": left}
        if guessed:
            entry["guessed"] = guessed
        if rate:
            entry["hours"] = (qxp + kills + bonus) / rate
        parts.append(entry)
    goal = int(route.get("goal") or 0)
    return {"parts": parts, "end": lv.value(), "goal": goal, "short": lv.to(goal) if goal else 0.0,
            "share": share, "shareNote": share_note, "rested": _num(player.get("rested")), "warnings": warnings,
            "rate": rate, "level": level}


# ------------------------------------------------------------------------ texts
def _quest_line(q: dict, plevel: int) -> str:
    base, guess = base_xp(q, plevel)
    xp = quest_xp(base, q.get("level"), plevel)
    bits = [f"level {q['level']}" if q.get("level") else "", str(q.get("type") or ""),
            (f"about {xp:,} XP" if guess else f"{xp:,} XP") if xp else ""]
    where = ("in the quest log" if q.get("inLog") else
             f"from {q.get('giver')} ({q.get('where')})" if q.get("giver") and q.get("where") else "")
    if q.get("after"):
        where += f", after [{q['after']}]"
    return f"[{q.get('title')}] (" + ", ".join(b for b in bits if b) + ")" + (f" {where}" if where else "")


def render(route: dict, snapshot: dict, est: dict | None = None, detail: bool = True) -> str:
    """The long route for the model: its parts with the levels they lead to, what is still missing to the goal."""
    est = est or estimate(route, snapshot)
    level = int(_num((snapshot.get("player") or {}).get("level"), 1))
    cur = int(route.get("current") or 0)
    lines = [f"Long route \"{route.get('title')}\" (goal: level {route.get('goal')}), kept for this character"
             + (f": {route['summary']}" if route.get("summary") else "") + "."]
    if route.get("skip"):
        lines.append("Left out of the whole route: " + ", ".join(f"[{t}]" for t in route["skip"]) + ".")
    for i, part in enumerate(route.get("parts") or []):
        e = est["parts"][i] if i < len(est["parts"]) else {}
        where = part["zone"] + (" then " + ", ".join(part["then"]) if part.get("then") else "")
        if e.get("done"):
            lines.append(f"{i + 1}. {part['title']} ({where}): done.")
            continue
        state = ("" if i != cur else " [on the map now]" if route.get("mapped") == cur
                 else " [the current part, not on the map: long_route go puts it there]")
        hours = f", about {duration(e['hours'] * 3600)} of play" if e.get("hours") else ""
        lines.append(f"{i + 1}. {part['title']} ({where}){state}: level {e.get('from', 0):.1f} -> {e.get('to', 0):.1f}, "
                     f"{e.get('left', 0)} quests, {int(e.get('quests', 0)):,} XP from quests + about "
                     f"{int(e.get('kills', 0)):,} from creatures{hours}")
        if detail:
            for q in part.get("quests") or []:
                lines.append("   " + _quest_line(q, level))
            for e2 in part.get("extra") or []:
                lines.append("   stop: " + str(e2.get("title") or e2.get("service") or "?")
                             + (f" ({e2['note']})" if e2.get("note") else ""))
            if part.get("note"):
                lines.append("   note: " + part["note"])
    goal = est["goal"]
    if not route.get("parts"):
        lines.append(f"No parts yet: add them with op=add. To reach level {goal} the character needs about "
                     f"{int(est['short']):,} XP, quests and the creatures on the way together.")
    elif est["short"] > 0:
        lines.append(f"Expected: level {est['end']:.1f} at the end, {goal - est['end']:.1f} levels short of {goal} "
                     f"(about {int(est['short']):,} XP more: add parts).")
    else:
        lines.append(f"Expected: level {est['end']:.1f} at the end: the goal, level {goal}, is reached.")
    lines.append(f"Creatures on the way count {round(est['share'] * 100)}% of the quests' XP ({est['shareNote']})"
                 + (f"; rested XP doubles the first {int(est['rested']):,} of it" if est["rested"] else "") + ".")
    if est.get("rate"):
        lines.append(f"The player's pace: about {int(est['rate']):,} XP per hour of play.")
    for w in est["warnings"]:
        lines.append("Warning: " + w)
    return "\n".join(lines)


def summary(route: dict, snapshot: dict, est: dict | None = None) -> dict:
    """The long route as the add-on shows it: a card in the chat, a line on the strip, the head of the stop list."""
    est = est or estimate(route, snapshot)
    parts = []
    for i, part in enumerate(route.get("parts") or []):
        e = est["parts"][i] if i < len(est["parts"]) else {}
        parts.append({"title": part["title"], "zone": part["zone"], **({"then": part["then"]} if part.get("then") else {}),
                      **({"done": True} if e.get("done") else
                         {"from": round(e.get("from", 0), 1), "to": round(e.get("to", 0), 1),
                          "quests": e.get("left", 0), "xp": int(e.get("quests", 0) + e.get("kills", 0)),
                          **({"mins": int(e["hours"] * 60)} if e.get("hours") else {})}),
                      **({"note": part["note"]} if part.get("note") else {})})
    return {"id": route.get("id"), "title": route.get("title"), "goal": route.get("goal"),
            "current": int(route.get("current") or 0) + 1, "end": round(est["end"], 1), "parts": parts,
            **({} if route.get("mapped") == route.get("current") else {"offMap": True})}


# ------------------------------------------------------------------------ planning a part
def part_args(route: dict, i: int, snapshot: dict) -> dict:
    """plan_route's arguments for part ``i``: its quests to pick up, the quests of the log that later parts hold left
    out (they wait for their part), the zones it goes into from where the player stands."""
    part = route["parts"][i]
    player = snapshot.get("player") or {}
    here = str(player.get("map") or player.get("zone") or "").lower()
    level = int(_num(player.get("level"), 1))
    zones = [part["zone"]] + list(part.get("then") or [])
    then = [{"zone": z} for z in dict.fromkeys(zones) if z.lower() != here]
    mine = {norm(q.get("title")) for q in part.get("quests") or []}
    later = sorted(t for t in taken_titles(route) - taken_titles(route, i + 1) if t not in mine)
    log_titles = {norm(q["title"]): q["title"] for q in log_quests(snapshot)}
    skip = list(dict.fromkeys(list(route.get("skip") or []) + list(part.get("skip") or [])
                              + [log_titles[t] for t in later if t in log_titles]))
    # a quest that opens at a level the character does not have yet cannot be picked up: it waits (its stop would wait
    # for nothing the player can do)
    new = [q for q in part.get("quests") or [] if not q.get("inLog")
           and not (isinstance(q.get("minLevel"), (int, float)) and q["minLevel"] > level)]
    args: dict = {"new_quests": [q["title"] for q in new]} if new else {}
    if then:
        args["then"] = then
    if skip:
        args["skip"] = skip
    for key in ("first", "last", "use", "avoid", "extra"):
        if part.get(key):
            args[key] = list(part[key])
    offered = [{k: q[k] for k in QUEST_KEYS if q.get(k) is not None} for q in new if q.get("where")]
    if offered:
        args["offered"] = offered
    return args


def waiting(route: dict, i: int, snapshot: dict) -> list[str]:
    """The quests of part ``i`` that open at a level the character does not have yet."""
    level = int(_num((snapshot.get("player") or {}).get("level"), 1))
    return [f"[{q['title']}] (opens at level {int(q['minLevel'])})" for q in route["parts"][i].get("quests") or []
            if not q.get("inLog") and isinstance(q.get("minLevel"), (int, float)) and q["minLevel"] > level]


# ------------------------------------------------------------------------ the file
class Store:
    """The long route of each character, in one file of the data folder (kept across restarts of the app)."""

    def __init__(self):
        self._routes: dict | None = None

    def _all(self) -> dict:
        if self._routes is None:
            try:
                data = json.loads((config.data_dir() / FILE_NAME).read_text(encoding="utf-8"))
                self._routes = data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                self._routes = {}
        return self._routes

    def get(self, char: str) -> dict | None:
        route = self._all().get(char or "")
        return route if isinstance(route, dict) and isinstance(route.get("parts"), list) else None

    def put(self, char: str, route: dict) -> None:
        self._all()[char or ""] = route
        self._save()

    def drop(self, char: str) -> bool:
        gone = self._all().pop(char or "", None) is not None
        if gone:
            self._save()
        return gone

    def _save(self) -> None:
        try:
            path = config.data_dir() / FILE_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._all(), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
