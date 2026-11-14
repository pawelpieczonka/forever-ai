"""The log files of the app. They are written only when the player asks for them ("Save logs to files" in the app
window, off by default): the app's own log, the add-on's debug lines and the game data of the last question. The
Activity list in the window does not depend on it."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

# every file "Clear logs" removes (the app's log with the two older parts it keeps, the add-on's debug lines, the
# game data of the last question)
NAMES = ("foreverai.log", "foreverai.log.1", "foreverai.log.2", "debug.log", "debug.log.1", "last_question.json")
_handler: RotatingFileHandler | None = None
_on = False


def path() -> Path:
    return config.data_dir() / "foreverai.log"


def enabled() -> bool:
    return _on


def set_enabled(on: bool) -> None:
    """Start or stop writing the log files."""
    global _handler, _on
    logger = logging.getLogger("foreverai")
    if on and _handler is None:
        try:
            path().parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path(), maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        except OSError:
            _on = False
            return
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        _handler = handler
    elif not on and _handler is not None:
        logger.removeHandler(_handler)
        _handler.close()
        _handler = None
    _on = bool(on)


def clear() -> int:
    """Delete the log files; returns how many there were. Writing goes on afterwards as it was set."""
    was = _on
    set_enabled(False)  # the open file is closed first: an open one cannot be deleted on Windows
    removed = 0
    for name in NAMES:
        p = config.data_dir() / name
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except OSError:
            pass
    set_enabled(was)
    return removed
