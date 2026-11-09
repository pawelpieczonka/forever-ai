"""First-run and every-run setup of the game side: find WoW Forever, install/update the ForeverAI add-on, seed the
knowledge base and create a desktop shortcut. Works both from source and from the packaged app (Windows and Linux)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config
from .osutil import NO_WINDOW, WINDOWS, child_env, ci_child, ci_join

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")
_WOW_KEYS = (r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft",
             r"SOFTWARE\Blizzard Entertainment\World of Warcraft")


def bundle_root() -> Path:
    """Folder with the bundled files: the PyInstaller bundle, or the repository when running from source."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return config.REPO_ROOT


def addon_source() -> Path:
    return bundle_root() / "addon" / "ForeverAI"


def is_game_dir(d: Path | str | None) -> bool:
    return bool(d) and ci_child(Path(d), "Wow.exe").exists()


def normalize_game_dir(d: Path | str | None) -> Path | None:
    """Accept the `_forever_` folder or the `World of Warcraft` folder above it."""
    if not d:
        return None
    p = Path(str(d).strip().strip('"'))
    if is_game_dir(p):
        return p
    if is_game_dir(ci_child(p, "_forever_")):
        return ci_child(p, "_forever_")
    return None


def find_game_dir() -> Path | None:
    """Windows: the Battle.net registry entry first, then "World of Warcraft" at the root of every drive or one
    folder deep (Program Files, Games, ...). Linux: the same inside every Wine prefix (Wine, Lutris, Steam/Proton,
    Bottles, Heroic), then on mounted disks (e.g. a Windows partition) and in the home folder."""
    return _find_windows() if WINDOWS else _find_linux()


def _find_windows() -> Path | None:
    try:
        import winreg
        for key in _WOW_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                    install = winreg.QueryValueEx(k, "InstallPath")[0]
                found = normalize_game_dir(Path(install.rstrip("\\")).parent)
                if found:
                    return found
            except OSError:
                continue
    except ImportError:
        pass
    drives = [Path(f"{c}:\\") for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").exists()]
    return _scan_roots(drives, normalize_game_dir)


def _scan_roots(roots: list[Path], accept) -> Path | None:
    """"World of Warcraft" directly in each root or one folder below it."""
    for root in roots:
        found = accept(root / "World of Warcraft")
        if found:
            return found
        try:
            tops = sorted(root.iterdir())
        except OSError:
            continue
        for top in tops:
            try:
                found = top.is_dir() and accept(top / "World of Warcraft")
            except OSError:
                continue
            if found:
                return found
    return None


# ---------------------------------------------------------------- Linux (the game runs under Wine / Proton)
def _forever_dir(d: Path) -> Path | None:
    """`d` when it is the `_forever_` folder, or the `_forever_` folder inside it. Stricter than normalize_game_dir:
    a Wine prefix often holds other WoW clients (private servers) with Wow.exe right in "World of Warcraft"."""
    if d.name.lower() == "_forever_" and is_game_dir(d):
        return d
    f = ci_child(d, "_forever_")
    return f if is_game_dir(f) else None


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def _lutris_prefixes() -> list[Path]:
    """Wine prefixes of the games set up in Lutris (native and Flatpak)."""
    home = Path.home()
    dirs = [_xdg("XDG_CONFIG_HOME", ".config") / "lutris" / "games",
            _xdg("XDG_DATA_HOME", ".local/share") / "lutris" / "games",
            home / ".var/app/net.lutris.Lutris/config/lutris/games",
            home / ".var/app/net.lutris.Lutris/data/lutris/games"]
    out: list[Path] = []
    for d in dirs:
        try:
            files = sorted(d.glob("*.yml"))
        except OSError:
            continue
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"^\s*prefix:\s*['\"]?(.+?)['\"]?\s*$", text, re.M):
                out.append(Path(m.group(1)).expanduser())
    return out


def wine_prefixes() -> list[Path]:
    """Every Wine prefix in the usual places, most likely first: $WINEPREFIX, Lutris, ~/.wine, then the folders of
    Lutris, Heroic, Bottles and Steam (Proton)."""
    home = Path.home()
    data = _xdg("XDG_DATA_HOME", ".local/share")
    cands: list[Path] = []
    if os.environ.get("WINEPREFIX"):
        cands.append(Path(os.environ["WINEPREFIX"]).expanduser())
    cands += _lutris_prefixes()
    cands.append(home / ".wine")
    for base, pattern in ((home / "Games", "*"),
                          (home / "Games" / "Heroic" / "Prefixes", "*/*"),
                          (data / "bottles" / "bottles", "*"),
                          (home / ".var/app/com.usebottles.bottles/data/bottles/bottles", "*"),
                          (data / "Steam" / "steamapps" / "compatdata", "*/pfx"),
                          (home / ".steam" / "steam" / "steamapps" / "compatdata", "*/pfx"),
                          (home / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/compatdata", "*/pfx")):
        try:
            cands += sorted(base.glob(pattern))
        except OSError:
            continue
    out, seen = [], set()
    for c in cands:
        try:
            if not (c / "drive_c").is_dir():
                continue
            key = c.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def wine_install_paths(prefix: Path) -> list[str]:
    """InstallPath of the Battle.net registry entries in a Wine prefix (system.reg is a text file)."""
    try:
        text = (Path(prefix) / "system.reg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for key in _WOW_KEYS:
        header = re.escape(key.replace("\\", "\\\\"))
        m = re.search(rf"^\[{header}\][^\n]*\n(.*?)(?=^\[|\Z)", text, re.I | re.M | re.S)
        if not m:
            continue
        v = re.search(r'^"InstallPath"="((?:[^"\\]|\\.)*)"', m.group(1), re.I | re.M)
        if v:
            out.append(re.sub(r"\\(.)", r"\1", v.group(1)))
    return out


def wine_to_unix(prefix: Path, win_path: str) -> Path | None:
    """C:\\Program Files (x86)\\... -> the matching folder in the prefix (drive letters from dosdevices)."""
    win_path = win_path.strip().rstrip("\\")
    if len(win_path) < 2 or win_path[1] != ":":
        return None
    letter = win_path[0].lower()
    drive = Path(prefix) / "dosdevices" / f"{letter}:"
    if drive.exists():
        drive = drive.resolve()  # c: -> ../drive_c, z: -> /
    elif letter == "c":
        drive = Path(prefix) / "drive_c"
    else:
        return None
    return ci_join(drive, *[p for p in win_path[2:].split("\\") if p])


def _mount_roots() -> list[Path]:
    user = os.environ.get("USER") or Path.home().name
    roots: list[Path] = []
    for base in (Path("/mnt"), Path("/media"), Path("/media") / user, Path("/run/media") / user):
        try:
            roots += sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
    return roots + [Path.home()]


def _find_linux() -> Path | None:
    prefixes = wine_prefixes()
    for prefix in prefixes:
        for install in wine_install_paths(prefix):
            p = wine_to_unix(prefix, install)
            found = p and (_forever_dir(p) or _forever_dir(p.parent))
            if found:
                return found
    for prefix in prefixes:
        found = _scan_roots([prefix / "drive_c"], _forever_dir)
        if found:
            return found
    return _scan_roots(_mount_roots(), _forever_dir)


# ---------------------------------------------------------------- add-on and knowledge base
def _tree_hash(folder: Path) -> str:
    h = hashlib.sha1()
    for f in sorted(folder.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(folder)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or bool(os.stat(p, follow_symlinks=False).st_file_attributes & 0x400)  # reparse point
    except (OSError, AttributeError):
        return False


def install_addon(addons_dir: Path) -> str:
    """Copy the bundled add-on into the game when it is missing or different. Returns what happened."""
    src = addon_source()
    dst = ci_child(Path(addons_dir), "ForeverAI")
    if not src.exists():
        return "missing-source"
    if _is_link(dst):
        return "linked"  # development: the add-on folder points at the repository
    want = _tree_hash(src)
    marker = dst / ".foreverai-build"
    if dst.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == want:
        return "unchanged"
    existed = dst.exists()
    if existed:
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    marker.write_text(want, encoding="utf-8")
    return "updated" if existed else "installed"


def seed_knowledge_base() -> bool:
    """Copy the bundled knowledge base on first run, so the first update only fetches what changed. From source the
    copy that ships with the prebuilt Windows app is used."""
    from .kb.sync import kb_path
    target = Path(kb_path())
    seeds = [bundle_root() / "seed" / "kb.sqlite"]
    if not getattr(sys, "frozen", False):
        seeds.append(config.REPO_ROOT / "release" / "ForeverAI" / "_internal" / "seed" / "kb.sqlite")
    seed = next((s for s in seeds if s.exists()), None)
    if target.exists() or seed is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed, target)
    return True


# ---------------------------------------------------------------- desktop shortcut
def app_command() -> tuple[str, str]:
    """(target, arguments) that start this app — the packaged app, or pythonw -m foreverai from source."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw if pyw.exists() else sys.executable), "-m foreverai"


def _app_workdir(target: str) -> str:
    return str(Path(target).parent if getattr(sys, "frozen", False) else config.REPO_ROOT / "companion")


def create_desktop_shortcut() -> Path | None:
    return _windows_shortcut() if WINDOWS else _linux_shortcut()


def _windows_shortcut() -> Path | None:
    target, args = app_command()
    workdir = _app_workdir(target)
    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'ForeverAI.lnk'));"
        f"$s.TargetPath='{target}';$s.Arguments='{args}';$s.WorkingDirectory='{workdir}';"
        "$s.Description='ForeverAI';"
        f"$s.IconLocation='{target},0';$s.Save();Join-Path $d 'ForeverAI.lnk'"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=30, creationflags=NO_WINDOW)
        path = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        return Path(path) if path else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def _desktop_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n")


def _desktop_exec_arg(a: str) -> str:
    """One argument of a .desktop Exec line (freedesktop quoting, then the string escapes)."""
    if a and not re.search(r"[\s\"'\\><~|&;$*?#()`%]", a):
        return a
    quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", a) + '"'
    return _desktop_string(quoted).replace("%", "%%")


def desktop_entry() -> str:
    target, args = app_command()
    exec_line = " ".join(_desktop_exec_arg(a) for a in [target, *args.split()])
    lines = ["[Desktop Entry]", "Type=Application", "Name=ForeverAI", "Comment=AI assistant for WoW Forever",
             f"Exec={exec_line}", f"Path={_desktop_string(_app_workdir(target))}"]
    icon = bundle_root() / "assets" / "foreverai.png"
    if icon.exists():
        lines.append(f"Icon={_desktop_string(str(icon))}")
    lines += ["Terminal=false", "Categories=Game;", "StartupWMClass=ForeverAI"]
    return "\n".join(lines) + "\n"


def desktop_folder() -> Path:
    """The desktop folder from ~/.config/user-dirs.dirs (it is localized, e.g. ~/Pulpit), else ~/Desktop."""
    home = Path.home()
    try:
        text = (_xdg("XDG_CONFIG_HOME", ".config") / "user-dirs.dirs").read_text(encoding="utf-8")
        m = re.search(r'^XDG_DESKTOP_DIR="(.*)"', text, re.M)
        if m:
            return Path(m.group(1).replace("$HOME", str(home)))
    except OSError:
        pass
    return home / "Desktop"


def _linux_shortcut() -> Path | None:
    """An application menu entry, plus a launcher on the desktop when there is a desktop folder."""
    entry = desktop_entry()
    menu = _xdg("XDG_DATA_HOME", ".local/share") / "applications" / "foreverai.desktop"
    try:
        menu.parent.mkdir(parents=True, exist_ok=True)
        menu.write_text(entry, encoding="utf-8")
    except OSError as e:
        log.warning("cannot write %s: %s", menu, e)
        return None
    desk = desktop_folder()
    if not desk.is_dir():
        return menu
    path = desk / "ForeverAI.desktop"
    try:
        path.write_text(entry, encoding="utf-8")
        path.chmod(0o755)  # KDE and GNOME only start executable launchers
    except OSError as e:
        log.warning("cannot write %s: %s", path, e)
        return menu
    if shutil.which("gio"):  # GNOME also wants the launcher marked as trusted
        try:
            subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"], env=child_env(),
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return path


def prepare_game(game_dir: Path, pool_size: int) -> dict:
    """Everything the game side needs; safe to run on every start."""
    from .inbox import ensure_pool
    paths = config.game_paths(game_dir)
    paths["addons"].mkdir(parents=True, exist_ok=True)
    paths["screenshots"].mkdir(parents=True, exist_ok=True)
    result = {"addon": install_addon(paths["addons"]), "helpers": ensure_pool(paths["addons"], pool_size)}
    result["seeded"] = seed_knowledge_base()
    return result
