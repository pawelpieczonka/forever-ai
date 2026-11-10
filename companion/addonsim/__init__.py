"""The add-on simulator: runs the ForeverAI add-on (addon/ForeverAI, Lua) outside the game, on an emulated WoW client,
connected to the real ForeverAI app, and takes pictures of its window. A development tool: it is not part of the apps.

    python -m addonsim -s open -s "ask Where do I turn in my quest?" -s wait-answer -s "shot chat"

Python (tests): Sim (the game client, sim.py), App (the ForeverAI app in this process, app.py)."""

from .app import App
from .sim import Sim, SimError, install_game, make_game_dir

__all__ = ["App", "Sim", "SimError", "install_game", "make_game_dir"]
