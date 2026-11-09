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
