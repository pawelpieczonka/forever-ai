"""The player's progress over time (the add-on's ``progress`` section) as a short text for the model: XP and gold per
hour without AFK time, time to the next level and to later levels, played time per level, the last session and today.

The add-on sends:
- ``level``, ``xp``, ``xpMax``, ``rested``, ``money`` (copper), ``maxLevel``, ``now`` (epoch seconds, the computer's clock);
- ``session``: the current session (login to logout; a /reload continues it) with ``start``, ``act`` (playing seconds:
  AFK and freezes left out), ``gain`` (XP), ``questXp``, ``lvl0``/``lvl``, ``money0``/``money``, ``earned``/``spent``
  (copper per cause), ``kills``, ``quests``, ``deaths``, ``reloads``, ``zones`` (playing seconds per zone);
- ``samples``: the current session downsampled, ``[t, act, level, xp, xpMax, rested, money]``;
- ``sessions``: summaries of earlier sessions, newest first (as ``session``, plus ``stop``);
- ``levels``: level-ups ``{level, at, online}``; ``online``: logged-in seconds counted by ForeverAI;
- ``played``: the last /played reading ``{total, level, at, online, lvl}`` (ForeverAI never asks for it itself).
"""

from __future__ import annotations

import time as _time
from datetime import datetime
from typing import Any

from .tools import money

AFK_GAP_S = 300          # samples without playing time: a longer gap between them is a break
MIN_PACE_S = 15 * 60     # less playing time than this says little about the pace
RECENT_WINDOW_S = 45 * 60  # "lately" in the current session: the last 45 minutes of play
RECENT_DAYS = 7

# XP from each level to the next in Classic (1 -> 2 is 400 ... 59 -> 60 is 209,800; 4,084,700 in total). Forever may
# differ: levels the game has shown keep their real value and the table is scaled to the current level's real value.
CLASSIC_XP = [400, 900, 1400, 2100, 2800, 3600, 4500, 5400, 6500, 7600, 8800, 10100, 11400, 12900, 14400, 16000, 17700,
              19400, 21300, 23200, 25200, 27300, 29400, 31700, 34000, 36400, 38900, 41400, 44300, 47400, 50800, 54500,
              58600, 62800, 67100, 71600, 76100, 80800, 85700, 90700, 95800, 101000, 106300, 111800, 117500, 123200,
              129100, 135100, 141200, 147500, 153900, 160400, 167100, 173900, 180800, 187900, 195000, 202300, 209800]


def _num(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def duration(sec: float | None) -> str:
    """1 h 52 min, 45 min, 2 d 3 h."""
    s = int(max(0, sec or 0))
    if s >= 86400:
        return f"{s // 86400} d {s // 3600 % 24} h"
    if s >= 3600:
        return f"{s // 3600} h {s // 60 % 60} min"
    return f"{max(1, s // 60) if s else 0} min"


def _n(v: float | None) -> str:
    return f"{int(round(v or 0)):,}"


def _clock(t: float, with_date: bool = False) -> str:
    d = datetime.fromtimestamp(t)
    return d.strftime("%Y-%m-%d %H:%M") if with_date else d.strftime("%H:%M")


def _ago(seconds: float) -> str:
    return "just now" if seconds < 60 else f"{duration(seconds)} ago"


def _signed(copper: float) -> str:
    return ("+" if copper >= 0 else "-") + money(abs(copper))


# ------------------------------------------------------------------------ samples
def _sample(row: Any) -> dict | None:
    if not isinstance(row, (list, tuple)) or len(row) < 5:
        return None
    vals = [_num(x) for x in row] + [None] * 7
    t, act, lvl, xp, xp_max, rested, cash = vals[:7]
    if t is None or lvl is None or xp is None:
        return None
    return {"t": t, "act": act, "lvl": int(lvl), "xp": xp, "xpMax": xp_max or 0, "rested": rested or 0, "money": cash}


def samples(section: dict) -> list[dict]:
    out = [s for s in (_sample(r) for r in section.get("samples") or []) if s]
    out.sort(key=lambda s: s["t"])
    return out


def step_xp(a: dict, b: dict) -> float:
    """XP gained from sample a to sample b; across a level-up the rest of a's level counts too."""
    if b["lvl"] == a["lvl"]:
        return max(0.0, b["xp"] - a["xp"])
    if b["lvl"] == a["lvl"] + 1:
        return max(0.0, a["xpMax"] - a["xp"]) + b["xp"]
    if b["lvl"] > a["lvl"]:  # more than one level: the levels in between are not known exactly
        return max(0.0, a["xpMax"] - a["xp"]) + b["xp"] + sum(level_xp(lv, {}) for lv in range(a["lvl"] + 1, b["lvl"]))
    return 0.0


def step_play(a: dict, b: dict) -> float:
    """Playing seconds from a to b: the add-on's count when it is there, else the wall time without breaks."""
    if a.get("act") is not None and b.get("act") is not None and b["act"] >= a["act"]:
        return b["act"] - a["act"]
    dt = b["t"] - a["t"]
    return dt if 0 < dt <= AFK_GAP_S else 0.0


def pace(rows: list[dict], window_s: float | None = None) -> tuple[float, float, float]:
    """(XP, playing seconds, copper) over the samples, or over their last ``window_s`` seconds of play."""
    xp = play = cash = 0.0
    for a, b in reversed(list(zip(rows, rows[1:]))):
        if window_s is not None and play >= window_s:
            break
        xp += step_xp(a, b)
        play += step_play(a, b)
        if a.get("money") is not None and b.get("money") is not None:
            cash += b["money"] - a["money"]
    return xp, play, cash


def per_hour(amount: float, seconds: float) -> float | None:
    return amount / seconds * 3600 if seconds and seconds > 0 else None


# ------------------------------------------------------------------------ levels
def level_xp(level: int, known: dict[int, float], scale: float = 1.0) -> float:
    """XP from ``level`` to the next: as the game showed it, else the Classic table (scaled)."""
    if level in known:
        return known[level]
    if 1 <= level <= len(CLASSIC_XP):
        return CLASSIC_XP[level - 1] * scale
    return CLASSIC_XP[-1] * scale


def xp_to_level(level: int, xp: float, xp_max: float, target: int, known: dict[int, float] | None = None,
                scale: float = 1.0) -> float:
    """XP still needed from the current state to reach ``target``."""
    if target <= level:
        return 0.0
    known = dict(known or {})
    if xp_max:
        known[level] = xp_max
    need = max(0.0, (xp_max or level_xp(level, known, scale)) - xp)
    for lv in range(level + 1, target):
        need += level_xp(lv, known, scale)
    return need


def table_scale(level: int, xp_max: float) -> float:
    """How this server's XP for the current level compares with Classic (1.0 = the same)."""
    if xp_max and 1 <= level <= len(CLASSIC_XP):
        return xp_max / CLASSIC_XP[level - 1]
    return 1.0


def targets(level: int, max_level: int) -> list[int]:
    """The next few levels and every fifth level up to the maximum."""
    out = [lv for lv in range(level + 1, min(level + 3, max_level) + 1)]
    out += [lv for lv in range(level + 1, max_level + 1) if lv % 5 == 0 and lv not in out]
    if max_level > level and max_level not in out:
        out.append(max_level)
    return sorted(out)


# ------------------------------------------------------------------------ played time
def played_now(section: dict) -> tuple[float | None, float | None, str | None]:
    """(total played, played at the current level, note) from the last /played, counted forward with the
    add-on's logged-in time."""
    p = section.get("played") or {}
    total, online, at = _num(p.get("total")), _num(section.get("online")), _num(p.get("at"))
    if total is None:
        return None, None, None
    since = max(0.0, (online or 0) - (_num(p.get("online"), online) or 0))
    level_now = None
    if p.get("level") is not None and p.get("lvl") == section.get("level"):
        level_now = (_num(p.get("level")) or 0) + since
    note = f"from /played on {_clock(at, True)}" if at else "from /played"
    if since >= 60:
        note += f", plus {duration(since)} logged in since"
    return total + since, level_now, note


def level_times(section: dict) -> list[tuple[int, float]]:
    """(level, logged-in seconds it took) for every level both of whose level-ups ForeverAI saw."""
    ups = sorted((lv for lv in section.get("levels") or [] if isinstance(lv, dict)), key=lambda e: _num(e.get("at"), 0))
    out = []
    for a, b in zip(ups, ups[1:]):
        la, lb = int(_num(a.get("level"), 0)), int(_num(b.get("level"), 0))
        oa, ob = _num(a.get("online")), _num(b.get("online"))
        if lb == la + 1 and oa is not None and ob is not None and ob >= oa:
            out.append((la, ob - oa))
    return out


# ------------------------------------------------------------------------ sessions
def causes(table: Any) -> str:
    items = sorted(((str(k), _num(v, 0) or 0) for k, v in (table or {}).items()), key=lambda kv: -kv[1])
    items = [(k, v) for k, v in items if v > 0]
    if not items:
        return "nothing"
    return f"{money(sum(v for _, v in items))} (" + ", ".join(f"{k} {money(v)}" for k, v in items) + ")"


def _zones(z: Any) -> str:
    items = sorted(((str(k), _num(v, 0) or 0) for k, v in (z or {}).items()), key=lambda kv: -kv[1])
    return ", ".join(f"{k} {duration(v)}" for k, v in items[:4] if v >= 60)


def session_line(s: dict) -> str:
    act, gain = _num(s.get("act"), 0) or 0, _num(s.get("gain"), 0) or 0
    bits = [f"XP +{_n(gain)}" + (f" ({_n(s.get('questXp'))} from quests)" if _num(s.get("questXp"), 0) else "")]
    rate = per_hour(gain, act)
    if rate and act >= MIN_PACE_S:
        bits[0] += f", {_n(rate)} XP/h"
    l0, l1 = s.get("lvl0"), s.get("lvl")
    if l0 is not None and l1 is not None and l1 != l0:
        bits.append(f"level {l0} -> {l1}")
    elif l1 is not None:
        bits.append(f"level {l1}")
    m0, m1 = _num(s.get("money0")), _num(s.get("money"))
    if m0 is not None and m1 is not None:
        bits.append(f"money {_signed(m1 - m0)} net")
    if s.get("earned"):
        bits.append(f"earned {causes(s.get('earned'))}")
    if s.get("spent"):
        bits.append(f"spent {causes(s.get('spent'))}")
    counts = [f"{k} {int(_num(s.get(k), 0) or 0)}" for k in ("kills", "quests", "deaths") if s.get(k) is not None]
    if counts:
        bits.append(", ".join(counts))
    z = _zones(s.get("zones"))
    if z:
        bits.append("zones: " + z)
    return "; ".join(bits)


def _span(s: dict, now: float) -> str:
    start, stop = _num(s.get("start"), now), _num(s.get("stop"))
    end = stop if stop is not None else now
    head = f"{_clock(start, True)}–{_clock(end)}" if stop is not None else f"since {_clock(start)}"
    return f"{head} ({duration(end - start)} logged in, {duration(s.get('act'))} playing)"


def _day(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def today(section: dict, now: float) -> dict | None:
    """Sessions of the computer's current day (by start), the current one included, added up."""
    day = _day(now)
    current = dict(section.get("session") or {})
    rows = [s for s in section.get("sessions") or [] if isinstance(s, dict) and _day(_num(s.get("start"), 0)) == day]
    if current and _day(_num(current.get("start"), now)) == day:
        rows.append(current)
    if not rows:
        return None
    tot: dict[str, Any] = {"n": len(rows), "earned": {}, "spent": {}, "zones": {}}
    for s in rows:
        for k in ("act", "gain", "questXp", "levels", "kills", "quests", "deaths"):
            tot[k] = tot.get(k, 0) + (_num(s.get(k), 0) or 0)
        m0, m1 = _num(s.get("money0")), _num(s.get("money"))
        if m0 is not None and m1 is not None:
            tot["net"] = tot.get("net", 0) + m1 - m0
        for key in ("earned", "spent", "zones"):
            for c, v in (s.get(key) or {}).items():
                tot[key][c] = tot[key].get(c, 0) + (_num(v, 0) or 0)
    tot["first"] = min(_num(s.get("start"), now) for s in rows)
    return tot


def recent(section: dict, now: float, days: int = RECENT_DAYS) -> tuple[float, float, float, int]:
    """(XP, playing seconds, net copper, sessions) of the earlier sessions of the last ``days`` days."""
    xp = play = cash = 0.0
    n = 0
    for s in section.get("sessions") or []:
        if not isinstance(s, dict) or _num(s.get("stop"), 0) < now - days * 86400:
            continue
        n += 1
        xp += _num(s.get("gain"), 0) or 0
        play += _num(s.get("act"), 0) or 0
        m0, m1 = _num(s.get("money0")), _num(s.get("money"))
        if m0 is not None and m1 is not None:
            cash += m1 - m0
    return xp, play, cash, n


# ------------------------------------------------------------------------ the text
def render(section: Any, now: float | None = None) -> str:
    """The progress section as text for the model."""
    if not isinstance(section, dict) or not section:
        return "No progress history yet: ForeverAI records it from now on."
    now = _num(section.get("now")) or now or _time.time()
    level = int(_num(section.get("level"), 0) or 0)
    xp, xp_max = _num(section.get("xp"), 0) or 0, _num(section.get("xpMax"), 0) or 0
    rested = _num(section.get("rested"), 0) or 0
    max_level = int(_num(section.get("maxLevel"), 0) or 0) or len(CLASSIC_XP) + 1
    sess = section.get("session") if isinstance(section.get("session"), dict) else {}
    rows = samples(section)
    lines: list[str] = []

    if level:
        head = f"Now: level {level}"
        if level < max_level and xp_max:
            head += f", {_n(xp)} / {_n(xp_max)} XP ({int(xp / xp_max * 100)}%), {_n(xp_max - xp)} XP to level {level + 1}"
        elif level >= max_level:
            head += " (maximum level)"
        head += f"; rested bonus {_n(rested)} XP" if rested else "; no rested XP"
        if section.get("money") is not None:
            head += f"; money {money(section.get('money'))}"
        lines.append(head + ".")

    # this session
    act = _num(sess.get("act"), 0) or 0
    gain = _num(sess.get("gain"), 0) or 0
    if rows and not gain:
        gain = pace(rows)[0]
    if not act and rows:
        act = pace(rows)[1]
    if sess:
        reloads = int(_num(sess.get("reloads"), 0) or 0)
        start = _num(sess.get("start"), now) or now
        lines.append(f"This session: since {_clock(start)} ({duration(now - start)} logged in, {duration(act)} playing; "
                     f"AFK and breaks left out" + (f"; {reloads} /reload{'s' if reloads != 1 else ''}, same session"
                                                   if reloads else "") + ").")
        lines.append("- " + session_line({**sess, "act": act, "gain": gain}))
    session_rate = per_hour(gain, act) if act >= MIN_PACE_S else None
    late_xp, late_play, _ = pace(rows, RECENT_WINDOW_S) if rows else (0, 0, 0)
    late_rate = per_hour(late_xp, late_play) if late_play >= MIN_PACE_S else None
    if late_rate is not None and act > late_play + 60:
        lines.append(f"- Lately (the last {duration(late_play)} of play): {_n(late_rate)} XP/h.")
    if act >= MIN_PACE_S:
        m0, m1 = _num(sess.get("money0")), _num(sess.get("money"))
        if m0 is not None and m1 is not None:
            lines.append(f"- Gold: {_signed(m1 - m0)} net, {_signed(per_hour(m1 - m0, act) or 0)} per hour of play.")

    # earlier sessions
    r_xp, r_play, r_cash, r_n = recent(section, now)
    recent_rate = per_hour(r_xp, r_play) if r_play >= MIN_PACE_S else None
    if r_n:
        text = f"Earlier sessions of the last {RECENT_DAYS} days: {r_n}, {duration(r_play)} playing, XP +{_n(r_xp)}"
        if recent_rate:
            text += f" ({_n(recent_rate)} XP/h)"
        text += f", money {_signed(r_cash)} net" + (f" ({_signed(per_hour(r_cash, r_play) or 0)} per hour)" if r_play >= MIN_PACE_S else "")
        lines.append(text + ".")

    # time to level
    rate, rate_name = None, ""
    for value, name in ((session_rate, "this session's pace"), (recent_rate, f"the pace of the last {RECENT_DAYS} days"),
                        (late_rate, "this session's pace")):
        if value and value > 0:
            rate, rate_name = value, name
            break
    if level and level < max_level and xp_max:
        if rate:
            known = {r["lvl"]: r["xpMax"] for r in rows if r.get("xpMax")}
            scale = table_scale(level, xp_max)
            est = []
            for target in targets(level, max_level):
                need = xp_to_level(level, xp, xp_max, target, known, scale)
                est.append(f"{target}: ~{duration(need / rate * 3600)}")
            lines.append(f"Time to level (playing time at {rate_name}, {_n(rate)} XP/h): " + "; ".join(est) + ".")
            note = ("Approximate: later levels need more XP and the pace changes with zone, group and quests; levels the "
                    "game has not shown yet use the Classic XP table" +
                    (f" scaled by {scale:.2f} to this server's XP for level {level}" if abs(scale - 1) > 0.01 else "") + ".")
            if rested:
                saved = min(rested, xp_to_level(level, xp, xp_max, level + 1, known, scale))
                note += (f" Rested XP doubles the XP from kills until the {_n(rested)} bonus is used up, so the next level "
                         f"can come up to ~{duration(saved / rate * 3600)} sooner when the XP comes from kills.")
            lines.append(note)
        else:
            lines.append("Time to level: not enough playing time recorded yet for a pace (needs about 15 minutes).")

    # last session
    past = [s for s in section.get("sessions") or [] if isinstance(s, dict)]
    if past:
        last = max(past, key=lambda s: _num(s.get("start"), 0))
        lines.append(f"Last session: {_span(last, now)}: {session_line(last)}.")

    # today
    day = today(section, now)
    if day:
        bits = [f"{day['n']} session{'s' if day['n'] != 1 else ''} since {_clock(day['first'])}",
                f"{duration(day.get('act'))} playing", f"XP +{_n(day.get('gain'))}"]
        if day.get("levels"):
            bits.append(f"levels +{int(day['levels'])}")
        if "net" in day:
            bits.append(f"money {_signed(day['net'])} net")
        if day["earned"]:
            bits.append(f"earned {causes(day['earned'])}")
        if day["spent"]:
            bits.append(f"spent {causes(day['spent'])}")
        bits.append(", ".join(f"{k} {int(day.get(k) or 0)}" for k in ("kills", "quests", "deaths")))
        lines.append(f"Today ({_day(now)}): " + "; ".join(bits) + ".")

    # played time
    total, at_level, note = played_now(section)
    if total is not None:
        lines.append(f"Played: {duration(total)} in total" + (f", {duration(at_level)} at level {level}" if at_level is not None
                                                            else "") + f" ({note}).")
    elif _num(section.get("online"), 0):
        since = _num(section.get("since"))
        lines.append(f"Played: not known (the player has not typed /played since ForeverAI started recording); logged in "
                     f"{duration(section.get('online'))} counted by ForeverAI" + (f" since {_clock(since, True)}" if since else "")
                     + ".")
    per_level = level_times(section)
    if per_level:
        lines.append("Time per level (logged in, counted by ForeverAI): " +
                     ", ".join(f"{lv} -> {lv + 1} {duration(sec)}" for lv, sec in per_level[-8:]) + ".")
    ups = [e for e in section.get("levels") or [] if isinstance(e, dict) and e.get("at")]
    if ups and not per_level:
        lines.append("Level-ups seen: " + ", ".join(f"{int(_num(e.get('level'), 0) or 0)} on {_clock(_num(e['at'], 0), True)}"
                                                     for e in ups[-5:]) + ".")
    return "\n".join(lines) if lines else "No progress history yet: ForeverAI records it from now on."
