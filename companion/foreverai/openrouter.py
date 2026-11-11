"""Minimal OpenRouter client: streaming chat with tools, model list, credits and key info."""

from __future__ import annotations

import json
import time
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

# Free models (ids ending in ":free") cost nothing per token. OpenRouter limits them to 20 requests a minute and 50 a
# day, 1000 a day once 10 credits were bought; every tool round of an answer is a request.
FREE_ROUTER = "openrouter/free"  # OpenRouter picks a free model that can do what the request needs (tools, images)
# the free models of https://openrouter.ai/models?variant=free&order=most-popular on 2026-11-11 that chat and call
# tools, most used first: the app window offers the free models of OpenRouter's model list in this order, newer ones
# after them and the free router last
FREE_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "nvidia/nemotron-3.5-lightning:free",
    "dots-studio/dots-3-note-preview:free",
    "cohere/north-mini-code:free",
    "recursive/flint-v1.0:free",
    "thinkingmachines/inkling-2-small:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "liquid/lfm-2.5-2.6b:free",
    "liquid/lfm-3-1.7b:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
]


def is_free(model: str) -> bool:
    return model == FREE_ROUTER or str(model).endswith(":free")


class OpenRouterError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def friendly_error(status: int, message: str, free: bool = False, limit: dict | None = None) -> str:
    """``free``: the request was for a free model; ``limit``: OpenRouter's X-RateLimit-* headers of a 429 (only its
    own limits of free models have them, a provider's limit does not)."""
    if status == 401:
        return "Invalid OpenRouter key (401). Set a new key in Settings."
    if status == 402:
        if free:
            return "The OpenRouter balance is below zero (402): free models answer again once it is topped up."
        return "No credits left on OpenRouter (402). Top up the account."
    if status == 429:
        if limit or "free-models-per" in message:
            return _free_limit_error(message, limit or {})
        if free:
            return "This free model is busy at its provider (429). Try again in a moment or choose another one."
        return "Too many requests to OpenRouter (429). Try again in a moment."
    if status == 404:
        return f"Model not available (404): {message}"
    return f"OpenRouter error {status}: {message}"


def _free_limit_error(message: str, limit: dict) -> str:
    """OpenRouter's own limit of free models: 20 requests a minute, or the requests of the day."""
    try:
        cap = int(limit.get("x-ratelimit-limit") or 0)
    except ValueError:
        cap = 0
    if "per-min" in message or (cap and cap <= 20 and "per-day" not in message):
        return ("Free models on OpenRouter take 20 requests a minute, and one question can take several. "
                "Try again in a minute.")
    try:
        reset = time.strftime(" It resets at %H:%M.", time.localtime(int(limit["x-ratelimit-reset"]) / 1000))
    except (KeyError, ValueError, OverflowError, OSError):
        reset = ""
    more = " Buying 10 credits raises it to 1000 a day." if 0 < cap < 1000 else ""
    return (f"The free models' daily limit on OpenRouter is used up ({f'{cap} requests, ' if cap else ''}and one "
            f"question can take several).{reset}{more} Until then choose a paid model or another provider.")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str
    extra: dict | None = None  # provider data that must go back with the call (Gemini: thought signature)

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
            arch = m.get("architecture") or {}
            out.append({
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "in": round(pin, 4),
                "out": round(pout, 4),
                "ctx": m.get("context_length"),
                "tools": "tools" in (m.get("supported_parameters") or []),
                "image": "image" in (arch.get("input_modalities") or []),
                "text": "text" in (arch.get("output_modalities") or ["text"]),  # not music, images or embeddings
                "free": pin == 0 and pout == 0,
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
        if is_free(model) and model != FREE_ROUTER:
            # a free model that is busy or down at its provider: another free one answers (a paid one would cost)
            body["models"] = [FREE_ROUTER]
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
        ("model", str: the model that answers, once) | ("error", str) | ("done", finish_reason)."""
        body = self.build_body(model, messages, tools, search_mode, max_tokens)
        calls: dict[int, dict] = {}
        finish = None
        answered_by = None
        free = is_free(model)
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code != 200:
                    r.read()
                    limit = {k.lower(): v for k, v in r.headers.items() if k.lower().startswith("x-ratelimit-")}
                    yield "error", friendly_error(r.status_code, _error_text(r), free, limit)
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
                        yield "error", friendly_error(int(err.get("code") or 500), str(err.get("message")), free)
                        return
                    if chunk.get("model") and not answered_by:
                        answered_by = chunk["model"]
                        yield "model", answered_by
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
        raw = (err.get("metadata") or {}).get("raw")  # the provider's own words behind "Provider returned error"
        return (str(err.get("message") or j) + (f": {raw}" if isinstance(raw, str) and raw else ""))[:300]
    except Exception:
        return r.text[:300]
