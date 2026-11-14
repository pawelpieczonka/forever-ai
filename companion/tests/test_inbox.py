import base64
import json
import re

from foreverai.inbox import POOL_PREFIX, ensure_pool, pool_size, write_inbox
from foreverai.service import InboxWriter


def test_ensure_pool_creates_lod_addons(tmp_path):
    (tmp_path / "ForeverAI_Data").mkdir()  # legacy folder from an older version is removed
    created = ensure_pool(tmp_path, size=5)
    assert created == 5
    toc = (tmp_path / f"{POOL_PREFIX}003" / f"{POOL_PREFIX}003.toc").read_text(encoding="utf-8")
    assert "## Interface: 16001" in toc
    assert "## LoadOnDemand: 1" in toc
    assert not (tmp_path / "ForeverAI_Data").exists()
    assert ensure_pool(tmp_path, size=5) == 0  # idempotent
    assert pool_size(tmp_path) == 5


def test_write_inbox_roundtrip(tmp_path):
    ensure_pool(tmp_path, size=5)
    payload = {"v": 1, "kind": "answer", "text": "Ünïcödé — non-ASCII survives ✓", "seq": 2}
    write_inbox(tmp_path, 5, payload)
    lua = (tmp_path / f"{POOL_PREFIX}005" / "data.lua").read_text(encoding="ascii")
    m = re.fullmatch(r'ForeverAI_Receive\(5, "([A-Za-z0-9+/=]+)"\)\n', lua)
    assert m, lua
    assert json.loads(base64.b64decode(m.group(1)).decode("utf-8")) == payload


def test_writer_fills_lookahead_and_stops_at_pool_end(tmp_path):
    ensure_pool(tmp_path, size=10)
    w = InboxWriter(tmp_path, "S", next_inbox=7, pool=10, lookahead=80)
    assert w.send({"kind": "answer", "text": "a"})
    for n in (7, 8, 9, 10):
        assert "ForeverAI_Receive" in (tmp_path / f"{POOL_PREFIX}{n:03d}" / "data.lua").read_text()
    assert "empty" in (tmp_path / f"{POOL_PREFIX}006" / "data.lua").read_text()
    assert not InboxWriter(tmp_path, "S", next_inbox=11, pool=10).send({"kind": "x"})


def _read(addons, n):
    text = (addons / f"{POOL_PREFIX}{n:03d}" / "data.lua").read_text(encoding="ascii")
    return json.loads(base64.b64decode(re.search(r'"([^"]*)"', text).group(1)))


def test_a_newer_state_takes_over_from_one_still_being_written(tmp_path, monkeypatch):
    """Every update of an answer rewrote all 200 inboxes behind a lock (160-620 ms measured), and a request for the
    game waited behind it. A newer state stops the older one after the nearest inboxes and writes them all; a final
    state is always written out."""
    import threading
    import time
    from foreverai import service
    ensure_pool(tmp_path, 60)
    w = service.InboxWriter(tmp_path, "S", next_inbox=1, pool=60, lookahead=60)
    plain, wrote = service.write_inbox, []

    def slow(addons_dir, n, payload, b64=None):
        wrote.append((payload["seq"], n))
        time.sleep(0.005)
        plain(addons_dir, n, payload, b64)
    monkeypatch.setattr(service, "write_inbox", slow)
    first = threading.Thread(target=lambda: w.send({"kind": "answer", "text": "a"}))
    first.start()
    while not wrote:
        time.sleep(0.001)
    assert w.send({"kind": "answer", "text": "ab"})
    first.join()
    old, new = [n for seq, n in wrote if seq == 1], [n for seq, n in wrote if seq == 2]
    assert service.InboxWriter.NEAR <= len(old) < 60 and new == list(range(1, 61))  # the newest state is in every inbox
    assert all(_read(tmp_path, n)["text"] == "ab" for n in (1, 30, 60))
    # a final state is written out, whatever comes after it
    wrote.clear()
    last = threading.Thread(target=lambda: w.send({"kind": "answer", "text": "abc", "final": True}))
    last.start()
    while not wrote:
        time.sleep(0.001)
    w.send({"kind": "answer", "text": "late"})
    last.join()
    assert [n for seq, n in wrote if seq == 3] == list(range(1, 61))
