"""Runs a scenario through the app's own answering code (Service) with a real Claude Code session; only the game is
missing: the reply goes to a capture instead of the inbox add-ons, and requests to the game answer "not available"."""

from __future__ import annotations

import copy
import json
import threading
import time
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).with_name("base_snapshot.json")


@lru_cache(maxsize=1)
def _base() -> dict:
    return json.loads(BASE.read_text(encoding="utf-8"))


def base_snapshot() -> dict:
    """A real game snapshot with the names of players replaced: a level 11 night elf hunter in Teldrassil, the quest
    pet beside them, a Strigid Screecher (the target of [Taming the Beast]) 24 m away."""
    return copy.deepcopy(_base())


def apply_patch(base: dict, patch: dict) -> dict:
    """A copy of ``base`` with ``patch`` merged in: dicts merge, None removes a key, everything else replaces."""
    out = copy.deepcopy(base)
    for key, val in (patch or {}).items():
        if val is None:
            out.pop(key, None)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = apply_patch(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


class Capture:
    """Stands in for the inbox writer: keeps the final reply."""

    def __init__(self):
        self.requests: list[dict] = []
        self.final: dict | None = None

    def send(self, payload: dict) -> bool:
        if payload.get("final"):
            self.final = payload
        return True

    def resend(self) -> None:
        pass


def run_scenario(sc, *, model: str, effort: str, search_mode: str = "never", kb=None) -> dict:
    """Ask the scenario's questions in one conversation. Needs FOREVERAI_DATA to point at a scratch folder.
    Returns {"name", "why", "turns": [{"question", "answer", "tools", "did", "secs", "checks"}], "passed", "failed"}."""
    from foreverai import config
    from foreverai.service import Service
    from foreverai.turnlog import TurnLog

    data = config.data_dir()
    store = config.SettingsStore(data / f"settings-{sc.name}.json")
    store.update(provider="claude_code", claude_model=model, claude_effort=effort, search_mode=search_mode)
    svc = Service(store, kb, data / "AddOns")
    svc.turns = TurnLog(data / "turns" / sc.name)
    svc._balance = (0.0, time.time() + 1e9)  # never ask OpenRouter for the balance
    svc.game_call = lambda ctx, op, args, timeout=45.0: {"error": "not available in this test run"}
    char, conv = f"Evalhunter-{sc.name}", f"eval-{sc.name}"
    svc._char = char
    for h in sc.history:
        svc.convs.append(conv, char, h["q"], h["a"], h.get("did"))
    out = {"name": sc.name, "why": sc.why, "turns": [], "passed": 0, "failed": 0}
    snap = base_snapshot()
    try:
        for i, turn in enumerate(sc.turns, 1):
            snap = turn.snapshot(snap)
            for text in turn.memory:
                svc.memory.add(char, text)
            writer = Capture()
            started = time.monotonic()
            svc._answer({"type": "question", "msgId": i, "session": "eval", "nextInbox": 1, "conv": conv, "char": char,
                         "text": turn.question, "snapshot": snap}, writer, threading.Event())
            final = writer.final or {}
            if final.get("kind") == "answer":
                rec = svc.turns.load()[-1]
                res = {"question": turn.question, "answer": rec["answer"], "tools": rec["tools"], "did": rec["did"],
                       "secs": rec["secs"]}
            else:
                res = {"question": turn.question, "answer": "ERROR: " + str(final.get("text") or "no reply"),
                       "tools": [], "did": [], "secs": round(time.monotonic() - started, 1), "error": True}
            res["checks"] = [check(res) for check in turn.checks]
            out["passed"] += sum(r.ok for r in res["checks"])
            out["failed"] += sum(not r.ok for r in res["checks"])
            out["turns"].append(res)
    finally:
        svc.close()
    return out
