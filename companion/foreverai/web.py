"""Fetching web pages for the AI (builds, guides). Uses browser TLS impersonation (curl-impersonate via curl_cffi),
because sites behind Cloudflare (e.g. mobalytics.gg) answer plain HTTP clients with 403."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

from .kb.text import html_to_text

IMPERSONATE = ["chrome", "safari", "firefox"]
MAX_CHARS = 30000
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 600
_CACHE_MAX = 30
_STATE_MARKERS = ("window.__PRELOADED_STATE__=", "window.__INITIAL_STATE__=", "window.__NUXT__=")
_TEXT_KEYS = {"text", "label", "title", "name", "description", "value", "content", "subtitle"}


def _blocked(status: int, body: str) -> bool:
    if status in (403, 429, 503):
        return True
    head = body[:4000]
    return "Just a moment..." in head or "cf-chl" in head or "challenge-platform" in head


def _get(url: str) -> tuple[int, str, str, str]:
    """(status, text, content_type, final_url). Tries several browser fingerprints until one is not blocked."""
    try:
        from curl_cffi import requests as cr
    except ImportError:  # fallback without impersonation
        cr = None
    last = (0, "", "", url)
    if cr is not None:
        for imp in IMPERSONATE:
            try:
                r = cr.get(url, impersonate=imp, timeout=25, allow_redirects=True)
            except Exception as e:  # network errors, TLS errors
                last = (0, f"{type(e).__name__}: {e}", "", url)
                continue
            last = (r.status_code, r.text, r.headers.get("content-type", ""), str(r.url))
            if not _blocked(r.status_code, r.text):
                return last
        return last
    import httpx
    try:
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140 Safari/537.36"},
                      timeout=25, follow_redirects=True)
    except httpx.HTTPError as e:
        return 0, f"{type(e).__name__}: {e}", "", url
    return r.status_code, r.text, r.headers.get("content-type", ""), str(r.url)


def _public_host(url: str) -> bool:
    """Only public internet hosts: a fetched page must not steer the AI into local/private services."""
    import ipaddress
    import socket
    host = urlparse(url).hostname or ""
    if not host or host.lower() in ("localhost",) or host.lower().endswith((".local", ".internal", ".lan")):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable here -> the request itself will fail
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _embedded_strings(html: str, limit: int = 12000) -> str:
    """Readable strings from JSON state embedded by JS-heavy sites (__NEXT_DATA__, __PRELOADED_STATE__, ld+json)."""
    blobs = []
    for marker in _STATE_MARKERS:
        i = html.find(marker)
        if i >= 0:
            end = html.find("</script>", i)
            blobs.append(html[i + len(marker):end].strip().rstrip(";"))
    for m in re.finditer(r'<script[^>]+(?:id="__NEXT_DATA__"|type="application/ld\+json")[^>]*>(.*?)</script>', html, re.S):
        blobs.append(m.group(1))
    out: list[str] = []
    seen: set[str] = set()

    def walk(o) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _TEXT_KEYS and isinstance(v, str):
                    s = re.sub(r"\s+", " ", v).strip()
                    if (len(s) >= 2 and s not in seen and not s.startswith(("http", "/", "{", "<"))
                            and not re.fullmatch(r"[\w.-]{24,}", s)):
                        seen.add(s)
                        out.append(s)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for blob in blobs:
        try:
            data, _ = json.JSONDecoder().raw_decode(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        walk(data)
    return " ".join(out)[:limit]


def _wowhead_markup(html: str) -> str:
    """Wowhead guides ship their body as WH.markup.printHtml("<markup>") and render it with JavaScript."""
    parts = []
    for m in re.finditer(r'WH\.markup\.printHtml\(\s*"', html):
        try:
            s, _ = json.JSONDecoder().raw_decode(html[m.end() - 1:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(s, str) and len(s) > 200:
            parts.append(s)
    if not parts:
        return ""
    md = "\n\n".join(parts).replace("\r", "")
    md = re.sub(r"\[h([1-4])[^\]]*\](.*?)\[/h\1\]", lambda m: "\n" + "#" * (int(m.group(1)) + 1) + " " + m.group(2) + "\n", md)
    md = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", md)
    md = re.sub(r"\[(item|spell|npc|quest|zone|object|achievement|currency)=(\d+)[^\]]*\]", r"[\1 \2]", md)
    md = re.sub(r"\[/td\]\s*", " | ", md)
    md = re.sub(r"\[/tr\]", "\n", md)
    md = re.sub(r"\[li\]", "\n- ", md)
    md = re.sub(r"\[/?(?:b|i|u|center|left|right|pad|div|grid|table|tr|td|ul|ol|li|small|color|span|tooltip|toggler|"
                r"tabs|tab|icon|img|screenshot|minibox|toc|hr|code|quote)\b[^\]]*\]", "", md)
    md = re.sub(r"[ \t]+", " ", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def page_text(html: str, url: str) -> str:
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    wowhead = _wowhead_markup(html)
    body = re.sub(r"<(script|style|noscript|svg|nav|footer)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = wowhead or html_to_text(body)
    first_heading = text.find("\n## ")
    if 0 <= first_heading < len(text) * 0.5:  # drop site navigation before the first heading
        text = text[first_heading + 1:]
    text = re.sub(r"(?:\n(?:-\s*)?\S{1,3}\s*){6,}", "\n", text)  # runs of tiny menu fragments
    head = f"Title: {re.sub(r'\s+', ' ', title.group(1)).strip()}\n" if title and title.group(1).strip() else ""
    out = f"{head}URL: {url}\n\n{text}"
    if len(text) < 3000:  # JS-rendered page: the content lives in embedded JSON
        extra = _embedded_strings(html)
        if extra:
            out += "\n\n# Content from data embedded in the page\n" + extra
    return out


def fetch(url: str, max_chars: int = MAX_CHARS) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")) or not urlparse(url).netloc:
        return "Rejected: give a full http(s) address."
    if not _public_host(url):
        return "Rejected: only public web pages can be fetched."
    cached = _CACHE.get(url)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    status, body, ctype, final = _get(url)
    if status == 0:
        return f"Could not fetch the page: {body}"
    if _blocked(status, body):
        return (f"The site {urlparse(url).netloc} blocks automated access (HTTP {status}, bot protection). "
                f"Ask the player to paste the key information from the page.")
    if status != 200:
        return f"The page returned HTTP {status}."
    if "html" in ctype or body.lstrip().startswith("<"):
        text = page_text(body, final)
    elif "json" in ctype or "text" in ctype:
        text = body
    else:
        return f"Unsupported content type: {ctype}"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(truncated)"
    _CACHE[url] = (time.time(), text)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]))
    return text
