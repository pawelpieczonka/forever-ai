"""New versions come as GitHub Releases: one download per platform, named after it
(ForeverAI-<version>-Windows-x64.zip, ForeverAI-<version>-Linux-x86_64.tar.gz). The app looks for a newer release once
per start and the window offers the download."""

from __future__ import annotations

import logging
import re

import httpx

from . import __version__
from .osutil import WINDOWS

log = logging.getLogger("foreverai")

REPO = "pawelpieczonka/forever-ai"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
PLATFORM = "Windows" if WINDOWS else "Linux"


def asset_name(version: str, platform: str = PLATFORM) -> str:
    """The release file of a platform (the release workflow uses the same names)."""
    return f"ForeverAI-{version}-Windows-x64.zip" if platform == "Windows" else f"ForeverAI-{version}-Linux-x86_64.tar.gz"


def version_key(v: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2); a pre-release suffix sorts before the release ('0.5.0-rc1' < '0.5.0')."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)$", str(v).strip())
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums += (0,) * (3 - len(nums))
    return nums + ((0,) if m.group(2) else (1,))


def latest_release(transport: httpx.BaseTransport | None = None, timeout: float = 10.0) -> dict | None:
    """{version, url, download} of the newest published release, or None (no release, offline, rate limit)."""
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True,
                          headers={"Accept": "application/vnd.github+json", "User-Agent": "ForeverAI"}) as c:
            r = c.get(LATEST_API)
        if r.status_code != 200:
            log.info("update check: GitHub answered %s", r.status_code)
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("update check failed: %s", e)
        return None
    version = str(data.get("tag_name") or "").lstrip("v")
    if not version or data.get("draft") or data.get("prerelease"):
        return None
    download = next((a.get("browser_download_url") for a in data.get("assets") or []
                     if a.get("name") == asset_name(version)), None)
    return {"version": version, "url": data.get("html_url") or RELEASES_URL, "download": download}


def newer_release(current: str = __version__, **kw) -> dict | None:
    rel = latest_release(**kw)
    if rel and version_key(rel["version"]) > version_key(current):
        return rel
    return None
