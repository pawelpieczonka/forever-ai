"""Paths, .env key handling and persistent settings."""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .gemini import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from .osutil import WINDOWS, ci_join

REPO_ROOT = Path(__file__).resolve().parents[2]
# arena 2026-11-08: gpt-6-luna 5/5 facts, cheapest and ~4x faster than deepseek-v4-flash (4/5, slowest)
DEFAULT_MODEL = "openai/gpt-6-luna"
FAVORITE_MODELS = [
    "openai/gpt-6-luna",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4.1-flash",
    "anthropic/claude-haiku-5.5",
    "z-ai/glm-5.3-flash",
    "xiaomi/mimo-v2.6-flash",
    "google/gemini-3.5-flash-lite",
]


def dev_mode() -> bool:
    """Running from a source checkout (data stays in companion/data); the packaged app never is."""
    if getattr(sys, "frozen", False):
        return False
    return (REPO_ROOT / ".git").exists() or (REPO_ROOT / ".env").exists()


def data_dir() -> Path:
    if os.environ.get("FOREVERAI_DATA"):
        d = Path(os.environ["FOREVERAI_DATA"])
    elif dev_mode():
        d = REPO_ROOT / "companion" / "data"
    elif WINDOWS:
        d = Path(os.environ.get("APPDATA", Path.home())) / "ForeverAI"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "foreverai"
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_path() -> Path:
    return (REPO_ROOT / ".env") if dev_mode() else data_dir() / ".env"


KEY_NAMES = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}
_ENV_FALLBACK = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def read_key(provider: str) -> str | None:
    """The API key of ``provider`` (openrouter | gemini): from .env, else from the environment."""
    name = KEY_NAMES[provider]
    p = env_path()
    if p.exists():
        key = (dotenv_values(p).get(name) or "").strip()
        if key:
            return key
    for var in _ENV_FALLBACK.get(provider, (name,)):
        if os.environ.get(var):
            return os.environ[var].strip()
    return None


def write_key(provider: str, key: str) -> None:
    """Replace the provider's key in .env, keeping other lines."""
    name = KEY_NAMES[provider]
    p = env_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith(f"{name}=")]
    lines.insert(0, f"{name}={key.strip()}")
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def read_api_key() -> str | None:
    return read_key("openrouter")


def write_api_key(key: str) -> None:
    write_key("openrouter", key)


def mask_key(key: str | None) -> str:
    if not key:
        return "(none)"
    return f"{key[:6]}…{key[-3:]}" if len(key) > 12 else "…"


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    fallback_model: str = "deepseek/deepseek-v4-flash"
    search_mode: str = "auto"  # auto | always | never
    favorites: list[str] = field(default_factory=lambda: list(FAVORITE_MODELS))
    low_balance: float = 1.0
    pool_size: int = 1000
    game_dir: str = ""
    max_answer_tokens: int = 1800
    provider: str = "openrouter"  # openrouter | claude_code | gemini
    gemini_model: str = GEMINI_DEFAULT_MODEL
    claude_model: str = "claude-haiku-5-5"
    claude_effort: str = "high"
    claude_suggest_dismissed: bool = False
    shortcut_offered: bool = False


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.Lock()
        self._detected = False
        self.settings = self._load()
        if self._detected:  # remember it, so a later start (e.g. autostart) does not depend on detection
            try:
                self.save()
            except OSError:
                pass

    def _load(self) -> Settings:
        s = Settings()
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
                for k, v in raw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
            except (OSError, json.JSONDecodeError):
                pass
        if not s.game_dir:
            found = detect_game_dir()
            s.game_dir = str(found) if found else ""
            self._detected = bool(found)
        return s

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.settings), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def update(self, **kw) -> Settings:
        for k, v in kw.items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, v)
        self.save()
        return self.settings


def choose_provider(settings: Settings, claude_info: dict | None) -> str | None:
    """The AI provider to use at start: the chosen one while it still works, else the signed-in Claude Code, else a
    provider with a saved key; None when nothing is available (the app then asks for a key)."""
    claude_ok = bool(claude_info and claude_info.get("loggedIn"))
    usable = {"claude_code": claude_ok, "openrouter": bool(read_key("openrouter")), "gemini": bool(read_key("gemini"))}
    if usable.get(settings.provider):
        return settings.provider
    for provider in ("claude_code", "openrouter", "gemini"):
        if usable[provider]:
            return provider
    return None


def detect_game_dir() -> Path | None:
    if os.environ.get("FOREVERAI_GAME_DIR"):
        return Path(os.environ["FOREVERAI_GAME_DIR"])
    from .setup_game import find_game_dir
    return find_game_dir()


def game_paths(game_dir: str | Path) -> dict[str, Path]:
    g = Path(game_dir)
    return {"game": g, "addons": ci_join(g, "Interface", "AddOns"), "screenshots": ci_join(g, "Screenshots"),
            "wtf": ci_join(g, "WTF")}
