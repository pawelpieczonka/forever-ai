"""The last answered questions, each with everything the model got (question, game data, earlier turns, memory) and what
came back (answer, tool calls, time). They stay on this computer; ``evals/replay.py`` runs them again through the
current prompt, so a prompt change can be compared with what the player actually got."""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("foreverai")


class TurnLog:
    def __init__(self, folder: Path | str, keep: int = 200):
        self.folder = Path(folder)
        self.keep = keep
        self._lock = threading.Lock()
        self._n = itertools.count()

    def save(self, record: dict) -> Path:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / f"{int(time.time() * 1000):013d}-{next(self._n)}.json"
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            for old in sorted(self.folder.glob("*.json"))[:-self.keep]:
                old.unlink(missing_ok=True)
            return path

    def load(self) -> list[dict]:
        """Oldest first; a file that cannot be read is skipped."""
        out = []
        for p in sorted(self.folder.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                log.warning("unreadable turn record %s", p.name)
        return out
