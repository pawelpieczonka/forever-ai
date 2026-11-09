import json
import os

from PIL import Image

from foreverai import web
from foreverai.frames import decode_frame, encode_frames
from foreverai.watcher import ScreenshotWatcher


def test_blocked_detection():
    assert web._blocked(403, "")
    assert web._blocked(200, "<html><title>Just a moment...</title>")
    assert not web._blocked(200, "<html><h1>Guide</h1></html>")


def test_page_text_drops_navigation_and_keeps_content():
    html = ("<html><head><title>Hunter Guide - Site</title></head><body><nav><a>LoL</a><a>TFT</a></nav>"
            "<div>LoL</div><div>TFT</div><div>D4</div><div>PoE</div><div>WoW</div><div>New</div><div>VAL</div>"
            "<h2>Overview</h2><p>Hunter is the best solo class.</p><h2>Talents</h2><p>Take Endurance Training.</p>"
            "</body></html>")
    out = web.page_text(html, "https://x/guide")
    assert out.startswith("Title: Hunter Guide - Site")
    assert "## Overview" in out and "Endurance Training" in out
    assert "TFT" not in out


def test_embedded_state_for_js_pages():
    state = {"doc": {"blocks": [{"text": "Level 10:"}, {"label": "Endurance Training"}, {"title": "Pets"},
                                {"id": "a" * 30}, {"url": "https://cdn/x.webp"}]}}
    html = f"<html><title>T</title><body><div id=app></div><script>window.__PRELOADED_STATE__={json.dumps(state)};</script></body></html>"
    out = web.page_text(html, "https://x")
    assert "Level 10: Endurance Training Pets" in out
    assert "cdn" not in out


def test_wowhead_markup():
    markup = "[h2 type=bar]Release Date[/h2]\r\n[table][tr][td]Raids[/td][td]December 9th[/td][/tr][/table] [item=1234]"
    html = f'<html><title>W</title><script>WH.markup.printHtml({json.dumps(markup + " " * 200)}, "guide-body");</script></html>'
    out = web.page_text(html, "https://wowhead")
    assert "### Release Date" in out and "Raids | December 9th" in out and "[item 1234]" in out


def test_fetch_rejects_bad_urls():
    assert web.fetch("ftp://x").startswith("Rejected")
    assert web.fetch("not-a-url").startswith("Rejected")
    for local in ("http://127.0.0.1:47811/", "http://localhost/admin", "http://192.168.1.1/", "http://10.0.0.5/x"):
        assert web.fetch(local).startswith("Rejected"), local


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(web, "_get", lambda url: (200, "<h2>x</h2><p>y</p>", "text/html", url))
    monkeypatch.setattr(web, "_public_host", lambda url: True)
    web._CACHE.clear()
    for i in range(50):
        web.fetch(f"https://site.example/{i}")
    assert len(web._CACHE) <= web._CACHE_MAX


def test_fetch_uses_impersonation_and_cache(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        return 200, "<html><title>G</title><h2>Build</h2><p>BM hunter</p></html>", "text/html", url

    monkeypatch.setattr(web, "_get", fake_get)
    web._CACHE.clear()
    a = web.fetch("https://mobalytics.gg/g")
    b = web.fetch("https://mobalytics.gg/g")
    assert "BM hunter" in a and a == b and len(calls) == 1


def test_fetch_reports_block(monkeypatch):
    monkeypatch.setattr(web, "_get", lambda url: (403, "Just a moment...", "text/html", url))
    web._CACHE.clear()
    assert "blocks automated access" in web.fetch("https://blocked.example/x")


def test_watcher_decodes_tga_and_png(tmp_path):
    frames = []
    w = ScreenshotWatcher(tmp_path, frames.append)
    data = os.urandom(5000)
    for ext in ("tga", "png"):
        screen = Image.new("RGB", (1920, 1080), (10, 40, 10))
        screen.paste(encode_frames(data, msg_id=5)[0], (0, 0))
        p = tmp_path / f"WoWScrnShot_1.{ext}"
        screen.save(p)
        assert w._try_decode(p) is True
        assert not p.exists()
    assert len(frames) == 2 and all(f.payload == data for f in frames)
    # the screen that carried the question is kept (downscaled JPEG, data block blacked out) for the AI
    import io
    jpeg = w.screen_for(5)
    assert jpeg and jpeg[:2] == b"\xff\xd8"
    shot = Image.open(io.BytesIO(jpeg))
    assert shot.width == 1280 and sum(shot.getpixel((20, 10))) < 30  # JPEG: near-black
    assert sum(shot.getpixel((900, 500))) > 30  # the rest of the screen is intact
    # the screenshot carrying a message is kept (small JPEG, data block blacked out) to show the AI the screen
    import io
    jpeg = w.screen_for(5)
    shot = Image.open(io.BytesIO(jpeg))
    assert shot.format == "JPEG" and shot.width == 1280
    assert max(shot.getpixel((10, 10))) < 20 and shot.getpixel((900, 600))[1] > 25
    plain = tmp_path / "WoWScrnShot_2.tga"
    Image.new("RGB", (800, 600), (5, 5, 5)).save(plain)
    assert w._try_decode(plain) is False and plain.exists()  # player's own screenshots are left alone


def test_old_data_screenshots_are_removed_on_start(tmp_path):
    data = os.urandom(3000)
    frames = []
    old = tmp_path / "WoWScrnShot_old.tga"
    screen = Image.new("RGB", (800, 600), (40, 40, 40))
    screen.paste(encode_frames(data, msg_id=3)[0], (0, 0))
    screen.save(old)
    mine = tmp_path / "WoWScrnShot_mine.tga"
    Image.new("RGB", (800, 600), (90, 120, 60)).save(mine)
    w = ScreenshotWatcher(tmp_path, frames.append)
    assert w.cleanup_old_frames() == 1
    assert not old.exists() and mine.exists() and frames == []  # not answered, the player's screenshot kept
