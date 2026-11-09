# ForeverAI

AI assistant in a chat window inside World of Warcraft: Forever. It answers using your character, quests, gear and surroundings, and a local Forever knowledge base. Answers come from Claude Code (Claude subscription) or OpenRouter.

## Requirements

- Windows 10/11, or Linux with WoW Forever running under Wine (Lutris, Steam/Proton, Bottles, Heroic or plain Wine)
- Claude Code signed in, or an OpenRouter API key
- Recommended: Questie, TomTom

## Install and run

### Windows

1. Copy `release/ForeverAI` anywhere and run `ForeverAI.exe`. It finds the game and installs the add-on.
2. In the game type `/reload`, then click the **AI** button or type `/fai`.

Keep `ForeverAI.exe` running while you play. OpenRouter key: in the game, **Settings → OpenRouter key**.

### Linux

You need Python 3.12+ with Tk from your distribution: `sudo apt install python3-venv python3-tk` (Debian/Ubuntu), `sudo dnf install python3-tkinter` (Fedora) or `sudo pacman -S tk` (Arch). Then, in the repository:

```bash
python3 -m venv companion/.venv
companion/.venv/bin/python -m pip install -r companion/requirements.txt
cd companion && .venv/bin/python -m foreverai
```

1. The app finds the game in your Wine prefixes (`$WINEPREFIX`, Lutris, `~/.wine`, `~/Games`, Heroic, Bottles, Steam) or on mounted disks, and installs the add-on. If it does not, choose the `World of Warcraft` folder or pass `--game-dir`.
2. In the game type `/reload`, then click the **AI** button or type `/fai`.

**Desktop shortcut** adds ForeverAI to the application menu and to the desktop. Claude Code is found on `PATH`, in `~/.local/bin`, npm/nvm installs and the Claude desktop app. Use the distribution's Python, not a standalone one (uv, python-build-standalone): their Tk has no font rendering, so the window shows bitmap fonts.

## Usage

- **Alt+click** an item, spell or quest to add it to your question.
- Changes in the game (add-on options, selling, repairing, abandoning a quest) happen only after you click **Accept**.
- **Settings** tab: AI provider, model, thinking level, web search, which game data is sent.
- Commands: `/fai`, `/fai <question>`, `/fai new`, `/fai debug`.

## Build from source

Windows:

```powershell
python -m venv companion\.venv
companion\.venv\Scripts\python.exe -m pip install -r companion\requirements.txt pyinstaller -c companion\constraints.txt
.\build_exe.ps1
```

Linux (builds `dist/linux/ForeverAI/ForeverAI`; it runs on distributions with the same or a newer glibc):

```bash
python3 -m venv companion/.venv
companion/.venv/bin/python -m pip install -r companion/requirements.txt pyinstaller -c companion/constraints.txt
./build_linux.sh
```

Tests: `companion\.venv\Scripts\python.exe -m pytest -q` on Windows, `.venv/bin/python -m pytest -q` on Linux (in `companion`).

## Uninstall

Delete the ForeverAI folder, your data and `ForeverAI` plus `ForeverAI_Inbox*` in `World of Warcraft/_forever_/Interface/AddOns`. Data: `%APPDATA%\ForeverAI` on Windows, `~/.local/share/foreverai` on Linux (`companion/data` when run from a git checkout). On Linux also delete `~/.local/share/applications/foreverai.desktop` and `ForeverAI.desktop` on the desktop.
