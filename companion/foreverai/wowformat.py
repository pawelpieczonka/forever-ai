"""Markdown -> WoW UI escape-sequence formatting for FontStrings/SimpleHTML-less text."""

from __future__ import annotations

import re

GOLD = "ffd100"
WHITE = "ffffff"
ITEM = "66ccff"
CODE = "a0e0a0"
LINK = "8fb8ff"


def _c(color: str, text: str) -> str:
    return f"|cff{color}{text}|r"


def _inline(s: str) -> str:
    # links [text](url) -> text
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", lambda m: _c(LINK, m.group(1)), s)
    # bare urls -> domain only
    s = re.sub(r"https?://([^/\s)]+)[^\s)]*", lambda m: _c(LINK, m.group(1)), s)
    # bold / italic / code
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: _c(WHITE, m.group(1)), s)
    s = re.sub(r"__(.+?)__", lambda m: _c(WHITE, m.group(1)), s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"`([^`]+)`", lambda m: _c(CODE, m.group(1)), s)
    # [Item Name] (game entity names in square brackets, not followed by "(")
    s = re.sub(r"\[([A-Z][^\[\]\n|]{1,60})\](?!\()", lambda m: _c(ITEM, "[" + m.group(1) + "]"), s)
    return s


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def bracket_names(md: str) -> list[str]:
    """Game entity names the model wrote in square brackets, e.g. [Worn Shortbow]."""
    seen, out = set(), []
    for m in re.finditer(r"\[([A-Z][^\[\]\n|]{1,60})\](?!\()", md or ""):
        n = m.group(1).strip()
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def md_to_wow(md: str) -> str:
    md = _EMOJI.sub("", md)  # the game fonts have no emoji glyphs (they render as boxes)
    md = md.replace("\r", "").replace("|", "¦")  # pipes are WoW escape chars; tables handled below
    out = []
    for raw in md.split("\n"):
        line = raw.rstrip()
        if re.fullmatch(r"\s*¦?[-:¦ ]{3,}¦?\s*", line) and "-" in line:
            continue  # markdown table separator
        if line.strip().startswith("¦") and line.strip().endswith("¦"):
            cells = [c.strip() for c in line.strip().strip("¦").split("¦")]
            out.append(_inline(" – ".join(c for c in cells if c)))
            continue
        line = line.replace("¦", "/")
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            # WoW keeps a color stack: nested |c...|r pairs are fine, re-opening colors would leak to the next lines
            out.append(_c(GOLD, _inline(m.group(2))))
            continue
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            indent = "   " * (len(m.group(1)) // 2)
            out.append(f"{indent}• {_inline(m.group(2))}")
            continue
        if re.fullmatch(r"\s*([-*_])\s*\1\s*\1[\s\-*_]*", line):
            out.append(_c("808080", "———"))
            continue
        out.append(_inline(line))
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
