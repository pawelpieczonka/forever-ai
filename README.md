# ForeverAI

AI assistant in a chat window inside World of Warcraft: Forever. It answers using your character, quests, gear and surroundings, and a local Forever knowledge base. Answers come from Claude Code (Claude subscription) or OpenRouter.

## Requirements

- Windows 10/11, WoW Forever
- Claude Code signed in, or an OpenRouter API key
- Recommended: Questie, TomTom

## Install and run

1. Copy `release/ForeverAI` anywhere and run `ForeverAI.exe`. It finds the game and installs the add-on.
2. In the game type `/reload`, then click the **AI** button or type `/fai`.

Keep `ForeverAI.exe` running while you play. OpenRouter key: in the game, **Settings → OpenRouter key**.

## Usage

- **Alt+click** an item, spell or quest to add it to your question.
- Changes in the game (add-on options, selling, repairing, abandoning a quest) happen only after you click **Accept**.
- **Settings** tab: AI provider, model, thinking level, web search, which game data is sent.
- Commands: `/fai`, `/fai <question>`, `/fai new`, `/fai debug`.

## Build from source

```powershell
python -m venv companion\.venv
companion\.venv\Scripts\python.exe -m pip install -r companion\requirements.txt pyinstaller -c companion\constraints.txt
.\build_exe.ps1
```

Tests: `companion\.venv\Scripts\python.exe -m pytest -q` (in `companion`).

## Uninstall

Delete the ForeverAI folder, `%APPDATA%\ForeverAI`, and `ForeverAI` plus `ForeverAI_Inbox*` in `World of Warcraft\_forever_\Interface\AddOns`.
