"""What differs between Windows and Linux: hidden child processes, the environment they get, opening a folder and
case-insensitive paths (the game under Wine does not care about case, the Linux file system does)."""

from __future__ import annotations

import os
import shlex
import shutil
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


# Linux terminal emulators and what goes before the command to run; the user's $TERMINAL is tried first
TERMINALS = {"xdg-terminal-exec": [], "x-terminal-emulator": ["-e"], "konsole": ["-e"], "gnome-terminal": ["--"],
             "xfce4-terminal": ["-x"], "alacritty": ["-e"], "kitty": [], "foot": [], "wezterm": ["start", "--"],
             "xterm": ["-e"]}


def open_terminal(argv: list[str]) -> bool:
    """Run a command in a new terminal window the user can type into (Claude Code's sign-in). On Linux the window
    stays open until Enter is pressed, so an error can be read. False when no terminal emulator was found."""
    if WINDOWS:
        subprocess.Popen(argv, env=child_env(), creationflags=subprocess.CREATE_NEW_CONSOLE)
        return True
    command = ["sh", "-c", shlex.join(argv) + '; echo; printf "Press Enter to close this window. "; read _']
    names = list(TERMINALS)
    if os.environ.get("TERMINAL"):
        names.insert(0, os.environ["TERMINAL"])
    for name in names:
        exe = shutil.which(name)
        if exe:
            subprocess.Popen([exe, *TERMINALS.get(Path(name).name, ["-e"]), *command], env=child_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False


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
