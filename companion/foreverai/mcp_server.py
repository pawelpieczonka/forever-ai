"""Minimal stdio MCP server exposing ForeverAI tools to Claude Code (newline-delimited JSON-RPC 2.0).

The server is a thin proxy: every tool call is forwarded to the running companion over a local HTTP bridge, so the
tools always work on the current question (game snapshot, requests to the game, memory, map marks).

Usage (started by Claude Code via --mcp-config):
  python -m foreverai.mcp_server --bridge http://127.0.0.1:<port>/tool --token-file <file>
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__
from .tools import TOOLS

PROTOCOL_VERSION = "2025-06-18"
BRIDGE_TIMEOUT_S = 90  # requests to the game wait for it


def _tool_list() -> list[dict]:
    return [{"name": t["function"]["name"], "description": t["function"]["description"],
             "inputSchema": t["function"]["parameters"]} for t in TOOLS]


def http_bridge(url: str, token: str) -> Callable[[str, dict], tuple[str, bool]]:
    def call(name: str, args: dict) -> tuple:
        req = urllib.request.Request(url, data=json.dumps({"name": name, "args": args}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Token": token})
        with urllib.request.urlopen(req, timeout=BRIDGE_TIMEOUT_S) as r:
            data = json.loads(r.read().decode("utf-8"))
        return str(data.get("text", "")), bool(data.get("isError"))
    return call


class Server:
    def __init__(self, call: Callable[[str, dict], tuple]):
        self.call = call

    def handle(self, req: dict) -> dict | None:
        method = req.get("method")
        rid = req.get("id")
        if rid is None:  # notification
            return None
        if method == "initialize":
            result = {"protocolVersion": req.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "foreverai", "version": __version__}}
        elif method == "tools/list":
            result = {"tools": _tool_list()}
        elif method == "tools/call":
            p = req.get("params") or {}
            try:
                text, is_error = self.call(p.get("name", ""), p.get("arguments") or {})
            except Exception as e:  # tool failures are reported to the model, not raised
                text, is_error = f"Tool error: {e}", True
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", required=True)
    ap.add_argument("--token-file", required=True)
    a = ap.parse_args()
    token = Path(a.token_file).read_text(encoding="utf-8").strip()
    server = Server(http_bridge(a.bridge, token))
    stdout = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        resp = server.handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


if __name__ == "__main__":
    main()
