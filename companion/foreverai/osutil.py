"""What differs between Windows and Linux: hidden child processes, the environment they get, opening a folder and
case-insensitive paths (the game under Wine does not care about case, the Linux file system does)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW: no console flashes up for child processes


def child_env(**extra: str) -> dict[str, str]:
    """Environment for child processes. The packaged Linux app points LD_LIBRARY_PATH at its own bundled libraries;
    programs it starts (claude, xdg-open) get the user's original value back."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = orig
    env.update(extra)
    return env


def open_path(path: Path | str) -> None:
    """Open a folder or file in the system file manager / default app."""
    if WINDOWS:
        os.startfile(str(path))
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], env=child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def ci_child(parent: Path, name: str) -> Path:
    """parent/name, matching an existing entry regardless of case (Interface\\AddOns may be Interface/Addons on a
    Linux disk); parent/name as given when there is no such entry."""
    p = parent / name
    if WINDOWS or p.exists():
        return p
    low = name.lower()
    try:
        with os.scandir(parent) as it:
            for e in it:
                if e.name.lower() == low:
                    return parent / e.name
    except OSError:
        pass
    return p


def ci_join(base: Path, *parts: str) -> Path:
    p = Path(base)
    for part in parts:
        p = ci_child(p, part)
    return p
