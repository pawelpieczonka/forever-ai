"""The ForeverAI window: shows that the app is running, what it is connected to, the knowledge base state with an
"Update now" button, and a short English activity log. Closing the window quits the app."""

from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import claude_code, config
from .osutil import WINDOWS, open_path

log = logging.getLogger("foreverai")

BG, PANEL, FG, DIM, ACCENT, WARN = "#14181d", "#1c2229", "#e6e9ec", "#8b96a3", "#3ddc97", "#ffb84d"
# Linux: fontconfig names (an unknown family such as Consolas would fall back to a proportional font)
UI, MONO, TITLE = ("Segoe UI", "Consolas", ("Segoe UI Semibold", 16)) if WINDOWS else \
    ("Sans", "Monospace", ("Sans", 16, "bold"))


class _QueueHandler(logging.Handler):
    """Activity lines and warnings from any thread -> the window (polled on the Tk thread)."""

    def __init__(self, q: queue.Queue):
        super().__init__(level=logging.INFO)
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "foreverai.activity" or record.levelno >= logging.WARNING:
            try:
                text = record.getMessage()
            except Exception:
                return
            if record.levelno >= logging.WARNING and record.name != "foreverai.activity":
                text = "Warning: " + text.splitlines()[0][:160]
            self.q.put((record.created, record.levelno, text))


def ask_game_dir(parent: tk.Misc | None = None) -> str | None:
    from .setup_game import normalize_game_dir
    initial = None
    if not WINDOWS:
        _show_hidden_toggle(parent)
        games = os.path.expanduser("~/Games")
        initial = games if os.path.isdir(games) else os.path.expanduser("~")
    while True:
        d = filedialog.askdirectory(parent=parent, title="Select the World of Warcraft folder", initialdir=initial)
        if not d:
            return None
        found = normalize_game_dir(d)
        if found:
            return str(found)
        messagebox.showwarning("ForeverAI", f"WoW Forever ({os.path.join('_forever_', 'Wow.exe')}) not found in this "
                               "folder.", parent=parent)


def _show_hidden_toggle(parent: tk.Misc | None) -> None:
    """The Tk folder dialog on Linux hides dot folders (~/.wine, ~/.local/share/...) without a way to show them;
    this adds the "Show hidden" toggle."""
    w = parent or tk._default_root
    if w is None:
        return
    try:
        w.tk.call("catch", "tk_getOpenFile -foobarbaz")  # loads the dialog code, so its variables exist
        w.tk.call("set", "::tk::dialog::file::showHiddenBtn", "1")
        w.tk.call("set", "::tk::dialog::file::showHiddenVar", "0")
    except tk.TclError:
        pass


class AppWindow:
    def __init__(self):
        self.root = tk.Tk(className="ForeverAI")  # the WM class matches StartupWMClass of the Linux launcher
        self.root.title("ForeverAI")
        self.root.configure(bg=BG)
        self.root.geometry("640x520")
        self.root.minsize(520, 420)
        try:
            from .setup_game import bundle_root
            if WINDOWS:
                self.root.iconbitmap(str(bundle_root() / "assets" / "foreverai.ico"))
            else:  # X11 has no .ico support; kept in self: Tk drops an image nobody references
                self._icon = tk.PhotoImage(file=str(bundle_root() / "assets" / "foreverai.png"))
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass
        self.q: queue.Queue = queue.Queue()
        logging.getLogger("foreverai").addHandler(_QueueHandler(self.q))
        self.comp = None
        self._build()

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", background=PANEL, foreground=FG, borderwidth=0, padding=(10, 4))
        style.map("TButton", background=[("active", "#26303a"), ("disabled", "#1a1f25")],
                  foreground=[("disabled", DIM)])

        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(top, text="ForeverAI", bg=BG, fg=ACCENT, font=TITLE).pack(side="left")
        self.state = tk.Label(top, text="● Starting…", bg=BG, fg=WARN, font=(UI, 10))
        self.state.pack(side="right")

        grid = tk.Frame(self.root, bg=PANEL)
        grid.pack(fill="x", padx=16, pady=10)
        grid.columnconfigure(1, weight=1)
        self.rows = {}
        for i, (key, label) in enumerate((("game", "Game"), ("addon", "Add-on"), ("ai", "AI"),
                                          ("kb", "Knowledge base"))):
            tk.Label(grid, text=label, bg=PANEL, fg=DIM, font=(UI, 9), anchor="w").grid(
                row=i, column=0, sticky="w", padx=(10, 12), pady=5)
            v = tk.Label(grid, text="…", bg=PANEL, fg=FG, font=(UI, 9), anchor="w", justify="left",
                         wraplength=380)
            v.grid(row=i, column=1, sticky="we", pady=5)
            self.rows[key] = v
        self.change_btn = ttk.Button(grid, text="Change…", command=self._change_game)
        self.change_btn.grid(row=0, column=2, padx=8)
        self.update_btn = ttk.Button(grid, text="Update now", command=self._update_kb)
        self.update_btn.grid(row=3, column=2, padx=8)

        tk.Label(self.root, text="Activity", bg=BG, fg=DIM, font=(UI, 9), anchor="w").pack(fill="x", padx=16)
        box = tk.Frame(self.root, bg=PANEL)
        box.pack(fill="both", expand=True, padx=16, pady=(2, 8))
        self.log = tk.Text(box, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=(MONO, 9),
                           wrap="word", height=12, state="disabled")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("time", foreground=DIM)

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bottom, text="Open data folder", command=lambda: open_path(config.data_dir())).pack(side="left")
        ttk.Button(bottom, text="Desktop shortcut", command=self._shortcut).pack(side="left", padx=8)
        ttk.Button(bottom, text="Quit", command=self.quit).pack(side="right")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ---------------------------------------------------------------- actions
    def _change_game(self) -> None:
        d = ask_game_dir(self.root)
        if not d:
            return
        config.SettingsStore().update(game_dir=d)
        messagebox.showinfo("ForeverAI", "Game folder saved. Restart ForeverAI to apply.", parent=self.root)
        self.quit()

    def _update_kb(self) -> None:
        if self.comp and not self.comp.syncing:
            self.comp.sync_now(True)

    def _shortcut(self) -> None:
        from .setup_game import create_desktop_shortcut
        path = create_desktop_shortcut()
        if path:
            logging.getLogger("foreverai.activity").info("Desktop shortcut created")
        else:
            messagebox.showwarning("ForeverAI", "Could not create the desktop shortcut.", parent=self.root)

    def quit(self) -> None:
        try:
            if self.comp:
                self.comp.stop()
        finally:
            self.root.destroy()

    # ---------------------------------------------------------------- refresh
    def attach(self, comp) -> None:
        self.comp = comp
        self.rows["game"].configure(text=str(comp.paths["game"]))

    def set_addon_state(self, text: str) -> None:
        self.rows["addon"].configure(text=text)

    def _refresh(self) -> None:
        while True:
            try:
                created, level, text = self.q.get_nowait()
            except queue.Empty:
                break
            import time
            self.log.configure(state="normal")
            self.log.insert("end", time.strftime("%H:%M:%S ", time.localtime(created)), "time")
            self.log.insert("end", text + "\n", "warn" if level >= logging.WARNING else "")
            lines = int(self.log.index("end-1c").split(".")[0])
            if lines > 400:
                self.log.delete("1.0", f"{lines - 400}.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        c = self.comp
        if c:
            self.state.configure(text="● Running", fg=ACCENT)
            s = c.store.settings
            if s.provider == "claude_code":
                info = claude_code.detect_cached() if claude_code._detect_cache else None
                names = {m["id"]: m["name"] for m in claude_code.CLAUDE_MODELS}
                if info and info.get("loggedIn"):
                    who = f"Claude Code {info.get('version') or ''} ({info.get('subscription') or 'signed in'})"
                elif info:
                    who = "Claude Code — not signed in (run claude)"
                else:
                    who = "Claude Code — checking" if claude_code._detect_cache is None else "Claude Code — not found"
                ai = f"{who} · {names.get(s.claude_model, s.claude_model)} · thinking {s.claude_effort}"
            else:
                bal = c.service._balance[0] if c.service._balance else None
                ai = f"OpenRouter · {s.model}" + (f" · balance ${bal:.2f}" if bal is not None else "")
            self.rows["ai"].configure(text=ai)
            import time as _t
            if not getattr(self, "_kb_at", 0) or _t.time() - self._kb_at > 5 or c.syncing != getattr(self, "_kb_sync", None):
                try:
                    self._kb_text = f"Updated {c.kb.meta('kb_date') or 'never'} · {c.kb.count_pages()} pages"
                except Exception:
                    self._kb_text = "Unavailable"
                self._kb_at, self._kb_sync = _t.time(), c.syncing
            kb = self._kb_text
            if c.syncing:
                kb = "Updating…"
            self.rows["kb"].configure(text=kb)
            self.update_btn.state(["disabled"] if c.syncing else ["!disabled"])
        self.root.after(700, self._refresh)

    def run(self) -> None:
        self.root.after(200, self._refresh)
        self.root.mainloop()
