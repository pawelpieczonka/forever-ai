"""Google Gemini API client with the same interface as the OpenRouter client.

Chat goes through Gemini's OpenAI-compatible endpoint (streaming, function tools, images). Web search uses Grounding
with Google Search, which that endpoint does not offer for chat: the model gets a ``web_search`` function and every call
runs as its own grounded ``generateContent`` request.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Generator, Iterator

import httpx

from .openrouter import ToolCall

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-flash-latest"  # Google's alias for the newest Flash model
# answers when the chosen model cannot (overloaded, out of quota, not answering): a lighter model on its own quota
FALLBACK_MODEL = "gemini-flash-lite-latest"
# statuses after which another model may still answer
UNAVAILABLE_STATUSES = (404, 429, 500, 503, 504)
# An overloaded model returns 503 to a native request, but on the OpenAI-compatible endpoint it often never answers.
# When an answer has not started after CHECK_AFTER_S, a tiny native request (probe) tells such a model from a busy one.
# Measured 2026-11-10 with a 30k-token question and a screenshot: Flash models start in 3-9 s (rarely ~20 s), Flash-Lite
# in 1-3 s; a model that serves answers the probe in about a second.
CHECK_AFTER_S = 10.0
PROBE_TIMEOUT_S = 8.0
FAVORITE_MODELS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                   "gemini-flash-lite-latest", "gemini-4-argon"]
KEY_URL = "https://aistudio.google.com/apikey"
# Gemini counts its thinking against the output limit; without room for it a short limit ends answers early
THINKING_ALLOWANCE = 4096
# Gemini 3+ requires the thought signature of a function call to come back with it; this documented placeholder
# skips the check when a response carried none
SKIP_SIGNATURE = "skip_thought_signature_validator"
_NOT_CHAT = ("embedding", "tts", "image", "live", "audio", "transcribe", "aqa", "banana", "robotics", "computer-use")

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web (Google Search) for current information that is not in the game data or the "
                       "knowledge base: Forever news, patch notes, builds, guides. Returns a short summary with sources. "
                       "query = a few keywords, e.g. 'WoW Forever Skyborne racial'.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
}


class GeminiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def friendly_error(status: int, message: str) -> str:
    low = message.lower()
    if status in (401, 403) or "api key" in low or "api_key" in low:
        return f"Google rejected the Gemini key ({status}). Set a new key in Settings."
    if status == 429:
        return "Gemini quota or rate limit reached (429). Wait a moment or check the plan in Google AI Studio."
    if status == 404:
        return f"Gemini model not available (404): {message}"
    if status in (500, 503):
        return f"Gemini is overloaded or unavailable ({status}). Try again in a moment."
    return f"Gemini error {status}: {message}"


def is_chat_model(model_id: str) -> bool:
    return model_id.startswith("gemini") and not any(word in model_id for word in _NOT_CHAT)


class Gemini:
    # the read timeout is also the longest wait for the first byte, for a model that answered the probe
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None, timeout: float = 45.0,
                 check_after: float = CHECK_AFTER_S, probe_timeout: float = PROBE_TIMEOUT_S):
        self.api_key = api_key
        self.check_after = check_after
        self.probe_timeout = probe_timeout
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"x-goog-api-key": api_key},
            timeout=httpx.Timeout(timeout, connect=15.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # ---------------------------------------------------------------- simple endpoints
    def _get(self, path: str, **params) -> Any:
        r = self._client.get(path, params=params or None)
        if r.status_code != 200:
            raise GeminiError(r.status_code, _error_text(r))
        return r.json()

    def key_info(self) -> dict:
        """Checks the key (raises GeminiError when Google rejects it)."""
        self._get("/models", pageSize=1)
        return {"label": "Gemini API key"}

    def models(self) -> list[dict]:
        """Text models that can chat: [{id, name, ctx, image, tools}] (every Gemini chat model reads images and
        calls functions)."""
        out, token = [], None
        for _ in range(5):
            data = self._get("/models", pageSize=1000, **({"pageToken": token} if token else {}))
            for m in data.get("models") or []:
                mid = str(m.get("name", "")).removeprefix("models/")
                if "generateContent" not in (m.get("supportedGenerationMethods") or []) or not is_chat_model(mid):
                    continue
                out.append({"id": mid, "name": m.get("displayName") or mid, "ctx": m.get("inputTokenLimit"),
                            "image": True, "tools": True})
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    # ---------------------------------------------------------------- chat
    def build_body(self, model: str, messages: list[dict], tools: list[dict] | None, search_mode: str,
                   max_tokens: int = 1800) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens + THINKING_ALLOWANCE,
            "reasoning_effort": "low",
        }
        all_tools = list(tools or [])
        if search_mode != "never":
            all_tools.append(WEB_SEARCH_TOOL)
        if all_tools:
            body["tools"] = all_tools
        return body

    def chat_stream(self, model: str, messages: list[dict], tools: list[dict] | None = None,
                    search_mode: str = "auto", max_tokens: int = 1800) -> Iterator[tuple[str, Any]]:
        """Yield ("text", str) | ("tool_calls", [ToolCall]) | ("usage", dict) | ("error", str) |
        ("done", finish_reason), like OpenRouter.chat_stream; ("unavailable", str) is an error after which another
        model may still answer (overloaded, out of quota, missing or not answering)."""
        body = self.build_body(model, messages, tools, search_mode, max_tokens)
        # the request runs in its own thread, so that a slow start can be checked while it waits
        events: queue.Queue = queue.Queue()
        stop = threading.Event()
        threading.Thread(target=_pump, args=(self._stream(model, body), events, stop), daemon=True,
                         name="gemini-chat").start()
        check = threading.Timer(self.check_after, lambda: events.put((_PROBED, self.probe(model))))
        check.daemon = True
        check.start()
        started = False
        try:
            while (item := events.get()) is not None:
                if isinstance(item, Exception):
                    raise item
                kind, val = item
                if kind == _PROBED:
                    if val and not started:  # still no answer, and the model cannot answer now: do not wait for it
                        yield "unavailable", val
                        return
                    continue
                if not started:
                    started = True
                    check.cancel()
                if kind != _STARTED:
                    yield kind, val
        finally:
            check.cancel()
            stop.set()  # the request ends with its next line or its timeout

    def probe(self, model: str) -> str | None:
        """Why ``model`` cannot answer now (overloaded, out of quota, missing, no reply to a tiny request), or None
        when it can (or the check itself failed). A native request: there an overloaded model returns 503 at once."""
        body = {"contents": [{"role": "user", "parts": [{"text": "Reply with OK."}]}],
                "generationConfig": {"maxOutputTokens": 1}}
        try:
            r = self._client.post(f"/models/{model}:generateContent", json=body, timeout=self.probe_timeout)
        except httpx.TimeoutException:
            return f"{model} is not answering (no reply to a short test request within {self.probe_timeout:.0f} s)."
        except httpx.HTTPError:
            return None
        return friendly_error(r.status_code, _error_text(r)) if r.status_code in UNAVAILABLE_STATUSES else None

    def _stream(self, model: str, body: dict) -> Generator[tuple[str, Any], None, None]:
        calls: dict[int, dict] = {}
        finish = None
        try:
            with self._client.stream("POST", "/openai/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {self.api_key}"}) as r:
                if r.status_code != 200:
                    r.read()
                    yield _error_kind(r.status_code), friendly_error(r.status_code, _error_text(r))
                    return
                yield _STARTED, None  # (the headers come with the first chunk, once the model has thought)
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk, list):  # errors come as a one-element list
                        chunk = chunk[0] if chunk and isinstance(chunk[0], dict) else {}
                    if "error" in chunk:
                        err = chunk["error"] or {}
                        status = int(err.get("code") or 500)
                        yield _error_kind(status), friendly_error(status, str(err.get("message")))
                        return
                    if chunk.get("usage"):
                        yield "usage", chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield "text", delta["content"]
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index")
                            slot = calls.setdefault(len(calls) if idx is None else idx,
                                                    {"id": "", "name": "", "arguments": "", "extra": None})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            if tc.get("extra_content"):
                                slot["extra"] = tc["extra_content"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                args = fn["arguments"]
                                slot["arguments"] += args if isinstance(args, str) else json.dumps(args)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
        except httpx.ReadTimeout:
            yield "unavailable", f"{model} did not answer within {self._client.timeout.read:.0f} s."
            return
        except httpx.HTTPError as e:
            yield "error", f"Cannot connect to Gemini: {e}"
            return
        if calls:
            ordered = [c for _, c in sorted(calls.items())]
            if not any(_signature(c["extra"]) for c in ordered):
                ordered[0]["extra"] = {"google": {"thought_signature": SKIP_SIGNATURE}}
            yield "tool_calls", [ToolCall(c["id"], c["name"], c["arguments"], c["extra"]) for c in ordered]
        yield "done", finish

    # ---------------------------------------------------------------- web search (grounding)
    def web_search(self, model: str, query: str) -> tuple[str, list[dict]]:
        """A grounded answer to the query and its sources [{title, url}]."""
        body = {
            "contents": [{"role": "user", "parts": [{"text": (
                "Search the web and summarize what you find for this World of Warcraft: Forever question. Keep "
                "names as in the game, include dates when the sources give them, at most ~250 words.\n\n" + query)}]}],
            "tools": [{"google_search": {}}],
        }
        try:
            r = self._client.post(f"/models/{model}:generateContent", json=body)
        except httpx.HTTPError as e:
            return f"Web search failed: {e}", []
        if r.status_code != 200:
            return "Web search failed: " + friendly_error(r.status_code, _error_text(r)), []
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []
                       if not p.get("thought")).strip()
        sources, seen = [], set()
        for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks") or []:
            web = ch.get("web") or {}
            if web.get("uri") and web["uri"] not in seen:
                seen.add(web["uri"])
                sources.append({"title": web.get("title") or web["uri"], "url": web["uri"]})
        if sources:
            text += "\n\nSources: " + "; ".join(f"{s['title']} <{s['url']}>" for s in sources[:8])
        return text or "No web results.", sources


def drop_signatures(messages: list[dict]) -> None:
    """Before another model continues a turn: a thought signature is only valid for the model that wrote it, so the
    tool calls so far get the documented placeholder."""
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if tc.get("extra_content"):
                tc["extra_content"] = {"google": {"thought_signature": SKIP_SIGNATURE}}


_STARTED, _PROBED = "_started", "_probed"  # chat_stream's own events, never yielded


def _pump(events: Generator, out: queue.Queue, stop: threading.Event) -> None:
    """Pass what a generator yields (or raises) to ``out``, then None; it stops early once ``stop`` is set."""
    try:
        for item in events:
            if stop.is_set():
                break
            out.put(item)
    except Exception as e:
        out.put(e)
    finally:
        events.close()
        out.put(None)


def _error_kind(status: int) -> str:
    return "unavailable" if status in UNAVAILABLE_STATUSES else "error"


def _signature(extra: dict | None) -> str | None:
    return ((extra or {}).get("google") or {}).get("thought_signature")


def _error_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        if isinstance(j, list):
            j = j[0] if j else {}
        err = j.get("error") or {}
        return str(err.get("message") or j)[:300]
    except Exception:
        return r.text[:300]
