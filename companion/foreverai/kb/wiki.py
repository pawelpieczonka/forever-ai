"""Incremental daily sync of WoW Forever pages from warcraft.wiki.gg (MediaWiki API).

1. list pages of Category:Forever and its subcategories (ns 0) - titles/ids only,
2. fetch revision metadata (rev id + SHA-1 computed by the wiki) without content,
3. download content only for new pages or pages whose rev id / SHA-1 changed,
4. convert to clean text and compare its MD5 - unchanged text does not rebuild the index,
5. drop pages that left the category.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

import httpx

from .store import KB
from .text import wikitext_to_text

API = "https://warcraft.wiki.gg/api.php"
SITE = "https://warcraft.wiki.gg/wiki/"
ROOT_CATEGORY = "Category:Forever"
USER_AGENT = "ForeverAI/0.3 (WoW add-on companion)"
SOURCE = "wiki"


class WikiClient:
    def __init__(self, transport: httpx.BaseTransport | None = None, min_interval: float = 1.0):
        self._http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, transport=transport)
        self._min_interval = min_interval
        self._last = 0.0
        self.bytes = 0

    def query(self, **params) -> dict:
        params = {"action": "query", "format": "json", "formatversion": "2", "maxlag": "5", **params}
        for attempt in range(5):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self._http.get(API, params=params)
            self.bytes += len(r.content)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("error", {}).get("code") == "maxlag":
                time.sleep(5 * (attempt + 1))
                continue
            return data
        raise RuntimeError("wiki API unavailable")

    def close(self):
        self._http.close()


def _batched(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def list_pages(client: WikiClient, root: str = ROOT_CATEGORY, max_categories: int = 200,
               say: Callable[[str], None] = lambda s: None) -> dict[int, str]:
    """Return {page_id: title} for all ns0 pages under ``root`` (recursive)."""
    pages: dict[int, str] = {}
    seen_cats = {root}
    queue = [root]
    done = 0
    while queue and len(seen_cats) <= max_categories:
        cat = queue.pop(0)
        done += 1
        if done % 10 == 0:
            say(f"Wiki: reading categories ({done} of {len(seen_cats)}, {len(pages)} pages so far)")
        cont: dict = {}
        while True:
            data = client.query(list="categorymembers", cmtitle=cat, cmprop="ids|title|type", cmlimit="500",
                                cmtype="page|subcat", **cont)
            for m in data.get("query", {}).get("categorymembers", []):
                if m.get("type") == "subcat" or m.get("ns") == 14:
                    if m["title"] not in seen_cats:
                        seen_cats.add(m["title"])
                        queue.append(m["title"])
                elif m.get("ns") == 0:
                    pages[int(m["pageid"])] = m["title"]
            if "continue" in data:
                cont = {k: v for k, v in data["continue"].items()}
            else:
                break
    return pages


def revision_meta(client: WikiClient, page_ids: list[int],
                  say: Callable[[str], None] = lambda s: None) -> dict[int, tuple[str, str, str]]:
    """{page_id: (rev_id, sha1, timestamp)} without downloading content."""
    out = {}
    for n, batch in enumerate(_batched(page_ids, 50)):
        if n and n % 8 == 0:
            say(f"Wiki: checked {n * 50} of {len(page_ids)} pages for changes")
        data = client.query(prop="revisions", rvprop="ids|timestamp|sha1", pageids="|".join(map(str, batch)))
        for p in data.get("query", {}).get("pages", []):
            revs = p.get("revisions") or []
            if revs:
                r = revs[0]
                out[int(p["pageid"])] = (str(r.get("revid")), r.get("sha1") or "", r.get("timestamp") or "")
    return out


def fetch_content(client: WikiClient, page_ids: list[int],
                  say: Callable[[str], None] = lambda s: None) -> dict[int, tuple[str, str, str, str, str]]:
    """{page_id: (title, wikitext, rev_id, sha1, ts)}"""
    out = {}
    for n, batch in enumerate(_batched(page_ids, 50)):
        if n:
            say(f"Wiki: downloaded {n * 50} of {len(page_ids)} pages")
        data = client.query(prop="revisions", rvprop="ids|timestamp|sha1|content", rvslots="main",
                            pageids="|".join(map(str, batch)))
        for p in data.get("query", {}).get("pages", []):
            revs = p.get("revisions") or []
            if not revs:
                continue
            r = revs[0]
            content = ((r.get("slots") or {}).get("main") or {}).get("content", "")
            out[int(p["pageid"])] = (p["title"], content, str(r.get("revid")), r.get("sha1") or "",
                                     r.get("timestamp") or "")
    return out


def sync_wiki(kb: KB, client: WikiClient | None = None, progress: Callable[[str], None] | None = None) -> dict:
    own = client is None
    client = client or WikiClient()
    started = time.time()
    report = {"total": 0, "new": 0, "changed": 0, "unchanged": 0, "deleted": 0, "bytes": 0, "errors": []}
    say = progress or (lambda s: None)
    try:
        pages = list_pages(client, say=say)
        report["total"] = len(pages)
        say(f"Wiki: {len(pages)} Forever pages, checking for changes")
        meta = revision_meta(client, list(pages), say=say)
        to_fetch, new_ids = [], set()
        for pid, (rev, sha1, ts) in meta.items():
            old = kb.page_meta(SOURCE, str(pid))
            if old is None:
                new_ids.add(pid)
                to_fetch.append(pid)
            elif old.rev_id != rev or old.sha1 != sha1:
                to_fetch.append(pid)
            else:
                report["unchanged"] += 1
        say(f"Wiki: {len(to_fetch)} pages to download ({len(new_ids)} new)" if to_fetch else "Wiki: no changes")
        for pid, (title, wikitext, rev, sha1, ts) in fetch_content(client, to_fetch, say=say).items():
            try:
                text = wikitext_to_text(wikitext)
            except Exception as e:  # malformed wikitext must not stop the sync
                report["errors"].append(f"{title}: {e}")
                continue
            url = SITE + title.replace(" ", "_")
            changed = kb.upsert_page(SOURCE, str(pid), title, url, rev, sha1, ts, f"{title}\n\n{text}")
            if pid in new_ids:
                report["new"] += 1
            elif changed:
                report["changed"] += 1
            else:
                report["unchanged"] += 1  # new revision, same clean text (MD5) -> index untouched
        report["deleted"] = kb.delete_missing(SOURCE, {str(p) for p in meta})
    except Exception as e:
        report["errors"].append(f"sync error: {e}")
    finally:
        report["bytes"] = client.bytes
        if own:
            client.close()
    kb.log_sync(SOURCE, started, report)
    if not any(err.startswith("sync error") for err in report["errors"]):
        kb.set_meta("last_sync_wiki", str(time.time()))
        kb.set_meta("kb_date", time.strftime("%Y-%m-%d %H:%M"))
    return report
