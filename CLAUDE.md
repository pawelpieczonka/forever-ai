# ForeverAI: instructions for Claude

## Apps are published as GitHub Releases

The prebuilt apps are not committed. `.github/workflows/release.yml` builds both from the same commit:

| Platform | Build script | Local output | Release file |
|---|---|---|---|
| Windows | `build_exe.ps1` (`-Package`) | `release/windows/ForeverAI-Windows/ForeverAI-Windows.exe` | `ForeverAI-<version>-Windows-x64.zip` |
| Linux | `build_linux.sh` (`--package`) | `release/linux/ForeverAI-Linux/ForeverAI-Linux` | `ForeverAI-<version>-Linux-x86_64.tar.gz` |

- Every pull request builds both apps (workflow artifacts). A push to `main` publishes the GitHub Release `v<version>` with both files and `SHA256SUMS.txt` when the version in the source has no release yet; the workflow creates the tag. So a version bump merged to `main` is a release, and changes merged without a bump are released with the next one. Pushing a tag `v<version>` also publishes it; the tag must match the version in the source.
- `release/`, `build/` and `dist/` are local build output and ignored by git. Never commit build output.
- The file names carry the platform (Windows / Linux) and must stay that way; `companion/foreverai/updates.py` (`asset_name`) finds the download for the update notice by these names, and `companion/tests/test_release.py` checks that the scripts and the workflow agree.
- The starter knowledge base is `seed/kb.sqlite` (committed, bundled into both apps).

## Changes that affect the apps

Anything that goes into the apps: the code (`companion/foreverai/**`, `companion/foreverai_app.py`), `companion/requirements.txt`, `companion/constraints.txt`, `addon/ForeverAI/**` (bundled and copied into the game folder), `assets/**`, `seed/kb.sqlite`, the version number, `build_exe.ps1`, `build_linux.sh`. The tests in `companion/tests/` and the rest of `companion/pyproject.toml` do not; `.github/version_check.py` (`APP_PATHS`) uses the same list.

- The CI builds both platforms for every pull request; check that both build jobs pass before you say a change is done. Do not merge or tag with one platform failing.
- PyInstaller cannot cross-compile. Locally you can only build your own platform; never copy a build from one platform into the other.

### Version number

The version is `X.Y.Z`, kept in four files that change together: `addon/ForeverAI/ForeverAI.toc`, `ns.version` in `addon/ForeverAI/Core.lua`, `companion/foreverai/__init__.py`, `companion/pyproject.toml` (`companion/tests/test_release.py` checks that they agree).

Every pull request that changes what goes into the apps raises the version by one step from the version on `main`, so merging it publishes a release:

- **Z (patch):** small changes, bug fixes and minor improvements.
- **Y (minor):** a single new feature; Z goes back to 0.
- **X (major):** a large set of changes that adds entirely new functionality to the add-on; Y and Z go back to 0. This is the user's decision: when a pull request might be major, ask them, and never raise X on your own.

The step belongs to the pull request, not to its commits: with 0.5.2 on `main`, a pull request with a fix is 0.5.3, stays 0.5.3 with a second fix, and becomes 0.6.0 when a later commit adds a feature. When `main` gets a new version before the pull request is merged, merge `main` and take the step from that version. A pull request that changes nothing in the apps (the README, this file, tests, the workflow) keeps the version, since a bump would publish a release with nothing new in it.

A minor or major pull request also brings a freshly downloaded knowledge base, so every release with new features ships current game data. Update a copy of `seed/kb.sqlite` from all sources in a temporary directory (the app's own knowledge base in `companion/data/` stays as it is) and commit it in the pull request:

```bash
d="$(mktemp -d)" && cp seed/kb.sqlite "$d/" && (cd companion && FOREVERAI_DATA="$d" .venv/bin/python -m foreverai.kb.sync --force) && cp "$d/kb.sqlite" seed/kb.sqlite
```

On Windows the same steps with `companion\.venv\Scripts\python.exe` and `$env:FOREVERAI_DATA`. Start from the committed file, not an empty one: Blizzard articles that have left the news pages are kept only from the existing knowledge base. `--force` checks every page against its source, downloads what changed and drops wiki pages that left the category.

The workflow's `version` job checks all of this on every pull request. For the knowledge base, the newest sync of each source (`sync_log` in the file) must have no errors and must have finished at most 24 hours before the commit that changed the file.

### Screenshots

A pull request that changes anything visible in the desktop app shows a screenshot from before and after the change in its description. That covers the window, its dialogs and the icons in `assets/`, but not the add-on's in-game UI. Take both from source: "before" on `main` (in a worktree, e.g. `git worktree add ../forever-ai-main origin/main`) and "after" on the branch. Run each with a fresh data folder and a stand-in game folder:

```bash
py=/path/to/system-tk-venv/bin/python kb="$PWD/seed/kb.sqlite"   # once, in this checkout
g="$(mktemp -d)" && touch "$g/Wow.exe" && d="$(mktemp -d)" && cp "$kb" "$d/" && echo '{"shortcut_offered": true}' > "$d/settings.json" && (cd companion && FOREVERAI_DATA="$d" "$py" -m foreverai --game-dir "$g")
```

On Windows, use the same steps with `$env:FOREVERAI_DATA`.

- **Python with the distribution's Tk.** Use the same kind of Python as the Linux build. A standalone Python's Tk (uv, python-build-standalone) draws bitmap fonts, so its screenshots do not show what players see and hide layout that only breaks with real fonts. `companion/.venv` may be such a Python. In that case make a venv from `/usr/bin/python3` and run `pip install -r companion/requirements.txt -c companion/constraints.txt` in it.
- **Stand-in game folder.** Any folder with a `Wow.exe` works. It stops the app from replacing the add-on in the real game with the one from `main`.
- **Fresh data folder.** It keeps the user's settings, conversations and characters out of the pictures, which matters because the repository is public. The same `kb.sqlite` and the skipped desktop-shortcut question make both windows start in the same state.
- **One instance.** Only one ForeverAI runs at a time, so ask the user to close theirs first.
- **Capturing.** Wait until the window has settled, then capture the same view at the window's default size. On Linux, `import -window ForeverAI before.png` (ImageMagick) captures just the window, also under Wayland, because Tk runs on XWayland.

Pull request images go on the `screenshots` branch, which is never merged, so they stay out of `main` and the apps. Put them under `pr-<number>/` as `before.png` and `after.png` (one pair per changed view); the first time, create the branch with `git worktree add --orphan -b screenshots <dir>`. Show each pair side by side:

```markdown
| Before | After |
|---|---|
| ![before](https://github.com/pawelpieczonka/forever-ai/blob/screenshots/pr-<number>/before.png?raw=true) | ![after](https://github.com/pawelpieczonka/forever-ai/blob/screenshots/pr-<number>/after.png?raw=true) |
```

### Before you finish

1. Run the tests: `companion/.venv/bin/python -m pytest -q` (Linux) or `companion\.venv\Scripts\python.exe -m pytest -q` (Windows), in `companion`. They include a Lua 5.1 test of the add-on's auction code when `lua5.1` is installed; `luac5.1 -p addon/ForeverAI/*.lua` checks the add-on's syntax.
2. If you changed what goes into the apps, build your platform and smoke-test it. `build_linux.sh` refuses to build while the app in `release/linux` is running (replacing its files breaks it): ask the user to close it. Linux: `release/linux/ForeverAI-Linux/ForeverAI-Linux --help` must print the usage. Windows (a windowed .exe prints nothing): check that `ForeverAI-Windows.exe` exists. On both: `_internal/seed/kb.sqlite` and `_internal/addon/ForeverAI/ForeverAI.toc` must be present, and the `## Version:` in that `.toc` must match the source.
3. Push, open the pull request (with the screenshots when the app looks different) and check that the workflow's test, version, Linux and Windows jobs pass.
4. Mention in your summary which builds were checked and how (local build, CI run).

## Integrations with other add-ons

ForeverAI reads other add-ons (Auctionator's price data and public API, Pawn's upgrade check, AceConfig options trees) but never changes their code or files. Changes to another add-on's settings only happen through a proposal the player accepts in the game.

## Conventions

- Application text is English; the user speaks Polish in conversations.
- The Linux app must run on the user's own Python-free system, so the Linux build is a PyInstaller bundle; it runs on distributions with the same or a newer glibc than the machine it was built on. The CI builds on Ubuntu 22.04 for that reason.
- Use the distribution's Python with Tk for the Linux build, not a standalone one (uv, python-build-standalone): its Tk has no font rendering and the window shows bitmap fonts. The CI checks that tkinter links the system Tk.
- Do not commit `companion/data/`, logs, `.env` or API keys (see `.gitignore`).
