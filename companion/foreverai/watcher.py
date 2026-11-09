"""Watches the game's Screenshots folder for ForeverAI data frames."""

from __future__ import annotations

import io
import logging
import os
from collections import OrderedDict
import threading
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from .frames import Frame, decode_frame

log = logging.getLogger("foreverai")


class ScreenshotWatcher(threading.Thread):
    EXTENSIONS = (".png", ".tga")

    def __init__(self, folder: Path, on_frame: Callable[[Frame], None], interval: float = 0.08):
        super().__init__(daemon=True, name="screenshot-watcher")
        self.folder = Path(folder)
        self.on_frame = on_frame
        self.interval = interval
        self._stop = threading.Event()
        self._seen: dict[str, float] = {}
        self.frames_decoded = 0
        # msg_id -> (image, frame) of the screenshot that carried a question; turned into a JPEG only when the player
        # attached the screen to that question
        self.screens: OrderedDict[int, object] = OrderedDict()

    def screen_for(self, msg_id) -> bytes | None:
        try:
            key = int(msg_id)
        except (TypeError, ValueError):
            return None
        item = self.screens.get(key)
        if item is None or isinstance(item, bytes):
            return item
        try:
            jpeg = self.screen_jpeg(*item)
        except (OSError, ValueError) as e:
            log.warning("screen copy failed: %s", e)
            return None
        self.screens[key] = jpeg
        return jpeg

    @staticmethod
    def screen_jpeg(im: Image.Image, frame: Frame, max_side: int = 1280) -> bytes:
        """The screenshot that carried a question, downscaled for the AI, with the data block blacked out."""
        im = im.convert("RGB")
        ImageDraw.Draw(im).rectangle((0, 0, (frame.grid_w + 6) * frame.cell, 72 * frame.cell), fill=(0, 0, 0))
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return buf.getvalue()

    def stop(self) -> None:
        self._stop.set()

    def _scan(self) -> list[Path]:
        if not self.folder.exists():
            return []
        out = []
        with os.scandir(self.folder) as it:
            for e in it:
                if e.is_file() and e.name.lower().endswith(self.EXTENSIONS) and e.name not in self._seen:
                    out.append(Path(e.path))
        return sorted(out, key=lambda p: p.name)

    def _try_decode(self, path: Path) -> bool | None:
        """True: frame (file removed); False: normal screenshot; None: not ready yet."""
        try:
            size1 = path.stat().st_size
            time.sleep(0.02)
            if path.stat().st_size != size1 or size1 == 0:
                return None
            im = Image.open(path)
            im.load()  # reads the file and releases it, so it can be deleted below
            region = im.crop((0, 0, min(im.width, 600), min(im.height, 300))).convert("RGB")
        except (OSError, SyntaxError, ValueError):
            return None  # still being written -> retried on the next scan
        frame = decode_frame(region)
        if frame is None:
            im.close()
            return False
        if frame.index == 0:
            self.screens[frame.msg_id] = (im, frame)
            while len(self.screens) > 2:
                self.screens.popitem(last=False)
        try:
            path.unlink()
        except OSError as e:
            log.warning("cannot delete %s: %s", path, e)
            self._seen[path.name] = time.time()  # the file stays: it must not be decoded again
        self.frames_decoded += 1
        self.on_frame(frame)
        return True

    def cleanup_old_frames(self) -> int:
        """Screenshots that existed before start: ForeverAI data blocks (sent while the app was not running) are
        deleted without being answered; the player's own screenshots are left alone."""
        removed = 0
        for p in self._scan():
            self._seen[p.name] = time.time()
            try:
                with Image.open(p) as im:
                    frame = decode_frame(im.crop((0, 0, min(im.width, 600), min(im.height, 300))).convert("RGB"))
                if frame is not None:
                    p.unlink()
                    self._seen.pop(p.name, None)  # the name is free again (see run)
                    removed += 1
            except (OSError, SyntaxError, ValueError):
                continue
        if removed:
            log.info("removed %d old ForeverAI data screenshots", removed)
        return removed

    def run(self) -> None:
        self.cleanup_old_frames()  # nothing that existed before start is answered
        pending: dict[str, float] = {}
        while not self._stop.is_set():
            for p in self._scan():
                pending.setdefault(p.name, time.time())
            for name, first in list(pending.items()):
                path = self.folder / name
                if not path.exists():
                    pending.pop(name)
                    continue
                res = self._try_decode(path)
                if res is None and time.time() - first < 10:
                    continue
                pending.pop(name)
                if res:
                    # not remembered: screenshot names have one-second precision, so the next frame of a message can
                    # arrive under the name of the frame that was just decoded and deleted
                    log.info("decoded frame from %s", name)
                else:
                    self._seen[name] = time.time()
            self._stop.wait(self.interval)
