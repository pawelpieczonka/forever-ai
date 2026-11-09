"""Local HTTP endpoint inside the app.

* ``/mcp`` — an MCP server (streamable HTTP transport, JSON responses) that Claude Code connects to directly, so the
  ForeverAI tools run in this process: no extra Python process is needed (important for the packaged .exe).
* ``/tool`` — the same tools as plain JSON, used by the stdio MCP server for development.

Only 127.0.0.1 is bound and every request must carry the random per-run token in the ``X-Token`` header.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

log = logging.getLogger("foreverai")


class Bridge:
    def __init__(self, handler: Callable[[str, dict], tuple]):
        from .mcp_server import Server
        self.token = secrets.token_urlsafe(24)
        token, call = self.token, handler
        mcp = Server(handler)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # quiet
                pass

            def _authorized(self) -> bool:
                return hmac.compare_digest(self.headers.get("X-Token", ""), token)

            def _reply(self, status: int, payload=None, extra: dict | None = None) -> None:
                data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def do_GET(self):  # no server-initiated stream
                self._reply(405 if self._authorized() else 403)

            def do_DELETE(self):  # session end
                self._reply(200 if self._authorized() else 403)

            def do_POST(self):
                if not self._authorized() or self.path not in ("/mcp", "/tool"):
                    self._reply(403)
                    return
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                except json.JSONDecodeError:
                    self._reply(400)
                    return
                if self.path == "/mcp":
                    requests = body if isinstance(body, list) else [body]
                    responses = [r for r in (mcp.handle(q) for q in requests if isinstance(q, dict)) if r is not None]
                    if not responses:  # only notifications / responses
                        self._reply(202)
                    else:
                        self._reply(200, responses if isinstance(body, list) else responses[0],
                                    {"Mcp-Session-Id": "foreverai"})
                    return
                try:
                    text, is_error = call(str(body.get("name", "")), body.get("args") or {})
                except Exception as e:
                    log.exception("bridge tool failed")
                    text, is_error = f"Tool error: {e}", True
                self._reply(200, {"text": text, "isError": is_error})

        class Server_(ThreadingHTTPServer):
            def handle_error(self, request, client_address):  # clients closing keep-alive connections are normal
                import sys
                if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
                    log.exception("bridge request failed")

        self.httpd = Server_(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}/tool"
        self.mcp_url = f"http://127.0.0.1:{port}/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="foreverai-bridge").start()

    def close(self) -> None:
        self.httpd.shutdown()
