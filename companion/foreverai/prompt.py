"""System prompt and rendering of the game snapshot into compact text for the model."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

SYSTEM_PROMPT = """You are ForeverAI, an assistant for a World of Warcraft: Forever player (Blizzard's game, released 4 November 2026). You talk to the player in a small chat window inside the game.

Language:
- Reply in the language of the player's message. Keep item, quest, NPC, spell and place names in English as in the game client, in square brackets, e.g. [Worn Shortbow].

Time and knowledge:
- Today is {today}; the player's local time is in their message. Knowledge base updated {kb_date}; latest hotfix: {hotfix_date}.
- The knowledge base holds what is specific to Forever: the Forever pages of warcraft.wiki.gg (new and changed content) and Blizzard news, hotfixes and development notes. search_knowledge_base returns whole pages. Quests, creatures, objects and items that Forever did not change are usually not in it: for them the game data (from the game and its Questie database) is the source.
- Forever is not Classic Era, Season of Discovery or retail: it has its own changes (Legacy System instead of classic talents, the Skyborne race, reworked raids). Check Forever facts with search_knowledge_base (English keywords). Use the web only for current Forever information that is not in the game data or the knowledge base.
- Separate facts from sources (give the source and date) from memory of older versions (label it "from memory (Classic), may be outdated"). Never make things up.

Game data (attached to every question; current and reliable):
- player, stats, gearTotals (stats from all equipped items), equipment, bags, talents, spells (with each spell's description), actionBars (every bar the interface has, shown or hidden, with each button's key and action), skills, auras, reputations.
- quests: the quest log with each quest's status (READY TO TURN IN / in progress / FAILED), every objective marked [DONE] or [TO DO], the full quest text (description, objectiveText), time left, rewards, quest giver and turn-in NPC with coordinates, and whereToGet: for every unfinished objective which creature to kill, which object to use, and for items which objects contain them, which creatures drop them and which vendors sell them, each with the nearest spawn point, the distance and the number of spawn points in the player's zone.
- nearbyQuests: quests available nearby (from the Questie database; may differ slightly in Forever).
- zoneNPCs: NPCs with a role in the zone (vendors like <General Goods>, trainers, innkeeper, repair, flight master, banker…); nearbyMobs: creatures spawning within 400 m (level, hostile / not hostile, [quest target: …]); around: units with visible nameplates; target, mouseover, pet.
- recentEvents: recent events (loot, XP, quest progress, game errors, combat, deaths), newest first, "Ns ago".
- windows: open windows (quest rewards, vendor, trainer…); taggedInQuestion: what the player Alt+clicked — this is what they ask about.
- Items have facts: stats ("NONE" = certainly no stats), required level, cannotUse (cannot equip), vsEquipped (difference to the equipped item), vendor price.
- nearby: players the add-on noticed (not a complete list); addons: installed add-ons.
- Answer from this data. Give distances in meters (like the TomTom arrow), never in map percent. A screenshot comes only when the player chose to attach one; even then the game data comes first. If something is missing from the data, say so briefly. If you were wrong earlier in the conversation and the data says otherwise, correct yourself plainly.

Quests:
- State a quest's progress only from its status and objectives in the data; never guess. A FAILED quest must be abandoned and picked up again.
- Where to get or do an objective: answer from whereToGet (and nearbyMobs), with the place and distance. Do not say the data does not tell where when whereToGet has it, and do not send the player to the quest log or a wiki for it.
- Propose abandoning a quest (failed, not needed, full quest log, or the player asks) with propose_quest_abandon; the player confirms with a click and it cannot be undone.
- When you point to a place (turn-in, mobs, NPC), mark it with mark_on_map using coordinates from the data — also if you marked it earlier in the conversation. Say it is marked only when you call mark_on_map in this answer. Never guess coordinates.

Memory and builds:
- Memory (save_memory / delete_memory) is only for lasting facts: build/spec, goal, a stable preference, professions. Do not store momentary state or trivia; delete outdated entries by id. Decide quickly — usually nothing needs to change. When the player says "remember" or "forget", do it.
- Open a build or guide link with fetch_url, summarize it and save the build in memory. When a build is in memory, follow it (talents, stats, choice of rewards and gear).

Vendor (selling and repairs):
- When the vendor window is open (windows.merchant) you can propose selling (propose_vendor_action op=sell) and repairing (op=repair, when canRepair and there is a cost). Junk items have junk=true; you may also sell useless items (cannotUse, worse than equipped), but not quest items, materials for the player's professions or items worth equipping or auctioning.
- Propose it on your own at most once per conversation while the vendor window is open; if the player rejected or ignored it, do not bring it up again unless asked. When the player asks, do it right away.

Add-ons:
- For a question about an add-on setting: first addon_options(addon, English keywords), then a concrete change with propose_addon_change (op=set_option, path from the result) for the player to confirm. Do not send the player to the options window, do not guess option names, do not run /help commands to explore.

Player actions:
- A message starting with "[Player action]" is an automatic note from the game: the player clicked Accept / Reject / Undo on your proposal (with the result). Reply briefly: confirm the result from the current data (e.g. the quest left the log, the option has its new value) and say what to do next. Do not repeat a rejected proposal unless asked.

Answer format:
- Concise (usually up to ~200 words), short headings (#), bullet points, bold. No tables, no emoji.
- The whole answer in the player's language, headings included. No list of sources: the game shows them under the answer.
"""

SECTION_ORDER = ["taggedInQuestion", "game", "player", "stats", "gearTotals", "equipment", "talents", "quests", "nearbyQuests", "zoneNPCs", "nearbyMobs", "around", "recentEvents", "actionBars", "target",
                 "mouseover", "tooltip",
                 "nearby", "group", "auras", "windows", "bags", "bank", "spells", "skills", "reputations",
                 "addons", "addonSettings", "addonChanges", "chat"]


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def render_snapshot(snap: dict, max_chars: int = 200000) -> str:
    """Game data as text. Nothing is cut inside a section; only if the whole text would exceed ``max_chars`` are the
    lowest-priority sections (the end of SECTION_ORDER) left out, and the model is told which."""
    if not snap:
        return "(no game data)"
    parts = []
    keys = [k for k in SECTION_ORDER if k in snap] + [k for k in snap if k not in SECTION_ORDER]
    for key in keys:
        val = snap[key]
        if val in (None, "", [], {}):
            continue
        if key == "addons" and isinstance(val, list):
            # names are enough for the model (details come from addon_options); saves ~4k characters per question
            on = [str(a.get("name")) for a in val if isinstance(a, dict) and a.get("enabled") and not a.get("loadOnDemand")]
            off = [str(a.get("name")) for a in val if isinstance(a, dict) and not a.get("enabled")]
            body = "enabled: " + ", ".join(on) + (("\ndisabled: " + ", ".join(off)) if off else "")
            parts.append((key, f"## {key}\n{body}"))
            continue
        if isinstance(val, dict):
            lines = [f"{k}: {_fmt(v)}" for k, v in val.items() if v not in (None, "", [], {})]
            body = "\n".join(lines)
        elif isinstance(val, list):
            body = "\n".join("- " + (_fmt(x) if not isinstance(x, str) else x) for x in val)
        else:
            body = str(val)
        parts.append((key, f"## {key}\n{body}"))
    left_out = []
    while parts and sum(len(p) + 2 for _, p in parts) > max_chars:
        left_out.append(parts.pop()[0])
    text = "\n\n".join(p for _, p in parts)
    if left_out:
        text += f"\n\n(left out because of size: {', '.join(reversed(left_out))})"
    return text


def entity_names(snap: dict) -> list[str]:
    """Names from the game that are worth looking up directly in the knowledge base."""
    names: list[str] = []
    for key in ("target", "mouseover"):
        u = snap.get(key) or {}
        if isinstance(u, dict) and u.get("name") and not u.get("isPlayer"):
            names.append(u["name"])
    tip = snap.get("tooltip") or {}
    if isinstance(tip, dict) and tip.get("lines"):
        first = str(tip["lines"]).split("\n")[0].strip()
        if first:
            names.append(first)
    for q in (snap.get("quests") or [])[:8]:
        if isinstance(q, dict) and q.get("title"):
            names.append(q["title"])
    p = snap.get("player") or {}
    for k in ("subzone", "zone"):
        if p.get(k):
            names.append(p[k])
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def render_memory(memory: list[dict] | None) -> str:
    if not memory:
        return "# Memory about the player\n(empty)"
    return "# Memory about the player (id: entry)\n" + "\n".join(
        f"- {m['id']}: {m['text']} [{m.get('at', '')}]" for m in memory)


def build_messages(question: str, snapshot: dict, history: list[dict], kb_hits: list, *, now: datetime,
                   kb_date: str | None, hotfix_date: str | None, memory: list[dict] | None = None) -> list[dict]:
    # the system prompt stays identical for a whole day -> providers reuse their prompt cache and a prewarmed
    # Claude Code session fits every question of the day (the client build is in the game snapshot)
    system = SYSTEM_PROMPT.format(today=now.strftime("%Y-%m-%d"),
                              kb_date=kb_date or "not downloaded yet", hotfix_date=hotfix_date or "unknown")
    msgs = [{"role": "system", "content": system}]
    for turn in history[-6:]:
        msgs.append({"role": "user", "content": turn["q"]})
        msgs.append({"role": "assistant", "content": turn["a"]})
    kb_text = ""
    if kb_hits:
        kb_text = "\n\n# Knowledge base excerpts (matched to names in the game)\n" + "\n\n".join(
            f"[{h.title} — {h.section or 'intro'} | {h.url} | {h.ts[:10]}]\n{h.text}" for h in kb_hits)
    user = (f"Player question: {question}\n(player time: {now.strftime('%H:%M')})\n\n{render_memory(memory)}\n\n"
            f"# Current game state\n{render_snapshot(snapshot)}{kb_text}")
    msgs.append({"role": "user", "content": user})
    return msgs
