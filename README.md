# ForeverAI

AI assistant in a chat window inside World of Warcraft: Forever. It answers using your character, quests, gear and surroundings, a local Forever knowledge base and the auction house prices of your auction add-on. Answers come from Claude Code (Claude subscription), OpenRouter or Google Gemini.

## Requirements

- Windows 10/11, or Linux with WoW Forever running under Wine (Lutris, Steam/Proton, Bottles, Heroic or plain Wine)
- One AI provider: Claude Code signed in, an [OpenRouter key](https://openrouter.ai/keys) or a [Google Gemini API key](https://aistudio.google.com/apikey)
- Recommended: Questie, TomTom; for auction house questions Auctionator (and Pawn for upgrade checks)

## Download

Get the newest version from [GitHub Releases](https://github.com/pawelpieczonka/forever-ai/releases/latest). Each file is for one platform:

| File | Platform | Start |
|---|---|---|
| `ForeverAI-<version>-Windows-x64.zip` | Windows 10/11 | `ForeverAI-Windows/ForeverAI-Windows.exe` |
| `ForeverAI-<version>-Linux-x86_64.tar.gz` | Linux (glibc 2.35 or newer: Ubuntu 22.04, Debian 12 and later) | `ForeverAI-Linux/ForeverAI-Linux` |

No Python is needed. `SHA256SUMS.txt` in the release has the checksums. The app tells you when a newer release is out (**Download update** in its window).

## Install and run

### Windows

1. Extract the zip anywhere and run `ForeverAI-Windows\ForeverAI-Windows.exe`. It finds the game and installs the add-on.
2. In the game type `/reload`, then click the **AI** button or type `/fai`.

### Linux

1. Extract the archive (`tar -xzf ForeverAI-<version>-Linux-x86_64.tar.gz`) and run `ForeverAI-Linux/ForeverAI-Linux` (from a file manager or a terminal).
2. The app finds the game in your Wine prefixes (`$WINEPREFIX`, Lutris, `~/.wine`, `~/Games`, Heroic, Bottles, Steam) or on mounted disks, and installs the add-on. If it does not, choose the `World of Warcraft` folder or start it with `--game-dir`.
3. In the game type `/reload`, then click the **AI** button or type `/fai`.

**Desktop shortcut** adds ForeverAI to the application menu and to the desktop. Claude Code is found on `PATH`, in `~/.local/bin`, npm/nvm installs and the Claude desktop app.

Keep ForeverAI running while you play.

### Choosing the AI

At start the app checks Claude Code first: when it is installed and signed in, it is used and no key is needed. Otherwise the window asks for an OpenRouter or a Google Gemini key (checked with the provider before it is saved). The AI is set in the app window under **AI settings**: the provider, the model (for OpenRouter and Gemini also any other model by its ID), the thinking level of Claude Code and web search; changes apply to the next question. **Keys & sign-in…** saves a key or signs in to Claude Code (also to switch back to a signed-in Claude Code). Keys are stored only on your computer, in the system's credential store: Windows Credential Manager, or the Secret Service on Linux (GNOME Keyring, KWallet, KeePassXC). Without one, a key is kept in `.env` in the data folder, readable only by your user, and moves to the credential store once one is available (keys saved by older versions move the same way). If Google cannot serve the chosen Gemini model (overloaded, out of quota or not answering), `gemini-flash-lite-latest` answers instead: when an answer has not started after 10 seconds, a short test request tells an overloaded model from a busy one, so the switch takes seconds rather than a long timeout. The chosen model is checked again in the background every few minutes and used again once it can answer.

## Usage

- **Alt+click** an item, spell or quest to add it to your question.
- Changes in the game (add-on options, selling, repairing, abandoning a quest) happen only after you click **Accept**.
- **Auction house:** ask e.g. "Find the best one-handed sword on the AH that is an upgrade for me, up to 100 gold". The AI searches the prices of Auctionator's last scan (open the auction house and scan in Auctionator first) and, with Pawn, checks which items are upgrades. **Search on the AH** under the answer shows the recommended items in Auctionator's Shopping tab while the auction house is open. ForeverAI only reads the other add-ons' data and never changes them; buying stays with you.
- **Routes and marks:** ask e.g. "Plan a route through my quests" or "Where do I find Befouled Webwood? Mark it". The stops go on the map (TomTom when it is installed, the game's map pin otherwise), stay there after a `/reload`, and each one comes off when you reach it. Tell the AI what to leave out or do last and the route follows.
- **Tracking creatures:** when a mark is where a creature is to be found, the add-on watches for it: a living one in sight gets a marker over its nameplate, a sound and a line in the chat. Every creature that counts for the same quest objective is watched for, and tracking ends by itself when the objective is done. While something is tracked the game shows the nameplates of all enemies; your own settings come back when it ends.
- The strip above the text field shows what is tracked and how many stops are marked; **x** ends either. `/fai stop` ends both.
- **Settings** tab: which game data is sent, font size, the AI button; it also shows the AI in use (chosen in the app window).
- Commands: `/fai`, `/fai <question>`, `/fai new`, `/fai stop`, `/fai track`, `/fai debug`.

## Run from source

Python 3.12+ with Tk from your distribution (Linux: `sudo apt install python3-venv python3-tk`, `sudo dnf install python3-tkinter` or `sudo pacman -S tk`). Use the distribution's Python, not a standalone one (uv, python-build-standalone): their Tk has no font rendering, so the window shows bitmap fonts.

```bash
python3 -m venv companion/.venv
companion/.venv/bin/python -m pip install -r companion/requirements.txt
cd companion && .venv/bin/python -m foreverai
```

Tests: `.venv/bin/python -m pytest -q` (Linux) or `.venv\Scripts\python.exe -m pytest -q` (Windows), in `companion`.

The add-on simulator runs the add-on outside the game, connected to the app, and saves pictures of its window: `.venv/bin/python -m addonsim --help`, in `companion`.

## Build

The apps are not committed: GitHub Actions (`.github/workflows/release.yml`) builds the Windows and the Linux app from the same commit for every pull request (as workflow artifacts) and publishes both as a GitHub Release when a new version reaches `main`. Local builds go to `release/` (ignored by git).

Windows (writes `release\windows\ForeverAI-Windows\`, with `-Package` also the release zip):

```powershell
python -m venv companion\.venv
companion\.venv\Scripts\python.exe -m pip install -r companion\requirements.txt pyinstaller -c companion\constraints.txt
.\build_exe.ps1 -Package
```

Linux (writes `release/linux/ForeverAI-Linux/`, with `--package` also the release archive; it runs on distributions with the same or a newer glibc than the build machine):

```bash
python3 -m venv companion/.venv
companion/.venv/bin/python -m pip install -r companion/requirements.txt pyinstaller -c companion/constraints.txt
./build_linux.sh --package
```

The starter knowledge base `seed/kb.sqlite` is bundled into both apps (your own `companion/data/kb.sqlite` is used instead when it exists).

### Publishing a release

1. Set the new version in `addon/ForeverAI/ForeverAI.toc`, `ns.version` in `addon/ForeverAI/Core.lua`, `companion/foreverai/__init__.py` and `companion/pyproject.toml` (a test checks that they agree).
2. Merge to `main`. The workflow builds both apps and, since this version has no release yet, publishes the release `v<version>` (it creates the tag) with both files and `SHA256SUMS.txt`.

A push to `main` with a version that is already released only builds the apps (workflow artifacts). Pushing a tag `v<version>` that matches the version in the source publishes it as well.

## Uninstall

Delete the ForeverAI folder, your data and `ForeverAI` plus `ForeverAI_Inbox*` in `World of Warcraft/_forever_/Interface/AddOns`. Data: `%APPDATA%\ForeverAI` on Windows, `~/.local/share/foreverai` on Linux (`companion/data` when run from a git checkout). On Linux also delete `~/.local/share/applications/foreverai.desktop` and `ForeverAI.desktop` on the desktop.
