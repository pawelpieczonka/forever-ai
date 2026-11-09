"""Reads installed addon TOC files to learn their SavedVariables names (the in-game API does not expose them)."""

from __future__ import annotations

import re
from pathlib import Path

FLAVOR_SUFFIXES = ("_Forever", "_Camelot", "-Camelot", "_Vanilla", "")


def _pick_toc(folder: Path) -> Path | None:
    name = folder.name
    for suf in FLAVOR_SUFFIXES:
        p = folder / f"{name}{suf}.toc"
        if p.exists():
            return p
    tocs = sorted(folder.glob("*.toc"))
    return tocs[0] if tocs else None


def saved_variables(addons_dir: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    addons_dir = Path(addons_dir)
    if not addons_dir.exists():
        return out
    for folder in addons_dir.iterdir():
        if not folder.is_dir() or folder.name.startswith("ForeverAI_Inbox") or folder.name == "ForeverAI_Data":
            continue
        toc = _pick_toc(folder)
        if not toc:
            continue
        try:
            text = toc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names: list[str] = []
        for m in re.finditer(r"^##\s*SavedVariables(?:PerCharacter)?\s*:\s*(.+)$", text, re.M):
            names += [n.strip() for n in m.group(1).split(",") if n.strip()]
        if names:
            out[folder.name] = names
    return out
