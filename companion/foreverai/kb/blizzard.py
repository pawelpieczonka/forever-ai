"""Daily sync of official Blizzard WoW Forever sources: news articles (incl. hotfixes) and dev-notes forum thread."""

from __future__ import annotations

import json
import re
import time

import httpx

from .store import KB
from .text import html_to_text, md5_text

USER_AGENT = "ForeverAI/0.3 (WoW add-on companion)"
NEWS_LISTINGS = [
    "https://worldofwarcraft.blizzard.com/en-us/news",
    "https://news.blizzard.com/en-us/world-of-warcraft",
]
MAX_NEWS_PAGES = 20  # older listing pages (?page=N) are read until one has no Forever article
NEWS_BASE = "https://worldofwarcraft.blizzard.com/en-us/news/"
FORUM_THREADS = ["https://us.forums.blizzard.com/en/wow/t/2360696"]  # WoW Forever development notes


def _article_ids(listing_html: str) -> set[str]:
    ids = set()
    for m in re.finditer(r'href="[^"]*/(?:news|article)/(\d+)/([^"]+)"', listing_html):
        if "forever" in m.group(2).lower():
            ids.add(m.group(1))
    return ids


def parse_article(page_html: str) -> dict | None:
    ld = None
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page_html, re.S):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "NewsArticle":
            ld = obj
            break
    i = page_html.find('class="detail"')
    if i < 0 or ld is None:
        return None
    start = page_html.find(">", i) + 1
    end = page_html.find("</div>", start)
    text = html_to_text(page_html[start:end if end > 0 else None])
    return {"title": ld.get("headline", ""), "published": ld.get("datePublished", ""),
            "modified": ld.get("dateModified", ld.get("datePublished", "")), "text": text}


def sync_blizzard(kb: KB, transport: httpx.BaseTransport | None = None, min_interval: float = 1.0,
                  progress=lambda s: None) -> dict:
    started = time.time()
    report = {"total": 0, "new": 0, "changed": 0, "unchanged": 0, "deleted": 0, "bytes": 0, "errors": []}
    http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=45, follow_redirects=True, transport=transport)
    last = [0.0]

    def get(url, headers=None):
        wait = min_interval - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        r = http.get(url, headers=headers or {})
        report["bytes"] += len(r.content)
        return r

    try:
        # Forever articles found on the news pages (all pages back to the first one without any), plus the ones
        # already in the knowledge base (kept up to date)
        ids = set(kb.all_page_ids("blizzard"))
        for listing in NEWS_LISTINGS:
            listed: set[str] = set()
            for page in range(1, MAX_NEWS_PAGES + 1):
                url = listing if page == 1 else f"{listing}?page={page}"
                try:
                    r = get(url)
                except httpx.HTTPError as e:
                    report["errors"].append(f"{url}: {e}")
                    break
                found = _article_ids(r.text) if r.status_code == 200 else set()
                if found <= listed:  # no Forever article, or the site ignores ?page and repeats the first page
                    break
                listed |= found
                progress(f"Blizzard: news page {page}, {len(listed)} Forever articles")
            ids |= listed
        hotfix_dates = []
        progress(f"Blizzard: checking {len(ids)} Forever articles")
        for n, aid in enumerate(sorted(ids)):
            if n and n % 10 == 0:
                progress(f"Blizzard: checked {n} of {len(ids)} articles")
            url = NEWS_BASE + aid
            old = kb.page_meta("blizzard", aid)
            headers = {"If-None-Match": old.etag} if old and old.etag else {}
            try:
                r = get(url, headers)
            except httpx.HTTPError as e:
                report["errors"].append(f"{url}: {e}")
                continue
            report["total"] += 1
            if r.status_code == 304:
                report["unchanged"] += 1
                continue
            if r.status_code != 200:
                report["errors"].append(f"{url}: HTTP {r.status_code}")
                continue
            art = parse_article(r.text)
            if not art:
                report["errors"].append(f"{url}: article not recognized")
                continue
            if "forever" not in (art["title"] + art["text"][:2000]).lower():
                continue
            if "hotfix" in art["title"].lower():
                hotfix_dates.append(art["modified"][:10])
            body = f"{art['title']}\nPublished: {art['published'][:10]}, updated: {art['modified'][:10]}\n\n{art['text']}"
            changed = kb.upsert_page("blizzard", aid, art["title"], url, None, md5_text(body), art["modified"], body,
                                     etag=r.headers.get("etag"))
            if old is None:
                report["new"] += 1
            elif changed:
                report["changed"] += 1
            else:
                report["unchanged"] += 1
        # hotfix date: newest hotfix article in the KB
        for t in kb._db.execute("SELECT ts, title FROM pages WHERE source='blizzard'").fetchall():
            if t[1] and "hotfix" in t[1].lower() and t[0]:
                hotfix_dates.append(t[0][:10])
        if hotfix_dates:
            kb.set_meta("hotfix_date", max(hotfix_dates))
        for thread in FORUM_THREADS:
            try:
                r = get(thread + ".json")
                if r.status_code != 200:
                    report["errors"].append(f"{thread}: HTTP {r.status_code}")
                    continue
                data = r.json()
                title = data.get("title", "Forum thread")
                for p in (data.get("post_stream") or {}).get("posts", []):
                    if p.get("post_number") != 1 and not (p.get("staff") or p.get("admin") or p.get("moderator")):
                        continue
                    pid = f"{data.get('id')}-{p.get('post_number')}"
                    body = f"{title} (post {p.get('post_number')}, {p.get('updated_at', '')[:10]})\n\n" + html_to_text(
                        p.get("cooked", ""))
                    old = kb.page_meta("forum", pid)
                    changed = kb.upsert_page("forum", pid, title, f"{thread}/{p.get('post_number')}", None,
                                             md5_text(body), p.get("updated_at"), body)
                    report["total"] += 1
                    report["new" if old is None else ("changed" if changed else "unchanged")] += 1
            except (httpx.HTTPError, ValueError) as e:
                report["errors"].append(f"{thread}: {e}")
    finally:
        http.close()
    kb.log_sync("blizzard", started, report)
    kb.set_meta("last_sync_blizzard", str(time.time()))
    return report


def needs_sync(kb: KB, source: str, hours: float = 24) -> bool:
    last = kb.meta(f"last_sync_{source}")
    return last is None or time.time() - float(last) > hours * 3600
