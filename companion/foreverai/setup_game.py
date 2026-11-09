"""First-run and every-run setup of the game side: find WoW Forever, install/update the ForeverAI add-on, seed the
knowledge base and create a desktop shortcut. Works both from source and from the packaged ForeverAI.exe."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def bundle_root() -> Path:
    """Folder with the bundled files: the PyInstaller bundle, or the repository when running from source."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return config.REPO_ROOT


def addon_source() -> Path:
    return bundle_root() / "addon" / "ForeverAI"


def is_game_dir(d: Path | str | None) -> bool:
    return bool(d) and (Path(d) / "Wow.exe").exists()


def normalize_game_dir(d: Path | str | None) -> Path | None:
    """Accept the `_forever_` folder or the `World of Warcraft` folder above it."""
    if not d:
        return None
    p = Path(str(d).strip().strip('"'))
    if is_game_dir(p):
        return p
    if is_game_dir(p / "_forever_"):
        return p / "_forever_"
    return None


def find_game_dir() -> Path | None:
    """Battle.net registry entry first, then "World of Warcraft" at the root of every drive or one folder deep
    (Program Files, Games, ...)."""
    if os.name == "nt":
        try:
            import winreg
            for key in (r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft",
                        r"SOFTWARE\Blizzard Entertainment\World of Warcraft"):
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
    for root in drives:
        found = normalize_game_dir(root / "World of Warcraft")
        if found:
            return found
        try:
            for top in root.iterdir():
                if top.is_dir():
                    found = normalize_game_dir(top / "World of Warcraft")
                    if found:
                        return found
        except OSError:
            continue
    return None


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
    dst = Path(addons_dir) / "ForeverAI"
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
    """Copy the bundled knowledge base on first run, so the first update only fetches what changed."""
    from .kb.sync import kb_path
    target = Path(kb_path())
    seed = bundle_root() / "seed" / "kb.sqlite"
    if target.exists() or not seed.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seed, target)
    return True


def app_command() -> tuple[str, str]:
    """(target, arguments) that start this app — the .exe, or pythonw -m foreverai from source."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw if pyw.exists() else sys.executable), "-m foreverai"


def create_desktop_shortcut() -> Path | None:
    target, args = app_command()
    workdir = str(Path(target).parent if getattr(sys, "frozen", False) else config.REPO_ROOT / "companion")
    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'ForeverAI.lnk'));"
        f"$s.TargetPath='{target}';$s.Arguments='{args}';$s.WorkingDirectory='{workdir}';"
        "$s.Description='ForeverAI';"
        f"$s.IconLocation='{target},0';$s.Save();Join-Path $d 'ForeverAI.lnk'"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW)
        path = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
        return Path(path) if path else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def prepare_game(game_dir: Path, pool_size: int) -> dict:
    """Everything the game side needs; safe to run on every start."""
    from .inbox import ensure_pool
    paths = config.game_paths(game_dir)
    paths["addons"].mkdir(parents=True, exist_ok=True)
    paths["screenshots"].mkdir(parents=True, exist_ok=True)
    result = {"addon": install_addon(paths["addons"]), "helpers": ensure_pool(paths["addons"], pool_size)}
    result["seeded"] = seed_knowledge_base()
    return result
