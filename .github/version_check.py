"""Pull request check: a change to what goes into the apps raises the version by one step (patch, minor or major)
from the base branch, so merging it publishes a release (release.yml); any other change keeps the version. A minor or
major step also brings a freshly downloaded knowledge base (seed/kb.sqlite).

    python3 .github/version_check.py origin/main      (in the repository root)"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# what goes into the apps: keep it the same as "Changes that affect the apps" in CLAUDE.md
APP_PATHS = ("companion/foreverai/", "companion/foreverai_app.py", "companion/requirements.txt",
             "companion/constraints.txt", "addon/ForeverAI/", "assets/", "seed/kb.sqlite",
             "build_exe.ps1", "build_linux.sh")
# the places that hold the version: a change there that is only the version is the bump, not a change to the apps
VERSION_FILES = ("addon/ForeverAI/ForeverAI.toc", "addon/ForeverAI/Core.lua", "companion/foreverai/__init__.py")
KB = "seed/kb.sqlite"
KB_SOURCES = ("wiki", "blizzard")  # what python -m foreverai.kb.sync downloads
FRESH_HOURS = 24  # the app itself syncs a source again after a day (kb/blizzard.py, needs_sync)


def next_versions(v: str) -> dict[str, str]:
    """'0.5.2' -> {patch: 0.5.3, minor: 0.6.0, major: 1.0.0}"""
    x, y, z = (int(n) for n in re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v).groups())
    return {"patch": f"{x}.{y}.{z + 1}", "minor": f"{x}.{y + 1}.0", "major": f"{x + 1}.0.0"}


def app_changes(changed: list[str], old: str, new: str, read_base, read_head) -> list[str]:
    """The changed files that go into the apps; a version file counts only for a change besides the version."""
    return [p for p in changed if p.startswith(APP_PATHS)
            and not (p in VERSION_FILES and read_head(p).replace(new, old) == read_base(p))]


def problem(old: str, new: str, apps: list[str]) -> str | None:
    """What is wrong with going from version old to new, given the changed app files; None when it is right."""
    step = next_versions(old)
    if apps and new not in step.values():
        shown = ", ".join(apps[:3]) + (f" and {len(apps) - 3} more" if len(apps) > 3 else "")
        return (f"this pull request changes the apps ({shown}) but the version goes from {old} to {new}: "
                f"raise it by one step, to {step['patch']} (patch), {step['minor']} (minor) "
                f"or {step['major']} (major, only when the user decides so); see CLAUDE.md, Version number")
    if not apps and new != old:
        return (f"this pull request changes nothing that goes into the apps: keep the version at {old}, "
                f"a bump would publish a release with nothing new in it")
    return None


def kb_problem(changed: list[str], syncs: dict[str, tuple[float, str]], committed: float) -> str | None:
    """What is wrong with the knowledge base of a minor or major step; None when it is fresh. ``syncs`` holds
    (finished, errors) of the newest sync of each source, ``committed`` the time of the commit that changed it."""
    again = "download it again, see CLAUDE.md, Version number"
    if KB not in changed:
        return f"a minor or major version step brings a freshly downloaded {KB}: {again}"
    for source in KB_SOURCES:
        if source not in syncs:
            return f"{KB} has no {source} sync: {again}"
        finished, errors = syncs[source]
        if errors:
            return f"the last {source} sync in {KB} had errors ({errors.splitlines()[0]}): {again}"
        if committed - finished > FRESH_HOURS * 3600:
            return f"{KB} was committed more than {FRESH_HOURS} hours after its last {source} sync: {again}"
    return None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def kb_syncs(ref: str) -> dict[str, tuple[float, str]]:
    """{source: (finished, errors)} of the newest sync of each source in the knowledge base at ref."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d, "kb.sqlite")
        path.write_bytes(subprocess.run(["git", "show", f"{ref}:{KB}"], check=True, capture_output=True).stdout)
        db = sqlite3.connect(path)
        rows = db.execute("SELECT source, finished, errors FROM sync_log ORDER BY id").fetchall()
        db.close()
    return {source: (finished, errors or "") for source, finished, errors in rows}  # later rows win


def main(base: str) -> int:
    fork = git("merge-base", base, "HEAD").strip()

    def reader(ref):
        return lambda path: git("show", f"{ref}:{path}")

    def version(ref):
        return re.search(r'__version__ = "(.+?)"', reader(ref)("companion/foreverai/__init__.py")).group(1)

    old, new = version(fork), version("HEAD")
    changed = git("diff", "--name-only", f"{fork}..HEAD").splitlines()
    apps = app_changes(changed, old, new, reader(fork), reader("HEAD"))
    msg = problem(old, new, apps)
    step = next_versions(old)
    feature = new in (step["minor"], step["major"])
    if not msg and feature:
        committed = git("log", "-1", "--format=%ct", f"{fork}..HEAD", "--", KB).strip()
        msg = kb_problem(changed, kb_syncs("HEAD") if KB in changed else {}, float(committed or 0))
    if msg:
        print(msg, file=sys.stderr)
        return 1
    print(f"version {old} -> {new}: " + ("one step for the app changes" if apps else "no app changes, kept")
          + (f", {KB} freshly downloaded" if feature else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
