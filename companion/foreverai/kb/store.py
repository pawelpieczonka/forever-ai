"""SQLite storage with FTS5 full-text search for the knowledge base."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .text import chunk_text, md5_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  source TEXT NOT NULL, page_id TEXT NOT NULL, title TEXT, url TEXT,
  rev_id TEXT, sha1 TEXT, ts TEXT, md5 TEXT, etag TEXT, fetched_at REAL,
  PRIMARY KEY (source, page_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
  title, section, body, source UNINDEXED, page_id UNINDEXED, url UNINDEXED, ts UNINDEXED,
  tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, started REAL, finished REAL,
  total INTEGER, new INTEGER, changed INTEGER, unchanged INTEGER, deleted INTEGER,
  bytes INTEGER, errors TEXT
);
"""

_STOP = {"the", "a", "an", "of", "in", "on", "and", "or", "to", "is", "for", "what", "how", "wow", "forever",
         "world", "warcraft", "with", "does", "do", "are", "be"}


@dataclass
class Hit:
    title: str
    section: str
    url: str
    ts: str
    source: str
    text: str


@dataclass
class PageMeta:
    rev_id: str | None
    sha1: str | None
    md5: str | None
    etag: str | None
    ts: str | None


class KB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True) if self.path != ":memory:" else None
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- pages
    def page_meta(self, source: str, page_id: str) -> PageMeta | None:
        with self._lock:
            row = self._db.execute("SELECT rev_id, sha1, md5, etag, ts FROM pages WHERE source=? AND page_id=?",
                                   (source, str(page_id))).fetchone()
        return PageMeta(*row) if row else None

    def all_page_ids(self, source: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT page_id FROM pages WHERE source=?", (source,))}

    def upsert_page(self, source: str, page_id: str, title: str, url: str, rev_id: str | None, sha1: str | None,
                    ts: str | None, text: str, etag: str | None = None) -> bool:
        """Store a page. Returns True when the clean text changed (chunks rebuilt), False otherwise."""
        page_id = str(page_id)
        digest = md5_text(text)
        with self._lock:
            old = self.page_meta(source, page_id)
            self._db.execute(
                "INSERT INTO pages(source,page_id,title,url,rev_id,sha1,ts,md5,etag,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source,page_id) DO UPDATE SET title=excluded.title, url=excluded.url, rev_id=excluded.rev_id, "
                "sha1=excluded.sha1, ts=excluded.ts, md5=excluded.md5, etag=excluded.etag, fetched_at=excluded.fetched_at",
                (source, page_id, title, url, rev_id, sha1, ts, digest, etag, time.time()))
            if old and old.md5 == digest:
                self._db.commit()
                return False
            self._db.execute("DELETE FROM chunks WHERE source=? AND page_id=?", (source, page_id))
            for section, body in chunk_text(title, text):
                self._db.execute("INSERT INTO chunks(title,section,body,source,page_id,url,ts) VALUES(?,?,?,?,?,?,?)",
                                 (title, section, body, source, page_id, url, ts or ""))
            self._db.commit()
            return True

    def touch_page(self, source: str, page_id: str, rev_id: str | None, sha1: str | None, ts: str | None,
                   etag: str | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE pages SET rev_id=?, sha1=?, ts=COALESCE(?, ts), etag=COALESCE(?, etag), fetched_at=? "
                             "WHERE source=? AND page_id=?", (rev_id, sha1, ts, etag, time.time(), source, str(page_id)))
            self._db.execute("UPDATE chunks SET ts=? WHERE source=? AND page_id=? AND ? IS NOT NULL",
                             (ts, source, str(page_id), ts))
            self._db.commit()

    def delete_missing(self, source: str, keep_ids: set[str]) -> int:
        gone = self.all_page_ids(source) - {str(k) for k in keep_ids}
        with self._lock:
            for pid in gone:
                self._db.execute("DELETE FROM pages WHERE source=? AND page_id=?", (source, pid))
                self._db.execute("DELETE FROM chunks WHERE source=? AND page_id=?", (source, pid))
            self._db.commit()
        return len(gone)

    def count_pages(self, source: str | None = None) -> int:
        with self._lock:
            if source:
                return self._db.execute("SELECT COUNT(*) FROM pages WHERE source=?", (source,)).fetchone()[0]
            return self._db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    # ---------------------------------------------------------------- search
    @staticmethod
    def _fts_query(query: str) -> str:
        words = [w for w in re.findall(r"[\w']+", query.lower()) if w not in _STOP and len(w) > 1]
        if not words:
            return ""
        return " OR ".join('"' + w.replace('"', "") + '"' for w in words[:12])

    def search(self, query: str, k: int = 6, max_chars: int = 1400) -> list[Hit]:
        q = self._fts_query(query)
        if not q:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT title, section, url, ts, source, body FROM chunks WHERE chunks MATCH ? "
                "ORDER BY bm25(chunks, 4.0, 2.0, 1.0) LIMIT ?", (q, k)).fetchall()
        return [Hit(r[0], r[1], r[2], r[3], r[4], r[5][:max_chars]) for r in rows]

    def page_text(self, url: str) -> str:
        """The whole stored text of a page (all its chunks in order, with section headings)."""
        with self._lock:
            rows = self._db.execute("SELECT section, body FROM chunks WHERE url = ? ORDER BY rowid", (url,)).fetchall()
        out, current = [], None
        for section, body in rows:
            if section and section != current:
                out.append(f"## {section}")
            current = section
            out.append(body)
        return "\n\n".join(out)

    def search_titles(self, names: list[str], k: int = 4) -> list[Hit]:
        """Exact-ish title matches for entity names coming from the game (items, quests, NPCs)."""
        hits: list[Hit] = []
        with self._lock:
            for name in names:
                if not name:
                    continue
                rows = self._db.execute(
                    "SELECT title, section, url, ts, source, body FROM chunks WHERE title = ? LIMIT 2", (name,)).fetchall()
                hits += [Hit(r[0], r[1], r[2], r[3], r[4], r[5][:1400]) for r in rows]
                if len(hits) >= k:
                    break
        return hits[:k]

    # ---------------------------------------------------------------- entities (for clickable links in the game)
    _ELINK = re.compile(r"\[Elinks-(item|spell|quest)\d*: (\d+)")

    def entity(self, name: str) -> tuple[str, int] | None:
        """("item"|"spell"|"quest", id) for a wiki page titled ``name`` (optionally with a "(...)" suffix)."""
        name = (name or "").strip()
        if not name or len(name) > 80:
            return None
        like = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + " (%"
        with self._lock:
            rows = self._db.execute(
                "SELECT title, body FROM chunks WHERE (title = ? OR title LIKE ? ESCAPE '\\') "
                "AND body LIKE '%[Elinks-%' LIMIT 6", (name, like)).fetchall()
        for title, body in sorted(rows, key=lambda r: r[0] != name):
            m = self._ELINK.search(body)
            if m:
                return m.group(1), int(m.group(2))
        return None

    # ---------------------------------------------------------------- meta / log
    def meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, value))
            self._db.commit()

    def log_sync(self, source: str, started: float, report: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO sync_log(source,started,finished,total,new,changed,unchanged,deleted,bytes,errors) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (source, started, time.time(), report.get("total", 0), report.get("new", 0), report.get("changed", 0),
                 report.get("unchanged", 0), report.get("deleted", 0), report.get("bytes", 0),
                 "\n".join(report.get("errors", []))[:4000]))
            self._db.commit()

    def last_syncs(self, limit: int = 10) -> list[tuple]:
        with self._lock:
            return self._db.execute(
                "SELECT source, datetime(started,'unixepoch','localtime'), total, new, changed, unchanged, deleted, bytes, "
                "errors FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
