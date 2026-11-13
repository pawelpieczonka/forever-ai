"""Inbound channel: a pool of LoadOnDemand addons ("inboxes").

Facts verified in the game client (phase 0):
  * the game indexes addon files when the UI loads (login or /reload); files created later are invisible,
    so the pool has to exist before the UI loads;
  * the content of an indexed LoD addon file is read from disk at ``C_AddOns.LoadAddOn`` time, so the companion
    can rewrite ``data.lua`` at any moment; each inbox can be loaded only once per UI session.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
from pathlib import Path

POOL_PREFIX = "ForeverAI_Inbox"
LEGACY_DATA_ADDON = "ForeverAI_Data"
DEFAULT_POOL = 400


def inbox_name(n: int) -> str:
    return f"{POOL_PREFIX}{n:03d}"


def interface_version(addons_dir: Path) -> str:
    """The game's interface number, as the ForeverAI add-on declares it (installed copy first, then the bundled one),
    so the helper add-ons never show up as out of date when the add-on is updated for a new client."""
    from .setup_game import addon_source
    for toc in (Path(addons_dir) / "ForeverAI" / "ForeverAI.toc", addon_source() / "ForeverAI.toc"):
        try:
            m = re.search(r"^## Interface:\s*([\d, ]+)$", toc.read_text(encoding="utf-8-sig"), re.M)
        except OSError:
            continue
        if m:
            return m.group(1).strip()
    raise FileNotFoundError("ForeverAI.toc not found (installed or bundled)")


def _toc(n: int, interface: str) -> str:
    return (f"## Interface: {interface}\n"
            f"## Title: |cff808080ForeverAI data {n:03d}|r\n"
            "## Notes: Part of ForeverAI (answer channel). Keep enabled.\n"
            "## Group: ForeverAI\n"
            "## LoadOnDemand: 1\n"
            "## Dependencies: ForeverAI\n\n"
            "data.lua\n")


def ensure_pool(addons_dir: Path, size: int = DEFAULT_POOL) -> int:
    """Create missing inbox addons (and refresh the description of existing ones). Returns the number created."""
    addons_dir = Path(addons_dir)
    interface = interface_version(addons_dir)
    created = 0
    for n in range(1, size + 1):
        name = inbox_name(n)
        d = addons_dir / name
        toc = d / f"{name}.toc"
        text = _toc(n, interface)
        if not toc.exists():
            d.mkdir(parents=True, exist_ok=True)
            toc.write_text(text, encoding="utf-8")
            (d / "data.lua").write_text("-- empty\n", encoding="ascii")
            created += 1
        elif toc.read_text(encoding="utf-8", errors="replace") != text:
            toc.write_text(text, encoding="utf-8")
    legacy = addons_dir / LEGACY_DATA_ADDON
    if legacy.is_dir():
        shutil.rmtree(legacy, ignore_errors=True)
    return created


def pool_size(addons_dir: Path) -> int:
    n = 0
    while (Path(addons_dir) / inbox_name(n + 1) / f"{inbox_name(n + 1)}.toc").exists():
        n += 1
    return n


def encode_inbox(payload: dict) -> str:
    """A payload as an inbox holds it (JSON, base64): the same text for every inbox it is written to."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def write_inbox(addons_dir: Path, n: int, payload: dict, b64: str | None = None) -> None:
    """Atomically replace inbox ``n`` content with ``payload`` (JSON, base64 inside a Lua call). ``b64``: the payload
    already encoded (encode_inbox), when it goes to many inboxes."""
    if b64 is None:
        b64 = encode_inbox(payload)
    path = Path(addons_dir) / inbox_name(n) / "data.lua"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f'ForeverAI_Receive({n}, "{b64}")\n', encoding="ascii")
    # Windows refuses to replace a file that is open (e.g. the game is reading it right now) -> retry briefly
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.025)
    os.replace(tmp, path)
