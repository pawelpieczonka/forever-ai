"""Run the scenarios (or the last recorded real questions) through the current system prompt and a real model.

    python -m evals.run                         every scenario, the model and thinking level set in the app
    python -m evals.run --only what_now,ok      some scenarios
    python -m evals.run --prompt-file old.txt   another system prompt (text with {today} {kb_date} {hotfix_date})
    python -m evals.run --replay 10             the last 10 questions the app recorded (data/turns), old answer beside new

Needs Claude Code signed in. Nothing touches the app's own data: the run works in a scratch folder with a copy of the
knowledge base. The report (Markdown and JSON) goes to companion/data/evals/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import checks as c
from .engine import run_scenario
from .scenarios import SCENARIOS, Scenario, Turn


def replay_scenarios(records: list[dict]) -> list[Scenario]:
    """Recorded questions as one-turn scenarios (no checks: the old answer is shown beside the new one)."""
    out = []
    for n, r in enumerate(records, 1):
        sc = Scenario(f"replay_{n:02d}", f"recorded {r.get('at')} · {r.get('model')} · {r.get('secs')} s",
                      [Turn(r["question"], [], full=r.get("snapshot") or {},
                            memory=[m["text"] for m in r.get("memory") or []])],
                      history=r.get("history") or [])
        sc.before = {"answer": r.get("answer"), "tools": r.get("tools") or [], "secs": r.get("secs")}
        out.append(sc)
    return out


def tool_line(tools: list[dict]) -> str:
    return ", ".join(f"{t['name'].split('__')[-1]}({json.dumps(t['args'], ensure_ascii=False)[:90]})" for t in tools)


def report(results: list[dict], label: str, model: str, effort: str, before: dict[str, dict]) -> str:
    turns = [t for r in results for t in r["turns"]]
    secs = [t["secs"] for t in turns] or [0]
    passed, failed = sum(r["passed"] for r in results), sum(r["failed"] for r in results)
    lines = [f"# {label}: {passed} of {passed + failed} checks passed",
             f"{model}, thinking {effort} · {len(turns)} answers · median {statistics.median(secs):.1f} s, "
             f"slowest {max(secs):.1f} s · {sum(c.words(t['answer']) for t in turns)} words in all", ""]
    for r in results:
        lines += [f"## {r['name']} — {'ok' if not r['failed'] else str(r['failed']) + ' FAILED'}", f"_{r['why']}_", ""]
        for t in r["turns"]:
            lines += [f"**Player:** {t['question']}", "",
                      f"**Answer** ({t['secs']} s, {c.words(t['answer'])} words"
                      + (f"; tools: {tool_line(t['tools'])}" if t["tools"] else "") + "):", "",
                      "\n".join("> " + ln for ln in t["answer"].splitlines()) or "> (empty)", ""]
            old = before.get(r["name"])
            if old:
                lines += [f"**Recorded answer** ({old['secs']} s, {c.words(old['answer'] or '')} words"
                          + (f"; tools: {tool_line(old['tools'])}" if old["tools"] else "") + "):", "",
                          "\n".join("> " + ln for ln in (old["answer"] or "").splitlines()), ""]
            lines += [f"- {'PASS' if k.ok else 'FAIL'} {k.label}" + (f" ({k.detail})" if k.detail else "")
                      for k in t["checks"]] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="scenario names, comma separated")
    ap.add_argument("--replay", type=int, metavar="N", help="the last N recorded questions instead of the scenarios")
    ap.add_argument("--prompt-file", help="a system prompt to use instead of the current one")
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--label", default="run")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--search", default="never", choices=["auto", "always", "never"], help="web search (default never)")
    args = ap.parse_args(argv)

    from foreverai import config, prompt
    real = config.data_dir()
    settings = config.SettingsStore(real / "settings.json").settings
    model, effort = args.model or settings.claude_model, args.effort or settings.claude_effort
    scenarios = SCENARIOS
    if args.replay:
        from foreverai.turnlog import TurnLog
        scenarios = replay_scenarios(TurnLog(real / "turns").load()[-args.replay:])
    if args.only:
        scenarios = [s for s in scenarios if s.name in args.only.split(",")]
    if not scenarios:
        print("nothing to run")
        return 1
    if args.prompt_file:
        prompt.SYSTEM_PROMPT = Path(args.prompt_file).read_text(encoding="utf-8")
    scratch = Path(tempfile.mkdtemp(prefix="foreverai-eval-"))
    os.environ["FOREVERAI_DATA"] = str(scratch)
    kb_file = next((p for p in (real / "kb.sqlite", config.REPO_ROOT / "seed" / "kb.sqlite") if p.exists()), None)

    def one(sc: Scenario) -> dict:
        kb = None
        if kb_file:
            from foreverai.kb.store import KB
            shutil.copy(kb_file, scratch / f"kb-{sc.name}.sqlite")
            kb = KB(scratch / f"kb-{sc.name}.sqlite")
        res = run_scenario(sc, model=model, effort=effort, search_mode=args.search, kb=kb)
        print(f"{'ok  ' if not res['failed'] else 'FAIL'} {sc.name}: {res['passed']} passed, {res['failed']} failed, "
              + ", ".join(f"{t['secs']} s" for t in res["turns"]), flush=True)
        return res

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(one, scenarios))
    before = {s.name: s.before for s in scenarios if getattr(s, "before", None)}
    text = report(results, args.label, model, effort, before)
    out_dir = real / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}-{args.label}"
    (out_dir / f"{stem}.md").write_text(text, encoding="utf-8")
    plain = [{**r, "turns": [{**t, "checks": [vars(k) for k in t["checks"]]} for t in r["turns"]]} for r in results]
    (out_dir / f"{stem}.json").write_text(json.dumps(plain, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text.split("\n")[0][2:], "·", text.split("\n")[1])
    print(out_dir / f"{stem}.md")
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
