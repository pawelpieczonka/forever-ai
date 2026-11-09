"""ForeverAI application: a window that runs the background service (game <-> AI) while it is open."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config
from .kb.store import KB
from .kb.sync import kb_path, run_due
from .service import Service
from .watcher import ScreenshotWatcher

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")
SINGLE_INSTANCE_PORT = 47811


def setup_logging() -> Path:
    path = config.data_dir() / "foreverai.log"
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout and sys.stdout.isatty():
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    return path


class Companion:
    """The background service: watches the game's screenshots, answers through the add-on files, keeps the
    knowledge base fresh."""

    def __init__(self):
        self.store = config.SettingsStore()
        s = self.store.settings
        if not s.game_dir:
            raise SystemExit("WoW Forever folder not found. Set FOREVERAI_GAME_DIR or game_dir in settings.json.")
        self.paths = config.game_paths(s.game_dir)
        self.paths["screenshots"].mkdir(parents=True, exist_ok=True)
        self.kb = KB(kb_path())
        self.service = Service(self.store, self.kb, self.paths["addons"])
        self.watcher = ScreenshotWatcher(self.paths["screenshots"], self.service.on_frame)
        self.status_text = "Starting…"
        self.syncing = False
        self._stop = threading.Event()
        self.service.on_event = self.set_status
        self.service.screen_for = self.watcher.screen_for

    def set_status(self, text: str) -> None:
        self.status_text = text
        log.info("status: %s", text)

    def start(self) -> None:
        self.watcher.start()
        threading.Thread(target=self._maintenance, daemon=True, name="maintenance").start()

        def warm_claude():
            from . import claude_code
            info = claude_code.detect(force=True)
            log.info("Claude Code: %s", {k: v for k, v in (info or {}).items() if k != "path"} or "not found")
            if info and info.get("loggedIn"):
                activity.info("Claude Code %s (%s)", info.get("version"), info.get("subscription"))
                # a fresh install has no OpenRouter key: use the signed-in Claude Code instead
                if self.store.settings.provider == "openrouter" and not config.read_api_key():
                    self.store.update(provider="claude_code")
                    activity.info("AI provider: Claude Code")
            elif info:
                activity.warning("Claude Code is not signed in")
            else:
                activity.info("Claude Code not found")
            self.service._prewarm_safe()  # the first question then skips Claude Code's start-up
        threading.Thread(target=warm_claude, daemon=True, name="claude-detect").start()
        self.set_status("Running")

    def _sync(self, force: bool) -> None:
        if self.syncing:
            return
        self.syncing = True
        try:
            res = run_due(self.kb, force=force, progress=activity.info)  # each step shows in the window
            if res:
                w = res.get("wiki") or {}
                b = res.get("blizzard") or {}
                errors = len(w.get("errors") or []) + len(b.get("errors") or [])
                activity.info("Knowledge base updated: %s new and %s changed pages, %s new articles%s",
                              w.get("new", 0), w.get("changed", 0), b.get("new", 0),
                              f" ({errors} errors, see the log)" if errors else "")
            elif force:
                activity.info("Knowledge base up to date")
        except Exception as e:
            log.exception("knowledge base update failed")
            activity.warning("Knowledge base update failed: %s", e)
        finally:
            self.syncing = False

    def sync_now(self, force: bool = True) -> None:
        threading.Thread(target=self._sync, args=(force,), daemon=True, name="kb-sync").start()

    def _maintenance(self) -> None:
        # on start: update if the last update is older than a day; then check every hour while the app runs
        while not self._stop.is_set():
            self._sync(False)
            self._stop.wait(3600)

    def stop(self) -> None:
        self._stop.set()
        self.watcher.stop()
        self.service.close()
        activity.info("Stopped")


def _single_instance() -> socket.socket | None:
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        return lock
    except OSError:
        return None


def run_window() -> None:
    from tkinter import messagebox

    from .setup_game import find_game_dir, normalize_game_dir, prepare_game
    from .window import AppWindow, ask_game_dir
    lock = _single_instance()
    win = AppWindow()
    if lock is None:
        messagebox.showinfo("ForeverAI", "ForeverAI is already running.")
        win.root.destroy()
        return
    store = config.SettingsStore()
    s = store.settings
    game = normalize_game_dir(s.game_dir) or find_game_dir()
    if not game:
        win.root.update()
        chosen = ask_game_dir(win.root)
        game = normalize_game_dir(chosen)
    if not game:
        messagebox.showerror("ForeverAI", "WoW Forever not found.")
        win.root.destroy()
        return
    if str(game) != s.game_dir:
        store.update(game_dir=str(game))
    first_run = not s.shortcut_offered
    try:
        res = prepare_game(game, store.settings.pool_size)
    except OSError as e:
        messagebox.showerror("ForeverAI", f"Cannot write to the game folder:\n{e}")
        win.root.destroy()
        return
    addon_text = {
        "installed": "Installed — /reload required",
        "updated": "Updated — /reload required",
        "unchanged": "Up to date",
        "linked": "Linked (development)",
    }.get(res["addon"], res["addon"])
    if res["addon"] in ("installed", "updated"):
        activity.info("Add-on %s", res["addon"])
    if res["helpers"]:
        activity.info("Helper add-ons created: %d", res["helpers"])
    comp = Companion()
    win.attach(comp)
    win.set_addon_state(addon_text)
    comp.start()
    activity.info("Started")
    if first_run:
        store.update(shortcut_offered=True)
        if messagebox.askyesno("ForeverAI", "Create a desktop shortcut?", parent=win.root):
            win._shortcut()
    win.run()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="foreverai")
    ap.add_argument("--headless", action="store_true", help="no window (development)")
    ap.add_argument("--setup", action="store_true", help="prepare the files in the game folder and exit")
    ap.add_argument("--game-dir", help="WoW Forever folder (…\\World of Warcraft\\_forever_)")
    args = ap.parse_args(argv)
    logfile = setup_logging()
    if args.game_dir:
        os.environ["FOREVERAI_GAME_DIR"] = args.game_dir
        config.SettingsStore().update(game_dir=str(Path(args.game_dir)))
    if args.setup:  # only writes files: allowed even when the app is already running
        from .setup_game import prepare_game
        s = config.SettingsStore().settings
        if not s.game_dir:
            raise SystemExit("WoW Forever folder not found. Pass it with --game-dir.")
        res = prepare_game(Path(s.game_dir), s.pool_size)
        print(f"Ready: add-on {res['addon']}, {res['helpers']} helper add-ons created. Log: {logfile}")
        return
    if not args.headless:
        run_window()
        return
    lock = _single_instance()  # kept alive for the whole run
    if lock is None:
        print("ForeverAI is already running.")
        return
    comp = Companion()
    comp.start()
    log.info("ForeverAI started headless (game: %s)", comp.paths["game"])
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        comp.stop()
