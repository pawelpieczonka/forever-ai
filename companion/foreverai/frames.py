"""Decoding of data frames that the addon paints on screen before calling Screenshot().

Frame layout (physical pixels, top-left corner of the screenshot), current format "FAI2":
  * a black block: a black border one cell thick, inside it a grid of ``grid_w`` x ``rows`` cells,
    each ``cell`` x ``cell`` pixels, read row by row;
  * every cell carries 12 bits: the R, G and B values are 0..15 (one 4-bit nibble each, high nibble first), so the
    block is nearly black and hardly visible during the one frame it is on screen.

The byte stream starts with an 18-byte header::

  magic (4) | msg_id u32 BE | index u8 | count u8 | cell u8 | reserved u8 | grid_w u16 BE | chunk_len u32 BE

followed by ``chunk_len`` payload bytes. Chunks of one message are joined in index order.
The older "FAI1" format (white border, 8 bits per channel) is still decoded.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

from PIL import Image

MAGIC = b"FAI2"
MAGIC_V1 = b"FAI1"
HEADER = struct.Struct(">4sIBBBBHI")
HEADER_SIZE = HEADER.size  # 18
DEFAULT_GRID_W = 128
DEFAULT_MAX_ROWS = 128
HEADER_CELLS_V1 = (HEADER_SIZE + 2) // 3  # 6 cells of 3 bytes
HEADER_CELLS = HEADER_SIZE * 2 // 3       # 12 cells of 3 nibbles


@dataclass
class Frame:
    msg_id: int
    index: int
    count: int
    cell: int
    grid_w: int
    payload: bytes


def frame_capacity(grid_w: int = DEFAULT_GRID_W, max_rows: int = DEFAULT_MAX_ROWS) -> int:
    return grid_w * max_rows * 3 // 2 - HEADER_SIZE


def _nibbles(data: bytes) -> list[int]:
    out = []
    for b in data:
        out += (b >> 4, b & 15)
    return out


def encode_frames(data: bytes, msg_id: int, cell: int = 1, grid_w: int = DEFAULT_GRID_W,
                  max_rows: int = DEFAULT_MAX_ROWS) -> list[Image.Image]:
    """Reference encoder (mirrors the Lua side). Used by tests and the arena tool."""
    cap = frame_capacity(grid_w, max_rows)
    chunks = [data[i:i + cap] for i in range(0, len(data), cap)] or [b""]
    images = []
    for idx, chunk in enumerate(chunks):
        stream = HEADER.pack(MAGIC, msg_id, idx, len(chunks), cell, 0, grid_w, len(chunk)) + chunk
        if len(stream) % 3:
            stream += b"\0" * (3 - len(stream) % 3)
        nib = _nibbles(stream)
        ncells = len(nib) // 3
        rows = (ncells + grid_w - 1) // grid_w
        img = Image.new("RGB", ((grid_w + 2) * cell, (rows + 2) * cell), (0, 0, 0))
        px = img.load()
        for i in range(ncells):
            r, c = divmod(i, grid_w)
            color = (nib[3 * i], nib[3 * i + 1], nib[3 * i + 2])
            x0, y0 = (c + 1) * cell, (r + 1) * cell
            for dy in range(cell):
                for dx in range(cell):
                    px[x0 + dx, y0 + dy] = color
        images.append(img)
    return images


def _pixels(img: Image.Image, cell: int, grid_w: int, start: int, count: int) -> list[tuple[int, int, int]]:
    px = img.load()
    half = cell // 2
    out = []
    for i in range(start, start + count):
        r, c = divmod(i, grid_w)
        out.append(px[(c + 1) * cell + half, (r + 1) * cell + half][:3])
    return out


def _from_nibbles(pixels: list[tuple[int, int, int]]) -> bytes | None:
    nib = [v for p in pixels for v in p]
    if any(v > 15 for v in nib):
        return None
    return bytes((nib[i] << 4) | nib[i + 1] for i in range(0, len(nib) - 1, 2))


def _decode_v2(img: Image.Image, w: int, h: int) -> Frame | None:
    for cell in (1, 2, 3, 4):
        if (HEADER_CELLS + 1) * cell >= w:
            break
        try:
            head = _from_nibbles(_pixels(img, cell, 10_000, 0, HEADER_CELLS))
        except IndexError:
            continue
        if not head or head[:4] != MAGIC:
            continue
        _m, msg_id, index, count, hcell, _r, grid_w, chunk_len = HEADER.unpack(head[:HEADER_SIZE])
        if hcell != cell or grid_w <= 0 or count == 0 or index >= count:
            continue
        ncells = ((HEADER_SIZE + chunk_len + 2) // 3 * 3) * 2 // 3
        rows = (ncells + grid_w - 1) // grid_w
        if (grid_w + 1) * cell >= w or (rows + 1) * cell >= h:
            continue
        stream = _from_nibbles(_pixels(img, cell, grid_w, 0, ncells))
        if stream is None:
            continue
        return Frame(msg_id, index, count, cell, grid_w, stream[HEADER_SIZE:HEADER_SIZE + chunk_len])
    return None


def _decode_v1(img: Image.Image, w: int, h: int) -> Frame | None:
    for cell in (1, 2, 3, 4):
        if (HEADER_CELLS_V1 + 1) * cell >= w:
            break
        try:
            head = bytes(v for p in _pixels(img, cell, 10_000, 0, HEADER_CELLS_V1) for v in p)
        except IndexError:
            continue
        if head[:4] != MAGIC_V1:
            continue
        _m, msg_id, index, count, hcell, _r, grid_w, chunk_len = HEADER.unpack(head[:HEADER_SIZE])
        if hcell != cell or grid_w <= 0 or count == 0 or index >= count:
            continue
        ncells = (HEADER_SIZE + chunk_len + 2) // 3
        rows = (ncells + grid_w - 1) // grid_w
        if (grid_w + 1) * cell >= w or (rows + 1) * cell >= h:
            continue
        stream = bytes(v for p in _pixels(img, cell, grid_w, 0, ncells) for v in p)
        return Frame(msg_id, index, count, cell, grid_w, stream[HEADER_SIZE:HEADER_SIZE + chunk_len])
    return None


def decode_frame(img: Image.Image) -> Frame | None:
    """Return the frame painted in the top-left corner of ``img`` or ``None``."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if w < 16 or h < 3:
        return None
    corner = img.load()[0, 0][:3]
    if corner == (0, 0, 0):
        return _decode_v2(img, w, h)
    if corner == (255, 255, 255):
        return _decode_v1(img, w, h)
    return None


@dataclass
class _Pending:
    count: int
    parts: dict[int, bytes] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.monotonic)


class FrameAssembler:
    """Joins frames of one message; returns the full byte string once all parts arrived."""

    def __init__(self, timeout_s: float = 60.0):
        self._pending: dict[int, _Pending] = {}
        self._timeout = timeout_s

    def add(self, frame: Frame) -> bytes | None:
        self._expire()
        p = self._pending.get(frame.msg_id)
        if p is None or p.count != frame.count:
            p = _Pending(frame.count)
            self._pending[frame.msg_id] = p
        p.parts[frame.index] = frame.payload
        if len(p.parts) == p.count:
            del self._pending[frame.msg_id]
            return b"".join(p.parts[i] for i in range(p.count))
        return None

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._pending.items() if now - v.first_seen > self._timeout]:
            del self._pending[key]
