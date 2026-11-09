"""Run knowledge-base syncs (CLI: python -m foreverai.kb.sync [--force])."""

from __future__ import annotations

import logging
import sys
import time

from ..config import data_dir
from .blizzard import needs_sync, sync_blizzard
from .store import KB
from .wiki import sync_wiki

log = logging.getLogger("foreverai")


def kb_path():
    return data_dir() / "kb.sqlite"


def run_due(kb: KB, force: bool = False, progress=print) -> dict:
    """Update the sources that are due (or all with ``force``); ``progress`` gets short status lines."""
    out = {}
    if force or needs_sync(kb, "wiki"):
        progress("Knowledge base: checking warcraft.wiki.gg")
        out["wiki"] = sync_wiki(kb, progress=progress)
        log.info("wiki sync: %s", out["wiki"])
    if force or needs_sync(kb, "blizzard"):
        progress("Knowledge base: checking Blizzard news")
        out["blizzard"] = sync_blizzard(kb, progress=progress)
        log.info("blizzard sync: %s", out["blizzard"])
    return out


if __name__ == "__main__":
    t0 = time.time()
    kb = KB(kb_path())
    res = run_due(kb, force="--force" in sys.argv)
    print(f"pages: {kb.count_pages()}  kb_date: {kb.meta('kb_date')}  hotfix: {kb.meta('hotfix_date')}  "
          f"time: {time.time() - t0:.0f}s")
    for row in kb.last_syncs(4):
        print(row[:8])
