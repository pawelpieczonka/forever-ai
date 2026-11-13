"""Reading the data tables other add-ons keep in their Lua files.

The app reads the player's own copies in Interface/AddOns (nothing of theirs is shipped with ForeverAI): an add-on
whose tables are private to it cannot be asked in the game, but its data files are plain table literals."""

from __future__ import annotations

import re
from pathlib import Path

_TOKEN = re.compile(r"""
    (?P<skip>\s+|--\[\[.*?\]\]|--[^\n]*)
  | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<num>-?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+))
  | (?P<name>[A-Za-z_][A-Za-z0-9_.:]*)
  | (?P<sym>[{}\[\]=,;()])
""", re.S | re.X)
_ESCAPE = re.compile(r"\\(\d{1,3}|.)", re.S)
_PLAIN = {"n": "\n", "t": "\t", "r": "\r", "a": "", "b": "", "f": "", "v": ""}
_WORDS = {"true": True, "false": False, "nil": None}


class LuaError(ValueError):
    pass


def _string(raw: str) -> str:
    body = raw[1:-1]
    if "\\" not in body:
        return body
    # decimal escapes are bytes (a name in UTF-8 written as \195\169): put them back together
    data = bytearray()
    pos = 0
    for m in _ESCAPE.finditer(body):
        data += body[pos:m.start()].encode("utf-8")
        code = m.group(1)
        data += bytes([int(code) & 255]) if code.isdigit() else _PLAIN.get(code, code).encode("utf-8")
        pos = m.end()
    data += body[pos:].encode("utf-8")
    return data.decode("utf-8", errors="replace")


class _Reader:
    def __init__(self, text: str, pos: int):
        self.text, self.pos = text, pos
        self.kind, self.value = "", ""
        self.next()

    def next(self) -> None:
        text, pos = self.text, self.pos
        while True:
            m = _TOKEN.match(text, pos)
            if not m:
                self.kind, self.value, self.pos = ("end", "", pos) if pos >= len(text) else ("bad", text[pos], pos + 1)
                return
            pos = m.end()
            if m.lastgroup != "skip":
                self.kind, self.value, self.pos = m.lastgroup, m.group(), pos
                return

    def take(self, sym: str) -> None:
        if self.kind != "sym" or self.value != sym:
            raise LuaError(f"expected {sym} near offset {self.pos}")
        self.next()

    def value_(self):
        kind, value = self.kind, self.value
        if kind == "str":
            self.next()
            return _string(value)
        if kind == "num":
            self.next()
            number = float.fromhex(value) if value.lower().lstrip("-").startswith("0x") else float(value)
            return int(number) if number == int(number) and "." not in value and "e" not in value.lower() else number
        if kind == "sym" and value == "{":
            return self.table()
        if kind == "name":
            self.next()
            if value in _WORDS:
                return _WORDS[value]
            # something that is no literal (L["text"], a call): the text inside when there is one, else nothing
            inner = None
            while self.kind == "sym" and self.value in "[(":
                close = "]" if self.value == "[" else ")"
                self.next()
                if self.kind != "sym" or self.value != close:
                    inner = self.value_()
                self.take(close)
            return inner
        raise LuaError(f"unexpected {value!r} near offset {self.pos}")

    def table(self):
        self.take("{")
        items, keyed = [], {}
        while not (self.kind == "sym" and self.value == "}"):
            if self.kind == "sym" and self.value == "[":
                self.next()
                key = self.value_()
                self.take("]")
                self.take("=")
                keyed[key] = self.value_()
            elif self.kind == "name" and self.value not in _WORDS and self._assigns():
                key = self.value
                self.next()
                self.take("=")
                keyed[key] = self.value_()
            else:
                items.append(self.value_())
            if self.kind == "sym" and self.value in ",;":
                self.next()
            elif not (self.kind == "sym" and self.value == "}"):
                raise LuaError(f"expected , or }} near offset {self.pos}")
        self.next()
        if not keyed:
            return items
        for n, item in enumerate(items, 1):
            keyed.setdefault(n, item)
        return keyed

    def _assigns(self) -> bool:
        m = _TOKEN.match(self.text, self.pos)
        while m and m.lastgroup == "skip":
            m = _TOKEN.match(self.text, m.end())
        return bool(m and m.group() == "=")


def table_after(text: str, start: str):
    """The table literal that follows the first place ``start`` (a regular expression) matches: lists for tables
    with only positions, dicts for the rest. None when ``start`` is not in the text; LuaError when the table is not
    a literal the reader understands."""
    m = re.search(start, text)
    if not m:
        return None
    brace = text.find("{", m.end() - 1 if text[m.end() - 1:m.end()] == "{" else m.end())
    if brace < 0:
        return None
    return _Reader(text, brace).table()


def assigned(text: str, name: str):
    """The table a file assigns to a field: ``ns.RareData = { ... }`` is ``assigned(text, "RareData")``."""
    return table_after(text, r"(?m)^[ \t]*(?:local[ \t]+)?[\w.]*\b" + re.escape(name) + r"[ \t]*=[ \t]*\{")


def passed(text: str, function: str):
    """The table a file passes to a function: ``Addon:MergeNPCs({ ... })`` is ``passed(text, "MergeNPCs")``."""
    return table_after(text, r"\b" + re.escape(function) + r"[ \t]*\([ \t]*\{")


def read(path) -> str:
    """The text of a data file; empty when it is not there."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
