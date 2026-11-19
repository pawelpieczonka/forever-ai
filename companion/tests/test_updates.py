"""The packaged app updates itself (updates.py): finding a release, the checked download, the two unpacked copies, the
hand-over to the new version's installer, the swap of the folders and what the new version reports. The installer runs
for real in a child process, on a stand-in app whose programs are shell scripts; test_update_e2e.py does the same with
the built apps."""

import hashlib
import io
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import types
import zipfile
from pathlib import Path

import httpx
import pytest

from foreverai import __version__, app, config, updates

PY = sys.executable  # the tests below pretend that this process is the packaged app (sys.executable changes)
COMPANION = Path(__file__).resolve().parents[1]
NEW = "99.1.0"
APP, EXE = updates.app_name(), updates.exe_name()
posix = pytest.mark.skipif(os.name == "nt", reason="shell scripts stand in for the programs")


def toc(version: str) -> str:
    return f"## Interface: 16001\n## Title: ForeverAI\n## Version: {version}\n"


def bundle(version: str, platform: str = updates.PLATFORM, program: bytes = b"#!/bin/sh\nexit 0\n") -> dict:
    """The files of a release archive (as the build scripts make them): {path: (content, mode)}."""
    top = updates.app_name(platform)
    return {f"{top}/{updates.exe_name(platform)}": (program, 0o755),
            f"{top}/_internal/addon/ForeverAI/ForeverAI.toc": (toc(version).encode(), 0o644),
            f"{top}/_internal/pillow.libs/libz-1234.so.1": (b"library", 0o644)}


def archive(files: dict, kind: str = "tar", links: tuple = ()) -> bytes:
    buf = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, (data, _mode) in files.items():
                z.writestr(name, data)
        return buf.getvalue()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
        for name, target in links:  # the Linux bundle links its libraries
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            t.addfile(info)
    return buf.getvalue()


def write_tree(root: Path, files: dict) -> None:
    for name, (data, mode) in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(mode)


def version_in(app_dir: Path) -> str:
    return updates.bundled_version(app_dir)


class Releases:
    """GitHub as the app sees it: the latest release, its archive for this platform and SHA256SUMS.txt."""

    API = "/repos/pawelpieczonka/forever-ai/releases/latest"

    def __init__(self, version=NEW, data: bytes | None = None, sums: str | None = None, digest: str | None = None,
                 status=200, archive_status=200):
        self.version = version
        self.data = data if data is not None else archive(bundle(version))
        sha = hashlib.sha256(self.data).hexdigest()
        self.sums = sums if sums is not None else f"{'0' * 64}  other.zip\n{sha}  {updates.asset_name(version)}\n"
        self.digest = digest if digest is not None else f"sha256:{sha}"
        self.status, self.archive_status = status, archive_status
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == self.API:
            if self.status != 200:
                return httpx.Response(self.status, json={"message": "no"})
            return httpx.Response(200, json={
                "tag_name": f"v{self.version}", "html_url": f"https://github.com/x/releases/tag/v{self.version}",
                "assets": [{"name": updates.asset_name(self.version), "size": len(self.data), "digest": self.digest,
                            "browser_download_url": "https://dl.test/archive"},
                           {"name": "SHA256SUMS.txt", "browser_download_url": "https://dl.test/sums"}]})
        if path == "/sums":
            return httpx.Response(200, text=self.sums)
        if path == "/archive":
            return httpx.Response(self.archive_status, content=self.data if self.archive_status == 200 else b"")
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def latest(self) -> dict:
        return updates.fetch_latest(transport=self.transport)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """The packaged app as the player unpacked it (Games/ForeverAI-Linux), and this process running from it."""
    write_tree(tmp_path / "Games", bundle(__version__))
    app_dir = tmp_path / "Games" / APP
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app_dir / EXE))
    monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("FOREVERAI_GAME_DIR", str(tmp_path / "game"))
    monkeypatch.delenv("FOREVERAI_NO_UPDATE_CHECK", raising=False)
    return app_dir


@pytest.fixture
def port(monkeypatch):
    """A free port for the single-instance lock: a ForeverAI running on this computer keeps the real one."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(app, "SINGLE_INSTANCE_PORT", free)
    monkeypatch.setenv("FOREVERAI_INSTANCE_PORT", str(free))  # the installer in a child process
    return free


def staged(app_dir: Path, version: str = NEW, attempts: int = 0, program: bytes = b"#!/bin/sh\nexit 0\n") -> Path:
    """An update downloaded next to the app: both copies and staged.json."""
    folder = app_dir.parent / updates.STAGING
    for copy in ("new", "run"):
        write_tree(folder / copy, bundle(version, program=program))
    (folder / "staged.json").write_text(json.dumps({"version": version, "from": __version__, "app": str(app_dir),
                                                    "url": "https://github.com/x/releases", "attempts": attempts}))
    return folder


def result_of(folder: Path) -> dict:
    return json.loads((folder / "result.json").read_text())


def wait_for(cond, timeout=20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------- finding and downloading
def test_the_latest_release_and_why_it_could_not_be_asked(monkeypatch):
    srv = Releases()
    rel = srv.latest()
    assert rel == {"version": NEW, "url": f"https://github.com/x/releases/tag/v{NEW}",
                   "download": "https://dl.test/archive", "size": len(srv.data), "digest": srv.digest,
                   "sums": "https://dl.test/sums"}
    assert updates.fetch_latest(transport=Releases(status=404).transport) is None  # no release yet
    with pytest.raises(updates.UpdateError, match="403"):  # the rate limit: not "up to date"
        updates.fetch_latest(transport=Releases(status=403).transport)

    def offline(request):
        raise httpx.ConnectError("Name or service not known")
    with pytest.raises(updates.UpdateError, match="not known"):
        updates.fetch_latest(transport=httpx.MockTransport(offline))
    assert updates.latest_release(transport=httpx.MockTransport(offline)) is None
    # a test serves its own release
    monkeypatch.setenv("FOREVERAI_RELEASES_API", "http://127.0.0.1:9/latest.json")
    seen = []
    updates.latest_release(transport=httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(404)))
    assert seen == ["http://127.0.0.1:9/latest.json"]


def test_the_download_is_checked_against_the_release(tmp_path):
    srv = Releases()
    progress = []
    path = updates.download(srv.latest(), tmp_path, lambda done, total: progress.append((done, total)),
                            transport=srv.transport)
    assert path == tmp_path / updates.asset_name(NEW) and path.read_bytes() == srv.data
    assert progress[-1] == (len(srv.data), len(srv.data)) and not list(tmp_path.glob("*.part"))
    assert updates.parse_sums(f"{'A' * 64} *x.zip\nnot a line\n") == {"x.zip": "a" * 64}

    def fails(srv, match):
        folder = tmp_path / str(len(list(tmp_path.iterdir())))
        folder.mkdir()
        with pytest.raises(updates.UpdateError, match=match):
            updates.download(srv.latest(), folder, transport=srv.transport)
        assert not list(folder.iterdir())  # nothing half downloaded stays
    other = archive(bundle("1.2.3"))
    fails(Releases(data=other, sums=srv.sums, digest=""), "does not match its checksum")  # damaged on the way
    fails(Releases(digest="sha256:" + "0" * 64), "does not match its checksum")  # GitHub's own digest disagrees
    fails(Releases(sums=f"{'0' * 64}  other.zip\n"), "has no line for")
    fails(Releases(archive_status=404), "the download failed")
    with pytest.raises(updates.UpdateError, match="has no"):
        updates.download(dict(srv.latest(), sums=None), tmp_path, transport=srv.transport)


def test_unpacking_keeps_the_bundle_and_nothing_outside_it(tmp_path):
    data = archive(bundle(NEW), links=((f"{APP}/_internal/libz.so.1", "pillow.libs/libz-1234.so.1"),))
    (tmp_path / "a.tar.gz").write_bytes(data)
    got = updates.unpack(tmp_path / "a.tar.gz", tmp_path / "out", "Linux")
    assert got == tmp_path / "out" / "ForeverAI-Linux"
    if os.name != "nt":
        assert os.access(got / "ForeverAI-Linux", os.X_OK)  # the program stays executable
        assert (got / "_internal/libz.so.1").read_bytes() == b"library"
    assert version_in(got) == NEW
    # the Windows zip
    (tmp_path / "w.zip").write_bytes(archive(bundle(NEW, "Windows"), "zip"))
    assert (updates.unpack(tmp_path / "w.zip", tmp_path / "win", "Windows") / "ForeverAI-Windows.exe").is_file()

    def refused(name, data, match):
        (tmp_path / name).write_bytes(data)
        with pytest.raises(updates.UpdateError, match=match):
            updates.unpack(tmp_path / name, tmp_path / "x", "Linux")
    refused("up.zip", archive({"ForeverAI-Linux/../../evil": (b"x", 0o644), **bundle(NEW)}, "zip"), "outside")
    refused("other.tar.gz", archive({"Other/file": (b"x", 0o644), **bundle(NEW)}), "outside")
    refused("abs.tar.gz", archive({"/etc/evil": (b"x", 0o644), **bundle(NEW)}), "outside")
    refused("link.tar.gz", archive(bundle(NEW), links=((f"{APP}/_internal/x", "../../../../etc/passwd"),)),
            "cannot be unpacked")
    refused("empty.tar.gz", archive({f"{APP}/readme.txt": (b"x", 0o644)}), "has no ForeverAI-Linux/ForeverAI-Linux")
    refused("broken.tar.gz", b"not an archive", "cannot be unpacked")
    assert not (tmp_path / "evil").exists() and not (tmp_path.parent / "evil").exists()


def test_stage_unpacks_both_copies_next_to_the_app(installed):
    srv = Releases()
    folder = updates.stage(srv.latest(), transport=srv.transport)
    assert folder == installed.parent / ".ForeverAI-update"
    for copy in ("new", "run"):
        assert version_in(folder / copy / APP) == NEW and (folder / copy / APP / EXE).is_file()
    assert sorted(p.name for p in folder.iterdir()) == ["new", "run", "staged.json"]  # the download is removed
    info = json.loads((folder / "staged.json").read_text())
    assert info == {"version": NEW, "from": __version__, "app": str(installed),
                    "url": f"https://github.com/x/releases/tag/v{NEW}", "attempts": 0}
    assert updates.staged_update() == info and version_in(installed) == __version__  # the app is not touched yet
    # an archive that holds another version than its release says: refused, nothing stays
    bad = Releases(data=archive(bundle("98.0.0")))
    with pytest.raises(updates.UpdateError, match="holds version 98.0.0, not 99.1.0"):
        updates.stage(bad.latest(), transport=bad.transport)
    assert not folder.exists()


def test_why_an_app_cannot_update_itself(installed, monkeypatch):
    assert updates.cannot_update() is None
    if os.name != "nt" and os.geteuid() != 0:
        installed.parent.chmod(0o555)
        try:
            assert updates.cannot_update() == f"it may not write to {installed.parent}"
        finally:
            installed.parent.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(installed / "ForeverAI"))
    assert updates.cannot_update() == f"its program was renamed from {EXE}"
    monkeypatch.setattr(sys, "frozen", False)
    assert updates.cannot_update() == "it runs from source" and updates.staging_dir() is None


def test_only_a_complete_newer_update_counts(installed):
    folder = staged(installed)
    assert updates.staged_update()["version"] == NEW
    shutil.rmtree(folder / "run")
    assert updates.staged_update() is None  # the copy that installs it is missing
    staged(installed, version=__version__)
    assert updates.staged_update() is None  # this version: nothing to install


# ---------------------------------------------------------------- the installer (the new version's copy)
def test_the_installer_swaps_the_folders_once_the_app_has_quit(installed, port, monkeypatch):
    folder = staged(installed, attempts=1)
    started = []
    monkeypatch.setattr(updates, "spawn", lambda args, cwd: started.append((args, cwd)))
    the_app = subprocess.Popen([PY, "-c", "import time; time.sleep(1.2)"])
    t0 = time.monotonic()
    assert updates.install(installed, the_app.pid, restart=True) == 0
    assert time.monotonic() - t0 >= 1.0 and the_app.wait() == 0  # it waited for the app to quit
    assert version_in(installed) == NEW and version_in(folder / "old") == __version__
    assert not (folder / "new" / APP).exists()
    assert result_of(folder) == {"version": NEW, "from": __version__, "ok": True}
    assert (folder / "started").read_text() == str(os.getpid())
    assert started == [([str(installed / EXE)], installed)]  # the new version starts
    assert f"installed {NEW}" in (folder / "install.log").read_text()


def test_the_installer_puts_the_old_version_back_when_the_new_one_cannot_move(installed, port, monkeypatch):
    folder = staged(installed)
    started = []
    monkeypatch.setattr(updates, "spawn", lambda args, cwd: started.append((args, cwd)))
    monkeypatch.setattr(updates, "RETRY_TIMEOUT", 0.3)
    rename = os.rename

    def locked(src, dst):
        if Path(src) == folder / "new" / APP:
            raise PermissionError(13, "Access is denied")
        rename(src, dst)
    monkeypatch.setattr(updates.os, "rename", locked)
    assert updates.install(installed, 0, restart=True) == 1
    assert version_in(installed) == __version__ and version_in(folder / "new" / APP) == NEW
    res = result_of(folder)
    assert not res["ok"] and res["error"].startswith(f"cannot move the new version to {installed}")
    assert started == [([str(installed / EXE)], installed)]  # the old version starts again


def test_the_installer_gives_up_when_the_app_does_not_quit(installed, port, monkeypatch):
    folder = staged(installed)
    started = []
    monkeypatch.setattr(updates, "spawn", lambda args, cwd: started.append(args))
    monkeypatch.setattr(updates, "QUIT_TIMEOUT", 0.5)
    the_app = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        assert updates.install(installed, the_app.pid, restart=True) == 1
    finally:
        the_app.kill()
        the_app.wait()
    assert result_of(folder)["error"] == "ForeverAI did not quit" and (folder / "started").exists()
    assert version_in(installed) == __version__ and started == []  # nothing moved, and the app still runs


def test_the_installer_leaves_a_foreverai_that_was_started_again(installed, port, monkeypatch):
    folder = staged(installed)
    started = []
    monkeypatch.setattr(updates, "spawn", lambda args, cwd: started.append(args))
    monkeypatch.setattr(updates, "RETRY_TIMEOUT", 0.5)
    other = socket.socket()
    other.bind(("127.0.0.1", port))  # the player started ForeverAI again right after it quit
    try:
        assert updates.install(installed, 0, restart=True) == 1
    finally:
        other.close()
    assert result_of(folder)["error"] == "ForeverAI was started again before the update was installed"
    assert version_in(installed) == __version__ and started == []  # it runs: not started a second time


@posix
def test_the_installer_waits_for_a_program_that_runs_from_the_app_folder(installed, port, monkeypatch):
    folder = staged(installed)
    monkeypatch.setattr(updates, "spawn", lambda args, cwd: None)
    monkeypatch.setattr(updates, "RETRY_TIMEOUT", 0.6)
    shutil.copy(shutil.which("sleep"), installed / "sleep")
    copy = subprocess.Popen([str(installed / "sleep"), "30"])
    try:
        assert wait_for(lambda: updates._running_from(installed) == [copy.pid], 5)
        assert updates.install(installed, 0, restart=True) == 1
    finally:
        copy.kill()
        copy.wait()
    assert result_of(folder)["error"] == f"a ForeverAI still runs from {installed}"
    assert version_in(installed) == __version__ and updates._running_from(installed) == []


def test_the_installer_refuses_a_new_version_that_does_not_load(installed, port, monkeypatch):
    folder = staged(installed)
    started = []
    monkeypatch.setattr(updates, "spawn", lambda args, cwd: started.append(args))

    def broken():
        raise ImportError("No module named 'tkinter'")
    monkeypatch.setattr(updates, "self_check", broken)
    assert updates.install(installed, os.getpid(), restart=True) == 1  # the check comes before the wait
    assert result_of(folder)["error"] == "No module named 'tkinter'" and not (folder / "started").exists()
    assert version_in(installed) == __version__ and started == []


def test_the_self_check_loads_every_module_but_does_not_start_the_app(monkeypatch):
    import foreverai.app

    def started(*a, **kw):
        raise AssertionError("foreverai.__main__ was imported: it starts the app")
    monkeypatch.setattr(foreverai.app, "main", started)
    sys.modules.pop("foreverai.__main__", None)
    updates.self_check()
    assert "foreverai.window" in sys.modules and "foreverai.kb.sync" in sys.modules


def test_the_process_waiter():
    proc = subprocess.Popen([PY, "-c", "import time; time.sleep(0.4)"])
    waiter = updates._Waiter(proc.pid)
    assert updates._alive(proc.pid)
    assert waiter.wait(10) and not updates._alive(proc.pid)  # an ended child that is not collected yet counts
    proc.wait()
    assert updates._Waiter(proc.pid).wait(0)  # one that has ended already
    still = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        assert not updates._Waiter(still.pid).wait(0.2)
    finally:
        still.kill()
        still.wait()


# ---------------------------------------------------------------- handing over (the app's side)
def program(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)


@posix
def test_the_app_quits_only_once_the_new_version_runs(installed):
    folder = staged(installed)
    installer = folder / "run" / APP / EXE
    program(installer, 'echo "$@" > args.txt\necho $$ > started\n')
    assert updates.start_installer(restart=True, pid=4321) is None
    assert (folder / "args.txt").read_text().split() == ["--install-update", str(installed), "--pid", "4321",
                                                          "--restart"]
    assert json.loads((folder / "staged.json").read_text())["attempts"] == 1
    # one that stops before the new version loads: why, from its result.json
    program(installer, 'printf \'{"ok": false, "error": "No module named tkinter"}\' > result.json\nexit 1\n')
    assert updates.start_installer(restart=False) == "No module named tkinter"
    assert result_of(folder) == {"version": NEW, "from": __version__, "ok": False, "error": "No module named tkinter"}
    program(installer, "exit 3\n")
    assert updates.start_installer(restart=False) == "the new version stopped (exit code 3)"
    program(installer, "exec sleep 30\n")
    t0 = time.monotonic()
    assert updates.start_installer(restart=False, timeout=0.5) == "the new version did not start in time"
    assert time.monotonic() - t0 < 5
    assert json.loads((folder / "staged.json").read_text())["attempts"] == 4
    shutil.rmtree(folder)
    assert updates.start_installer(restart=False) == "no update is ready"


def test_the_command_line_of_the_installer_is_accepted(monkeypatch):
    """The version that runs starts the next version's copy with installer_args: every version accepts them, and the
    installer starts before logging, settings or the window."""
    calls = []
    monkeypatch.setattr(app, "setup_logging", lambda: pytest.fail("the installer touched the logs"))
    monkeypatch.setattr(updates, "install", lambda app_dir, pid, restart: calls.append((app_dir, pid, restart)) or 7)
    for restart in (True, False):
        with pytest.raises(SystemExit) as e:
            app.main(updates.installer_args(Path("/games/ForeverAI-Linux"), 4321, restart))
        assert e.value.code == 7
    assert calls == [(Path("/games/ForeverAI-Linux"), 4321, True), (Path("/games/ForeverAI-Linux"), 4321, False)]


def test_help_lists_update_but_not_the_installer(capsys):
    with pytest.raises(SystemExit):
        app.main(["--help"])
    out = capsys.readouterr().out
    assert "--update" in out and "--install-update" not in out and "--pid" not in out


@posix
def test_from_the_download_to_the_new_version_running(installed, port, monkeypatch, tmp_path, caplog):
    """The whole update with real processes: the app downloads the release and starts the new version's copy, which
    runs this checkout's installer, waits for the app to quit, swaps the folders and starts the new version."""
    ran = tmp_path / "ran"
    ran.mkdir()
    new_program = (
        '#!/bin/sh\n'
        'if [ "$1" = "--install-update" ]; then\n'
        f'  exec "{PY}" -c "import sys; sys.path.insert(0, sys.argv.pop(1)); from foreverai.app import main; '
        f'main(sys.argv[1:])" "{COMPANION}" "$@"\n'
        'fi\n'
        f'echo "$@" > "{ran}/new-$$"\n')
    srv = Releases(data=archive(bundle(NEW, program=new_program.encode()),
                                links=((f"{APP}/_internal/libz.so.1", "pillow.libs/libz-1234.so.1"),)))
    store = config.SettingsStore()
    upd = updates.Updater(store, transport=srv.transport)
    upd._check()  # automatic updates: found and downloaded
    assert upd.state == "ready" and upd.text() == f"ForeverAI {NEW} is ready: it is installed when you quit ForeverAI"
    folder = installed.parent / updates.STAGING
    the_app = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])  # stands in for this app
    try:
        assert updates.start_installer(restart=True, pid=the_app.pid) is None  # the new version loads
        time.sleep(0.3)
        assert not (folder / "result.json").exists() and version_in(installed) == __version__  # the app still runs
        the_app.terminate()
        the_app.wait()
        assert wait_for(lambda: (folder / "result.json").exists() and list(ran.iterdir()))
    finally:
        the_app.kill()
    assert result_of(folder) == {"version": NEW, "from": __version__, "ok": True}, (folder / "install.log").read_text()
    assert version_in(installed) == NEW and version_in(folder / "old") == __version__
    assert os.access(installed / EXE, os.X_OK) and (installed / "_internal/libz.so.1").read_bytes() == b"library"
    assert [p.read_text() for p in ran.iterdir()] == ["\n"]  # the new version started, with no arguments

    # the new version starts: it says so, and removes what the update left
    monkeypatch.setattr(updates, "__version__", NEW)
    caplog.set_level(logging.INFO, logger="foreverai")
    updates.Updater(store).resume()
    assert f"Updated to ForeverAI {NEW}" in caplog.text
    assert wait_for(lambda: not folder.exists(), 10)
    assert updates.install_at_start() is False


# ---------------------------------------------------------------- the running app
def test_a_new_version_is_downloaded_and_installed_when_the_app_quits(installed, monkeypatch):
    srv = Releases()
    store = config.SettingsStore()
    upd = updates.Updater(store, transport=srv.transport)
    assert upd.reason is None and upd.button() == ("Check now", False)
    upd._check()
    assert upd.state == "ready" and upd.button() == ("Restart now", True) and "/archive" in srv.paths
    assert updates.staged_update()["version"] == NEW
    calls = []
    monkeypatch.setattr(updates, "start_installer", lambda restart, **kw: calls.append(restart))
    upd.on_quit()
    assert calls == [False]  # installed once the app has quit; no restart
    # checked again later: the same release is not downloaded twice
    upd2 = updates.Updater(store, transport=srv.transport)
    srv.paths.clear()
    upd2._check()
    assert upd2.state == "ready" and "/archive" not in srv.paths


def test_with_automatic_updates_off_the_window_only_offers_it(installed, monkeypatch):
    srv = Releases()
    store = config.SettingsStore()
    store.update(auto_update=False)
    upd = updates.Updater(store, transport=srv.transport)
    upd._check()
    assert upd.state == "available" and upd.text() == f"ForeverAI {NEW} is available"
    assert upd.button() == ("Install", True) and "/archive" not in srv.paths
    calls = []
    monkeypatch.setattr(updates, "start_installer", lambda restart, **kw: calls.append(restart))
    upd.on_quit()
    assert calls == []
    upd._install_now()  # Install: download, then restart into it
    assert calls == [True] and upd.exit_requested and updates.staged_update()["version"] == NEW
    # switched on again: a release found meanwhile is downloaded at once
    upd.state = "available"
    store.update(auto_update=True)
    upd.toggled()
    assert upd._wake.is_set()


def test_the_states_of_the_updates_row(installed, monkeypatch):
    store = config.SettingsStore()

    def offline(request):
        raise httpx.ConnectError("no network")
    upd = updates.Updater(store, transport=httpx.MockTransport(offline))
    upd._check()
    assert upd.state == "offline" and upd.text().startswith("Could not check for a new version · ")
    assert upd.button() == ("Check now", True)
    upd.transport = Releases(version=__version__).transport
    upd._check()
    assert upd.state == "current" and upd.text().startswith("Up to date · checked ")
    upd.transport = Releases(archive_status=404).transport
    upd._check()
    assert upd.state == "failed" and upd.text().startswith(f"The update to {NEW} failed: the download failed")
    assert upd.button() == ("Try again", True)
    upd.state, upd.total, upd.done = "downloading", 200, 50
    assert upd.text() == f"Downloading ForeverAI {NEW}… 25%" and upd.button() == ("Restart now", False)
    upd.state = "installing"
    assert upd.text() == f"Installing ForeverAI {NEW}…" and upd.button() == ("Restart now", False)


def test_a_download_that_failed_is_tried_again_but_a_failed_installation_is_not(installed):
    store = config.SettingsStore()
    srv = Releases(archive_status=503)
    upd = updates.Updater(store, transport=srv.transport)
    upd._check()
    assert upd.state == "failed"
    srv.archive_status = 200
    upd._check()  # the next check: downloaded now
    assert upd.state == "ready"
    shutil.rmtree(installed.parent / updates.STAGING)
    store.update(update_failed=NEW)  # installing it did not work
    upd = updates.Updater(store, transport=srv.transport)
    srv.paths.clear()
    upd._check()
    assert upd.state == "failed" and "/archive" not in srv.paths and upd.button() == ("Try again", True)


def test_an_app_that_cannot_update_itself_offers_the_download_page(installed, monkeypatch):
    monkeypatch.setattr(updates, "cannot_update", lambda: "it may not write to C:\\Program Files")
    srv = Releases()
    upd = updates.Updater(config.SettingsStore(), transport=srv.transport)
    upd._check()
    assert upd.state == "available" and "/archive" not in srv.paths and upd.button() == ("Download…", True)
    assert "cannot update itself (it may not write to C:\\Program Files)" in upd.text()
    opened = []
    monkeypatch.setattr(updates, "open_path", opened.append)
    upd.press()
    assert opened == [f"https://github.com/x/releases/tag/v{NEW}"]


def test_from_source_there_are_no_updates(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    upd = updates.Updater(types.SimpleNamespace(settings=config.Settings(game_dir="x")))
    upd.start()
    assert upd.state == "source" and upd.button() is None and "git pull" in upd.text()


def test_a_newer_release_replaces_the_downloaded_one(installed):
    store = config.SettingsStore()
    staged(installed, version="99.0.5")
    upd = updates.Updater(store, transport=Releases().transport)
    upd.resume()
    assert upd.state == "ready" and upd.release["version"] == "99.0.5"
    upd._check()
    assert upd.state == "ready" and updates.staged_update()["version"] == NEW


@posix
def test_restart_now_that_fails_keeps_the_app_running(installed):
    folder = staged(installed)
    program(folder / "run" / APP / EXE, 'printf \'{"ok": false, "error": "No module named tkinter"}\' > result.json\n'
                                        'exit 1\n')
    store = config.SettingsStore()
    upd = updates.Updater(store)
    upd.resume()
    upd._restart()
    assert not upd.exit_requested and upd.state == "ready"  # tried again at the next quit
    assert upd.error == "No module named tkinter" and not (folder / "result.json").exists()
    for _ in range(updates.MAX_ATTEMPTS - 1):
        upd._restart()
    assert upd.state == "failed" and store.settings.update_failed == NEW and not folder.exists()


def test_what_the_new_version_finds_at_start(installed, monkeypatch, caplog):
    store = config.SettingsStore()
    caplog.set_level(logging.INFO, logger="foreverai")
    folder = installed.parent / updates.STAGING

    def resumed():
        upd = updates.Updater(store)
        upd.resume()
        return upd
    # installed: reported, and what is left removed
    staged(installed, version=__version__, attempts=1)
    (folder / "result.json").write_text(json.dumps({"version": __version__, "from": "0.8.0", "ok": True}))
    store.update(update_failed="0.8.5")
    resumed()
    assert f"Updated to ForeverAI {__version__}" in caplog.text and store.settings.update_failed == ""
    assert wait_for(lambda: not folder.exists(), 10)
    # an installation that failed: tried again at the next quit, then given up
    staged(installed, attempts=1)
    (folder / "result.json").write_text(json.dumps({"version": NEW, "ok": False, "error": "cannot move it"}))
    upd = resumed()
    assert upd.state == "ready" and "could not be installed (cannot move it)" in caplog.text
    assert not (folder / "result.json").exists() and updates.install_at_start() is False  # waits for a quit
    staged(installed, attempts=updates.MAX_ATTEMPTS)
    (folder / "result.json").write_text(json.dumps({"version": NEW, "ok": False, "error": "cannot move it"}))
    upd = resumed()
    assert upd.state == "failed" and store.settings.update_failed == NEW and not folder.exists()
    # an installer that ended without a result (the computer was turned off)
    staged(installed, attempts=1)
    dead = subprocess.Popen([PY, "-c", "pass"])
    dead.wait()
    (folder / "started").write_text(str(dead.pid))
    upd = resumed()
    assert upd.state == "ready" and "the installation did not finish" in caplog.text
    # an installer that still runs (another start of the app hands over to it)
    (folder / "started").write_text(str(os.getpid()))
    assert resumed().state == "installing"
    # ... unless it started long ago: its pid belongs to another program by now
    old = time.time() - updates.INSTALLER_MAX_S - 60
    os.utime(folder / "started", (old, old))
    assert resumed().state == "ready" and not (folder / "started").exists()
    # downloaded and never tried
    staged(installed)
    assert resumed().state == "ready"
    # an update to this version or an older one, or an unfinished download
    staged(installed, version="0.0.1")
    resumed()
    assert not folder.exists()
    folder.mkdir()
    (folder / "x.tar.gz.part").write_bytes(b"x")
    resumed()
    assert not folder.exists()


def test_cleaning_up_after_an_update_leaves_the_next_one_alone(installed):
    """The new version removes what its update left before it checks again: the cleanup once ran in a thread of its own
    and deleted the next download while it was being unpacked."""
    folder = staged(installed, version=__version__, attempts=1)
    (folder / "old").mkdir()
    write_tree(folder / "old", bundle("0.8.0"))
    (folder / "result.json").write_text(json.dumps({"version": __version__, "from": "0.8.0", "ok": True}))
    upd = updates.Updater(config.SettingsStore(), transport=Releases().transport)
    upd.resume()
    assert not folder.exists()  # removed before resume returns
    upd._check()
    time.sleep(0.5)
    assert upd.state == "ready" and updates.staged_update()["version"] == NEW
    assert sorted(p.name for p in folder.iterdir()) == ["new", "run", "staged.json"]


def test_a_downloaded_update_is_installed_at_the_next_start(installed, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "start_installer", lambda restart, **kw: calls.append(restart))
    assert updates.install_at_start() is False  # nothing downloaded
    staged(installed)
    assert updates.install_at_start() is True and calls == [True]
    staged(installed, attempts=1)  # tried already: the next try waits for a quit, so a failing one cannot loop
    assert updates.install_at_start() is False and calls == [True]
    staged(installed)
    config.SettingsStore().update(auto_update=False)
    assert updates.install_at_start() is False and calls == [True]


def test_run_window_hands_over_before_the_window_opens(monkeypatch):
    import foreverai.window as window
    monkeypatch.setattr(app, "_single_instance", lambda: socket.socket())
    monkeypatch.setattr(updates, "install_at_start", lambda: True)
    monkeypatch.setattr(window, "AppWindow", lambda: pytest.fail("the window opened"))
    app.run_window()


def test_update_from_the_command_line(installed, port, monkeypatch):
    srv = Releases()
    said = []
    calls = []
    monkeypatch.setattr(updates, "start_installer", lambda restart, **kw: calls.append(restart))
    assert updates.update_now(said.append, transport=srv.transport) == 0
    assert said[0] == f"Downloading ForeverAI {NEW} (0 MB)…" and "  100%" in said
    assert said[-1] == f"ForeverAI {NEW} is installed as soon as this command ends." and calls == [False]
    said.clear()
    assert updates.update_now(said.append, transport=Releases(version=__version__).transport) == 0
    assert said == [f"ForeverAI {__version__} is up to date."]
    other = socket.socket()
    other.bind(("127.0.0.1", port))
    try:
        said.clear()
        assert updates.update_now(said.append, transport=srv.transport) == 1
        assert "ForeverAI is running" in said[0]
    finally:
        other.close()
    monkeypatch.setattr(sys, "frozen", False)
    said.clear()
    assert updates.update_now(said.append) == 1 and "it runs from source" in said[0]


def test_update_command_line_flag(monkeypatch):
    monkeypatch.setattr(app, "setup_logging", lambda: Path("log"))
    monkeypatch.setattr(updates, "update_now", lambda: 5)
    with pytest.raises(SystemExit) as e:
        app.main(["--update"])
    assert e.value.code == 5
