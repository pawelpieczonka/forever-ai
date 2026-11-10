"""The ForeverAI window: shows that the app is running, what it is connected to, the knowledge base state with an
"Update now" button, the AI settings (provider, model, thinking level, web search) and a short English activity log.
It asks for an OpenRouter or Google Gemini key when Claude Code is not available. Closing the window quits the app."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import claude_code, config
from .osutil import WINDOWS, open_path, open_terminal

log = logging.getLogger("foreverai")

BG, PANEL, FG, DIM, ACCENT, WARN = "#14181d", "#1c2229", "#e6e9ec", "#8b96a3", "#3ddc97", "#ffb84d"
FIELD, HOVER = "#262e37", "#26303a"
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
        self.root.geometry("660x660")
        self.root.minsize(540, 560)
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
        style.map("TButton", background=[("active", HOVER), ("disabled", "#1a1f25")],
                  foreground=[("disabled", DIM)])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG, arrowcolor=FG,
                        bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, selectbackground=FIELD,
                        selectforeground=FG, padding=(6, 2))
        style.map("TCombobox", fieldbackground=[("readonly", FIELD), ("disabled", "#1a1f25")],
                  background=[("active", HOVER)], foreground=[("disabled", DIM)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        for opt, value in (("background", FIELD), ("foreground", FG), ("selectBackground", HOVER),
                           ("selectForeground", FG)):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", value)  # the drop-down list

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

        tk.Label(self.root, text="AI settings", bg=BG, fg=DIM, font=(UI, 9), anchor="w").pack(fill="x", padx=16)
        self.ai = AISettings(self.root, self.ask_key)
        self.ai.frame.pack(fill="x", padx=16, pady=(2, 10))

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
        self.update_dl = ttk.Button(bottom, text="Download update", command=self._download_update)
        self._key_dialog = None
        self._key_asked = False
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

    def ask_key(self, provider: str | None = None) -> None:
        """Keys & sign-in; ``provider`` (openrouter / gemini) is preselected for its key."""
        if not self.comp:
            return
        if self._key_dialog and self._key_dialog.alive():
            if provider in KeyDialog.LINKS:
                self._key_dialog.provider.set(provider)
            self._key_dialog.top.lift()
            return
        self._key_dialog = KeyDialog(self.root, self.comp, provider)

    def _download_update(self) -> None:
        rel = self.comp.update if self.comp else None
        if rel:
            open_path(rel.get("url"))

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
        self.ai.attach(comp)

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
            replaced = c.files_replaced()
            self.state.configure(text="● Restart ForeverAI" if replaced else "● Running", fg=WARN if replaced else ACCENT)
            s = c.store.settings
            if c.needs_key and (config.read_key("openrouter") or config.read_key("gemini")):
                c.needs_key = False  # a key was entered in the game
            if c.needs_key and not self._key_asked:
                self._key_asked = True
                self.ask_key()
            if c.update and not self.update_dl.winfo_ismapped():
                self.update_dl.configure(text=f"Download update {c.update['version']}")
                self.update_dl.pack(side="right", padx=8)
            if not c.claude_checked:
                ai = "Checking Claude Code…"
            elif c.needs_key:
                ai = "No AI connected — click Keys & sign-in…"
            elif s.provider == "gemini":
                ai = f"Google Gemini · {s.gemini_model}"
            elif s.provider == "claude_code":
                info = claude_code.detect_cached() if claude_code._detect_cache else None
                names = {m["id"]: m["name"] for m in claude_code.CLAUDE_MODELS}
                if info and info.get("loggedIn"):
                    who = f"Claude Code {info.get('version') or ''} ({info.get('subscription') or 'signed in'})"
                elif info:
                    who = "Claude Code — not signed in (claude auth login)"
                else:
                    who = "Claude Code — checking" if claude_code._detect_cache is None else "Claude Code — not found"
                ai = f"{who} · {names.get(s.claude_model, s.claude_model)} · thinking {s.claude_effort}"
            else:
                bal = c.service._balance[0] if c.service._balance else None
                ai = f"OpenRouter · {s.model}" + (f" · balance ${bal:.2f}" if bal is not None else "")
            self.rows["ai"].configure(text=ai, fg=WARN if c.needs_key else FG)
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
            self.ai.sync()
        self.root.after(700, self._refresh)

    def run(self) -> None:
        self.root.after(200, self._refresh)
        self.root.mainloop()


class AISettings:
    """Provider, model, thinking level (Claude Code) and web search. Changes apply at once; a provider without a key
    (or a Claude Code that is not signed in) opens Keys & sign-in. These settings used to be in the game, where each
    change went to the app as a screenshot and its reply used up an inbox."""

    PROVIDERS = (("claude_code", "Claude Code (subscription)"), ("openrouter", "OpenRouter"),
                 ("gemini", "Google Gemini"))
    SEARCH = (("auto", "Auto"), ("always", "Always"), ("never", "Off"))
    MESSAGE_S = 8  # a result stays this long before the Claude Code hint comes back

    def __init__(self, parent: tk.Misc, ask_key):
        from .service import EFFORT_NAMES
        self.efforts = [(e, EFFORT_NAMES.get(e, e)) for e in claude_code.EFFORTS]
        self.ask_key = ask_key
        self.comp = None
        self.busy = False
        self.results: queue.Queue = queue.Queue()
        self.choices: list[dict] = []
        self._choices_for = None  # (provider, has key) the model list was made for
        self._msg_until = 0.0
        f = self.frame = tk.Frame(parent, bg=PANEL)
        f.columnconfigure(2, weight=1)
        self.provider = ttk.Combobox(f, state="readonly", width=30, values=[n for _, n in self.PROVIDERS])
        self.model = ttk.Combobox(f, width=30)  # OpenRouter and Gemini: another model can be typed by its ID
        self.effort = ttk.Combobox(f, state="readonly", width=14, values=[n for _, n in self.efforts])
        self.search = ttk.Combobox(f, state="readonly", width=14, values=[n for _, n in self.SEARCH])
        self.labels = {}
        for i, (text, box) in enumerate((("Provider", self.provider), ("Model", self.model),
                                         ("Thinking", self.effort), ("Web search", self.search))):
            lbl = tk.Label(f, text=text, bg=PANEL, fg=DIM, font=(UI, 9), anchor="w")
            lbl.grid(row=i, column=0, sticky="w", padx=(10, 12), pady=4)
            box.grid(row=i, column=1, sticky="w", pady=4)
            self.labels[box] = lbl
        self.keys_btn = ttk.Button(f, text="Keys & sign-in…", command=lambda: self.ask_key())
        self.keys_btn.grid(row=0, column=2, sticky="w", padx=8)
        self.note = tk.Label(f, text="", bg=PANEL, fg=DIM, font=(UI, 9), anchor="w", justify="left", wraplength=230)
        self.note.grid(row=1, column=2, sticky="w", padx=8)
        self.msg = tk.Label(f, text="", bg=PANEL, fg=DIM, font=(UI, 9), anchor="w", justify="left", wraplength=560)
        self.msg.grid(row=4, column=0, columnspan=3, sticky="we", padx=10, pady=(0, 6))
        self.provider.bind("<<ComboboxSelected>>", lambda _e: self._pick_provider())
        self.model.bind("<<ComboboxSelected>>", lambda _e: self._pick_model())
        self.model.bind("<Return>", lambda _e: self._pick_model())
        self.model.bind("<FocusOut>", lambda _e: self._show_model())  # a typed ID that was not applied
        self.effort.bind("<<ComboboxSelected>>", lambda _e: self._pick(
            "set_effort", self.efforts[self.effort.current()][0]))
        self.search.bind("<<ComboboxSelected>>", lambda _e: self._pick(
            "set_search_mode", self.SEARCH[self.search.current()][0]))
        self._set_enabled(False)
        f.after(100, self._poll)

    # ---------------------------------------------------------------- state
    def attach(self, comp) -> None:
        self.comp = comp
        self._set_enabled(True)
        self.sync()

    def _settings(self):
        return self.comp.store.settings

    def _model_id(self) -> str:
        s = self._settings()
        return {"claude_code": s.claude_model, "gemini": s.gemini_model}.get(s.provider, s.model)

    def _set_enabled(self, on: bool) -> None:
        for box in (self.provider, self.effort, self.search):
            box.configure(state="readonly" if on else "disabled")
        claude = bool(self.comp) and self._settings().provider == "claude_code"
        self.model.configure(state=("readonly" if claude else "normal") if on else "disabled")

    def sync(self) -> None:
        """Show the saved settings (also after a change elsewhere, e.g. a key saved in Keys & sign-in)."""
        if not self.comp or self.busy:
            return
        s = self._settings()
        names = dict(self.PROVIDERS)
        if self.provider.get() != names.get(s.provider, s.provider):
            self.provider.set(names.get(s.provider, s.provider))
            self._set_enabled(True)
        # the key is looked up while there is none (it may be saved any moment), not every refresh
        if not self._choices_for or self._choices_for[0] != s.provider or not self._choices_for[1]:
            has_key = s.provider == "claude_code" or bool(config.read_key(s.provider))
            if self._choices_for != (s.provider, has_key):  # the list with prices needs the key
                self._choices_for = (s.provider, has_key)
                self._load_choices(s.provider)
        if not self._editing():
            self._show_model()
        for w in (self.effort, self.labels[self.effort]):  # only Claude Code has a thinking level
            w.grid() if s.provider == "claude_code" else w.grid_remove()
        self.effort.set(dict(self.efforts).get(s.claude_effort, s.claude_effort))
        self.search.set(dict(self.SEARCH).get(s.search_mode, s.search_mode))
        if time.time() > self._msg_until:
            self.msg.configure(text=self._hint(), fg=DIM)

    def _editing(self) -> bool:
        """The player is typing a model ID or has a drop-down list open: the boxes are not overwritten."""
        try:
            return self.frame.focus_get() is self.model
        except (KeyError, tk.TclError):  # the focus is in a drop-down list (Tk cannot name its popdown)
            return True

    def _hint(self) -> str:
        """Claude Code signed in but not used: say what it is good for (this was a card in the game before)."""
        info = claude_code._detect_cache[0] if claude_code._detect_cache else None
        if info and info.get("loggedIn") and self._settings().provider != "claude_code":
            return ("Claude Code is signed in: choose it as the provider to use your Claude subscription "
                    "(no per-token cost).")
        return ""

    def _show_model(self) -> None:
        mid = self._model_id()
        choice = next((c for c in self.choices if c["id"] == mid), None)
        self.model.set(choice["name"] if choice else mid)
        self.note.configure(text=(choice or {}).get("price") or "")

    def _load_choices(self, provider: str) -> None:
        comp = self.comp

        def run():
            try:
                choices = comp.service.model_choices(provider)
            except Exception as e:  # network: the list stays as it was
                log.warning("model list for %s: %s", provider, e)
                return
            self.results.put(("choices", provider, choices))
        threading.Thread(target=run, daemon=True).start()

    def _message(self, text: str, ok: bool) -> None:
        self.msg.configure(text=text, fg=ACCENT if ok else WARN)
        self._msg_until = time.time() + self.MESSAGE_S

    # ---------------------------------------------------------------- changes
    def _pick_provider(self) -> None:
        provider = self.PROVIDERS[self.provider.current()][0]
        if provider == self._settings().provider:
            return
        self._pick("set_provider", provider)

    def _pick_model(self) -> None:
        text = self.model.get().strip()
        by_name = {c["name"]: c["id"] for c in self.choices}
        model = by_name.get(text, text)
        if not model or model == self._model_id():
            self._show_model()
            return
        self._pick("set_model", model)

    def _pick(self, what: str, value: str) -> None:
        if not self.comp or self.busy:
            return
        self.busy = True
        self._set_enabled(False)
        self.msg.configure(text="Applying…", fg=DIM)
        service = self.comp.service

        def run():
            try:
                ok, text = getattr(service, what)(value)
            except Exception as e:
                log.exception("%s failed", what)
                ok, text = False, f"Could not change it: {str(e)[:120]}"
            self.results.put((what, value, (ok, text)))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                what, value, result = self.results.get_nowait()
                if what == "choices":
                    if self.comp and value == self._settings().provider:
                        self.choices = result
                        names = [c["name"] for c in result]
                        self.model.configure(values=names, height=min(10, max(1, len(names))))
                        if not self._editing():
                            self._show_model()
                    continue
                ok, text = result
                self.busy = False
                self._set_enabled(True)
                self._message(text, ok)
                if what == "set_provider" and not ok:
                    self.ask_key(value)  # no key yet / not signed in: where to fix it
                self.sync()
        except queue.Empty:
            pass
        try:
            self.frame.after(100, self._poll)
        except tk.TclError:  # the window is closed
            pass


class KeyDialog:
    """Connect an AI: Claude Code is checked first; without it the player enters an OpenRouter or a Google Gemini
    key, which is checked with the provider before it is saved. A signed-in Claude Code that is not in use can be
    switched back to."""

    LINKS = {"openrouter": "https://openrouter.ai/keys", "gemini": "https://aistudio.google.com/apikey"}

    def __init__(self, root: tk.Tk, comp, provider: str | None = None):
        from .gemini import KEY_URL
        self.LINKS = dict(self.LINKS, gemini=KEY_URL)
        self.comp = comp
        self.results: queue.Queue = queue.Queue()
        top = self.top = tk.Toplevel(root, bg=BG)
        top.title("ForeverAI — connect an AI")
        top.transient(root)
        top.resizable(False, False)
        pad = {"padx": 18}
        tk.Label(top, text="Connect an AI", bg=BG, fg=ACCENT, font=(UI, 13, "bold"), anchor="w").pack(
            fill="x", pady=(14, 4), **pad)
        self.claude_line = tk.Label(top, text=self._claude_text(), bg=BG, fg=DIM, font=(UI, 9), anchor="w",
                                    justify="left", wraplength=420)
        self.claude_line.pack(fill="x", **pad)
        self.claude_row = tk.Frame(top, bg=BG)
        self.claude_row.pack(fill="x", **pad)
        self.sign_in_btn = ttk.Button(self.claude_row, text="Sign in to Claude Code", command=self._sign_in)
        self.use_claude_btn = ttk.Button(self.claude_row, text="Use Claude Code", command=self._retry_claude)
        self._show_claude_button()
        tk.Label(top, text="Use an API key instead:", bg=BG, fg=FG, font=(UI, 10), anchor="w").pack(
            fill="x", pady=(12, 4), **pad)
        if provider not in self.LINKS:
            provider = "gemini" if comp.store.settings.provider == "gemini" else "openrouter"
        self.provider = tk.StringVar(value=provider)
        for value, text in (("openrouter", "OpenRouter — many models, pay per use"),
                            ("gemini", "Google Gemini — key from Google AI Studio")):
            row = tk.Frame(top, bg=BG)
            row.pack(fill="x", **pad)
            tk.Radiobutton(row, text=text, value=value, variable=self.provider, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG, font=(UI, 9), anchor="w",
                           highlightthickness=0).pack(side="left")
            link = tk.Label(row, text="Get a key ↗", bg=BG, fg=ACCENT, font=(UI, 9, "underline"), cursor="hand2")
            link.pack(side="right")
            link.bind("<Button-1>", lambda _e, v=value: open_path(self.LINKS[v]))
        # "*" and not "•": a Tk without fontconfig (core X fonts) draws "•" zero wide, so a pasted key looked empty
        self.entry = tk.Entry(top, show="*", width=52, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                              font=(MONO, 10))
        self.entry.pack(fill="x", pady=(8, 2), ipady=4, **pad)
        self.entry.bind("<Return>", lambda _e: self._save())
        menu = tk.Menu(top, tearoff=False)
        menu.add_command(label="Paste", command=lambda: self.entry.event_generate("<<Paste>>"))
        menu.add_command(label="Clear", command=lambda: self.entry.delete(0, "end"))
        self.entry.bind("<Button-3>", lambda e: (self.entry.focus_set(), menu.tk_popup(e.x_root, e.y_root)))
        self.msg = tk.Label(top, text="", bg=BG, fg=DIM, font=(UI, 9), anchor="w", justify="left", wraplength=420)
        self.msg.pack(fill="x", pady=(4, 0), **pad)
        buttons = tk.Frame(top, bg=BG)
        buttons.pack(fill="x", pady=(10, 16), **pad)
        self.save_btn = ttk.Button(buttons, text="Save key", command=self._save)
        self.save_btn.pack(side="left")
        self.retry_btn = ttk.Button(buttons, text="Check Claude Code again", command=self._retry_claude)
        self.retry_btn.pack(side="left", padx=8)
        ttk.Button(buttons, text="Later", command=self.close).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", self.close)
        self.entry.focus_set()
        top.after(100, self._poll)

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def close(self) -> None:
        if self.alive():
            self.top.destroy()

    @staticmethod
    def _claude_info() -> dict | None:
        return claude_code._detect_cache[0] if claude_code._detect_cache else None

    def _claude_text(self) -> str:
        info = self._claude_info()
        if info and info.get("loggedIn"):
            provider = self.comp.store.settings.provider
            if provider == "claude_code":
                return f"Claude Code {info.get('version') or ''} is signed in and in use — no key is needed."
            from .service import PROVIDER_NAMES
            return (f"Claude Code {info.get('version') or ''} is signed in, but ForeverAI uses "
                    f"{PROVIDER_NAMES.get(provider, provider)} now. Click \"Use Claude Code\" to switch back (no key "
                    "is needed).")
        if info:
            # the Claude desktop app signs in its own sessions only; the claude command keeps its own sign-in
            return ("Claude Code is installed but not signed in (signing in to the Claude desktop app does not sign "
                    "in Claude Code). Click \"Sign in to Claude Code\", finish in the browser, then click \"Check "
                    "Claude Code again\".")
        return "Claude Code was not found. With a Claude subscription, install Claude Code and sign in to use it."

    def _show_claude_button(self) -> None:
        """Installed but not signed in: "Sign in to Claude Code"; signed in but another provider in use: "Use Claude
        Code"."""
        info = self._claude_info()
        if info and not info.get("loggedIn"):
            self.sign_in_btn.pack(side="left", pady=(6, 0))
        else:
            self.sign_in_btn.pack_forget()
        if info and info.get("loggedIn") and self.comp.store.settings.provider != "claude_code":
            self.use_claude_btn.pack(side="left", pady=(6, 0))
        else:
            self.use_claude_btn.pack_forget()

    def _sign_in(self) -> None:
        info = self._claude_info()
        if info and open_terminal([info["path"], "auth", "login"]):
            self.msg.configure(text="Sign in in the terminal window and the browser, then click \"Check Claude Code "
                                    "again\".", fg=DIM)
        else:
            self.msg.configure(text="No terminal was found. Open one and run: claude auth login", fg=WARN)

    def _busy(self, text: str) -> None:
        self.msg.configure(text=text, fg=DIM)
        for b in (self.save_btn, self.retry_btn, self.use_claude_btn):
            b.state(["disabled"])

    def _save(self) -> None:
        key, provider = self.entry.get().strip(), self.provider.get()
        if not key:
            self.msg.configure(text="Paste the key first.", fg=WARN)
            return
        self._busy("Checking the key…")

        def run():
            ok, text = self.comp.service.save_key(provider, key, use=True)
            self.results.put(("key", ok, text))
        threading.Thread(target=run, daemon=True).start()

    def _retry_claude(self) -> None:
        self._busy("Checking Claude Code…")

        def run():  # the player asked for Claude Code: a signed-in one is used even when a key works too
            provider = self.comp.check_providers(prefer_claude=True)
            self.results.put(("claude", provider == "claude_code", provider))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self) -> None:
        if not self.alive():
            return
        try:
            kind, ok, value = self.results.get_nowait()
        except queue.Empty:
            self.top.after(100, self._poll)
            return
        for b in (self.save_btn, self.retry_btn, self.use_claude_btn):
            b.state(["!disabled"])
        self.claude_line.configure(text=self._claude_text())
        self._show_claude_button()
        if kind == "key":
            self.msg.configure(text=value, fg=ACCENT if ok else WARN)
            if ok:
                self.comp.needs_key = False
                self.entry.delete(0, "end")
                self.top.after(1200, self.close)
        elif ok:
            self.msg.configure(text="Using Claude Code.", fg=ACCENT)
            self.top.after(1200, self.close)
        elif value:
            from .service import PROVIDER_NAMES
            self.msg.configure(text=f"Claude Code is still not available. Using {PROVIDER_NAMES.get(value, value)}.",
                               fg=WARN)
        else:
            self.msg.configure(text="Claude Code is still not available. Enter a key instead.", fg=WARN)
        self.top.after(100, self._poll)
