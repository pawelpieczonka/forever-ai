"""API keys in the operating system's credential store: Windows Credential Manager, or the Secret Service on Linux
(GNOME Keyring, KWallet, KeePassXC). Without one (a Linux session with no keyring running) config keeps the keys in the
.env file in the data folder, readable only by the user."""

from __future__ import annotations

import logging
import threading

from .osutil import WINDOWS

log = logging.getLogger("foreverai")

SERVICE = "ForeverAI"
NAME = "Windows Credential Manager" if WINDOWS else "the system keyring"

_lock = threading.Lock()
_state: dict = {"backend": None, "checked": False}
_cache: dict[str, str | None] = {}  # only this app writes its entries: each one is read from the store once


def _load():
    # the backend is picked explicitly (not through keyring's entry points): the bundled app then contains exactly it,
    # and no plain-text or "fail" keyring from another package can be chosen instead
    if WINDOWS:
        from keyring.backends.Windows import WinVaultKeyring as Backend
    else:
        from keyring.backends.SecretService import Keyring as Backend
    Backend.priority  # raises when the store cannot be used (no Secret Service running, no D-Bus session)
    return Backend()


def _backend():
    if not _state["checked"]:
        _state["checked"] = True
        try:
            _state["backend"] = _load()
        except Exception as e:
            log.warning("no system credential store (%s): API keys stay in the .env file", e)
    return _state["backend"]


def _give_up(action: str, e: Exception) -> None:
    # a locked keyring whose unlock prompt the player dismissed must not prompt again on every read
    log.warning("cannot %s %s (%s): API keys stay in the .env file until ForeverAI restarts", action, NAME, e)
    _state["backend"] = None


def available() -> bool:
    with _lock:
        return _backend() is not None


def get(name: str) -> str | None:
    """The stored value; None when there is none or the store cannot be used."""
    with _lock:
        if name in _cache:
            return _cache[name]
        kr = _backend()
        if kr is None:
            return None
        try:
            value = kr.get_password(SERVICE, name)
        except Exception as e:
            _give_up("read from", e)
            return None
        _cache[name] = value
        return value


def put(name: str, value: str) -> bool:
    """Store the value; True only when the store holds it afterwards."""
    with _lock:
        kr = _backend()
        if kr is None:
            return False
        try:
            kr.set_password(SERVICE, name, value)
            stored = kr.get_password(SERVICE, name)
        except Exception as e:
            _give_up("write to", e)
            return False
        if stored != value:
            log.warning("%s did not keep %s: it stays in the .env file", NAME, name)
            return False
        _cache[name] = value
        return True


def reset() -> None:
    """Forget the store and the cache: the next use checks the store again."""
    with _lock:
        _state.update(backend=None, checked=False)
        _cache.clear()
