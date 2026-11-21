"""New versions come as GitHub Releases: one archive per platform, named after it (ForeverAI-<version>-Windows-x64.zip,
ForeverAI-<version>-Linux-x86_64.tar.gz, as the release workflow names them), SHA256SUMS.txt and SHA256SUMS.txt.sig.

The packaged app updates itself. It looks for a newer release when it starts and every few hours, downloads the archive
in the background, checks it against SHA256SUMS.txt and unpacks it next to its own folder. The new version is installed
when the app quits, when the player clicks Restart now, or at the next start when the app was not quit (the computer
was shut down with it open). With automatic updates off the window only says that a new version is out; from source,
or from a folder the app may not write to (Program Files), it offers the download page instead.

SHA256SUMS.txt.sig is the Ed25519 signature of SHA256SUMS.txt with the release key. Its private half is a secret of the
repository that only the release workflow uses (.github/sign_sums.sh); the app knows the public half (RELEASE_KEYS) and
takes no update without that signature, which it checks before it downloads the archive. So someone who can publish a
release on GitHub but does not have the key cannot ship an update. Nor can the signed files of an older release pass
for a newer one: the names in SHA256SUMS.txt carry the version, and the archive must hold the version of its release.

The app cannot replace its own folder while it runs from it: Windows locks a running program's files, and on Linux the
running app would go on reading its code from the new files. A second copy of the new version does it. The app starts
that copy with ``--install-update <app folder> --pid <its pid> [--restart]`` and quits once the copy has checked that
the new version loads. The copy waits until the app has quit, moves the app's folder aside and the new version into its
place, writes how it went, and starts the app again after a restart. The new version reports the result and removes
what is left when it starts. Every version must keep accepting that command line, since the version that runs starts
the next version's copy with it.

Next to the app's folder (Games/ForeverAI-Linux, say):

    Games/.ForeverAI-update/
        ForeverAI-<v>-Linux-x86_64.tar.gz   the download (removed once unpacked)
        new/ForeverAI-Linux/                the new version, moved into the app's place
        run/ForeverAI-Linux/                the copy that installs it
        staged.json                         written last: the update is ready to install
        started                             the copy runs and the new version loads (written by the copy)
        result.json                         how the installation went (written by the copy)
        install.log                         the copy's log
        old/                                the previous version, after the swap

For tests: FOREVERAI_RELEASES_API replaces the GitHub address of the latest release, and FOREVERAI_RELEASE_KEY the
release keys, so that a test can serve its own release, signed with a key of its own."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from . import __version__
from .osutil import WINDOWS, child_env, open_path

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")

REPO = "pawelpieczonka/forever-ai"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
PLATFORM = "Windows" if WINDOWS else "Linux"
SUMS = "SHA256SUMS.txt"
SIGNATURE = SUMS + ".sig"  # the Ed25519 signature of SHA256SUMS.txt (64 bytes), made by .github/sign_sums.sh
# The public half of the release key (32 bytes in base64): an update is installed only when its SHA256SUMS.txt carries
# the signature of one of these. How to make one, and to change it, is in CLAUDE.md (Self-update).
RELEASE_KEYS: tuple[str, ...] = ("nVEPi98TbpkzcHFetmY4OUC3P82qH1Q+WJMMkKedKXg=",)
# RFC 8032, 7.1, test 1 (the empty message): the copy of a new version checks with it that it can check signatures
_RFC8032_KEY = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
_RFC8032_SIGNATURE = ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                      "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
STAGING = ".ForeverAI-update"
CHECK_EVERY = 6 * 3600  # seconds between two checks while the app runs
MAX_ATTEMPTS = 3  # installations of one downloaded update before it is given up
HANDOFF_TIMEOUT = 60.0  # for the copy to start (Windows scans a new program before its first start)
QUIT_TIMEOUT = 120.0  # for the app to quit once the copy runs
RETRY_TIMEOUT = 30.0  # for a folder that cannot move yet (an antivirus or indexer still has a file open)
INSTALLER_MAX_S = 600  # an installer that started longer ago than this has ended (its pid may be reused)


class UpdateError(Exception):
    """An update that cannot go on; the message is shown to the player."""


def asset_name(version: str, platform: str = PLATFORM) -> str:
    """The release file of a platform (the release workflow uses the same names)."""
    return f"ForeverAI-{version}-Windows-x64.zip" if platform == "Windows" else f"ForeverAI-{version}-Linux-x86_64.tar.gz"


def app_name(platform: str = PLATFORM) -> str:
    """The app's folder in the release archive, and its program in that folder (without .exe)."""
    return f"ForeverAI-{platform}"


def exe_name(platform: str = PLATFORM) -> str:
    return app_name(platform) + (".exe" if platform == "Windows" else "")


def version_key(v: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2); a pre-release suffix sorts before the release ('0.5.0-rc1' < '0.5.0')."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)$", str(v).strip())
    if not m:
        return (0,)
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums += (0,) * (3 - len(nums))
    return nums + ((0,) if m.group(2) else (1,))


def newer(version: str | None, than: str | None = None) -> bool:
    """``version`` is newer than ``than`` (this app's version)."""
    return bool(version) and version_key(version) > version_key(than or __version__)


# ---------------------------------------------------------------- finding and downloading a release
def fetch_latest(transport: httpx.BaseTransport | None = None, timeout: float = 10.0) -> dict | None:
    """The newest published release: {version, url (its page), download, size, digest (the archive of this platform),
    sums (SHA256SUMS.txt), signature (SHA256SUMS.txt.sig)}, or None when there is none. Raises UpdateError when GitHub
    cannot be asked (offline, rate limit)."""
    api = os.environ.get("FOREVERAI_RELEASES_API") or LATEST_API
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True,
                          headers={"Accept": "application/vnd.github+json", "User-Agent": "ForeverAI"}) as c:
            r = c.get(api)
        if r.status_code == 404:
            return None  # no release yet
        if r.status_code != 200:
            raise UpdateError(f"GitHub answered {r.status_code}")
        data = r.json()
    except httpx.HTTPError as e:
        raise UpdateError(_short(e)) from e
    except ValueError as e:
        raise UpdateError("GitHub sent no release data") from e
    version = str(data.get("tag_name") or "").lstrip("v") if isinstance(data, dict) else ""
    if not version or data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    archive = assets.get(asset_name(version)) or {}
    return {"version": version, "url": data.get("html_url") or RELEASES_URL,
            "download": archive.get("browser_download_url"), "size": int(archive.get("size") or 0),
            "digest": str(archive.get("digest") or ""), "sums": (assets.get(SUMS) or {}).get("browser_download_url"),
            "signature": (assets.get(SIGNATURE) or {}).get("browser_download_url")}


def latest_release(transport: httpx.BaseTransport | None = None, timeout: float = 10.0) -> dict | None:
    """fetch_latest, or None when GitHub cannot be asked."""
    try:
        return fetch_latest(transport, timeout)
    except UpdateError as e:
        log.info("update check failed: %s", e)
        return None


def newer_release(current: str = __version__, **kw) -> dict | None:
    rel = latest_release(**kw)
    return rel if rel and newer(rel["version"], current) else None


def parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS.txt (sha256sum's format) -> {file name: hex digest}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def release_keys() -> list[bytes]:
    """The public keys whose signature an update needs: RELEASE_KEYS, or the one in FOREVERAI_RELEASE_KEY (tests)."""
    test_key = (os.environ.get("FOREVERAI_RELEASE_KEY") or "").strip()
    keys = []
    for text in [test_key] if test_key else RELEASE_KEYS:
        try:
            key = base64.b64decode(text, validate=True)
        except ValueError:
            key = b""
        if len(key) != 32:
            raise UpdateError(f"{text!r} is not a release key (an Ed25519 public key, 32 bytes in base64)")
        keys.append(key)
    if not keys:
        raise UpdateError("this version of ForeverAI knows no release key")
    return keys


def ed25519_verify(key: bytes, signature: bytes, data: bytes) -> bool:
    """``signature`` is the Ed25519 signature of ``data`` made with the private half of the public key ``key``."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as e:  # a build without it can check no update
        raise UpdateError(f"signatures cannot be checked ({_short(e)})") from e
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, data)
    except (InvalidSignature, ValueError):
        return False
    return True


def check_signature(sums: bytes, signature: bytes) -> None:
    """``signature`` (SHA256SUMS.txt.sig) is the release key's signature of ``sums`` (SHA256SUMS.txt, as downloaded);
    raises UpdateError when it is not."""
    if not any(ed25519_verify(key, signature, sums) for key in release_keys()):
        raise UpdateError(f"the release is not signed with ForeverAI's release key ({SIGNATURE} does not match)")


def download(rel: dict, folder: Path, progress: Callable[[int, int], None] | None = None,
             transport: httpx.BaseTransport | None = None) -> Path:
    """The archive of this platform from release ``rel``, saved in ``folder``. SHA256SUMS.txt must carry the release
    key's signature, checked before the archive is downloaded, and the archive must match its line there (and the
    digest GitHub lists for it). ``progress(done, total)`` follows the download."""
    name = asset_name(rel["version"])
    if not rel.get("download") or not rel.get("sums"):
        raise UpdateError(f"the release has no {name} or {SUMS}")
    if not rel.get("signature"):
        raise UpdateError(f"the release is not signed (it has no {SIGNATURE})")
    target, part = folder / name, folder / (name + ".part")
    h = hashlib.sha256()
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=15.0), transport=transport, follow_redirects=True,
                          headers={"User-Agent": "ForeverAI"}) as c:
            r = c.get(rel["sums"])
            r.raise_for_status()
            sums = r.content  # the bytes that were signed
            r = c.get(rel["signature"])
            r.raise_for_status()
            check_signature(sums, r.content)
            want = parse_sums(sums.decode("utf-8", errors="replace")).get(name)
            if not want:
                raise UpdateError(f"{SUMS} of the release has no line for {name}")
            with c.stream("GET", rel["download"]) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0) or int(rel.get("size") or 0)
                done = 0
                with open(part, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
    except httpx.HTTPError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"the download failed ({_short(e)})") from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    got = h.hexdigest()
    digest = str(rel.get("digest") or "").lower()
    if got != want or (digest.startswith("sha256:") and digest[7:] != got):
        part.unlink(missing_ok=True)
        raise UpdateError(f"{name} does not match its checksum (a damaged download)")
    os.replace(part, target)
    return target


def _check_member(name: str, top: str) -> None:
    parts = name.replace("\\", "/").split("/")
    if parts[0] != top or ".." in parts or name.startswith(("/", "\\")) or ":" in parts[0]:
        raise UpdateError(f"the archive holds {name!r}, outside {top}/")


def unpack(archive: Path, into: Path, platform: str = PLATFORM) -> Path:
    """Unpack a release archive into the new folder ``into`` and return the app's folder in it. Everything in the
    archive must be in the app's folder; nothing is written outside ``into`` (tar links included)."""
    top = app_name(platform)
    if not remove(into):
        raise UpdateError(f"cannot remove {into}")
    into.mkdir(parents=True)
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                for name in z.namelist():
                    _check_member(name, top)
                z.extractall(into)
        else:
            with tarfile.open(archive, "r:*") as t:
                for m in t.getmembers():
                    _check_member(m.name, top)
                t.extractall(into, filter="data")  # keeps the program's executable bit
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as e:
        raise UpdateError(f"{archive.name} cannot be unpacked ({e})") from e
    if not (into / top / exe_name(platform)).is_file():
        raise UpdateError(f"{archive.name} has no {top}/{exe_name(platform)}")
    return into / top


def bundled_version(app_dir: Path) -> str | None:
    """The version of an unpacked app: the one in the .toc of the add-on it carries (the four places agree)."""
    for toc in app_dir.rglob("ForeverAI.toc"):
        if toc.parent.name == "ForeverAI" and toc.parent.parent.name == "addon":
            m = re.search(r"^## Version: *(\S+)", toc.read_text(encoding="utf-8", errors="replace"), re.M)
            if m:
                return m.group(1)
    return None


# ---------------------------------------------------------------- the app's folder and the update next to it
def install_dir() -> Path | None:
    """The packaged app's folder (ForeverAI-Windows / ForeverAI-Linux, or what the player renamed it to); None from
    source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def staging_dir(app_dir: Path | None = None) -> Path | None:
    app_dir = app_dir or install_dir()
    return app_dir.parent / STAGING if app_dir else None


def cannot_update() -> str | None:
    """Why this app cannot replace itself, or None when it can."""
    app_dir = install_dir()
    if app_dir is None:
        return "it runs from source"
    if Path(sys.executable).name != exe_name():
        return f"its program was renamed from {exe_name()}"
    try:  # a real write: on Windows os.access does not know the folder's permissions (Program Files)
        fd, probe = tempfile.mkstemp(prefix=".ForeverAI-probe-", dir=app_dir.parent)
        os.close(fd)
        os.unlink(probe)
    except OSError:
        return f"it may not write to {app_dir.parent}"
    if not os.access(app_dir, os.W_OK):  # Linux: moving a folder elsewhere also writes to it
        return f"it may not change {app_dir}"
    return None


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _writable(func, path, _exc) -> None:
    """rmtree on Windows: a read-only file is made writable and removed again."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove(path: Path | None, timeout: float = 10.0) -> bool:
    """Delete a folder or file, trying again for a while: Windows keeps a program's files locked for a moment after
    it ends. True when it is gone."""
    if path is None:
        return True
    deadline = time.monotonic() + timeout
    while True:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, onexc=_writable)
            elif path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            pass
        if not (path.exists() or path.is_symlink()):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def staged_update(app_dir: Path | None = None) -> dict | None:
    """The update waiting next to the app (staged.json, with both copies there), when it is newer than this app."""
    folder = staging_dir(app_dir)
    staged = read_json(folder / "staged.json") if folder else None
    if not staged or not newer(staged.get("version")):
        return None
    for copy in ("new", "run"):
        if not (folder / copy / app_name() / exe_name()).is_file():
            return None
    return staged


def stage(rel: dict, progress: Callable[[int, int], None] | None = None,
          transport: httpx.BaseTransport | None = None) -> Path:
    """Download release ``rel`` and unpack it twice next to the app's folder: the new version, and the copy that
    installs it. staged.json is written last. Returns the staging folder."""
    app_dir = install_dir()
    if app_dir is None:
        raise UpdateError("it runs from source")
    folder = staging_dir(app_dir)
    if not remove(folder):
        raise UpdateError(f"cannot remove {folder}")
    try:
        folder.mkdir()
        archive = download(rel, folder, progress, transport)
        new = unpack(archive, folder / "new")
        found = bundled_version(new)
        if found != rel["version"]:
            raise UpdateError(f"{archive.name} holds version {found or 'unknown'}, not {rel['version']}")
        unpack(archive, folder / "run")
        archive.unlink()
        _write_json(folder / "staged.json", {"version": rel["version"], "from": __version__, "app": str(app_dir),
                                             "url": rel.get("url") or RELEASES_URL, "attempts": 0})
    except OSError as e:
        remove(folder)
        raise UpdateError(f"cannot write the update next to the app ({e})") from e
    except BaseException:
        remove(folder)
        raise
    return folder


# ---------------------------------------------------------------- handing over to the new version
def spawn(args: list[str], cwd: Path) -> subprocess.Popen:
    """Start a ForeverAI program on its own: it outlives this process and gets the user's environment, not the one
    PyInstaller set up for this process."""
    env = child_env(PYINSTALLER_RESET_ENVIRONMENT="1")
    for k in [k for k in env if k.startswith("_PYI_")]:
        del env[k]
    kw = dict(cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        try:  # a launcher's job could end it together with this app
            return subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:  # a job that does not allow leaving it
            return subprocess.Popen(args, creationflags=flags, **kw)
    return subprocess.Popen(args, start_new_session=True, **kw)


def installer_args(app_dir: Path, pid: int, restart: bool) -> list[str]:
    """What the copy gets: every later version accepts it (app.main, --install-update)."""
    return ["--install-update", str(app_dir), "--pid", str(pid)] + (["--restart"] if restart else [])


def start_installer(restart: bool, timeout: float | None = None, pid: int | None = None) -> str | None:
    """Start the copy of the staged version that installs it once this app (or the process ``pid``) has quit, and
    starts the app again after a restart. Returns None when it runs and the new version loads, so this app quits now;
    else why not, which is also written to result.json."""
    app_dir = install_dir()
    folder = staging_dir(app_dir)
    staged = staged_update(app_dir)
    if folder is None or staged is None:
        return "no update is ready"
    staged["attempts"] = int(staged.get("attempts") or 0) + 1
    _write_json(folder / "staged.json", staged)
    for name in ("started", "result.json"):
        (folder / name).unlink(missing_ok=True)
    exe = folder / "run" / app_name() / exe_name()
    error = None
    try:
        proc = spawn([str(exe), *installer_args(app_dir, pid or os.getpid(), restart)], folder)
    except OSError as e:
        error = f"the new version cannot be started ({e})"
    else:
        deadline = time.monotonic() + (HANDOFF_TIMEOUT if timeout is None else timeout)
        while True:
            if (folder / "started").exists():
                return None
            if proc.poll() is not None:
                error = (read_json(folder / "result.json") or {}).get("error") or \
                    f"the new version stopped (exit code {proc.returncode})"
                break
            if time.monotonic() >= deadline:
                proc.kill()
                error = "the new version did not start in time"
                break
            time.sleep(0.1)
    _write_json(folder / "result.json", {"version": staged["version"], "from": __version__, "ok": False, "error": error})
    log.warning("update to %s: %s", staged["version"], error)
    return error


class _Waiter:
    """Waits for a process to end. Opened while it still runs, since a pid can be given to a new process once the old
    one has ended."""

    def __init__(self, pid: int):
        self.pid = pid
        self.handle = self.fd = None
        if WINDOWS:
            import ctypes
            from ctypes import wintypes
            k32 = self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE; none: it has ended already
        else:
            try:
                self.fd = os.pidfd_open(pid)
            except (AttributeError, OSError):  # an older kernel, or the process has ended already
                self.fd = None

    def wait(self, timeout: float) -> bool:
        """True once the process has ended, False when it still runs after ``timeout`` seconds."""
        if WINDOWS:
            if not self.handle:
                return True
            try:
                return self.k32.WaitForSingleObject(self.handle, int(timeout * 1000)) == 0
            finally:
                self.k32.CloseHandle(self.handle)
                self.handle = None
        if self.fd is not None:
            import select
            try:
                return bool(select.select([self.fd], [], [], timeout)[0])
            finally:
                os.close(self.fd)
                self.fd = None
        deadline = time.monotonic() + timeout
        while _alive(self.pid):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True


def _alive(pid: int) -> bool:
    """A process that runs (an ended one waiting to be collected by its parent does not count)."""
    if WINDOWS:
        w = _Waiter(pid)
        return not w.wait(0)
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return text[text.rindex(")") + 2] not in "ZX"
    except FileNotFoundError:
        return False
    except (OSError, ValueError, IndexError):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _running_from(folder: Path) -> list[int]:
    """Linux: the processes whose program is in ``folder`` (another copy of the app started meanwhile)."""
    if WINDOWS:
        return []  # Windows refuses to move a folder a program runs from: the move is tried again instead
    out = []
    prefix = str(folder.resolve()) + os.sep
    for exe in Path("/proc").glob("[0-9]*/exe"):
        try:
            if os.readlink(exe).startswith(prefix):
                out.append(int(exe.parent.name))
        except (OSError, ValueError):
            continue
    return out


def _retry(action: Callable[[], None], what: str) -> None:
    deadline = time.monotonic() + RETRY_TIMEOUT
    while True:
        try:
            action()
            return
        except OSError as e:
            if time.monotonic() >= deadline:
                raise UpdateError(f"{what} ({e})") from e
            time.sleep(0.25)


def swap(app_dir: Path, new: Path, old: Path) -> None:
    """Move the app's folder to ``old`` and ``new`` into its place. The old folder goes back when the new one cannot
    move."""
    if not remove(old):
        raise UpdateError(f"cannot remove {old}")
    _retry(lambda: os.rename(app_dir, old), f"cannot move {app_dir}")
    try:
        _retry(lambda: os.rename(new, app_dir), f"cannot move the new version to {app_dir}")
    except UpdateError as e:
        try:
            _retry(lambda: os.rename(old, app_dir), "cannot move the previous version back")
        except UpdateError as back:
            raise UpdateError(f"{e}; {back}: it is in {old}") from e
        raise


def self_check() -> None:
    """The new version's code loads here: every module of the app imports, Tcl starts, and it can check the signature
    of the update after it. A build that would not start, or not update again, stops the update before anything is
    moved."""
    import importlib
    import pkgutil

    import foreverai
    names = {"foreverai.app", "foreverai.window"}
    names.update(m.name for m in pkgutil.walk_packages(foreverai.__path__, "foreverai.")
                 if not m.name.endswith("__main__"))
    for name in sorted(names):
        importlib.import_module(name)
    import tkinter
    tkinter.Tcl().eval("info patchlevel")
    release_keys()
    key, signature = bytes.fromhex(_RFC8032_KEY), bytes.fromhex(_RFC8032_SIGNATURE)
    if not ed25519_verify(key, signature, b"") or ed25519_verify(key, signature, b"x"):
        raise UpdateError("the new version cannot check signatures")
    log.info("the new version loads (%d modules)", len(names))


def install(app_dir: Path, pid: int, restart: bool) -> int:
    """--install-update: run by the copy of the new version in .ForeverAI-update/run. Checks that the new version
    loads, waits until the app (``pid``) has quit, swaps the folders and writes result.json; with ``restart`` it then
    starts the app again (the new version, or the old one when the swap failed). Returns the exit code."""
    app_dir = Path(app_dir).absolute()
    folder = app_dir.parent / STAGING
    handler = None
    try:
        handler = logging.FileHandler(folder / "install.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError:
        pass
    staged = read_json(folder / "staged.json") or {}
    result = {"version": staged.get("version") or __version__, "from": staged.get("from") or "", "ok": False}
    log.info("installer %s: %s into %s (app pid %s, restart %s)", __version__, result["version"], app_dir, pid, restart)
    lock = None
    restart_app = False  # the app quit for this and no other ForeverAI runs: it is started again
    try:
        waiter = _Waiter(pid)
        self_check()
        (folder / "started").write_text(str(os.getpid()), encoding="utf-8")
        if not waiter.wait(QUIT_TIMEOUT):
            raise UpdateError("ForeverAI did not quit")
        from .app import _single_instance
        deadline = time.monotonic() + RETRY_TIMEOUT
        while (lock := _single_instance()) is None:  # no other ForeverAI may start while the folders move
            if time.monotonic() >= deadline:
                raise UpdateError("ForeverAI was started again before the update was installed")
            time.sleep(0.2)
        restart_app = restart
        while _running_from(app_dir):
            if time.monotonic() >= deadline:
                raise UpdateError(f"a ForeverAI still runs from {app_dir}")
            time.sleep(0.2)
        swap(app_dir, folder / "new" / app_name(), folder / "old")
        result["ok"] = True
        log.info("installed %s", result["version"])
    except Exception as e:
        log.exception("installation failed")
        result["error"] = str(e) or type(e).__name__
    finally:
        try:
            _write_json(folder / "result.json", result)
        except OSError:
            log.exception("cannot write result.json")
        if lock is not None:
            lock.close()  # before the app starts again: it takes the same port
    if restart_app:  # the new version, or the old one put back
        try:
            spawn([str(app_dir / exe_name())], app_dir)
        except OSError:
            log.exception("cannot start %s", app_dir / exe_name())
    if handler is not None:
        log.removeHandler(handler)
        handler.close()
    return 0 if result["ok"] else 1


def install_at_start() -> bool:
    """At start, before the window: an update downloaded earlier and never tried (the app was not quit since, e.g. the
    computer was shut down with it open) is installed now, with a restart. True: the installer runs and the caller
    quits at once."""
    staged = staged_update()
    if not staged or staged.get("attempts"):
        return False  # none, or an installation was tried already: the next one waits for a quit or Restart now
    from . import config
    if cannot_update() or not config.SettingsStore().settings.auto_update:
        return False
    log.info("installing ForeverAI %s, downloaded earlier", staged["version"])
    return start_installer(restart=True) is None


def update_now(say: Callable[[str], None] = print, transport: httpx.BaseTransport | None = None) -> int:
    """--update: download a newer version and have it installed as soon as this command ends. Returns the exit code."""
    from .app import _single_instance
    reason = cannot_update()
    if reason:
        say(f"ForeverAI cannot update itself: {reason}. New versions: {RELEASES_URL}")
        return 1
    lock = _single_instance()  # held until this process ends: the installer waits for it
    if lock is None:
        say("ForeverAI is running: click Restart now in its window, or quit it and run this again.")
        return 1
    try:
        rel = fetch_latest(transport)
    except UpdateError as e:
        say(f"Could not check for a new version: {e}")
        return 1
    if not rel or not newer(rel["version"]):
        say(f"ForeverAI {__version__} is up to date.")
        return 0
    staged = staged_update()
    if not staged or staged["version"] != rel["version"]:
        say(f"Downloading ForeverAI {rel['version']} ({_mb(rel.get('size') or 0)})…")
        shown = [-1]

        def progress(done: int, total: int) -> None:
            tenth = done * 10 // total if total else 0
            if tenth != shown[0]:
                shown[0] = tenth
                say(f"  {tenth * 10}%")
        try:
            stage(rel, progress, transport)
        except UpdateError as e:
            say(f"The update failed: {e}")
            return 1
    error = start_installer(restart=False)
    if error:
        say(f"The update failed: {error}")
        return 1
    say(f"ForeverAI {rel['version']} is installed as soon as this command ends.")
    return 0


# ---------------------------------------------------------------- the running app's side
class Updater:
    """Updates of the running app: checks for a newer release (at start, every few hours, and when asked), downloads it
    in the background while updates are automatic, and hands over to the new version when the app quits or restarts.
    The window shows ``text()`` and the ``button()`` of the current state; ``exit_requested`` asks it to quit."""

    def __init__(self, store, transport: httpx.BaseTransport | None = None):
        self.store = store
        self.transport = transport
        self.state = "idle"  # idle checking current offline available downloading ready installing failed source off
        self.release: dict | None = None  # the newer release, or the staged one
        self.done = self.total = 0  # bytes of the download
        self.error = ""
        self.checked_at = 0.0
        self.exit_requested = False  # the installer runs: the window quits now
        self.reason = cannot_update()  # why this app cannot replace itself
        self._busy = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()

    # ------------------------------------------------------------ what the window shows
    def text(self) -> str:
        v = (self.release or {}).get("version") or "?"
        s = self.state
        if s == "source":
            return "Runs from source: new versions come with git pull"
        if s == "off":
            return "Update checks are off (FOREVERAI_NO_UPDATE_CHECK)"
        if s in ("idle", "checking"):
            return "Checking for a new version…"
        if s == "current":
            return f"Up to date · checked {_hhmm(self.checked_at)}"
        if s == "offline":
            return f"Could not check for a new version · {_hhmm(self.checked_at)}"
        if s == "available":
            if self.reason:
                return f"ForeverAI {v} is out. This copy cannot update itself ({self.reason}): download it from GitHub"
            return f"ForeverAI {v} is available"
        if s == "downloading":
            pct = f" {self.done * 100 // self.total}%" if self.total else ""
            return f"Downloading ForeverAI {v}…{pct}"
        if s == "ready":
            if self.store.settings.auto_update:
                return f"ForeverAI {v} is ready: it is installed when you quit ForeverAI"
            return f"ForeverAI {v} is ready to install"
        if s == "installing":
            return f"Installing ForeverAI {v}…"
        if s == "failed":
            return f"The update to {v} failed: {self.error}" if self.error else f"The update to {v} failed"
        return ""

    def button(self) -> tuple[str, bool] | None:
        """(label, enabled) of the button for this state, or None for no button."""
        return {"idle": ("Check now", False), "checking": ("Check now", False), "current": ("Check now", True),
                "offline": ("Check now", True),
                "available": ("Download…", True) if self.reason else ("Install", True),
                "downloading": ("Restart now", False), "ready": ("Restart now", True),
                "installing": ("Restart now", False), "failed": ("Try again", True)}.get(self.state)

    def press(self) -> None:
        """The window's button."""
        s = self.state
        if s in ("current", "offline"):
            self.check_now()
        elif s == "available" and self.reason:
            open_path((self.release or {}).get("url") or RELEASES_URL)
        elif s in ("available", "failed"):
            self._background(self._install_now)
        elif s == "ready":
            self._background(self._restart)

    # ------------------------------------------------------------ running
    def start(self) -> None:
        if install_dir() is None:
            self.state = "source"
            return
        if os.environ.get("FOREVERAI_NO_UPDATE_CHECK"):
            self.state = "off"
            return
        threading.Thread(target=self._run, daemon=True, name="updates").start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def check_now(self) -> None:
        self._wake.set()

    def on_quit(self) -> None:
        """The app quits: a downloaded update is installed now (automatic updates only). A download or check that is
        still running is left behind; the update is then installed at the next start."""
        self.stop()
        if not self._busy.acquire(timeout=5):
            return
        try:
            if self.state == "ready" and self.store.settings.auto_update and not self.reason and \
                    not self.exit_requested and start_installer(restart=False) is None:
                log.info("ForeverAI %s is installed once the app has quit", (self.release or {}).get("version"))
        finally:
            self._busy.release()

    def toggled(self) -> None:
        """Automatic updates were switched on or off: a release found meanwhile is downloaded now."""
        if self.store.settings.auto_update and self.state == "available":
            self.check_now()

    def _background(self, work: Callable[[], None]) -> None:
        def run():
            if not self._busy.acquire(blocking=False):
                return
            try:
                work()
            except Exception as e:  # never leave the button in a state that cannot be pressed
                log.exception("update failed")
                self.state, self.error = "failed", str(e)
            finally:
                self._busy.release()
        threading.Thread(target=run, daemon=True, name="update-action").start()

    def _run(self) -> None:
        with self._busy:
            self.resume()
        while not self._stop.is_set():
            with self._busy:
                if self.exit_requested:
                    return
                if self.state == "installing":  # another start's installer: its result, once it has one
                    self.resume()
                else:
                    self._check()
            self._wake.wait(10 if self.state == "installing" else CHECK_EVERY)
            self._wake.clear()

    def resume(self) -> None:
        """At start: what an earlier update left next to the app. The result of an installation is reported (and what
        is left removed); a downloaded update that failed to install is tried again at the next quit, up to
        MAX_ATTEMPTS times; one never tried stays ready."""
        folder = staging_dir()
        if folder is None or not folder.exists():
            return
        result = read_json(folder / "result.json")
        staged = read_json(folder / "staged.json") or {}
        started = read_json_int(folder / "started")
        if result is None and started:
            try:  # an installer is done within minutes: an older pid may belong to another program by now
                recent = time.time() - (folder / "started").stat().st_mtime < INSTALLER_MAX_S
            except OSError:
                recent = False
            if recent and _alive(started):
                self.state = "installing"  # another start of the app hands over to it right now
                self.release = {"version": staged.get("version")}
                return
            result = {"version": staged.get("version"), "ok": False, "error": "the installation did not finish"}
        if result is not None and result.get("ok"):
            if result.get("version") == __version__:
                activity.info("Updated to ForeverAI %s", __version__)
            if self.store.settings.update_failed:
                self.store.update(update_failed="")
            # here and not in a thread of its own, which would also remove the next update while it downloads; the
            # installer may still be ending (Windows keeps its files locked until then)
            if not remove(folder, 60.0):
                log.warning("cannot remove %s", folder)
            return
        if result is not None:
            version, error = result.get("version") or "?", result.get("error") or "unknown error"
            log.warning("update to %s failed: %s", version, error)
            self.release, self.error = {"version": version, "url": staged.get("url")}, error
            if staged_update() and int(staged.get("attempts") or 0) < MAX_ATTEMPTS:
                for name in ("result.json", "started"):
                    (folder / name).unlink(missing_ok=True)
                self.state = "ready"
                activity.warning("ForeverAI %s could not be installed (%s); it is tried again when you quit", version,
                                 error)
                return
            self.state = "failed"
            activity.warning("The update to ForeverAI %s failed: %s", version, error)
            self.store.update(update_failed=version)
            remove(folder)
            return
        staged = staged_update()
        if staged:
            self.release, self.state = {"version": staged["version"], "url": staged.get("url")}, "ready"
            return
        remove(folder)  # an unfinished download, or an update to this version or an older one

    def _check(self) -> None:
        if self.state not in ("ready", "failed", "downloading"):
            self.state = "checking"
        try:
            rel = fetch_latest(self.transport)
        except UpdateError as e:
            self.checked_at = time.time()
            if self.state == "checking":
                self.state, self.error = "offline", str(e)
            log.info("update check failed: %s", e)
            return
        self.checked_at = time.time()
        if not rel or not newer(rel["version"]):
            if self.state != "ready":
                self.state, self.release = "current", None
            return
        staged = staged_update()
        if staged and version_key(staged["version"]) >= version_key(rel["version"]):
            self.state, self.release = "ready", dict(rel, version=staged["version"])
            return
        if (self.release or {}).get("version") != rel["version"]:
            activity.info("ForeverAI %s is available (you have %s)", rel["version"], __version__)
        self.release = rel
        s = self.store.settings
        if s.update_failed == rel["version"] and not self.reason:
            self.state = "failed"  # tried and failed: only Try again downloads it again
            return
        if self.reason or not s.auto_update:
            self.state = "available"
            return
        self._download(rel)

    def _download(self, rel: dict) -> bool:
        self.state, self.done, self.total, self.error = "downloading", 0, int(rel.get("size") or 0), ""
        activity.info("Downloading ForeverAI %s (%s)…", rel["version"], _mb(self.total))
        try:
            stage(rel, self._progress, self.transport)
        except UpdateError as e:
            self.state, self.error = "failed", str(e)
            activity.warning("The update to ForeverAI %s failed: %s", rel["version"], e)
            return False
        self.state = "ready"
        activity.info("ForeverAI %s is ready: it is installed when you quit ForeverAI, or click Restart now",
                      rel["version"])
        return True

    def _progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def _install_now(self) -> None:
        """Install (automatic updates off) or Try again: download the newest release when needed, then restart."""
        if self.store.settings.update_failed:
            self.store.update(update_failed="")
        if not staged_update():
            self.state = "checking"
            try:
                rel = fetch_latest(self.transport)
            except UpdateError as e:
                self.state, self.error = "failed", f"could not check for a new version ({e})"
                return
            if not rel or not newer(rel["version"]):
                self.state, self.release = "current", None
                return
            self.release = rel
            if not self._download(rel):
                return
        self._restart()

    def _restart(self) -> None:
        self.state = "installing"
        if start_installer(restart=True) is None:
            self.exit_requested = True
            return
        self.state = "idle"
        self.resume()  # result.json says why: tried again at the next quit, or given up


def read_json_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t)) if t else "—"


def _mb(n: int) -> str:
    return f"{n / 2**20:.0f} MB" if n else "size unknown"


def _short(e: Exception) -> str:
    return (str(e) or type(e).__name__).splitlines()[0][:160]
