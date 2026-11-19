"""The built app updates itself. The CI runs this after building each platform; locally, after a build:

    FOREVERAI_BUILT_APP="$PWD/../release/linux/ForeverAI-Linux" .venv/bin/python -m pytest -q tests/test_update_e2e.py

A copy of the built app plays the installed one. A second copy, with another version in its add-on's .toc and a file to
recognize it by, is the new release, served from this computer with SHA256SUMS.txt like a GitHub release. The installed
copy runs --update: it downloads and checks the archive, unpacks both copies next to its folder and starts the new
version's copy, which checks that it loads in the frozen app and installs it once --update has ended."""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from foreverai import updates

BUILT = os.environ.get("FOREVERAI_BUILT_APP")
pytestmark = pytest.mark.skipif(not BUILT, reason="set FOREVERAI_BUILT_APP to the folder of a built app")
NEW = "99.0.0"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _release(built: Path, top: str, platform: str, serve: Path) -> str:
    """The new release in ``serve``: the archive as the build scripts pack it. Returns its name."""
    rel = serve.parent / "rel"
    shutil.copytree(built, rel / top, symlinks=True)
    toc = rel / top / "_internal" / "addon" / "ForeverAI" / "ForeverAI.toc"
    toc.write_text(re.sub(r"(?m)^## Version: .*$", f"## Version: {NEW}", toc.read_text(encoding="utf-8")),
                   encoding="utf-8")
    (rel / top / "_internal" / "update-e2e.txt").write_text(NEW, encoding="utf-8")
    name = updates.asset_name(NEW, platform)
    if platform == "Windows":
        with zipfile.ZipFile(serve / name, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted((rel / top).rglob("*")):
                z.write(p, p.relative_to(rel).as_posix())
    else:
        with tarfile.open(serve / name, "w:gz") as t:
            t.add(rel / top, arcname=top)  # with the executable bit and the library links
    return name


def test_the_built_app_installs_a_newer_release(tmp_path):
    built = Path(BUILT).resolve()
    platform = "Windows" if (built / "ForeverAI-Windows.exe").is_file() else "Linux"
    top, exe = updates.app_name(platform), updates.exe_name(platform)
    assert (built / exe).is_file(), f"no {exe} in {built}"
    old_version = updates.bundled_version(built)
    games = tmp_path / "Games"
    shutil.copytree(built, games / top, symlinks=True)  # the installed app
    serve = tmp_path / "serve"
    serve.mkdir()
    name = _release(built, top, platform, serve)
    digest = hashlib.sha256((serve / name).read_bytes()).hexdigest()
    (serve / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(serve)))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    (serve / "latest.json").write_text(json.dumps({
        "tag_name": f"v{NEW}", "html_url": f"{url}/", "assets": [
            {"name": name, "size": (serve / name).stat().st_size, "digest": f"sha256:{digest}",
             "browser_download_url": f"{url}/{name}"},
            {"name": "SHA256SUMS.txt", "browser_download_url": f"{url}/SHA256SUMS.txt"}]}), encoding="utf-8")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ, FOREVERAI_RELEASES_API=f"{url}/latest.json", FOREVERAI_DATA=str(tmp_path / "data"),
               FOREVERAI_GAME_DIR=str(tmp_path / "game"), FOREVERAI_INSTANCE_PORT=str(_free_port()))
    env.pop("FOREVERAI_NO_UPDATE_CHECK", None)
    folder = games / updates.STAGING

    def log() -> str:
        p = folder / "install.log"
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else "(no install.log)"
    try:
        run = subprocess.run([str(games / top / exe), "--update"], env=env, capture_output=True, text=True,
                             timeout=600)
        assert run.returncode == 0, f"{run.stdout}\n{run.stderr}\n{log()}"
        deadline = time.monotonic() + 300
        while not (folder / "result.json").exists():
            assert time.monotonic() < deadline, f"no result.json\n{log()}"
            time.sleep(0.5)
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        assert result == {"version": NEW, "from": old_version, "ok": True}, log()
        assert (games / top / "_internal" / "update-e2e.txt").read_text(encoding="utf-8") == NEW
        assert updates.bundled_version(games / top) == NEW and (games / top / exe).is_file()
        assert updates.bundled_version(folder / "old") == old_version
        if platform == "Linux":
            assert os.access(games / top / exe, os.X_OK)
        loaded = re.search(r"the new version loads \((\d+) modules\)", log())
        assert loaded and int(loaded.group(1)) > 30, log()  # every module, from the frozen app's own archive
        installer = int((folder / "started").read_text())
        deadline = time.monotonic() + 60
        while updates._alive(installer):  # it ends right after: on Windows its files stay locked until then
            assert time.monotonic() < deadline, "the installer did not end"
            time.sleep(0.2)
    finally:
        server.shutdown()
