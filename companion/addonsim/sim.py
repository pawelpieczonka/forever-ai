"""The simulated game client: runs the real add-on code (Lua 5.1 through lupa) on the emulated WoW API in lua/, in a
stand-in game folder, frame by frame.

The add-on talks to the outside exactly as in the game: Screenshot() writes Screenshots/WoWScrnShot_*.tga of the
simulated screen (with the data block the add-on painted), C_AddOns.LoadAddOn reads Interface/AddOns/<name>/*.lua from
disk when it is called, and saved variables are written to WTF on /reload and logout. So the ForeverAI app can answer
it unchanged (see app.py)."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import shutil
import time
import zlib
from datetime import datetime
from pathlib import Path

from lupa import lua51

from .render import Canvas
from .text import TextEngine

LUA_DIR = Path(__file__).with_name("lua")
ACCOUNT = "SIMULATOR"

log = logging.getLogger("addonsim")


class SimError(RuntimeError):
    """A step the simulated player cannot do (no such button, nothing to type into, ...)."""


def _s(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return "" if v is None else str(v)


def _b(v) -> bytes:
    return v if isinstance(v, bytes) else str(v).encode("utf-8")


def _pair(res) -> tuple:
    """(ok, reason) from a Lua function that returns one or two values."""
    return res if isinstance(res, tuple) else (res, None)


def make_game_dir(game_dir: Path) -> Path:
    """A folder the ForeverAI app accepts as the game (it only looks for Wow.exe), with the game's subfolders."""
    game_dir = Path(game_dir)
    for sub in ("Interface/AddOns", "Screenshots", "WTF"):
        (game_dir / sub).mkdir(parents=True, exist_ok=True)
    exe = game_dir / "Wow.exe"
    if not exe.exists():
        exe.write_bytes(b"")
    return game_dir


def install_game(game_dir: Path, addon_dir: Path | None = None, pool: int = 300) -> None:
    """The add-on and its inbox add-ons in the stand-in game folder, for a run without the ForeverAI app (which puts
    them there itself). addon_dir: default addon/ForeverAI of this checkout."""
    from foreverai.inbox import ensure_pool
    from foreverai.setup_game import addon_source
    addons = make_game_dir(game_dir) / "Interface" / "AddOns"
    target = addons / "ForeverAI"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(addon_dir or addon_source(), target)
    ensure_pool(addons, pool)


def read_toc(path: Path) -> dict:
    meta, files = {}, []
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("##"):
            key, _, value = line[2:].partition(":")
            meta[key.strip()] = value.strip()
        elif line and not line.startswith("#"):
            files.append(line.replace("\\", "/"))
    return {"meta": meta, "files": files}


def scan_addons(addons_dir: Path) -> list[dict]:
    """The add-ons in Interface/AddOns, as the game lists them when the UI loads (alphabetically)."""
    out = []
    if not addons_dir.is_dir():
        return out
    for d in sorted((p for p in addons_dir.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        toc = next((t for t in d.glob("*.toc") if t.stem.lower() == d.name.lower()), None)
        if not toc:
            continue
        info = read_toc(toc)
        meta = info["meta"]

        def names(key, meta=meta):
            return [n.strip() for n in meta.get(key, "").split(",") if n.strip()]
        out.append({"name": d.name, "dir": str(d), "files": info["files"], "meta": meta,
                    "lod": meta.get("LoadOnDemand", "0").strip() == "1", "sv": names("SavedVariables"),
                    "svChar": names("SavedVariablesPerCharacter"),
                    "deps": names("Dependencies") or names("RequiredDeps")})
    return out


class Sim:
    """The game client around the add-on.

    ``realtime``: the game clock follows the wall clock (needed when the ForeverAI app answers, since it runs in real
    time); otherwise simulated seconds pass as fast as the frames can be computed."""

    def __init__(self, game_dir: Path, *, screen: tuple[int, int] = (1920, 1080), fps: int = 30,
                 realtime: bool = False, clock: datetime | None = None, text: TextEngine | None = None,
                 seed: int = 1):
        self.game_dir = make_game_dir(Path(game_dir))
        self.addons_dir = self.game_dir / "Interface" / "AddOns"
        self.screens_dir = self.game_dir / "Screenshots"
        self.screen = (int(screen[0]), int(screen[1]))
        self.fps = fps
        self.dt = 1.0 / fps
        self.realtime = realtime
        self.epoch = (clock or datetime.now()).timestamp()
        self.text = text or TextEngine()
        self.seed = seed
        self.now = 0.0
        self.errors: list[dict] = []
        self.warnings: list[str] = []
        self.missing: set[str] = set()
        self.chat: list[str] = []
        self.placeholders: set[str] = set()
        self.reloads = 0
        self.cvars: dict[str, str] = {}
        self._canvas: Canvas | None = None
        self._wall0 = time.perf_counter()
        self._lua = None
        self._wow = None
        self._started = False

    # ------------------------------------------------------------------------------------------------ the Lua side
    def _host(self):
        lua = self._lua

        def text_size(text, font, size, flags, width, wrap, spacing, max_lines):
            lay = self.text.layout(_s(text), _s(font), float(size), float(width), bool(wrap), float(spacing),
                                   int(max_lines or 0))
            return lay.width, lay.height, len(lay.lines), 1 if lay.truncated else 0

        def json_decode(s):
            try:
                return True, self.to_lua(json.loads(_s(s)))
            except (ValueError, TypeError) as e:
                return False, _b(e)

        def compress(s, method):
            if int(method or 0) == 0:  # raw deflate
                c = zlib.compressobj(wbits=-15)
                return c.compress(s) + c.flush()
            return zlib.compress(s, wbits=31) if int(method) == 2 else zlib.compress(s)

        def decompress(s, method):
            try:
                wbits = {0: -15, 2: 31}.get(int(method or 0), 15)
                return True, zlib.decompress(s, wbits=wbits)
            except zlib.error as e:
                return False, _b(e)

        def b64decode(s):
            try:
                return True, base64.b64decode(s, validate=False)
            except (binascii.Error, ValueError) as e:
                return False, _b(e)

        def read_file(path):
            try:
                return Path(_s(path)).read_bytes()
            except OSError as e:
                return None, _b(e)

        def write_file(path, data):
            p = Path(_s(path))
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            return True

        def saved_path(addon, per_char):
            base = self.game_dir / "WTF" / "Account" / ACCOUNT
            if per_char:
                p = self._wow.player
                base = base / _s(p.realm) / _s(p.name)
            return _b(base / "SavedVariables" / f"{_s(addon)}.lua")

        def screenshot(name, ext, quality):
            try:
                self._game_screenshot(_s(name), _s(ext), int(quality or 3))
                return True
            except OSError as e:
                log.warning("screenshot failed: %s", e)
                return False

        def draw(kind, *args):
            if self._canvas is not None:
                self._canvas.op(kind, *args)

        def host_log(level, msg):
            level, msg = _s(level), _s(msg)
            if level == "chat":
                self.chat.append(msg)
                log.info("chat: %s", msg)
            elif level == "warning":
                self.warnings.append(msg)
                log.warning("%s", msg)
            elif level == "error":
                log.error("Lua error: %s", msg)
            else:
                log.info("%s", msg)

        return lua.table_from({
            b"text_size": text_size, b"json_decode": json_decode, b"compress": compress, b"decompress": decompress,
            b"b64encode": lambda s: base64.b64encode(s), b"b64decode": b64decode, b"read_file": read_file,
            b"write_file": write_file, b"saved_path": saved_path, b"screenshot": screenshot, b"draw": draw,
            b"log": host_log, b"wall": lambda: time.perf_counter() - self._wall0, b"epoch": self.epoch,
            b"screen_w": self.screen[0], b"screen_h": self.screen[1],
        })

    def to_lua(self, v):
        """A Python value as a Lua value (strings as bytes, lists as arrays from 1)."""
        if isinstance(v, dict):
            t = self._lua.table()
            for k, x in v.items():
                if x is not None:
                    t[_b(k) if isinstance(k, str) else k] = self.to_lua(x)
            return t
        if isinstance(v, (list, tuple)):
            t = self._lua.table()
            for i, x in enumerate(v, 1):
                if x is not None:
                    t[i] = self.to_lua(x)
            return t
        if isinstance(v, str):
            return v.encode("utf-8")
        return v

    def to_py(self, v, depth: int = 0):
        """A Lua value as a Python value (tables with keys 1..n as lists)."""
        if depth > 40:
            return "<too deep>"
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace")
        if lua51.lua_type(v) == "table":
            items = list(v.items())
            if items and all(isinstance(k, int) for k, _ in items) and sorted(k for k, _ in items) == list(
                    range(1, len(items) + 1)):
                return [self.to_py(x, depth + 1) for _, x in sorted(items)]
            return {_s(k) if isinstance(k, bytes) else str(k): self.to_py(x, depth + 1) for k, x in items}
        if lua51.lua_type(v) == "function":
            return "<function>"
        return v

    def _boot(self, reload: bool) -> None:
        self._lua = lua51.LuaRuntime(encoding=None, unpack_returned_tuples=True, register_eval=False,
                                     register_builtins=False)
        self._lua.execute(b"math.randomseed(...)", self.seed + self.reloads)
        self._wow = self._lua.execute((LUA_DIR / "wow.lua").read_bytes(), self._host())
        self._lua.execute((LUA_DIR / "world.lua").read_bytes(), self._wow)
        # what the game does not have
        self._lua.execute(b"os, io, require, package, module, dofile, loadfile, debug, python = nil")
        self._wow.now = self.now
        for k, v in self.cvars.items():
            self._wow.cvars[_b(k)] = _b(v)
        self._wow.setAddons(self.to_lua(scan_addons(self.addons_dir)))
        self._wow.loadAddOns()
        self._wow.fire(b"PLAYER_LOGIN")
        self._wow.fire(b"PLAYER_ENTERING_WORLD", not reload, reload)
        self._wow.settle()
        self._collect()

    def _collect(self) -> None:
        """Errors and API gaps the Lua side noted since the last call."""
        errs = self._wow.errors
        for i in range(len(self.errors) - self._errors_base + 1, len(errs) + 1):
            e = errs[i]
            self.errors.append({"msg": _s(e.msg), "trace": _s(e.trace)})
        for k in self._wow.missing.keys():
            self.missing.add(_s(k))

    # ------------------------------------------------------------------------------------------------ lifecycle
    def start(self) -> "Sim":
        """Logs in: loads the add-ons from the game folder and starts the UI."""
        self._errors_base = len(self.errors)
        self._boot(reload=False)
        self._started = True
        self._wall_start = time.perf_counter() - self.now
        return self

    def reload(self) -> None:
        """/reload: the add-ons save their variables, the UI loads again from the files on disk."""
        self._logout()
        self.reloads += 1
        self._errors_base = len(self.errors)
        self._boot(reload=True)

    def _logout(self) -> None:
        self._wow.fire(b"PLAYER_LOGOUT")
        self._wow.saveVariables()
        self.cvars = {_s(k): _s(v) for k, v in self._wow.cvars.items()}
        self._collect()

    def close(self) -> None:
        if self._started:
            self._logout()
            self._started = False

    def __enter__(self) -> "Sim":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------------------------------------ time
    def step(self, frames: int = 1) -> None:
        for _ in range(frames):
            if self.realtime:
                wait = self._wall_start + self.now + self.dt - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
            self.now += self.dt
            self._wow.tick(self.dt)
            if self._wow.reloadRequested:
                self.reload()
        self._collect()

    def run(self, seconds: float) -> None:
        self.step(max(1, int(round(seconds * self.fps))))

    def run_until(self, predicate, timeout: float, what: str = "the condition") -> None:
        end = self.now + timeout
        while not predicate():
            if self.now >= end:
                raise SimError(f"{what} did not happen within {timeout:g} s (game time)")
            self.step()

    # ------------------------------------------------------------------------------------------------ the player
    def lua(self, code: str, *args):
        """Runs Lua code in the game's global environment (as /run would) and returns its result."""
        res = self._lua.execute(_b(code), *(self.to_lua(a) for a in args))
        self._wow.settle()
        self._collect()
        return res

    def _find(self, label: str, nth: int = 1):
        w, count = _pair(self._wow.find(_b(label), nth))
        if w is None:
            visible = ", ".join(sorted({t for t in self.clickables()}))[:600]
            raise SimError(f"no visible button or field {label!r}" + (f" #{nth} (there are {count})" if count else "")
                           + (f"; visible: {visible}" if visible else ""))
        return w

    def clickables(self) -> list[str]:
        out = []
        for w in self._wow.widgets.values():
            s = self._wow.S[w]
            if _s(s.kind) in ("Button", "CheckButton") and self._wow.isVisible(w):
                fs = s.fontString
                text = _s(self._wow.stripCodes(self._wow.S[fs].text)) if fs is not None else ""
                if not text:
                    for r in s.regions.values():
                        rs = self._wow.S[r]
                        if _s(rs.kind) == "FontString" and _s(rs.text):
                            text = _s(self._wow.stripCodes(rs.text))
                            break
                if text:
                    out.append(text)
        return out

    def click(self, label: str, nth: int = 1, button: str = "LeftButton") -> None:
        w = self._find(label, nth)
        ok, why = _pair(self._wow.click(w, _b(button)))
        if not ok:
            raise SimError(f"cannot click {label!r}: {_s(why)}")
        self.step()

    def hover(self, label: str, nth: int = 1) -> None:
        self._wow.hover(self._find(label, nth))
        self.step()

    def leave(self) -> None:
        self._wow.leave()
        self.step()

    def type(self, text: str) -> None:
        ok, why = _pair(self._wow.typeText(_b(text)))
        if not ok:
            raise SimError(_s(why))
        self.step()

    def key(self, key: str) -> None:
        self._wow.key(_b(key))
        self.step()

    def wheel(self, delta: float, nth: int = 1) -> None:
        ok, why = _pair(self._wow.wheel(delta, nth))
        if not ok:
            raise SimError(_s(why))
        self.step()

    def slash(self, line: str) -> None:
        ok, why = _pair(self._wow.slash(_b(line)))
        if not ok:
            raise SimError(_s(why))
        self.step()

    def ask(self, text: str) -> None:
        """Types a question into the chat field and presses Enter."""
        if self.lua("return ForeverAIFrame and ForeverAIFrame:IsShown()") is not True:
            self.slash("/fai")
        self.click("Chat")
        self.type(text)
        self.key("ENTER")

    def answering(self) -> bool:
        return self.lua("local ns = ForeverAI return ns ~= nil and (ns.pending ~= nil or ns.collecting == true "
                        "or (ns.Transport ~= nil and ns.Transport.sending == true))") is True

    def wait_answer(self, timeout: float = 90) -> None:
        self.run_until(lambda: not self.answering(), timeout, "the answer")
        self.step(2)

    # ------------------------------------------------------------------------------------------------ looking
    def box(self, label: str, nth: int = 1) -> tuple[int, int, int, int]:
        """Where a button, check box or text field is on the screen: (left, top, right, bottom) in pixels."""
        r = self._wow.boundsPx(self._find(label, nth))
        return tuple(int(round(v)) for v in r)

    def texts(self) -> list[str]:
        """Every text on the screen, top to bottom, without colour codes."""
        out = []
        for item in self._wow.visibleTexts().values():
            out.append(_s(self._wow.stripCodes(item.text)))
        return out

    def render(self) -> Canvas:
        canvas = Canvas(self.screen[0], self.screen[1], self.text)
        self._canvas = canvas
        try:
            self._wow.draw()
        finally:
            self._canvas = None
        self.placeholders |= canvas.placeholders
        self._collect()
        return canvas

    def frame_box(self, name: str, margin: int = 0) -> tuple[int, int, int, int] | None:
        f = self._lua.globals()[_b(name)]
        if f is None or not self._wow.isVisible(f):
            return None
        r = self._wow.boundsPx(f)
        if not isinstance(r, tuple) or r[0] is None:
            return None
        x0, y0, x1, y1 = r
        return (max(0, int(x0) - margin), max(0, int(y0) - margin), min(self.screen[0], int(x1 + 0.999) + margin),
                min(self.screen[1], int(y1 + 0.999) + margin))

    def screenshot(self, path: Path, region: str = "window") -> Path:
        """A PNG of the simulated screen. region: "window" (the ForeverAI window, with a tooltip shown over it),
        "screen", or the name of a frame."""
        im = self.render().image()
        box = None
        if region == "window":
            box = self.frame_box("ForeverAIFrame", 16)
            tip = self.frame_box("GameTooltip", 16)
            if box and tip:
                box = (min(box[0], tip[0]), min(box[1], tip[1]), max(box[2], tip[2]), max(box[3], tip[3]))
        elif region != "screen":
            box = self.frame_box(region, 16)
            if box is None:
                raise SimError(f"the frame {region!r} is not on the screen")
        if box:
            im = im.crop(box)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path)
        return path

    def _game_screenshot(self, name: str, ext: str, quality: int) -> None:
        im = self.render().image()
        path = self.screens_dir / name
        tmp = path.with_name(path.name + ".part")
        if ext == "tga":
            im.save(tmp, "TGA")
        elif ext == "png":
            im.save(tmp, "PNG")
        else:
            im.save(tmp, "JPEG", quality=max(10, min(95, quality * 10)))
        os.replace(tmp, path)

    # ------------------------------------------------------------------------------------------------ the app's side
    def inbox_next(self) -> int:
        return int(self.lua("return ForeverAI.Transport.nextInbox") or 1)

    def deliver(self, payload: dict, lookahead: int = 30) -> None:
        """Puts a reply into the inboxes the add-on reads next, the way the ForeverAI app does (for tests that run the
        add-on without the app). The session is filled in."""
        from foreverai.inbox import pool_size, write_inbox
        body = dict(payload)
        body.setdefault("session", _s(self.lua("return ForeverAI.session")))
        first = self.inbox_next()
        for n in range(first, min(pool_size(self.addons_dir), first + lookahead - 1) + 1):
            write_inbox(self.addons_dir, n, body)

    def sent_messages(self, remove: bool = True) -> list[dict]:
        """The messages the add-on sent through screenshots, decoded the way the ForeverAI app decodes them (for tests
        without the app)."""
        from PIL import Image

        from foreverai.frames import FrameAssembler, decode_frame
        assembler = FrameAssembler()
        out = []
        for p in sorted(self.screens_dir.glob("WoWScrnShot_*")):
            with Image.open(p) as im:
                frame = decode_frame(im.crop((0, 0, min(im.width, 600), min(im.height, 300))).convert("RGB"))
            if remove:
                p.unlink()
            if frame is None:
                continue
            data = assembler.add(frame)
            if data is not None:
                out.append(json.loads(zlib.decompress(data)))
        return out

    def report(self) -> dict:
        return {"errors": self.errors, "warnings": sorted(set(self.warnings)), "missingApi": sorted(self.missing),
                "placeholderTextures": sorted(self.placeholders), "chat": self.chat, "reloads": self.reloads,
                "gameTime": round(self.now, 2), "fonts": self.text.describe()}


def parse_clock(text: str) -> datetime:
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})(?::(\d{2}))?", text.strip())
    if not m:
        raise ValueError("clock: use YYYY-MM-DD HH:MM")
    return datetime.fromisoformat(f"{m.group(1)} {m.group(2)}:{m.group(3) or '00'}")
