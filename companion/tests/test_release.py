import base64
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from foreverai import __version__, updates

ROOT = Path(__file__).resolve().parents[2]
version_check = runpy.run_path(str(ROOT / ".github/version_check.py"))


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


def test_pull_request_version_step():
    problem = version_check["problem"]
    for new in ("0.5.3", "0.6.0", "1.0.0"):
        assert problem("0.5.2", new, ["companion/foreverai/web.py"]) is None
    for new in ("0.5.2", "0.5.4", "0.6.1", "0.5.1"):  # no step, two steps, back
        assert "raise it by one step, to 0.5.3" in problem("0.5.2", new, ["addon/ForeverAI/UI.lua"])
    assert problem("0.5.2", "0.5.2", []) is None
    assert "keep the version at 0.5.2" in problem("0.5.2", "0.5.3", [])


def test_pull_request_app_changes():
    base = {"addon/ForeverAI/Core.lua": 'ns.version = "0.5.2"\nlocal a = 1\n'}
    bump = {"addon/ForeverAI/Core.lua": 'ns.version = "0.5.3"\nlocal a = 1\n'}
    edit = {"addon/ForeverAI/Core.lua": 'ns.version = "0.5.3"\nlocal a = 2\n'}
    others = ["README.md", "CLAUDE.md", ".github/workflows/release.yml", "companion/tests/test_web.py",
              "companion/pyproject.toml"]
    apps = version_check["app_changes"]
    assert apps(others + ["addon/ForeverAI/Core.lua"], "0.5.2", "0.5.3", base.get, bump.get) == []
    assert apps(["addon/ForeverAI/Core.lua"], "0.5.2", "0.5.3", base.get, edit.get) == ["addon/ForeverAI/Core.lua"]
    changed = ["companion/foreverai/web.py", "companion/foreverai_app.py", "companion/requirements.txt",
               "seed/kb.sqlite", "assets/foreverai.png", "build_linux.sh", "build_exe.ps1"]
    assert apps(changed, "0.5.2", "0.5.3", base.get, bump.get) == changed


def test_pull_request_knowledge_base():
    kb_problem, day = version_check["kb_problem"], 24 * 3600
    fresh = {"wiki": (1000.0, ""), "blizzard": (2000.0, "")}
    assert kb_problem(["seed/kb.sqlite"], fresh, 1000.0 + day) is None
    assert "brings a freshly downloaded seed/kb.sqlite" in kb_problem(["companion/foreverai/web.py"], {}, 0)
    assert "after its last wiki sync" in kb_problem(["seed/kb.sqlite"], fresh, 1001.0 + day)
    assert "has no blizzard sync" in kb_problem(["seed/kb.sqlite"], {"wiki": (1000.0, "")}, 1000.0)
    assert "after its last blizzard sync" in kb_problem(["seed/kb.sqlite"], dict(fresh, wiki=(3000.0, "")),
                                                         2001.0 + day)
    failed = dict(fresh, blizzard=(2000.0, "https://x: timed out\nmore"))
    assert "blizzard sync in seed/kb.sqlite had errors (https://x: timed out)" in kb_problem(["seed/kb.sqlite"],
                                                                                              failed, 2000.0)


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
           "assets": [{"name": updates.asset_name("9.1.0"), "browser_download_url": "https://dl/x", "size": 1234,
                       "digest": "sha256:ab"},
                      {"name": updates.asset_name("9.1.0", "Windows" if updates.PLATFORM == "Linux" else "Linux"),
                       "browser_download_url": "https://dl/other-platform"},
                      {"name": "SHA256SUMS.txt", "browser_download_url": "https://dl/sums"},
                      {"name": "SHA256SUMS.txt.sig", "browser_download_url": "https://dl/sig"}]}
    found = updates.newer_release("0.5.0", transport=_transport(rel))
    assert found == {"version": "9.1.0", "url": rel["html_url"], "download": "https://dl/x", "size": 1234,
                     "digest": "sha256:ab", "sums": "https://dl/sums", "signature": "https://dl/sig"}
    assert updates.newer_release("9.1.0", transport=_transport(rel)) is None  # already up to date
    assert updates.newer_release("0.5.0", transport=_transport({"message": "Not Found"}, 404)) is None
    assert updates.newer_release("0.5.0", transport=_transport(dict(rel, prerelease=True))) is None


@pytest.mark.skipif(not (shutil.which("openssl") and shutil.which("bash")), reason="needs openssl and bash")
def test_the_release_job_signs_what_the_app_checks(tmp_path, monkeypatch):
    """.github/sign_sums.sh (the release job) signs SHA256SUMS.txt with openssl; the app checks the signature with
    cryptography. The job stops when the secret is missing or its key is not one that updates.py knows."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{'a' * 64}  {updates.asset_name('9.1.0', 'Windows')}\n"
                    f"{'b' * 64}  {updates.asset_name('9.1.0', 'Linux')}\n")
    source = tmp_path / "updates.py"

    def sign(release_key: str, keys: tuple) -> subprocess.CompletedProcess:
        source.write_text("RELEASE_KEYS = (" + "".join(f'"{k}", ' for k in keys) + ")\n")  # as updates.py has it
        (tmp_path / "SHA256SUMS.txt.sig").unlink(missing_ok=True)
        return subprocess.run(["bash", str(ROOT / ".github/sign_sums.sh"), str(sums), str(source)], capture_output=True,
                              text=True, env=dict(os.environ, RELEASE_SIGNING_KEY=release_key))
    assert sign(pem, ("another key", public)).returncode == 0  # one of the keys the app knows
    run = sign(pem, (public,))
    assert run.returncode == 0 and f"with the release key {public}" in run.stdout, run.stdout + run.stderr
    signature = (tmp_path / "SHA256SUMS.txt.sig").read_bytes()
    assert len(signature) == 64
    monkeypatch.setenv("FOREVERAI_RELEASE_KEY", public)
    updates.check_signature(sums.read_bytes(), signature)
    # the app's own keys are written so that the job finds them
    text = (ROOT / "companion/foreverai/updates.py").read_text()
    assert updates.RELEASE_KEYS and all(f'"{k}"' in text for k in updates.RELEASE_KEYS)
    # the secret is missing, or its key is not in updates.py: no signature, so no release
    run = sign("", (public,))
    assert run.returncode == 1 and "RELEASE_SIGNING_KEY is empty" in run.stdout
    run = sign(pem, ("another key",))
    assert run.returncode == 1 and f"public half {public} is not in RELEASE_KEYS" in run.stdout
    p256 = ec.generate_private_key(ec.SECP256R1()).private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    run = sign(p256.decode(), (public,))
    assert run.returncode == 1 and "RELEASE_SIGNING_KEY is not an Ed25519 key" in run.stdout
    assert not (tmp_path / "SHA256SUMS.txt.sig").exists()
    # the workflow runs it before it publishes the signature with the archives
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "../.github/sign_sums.sh SHA256SUMS.txt ../companion/foreverai/updates.py" in workflow
    assert "RELEASE_SIGNING_KEY: ${{ secrets.RELEASE_SIGNING_KEY }}" in workflow
    assert 'gh release create "v$VERSION" ForeverAI-* SHA256SUMS.txt SHA256SUMS.txt.sig' in workflow
