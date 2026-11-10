"""Checks on one answered turn: {"answer": text, "tools": [{"name", "args"}], "did": [...], "secs": n}.
Each factory returns a function turn -> Result; the label says what was expected."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Result:
    label: str
    ok: bool
    detail: str = ""


Check = Callable[[dict], Result]


def words(md: str) -> int:
    """Words the player reads: markup, coordinates and bare numbers do not count."""
    text = re.sub(r"[#*_`\[\]()>|-]", " ", md or "")
    return len([w for w in re.findall(r"[^\W\d_][\w'’]*", text)])


def headings(md: str) -> int:
    return len(re.findall(r"^#{1,6} ", md or "", re.M))


def _tools(turn: dict, name: str) -> list[dict]:
    return [t for t in turn.get("tools") or [] if str(t.get("name", "")).split("__")[-1] == name]


def max_words(n: int) -> Check:
    def check(turn: dict) -> Result:
        got = words(turn["answer"])
        return Result(f"at most {n} words", got <= n, f"{got} words")
    return check


def max_headings(n: int) -> Check:
    def check(turn: dict) -> Result:
        got = headings(turn["answer"])
        return Result(f"at most {n} headings", got <= n, f"{got} headings")
    return check


def calls(name: str, lo: int, hi: int) -> Check:
    def check(turn: dict) -> Result:
        got = len(_tools(turn, name))
        return Result(f"{name} x{lo}" if lo == hi else f"{name} x{lo}-{hi}", lo <= got <= hi, f"{got} calls")
    return check


def _marked(turn: dict) -> list[tuple[float, float]]:
    """Where the answer put marks: mark_on_map calls and what the answer did (the route tool marks its stops itself)."""
    got = []
    for t in _tools(turn, "mark_on_map"):
        try:
            got.append((float(t["args"].get("x")), float(t["args"].get("y"))))
        except (TypeError, ValueError):
            pass
    for line in turn.get("did") or []:
        m = re.match(r"^(?:marked on the map|added to the marks).*\((\d+(?:\.\d+)?), (\d+(?:\.\d+)?)\)$", line)
        if m and (float(m.group(1)), float(m.group(2))) not in got:
            got.append((float(m.group(1)), float(m.group(2))))
    return got


def marked(lo: int, hi: int) -> Check:
    """How many places the answer marked, however it did it."""
    def check(turn: dict) -> Result:
        got = len(_marked(turn))
        return Result(f"marks {lo}" if lo == hi else f"marks {lo}-{hi} places", lo <= got <= hi, f"{got} marks")
    return check


def marks(x: float, y: float, tolerance: float = 0.6) -> Check:
    """A map mark at (x, y)."""
    def check(turn: dict) -> Result:
        got = _marked(turn)
        ok = any(abs(gx - x) <= tolerance and abs(gy - y) <= tolerance for gx, gy in got)
        return Result(f"marks {x}, {y}", ok, f"marked {got}")
    return check


def says(*patterns: str) -> Check:
    def check(turn: dict) -> Result:
        missing = [p for p in patterns if not re.search(p, turn["answer"], re.I)]
        return Result("says " + ", ".join(patterns), not missing, "missing: " + ", ".join(missing) if missing else "")
    return check


def never_says(*patterns: str) -> Check:
    def check(turn: dict) -> Result:
        found = [p for p in patterns if re.search(p, turn["answer"], re.I)]
        return Result("never says " + ", ".join(patterns), not found, "found: " + ", ".join(found) if found else "")
    return check


def _sold(turn: dict) -> list[str]:
    out = []
    for t in _tools(turn, "propose_vendor_action"):
        if t["args"].get("op") == "sell":
            out += [str(i).strip("[] ").lower() for i in t["args"].get("items") or []]
    return out


def sells(*names: str) -> Check:
    """Every item (any of its names: name or id) is in a proposed sale."""
    def check(turn: dict) -> Result:
        sold = _sold(turn)
        return Result("sells " + " / ".join(names), any(n.lower() in sold for n in names), f"sold {sold}")
    return check


def keeps(*names: str) -> Check:
    """The item (given by name and id) is in no proposed sale."""
    def check(turn: dict) -> Result:
        sold = _sold(turn)
        return Result("keeps " + names[0], not any(n.lower() in sold for n in names), f"sold {sold}")
    return check


def at_most(n: int, pattern: str) -> Check:
    def check(turn: dict) -> Result:
        got = len(re.findall(pattern, turn["answer"], re.I))
        return Result(f"{pattern} at most {n}x", got <= n, f"{got}x")
    return check


def language(code: str) -> Check:
    """"en" or anything else: told apart by common English words (game names in [brackets] do not count)."""
    def check(turn: dict) -> Result:
        text = re.sub(r"\[[^\]]*\]", " ", turn["answer"])
        english = len(re.findall(r"\b(the|you|your|and|is|are|it|that|this|with)\b", text, re.I))
        total = max(1, words(text))
        is_english = english / total > 0.08
        return Result(f"answers in {code}", is_english == (code == "en"), f"{english} English words of {total}")
    return check
