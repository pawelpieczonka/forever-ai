"""Model arena: same questions + same game snapshot for several models -> HTML report.

Usage: python -m foreverai.arena [--models a,b,c] [--search never|auto|always]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import time
import webbrowser
from datetime import datetime

from . import config
from .kb.store import KB
from .kb.sync import kb_path
from .openrouter import OpenRouter
from .prompt import build_messages
from .tools import TOOLS, run_kb_search

QUESTIONS = [
    {"q": "When do the first raids open in WoW Forever and which raids are they?", "expect": ["9", "Dec", "Barrow"]},
    {"q": "What is the Skyborne race and how do I unlock it?", "expect": ["Skyborne", "Pack"]},
    {"q": "What did the 6 November 2026 hotfix change for druids?", "expect": ["Bloom"]},
    {"q": "What is the maximum character level in WoW Forever?", "expect": ["60"]},
    {"q": "What should I do next with my quest and where do I turn it in?", "expect": []},
    {"q": "Which of my items should I upgrade first?", "expect": []},
    {"q": "Which stats matter most for my class at this level?", "expect": []},
    {"q": "How do I get the Gryphon of Eternal Moonlight mount in WoW Forever?", "expect": ["not"], "trap": True},
]


def run(models: list[str], search: str) -> str:
    key = config.read_api_key()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is missing in .env")
    client = OpenRouter(key)
    kb = KB(kb_path())
    last = config.data_dir() / "last_question.json"
    snapshot = json.loads(last.read_text(encoding="utf-8")).get("snapshot", {}) if last.exists() else {}
    rows = []
    for item in QUESTIONS:
        for model in models:
            msgs = build_messages(item["q"], snapshot, [], [], now=datetime.now(), kb_date=kb.meta("kb_date"),
                                  hotfix_date=kb.meta("hotfix_date"))
            text, cost, t0, first = "", 0.0, time.monotonic(), None
            for _ in range(4):
                calls = None
                for kind, val in client.chat_stream(model, msgs, TOOLS, search, 900):
                    if kind == "text":
                        first = first or time.monotonic() - t0
                        text += val
                    elif kind == "usage":
                        cost += float(val.get("cost") or 0)
                    elif kind == "tool_calls":
                        calls = val
                    elif kind == "error":
                        text += f"\n[ERROR] {val}"
                if not calls:
                    break
                msgs.append({"role": "assistant", "content": None, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]})
                for c in calls:
                    res = run_kb_search(kb, c.args().get("query", ""))[0] if c.name == "search_knowledge_base" else "n/a"
                    msgs.append({"role": "tool", "tool_call_id": c.id, "content": res})
            hit = all(e.lower() in text.lower() for e in item["expect"]) if item["expect"] else None
            rows.append({"q": item["q"], "model": model, "text": text, "cost": cost, "secs": time.monotonic() - t0,
                         "first": first, "hit": hit})
            print(f"{model:40s} {cost:.5f}$ {time.monotonic() - t0:5.1f}s  {item['q'][:50]}")
    out = config.data_dir() / f"arena-{datetime.now():%Y%m%d-%H%M}.html"
    parts = ["<html><meta charset='utf-8'><title>ForeverAI arena</title><style>body{font-family:sans-serif;margin:20px}"
             "td{vertical-align:top;border:1px solid #ccc;padding:6px;max-width:420px;white-space:pre-wrap;font-size:13px}"
             ".ok{background:#e6ffe6}.bad{background:#ffe6e6}</style><h1>ForeverAI model arena</h1>"]
    summary = {}
    for r in rows:
        s = summary.setdefault(r["model"], {"cost": 0, "secs": 0, "hits": 0, "checks": 0})
        s["cost"] += r["cost"]
        s["secs"] += r["secs"]
        if r["hit"] is not None:
            s["checks"] += 1
            s["hits"] += int(r["hit"])
    parts.append("<table><tr><th>Model</th><th>Facts OK</th><th>Cost</th><th>Time</th></tr>")
    for m, s in summary.items():
        parts.append(f"<tr><td>{html.escape(m)}</td><td>{s['hits']}/{s['checks']}</td><td>${s['cost']:.4f}</td>"
                     f"<td>{s['secs']:.0f}s</td></tr>")
    parts.append("</table>")
    for item in QUESTIONS:
        parts.append(f"<h3>{html.escape(item['q'])}</h3><table><tr>")
        for r in [r for r in rows if r["q"] == item["q"]]:
            cls = "" if r["hit"] is None else ("ok" if r["hit"] else "bad")
            parts.append(f"<td class='{cls}'><b>{html.escape(r['model'])}</b><br>${r['cost']:.5f} · "
                         f"{r['secs']:.1f}s<br><br>{html.escape(r['text'])}<br><br>Rating (1-5): ____</td>")
        parts.append("</tr></table>")
    out.write_text("".join(parts), encoding="utf-8")
    return str(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(config.FAVORITE_MODELS))
    ap.add_argument("--search", default="never")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    path = run([m.strip() for m in a.models.split(",") if m.strip()], a.search)
    print("Report:", path)
    if os.name == "nt" and not a.no_open:
        webbrowser.open(path)


if __name__ == "__main__":
    main()
