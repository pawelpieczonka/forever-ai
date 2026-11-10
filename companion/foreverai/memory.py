"""Long-term memory about the player (per character) and persistent conversations."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

MAX_ITEMS = 40
MAX_LEN = 700
MAX_TURNS = 30


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class MemoryStore:
    """Facts the AI keeps about a character, e.g. goals and preferences. Cleared/edited from the game."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}

    def _merge_same_name(self, char: str) -> None:
        """The realm part of "Name-Realm" is not stable in this client (it has reported different realm names for the
        same character), so entries saved under the same character name are folded into the current key."""
        name = (char or "?").split("-", 1)[0]
        others = [k for k in self._data if k != char and k.split("-", 1)[0] == name]
        if not others:
            return
        items = self._data.setdefault(char, [])
        for k in others:
            for it in self._data.pop(k):
                if any(i["text"].lower() == it["text"].lower() for i in items):
                    continue
                items.append(dict(it, id=max([i["id"] for i in items] or [0]) + 1))
        items.sort(key=lambda i: (i.get("at", ""), i["id"]))
        for n, it in enumerate(items, 1):
            it["id"] = n
        del items[:-MAX_ITEMS]
        _atomic_write(self.path, self._data)

    def get(self, char: str) -> list[dict]:
        with self._lock:
            self._merge_same_name(char or "?")
            return list(self._data.get(char or "?", []))

    def add(self, char: str, text: str) -> dict | None:
        text = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_LEN]
        if not text:
            return None
        with self._lock:
            self._merge_same_name(char or "?")
            items = self._data.setdefault(char or "?", [])
            if any(i["text"].lower() == text.lower() for i in items):
                return None
            next_id = max([i["id"] for i in items] or [0]) + 1
            item = {"id": next_id, "text": text, "at": datetime.now().strftime("%Y-%m-%d")}
            items.append(item)
            del items[:-MAX_ITEMS]
            _atomic_write(self.path, self._data)
            return item

    def delete(self, char: str, item_id: int) -> bool:
        with self._lock:
            items = self._data.get(char or "?", [])
            before = len(items)
            items[:] = [i for i in items if i["id"] != int(item_id)]
            _atomic_write(self.path, self._data)
            return len(items) != before

    def clear(self, char: str) -> None:
        with self._lock:
            self._data[char or "?"] = []
            _atomic_write(self.path, self._data)


class ConversationStore:
    """Conversations persisted on disk, so they can be continued after a restart or reopened from the game."""

    def __init__(self, folder: Path, max_age_days: int = 120):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        limit = time.time() - max_age_days * 86400
        for p in self.folder.glob("*.json"):
            try:
                if p.stat().st_mtime < limit:
                    p.unlink()
            except OSError:
                pass

    @staticmethod
    def _safe(conv: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", str(conv or "default"))[:60]

    def _load(self, conv: str) -> dict:
        key = self._safe(conv)
        if key not in self._cache:
            p = self.folder / f"{key}.json"
            data = None
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
            self._cache[key] = data or {"id": key, "turns": []}
        return self._cache[key]

    def history(self, conv: str) -> list[dict]:
        return list(self._load(conv)["turns"])

    def append(self, conv: str, char: str, question: str, answer: str, did: list[str] | None = None) -> None:
        """``did``: what the answer did besides its text (map marks, proposals, memory), for the following turns."""
        with self._lock:
            data = self._load(conv)
            if not data.get("title"):
                data["title"] = question[:80]
                data["created"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["char"] = char
            data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            data["turns"].append({"q": question, "a": answer, **({"did": list(did)} if did else {})})
            del data["turns"][:-MAX_TURNS]
            _atomic_write(self.folder / f"{self._safe(conv)}.json", data)

    def delete(self, conv: str) -> None:
        with self._lock:
            key = self._safe(conv)
            self._cache.pop(key, None)
            try:
                (self.folder / f"{key}.json").unlink()
            except OSError:
                pass
