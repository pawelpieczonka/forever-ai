"""Text in the simulator: the free fonts that stand in for the game's, and the game's rules for a FontString (colour
codes, hyperlinks, inline textures, word wrap, "..." when a line does not fit). The same layout measures text for the
add-on (GetStringWidth, GetStringHeight) and draws it, so what the add-on measured is what the picture shows."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ImageFont

# The add-on writes with the chat font (Arial Narrow in the English client); the game's other fonts are Friz Quadrata.
# Neither is free, so the closest installed font is used. FOREVERAI_SIM_FONT (a .ttf/.otf path) overrides both.
CANDIDATES = {
    "narrow": ["Liberation Sans Narrow", "Arial Narrow", "Archivo Narrow", "Roboto Condensed", "Fira Sans Condensed",
               "Open Sans Condensed", "DejaVu Sans Condensed", "Liberation Sans", "Arimo", "Arial", "DejaVu Sans"],
    "sans": ["Liberation Sans", "Arimo", "Arial", "Fira Sans", "Open Sans", "Noto Sans", "DejaVu Sans"],
}
WINDOWS_FILES = {"Arial Narrow": "ARIALN.TTF", "Arial": "arial.ttf", "DejaVu Sans": "DejaVuSans.ttf"}
GAME_FONTS = {"arialn.ttf": "narrow"}  # every other game font: "sans"

NAMED_COLORS = {"NORMAL_FONT_COLOR": (255, 209, 0), "HIGHLIGHT_FONT_COLOR": (255, 255, 255),
                "GRAY_FONT_COLOR": (128, 128, 128), "RED_FONT_COLOR": (255, 26, 26), "GREEN_FONT_COLOR": (26, 255, 26),
                "WHITE_FONT_COLOR": (255, 255, 255), "YELLOW_FONT_COLOR": (255, 255, 0)}


def _fc_match(family: str) -> Path | None:
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}\n%{family}", f"{family}:style=Regular"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.splitlines()
    if len(lines) < 2:
        return None
    families = [f.strip().lower() for f in lines[1].split(",")]
    return Path(lines[0]) if family.lower() in families and Path(lines[0]).exists() else None


def _find(family: str) -> Path | None:
    if sys.platform == "win32":
        name = WINDOWS_FILES.get(family)
        p = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name if name else None
        return p if p and p.exists() else None
    return _fc_match(family)


@dataclass
class FontChoice:
    kind: str
    path: Path | None  # None: Pillow's own font
    family: str


def choose_fonts() -> dict[str, FontChoice]:
    forced = os.environ.get("FOREVERAI_SIM_FONT")
    out = {}
    for kind, families in CANDIDATES.items():
        if forced:
            out[kind] = FontChoice(kind, Path(forced), Path(forced).stem)
            continue
        for family in families:
            p = _find(family)
            if p:
                out[kind] = FontChoice(kind, p, family)
                break
        else:
            out[kind] = FontChoice(kind, None, "Pillow default")
    return out


# ---------------------------------------------------------------------------------------------------- escape codes
@dataclass
class Piece:
    text: str
    color: tuple | None  # None: the FontString's own colour
    icon: bool = False


_TOKEN = re.compile(r"\|\||\|c([0-9a-fA-F]{8})|\|cn([A-Z_]*):|\|r|\|H.*?\|h|\|h|\|T.*?\|t|\|A.*?\|a|\|n|\n")


def parse(text: str) -> list[list[Piece]]:
    """The text as paragraphs (one per newline) of pieces with their colour."""
    paragraphs: list[list[Piece]] = [[]]
    colors: list[tuple] = []
    pos = 0

    def add(s: str) -> None:
        if s:
            paragraphs[-1].append(Piece(s, colors[-1] if colors else None))

    for m in _TOKEN.finditer(text):
        add(text[pos:m.start()])
        pos = m.end()
        tok = m.group(0)
        if tok == "||":
            add("|")
        elif m.group(1):
            colors.append(tuple(int(m.group(1)[i:i + 2], 16) for i in (2, 4, 6)))
        elif m.group(2) is not None:
            colors.append(NAMED_COLORS.get(m.group(2), (255, 255, 255)))
        elif tok == "|r":
            if colors:
                colors.pop()
        elif tok in ("\n", "|n"):
            paragraphs.append([])
        elif tok.startswith(("|T", "|A")):
            paragraphs[-1].append(Piece("", colors[-1] if colors else None, icon=True))
        # |H...|h opens a link and |h closes it: the link text itself is shown like any other text
    add(text[pos:])
    return paragraphs


# ---------------------------------------------------------------------------------------------------- layout
@dataclass
class Line:
    pieces: list[Piece] = field(default_factory=list)
    width: float = 0.0


@dataclass
class Layout:
    lines: list[Line]
    width: float
    height: float
    truncated: bool
    line_height: float


ELLIPSIS = "..."


class TextEngine:
    def __init__(self, fonts: dict[str, FontChoice] | None = None):
        self.fonts = fonts or choose_fonts()
        self._faces: dict = {}
        self._widths: dict = {}
        self._layouts: dict = {}

    def face(self, game_path: str, size: float):
        kind = GAME_FONTS.get(game_path.replace("\\", "/").rsplit("/", 1)[-1].lower(), "sans")
        size = max(1.0, round(size, 2))
        key = (kind, size)
        f = self._faces.get(key)
        if f is None:
            choice = self.fonts[kind]
            f = ImageFont.truetype(str(choice.path), size) if choice.path else ImageFont.load_default(size)
            self._faces[key] = f
        return f

    def width(self, face, s: str) -> float:
        key = (id(face), s)
        w = self._widths.get(key)
        if w is None:
            w = face.getlength(s)
            self._widths[key] = w
        return w

    def _piece_width(self, face, p: Piece, size: float) -> float:
        return size if p.icon else self.width(face, p.text)

    def layout(self, text: str, game_font: str, size: float, width: float, wrap: bool, spacing: float = 0.0,
               max_lines: int = 0) -> Layout:
        key = (text, game_font, round(size, 2), round(width, 2), wrap, round(spacing, 2), max_lines)
        cached = self._layouts.get(key)
        if cached:
            return cached
        face = self.face(game_font, size)
        lines: list[Line] = []
        truncated = False
        if text != "":
            for para in parse(text):
                if wrap and width > 0:
                    lines.extend(self._wrap(face, para, width, size))
                else:
                    line = Line(list(para), sum(self._piece_width(face, p, size) for p in para))
                    if width > 0 and line.width > width + 0.5:
                        line = self._cut(face, line, width, size)
                        truncated = True
                    lines.append(line)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._cut(face, lines[-1], width if width > 0 else lines[-1].width, size, force=True)
            truncated = True
        n = len(lines)
        height = n * size + max(0, n - 1) * spacing
        result = Layout(lines, max((ln.width for ln in lines), default=0.0), height, truncated, size)
        if len(self._layouts) > 20000:
            self._layouts.clear()
        self._layouts[key] = result
        return result

    def _wrap(self, face, para: list[Piece], width: float, size: float) -> list[Line]:
        # words and spaces, each keeping its colour
        tokens: list[Piece] = []
        for p in para:
            if p.icon:
                tokens.append(p)
                continue
            for part in re.findall(r" +|[^ ]+", p.text):
                tokens.append(Piece(part, p.color))
        lines = [Line()]

        def push(tok: Piece, w: float) -> None:
            ln = lines[-1]
            if ln.pieces and ln.pieces[-1].color == tok.color and not ln.pieces[-1].icon and not tok.icon:
                ln.pieces[-1] = Piece(ln.pieces[-1].text + tok.text, tok.color)
            else:
                ln.pieces.append(tok)
            ln.width += w

        for tok in tokens:
            w = self._piece_width(face, tok, size)
            ln = lines[-1]
            space = not tok.icon and tok.text.startswith(" ")
            if space:
                if ln.pieces or len(lines) == 1:  # spaces at a wrap are dropped, at the start of the text kept
                    push(tok, w)
                continue
            if ln.width + w > width + 0.5 and ln.pieces:
                self._rstrip(face, ln)
                lines.append(Line())
                ln = lines[-1]
            if w > width + 0.5 and not tok.icon:  # a word longer than the line is broken between letters
                part = ""
                for ch in tok.text:
                    cw = self.width(face, part + ch)
                    if part and lines[-1].width + cw > width + 0.5:
                        push(Piece(part, tok.color), self.width(face, part))
                        lines.append(Line())
                        part = ch
                    else:
                        part += ch
                if part:
                    push(Piece(part, tok.color), self.width(face, part))
                continue
            push(tok, w)
        for ln in lines:
            self._rstrip(face, ln)
        return lines

    def _rstrip(self, face, ln: Line) -> None:
        while ln.pieces and not ln.pieces[-1].icon and ln.pieces[-1].text.endswith(" "):
            last = ln.pieces[-1]
            stripped = last.text.rstrip(" ")
            ln.width -= self.width(face, last.text) - self.width(face, stripped)
            if stripped:
                ln.pieces[-1] = Piece(stripped, last.color)
            else:
                ln.pieces.pop()

    def _cut(self, face, line: Line, width: float, size: float, force: bool = False) -> Line:
        """The line shortened to fit ``width`` with "..." at its end."""
        room = width - self.width(face, ELLIPSIS)
        out, used = Line(), 0.0
        for p in line.pieces:
            w = self._piece_width(face, p, size)
            if used + w <= room:
                out.pieces.append(p)
                used += w
                continue
            if not p.icon:
                part = ""
                for ch in p.text:
                    if used + self.width(face, part + ch) > room:
                        break
                    part += ch
                if part:
                    out.pieces.append(Piece(part, p.color))
                    used += self.width(face, part)
            break
        else:
            if not force:
                return line
        color = out.pieces[-1].color if out.pieces else None
        out.pieces.append(Piece(ELLIPSIS, color))
        out.width = used + self.width(face, ELLIPSIS)
        return out

    def describe(self) -> dict[str, str]:
        return {k: (f"{c.family} ({c.path})" if c.path else c.family) for k, c in self.fonts.items()}
