"""python -m addonsim: runs the add-on in the simulator, plays a scenario and saves pictures and a report."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from .scenario import STEPS, Runner, parse, write_report
from .sim import Sim, SimError, install_game, make_game_dir, parse_clock

DEFAULT_STEPS = ["open", "shot window"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m addonsim",
        description="Runs the ForeverAI add-on outside the game: the real Lua code on an emulated WoW client, "
                    "connected to the real ForeverAI app (in this process, with its own data folder and a scripted "
                    "AI). Plays the steps, saves pictures and texts, and prints report.json.",
        epilog="steps:\n" + STEPS, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", help="a file with one step per line ('-': standard input)")
    ap.add_argument("-s", "--step", action="append", default=[], help="a step (repeatable; after the file's steps)")
    ap.add_argument("-o", "--out", help="folder for the pictures, texts and report.json (default: a new temporary one)")
    ap.add_argument("--addon", help="the add-on folder to run (default: addon/ForeverAI of this checkout)")
    ap.add_argument("--no-app", action="store_true",
                    help="without the ForeverAI app: nothing answers, and game time runs as fast as it can")
    ap.add_argument("--ai", choices=("scripted", "live"), default="scripted",
                    help="scripted (default): replies from --reply / reply steps; live: the real provider (costs)")
    ap.add_argument("--reply", action="append", default=[], help="a scripted answer (repeatable, used in order)")
    ap.add_argument("--screen", default="1920x1080", help="physical screen size (default 1920x1080)")
    ap.add_argument("--clock", help="the game clock at the start, YYYY-MM-DD HH:MM (default: now)")
    ap.add_argument("--game-dir", help="stand-in game folder to use and keep (saved variables stay between runs)")
    ap.add_argument("--data-dir", help="the app's data folder to use and keep")
    ap.add_argument("--keep", action="store_true", help="keep the temporary game and data folders")
    ap.add_argument("-v", "--verbose", action="store_true", help="show the simulator's and the app's log on stderr")
    args = ap.parse_args(argv)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO if args.verbose else logging.WARNING)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger("addonsim").setLevel(logging.INFO)

    steps: list[str] = []
    if args.scenario:
        text = sys.stdin.read() if args.scenario == "-" else Path(args.scenario).read_text(encoding="utf-8")
        steps += parse(text)
    steps += [s.strip() for s in args.step if s.strip()]
    steps = steps or DEFAULT_STEPS
    try:
        width, height = (int(x) for x in args.screen.lower().split("x"))
        clock = parse_clock(args.clock) if args.clock else None
    except ValueError as e:
        ap.error(str(e))

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="addonsim-"))
    out.mkdir(parents=True, exist_ok=True)
    temp = []
    game = Path(args.game_dir) if args.game_dir else Path(tempfile.mkdtemp(prefix="addonsim-game-"))
    data = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="addonsim-data-"))
    if not args.game_dir:
        temp.append(game)
    if not args.data_dir:
        temp.append(data)
    make_game_dir(game)
    addon = Path(args.addon).resolve() if args.addon else None

    app = None
    failed = None
    sim = Sim(game, screen=(width, height), realtime=not args.no_app, clock=clock)
    runner = Runner(sim, out)
    try:
        if args.no_app:
            install_game(game, addon)
        else:
            from .app import App
            app = App(game, data, ai=args.ai, replies=args.reply, addon_dir=addon).start()
            runner.app = app
        sim.start()
        failed = runner.run(steps)
    except (SimError, OSError, RuntimeError) as e:
        failed = f"{type(e).__name__}: {e}"
    finally:
        try:
            sim.close()
        except Exception as e:  # the report still goes out
            failed = failed or f"closing the game: {e}"
        if app:
            app.stop()

    report = {"ok": failed is None and not sim.errors, "failed": failed, "out": str(out), **runner.report(),
              "sim": sim.report(), "app": app.report() if app else None,
              "gameDir": str(game) if (args.keep or args.game_dir) else None,
              "dataDir": str(data) if (args.keep or args.data_dir) and app else None}
    if sim.errors and not failed:
        report["failed"] = f"{len(sim.errors)} Lua error(s) in the add-on (see sim.errors)"
    write_report(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.keep:
        for d in temp:
            shutil.rmtree(d, ignore_errors=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
