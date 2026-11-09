# ForeverAI: instructions for Claude

## Apps are published as GitHub Releases

The prebuilt apps are not committed. `.github/workflows/release.yml` builds both from the same commit:

| Platform | Build script | Local output | Release file |
|---|---|---|---|
| Windows | `build_exe.ps1` (`-Package`) | `release/windows/ForeverAI-Windows/ForeverAI-Windows.exe` | `ForeverAI-<version>-Windows-x64.zip` |
| Linux | `build_linux.sh` (`--package`) | `release/linux/ForeverAI-Linux/ForeverAI-Linux` | `ForeverAI-<version>-Linux-x86_64.tar.gz` |

- Every pull request builds both apps (workflow artifacts). A push to `main` publishes the GitHub Release `v<version>` with both files and `SHA256SUMS.txt` when the version in the source has no release yet; the workflow creates the tag. So a version bump merged to `main` is a release, and changes merged without a bump are released with the next one. Pushing a tag `v<version>` also publishes it; the tag must match the version in the source.
- `release/`, `build/` and `dist/` are local build output and ignored by git. Never commit build output.
- The file names carry the platform (Windows / Linux) and must stay that way; `companion/foreverai/updates.py` (`asset_name`) finds the download for the update notice by these names, and `tests/test_release.py` checks that the scripts and the workflow agree.
- The starter knowledge base is `seed/kb.sqlite` (committed, bundled into both apps).

## Changes that affect the apps

Anything that goes into the apps: `companion/**` (code, `requirements.txt`, `constraints.txt`), `addon/ForeverAI/**` (bundled and copied into the game folder), `assets/**`, `seed/kb.sqlite`, the version number, `build_exe.ps1`, `build_linux.sh`.

- The CI builds both platforms for every pull request; check that both build jobs pass before you say a change is done. Do not merge or tag with one platform failing.
- PyInstaller cannot cross-compile. Locally you can only build your own platform; never copy a build from one platform into the other.
- Version number: `addon/ForeverAI/ForeverAI.toc`, `ns.version` in `addon/ForeverAI/Core.lua`, `companion/foreverai/__init__.py`, `companion/pyproject.toml` (all four together). Bumping it publishes a release once merged to `main`.

### Before you finish

1. Run the tests: `companion/.venv/bin/python -m pytest -q` (Linux) or `companion\.venv\Scripts\python.exe -m pytest -q` (Windows), in `companion`. They include a Lua 5.1 test of the add-on's auction code when `lua5.1` is installed; `luac5.1 -p addon/ForeverAI/*.lua` checks the add-on's syntax.
2. If you changed what goes into the apps, build your platform and smoke-test it. Linux: `release/linux/ForeverAI-Linux/ForeverAI-Linux --help` must print the usage. Windows (a windowed .exe prints nothing): check that `ForeverAI-Windows.exe` exists. On both: `_internal/seed/kb.sqlite` and `_internal/addon/ForeverAI/ForeverAI.toc` must be present, and the `## Version:` in that `.toc` must match the source.
3. Push and check that the workflow's test, Linux and Windows jobs pass.
4. Mention in your summary which builds were checked and how (local build, CI run).

## Integrations with other add-ons

ForeverAI reads other add-ons (Auctionator's price data and public API, Pawn's upgrade check, AceConfig options trees) but never changes their code or files. Changes to another add-on's settings only happen through a proposal the player accepts in the game.

## Conventions

- Application text is English; the user speaks Polish in conversations.
- The Linux app must run on the user's own Python-free system, so the Linux build is a PyInstaller bundle; it runs on distributions with the same or a newer glibc than the machine it was built on. The CI builds on Ubuntu 22.04 for that reason.
- Use the distribution's Python with Tk for the Linux build, not a standalone one (uv, python-build-standalone): its Tk has no font rendering and the window shows bitmap fonts. The CI checks that tkinter links the system Tk.
- Do not commit `companion/data/`, logs, `.env` or API keys (see `.gitignore`).
