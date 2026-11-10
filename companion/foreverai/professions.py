"""The player's professions (the add-on's ``professions`` section) as a short text for the model.

Per profession the add-on sends ``rank``, ``maxRank``, ``readAgo`` (seconds since its window was read), ``recipes``
(``n`` name, ``i`` crafted item id, ``d`` difficulty optimal/medium/easy, ``c`` how many can be made now, ``u`` skill-ups
per craft, ``m`` [min, max] made, ``r`` reagents: ``n`` name, ``i`` item id, ``q`` needed, ``h`` in the bags now, ``b`` more
in the bank), ``gray`` (names of recipes that give no skill-ups), ``unlearned`` (count), ``partial`` (a filter hid part of
the list), ``capped`` and ``trainer`` (what a trainer offered: ``offers`` with ``n``, ``rank``, ``cost``, ``skill``, ``lvl``,
``ok``).
"""

from __future__ import annotations

from typing import Any

from .progress import duration
from .tools import money

COLORS = {"optimal": "orange", "medium": "yellow", "easy": "green", "trivial": "gray"}
ORDER = {"optimal": 0, "medium": 1, "easy": 2}


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _item(name: Any, item_id: Any) -> str:
    return f"[{name or '?'}]" + (f" (item {item_id})" if _int(item_id) else "")


def recipe_line(r: dict) -> str:
    color = COLORS.get(str(r.get("d")), "color unknown")
    ups = _int(r.get("u"))
    head = f"- {_item(r.get('n'), r.get('i'))} — {color}" + (f" ({ups} skill-ups per craft)" if ups and ups > 1 else "")
    made = r.get("m")
    if isinstance(made, list) and len(made) == 2:
        head += f", makes {made[0]}-{made[1]}"
    can = _int(r.get("c"))
    if can is not None:
        head += f", can make {can} now"
    reagents = []
    for g in r.get("r") or []:
        if not isinstance(g, dict):
            continue
        notes = [f"item {g['i']}"] if _int(g.get("i")) else []
        have, bank = _int(g.get("h")), _int(g.get("b"))
        if have is not None:
            notes.append(f"have {have}" + (f", +{bank} in bank" if bank else ""))
        reagents.append(f"{_int(g.get('q')) or 1} [{g.get('n') or '?'}]" + (f" ({'; '.join(notes)})" if notes else ""))
    if reagents:
        head += ": " + ", ".join(reagents)
    return head


def render(section: Any) -> str:
    if not isinstance(section, dict) or not section:
        return "No profession read yet: the player has to open each profession window once."
    out = []
    for name in sorted(section):
        p = section[name]
        if not isinstance(p, dict):
            continue
        rank, top = p.get("rank"), p.get("maxRank")
        head = f"{name} {rank if rank is not None else '?'}/{top if top is not None else '?'}"
        ago = _int(p.get("readAgo"))
        head += f" (recipes read from its window {duration(ago)} ago" if ago and ago >= 60 else " (recipes read from its window just now"
        head += "; reagents counted in the bags now):"
        out.append(head)
        recipes = [r for r in p.get("recipes") or [] if isinstance(r, dict)]
        recipes.sort(key=lambda r: (ORDER.get(str(r.get("d")), 3), str(r.get("n"))))
        out += [recipe_line(r) for r in recipes] or ["- no recipe that still gives skill-ups"]
        gray = [str(g) for g in p.get("gray") or []]
        if gray:
            out.append(f"Gray, no skill-ups ({len(gray)}): " + ", ".join(sorted(gray)))
        if _int(p.get("unlearned")):
            out.append(f"{p['unlearned']} recipes of {name} are not learned yet (the game lists them; see the knowledge base for "
                       "where they come from).")
        if p.get("partial"):
            out.append(f"Part of the list was hidden by {p['partial']} in the profession window when it was last read; "
                       "recipes not shown then come from an earlier reading.")
        if p.get("capped"):
            out.append("The list was cut at 400 recipes.")
        tr = p.get("trainer") if isinstance(p.get("trainer"), dict) else None
        if tr and tr.get("offers"):
            offers = []
            for o in tr["offers"]:
                if not isinstance(o, dict):
                    continue
                bits = [f"[{o.get('n')}]" + (f" ({o['rank']})" if o.get("rank") else "")]
                if o.get("cost") is not None:
                    bits.append(money(o["cost"]))
                if o.get("skill") is not None:
                    bits.append(f"needs {name} {o['skill']}")
                if _int(o.get("lvl")) and _int(o.get("lvl")) > 1:
                    bits.append(f"level {o['lvl']}")
                bits.append("can learn now" if o.get("ok") else "not yet")
                offers.append(" ".join(bits[:1]) + " — " + ", ".join(bits[1:]))
            who = f" ({tr['npc']})" if tr.get("npc") else ""
            ago = _int(tr.get("readAgo"))
            out.append(f"Trainer{who}, seen {duration(ago) + ' ago' if ago and ago >= 60 else 'just now'}, offers: "
                       + "; ".join(offers))
    return "\n".join(out) if out else "No profession read yet: the player has to open each profession window once."
