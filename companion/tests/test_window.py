"""The AI settings in the app window (they were in the game before 0.6.0)."""

import gc
import time
import types

import pytest

from foreverai import claude_code, config

from test_service import FreeClient, env, free_env  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def collect_tk_garbage():
    """The Tk objects of a closed window are freed here, on the main thread. Left to a collection that happens to run in
    one of the background threads (the model lists load in threads), they were freed there and Tcl crashed the tests
    ("Fatal Python error: Illegal instruction")."""
    yield
    gc.collect()


@pytest.fixture
def tk_root():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()


def _pump(root, until, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if until():
            return True
        time.sleep(0.01)
    return False


def _choose(box, value):
    box.set(value)
    box.event_generate("<<ComboboxSelected>>")


def _shown(widget):
    return widget.winfo_manager() == "grid"


def test_ai_settings_panel_changes_the_app_settings(env, tk_root, monkeypatch):  # noqa: F811
    from foreverai import window
    svc, _, _ = env
    svc.prewarm_later = lambda: None
    monkeypatch.setattr(claude_code, "_detect_cache", None)
    asked = []
    panel = window.AISettings(tk_root, lambda provider=None: asked.append(provider))
    panel.frame.pack()
    assert str(panel.provider.cget("state")) == "disabled"  # nothing to change before the app is ready
    panel.attach(types.SimpleNamespace(store=svc.store, service=svc))
    s = svc.store.settings
    root = tk_root

    # OpenRouter: the models with their prices, no thinking level
    assert _pump(root, lambda: panel.choices and panel.note.cget("text"))
    assert panel.provider.get() == "OpenRouter" and panel.model.get() == "openai/gpt-6-luna"
    assert panel.note.cget("text") == "$0.10 / $0.50 per 1M tokens" and panel.search.get() == "Auto"
    assert "deepseek/deepseek-v4-flash" in panel.model.cget("values")
    assert not _shown(panel.effort) and str(panel.model.cget("state")) == "normal"

    _choose(panel.model, "deepseek/deepseek-v4-flash")
    assert _pump(root, lambda: not panel.busy and "Model:" in panel.msg.cget("text"))
    assert s.model == "deepseek/deepseek-v4-flash" and panel.note.cget("text") == "$0.09 / $0.18 per 1M tokens"
    # another model typed by its ID: an unknown one changes nothing
    panel.model.set("nope/model")
    assert panel.model.bind("<Return>")  # Enter applies it (a withdrawn window cannot take the key focus here)
    panel._pick_model()
    assert _pump(root, lambda: not panel.busy and "does not exist" in panel.msg.cget("text"))
    assert s.model == "deepseek/deepseek-v4-flash" and panel.msg.cget("fg") == window.WARN

    _choose(panel.search, "Off")
    assert _pump(root, lambda: not panel.busy and s.search_mode == "never")
    assert panel.msg.cget("text") == "Web search: off"

    # a provider without a key: Keys & sign-in opens for it, the box shows the provider in use again
    _choose(panel.provider, "Google Gemini")
    assert _pump(root, lambda: asked == ["gemini"])
    assert s.provider == "openrouter" and panel.provider.get() == "OpenRouter" and "No Google Gemini key" in panel.msg.cget("text")

    # Claude Code signed in but not used: the hint the game showed as a card is here now
    info = {"path": "/opt/claude", "version": "2", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    monkeypatch.setattr(claude_code, "_detect_cache", (info, time.time()))
    panel._msg_until = 0
    panel.sync()
    assert "Claude Code is signed in" in panel.msg.cget("text")
    _choose(panel.provider, "Claude Code (subscription)")
    assert _pump(root, lambda: not panel.busy and s.provider == "claude_code")
    assert _pump(root, lambda: panel.model.get() == "Haiku 5.5")
    assert _shown(panel.effort) and panel.effort.get() == "high" and str(panel.model.cget("state")) == "readonly"
    assert panel.note.cget("text") == ""
    _choose(panel.model, "Sonnet 5.5")
    assert _pump(root, lambda: not panel.busy and s.claude_model == "claude-sonnet-5-5")
    _choose(panel.effort, "very high")
    assert _pump(root, lambda: not panel.busy and s.claude_effort == "xhigh")
    panel._msg_until = 0
    panel.sync()
    assert panel.msg.cget("text") == ""  # in use: no hint

    # changed elsewhere (e.g. a key saved in Keys & sign-in switches to its provider): the panel follows
    svc.store.update(provider="openrouter")
    panel.sync()
    assert _pump(root, lambda: panel.model.get() == "deepseek/deepseek-v4-flash")
    assert panel.provider.get() == "OpenRouter" and not _shown(panel.effort)


def _texts(widget):
    out = []
    for c in widget.winfo_children():
        try:
            out.append(str(c.cget("text")))
        except Exception:  # no text option
            pass
        out += _texts(c)
    return out


def test_free_models_in_the_ai_settings(free_env, tk_root, monkeypatch):  # noqa: F811
    """The free OpenRouter models are in the model list; a free one shows the requests left today, and the panel says
    that web search is paid with it."""
    from foreverai import window
    svc, _, _ = free_env
    svc.prewarm_later = lambda: None
    monkeypatch.setattr(claude_code, "_detect_cache", None)
    panel = window.AISettings(tk_root, lambda provider=None: None)
    panel.frame.pack()
    panel.attach(types.SimpleNamespace(store=svc.store, service=svc))
    s = svc.store.settings
    assert _pump(tk_root, lambda: panel.choices and panel.note.cget("text"))
    values = list(panel.model.cget("values"))
    assert values[-2:] == ["acme/new-model:free", "openrouter/free"] and "google/gemma-4-31b-it:free" in values
    assert int(panel.model.cget("height")) == len(values) == 11  # up to 15 rows, then the drop-down scrolls
    assert panel.msg.cget("text") == ""  # a paid model: no word about free models
    _choose(panel.model, "google/gemma-4-31b-it:free")
    assert _pump(tk_root, lambda: not panel.busy and s.model == "google/gemma-4-31b-it:free")
    assert panel.note.cget("text") == "Free · 37 of 50 requests left today"
    panel._msg_until = 0
    panel.sync()
    assert "web search is paid per search" in panel.msg.cget("text") and "$0.10" in panel.msg.cget("text")
    _choose(panel.search, "Off")
    assert _pump(tk_root, lambda: not panel.busy and s.search_mode == "never")
    panel._msg_until = 0
    panel.sync()
    assert panel.msg.cget("text") == ""


def test_app_window_shows_the_ai_settings(env, monkeypatch):  # noqa: F811
    """The whole window with the panel refreshes without errors; the key dialog opens from the panel."""
    import tkinter as tk

    from foreverai import window
    svc, _, tmp = env
    svc.prewarm_later = lambda: None
    monkeypatch.setattr(claude_code, "_detect_cache", None)
    try:
        w = window.AppWindow()
    except tk.TclError:
        pytest.skip("no display")
    try:
        w.root.withdraw()
        kb = types.SimpleNamespace(meta=lambda k: "2026-11-01", count_pages=lambda: 120)
        from foreverai.updates import Updater
        updater = Updater(svc.store)
        updater.state, updater.checked_at = "current", time.time()
        comp = types.SimpleNamespace(paths={"game": tmp}, store=svc.store, service=svc, files_replaced=lambda: False,
                                     needs_key=False, updater=updater, claude_checked=True, kb=kb, syncing=False,
                                     sync_now=lambda force: None, stop=lambda: None)
        w.attach(comp)
        w.root.after(10, w._refresh)
        assert _pump(w.root, lambda: w.rows["ai"].cget("text").startswith("OpenRouter"))
        assert _pump(w.root, lambda: w.ai.model.get() == "openai/gpt-6-luna" and w.ai.choices)
        texts = _texts(w.root)
        assert "AI settings" in texts and "Keys & sign-in…" in texts
        assert "AI provider…" not in texts  # the old button: Keys & sign-in… in the AI settings replaces it
        w.ai.keys_btn.invoke()
        assert w._key_dialog and w._key_dialog.alive() and w._key_dialog.provider.get() == "openrouter"
        w.ask_key("gemini")  # already open: switched to the provider asked for
        assert w._key_dialog.provider.get() == "gemini"
        w._key_dialog.close()
        w.ask_key("gemini")
        assert w._key_dialog.provider.get() == "gemini"
        w._key_dialog.close()
    finally:
        w.root.destroy()


def test_the_updates_row_follows_the_updater(env, monkeypatch):  # noqa: F811
    """New versions of the app: the row shows the updater's state with its button, the box under it switches automatic
    updates, and the window quits once the new version's installer runs."""
    import tkinter as tk

    from foreverai import window
    from foreverai.updates import Updater
    svc, _, tmp = env
    svc.prewarm_later = lambda: None
    monkeypatch.setattr(claude_code, "_detect_cache", None)
    try:
        w = window.AppWindow()
    except tk.TclError:
        pytest.skip("no display")
    stopped = []
    try:
        w.root.withdraw()
        updater = Updater(svc.store)
        pressed = []
        monkeypatch.setattr(updater, "press", lambda: pressed.append(updater.state))
        kb = types.SimpleNamespace(meta=lambda k: "2026-11-01", count_pages=lambda: 120)
        comp = types.SimpleNamespace(paths={"game": tmp}, store=svc.store, service=svc, files_replaced=lambda: False,
                                     needs_key=False, updater=updater, claude_checked=True, kb=kb, syncing=False,
                                     sync_now=lambda force: None, stop=lambda: stopped.append(True))
        w.attach(comp)
        row, button = w.rows["updates"], w.app_btn
        texts = _texts(w.root)
        assert "Updates" in texts and "Install updates automatically" in texts
        assert "Download update" not in " ".join(texts)  # the old button at the bottom
        assert w.auto_update.get() is True

        updater.state, updater.checked_at = "current", time.time()
        w._show_update()
        w.root.update()
        assert row.cget("text").startswith("Up to date · checked ") and button.cget("text") == "Check now"
        assert button.winfo_manager() == "grid" and button.instate(["!disabled"])
        assert button.grid_info()["row"] == row.master.grid_info()["row"]  # in the line of the Updates row
        button.invoke()
        assert pressed == ["current"]

        updater.state, updater.release, updater.done, updater.total = "downloading", {"version": "9.9.0"}, 30, 120
        w._show_update()
        assert row.cget("text") == "Downloading ForeverAI 9.9.0… 25%" and button.instate(["disabled"])
        updater.state = "ready"
        w._show_update()
        assert row.cget("text") == "ForeverAI 9.9.0 is ready: it is installed when you quit ForeverAI"
        assert button.cget("text") == "Restart now" and button.instate(["!disabled"])
        updater.state, updater.error = "failed", "the download failed (timed out)"
        w._show_update()
        assert row.cget("fg") == window.WARN and button.cget("text") == "Try again"
        assert row.cget("text") == "The update to 9.9.0 failed: the download failed (timed out)"

        # automatic updates off: saved in the app's settings, and the row says what is left to do
        toggled = []
        monkeypatch.setattr(updater, "toggled", lambda: toggled.append(svc.store.settings.auto_update))
        w.auto_box.invoke()
        assert svc.store.settings.auto_update is False and toggled == [False]
        assert config.SettingsStore(svc.store.path).settings.auto_update is False  # on disk
        updater.state = "ready"
        w._show_update()
        assert row.cget("text") == "ForeverAI 9.9.0 is ready to install"
        w.auto_box.invoke()
        assert svc.store.settings.auto_update is True and toggled == [False, True]

        # from source: no button and no box
        updater.state = "source"
        w._show_update()
        w.root.update()
        assert button.winfo_manager() == "" and w.auto_box.winfo_manager() == ""
        assert row.cget("text") == "Runs from source: new versions come with git pull"

        # the installer runs and waits for this app: the window quits
        updater.state, updater.exit_requested = "installing", True
        w._refresh()
        assert stopped == [True]
        with pytest.raises(tk.TclError):
            w.root.winfo_exists()
    finally:
        try:
            w.root.destroy()
        except tk.TclError:
            pass


def test_key_dialog_preselects_the_provider(tk_root):
    from foreverai import window
    comp = types.SimpleNamespace(store=types.SimpleNamespace(settings=config.Settings(game_dir="x", provider="claude_code")))
    d = window.KeyDialog(tk_root, comp, "gemini")
    assert d.provider.get() == "gemini"
    d.close()
    d = window.KeyDialog(tk_root, comp, "claude_code")  # no key for Claude Code: the usual choice
    assert d.provider.get() == "openrouter"
    d.close()


def test_the_model_for_route_updates_is_chosen_in_the_app(env, tk_root, monkeypatch):  # noqa: F811
    from foreverai import window
    svc, _, _ = env
    svc.prewarm_later = lambda: None
    monkeypatch.setattr(claude_code, "_detect_cache", None)
    panel = window.AISettings(tk_root, lambda provider=None: None)
    panel.frame.pack()
    panel.attach(types.SimpleNamespace(store=svc.store, service=svc))
    s = svc.store.settings
    assert _pump(tk_root, lambda: panel.choices and len(panel.route.cget("values")) > 1)
    assert panel.route.get() == "Same as questions" and panel.route.cget("values")[0] == "Same as questions"
    assert str(panel.route.cget("state")) == "readonly"
    _choose(panel.route, "deepseek/deepseek-v4-flash")
    assert _pump(tk_root, lambda: not panel.busy and "Route updates:" in panel.msg.cget("text"))
    assert s.route_model == {"openrouter": "deepseek/deepseek-v4-flash"} and panel.route.get() == "deepseek/deepseek-v4-flash"
    _choose(panel.route, "Same as questions")
    assert _pump(tk_root, lambda: not panel.busy and s.route_model == {})
    assert panel.route.get() == "Same as questions"


def test_the_thinking_level_of_route_updates_is_next_to_their_model(env, tk_root, monkeypatch):  # noqa: F811
    from foreverai import window
    svc, _, _ = env
    svc.prewarm_later = lambda: None
    monkeypatch.setattr(claude_code, "_detect_cache", None)
    panel = window.AISettings(tk_root, lambda provider=None: None)
    panel.frame.pack()
    panel.attach(types.SimpleNamespace(store=svc.store, service=svc))
    s = svc.store.settings
    assert _pump(tk_root, lambda: panel.choices and len(panel.route.cget("values")) > 1)
    assert not panel.route_effort.winfo_manager()  # only Claude Code has a thinking level

    info = {"path": "/opt/claude", "version": "2", "loggedIn": True, "subscription": "max"}
    monkeypatch.setattr(claude_code, "detect", lambda force=False: info)
    monkeypatch.setattr(claude_code, "_detect_cache", (info, time.time()))
    _choose(panel.provider, "Claude Code (subscription)")
    assert _pump(tk_root, lambda: not panel.busy and s.provider == "claude_code")
    assert _pump(tk_root, lambda: panel.route_effort.winfo_manager() != "")
    # in the line of the model for route updates, right after it
    assert panel.route_effort.master.grid_info()["row"] == panel.route.grid_info()["row"]
    assert panel.route_effort.get() == "Same thinking" and panel.route_effort.cget("values")[0] == "Same thinking"
    assert "very high" in panel.route_effort.cget("values")
    _choose(panel.route_effort, "low")
    assert _pump(tk_root, lambda: not panel.busy and s.route_effort == "low")
    assert panel.msg.cget("text") == "Route updates: thinking low" and panel.route_effort.get() == "low"
    assert s.claude_effort == "high" and panel.effort.get() == "high"  # the thinking level of questions stays
    _choose(panel.route_effort, "Same thinking")
    assert _pump(tk_root, lambda: not panel.busy and s.route_effort == "")
    assert panel.route_effort.get() == "Same thinking"


def test_the_window_shows_the_app_version_next_to_its_title():
    import tkinter as tk

    from foreverai import __version__, window
    try:
        w = window.AppWindow()
    except tk.TclError:
        pytest.skip("no display")
    try:
        w.root.withdraw()
        header = w.version.master.pack_slaves()
        assert [str(s.cget("text")) for s in header[:2]] == ["ForeverAI", f"v{__version__}"]
        assert w.version.cget("fg") == window.DIM  # small and dimmed, not a second title
        assert w.version.cget("font") != header[0].cget("font")
    finally:
        w.root.destroy()
