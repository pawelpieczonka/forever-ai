import json

import httpx

from foreverai.kb.blizzard import parse_article
from foreverai.kb.store import KB
from foreverai.kb.text import chunk_text, html_to_text, wikitext_to_text
from foreverai.kb.wiki import WikiClient, sync_wiki


def test_upsert_md5_logic(tmp_path):
    kb = KB(tmp_path / "kb.sqlite")
    assert kb.upsert_page("wiki", "1", "Defensive Stance", "u", "10", "aaa", "2026-11-02", "Threat bonus 80%.") is True
    # same revision text again -> no rebuild
    assert kb.upsert_page("wiki", "1", "Defensive Stance", "u", "10", "aaa", "2026-11-02", "Threat bonus 80%.") is False
    # new revision (different sha1) but identical clean text -> MD5 equal -> no rebuild
    assert kb.upsert_page("wiki", "1", "Defensive Stance", "u", "11", "bbb", "2026-11-03", "Threat bonus 80%.") is False
    assert kb.page_meta("wiki", "1").rev_id == "11"
    # changed text -> rebuild
    assert kb.upsert_page("wiki", "1", "Defensive Stance", "u", "12", "ccc", "2026-11-04", "Threat bonus 90%.") is True
    hits = kb.search("defensive stance threat")
    assert hits and "90%" in hits[0].text and hits[0].ts == "2026-11-04"


def test_delete_missing(tmp_path):
    kb = KB(tmp_path / "kb.sqlite")
    kb.upsert_page("wiki", "1", "A", "u", "1", "s", "t", "alpha text")
    kb.upsert_page("wiki", "2", "B", "u", "1", "s", "t", "beta text")
    assert kb.delete_missing("wiki", {"1"}) == 1
    assert kb.search("beta") == []


def test_wikitext_templates_are_kept_as_text():
    wt = "{{Itembox|name=Worn Shortbow|dps=1.5|slot=Ranged}}\n'''Worn Shortbow''' is a [[bow]].\n== Source ==\nSold by [[Ilyenia Moonfire|Ilyenia]].[[Category:Forever items]]"
    text = wikitext_to_text(wt)
    assert "[Itembox: name=Worn Shortbow; dps=1.5; slot=Ranged]" in text
    assert "Worn Shortbow is a bow." in text
    assert "## Source" in text and "Sold by Ilyenia." in text
    assert "Category" not in text


def test_chunking_by_sections():
    chunks = chunk_text("T", "intro\n## A\n" + "x " * 900 + "\n## B\nshort")
    assert [c[0] for c in chunks][0] == ""
    assert any(c[0] == "B" and c[1] == "short" for c in chunks)
    assert all(len(c[1]) <= 1400 for c in chunks)


def test_html_to_text():
    assert html_to_text("<p>Hi</p><ul><li>one</li><li><strong>two</strong></li></ul>") == "Hi\n\n- one\n- two"


def test_parse_article():
    ld = {"@type": "NewsArticle", "headline": "WoW: Forever Hotfixes - November 6, 2026",
          "datePublished": "2026-11-07T01:06:34+00:00", "dateModified": "2026-11-07T01:06:34+00:00"}
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script><div class="detail"><p>Fixed Bloom.</p></div>'
    art = parse_article(html)
    assert art["title"].startswith("WoW: Forever Hotfixes") and art["modified"].startswith("2026-11-07")
    assert "Fixed Bloom." in art["text"]


def test_sync_blizzard_reads_older_news_pages(tmp_path):
    from foreverai.kb.blizzard import sync_blizzard

    def link(aid):
        return f'<a href="/en-us/news/{aid}/wow-forever-article-{aid}">x</a>'

    listing = {1: link(1) + link(2), 2: link(3), 3: '<a href="/en-us/news/9/midnight-news">x</a>'}
    article_requests = []

    def handler(request: httpx.Request):
        url = str(request.url)
        if "/news/" in url:  # an article
            aid = url.rstrip("/").rsplit("/", 1)[1]
            article_requests.append(aid)
            ld = {"@type": "NewsArticle", "headline": f"WoW: Forever article {aid}",
                  "datePublished": "2026-11-01T00:00:00+00:00"}
            return httpx.Response(200, text=f'<script type="application/ld+json">{json.dumps(ld)}</script>'
                                             f'<div class="detail"><p>Forever text {aid}.</p></div>')
        if "forums" in url:
            return httpx.Response(404)
        if "news.blizzard.com" in url:  # ignores ?page and repeats its first page
            return httpx.Response(200, text=link(1))
        return httpx.Response(200, text=listing.get(int(request.url.params.get("page", 1)), ""))

    kb = KB(tmp_path / "kb.sqlite")
    report = sync_blizzard(kb, transport=httpx.MockTransport(handler), min_interval=0)
    assert sorted(article_requests) == ["1", "2", "3"]  # article 3 is only on page 2
    assert report["new"] == 3


def test_sync_wiki_downloads_only_changed(tmp_path):
    state = {"rev": {101: ("1", "s1"), 102: ("5", "s5")}, "content_requests": []}

    def handler(request: httpx.Request):
        p = dict(request.url.params)
        if p.get("list") == "categorymembers":
            if p["cmtitle"] == "Category:Forever":
                members = [{"pageid": 101, "ns": 0, "title": "Barrow Deeps", "type": "page"},
                           {"pageid": 900, "ns": 14, "title": "Category:Forever quests", "type": "subcat"}]
            else:
                members = [{"pageid": 102, "ns": 0, "title": "The Balance of Nature", "type": "page"}]
            return httpx.Response(200, json={"query": {"categorymembers": members}})
        ids = [int(x) for x in p["pageids"].split("|")]
        if "content" in p["rvprop"]:
            state["content_requests"].append(ids)
        pages = []
        for i in ids:
            rev, sha = state["rev"][i]
            r = {"revid": int(rev), "sha1": sha, "timestamp": "2026-11-08T10:00:00Z"}
            if "content" in p["rvprop"]:
                r["slots"] = {"main": {"content": f"Page {i} rev {rev}. Raid unlocks December 9."}}
            pages.append({"pageid": i, "title": "Barrow Deeps" if i == 101 else "The Balance of Nature", "revisions": [r]})
        return httpx.Response(200, json={"query": {"pages": pages}})

    kb = KB(tmp_path / "kb.sqlite")
    client = WikiClient(transport=httpx.MockTransport(handler), min_interval=0)
    r1 = sync_wiki(kb, client)
    assert r1["new"] == 2 and r1["total"] == 2
    state["rev"][102] = ("6", "s6")  # only one page edited
    r2 = sync_wiki(kb, client)
    assert state["content_requests"][-1] == [102]
    assert r2["changed"] == 1 and r2["unchanged"] == 1 and r2["new"] == 0
    assert kb.search("Barrow Deeps raid")[0].url.endswith("Barrow_Deeps")


def test_search_returns_whole_pages_without_empty_sections(tmp_path):
    from foreverai.tools import run_kb_search
    kb = KB(tmp_path / "kb.sqlite")
    body = ("Barrow Deeps\n\nThe first raid of WoW Forever.\n## Release\nOpens on December 9, 2026 for all realms.\n"
            "## Bosses\n- Grave Warden Ulric, the first boss of the raid\n## Patch changes\n-\n## External links\n[Elinks-NPC: 1]")
    kb.upsert_page("wiki", "1", "Barrow Deeps", "https://w/Barrow_Deeps", "1", "s", "2026-11-08", body)
    kb.upsert_page("wiki", "2", "Raid lockouts", "https://w/Raid_lockouts", "1", "s", "2026-11-08",
                   "Raid lockouts\n\nRaids reset every Tuesday; the Barrow Deeps raid follows the same lockout rules.")
    text, sources = run_kb_search(kb, "barrow deeps raid release")
    assert "## Release" in text and "December 9" in text and "Grave Warden Ulric" in text  # the whole page
    assert text.count("[Barrow Deeps |") == 1  # one page once, not one entry per fragment
    assert [s["title"] for s in sources][0] == "Barrow Deeps"


def test_snapshot_text_is_not_cut_inside_a_section():
    from foreverai.prompt import render_snapshot
    snap = {"quests": [{"title": "Webwood Egg", "description": "x" * 5000}], "chat": ["y" * 3000]}
    full = render_snapshot(snap)
    assert "x" * 5000 in full and "left out" not in full
    small = render_snapshot(snap, max_chars=5300)
    assert "x" * 5000 in small and "y" * 3000 not in small and "(left out because of size: chat)" in small
