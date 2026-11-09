"""Claude Code as an AI provider: a headless `claude -p` session on the user's Claude subscription.

The model gets only the ForeverAI tools (served by this app over local MCP) and, depending on the search setting,
web search; no file, shell or code tools. Questions go through stdin, the system prompt through a file.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

from .osutil import NO_WINDOW, WINDOWS, child_env

log = logging.getLogger("foreverai")

CLAUDE_MODELS = [
    {"id": "claude-haiku-5-5", "name": "Haiku 5.5"},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5"},
    {"id": "claude-opus-5-5", "name": "Opus 5.5"},
    {"id": "claude-fable-5-1", "name": "Fable 5.1"},
]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_MODEL = "claude-haiku-5-5"  # fast; "high" thinking keeps answers careful
DEFAULT_EFFORT = "high"
MCP_TOOLS = ["mcp__foreverai__search_knowledge_base", "mcp__foreverai__propose_addon_change",
             "mcp__foreverai__save_memory", "mcp__foreverai__delete_memory", "mcp__foreverai__fetch_url",
             "mcp__foreverai__mark_on_map", "mcp__foreverai__addon_options", "mcp__foreverai__propose_quest_abandon",
             "mcp__foreverai__propose_vendor_action", "mcp__foreverai__search_auction_house"]
TIMEOUT_S = 300


def _version_key(name: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in name.lstrip("v").split("."))


def candidate_executables() -> list[str]:
    """Every place Claude Code can live, most likely first. Windows (the npm .cmd shim is avoided: cmd.exe mangles
    arguments): npm global install, the native installer, the old local install, and the copy bundled with the
    Claude desktop app (%APPDATA%\\Claude\\claude-code\\<version>\\…\\claude.exe, newest version first).
    Linux: PATH and the same installs, the desktop app copy being ~/.config/Claude/claude-code/<version>/…/claude."""
    out: list[str] = []
    first = _find_listed()
    if first:
        out.append(first)
    if WINDOWS:
        bundled = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude" / "claude-code"
    else:
        bundled = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Claude" / "claude-code"
    if bundled.is_dir():
        for ver in sorted((d for d in bundled.iterdir() if d.is_dir()), key=lambda d: _version_key(d.name),
                          reverse=True):
            for exe in ver.rglob("claude.exe" if WINDOWS else "claude"):
                if exe.is_file():
                    out.append(str(exe))
    seen, unique = set(), []
    for c in out:
        if os.path.normcase(c) not in seen:
            seen.add(os.path.normcase(c))
            unique.append(c)
    return unique


def find_executable() -> str | None:
    cands = candidate_executables()
    return cands[0] if cands else None


def _find_listed() -> str | None:
    """Locate the real claude executable on PATH or in the usual install folders."""
    return _find_listed_windows() if WINDOWS else _find_listed_unix()


def _find_listed_unix() -> str | None:
    """PATH first, then the folders a desktop launcher's PATH often lacks: the native installer (~/.local/bin), the
    old local install, npm/bun/volta prefixes and nvm (newest node first)."""
    which = shutil.which("claude")
    if which:
        return which
    home = Path.home()
    candidates = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude",
                  home / ".npm-global" / "bin" / "claude", home / ".bun" / "bin" / "claude",
                  home / ".volta" / "bin" / "claude", Path("/usr/local/bin/claude"), Path("/usr/bin/claude")]
    candidates += sorted((home / ".nvm" / "versions" / "node").glob("*/bin/claude"),
                         key=lambda c: _version_key(c.parents[1].name), reverse=True)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _find_listed_windows() -> str | None:
    candidates: list[Path] = []
    which = shutil.which("claude")
    if which:
        p = Path(which)
        if p.suffix.lower() in (".cmd", ".ps1", ""):
            candidates.append(p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
        if p.suffix.lower() == ".exe":
            candidates.append(p)
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates += [
        appdata / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude.exe",
        home / ".claude" / "local" / "claude.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _env(exe: str, **extra: str) -> dict[str, str]:
    """Environment for a claude process. Linux: its own folder goes first on PATH, since an npm/nvm install is a node
    script that needs the node next to it, and a desktop launcher's PATH may not have it."""
    env = child_env(**extra)
    if not WINDOWS:
        env["PATH"] = os.pathsep.join(p for p in (str(Path(exe).parent), env.get("PATH")) if p)
    return env


_detect_cache: tuple[dict | None, float] | None = None


def detect(force: bool = False) -> dict | None:
    """{path, version, loggedIn, authMethod, subscription} or None when Claude Code is not installed."""
    global _detect_cache
    if not force and _detect_cache and time.time() - _detect_cache[1] < 600:
        return _detect_cache[0]
    info = None
    for exe in candidate_executables():
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                                 env=_env(exe), creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("claude candidate %s failed: %s", exe, e)
            continue
        # only a binary that reports itself as Claude Code (not e.g. the Claude desktop app or another "claude")
        if "Claude Code" not in (out.stdout or ""):
            log.info("skipping %s: not Claude Code (%r)", exe, (out.stdout or "")[:60])
            continue
        candidate = {"path": exe, "version": out.stdout.strip().split(" ")[0] or None, "loggedIn": False,
                     "authMethod": None, "subscription": None}
        try:
            st = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30,
                                env=_env(exe), creationflags=NO_WINDOW)
            data = json.loads(st.stdout or "{}")
            candidate["loggedIn"] = bool(data.get("loggedIn"))
            candidate["authMethod"] = data.get("authMethod")
            candidate["subscription"] = data.get("subscriptionType")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log.warning("claude auth status failed for %s: %s", exe, e)
        if info is None or (candidate["loggedIn"] and not info["loggedIn"]):
            info = candidate
        if info["loggedIn"]:
            break
    _detect_cache = (info, time.time())
    return info


_detect_refreshing = False


def detect_cached() -> dict | None:
    """Last detection result without blocking; an expired result is refreshed in the background."""
    global _detect_refreshing
    if _detect_cache is None:
        return detect()
    if time.time() - _detect_cache[1] > 600 and not _detect_refreshing:
        _detect_refreshing = True

        def run():
            global _detect_refreshing
            try:
                detect(force=True)
            finally:
                _detect_refreshing = False
        threading.Thread(target=run, daemon=True).start()
    return _detect_cache[0]


def flatten_messages(messages: list[dict]) -> tuple[str, str]:
    """OpenAI-style message list -> (system prompt, single user prompt with the earlier turns inline)."""
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    rest = messages[1:] if system else messages
    *history, last = rest
    parts = []
    if history:
        parts.append("# Earlier conversation (for context)")
        for m in history:
            who = "Player" if m["role"] == "user" else "You (ForeverAI)"
            parts.append(f"{who}: {m['content']}")
        parts.append("")
    parts.append(last["content"])
    return system, "\n".join(parts)


def _parse_stream(get_event, on_tool) -> Iterator[tuple[str, object]]:
    """Shared parser of Claude Code stream-json events for one turn.
    Yields ("text", full_text) / ("phase", str) / ("final", text) / ("error", str); stops at the end of the turn."""
    text = ""
    while True:
        ev = get_event()
        if ev is None:
            yield "error", "Claude Code ended without an answer."
            return
        kind = ev.get("type")
        if kind == "stream_event":
            e = ev.get("event") or {}
            et = e.get("type")
            if et == "message_start":
                text = ""
            elif et == "content_block_start":
                block = e.get("content_block") or {}
                if block.get("type") in ("tool_use", "server_tool_use"):
                    phase = on_tool(block.get("name", ""))
                    if phase:
                        yield "phase", phase
            elif et == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    text += d["text"]
                    yield "text", text
            elif et == "message_delta":
                stop = (e.get("delta") or {}).get("stop_reason")
                if stop in ("end_turn", "max_tokens", "stop_sequence") and text:
                    # the answer is complete; Claude Code would still spend ~2-3 s on a post-turn summary
                    yield "final", text
                    return
        elif kind == "result":
            if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                yield "error", f"Claude Code: {ev.get('result') or ev.get('subtype')}"
            else:
                yield "final", ev.get("result") or text
            return


def tool_phase(name: str) -> str | None:
    for key, phase in (("search_knowledge_base", "Searching knowledge base…"), ("propose_addon_change", "Preparing a change…"),
                       ("fetch_url", "Reading page…"), ("mark_on_map", "Marking the map…"),
                       ("save_memory", "Saving to memory…"), ("delete_memory", "Updating memory…"),
                       ("addon_options", "Checking add-on options…"), ("propose_quest_abandon", "Preparing quest abandon…"),
                       ("propose_vendor_action", "Preparing vendor list…"),
                       ("search_auction_house", "Searching the auction house…")):
        if key in name:
            return phase
    if "web" in name.lower():
        return "Searching the web…"
    return None


class ClaudeSession:
    """A long-lived `claude -p --input-format stream-json` process: no start-up cost per question, the conversation
    stays in Claude's context (prompt cache), and it is started ahead of time."""

    def __init__(self, exe: str, workdir: Path, system: str, model: str, effort: str, search_mode: str,
                 bridge_url: str, bridge_token: str):
        import queue
        self.key = (model, effort, search_mode, system)
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "system.txt").write_text(system, encoding="utf-8")
        # the ForeverAI tools are served by the app itself (MCP over local HTTP): no extra process, works in the .exe
        mcp = {"mcpServers": {"foreverai": {"type": "http", "url": bridge_url, "headers": {"X-Token": bridge_token}}}}
        (self.workdir / "mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        # pages are read with our fetch_url tool (browser impersonation; Claude's own WebFetch gets 403 on Cloudflare
        # sites like mobalytics.gg); WebSearch follows the search mode
        builtin = "" if search_mode == "never" else "WebSearch"
        allowed = MCP_TOOLS + ([builtin] if builtin else [])
        cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", model, "--effort", effort,
               "--system-prompt-file", str(self.workdir / "system.txt"), "--tools", builtin,
               "--mcp-config", str(self.workdir / "mcp.json"), "--strict-mcp-config",
               "--allowedTools", ",".join(allowed), "--permission-prompts", "none",
               "--setting-sources", "project",  # no user plugins/hooks/CLAUDE.md in the game assistant
               "--no-session-persistence", "--disable-slash-commands"]
        self.proc = subprocess.Popen(cmd, cwd=str(self.workdir), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=_env(exe, PYTHONIOENCODING="utf-8"),
                                     creationflags=NO_WINDOW)
        self.queue: "queue.Queue[dict | None]" = queue.Queue()
        self.conv: str | None = None  # conversation this session holds in its context
        self.turns = 0
        self.sent_chars = 0  # everything sent in this session (its context grows with every question)
        self.busy = False
        self._unfinished = False  # the last turn was answered at message_delta; its `result` is still to come
        self.last_used = time.time()
        threading.Thread(target=self._reader, daemon=True, name="claude-session").start()

    def _reader(self) -> None:
        for raw in self.proc.stdout:
            try:
                ev = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":  # sent at the start of every turn
                status = {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}
                if status != getattr(self, "mcp_status", None):
                    self.mcp_status = status
                    log.log(logging.WARNING if any(v != "connected" for v in status.values()) else logging.INFO,
                            "claude session mcp: %s", status)
            self.queue.put(ev)
        self.queue.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        if self.alive():
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.kill()

    def _settle(self, timeout: float = 8.0) -> bool:
        """Consume what is left of the previous turn (post-turn summary, `result`) before the next one starts."""
        import queue
        if not self._unfinished:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = self.queue.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if ev is None:
                self.queue.put(None)
                return False
            if ev.get("type") == "result":
                self._unfinished = False
                return True
        return False

    def turn(self, prompt: str, images: list[dict] | None = None,
             timeout: float = TIMEOUT_S) -> Iterator[tuple[str, object]]:
        import queue
        if not self._settle():
            yield "error", "Claude Code did not finish the previous answer."
            return
        content: list[dict] = [{"type": "text", "text": prompt}] + list(images or [])
        line = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)
        try:
            self.proc.stdin.write(line.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError as e:
            yield "error", f"Claude Code session interrupted: {e}"
            return
        self.busy = True
        self.turns += 1
        self.sent_chars += len(prompt)
        self.last_used = time.time()
        deadline = time.time() + timeout

        def get_event():
            try:
                ev = self.queue.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                return None
            if ev is None:
                self.queue.put(None)
            return ev
        finished = False
        try:
            for kind, val in _parse_stream(get_event, tool_phase):
                if kind == "final":
                    finished = True
                yield kind, val
        finally:
            self.busy = False
            self.last_used = time.time()
            if finished:
                self._unfinished = True
            else:  # cancelled, failed or timed out mid-turn: this process is no longer in a known state
                self.close()
