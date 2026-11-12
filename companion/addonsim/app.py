"""The ForeverAI app for the simulator: the background part of the desktop app (foreverai.service.Service and the
ScreenshotWatcher, as foreverai.app.Companion runs them) in this process, against the stand-in game folder.

It is kept apart from the player's own ForeverAI: its own data folder, no API keys from the repository's .env or the
system credential store, and no update check. With ai="scripted" (the default) a scripted AI answers instead of a
model: the replies queued with queue_reply() in order, else a short reply that names the question; tool calls in a
reply go through the app's real tool handling (map marks, proposals, memory). ai="live" asks the real provider: Claude
Code when it is signed in, else OpenRouter or Gemini with a key from the environment (OPENROUTER_API_KEY,
GEMINI_API_KEY); that costs money or subscription use."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from collections import deque
from pathlib import Path

from foreverai import claude_code, config, keystore
from foreverai.openrouter import ToolCall
from foreverai.setup_game import prepare_game

SCRIPTED_MODEL = "sim/scripted-ai"
SCRIPTED_FREE_MODEL = "sim/scripted-ai:free"  # answers like the other one, as a free OpenRouter model (model=...)


class MemoryKeyring:
    """Stands in for the system credential store: the simulator never reads or writes the player's keys."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value


class ScriptedAI:
    """Stands in for the OpenRouter client (foreverai.openrouter.OpenRouter)."""

    def __init__(self, key: str, app: "App"):
        self.key = key
        self.app = app

    def credits(self) -> float:
        return 5.0

    def models(self) -> list[dict]:
        return [{"id": SCRIPTED_MODEL, "name": "Scripted AI (simulator)", "in": 0.0, "out": 0.0},
                {"id": SCRIPTED_FREE_MODEL, "name": "Scripted AI (simulator, free)", "in": 0.0, "out": 0.0,
                 "free": True, "tools": True}]

    def key_info(self) -> dict:
        return {"label": "simulator", "free_model_daily_requests": {"used": 13, "limit": 50, "remaining": 37}}

    def chat_stream(self, model, messages, tools=None, search_mode="auto", max_tokens=1800):
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = _question(messages[last_user].get("content"))
        reply = self.app.reply_for(question, str(messages[last_user].get("content"))[:4000])
        tool_round = any(m["role"] == "tool" for m in messages[last_user + 1:])
        if tool_round:
            names = {c.get("id"): (c.get("function") or {}).get("name") for m in messages[last_user + 1:]
                     for c in m.get("tool_calls") or []}
            self.app.tool_results += [{"tool": names.get(m.get("tool_call_id")), "result": m.get("content")}
                                      for m in messages[last_user + 1:] if m["role"] == "tool"]
        if reply.get("tools") and not tool_round:
            calls = [ToolCall(f"sim{i}", t["name"], json.dumps(t.get("args", {})))
                     for i, t in enumerate(reply["tools"])]
            yield "tool_calls", calls
            yield "done", "tool_calls"
            return
        text = reply.get("text", "")
        for chunk in re.findall(r"\S+\s*|\s+", text):
            time.sleep(self.app.stream_delay)
            yield "text", chunk
        yield "usage", {"cost": 0.0}
        yield "done", "stop"


def _question(content) -> str:
    m = re.search(r"Player question: (.*)", str(content or ""))
    return m.group(1).strip() if m else ""


def default_reply(question: str) -> dict:
    return {"text": f"**Scripted answer** (the add-on simulator; no model was asked)\n\nYou asked: {question}\n\n"
                    "- The question went from the add-on to the app as a screenshot.\n"
                    "- This answer came back through the inbox add-ons."}


class App:
    def __init__(self, game_dir: Path, data_dir: Path, *, ai: str = "scripted", replies=(),
                 addon_dir: Path | None = None, kb: bool = True, pool: int = 300, stream_delay: float = 0.03,
                 model: str = SCRIPTED_MODEL):
        if ai not in ("scripted", "live"):
            raise ValueError("ai: scripted or live")
        self.model = model  # the scripted AI's model id (ai="scripted")
        self.game_dir, self.data_dir = Path(game_dir), Path(data_dir)
        self.ai = ai
        self.addon_dir = Path(addon_dir) if addon_dir else None
        self.kb_enabled = kb
        self.pool = pool
        self.stream_delay = stream_delay
        self.replies: deque[dict] = deque(self._reply(r) for r in replies)
        self.received: list[dict] = []  # messages from the game (without the game data)
        self.activity: list[str] = []
        self.tool_results: list[dict] = []  # what the app's tools returned to the scripted AI
        self.log_lines: list[str] = []
        self._turns: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._saved: list = []
        self.service = None
        self.watcher = None

    @staticmethod
    def _reply(r) -> dict:
        if isinstance(r, dict):
            return r
        r = str(r)
        if r.lstrip().startswith("{"):
            return json.loads(r)
        return {"text": r}

    def queue_reply(self, reply) -> None:
        with self._lock:
            self.replies.append(self._reply(reply))

    def reply_for(self, question: str, prompt: str) -> dict:
        """The reply for this question: the same one for every round of the question (tool calls, then text)."""
        with self._lock:
            reply = self._turns.get(prompt)
            if reply is None:
                reply = self.replies.popleft() if self.replies else default_reply(question)
                self._turns[prompt] = reply
            return reply

    # ------------------------------------------------------------------------------------------------ isolation
    def _patch(self, obj, name, value) -> None:
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def _env(self, name, value) -> None:
        self._saved.append((os.environ, name, os.environ.get(name)))
        os.environ[name] = value

    def _restore(self) -> None:
        for obj, name, old in reversed(self._saved):
            if obj is os.environ:
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old
            else:
                setattr(obj, name, old)
        self._saved.clear()
        keystore.reset()

    # ------------------------------------------------------------------------------------------------ run
    def start(self) -> "App":
        from foreverai.kb.store import KB
        from foreverai.kb.sync import kb_path
        from foreverai.service import Service
        from foreverai.watcher import ScreenshotWatcher

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._env("FOREVERAI_DATA", str(self.data_dir))
        self._env("FOREVERAI_GAME_DIR", str(self.game_dir))
        self._env("FOREVERAI_NO_UPDATE_CHECK", "1")
        env_file = self.data_dir / ".env"
        self._patch(config, "env_path", lambda: env_file)
        memory = MemoryKeyring()
        self._patch(keystore, "_load", lambda: memory)
        keystore.reset()
        self._logging()

        store = config.SettingsStore(self.data_dir / "settings.json")
        store.update(game_dir=str(self.game_dir), pool_size=self.pool, shortcut_offered=True)
        if self.ai == "scripted":
            self._patch(claude_code, "detect", lambda force=False: None)
            env_file.write_text("OPENROUTER_API_KEY=sim-scripted-ai\n", encoding="utf-8")
            store.update(provider="openrouter", model=self.model, fallback_model=SCRIPTED_MODEL)
            factory = lambda key: ScriptedAI(key, self)  # noqa: E731
        else:
            provider = config.choose_provider(store.settings, claude_code.detect(force=True))
            if provider is None:
                raise RuntimeError("ai=live needs a signed-in Claude Code or OPENROUTER_API_KEY / GEMINI_API_KEY")
            store.update(provider=provider)
            factory = None

        prepare_game(self.game_dir, self.pool)
        if self.addon_dir:  # another version of the add-on (e.g. from main, for "before" pictures)
            target = self.game_dir / "Interface" / "AddOns" / "ForeverAI"
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(self.addon_dir, target)
        kb_file = Path(kb_path())  # prepare_game copied the starter knowledge base there, as on a first start
        kb = KB(kb_file) if self.kb_enabled and kb_file.exists() else None
        kwargs = {"client_factory": factory} if factory else {}
        self.service = Service(store, kb, self.game_dir / "Interface" / "AddOns", **kwargs)
        handle = self.service.handle

        def recorded(msg: dict) -> None:
            self.received.append({k: v for k, v in msg.items() if k not in ("snapshot", "perf")})
            handle(msg)
        self.service.handle = recorded
        self.watcher = ScreenshotWatcher(self.game_dir / "Screenshots", self.service.on_frame)
        self.service.screen_for = self.watcher.screen_for
        self.watcher.start()
        return self

    def _logging(self) -> None:
        app = self

        class Collect(logging.Handler):
            def emit(self, record):
                line = record.getMessage()
                if record.name == "foreverai.activity":
                    app.activity.append(line)
                app.log_lines.append(f"{record.levelname} {record.name}: {line}")

        handler = Collect(logging.INFO)
        logger = logging.getLogger("foreverai")
        self._saved.append((logger, "level", logger.level))  # restored with setattr, like the patches
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self._handler = handler

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()  # a daemon thread; not joined, so an older app's watcher (before 0.6.0) works too
        if self.service:
            self.service.close()
        logging.getLogger("foreverai").removeHandler(self._handler)
        (self.data_dir / "foreverai.log").write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        self._restore()

    def __enter__(self) -> "App":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def report(self) -> dict:
        return {"ai": self.ai, "received": [{k: m.get(k) for k in ("type", "text", "value") if m.get(k) is not None}
                                            for m in self.received],
                "activity": self.activity, "toolResults": self.tool_results, "log": str(self.data_dir / "foreverai.log")}
