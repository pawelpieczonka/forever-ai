"""Handles messages coming from the game and sends replies through the inbox pool."""

from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import claude_code, config, gemini
from .frames import Frame, FrameAssembler
from .inbox import pool_size, write_inbox
from .kb.store import KB
from .memory import ConversationStore, MemoryStore
from .gemini import Gemini
from .openrouter import OpenRouter
from .prompt import build_messages, entity_names
from .tools import (TOOLS, auction_request, format_addon_options, format_auction, remember_options, run_fetch,
                    run_kb_search, validate_abandon, validate_mark, validate_proposal, validate_vendor)
from .wowformat import bracket_names, md_to_wow

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")  # short English lines shown in the app window

_REQUEST_NAMES = {"status": "status refresh", "set_model": "model change", "set_provider": "AI provider change",
                  "set_effort": "thinking level change", "set_search_mode": "web search setting",
                  "set_api_key": "OpenRouter key change", "set_gemini_key": "Gemini key change", "memory_add": "memory: add", "memory_delete": "memory: delete",
                  "memory_clear": "memory: clear", "memory_list": "memory: list", "dismiss_claude": "settings"}

FIRST_FLUSH_S = 1.0
FLUSH_EVERY_S = 1.5
RETRY_TEXT_S = 2.0      # the addon reads the next inbox this soon while text is streaming
RETRY_THINKING_S = 3.0  # ... and this soon while the model is still thinking / using tools
MAX_TOOL_ROUNDS = 5
EFFORT_NAMES = {"low": "low", "medium": "medium", "high": "high", "xhigh": "very high", "max": "max"}
PROVIDER_NAMES = {"openrouter": "OpenRouter", "gemini": "Google Gemini", "claude_code": "Claude Code"}
SCREEN_NOTE = ("The player attached the current screen as an image (top-left corner blacked out). Use it for what "
               "the game data does not contain.")
MAX_SESSION_TURNS = 8  # then a fresh Claude session (with the last turns inline) keeps the context small
MAX_SESSION_CHARS = 350_000  # ~90k tokens of questions with game data: a fresh session before the context fills up


def model_name(model_id: str) -> str:
    """claude-haiku-5-5 -> Haiku 5.5 (OpenRouter ids are shown as they are)."""
    for m in claude_code.CLAUDE_MODELS:
        if m["id"] == model_id:
            return m["name"]
    return model_id


class InboxWriter:
    """Writes the latest reply state into the next ``lookahead`` inboxes.

    The game indexes addon files only when the UI loads, so there is no way to signal "ready" with new files.
    Instead the addon reads one inbox every ~2.5 s (each LoadOnDemand addon can be read once per UI session) and
    every inbox ahead of it always holds the newest state of the reply.
    """

    # the addon reads one inbox every >=1 s (2-4 s in practice) for up to 300 s -> 200 covers the whole wait
    def __init__(self, addons_dir: Path, session: str, next_inbox: int, pool: int, lookahead: int = 200):
        self.addons_dir = addons_dir
        self.session = session
        self.base = int(next_inbox)
        self.pool = pool
        self.lookahead = lookahead
        self.seq = 0
        self.requests: list[dict] = []  # work the game should do for the model (e.g. read an add-on's options)
        self.last: dict | None = None
        self._lock = threading.Lock()

    def send(self, payload: dict) -> bool:
        last = min(self.pool, self.base + self.lookahead - 1)
        if self.base > last:
            log.warning("inbox pool exhausted (base=%s pool=%s)", self.base, self.pool)
            return False
        with self._lock:
            self.seq += 1
            body = {"v": 1, "session": self.session, "seq": self.seq, **payload}
            if not payload.get("final"):
                self.last = dict(payload)
                if self.requests:
                    body["requests"] = list(self.requests)
                    body["retryAfter"] = 1.0  # the game should pick the request up quickly
            for n in range(self.base, last + 1):
                write_inbox(self.addons_dir, n, body)
        log.info("inbox %03d-%03d <- %s seq=%s final=%s len=%s req=%s", self.base, last, body.get("kind"), self.seq,
                 body.get("final"), len(body.get("text") or ""), len(body.get("requests") or []))
        if body.get("final") and body.get("kind") == "answer":
            activity.info("Answer · %s s · %s", body.get("secs", "?"), body.get("model") or "AI")
        elif body.get("kind") == "error":
            activity.warning("Error: %s", str(body.get("text") or "")[:140])
        return True

    def resend(self) -> None:
        """Publish the current (non-final) state again, e.g. after a game request was added."""
        if self.last is not None:
            self.send(dict(self.last))


class TurnContext:
    """Everything the tools of one answer work on."""

    def __init__(self, msg: dict, writer: InboxWriter, snapshot: dict, cancel: threading.Event,
                 sources: list[dict]):
        self.msg_id = msg.get("msgId")
        self.writer = writer
        self.snapshot = snapshot
        self.cancel = cancel
        self.sources = sources
        self.actions: list[dict] = []
        self.marks: list[dict] = []
        self.phase_cb: Callable[[str], None] = lambda p: None
        self.pre_urls: set[str] = set()
        self.known: dict = {}  # addon options the game reported in this answer (validates proposals)
        self.want_screen = bool(msg.get("wantScreen"))
        self.ah_items: dict[str, dict] = {}  # items the auction search returned (name -> id, price)
        self.ah_open: bool | None = None
        self.web_search: Callable[[str], tuple[str, list[dict]]] | None = None  # Gemini: grounded search

    def used_sources(self, answer: str, limit: int = 8) -> list[dict]:
        """Sources for the answer card: what the model looked up itself, plus pre-fetched knowledge-base pages only
        when the answer actually talks about them."""
        low = (answer or "").lower()
        out = []
        for src in self.sources:
            if src.get("url") in self.pre_urls:
                title = str(src.get("title") or "").lower().replace("(forever)", "").strip()
                if not title or title not in low:
                    continue
            out.append(src)
        return out[:limit]

    def auction_card(self, answer: str) -> list[dict] | None:
        """Auction items the answer recommends (named in [brackets]), for the "Search on the AH" card."""
        if not self.ah_items:
            return None
        named = {n.lower() for n in bracket_names(answer or "")}
        picked = [v for k, v in self.ah_items.items() if k.lower() in named]
        return picked[:10] or None

    def add_source(self, src: dict) -> None:
        if src.get("url") and all(x["url"] != src["url"] for x in self.sources):
            self.sources.append({k: src.get(k) for k in ("title", "url", "date")})


class Service:
    def __init__(self, settings: config.SettingsStore, kb: KB | None, addons_dir: Path,
                 client_factory: Callable[[str], OpenRouter] = OpenRouter, lookahead: int = 200,
                 gemini_factory: Callable[[str], Gemini] = Gemini):
        self.store = settings
        self.kb = kb
        self.addons_dir = Path(addons_dir)
        self.client_factory = client_factory
        self.lookahead = lookahead
        self.assembler = FrameAssembler()
        self.gemini_factory = gemini_factory
        self.memory = MemoryStore(config.data_dir() / "memory.json")
        self.convs = ConversationStore(config.data_dir() / "convs")
        self._char = "?"
        self.last_cost: float | None = None
        self._balance: tuple[float, float] | None = None  # (value, fetched_at)
        self._models: tuple[list[dict], float] | None = None
        self._gemini_models: tuple[list[dict], float] | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.on_event: Callable[[str], None] = lambda s: None
        self.last_question: dict | None = None
        self._turn: TurnContext | None = None  # answer in progress (tools called by Claude Code land here)
        self._game_pending: dict[str, dict] = {}
        self._req_ids = itertools.count(1)
        self._bridge = None
        self._claude: claude_code.ClaudeSession | None = None
        self._claude_lock = threading.Lock()
        self.screen_for: Callable[[int], bytes | None] = lambda msg_id: None  # set by the app (screenshot watcher)

    # ---------------------------------------------------------------- input
    def on_frame(self, frame: Frame) -> None:
        data = self.assembler.add(frame)
        if data is None:
            return
        try:
            msg = json.loads(zlib.decompress(data).decode("utf-8"))
        except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as e:
            log.error("cannot decode message %s: %s", frame.msg_id, e)
            return
        self.handle(msg)

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "game_result":  # the game finished work the model asked for; no reply goes back
            self._game_result(msg)
            return
        safe = {k: v for k, v in msg.items() if k not in ("snapshot", "value")}
        log.info("message %s", safe)
        if kind == "question":
            activity.info("Question: %s", str(msg.get("text") or "")[:90])
            log.info("game data: %s", msg.get("perf"))
        elif kind in _REQUEST_NAMES:
            activity.info("Request: %s", _REQUEST_NAMES[kind])
        self._char = str(msg.get("char") or self._char)
        for conv in msg.get("deleteConvs") or []:
            self.convs.delete(str(conv))
        writer = InboxWriter(self.addons_dir, str(msg.get("session")), int(msg.get("nextInbox") or 1),
                             pool_size(self.addons_dir), self.lookahead)
        if kind in ("memory_add", "memory_delete", "memory_clear", "memory_list"):
            text = None
            if kind == "memory_add":
                item = self.memory.add(self._char, str(msg.get("value") or ""))
                text = "Added to memory." if item else "Nothing added (empty or duplicate)."
            elif kind == "memory_delete":
                self.memory.delete(self._char, int(msg.get("value") or 0))
                text = "Entry deleted."
            elif kind == "memory_clear":
                self.memory.clear(self._char)
                text = "Memory cleared."
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "text": text,
                         "status": self.status()})
            return
        if kind == "question":
            self._save_snapshot(msg)
            sess = str(msg.get("session"))
            with self._lock:
                old = self._cancel.get(sess)
                if old:
                    old.set()
                ev = threading.Event()
                self._cancel[sess] = ev
            writer.send({"kind": "answer", "final": False, "msgId": msg.get("msgId"), "text": "",
                         "phase": "Thinking…", "retryAfter": RETRY_THINKING_S})
            threading.Thread(target=self._answer_safe, args=(msg, writer, ev), daemon=True).start()
        elif kind == "status":
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "status": self.status(True)})
        elif kind == "set_model":
            self._set_model(msg, writer)
        elif kind == "set_search_mode":
            mode = msg.get("value")
            if mode in ("auto", "always", "never"):
                self.store.update(search_mode=mode)
                self.prewarm_later()
                writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"),
                             "text": f"Web search: {mode}", "status": self.status()})
            else:
                writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"), "text": "Unknown mode."})
        elif kind == "set_api_key":
            self._set_key(msg, writer, "openrouter")
        elif kind == "set_gemini_key":
            self._set_key(msg, writer, "gemini")
        elif kind == "set_provider":
            self._set_provider(msg, writer)
        elif kind == "set_effort":
            effort = msg.get("value")
            if effort in claude_code.EFFORTS:
                self.store.update(claude_effort=effort)
                self.prewarm_later()
                writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"),
                             "text": f"Thinking level: {EFFORT_NAMES.get(effort, effort)}", "status": self.status()})
            else:
                writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"), "text": "Unknown thinking level."})
        elif kind == "dismiss_claude":
            self.store.update(claude_suggest_dismissed=True)
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"),
                         "text": "Keeping " + PROVIDER_NAMES.get(self.store.settings.provider, "the current provider") + ".",
                         "status": self.status()})
        elif kind == "new_conversation":
            pass
        else:
            log.warning("unknown message type %s", kind)

    # ---------------------------------------------------------------- settings
    def _set_provider(self, msg: dict, writer: InboxWriter) -> None:
        value = msg.get("value")
        if value == "claude_code":
            info = claude_code.detect(force=True)
            if not info or not info.get("loggedIn"):
                writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                             "text": "Claude Code is not installed or not signed in (sign in: run claude auth login in a terminal)."})
                return
        elif value in ("openrouter", "gemini"):
            if not config.read_key(value):
                writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"), "status": self.status(),
                             "text": f"No {PROVIDER_NAMES[value]} key yet. Enter it below first."})
                return
        else:
            writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"), "text": "Unknown provider."})
            return
        self.store.update(provider=value)
        self.prewarm_later()
        s = self.store.settings
        label = {"claude_code": f"Claude Code · {model_name(s.claude_model)} · thinking {EFFORT_NAMES.get(s.claude_effort, s.claude_effort)}",
                 "gemini": f"Google Gemini · {s.gemini_model}"}.get(value, f"OpenRouter · {s.model}")
        writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "text": "AI provider: " + label,
                     "status": self.status(True)})

    def _set_model(self, msg: dict, writer: InboxWriter) -> None:
        model = str(msg.get("value") or "").strip()
        if self.store.settings.provider == "claude_code":
            known = {m["id"] for m in claude_code.CLAUDE_MODELS} | {"sonnet", "opus", "haiku", "fable"}
            if model not in known and not model.startswith("claude-"):
                writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                             "text": f"{model} is not a Claude model."})
                return
            self.store.update(claude_model=model)
            self.prewarm_later()
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"),
                         "text": f"Claude model: {model_name(model)}", "status": self.status()})
            return
        if self.store.settings.provider == "gemini":
            ids = {m["id"] for m in self.gemini_models()}
            if not gemini.is_chat_model(model) or (ids and model not in ids and not model.endswith("-latest")):
                writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                             "text": f"{model} is not a Gemini chat model available to this key."})
                return
            self.store.update(gemini_model=model)
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "text": f"Gemini model: {model}",
                         "status": self.status()})
            return
        ids = {m["id"] for m in self.models()}
        if ids and model not in ids:
            writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                         "text": f"Model {model} does not exist on OpenRouter."})
            return
        self.store.update(model=model)
        writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "text": f"Model: {model}",
                     "status": self.status()})

    def save_key(self, provider: str, key: str, use: bool = False) -> tuple[bool, str]:
        """Check the key with the provider and save it; ``use`` also switches to that provider.
        Returns (ok, message for the player)."""
        key = key.strip()
        name = PROVIDER_NAMES[provider]
        if not key or any(c.isspace() for c in key):
            return False, f"That does not look like a {name} key."
        factory = self.gemini_factory if provider == "gemini" else self.client_factory
        try:
            factory(key).key_info()
        except Exception as e:  # invalid key or network
            return False, f"{name} rejected the key ({str(e)[:80]}). Nothing changed."
        config.write_key(provider, key)
        if provider == "openrouter":
            self._balance = self._models = None
        else:
            self._gemini_models = None
        if use:
            self.store.update(provider=provider)
        activity.info("%s key saved%s", name, " · AI provider: " + name if use else "")
        return True, "Key saved." + (f" Using {name}." if use else "")

    def _set_key(self, msg: dict, writer: InboxWriter, provider: str) -> None:
        # a key entered while that provider has no key yet also switches to it
        use = not config.read_key(provider) and self.store.settings.provider != "claude_code"
        ok, text = self.save_key(provider, str(msg.get("value") or ""), use=use)
        writer.send({"kind": "status" if ok else "error", "final": True, "msgId": msg.get("msgId"), "text": text,
                     "status": self.status(ok)})

    # ---------------------------------------------------------------- status
    def _refresh(self, what: str) -> None:
        key = config.read_key("gemini" if what == "gemini_models" else "openrouter")
        if not key:
            return
        try:
            if what == "gemini_models":
                self._gemini_models = (self.gemini_factory(key).models(), time.time())
            elif what == "models":
                self._models = (self.client_factory(key).models(), time.time())
            else:
                self._balance = (self.client_factory(key).credits(), time.time())
        except Exception as e:
            log.warning("%s refresh failed: %s", what, e)
            if what == "gemini_models" and self._gemini_models is None:
                self._gemini_models = ([], time.time() - 3300)  # do not block every status; retry in 5 minutes

    def _refresh_later(self, what: str) -> None:
        """Refresh a cached value in the background so replies never wait on the network."""
        flag = f"_refreshing_{what}"
        if getattr(self, flag, False):
            return
        setattr(self, flag, True)

        def run():
            try:
                self._refresh(what)
            finally:
                setattr(self, flag, False)
        threading.Thread(target=run, daemon=True).start()

    def models(self) -> list[dict]:
        if self._models is None:
            self._refresh("models")  # first use: nothing cached yet
        elif time.time() - self._models[1] > 3600:
            self._refresh_later("models")
        return self._models[0] if self._models else []

    def gemini_models(self) -> list[dict]:
        if self._gemini_models is None:
            self._refresh("gemini_models")  # first use: nothing cached yet
        elif time.time() - self._gemini_models[1] > 3600:
            self._refresh_later("gemini_models")
        return self._gemini_models[0] if self._gemini_models else []

    def gemini_status(self) -> dict:
        s = self.store.settings
        key = config.read_key("gemini")
        available = {m["id"]: m for m in self.gemini_models()} if key else {}
        ids = [s.gemini_model] + [m for m in gemini.FAVORITE_MODELS if m != s.gemini_model]
        if available:  # aliases (…-latest) are not listed by the API but work
            ids = [m for m in ids if m in available or m.endswith("-latest") or m == s.gemini_model]
        return {"hasKey": bool(key), "keyMask": config.mask_key(key), "model": s.gemini_model,
                "models": [{"id": m, "name": (available.get(m) or {}).get("name") or m} for m in ids[:8]]}

    def balance(self, fresh: bool = False) -> float | None:
        if self._balance is None or fresh:
            self._refresh("balance")
        elif time.time() - self._balance[1] > 60:
            self._refresh_later("balance")
        return self._balance[0] if self._balance else None

    def _spent(self, cost: float | None) -> None:
        """Apply the known cost of an answer to the cached balance and verify it in the background."""
        if self._balance and cost:
            self._balance = (self._balance[0] - cost, self._balance[1])
        self._refresh_later("balance")

    def _status_after(self, cost: float | None) -> dict:
        self._spent(cost)
        return self.status()

    def status(self, fresh: bool = False) -> dict:
        s = self.store.settings
        bal = self.balance(fresh) if config.read_key("openrouter") else None
        by_id = {m["id"]: m for m in self.models()}
        favs = []
        for mid in [s.model] + [f for f in s.favorites if f != s.model]:
            m = by_id.get(mid)
            favs.append({"id": mid, "in": m["in"] if m else None, "out": m["out"] if m else None})
        return {
            "balance": round(bal, 4) if bal is not None else None,
            "balanceAt": datetime.now().strftime("%H:%M"),
            "lowBalance": s.low_balance,
            "model": s.model,
            "searchMode": s.search_mode,
            "lastCost": self.last_cost,
            "kbDate": self.kb.meta("kb_date") if self.kb else None,
            "hotfixDate": self.kb.meta("hotfix_date") if self.kb else None,
            "kbPages": self.kb.count_pages() if self.kb else 0,
            "keyMask": config.mask_key(config.read_api_key()),
            "models": favs[:10],
            "addonSV": self.addon_sv(),
            "provider": s.provider,
            "memory": self.memory.get(self._char),
            "claude": self.claude_status(),
            "gemini": self.gemini_status(),
            "suggestClaude": bool(self.claude_status()["loggedIn"]) and s.provider != "claude_code"
                             and not s.claude_suggest_dismissed,
        }

    def claude_status(self) -> dict:
        s = self.store.settings
        info = claude_code.detect_cached()
        return {"detected": bool(info), "loggedIn": bool(info and info.get("loggedIn")),
                "subscription": info.get("subscription") if info else None,
                "version": info.get("version") if info else None,
                "model": s.claude_model, "effort": s.claude_effort,
                "models": claude_code.CLAUDE_MODELS, "efforts": claude_code.EFFORTS}

    def addon_sv(self) -> dict:
        if not hasattr(self, "_addon_sv"):
            from .addon_meta import saved_variables
            try:
                self._addon_sv = saved_variables(self.addons_dir)
            except OSError:
                self._addon_sv = {}
        return self._addon_sv

    # ---------------------------------------------------------------- answering
    def _save_snapshot(self, msg: dict) -> None:
        try:
            p = config.data_dir() / "last_question.json"
            p.write_text(json.dumps({"text": msg.get("text"), "snapshot": msg.get("snapshot")}, ensure_ascii=False),
                         encoding="utf-8")
        except OSError:
            pass

    def _answer_safe(self, msg: dict, writer: InboxWriter, cancel: threading.Event) -> None:
        try:
            self._answer(msg, writer, cancel)
        except Exception as e:  # never leave the player without a reply
            log.exception("answer failed")
            writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                         "text": f"ForeverAI error: {str(e)[:200]}", "status": self.status()})

    def _answer(self, msg: dict, writer: InboxWriter, cancel: threading.Event) -> None:
        msg_id = msg.get("msgId")
        question = str(msg.get("text") or "").strip()
        snapshot = msg.get("snapshot") or {}
        s = self.store.settings
        conv = str(msg.get("conv") or "default")
        history = self.convs.history(conv)
        pre_hits = []
        if self.kb:
            names = entity_names(snapshot)
            pre_hits = self.kb.search_titles(names, k=3)
        messages = build_messages(question, snapshot, history, pre_hits, now=datetime.now(),
                                  kb_date=self.kb.meta("kb_date") if self.kb else None,
                                  hotfix_date=self.kb.meta("hotfix_date") if self.kb else None,
                                  memory=self.memory.get(self._char))
        sources: list[dict] = [{"title": h.title, "url": h.url, "date": h.ts[:10]} for h in pre_hits]
        ctx = TurnContext(msg, writer, snapshot, cancel, sources)
        ctx.pre_urls = {x["url"] for x in sources}  # guessed from names in the game, not chosen by the model
        self._turn = ctx
        try:
            if s.provider == "claude_code":
                self._answer_claude(msg, ctx, messages, conv)
            else:
                self._answer_api(msg, ctx, messages, "gemini" if s.provider == "gemini" else "openrouter")
        finally:
            if self._turn is ctx:
                self._turn = None

    def _answer_api(self, msg: dict, ctx: TurnContext, messages: list[dict], provider: str) -> None:
        """An answer from an API with OpenAI-style chat (OpenRouter or Gemini), running the tool rounds here."""
        msg_id, writer, cancel, sources = ctx.msg_id, ctx.writer, ctx.cancel, ctx.sources
        question = str(msg.get("text") or "").strip()
        s = self.store.settings
        is_gemini = provider == "gemini"
        key = config.read_key(provider)
        if not key:
            writer.send({"kind": "error", "final": True, "msgId": msg_id, "status": self.status(),
                         "text": f"No {PROVIDER_NAMES[provider]} key. Enter it in the ForeverAI window (AI key…) or "
                                 f"in Settings → {PROVIDER_NAMES[provider]} key."})
            return
        client = (self.gemini_factory if is_gemini else self.client_factory)(key)
        model = s.gemini_model if is_gemini else s.model
        if is_gemini:
            def search(query: str) -> tuple[str, list[dict]]:
                return client.web_search(model, query)
            ctx.web_search = search
            if s.search_mode == "always":
                messages[0]["content"] += "\n\nAlways check current information with web_search before answering."
        shot = self.screen_image(ctx)
        if shot:
            model_info = {"image": True} if is_gemini else ({m["id"]: m for m in self.models()}.get(s.model) or {})
            last = messages[-1]
            if model_info.get("image"):
                last["content"] = [{"type": "text", "text": last["content"] + "\n\n" + SCREEN_NOTE},
                                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + shot}}]
            else:
                last["content"] += f"\n\n(The player attached a screenshot, but {model} cannot read images.)"
        text = ""
        phase = "Thinking…"
        cost = 0.0
        started = time.monotonic()
        last_sent = [0.0, "", ""]  # time, text, phase
        tried_fallback = is_gemini  # the fallback model is an OpenRouter one

        def flush(force: bool = False) -> None:
            now = time.monotonic()
            if cancel.is_set():
                return
            elapsed = now - started
            if not force:
                if elapsed < FIRST_FLUSH_S or now - last_sent[0] < FLUSH_EVERY_S:
                    return
                if text == last_sent[1] and phase == last_sent[2]:
                    return
            writer.send({"kind": "answer", "final": False, "msgId": msg_id, "text": md_to_wow(text) if text else "",
                         "phase": phase, "retryAfter": RETRY_TEXT_S if text else RETRY_THINKING_S})
            last_sent[:] = [now, text, phase]

        def set_phase(p: str) -> None:
            nonlocal phase
            if p:
                phase = p
                flush(force=True)
        ctx.phase_cb = set_phase
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            calls = None
            round_text = ""
            error = None
            for kind, val in client.chat_stream(model, messages, TOOLS, s.search_mode, s.max_answer_tokens):
                if cancel.is_set():
                    log.info("answer %s cancelled", msg_id)
                    return
                if kind == "text":
                    text += val
                    round_text += val
                    phase = "Writing…"
                    flush()
                elif kind == "annotations":
                    for a in val:
                        uc = a.get("url_citation") or {}
                        if uc.get("url") and all(x["url"] != uc["url"] for x in sources):
                            sources.append({"title": uc.get("title") or uc["url"], "url": uc["url"], "date": ""})
                    phase = "Searched the web…"
                elif kind == "usage":
                    cost += float(val.get("cost") or 0)
                elif kind == "tool_calls":
                    calls = val
                elif kind == "error":
                    error = val
            if error:
                if not text and not tried_fallback and s.fallback_model and s.fallback_model != model and (
                        "404" in error or "Model" in error or "400" in error):
                    tried_fallback = True
                    model = s.fallback_model
                    phase = f"Fallback model {model}…"
                    rounds -= 1
                    continue
                writer.send({"kind": "error", "final": True, "msgId": msg_id, "text": error, "status": self.status()})
                return
            if not calls:
                break
            messages.append({"role": "assistant", "content": round_text or None, "tool_calls": [
                {"id": c.id or f"call_{i}", "type": "function", "function": {"name": c.name, "arguments": c.arguments},
                 **({"extra_content": c.extra} if c.extra else {})}
                for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                log.info("tool %s %s", c.name, {k: str(v)[:60] for k, v in (c.args() or {}).items()})
                activity.info("Tool: %s", c.name)
                result, _ = self.run_tool(ctx, c.name, c.args())
                messages.append({"role": "tool", "tool_call_id": c.id or f"call_{i}", "content": result})
        self.last_cost = None if is_gemini else round(cost, 6)
        self.ensure_marks(ctx, text)
        self.convs.append(str(msg.get("conv") or "default"), self._char, question, text)
        final_text = md_to_wow(text) if text else "(no answer)"
        writer.send({"kind": "answer", "final": True, "msgId": msg_id, "text": final_text, "sources": ctx.used_sources(text),
                     "actions": ctx.actions, "marks": ctx.marks, "cost": self.last_cost, "model": model,
                     **({"costNote": "Gemini API"} if is_gemini else {}),
                     "links": self.links(text, ctx), "auction": ctx.auction_card(text),
                     "secs": round(time.monotonic() - started, 1),
                     "status": self.status() if is_gemini else self._status_after(cost)})
        self.on_event(f"Last answer: {model}" + ("" if is_gemini else f", ${self.last_cost:.4f}"))

    def links(self, md: str, ctx: TurnContext | None = None) -> list[dict]:
        """Item/spell/quest ids for names written in [brackets], so the game can make them clickable."""
        out = []
        found = {k.lower(): v for k, v in (ctx.ah_items if ctx else {}).items()}  # items from the auction search
        for name in bracket_names(md)[:40]:
            if name.lower() in found:
                out.append({"name": name, "kind": "item", "id": found[name.lower()]["id"]})
                continue
            if not self.kb:
                continue
            try:
                ent = self.kb.entity(name)
            except Exception:
                ent = None
            if ent:
                out.append({"name": name, "kind": ent[0], "id": ent[1]})
        return out

    # ---------------------------------------------------------------- tools (both providers)
    def run_tool(self, ctx: TurnContext | None, name: str, args: dict) -> tuple[str, bool]:
        """Execute one model tool call for the answer in progress. Returns (text for the model, is_error)."""
        if ctx is None:
            return "No question in progress; the tool is not available.", True
        phase = claude_code.tool_phase(name)
        if name == "search_knowledge_base":
            query = str(args.get("query", ""))
            ctx.phase_cb(f"Searching knowledge base: {query[:40]}")
            text, found = run_kb_search(self.kb, query)
            for f in found:  # search hits count as sources only if the answer actually talks about them
                ctx.add_source(f)
                ctx.pre_urls.add(f["url"])
            return text, False
        if name == "propose_addon_change":
            action, text = validate_proposal(args, ctx.snapshot, ctx.known)
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "mark_on_map":
            mark, text = validate_mark(args)
            if mark and len(ctx.marks) < 8:
                ctx.marks.append(mark)
            return text, mark is None
        if name == "save_memory":
            if phase:
                ctx.phase_cb(phase)
            item = self.memory.add(self._char, str(args.get("text", "")))
            if not item:
                return "Not saved (empty or already saved).", True
            return f"Saved to memory (id {item['id']}).", False
        if name == "delete_memory":
            try:
                item_id = int(args.get("id"))
            except (TypeError, ValueError):
                return "Give the entry id (a number).", True
            ok = self.memory.delete(self._char, item_id)
            return ("Deleted from memory." if ok else f"No entry with id {item_id}."), not ok
        if name == "fetch_url":
            url = str(args.get("url", ""))
            ctx.phase_cb(f"Reading page: {url[:45]}")
            text = run_fetch(url)
            if url.startswith("http") and not text.startswith(("Rejected", "Could not", "The site", "The page")):
                title = text.split("\n", 1)[0].removeprefix("Title: ").strip() if text.startswith("Title:") else url
                ctx.add_source({"title": title, "url": url, "date": ""})
            return text, False
        if name == "propose_vendor_action":
            action, text = validate_vendor(args, ctx.snapshot)
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "propose_quest_abandon":
            action, text = validate_abandon(args, ctx.snapshot)
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "search_auction_house":
            req, err = auction_request(args)
            if err:
                return err, True
            ctx.phase_cb("Searching the auction house…")
            res = self.game_call(ctx, "auction_search", req)
            for it in (res or {}).get("items") or []:
                if it.get("name") and it.get("id"):
                    ctx.ah_items[str(it["name"])] = {"id": it["id"], "name": it["name"], "price": it.get("price")}
            if res and res.get("ahOpen") is not None:
                ctx.ah_open = bool(res.get("ahOpen"))
            return format_auction(res), not res or bool(res.get("error"))
        if name == "web_search":
            if not ctx.web_search:
                return "Web search is not available with this AI provider.", True
            query = str(args.get("query", "")).strip()[:200]
            if not query:
                return "Give a search query.", True
            ctx.phase_cb(f"Searching the web: {query[:40]}")
            text, found = ctx.web_search(query)
            for f in found:
                ctx.add_source({"title": f.get("title"), "url": f.get("url"), "date": ""})
            return text, not found and text.startswith("Web search failed")
        if name == "addon_options":
            addon = str(args.get("addon", "")).strip()[:60]
            query = str(args.get("query", "")).strip()[:120]
            if not addon:
                return "Give the add-on name (folder from the addons list).", True
            ctx.phase_cb(f"Checking {addon} options…")
            res = self.game_call(ctx, "addon_options", {"addon": addon, "query": query})
            remember_options(ctx.known, addon, res)
            return format_addon_options(addon, res), not res or bool(res.get("error"))
        return f"Unknown tool {name}", True

    MARK_CLAIM = re.compile(r"marked|on the map|on your map", re.IGNORECASE)
    COORDS = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*,\s*(\d{1,2}(?:\.\d)?)(?![\d.])")

    def ensure_marks(self, ctx: TurnContext, text: str) -> None:
        """The answer says a place is marked on the map but the model never called mark_on_map: mark the
        coordinates the answer gives, named after the NPC/quest/mob the game data has at that spot."""
        if ctx.marks or not text or not self.MARK_CLAIM.search(text):
            return
        names: dict[str, str] = {}
        snap = ctx.snapshot or {}
        for line in (snap.get("zoneNPCs") or []) + (snap.get("nearbyMobs") or []):
            if isinstance(line, str):
                m = self.COORDS.search(line.split("—", 1)[-1])
                if m:
                    names.setdefault(f"{float(m.group(1)):.1f},{float(m.group(2)):.1f}", re.split(r" <|, level| —", line)[0])
        for q in snap.get("quests") or []:
            if isinstance(q, dict):
                for field in ("questGiver", "turnIn"):
                    for part in str(q.get(field) or "").split(";"):
                        m = self.COORDS.search(part)
                        if m:
                            names.setdefault(f"{float(m.group(1)):.1f},{float(m.group(2)):.1f}", part.split(" (")[0].strip())
        seen = set()
        for m in self.COORDS.finditer(text):
            key = f"{float(m.group(1)):.1f},{float(m.group(2)):.1f}"
            if key in seen or len(ctx.marks) >= 4:
                continue
            seen.add(key)
            mark, _ = validate_mark({"title": names.get(key, "Point"), "x": m.group(1), "y": m.group(2)})
            if mark:
                ctx.marks.append(mark)
        if ctx.marks:
            log.info("answer claimed a map mark without calling the tool; marked %s", [m["title"] for m in ctx.marks])

    def tool_from_bridge(self, name: str, args: dict) -> tuple:
        log.info("tool %s %s", name, {k: str(v)[:60] for k, v in (args or {}).items()})
        activity.info("Tool: %s", name)
        return self.run_tool(self._turn, name, args)

    def screen_image(self, ctx: TurnContext) -> str | None:
        """The screen at the moment of the question (JPEG, base64), only when the player ticked "Screen"."""
        if not ctx.want_screen:
            return None
        jpeg = self.screen_for(ctx.msg_id)
        if not jpeg:
            log.warning("no screenshot kept for question %s", ctx.msg_id)
            return None
        import base64
        return base64.b64encode(jpeg).decode("ascii")

    def game_call(self, ctx: TurnContext, op: str, args: dict, timeout: float = 45.0) -> dict | None:
        """Ask the game (through the reply inboxes) to do something and wait for its answer frame."""
        rid = f"{ctx.msg_id}-{next(self._req_ids)}"
        done = threading.Event()
        slot = {"event": done, "result": None}
        req = {"id": rid, "op": op, **args}
        with self._lock:
            self._game_pending[rid] = slot
        ctx.writer.requests.append(req)
        ctx.writer.resend()
        deadline = time.monotonic() + timeout
        try:
            while not done.wait(0.2):
                if ctx.cancel.is_set() or time.monotonic() > deadline:
                    break
        finally:
            with self._lock:
                self._game_pending.pop(rid, None)
            if req in ctx.writer.requests:
                ctx.writer.requests.remove(req)
        return slot["result"]

    def _game_result(self, msg: dict) -> None:
        rid = str(msg.get("id") or "")
        with self._lock:
            slot = self._game_pending.get(rid)
        log.info("game result %s (%s)", rid, "waiting" if slot else "nobody waiting")
        if slot:
            res = msg.get("result")
            slot["result"] = res if isinstance(res, dict) else {"error": "empty result from the game"}
            slot["event"].set()

    # ---------------------------------------------------------------- Claude Code provider
    def _bridge_info(self) -> tuple[str, str]:
        if self._bridge is None:
            from .bridge import Bridge
            self._bridge = Bridge(self.tool_from_bridge)
        return self._bridge.mcp_url, self._bridge.token

    def _claude_system(self, system: str) -> str:
        s = self.store.settings
        system += ("\n\nThe foreverai MCP server provides: search_knowledge_base, search_auction_house, addon_options, "
                   "propose_addon_change, propose_quest_abandon, propose_vendor_action, mark_on_map, "
                   "save_memory, delete_memory, fetch_url. Read web pages (e.g. build links) only with fetch_url — it gets "
                   "past bot blocking.")
        if s.search_mode == "always":
            system += " Always check current information on the web (WebSearch) before answering."
        return system

    def _new_claude_session(self, exe: str, system: str) -> claude_code.ClaudeSession:
        s = self.store.settings
        url, token = self._bridge_info()
        workdir = config.data_dir() / "claude_session" / f"{int(time.time() * 1000)}"
        return claude_code.ClaudeSession(exe, workdir, system, s.claude_model, s.claude_effort, s.search_mode,
                                         url, token)

    def _drop_claude(self) -> None:
        old, self._claude = self._claude, None
        if old:
            old.close()
            import shutil
            threading.Thread(target=lambda: (time.sleep(2), shutil.rmtree(old.workdir, ignore_errors=True)),
                             daemon=True).start()

    def claude_session_for(self, system: str, conv: str) -> tuple[claude_code.ClaudeSession, bool] | None:
        """A running session for this conversation; (session, continues) — continues=False means the session is new
        to this conversation and the earlier turns must be sent along with the question."""
        info = claude_code.detect_cached()
        if not info or not info.get("loggedIn"):
            return None
        s = self.store.settings
        key = (s.claude_model, s.claude_effort, s.search_mode, system)
        with self._claude_lock:
            cur = self._claude
            if cur and (not cur.alive() or cur.busy or cur.key != key or cur.turns >= MAX_SESSION_TURNS
                        or getattr(cur, "sent_chars", 0) >= MAX_SESSION_CHARS
                        or (cur.conv is not None and cur.conv != conv)):
                self._drop_claude()
                cur = None
            if cur is None:
                cur = self._claude = self._new_claude_session(info["path"], system)
            continues = cur.conv == conv
            cur.conv = conv
            return cur, continues

    def prewarm_later(self) -> None:
        threading.Thread(target=self._prewarm_safe, daemon=True, name="claude-prewarm").start()

    def _prewarm_safe(self) -> None:
        try:
            self.prewarm_claude()
        except Exception:
            log.exception("claude prewarm failed")

    def prewarm_claude(self) -> None:
        """Start a Claude Code session ahead of the first question (start-up takes ~1-2 s plus MCP)."""
        s = self.store.settings
        if s.provider != "claude_code":
            return
        info = claude_code.detect_cached()
        if not info or not info.get("loggedIn"):
            return
        messages = build_messages("", {}, [], [], now=datetime.now(), kb_date=self.kb.meta("kb_date") if self.kb else None,
                                  hotfix_date=self.kb.meta("hotfix_date") if self.kb else None)
        system = self._claude_system(messages[0]["content"])
        key = (s.claude_model, s.claude_effort, s.search_mode, system)
        with self._claude_lock:
            if self._claude and self._claude.alive() and self._claude.key == key:
                return
            self._drop_claude()
            self._claude = self._new_claude_session(info["path"], system)
            log.info("claude session prewarmed (%s, effort %s)", s.claude_model, s.claude_effort)
            activity.info("Claude session ready")

    def _answer_claude(self, msg: dict, ctx: TurnContext, messages: list[dict], conv: str) -> None:
        msg_id, writer, cancel, sources = ctx.msg_id, ctx.writer, ctx.cancel, ctx.sources
        question = str(msg.get("text") or "").strip()
        s = self.store.settings
        system = self._claude_system(messages[0]["content"])
        got = self.claude_session_for(system, conv)
        if got is None:
            writer.send({"kind": "error", "final": True, "msgId": msg_id, "status": self.status(),
                         "text": "Claude Code is not available or not signed in. Sign in (run claude auth login in a terminal) or switch "
                                 "the AI provider to OpenRouter or Gemini in Settings."})
            return
        session, continues = got
        # a session that already holds this conversation only needs the new question
        _, prompt = claude_code.flatten_messages([messages[0], messages[-1]] if continues else messages)
        shot = self.screen_image(ctx)
        images = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": shot}}] if shot else []
        if shot:
            prompt += "\n\n" + SCREEN_NOTE
        started = time.monotonic()
        state = {"text": "", "phase": "Thinking…", "sent": (0.0, "", "")}

        def flush(force: bool = False) -> None:
            now = time.monotonic()
            t, last_text, last_phase = state["sent"]
            if cancel.is_set():
                return
            if not force and (now - started < FIRST_FLUSH_S or now - t < FLUSH_EVERY_S):
                return
            if state["text"] == last_text and state["phase"] == last_phase:
                return
            writer.send({"kind": "answer", "final": False, "msgId": msg_id, "phase": state["phase"],
                         "text": md_to_wow(state["text"]) if state["text"] else "",
                         "retryAfter": RETRY_TEXT_S if state["text"] else RETRY_THINKING_S})
            state["sent"] = (now, state["text"], state["phase"])

        def set_phase(p: str) -> None:
            if p:
                state["phase"] = p
                flush(force=True)
        ctx.phase_cb = set_phase
        final = None
        for kind, val in session.turn(prompt, images):
            if cancel.is_set():
                log.info("claude answer %s cancelled", msg_id)
                break
            if kind == "text":
                state["text"], state["phase"] = str(val), "Writing…"
                flush()
            elif kind == "phase":
                state["phase"] = str(val)
                flush()
            elif kind == "final":
                final = str(val or state["text"])
            elif kind == "error":
                writer.send({"kind": "error", "final": True, "msgId": msg_id, "text": str(val),
                             "status": self.status()})
                break
        if final is None:
            with self._claude_lock:
                if self._claude is session:
                    self._drop_claude()
            return
        text = final or state["text"]
        self.ensure_marks(ctx, text)
        self.convs.append(conv, self._char, question, text)
        self.last_cost = None
        writer.send({"kind": "answer", "final": True, "msgId": msg_id,
                     "text": md_to_wow(text) if text else "(no answer)", "links": self.links(text, ctx),
                     "auction": ctx.auction_card(text),
                     "marks": ctx.marks, "sources": ctx.used_sources(text), "actions": ctx.actions,
                     "model": model_name(s.claude_model),
                     "costNote": "Claude subscription", "secs": round(time.monotonic() - started, 1),
                     "status": self.status()})
        self.on_event(f"Last answer: Claude Code {s.claude_model}")

    def close(self) -> None:
        with self._claude_lock:
            self._drop_claude()

