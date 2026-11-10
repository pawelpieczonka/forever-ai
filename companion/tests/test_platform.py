import os
import sys

import pytest

from foreverai import claude_code, config, osutil, setup_game

linux_only = pytest.mark.skipif(os.name == "nt", reason="Linux paths")

SYSTEM_REG = r"""WINE REGISTRY Version 2
;; All keys relative to \\Machine

[Software\\Wow6432Node\\Blizzard Entertainment\\Battle.net\\Capabilities] 1793750998
"ApplicationName"="Battle.net"

[Software\\Wow6432Node\\Blizzard Entertainment\\World of Warcraft] 1793750998
#time=1dd6bf0d3e1db6a
"InstallPath"="C:\\Program Files (x86)\\World of Warcraft\\_forever_\\"
"GamePath"="C:\\Program Files (x86)\\World of Warcraft\\_forever_\\Wow.exe"

[Software\\Wow6432Node\\Microsoft] 1793750998
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "WINEPREFIX", "FOREVERAI_DATA"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def make_prefix(root, game_parent="Program Files (x86)", reg=SYSTEM_REG):
    """A minimal Wine prefix with WoW Forever installed."""
    (root / "drive_c" / game_parent / "World of Warcraft" / "_forever_").mkdir(parents=True)
    (root / "drive_c" / game_parent / "World of Warcraft" / "_forever_" / "Wow.exe").write_bytes(b"MZ")
    (root / "dosdevices").mkdir()
    (root / "dosdevices" / "c:").symlink_to("../drive_c")
    (root / "system.reg").write_text(reg, encoding="utf-8")
    return root / "drive_c" / game_parent / "World of Warcraft" / "_forever_"


def test_wine_install_paths_reads_battlenet_entry(tmp_path):
    (tmp_path / "system.reg").write_text(SYSTEM_REG, encoding="utf-8")
    assert setup_game.wine_install_paths(tmp_path) == [r"C:\Program Files (x86)\World of Warcraft\_forever_" + "\\"]
    assert setup_game.wine_install_paths(tmp_path / "missing") == []


@linux_only
def test_wine_to_unix_maps_drive_letters_and_case(tmp_path):
    (tmp_path / "drive_c" / "Program Files (x86)" / "World of Warcraft").mkdir(parents=True)
    (tmp_path / "games").mkdir()
    (tmp_path / "dosdevices").mkdir()
    (tmp_path / "dosdevices" / "c:").symlink_to("../drive_c")
    (tmp_path / "dosdevices" / "d:").symlink_to(tmp_path / "games")
    got = setup_game.wine_to_unix(tmp_path, r"C:\PROGRAM FILES (X86)\World of Warcraft\_forever_" + "\\")
    assert got == (tmp_path / "drive_c").resolve() / "Program Files (x86)" / "World of Warcraft" / "_forever_"
    assert setup_game.wine_to_unix(tmp_path, r"D:\WoW") == (tmp_path / "games").resolve() / "WoW"
    assert setup_game.wine_to_unix(tmp_path, r"E:\WoW") is None  # no such drive in the prefix
    assert setup_game.wine_to_unix(tmp_path, "relative") is None


@linux_only
def test_find_game_dir_from_wineprefix_registry(home, monkeypatch):
    game = make_prefix(home / "wow-prefix")
    monkeypatch.setenv("WINEPREFIX", str(home / "wow-prefix"))
    assert setup_game.find_game_dir() == game.resolve()


@linux_only
def test_find_game_dir_in_lutris_prefix_without_registry(home):
    game = make_prefix(home / "elsewhere" / "battlenet", game_parent="Games", reg="WINE REGISTRY Version 2\n")
    (home / ".config" / "lutris" / "games").mkdir(parents=True)
    (home / ".config" / "lutris" / "games" / "battlenet-1.yml").write_text(
        f"game:\n  exe: drive_c/Battle.net/Battle.net Launcher.exe\n  prefix: {home / 'elsewhere' / 'battlenet'}\n")
    assert setup_game.wine_prefixes()[0] == home / "elsewhere" / "battlenet"
    assert setup_game.find_game_dir() == game


@linux_only
def test_private_server_client_is_not_taken_for_forever(home):
    """A 3.3.5 client keeps Wow.exe right in "World of Warcraft"; only a `_forever_` folder counts."""
    old = home / ".wine" / "drive_c" / "World of Warcraft"
    old.mkdir(parents=True)
    (old / "Wow.exe").write_bytes(b"MZ")
    assert setup_game._forever_dir(old) is None
    assert setup_game.normalize_game_dir(old) == old  # picking it by hand is still allowed


@linux_only
def test_game_paths_follow_existing_case(tmp_path):
    (tmp_path / "interface" / "Addons").mkdir(parents=True)
    paths = config.game_paths(tmp_path)
    assert paths["addons"] == tmp_path / "interface" / "Addons"
    assert paths["screenshots"] == tmp_path / "Screenshots"  # missing: the game's own spelling


@linux_only
def test_data_dir_is_xdg(home, monkeypatch):
    monkeypatch.setattr(config, "dev_mode", lambda: False)
    assert config.data_dir() == home / ".local" / "share" / "foreverai"
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    assert config.data_dir() == home / "xdg" / "foreverai"


@linux_only
def test_desktop_entry_quotes_paths(monkeypatch):
    monkeypatch.setattr(setup_game, "app_command", lambda: ("/home/me/My Games/ForeverAI/ForeverAI", ""))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    entry = setup_game.desktop_entry()
    assert 'Exec="/home/me/My Games/ForeverAI/ForeverAI"\n' in entry
    assert "Path=/home/me/My Games/ForeverAI\n" in entry
    assert "StartupWMClass=ForeverAI" in entry
    assert setup_game._desktop_exec_arg('a"$b') == r'"a\\"\\$b"'
    assert setup_game._desktop_exec_arg("50%") == '"50%%"'
    assert setup_game._desktop_exec_arg("-m") == "-m"


@linux_only
def test_linux_shortcut_uses_localized_desktop(home):
    (home / ".config").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Pulpit"\n')
    (home / "Pulpit").mkdir()
    path = setup_game.create_desktop_shortcut()
    assert path == home / "Pulpit" / "ForeverAI.desktop"
    assert os.access(path, os.X_OK)
    assert (home / ".local" / "share" / "applications" / "foreverai.desktop").read_text() == path.read_text()


@linux_only
def test_claude_found_outside_path(home, monkeypatch):
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    assert claude_code._find_listed() is None
    for d in ("v18.20.0", "v22.3.0"):
        (home / ".nvm" / "versions" / "node" / d / "bin").mkdir(parents=True)
        exe = home / ".nvm" / "versions" / "node" / d / "bin" / "claude"
        exe.write_text("#!/usr/bin/env node\n")
        exe.chmod(0o755)
    assert claude_code._find_listed() == str(home / ".nvm/versions/node/v22.3.0/bin/claude")
    env = claude_code._env(claude_code._find_listed())
    assert env["PATH"].split(os.pathsep)[0] == str(home / ".nvm/versions/node/v22.3.0/bin")


@linux_only
def test_claude_desktop_app_copy_newest_first(home, monkeypatch):
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    for ver in ("2.1.9", "2.1.295"):
        d = home / ".config" / "Claude" / "claude-code" / ver / "abc"
        d.mkdir(parents=True)
        (d / "claude").write_text("")
    assert claude_code.candidate_executables() == [
        str(home / ".config/Claude/claude-code/2.1.295/abc/claude"),
        str(home / ".config/Claude/claude-code/2.1.9/abc/claude")]


def test_child_env_restores_library_path_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/opt/lib")
    assert osutil.child_env(X="1")["LD_LIBRARY_PATH"] == "/opt/lib"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    env = osutil.child_env()
    assert "LD_LIBRARY_PATH" not in env and "LD_LIBRARY_PATH_ORIG" not in env


@linux_only
def test_open_terminal_runs_the_sign_in_in_a_terminal(monkeypatch):
    started = []
    monkeypatch.setattr(osutil.subprocess, "Popen", lambda argv, **kw: started.append(argv))
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(osutil.shutil, "which", lambda n: "/usr/bin/konsole" if n == "konsole" else None)
    assert osutil.open_terminal(["/home/me/My Apps/claude", "auth", "login"])
    exe, flag, sh, c, script = started[0]
    assert (exe, flag, sh, c) == ("/usr/bin/konsole", "-e", "sh", "-c")
    assert script.startswith("'/home/me/My Apps/claude' auth login;") and "read _" in script
    # the user's $TERMINAL first, with the arguments that terminal needs
    monkeypatch.setenv("TERMINAL", "kitty")
    monkeypatch.setattr(osutil.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert osutil.open_terminal(["claude", "auth", "login"]) and started[1][:2] == ["/usr/bin/kitty", "sh"]
    monkeypatch.setattr(osutil.shutil, "which", lambda n: None)
    assert not osutil.open_terminal(["claude", "auth", "login"])


def test_thread_error_goes_to_the_log(caplog):
    import threading

    from foreverai import app

    def crash():
        raise RuntimeError("boom")
    old = threading.excepthook
    threading.excepthook = app._log_thread_error
    try:
        t = threading.Thread(target=crash, name="screenshot-watcher")
        t.start()
        t.join()
    finally:
        threading.excepthook = old
    rec = next(r for r in caplog.records if "screenshot-watcher stopped" in r.getMessage())
    assert rec.levelname == "ERROR" and "RuntimeError: boom" in rec.getMessage() and rec.exc_info


def test_replaced_app_files_ask_for_a_restart(tmp_path, monkeypatch):
    """A new build or version unpacked over the running app: the window asks for a restart."""
    from types import SimpleNamespace

    from foreverai import app
    exe = tmp_path / "ForeverAI-Linux"
    exe.write_bytes(b"old")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    comp = SimpleNamespace(_exe=app._exe_id(), _replaced=False)
    assert app.Companion.files_replaced(comp) is False
    exe.unlink()
    exe.write_bytes(b"new build")  # a new file, as PyInstaller and tar write it (it may get the old inode back)
    assert app.Companion.files_replaced(comp) is True
    monkeypatch.setattr(sys, "frozen", False)
    assert app._exe_id() is None  # from source nothing is checked
