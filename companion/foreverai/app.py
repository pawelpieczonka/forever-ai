"""ForeverAI application: a window that runs the background service (game <-> AI) while it is open.

At start the app checks Claude Code first (a signed-in Claude Code needs no key); without it, and without a saved
OpenRouter or Google Gemini key, the window asks for one."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

from . import __version__, config
from .kb.store import KB
from .kb.sync import kb_path, run_due
from .service import PROVIDER_NAMES, Service
from .watcher import ScreenshotWatcher

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")
SINGLE_INSTANCE_PORT = 47811


def setup_logging() -> Path:
    """The log goes to the window's Activity list always, and to files only when the player switched that on."""
    from . import logs
    path = logs.path()
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    logs.set_enabled(config.SettingsStore().settings.save_logs)
    if sys.stdout and sys.stdout.isatty():
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    threading.excepthook = _log_thread_error
    return path


def _log_thread_error(args: threading.ExceptHookArgs) -> None:
    """A thread that ends with an error goes to the log and the window: the windowed app has no console for Python's
    own report."""
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "a thread"
    log.error("%s stopped after an error (%s: %s); restart ForeverAI", name, args.exc_type.__name__, args.exc_value,
              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def _exe_id() -> tuple[int, int, int] | None:
    """Identity of the packaged app's executable (None from source); a replaced file can get the old inode back."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        st = os.stat(sys.executable)
    except OSError:
        return (0, 0, 0)
    return st.st_ino, st.st_size, st.st_mtime_ns


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
        self.service = Service(self.store, self.kb, self.paths["addons"], keep_notes=True)
        self.watcher = ScreenshotWatcher(self.paths["screenshots"], self.service.on_frame)
        self.status_text = "Starting…"
        self.syncing = False
        self.claude_checked = False  # the start-up check of Claude Code is done
        self.needs_key = False  # no AI provider is usable: the window asks for a key
        self.update: dict | None = None  # a newer release on GitHub
        self._stop = threading.Event()
        self.service.on_event = self.set_status
        self.service.screen_for = self.watcher.screen_for
        self._exe = _exe_id()
        self._replaced = False

    def files_replaced(self) -> bool:
        """The packaged app's files were replaced while it runs (a new build, or a new version unpacked over it). It
        reads its code from them, so the parts that load later fail until it restarts."""
        if not self._replaced and self._exe is not None and _exe_id() != self._exe:
            self._replaced = True
            log.warning("ForeverAI's files were replaced while it runs (new build or version): restart ForeverAI")
        return self._replaced

    def set_status(self, text: str) -> None:
        self.status_text = text
        log.info("status: %s", text)

    def start(self) -> None:
        self.watcher.start()
        threading.Thread(target=self._maintenance, daemon=True, name="maintenance").start()

        threading.Thread(target=self.check_providers, daemon=True, name="claude-detect").start()
        if not config.dev_mode() and not os.environ.get("FOREVERAI_NO_UPDATE_CHECK"):
            threading.Thread(target=self._check_update, daemon=True, name="update-check").start()
        self.set_status("Running")

    def check_providers(self, prefer_claude: bool = False) -> str | None:
        """Claude Code first, then the saved keys; picks the provider (or asks for a key). Returns the provider.
        ``prefer_claude`` (the player asked for Claude Code) switches to a signed-in Claude Code even when the chosen
        provider still works."""
        from . import claude_code
        activity.info("Checking Claude Code…")
        info = claude_code.detect(force=True)
        log.info("Claude Code: %s", {k: v for k, v in (info or {}).items() if k != "path"} or "not found")
        if info and info.get("loggedIn"):
            activity.info("Claude Code %s (%s)", info.get("version"), info.get("subscription"))
        elif info:
            activity.warning("Claude Code is installed but not signed in (sign in: claude auth login)")
        else:
            activity.info("Claude Code not found")
        if prefer_claude and info and info.get("loggedIn"):
            provider = "claude_code"
        else:
            provider = config.choose_provider(self.store.settings, info)
        if provider is None:
            self.needs_key = True
            activity.warning("No AI provider yet: enter an OpenRouter or Google Gemini key")
        else:
            self.needs_key = False
            if provider != self.store.settings.provider:
                self.store.update(provider=provider)
            activity.info("AI provider: %s", PROVIDER_NAMES[provider])
        self.claude_checked = True
        self.service._prewarm_safe()  # the first question then skips Claude Code's start-up
        return provider

    def _check_update(self) -> None:
        from .updates import newer_release
        rel = newer_release()
        if rel:
            self.update = rel
            activity.info("ForeverAI %s is available (you have %s): click Download update", rel["version"],
                          __version__)

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
    ap.add_argument("--game-dir", help="WoW Forever folder (…/World of Warcraft/_forever_)")
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
