"""Scenarios: what the simulated player does, one step per line. Empty lines and lines starting with # are skipped."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .sim import Sim, SimError

STEPS = """\
open                      open the ForeverAI window (/fai) when it is closed
tab NAME                  click a tab (Chat, History, Memory, Settings)
click LABEL [#N]          click a button, check box or text field by its text (#N: the N-th of several)
rclick LABEL [#N]         the same with the right mouse button
hover LABEL [#N]          move the mouse over it (its tooltip shows); leave: move it away
type TEXT                 type into the focused text field (else the first visible one)
key KEY                   ENTER, ESCAPE, TAB, UP, ...
ask TEXT                  type a question into the chat and press Enter
wait SECONDS              let time pass
wait-answer [SECONDS]     until the answer is complete (default 90)
reply TEXT|JSON           the scripted AI's next answer; JSON: {"text": "...", "tools": [{"name": "...", "args": {}}]}
shot NAME [AREA]          save NAME.png; AREA: window (default: the window and a tooltip over it), screen, a frame name
dump [NAME]               save the visible texts as NAME.txt (and put them in the report)
expect TEXT               a visible text contains TEXT (else the run fails); expect-not TEXT: none does
resize W H                size of the ForeverAI window
scroll DELTA [#N]         the mouse wheel over a scroll area (positive: up)
slash /COMMAND            a slash command: /fai status, /fai new, /reload, ...
reload                    /reload: saved variables are written and the UI loads again
lua CODE                  run Lua in the game; its result goes to the report"""


def parse(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def _nth(arg: str) -> tuple[str, int]:
    m = re.fullmatch(r"(.*?)\s+#(\d+)", arg)
    return (m.group(1), int(m.group(2))) if m else (arg, 1)


def _file_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-") or "shot"


class Runner:
    def __init__(self, sim: Sim, out: Path, app=None):
        self.sim, self.out, self.app = sim, Path(out), app
        self.screenshots: list[str] = []
        self.dumps: dict[str, list[str]] = {}
        self.results: list = []
        self.done: list[str] = []

    def run(self, steps: list[str]) -> str | None:
        """Runs the steps; returns why the run failed, or None."""
        for i, step in enumerate(steps, 1):
            try:
                self.step(step)
            except (SimError, ValueError) as e:
                return f"step {i} ({step}): {e}"
            self.done.append(step)
        return None

    def step(self, line: str) -> None:
        sim = self.sim
        cmd, _, arg = line.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd == "open":
            if sim.lua("return ForeverAIFrame ~= nil and ForeverAIFrame:IsShown()") is not True:
                sim.slash("/fai")
        elif cmd == "tab":
            sim.click(arg)
        elif cmd in ("click", "rclick"):
            label, nth = _nth(arg)
            sim.click(label, nth, "RightButton" if cmd == "rclick" else "LeftButton")
        elif cmd == "hover":
            sim.hover(*_nth(arg))
        elif cmd == "leave":
            sim.leave()
        elif cmd == "type":
            sim.type(arg)
        elif cmd == "key":
            sim.key(arg)
        elif cmd == "ask":
            sim.ask(arg)
        elif cmd == "wait":
            sim.run(float(arg or 1))
        elif cmd == "wait-answer":
            sim.wait_answer(float(arg or 90))
        elif cmd == "reply":
            if self.app is None:
                raise SimError("reply needs the app (the run is without it)")
            self.app.queue_reply(arg)
        elif cmd == "shot":
            name, _, area = arg.partition(" ")
            path = sim.screenshot(self.out / f"{_file_name(name or 'shot')}.png", area.strip() or "window")
            self.screenshots.append(str(path))
        elif cmd == "dump":
            name = _file_name(arg or f"texts-{len(self.dumps) + 1}")
            texts = sim.texts()
            (self.out / f"{name}.txt").write_text("\n".join(texts) + "\n", encoding="utf-8")
            self.dumps[name] = texts
        elif cmd in ("expect", "expect-not"):
            found = any(arg in t for t in sim.texts())
            if cmd == "expect" and not found:
                raise SimError(f"no visible text contains {arg!r}; visible: " + " | ".join(sim.texts())[:1500])
            if cmd == "expect-not" and found:
                raise SimError(f"a visible text contains {arg!r}")
        elif cmd == "resize":
            w, h = (float(x) for x in arg.split())
            sim.lua("ForeverAIFrame:SetSize(...)", w, h)
            sim.step(2)
        elif cmd == "scroll":
            delta, nth = _nth(arg)
            sim.wheel(float(delta), nth)
        elif cmd == "slash":
            sim.slash(arg)
        elif cmd == "reload":
            sim.reload()
            sim.step()
        elif cmd == "lua":
            res = sim.lua(arg)
            self.results.append({"lua": arg, "result": sim.to_py(res) if not isinstance(res, tuple)
                                 else [sim.to_py(x) for x in res]})
            sim.step()
        else:
            raise SimError(f"unknown step {cmd!r}; the steps are:\n{STEPS}")

    def report(self) -> dict:
        return {"steps": self.done, "screenshots": self.screenshots, "texts": self.dumps,
                "luaResults": self.results}


def write_report(out: Path, report: dict) -> Path:
    path = Path(out) / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
