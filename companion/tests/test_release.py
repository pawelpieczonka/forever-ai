import re
from pathlib import Path

import httpx

from foreverai import __version__, updates

ROOT = Path(__file__).resolve().parents[2]


def test_version_is_the_same_everywhere():
    """The release workflow publishes the tag only when it matches; all four places must agree."""
    toc = re.search(r"^## Version: (.+)$", (ROOT / "addon/ForeverAI/ForeverAI.toc").read_text(), re.M).group(1)
    core = re.search(r'ns\.version = "(.+?)"', (ROOT / "addon/ForeverAI/Core.lua").read_text()).group(1)
    pyproject = re.search(r'^version = "(.+?)"', (ROOT / "companion/pyproject.toml").read_text(), re.M).group(1)
    assert toc == core == pyproject == __version__


def test_release_names_match_the_build_scripts():
    assert updates.asset_name("0.5.0", "Windows") == "ForeverAI-0.5.0-Windows-x64.zip"
    assert updates.asset_name("0.5.0", "Linux") == "ForeverAI-0.5.0-Linux-x86_64.tar.gz"
    assert "ForeverAI-$version-Linux-x86_64.tar.gz" in (ROOT / "build_linux.sh").read_text()
    assert "ForeverAI-$version-Windows-x64.zip" in (ROOT / "build_exe.ps1").read_text()
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "ForeverAI-*-Linux-x86_64.tar.gz" in workflow and "ForeverAI-*-Windows-x64.zip" in workflow


def test_version_order():
    assert updates.version_key("v0.10.0") > updates.version_key("0.9.9")
    assert updates.version_key("0.5") == updates.version_key("0.5.0")
    assert updates.version_key("0.5.0-rc1") < updates.version_key("0.5.0")


def _transport(payload, status=200):
    def handler(request):
        assert request.url.path == "/repos/pawelpieczonka/forever-ai/releases/latest"
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


def test_newer_release_from_github():
    rel = {"tag_name": "v9.1.0", "html_url": "https://github.com/pawelpieczonka/forever-ai/releases/tag/v9.1.0",
           "assets": [{"name": updates.asset_name("9.1.0"), "browser_download_url": "https://dl/x"}]}
    found = updates.newer_release("0.5.0", transport=_transport(rel))
    assert found == {"version": "9.1.0", "url": rel["html_url"], "download": "https://dl/x"}
    assert updates.newer_release("9.1.0", transport=_transport(rel)) is None  # already up to date
    assert updates.newer_release("0.5.0", transport=_transport({"message": "Not Found"}, 404)) is None
    assert updates.newer_release("0.5.0", transport=_transport(dict(rel, prerelease=True))) is None
