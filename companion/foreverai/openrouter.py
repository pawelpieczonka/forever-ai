"""Minimal OpenRouter client: streaming chat with tools, model list, credits and key info."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

BASE_URL = "https://openrouter.ai/api/v1"
APP_HEADERS = {"X-Title": "ForeverAI", "HTTP-Referer": "https://github.com/foreverai-local"}

WEB_PREFERRED_DOMAINS = [
    "warcraft.wiki.gg",
    "worldofwarcraft.blizzard.com",
    "us.forums.blizzard.com",
    "eu.forums.blizzard.com",
    "www.wowhead.com",
    "www.warcrafttavern.com",
]


class OpenRouterError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def friendly_error(status: int, message: str) -> str:
    if status == 401:
        return "Invalid OpenRouter key (401). Set a new key in Settings."
    if status == 402:
        return "No credits left on OpenRouter (402). Top up the account."
    if status == 429:
        return "Too many requests to OpenRouter (429). Try again in a moment."
    if status == 404:
        return f"Model not available (404): {message}"
    return f"OpenRouter error {status}: {message}"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def args(self) -> dict:
        try:
            return json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}


class OpenRouter:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None, timeout: float = 120.0):
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {api_key}", **APP_HEADERS},
            timeout=httpx.Timeout(timeout, connect=15.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ---------------------------------------------------------------- simple endpoints
    def _get(self, path: str) -> Any:
        r = self._client.get(path)
        if r.status_code != 200:
            raise OpenRouterError(r.status_code, _error_text(r))
        return r.json()

    def credits(self) -> float:
        d = self._get("/credits")["data"]
        return float(d["total_credits"]) - float(d["total_usage"])

    def key_info(self) -> dict:
        return self._get("/key")["data"]

    def models(self) -> list[dict]:
        out = []
        for m in self._get("/models")["data"]:
            p = m.get("pricing") or {}
            try:
                pin, pout = float(p.get("prompt", 0)) * 1e6, float(p.get("completion", 0)) * 1e6
            except (TypeError, ValueError):
                continue
            out.append({
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "in": round(pin, 4),
                "out": round(pout, 4),
                "ctx": m.get("context_length"),
                "tools": "tools" in (m.get("supported_parameters") or []),
                "image": "image" in ((m.get("architecture") or {}).get("input_modalities") or []),
            })
        return out

    # ---------------------------------------------------------------- chat
    def build_body(self, model: str, messages: list[dict], tools: list[dict] | None, search_mode: str,
                   max_tokens: int = 1800) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "usage": {"include": True},
            "reasoning": {"effort": "low", "exclude": True},
        }
        all_tools = list(tools or [])
        if search_mode == "auto":
            all_tools.append({"type": "openrouter:web_search",
                              "parameters": {"max_results": 5, "search_context_size": "medium"}})
            body["max_tool_calls"] = 6
        elif search_mode == "always":
            body["plugins"] = [{"id": "web", "max_results": 5}]
        if all_tools:
            body["tools"] = all_tools
        return body

    def chat_stream(self, model: str, messages: list[dict], tools: list[dict] | None = None,
                    search_mode: str = "auto", max_tokens: int = 1800) -> Iterator[tuple[str, Any]]:
        """Yield ("text", str) | ("tool_calls", [ToolCall]) | ("annotations", list) | ("usage", dict) |
        ("error", str) | ("done", finish_reason)."""
        body = self.build_body(model, messages, tools, search_mode, max_tokens)
        calls: dict[int, dict] = {}
        finish = None
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code != 200:
                    r.read()
                    yield "error", friendly_error(r.status_code, _error_text(r))
                    return
                for line in r.iter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "error" in chunk:
                        err = chunk["error"]
                        yield "error", friendly_error(int(err.get("code") or 500), str(err.get("message")))
                        return
                    if chunk.get("usage"):
                        yield "usage", chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield "text", delta["content"]
                        if delta.get("annotations"):
                            yield "annotations", delta["annotations"]
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
        except httpx.HTTPError as e:
            yield "error", f"Cannot connect to OpenRouter: {e}"
            return
        if calls:
            yield "tool_calls", [ToolCall(c["id"], c["name"], c["arguments"]) for _, c in sorted(calls.items())]
        yield "done", finish


def _error_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        err = j.get("error") or {}
        return str(err.get("message") or j)[:300]
    except Exception:
        return r.text[:300]
