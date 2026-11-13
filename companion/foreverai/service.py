"""Handles messages coming from the game and sends replies through the inbox pool."""

from __future__ import annotations

import itertools
import json
import logging
import re
import secrets
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import claude_code, config, gemini, keystore
from .frames import Frame, FrameAssembler
from .inbox import encode_inbox, pool_size, write_inbox
from .kb.store import KB
from .memory import ConversationStore, MemoryStore
from .gemini import Gemini
from .openrouter import FREE_MODELS, FREE_ROUTER, OpenRouter, is_free
from .prompt import NOTES_SYSTEM, build_messages, entity_names, notes_prompt
from .mapnotes import MapNotes, format_map_notes
from .petskills import PetSkills, format_pet_abilities
from .tools import (TOOLS, auction_request, format_addon_options, format_auction, format_gear_upgrades, format_sell_prices,
                    format_zone_quests, next_action_id, remember_options, run_fetch, run_kb_search, sell_price_request,
                    validate_abandon, validate_mark, validate_proposal, validate_vendor)
from .wowformat import bracket_names, md_to_wow

log = logging.getLogger("foreverai")
activity = logging.getLogger("foreverai.activity")  # short English lines shown in the app window

_REQUEST_NAMES = {"status": "status refresh", "memory_add": "memory: add", "memory_delete": "memory: delete",
                  "memory_clear": "memory: clear", "memory_list": "memory: list"}
# AI settings sent by an add-on older than 0.6.0 (still loaded in a game that was not reloaded since the update)
_MOVED_SETTINGS = ("set_model", "set_provider", "set_effort", "set_search_mode", "set_api_key", "set_gemini_key",
                   "dismiss_claude")
SEARCH_MODES = {"auto": "auto", "always": "always", "never": "off"}

FIRST_FLUSH_S = 1.0
FLUSH_EVERY_S = 1.5
VENDOR_PRICES_S = 10.0  # how long a vendor sale waits for the auction prices of its items (a lookup in price data)
RETRY_TEXT_S = 1.0      # the addon reads the next inbox this soon while text is streaming: the end is near
RETRY_THINKING_S = 3.0  # ... and at most this late while the model is thinking or using tools (the add-on looks
                        # sooner by itself in the first half minute of a question: Transport.lua, pace)
# a Gemini model that could not answer is skipped (the fallback answers) until a probe in the background, at most this
# often, finds that it can again
GEMINI_RECHECK_S = 120
MAX_TOOL_ROUNDS = 5
# OpenRouter's web search is paid per search, also with a free model: with one it is used only while the balance covers
# a few searches, since a balance below zero stops free models too
FREE_SEARCH_MIN_BALANCE = 0.10
EFFORT_NAMES = {"low": "low", "medium": "medium", "high": "high", "xhigh": "very high", "max": "max"}
PROVIDER_NAMES = {"openrouter": "OpenRouter", "gemini": "Google Gemini", "claude_code": "Claude Code"}
SCREEN_NOTE = ("The player attached the current screen as an image (top-left corner blacked out). Use it for what "
               "the game data does not contain.")
# A Claude session keeps every question with its game data (~30,000 characters each) and every tool result (a
# knowledge-base search returns up to 45,000): after this many turns or characters a fresh session takes over, with
# the last turns inline, before the model's context fills up and Claude Code stops to compact it.
MAX_SESSION_TURNS = 14
MAX_SESSION_CHARS = 350_000  # ~90k tokens of questions and tool results; answers and thinking come on top
MAX_MARKS = 12  # marks one answer can set by hand
MAX_ROUTE_MARKS = 20  # ... and the stops of a planned route: one through several zones is long, and loses its end otherwise
MAX_TRACKED = 5  # creatures the add-on watches for at once
# a knowledge-base search that found nothing, when the model can search the web: the next step, at the moment it
# matters (a model that read "not in the knowledge base" used to answer another question instead)
KB_MISS_WEB = (" When the answer depends on it (an event, a system, a change in Forever), search the web for it "
               "before you answer: \"it is not in the data\" is no answer while you can look it up, and never answer "
               "another question in place of the one asked.")


def model_name(model_id: str) -> str:
    """claude-haiku-5-5 -> Haiku 5.5 (OpenRouter ids are shown as they are)."""
    for m in claude_code.CLAUDE_MODELS:
        if m["id"] == model_id:
            return m["name"]
    return model_id


class InboxWriter:
    """Writes the latest reply state into the next ``lookahead`` inboxes.

    The game indexes addon files only when the UI loads, so there is no way to signal "ready" with new files.
    Instead the addon reads one inbox every ~2.5 s (each LoadOnDemand addon can be read once per UI session) and
    every inbox ahead of it always holds the newest state of the reply.
    """

    NEAR = 8  # inboxes a state is always written to, even with a newer one waiting

    # the addon reads one inbox every >=1 s (2-4 s in practice) for up to 300 s -> 200 covers the whole wait
    def __init__(self, addons_dir: Path, session: str, next_inbox: int, pool: int, lookahead: int = 200):
        self.addons_dir = addons_dir
        self.session = session
        self.base = int(next_inbox)
        self.pool = pool
        self.lookahead = lookahead
        self.seq = 0
        self.requests: list[dict] = []  # work the game should do for the model (e.g. read an add-on's options)
        self.last: dict | None = None
        self._lock = threading.Lock()
        self._asked = 0  # states handed to send() so far: one still being written sees that a newer one waits
        self._asked_lock = threading.Lock()

    def send(self, payload: dict) -> bool:
        last = min(self.pool, self.base + self.lookahead - 1)
        if self.base > last:
            log.warning("inbox pool exhausted (base=%s pool=%s)", self.base, self.pool)
            return False
        with self._asked_lock:
            self._asked += 1
        with self._lock:
            mine = self._asked
            self.seq += 1
            body = {"v": 1, "session": self.session, "seq": self.seq, **payload}
            if not payload.get("final"):
                self.last = dict(payload)
                if self.requests:
                    body["requests"] = list(self.requests)
                    body["retryAfter"] = 1.0  # the game should pick the request up quickly
            text = encode_inbox(body)  # once for all of them: only the inbox's number differs
            for n in range(self.base, last + 1):
                write_inbox(self.addons_dir, n, body, text)
                # Writing them all takes a few hundred milliseconds. A newer state that waits for the lock (a request
                # for the game, the next piece of text) writes every inbox again from the first: this one stops, so the
                # newer one is in front of the add-on sooner. A final state is always written out.
                if self._asked != mine and not body.get("final") and n - self.base >= self.NEAR:
                    break
        log.info("inbox %03d-%03d <- %s seq=%s final=%s len=%s req=%s", self.base, last, body.get("kind"), self.seq,
                 body.get("final"), len(body.get("text") or ""), len(body.get("requests") or []))
        if body.get("final") and body.get("kind") == "answer":
            activity.info("Answer · %s s · %s", body.get("secs", "?"), body.get("model") or "AI")
        elif body.get("kind") == "error":
            activity.warning("Error: %s", str(body.get("text") or "")[:140])
        return True

    def resend(self) -> None:
        """Publish the current (non-final) state again, e.g. after a game request was added."""
        if self.last is not None:
            self.send(dict(self.last))


# a line of the "marks" section: "2. Sentinel Arynia Cloudsbreak — Teldrassil 38.3, 34.4 (115 m)"
# what the app keeps of a quest the game offers (zone_quests): enough to plan all of it
_QUEST_KEYS = ("id", "title", "level", "minLevel", "type", "giver", "where", "objective", "whereToGet", "turnIn",
               "next", "nextId", "startsWhenTaken")
ZONE_QUESTS_KEEP_S = 300.0  # the quests of a zone read for a route are good for this long
ZONE_QUESTS_WAIT_S = 15.0  # how long a route waits for the game to list a zone's quests
ZONE_QUESTS_RETRY_S = 60.0  # after the game did not answer, it is not asked again for this long
def _quest_key(q: dict):
    """How a quest the game offers is known: its number (the parts of a chain, and some quests, share a title)."""
    return q["id"] if isinstance(q.get("id"), int) else str(q.get("title") or "").lower()


_MARK_ZONE = re.compile(r"^\d+\. .+? — (.+?) \d+(?:\.\d+)?, \d+(?:\.\d+)?(?: \((?:\d+ m|done)\))?$")
_MARK_LINE = re.compile(r"^\d+\. (.+) — .*? (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)(?: \((?:\d+ m|done)\))?$")


_MARK_AT = re.compile(r"^(.+?) (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)(?: \((?:\d+ m|done)\))?$")
EXTRA_TITLE = 56  # the title of a stop asked for: it is found again on the map by it, number and all
NOTES_AUTO_TURNS = 6  # messages of the add-on alone (route updates) after which the notes are brought up to date
NOTES_MAX_CHARS = 2500
FAR_M = 5000  # the add-on's way between two maps: a stop on another map is never "on the way"
# an answer that blames the tools ("narzędzia do planowania i znaczników nie działają", "the route tool does not work")
_NO_TOOLS = re.compile(r"\b(narzędzi\w*|tools?)\b[^.\n]{0,60}?\b(nie działa\w*|nie są dostępne|niedostępn\w*|"
                       r"do(?:es)? not work|doesn't work|don't work|not working|(?:are|is) not available|unavailable)",
                       re.I)


def _map_stops(snapshot: dict) -> list[dict]:
    """The stops of the route on the player's map, as the marks section lists them: title, x, y, zone, done."""
    out = []
    for line in snapshot.get("marks") or []:
        m = _MARK_LINE.match(str(line))
        if m:
            at = _MARK_AT.match(str(line).rsplit(" — ", 1)[-1])
            out.append({"title": m.group(1), "x": float(m.group(2)), "y": float(m.group(3)),
                        "zone": at.group(1) if at else "", "done": str(line).endswith("(done)")})
    return out


def _bare(stop: dict) -> dict:
    """A stop as the game is sent it."""
    return {"title": stop["title"], "x": stop["x"], "y": stop["y"]}


def _stop_tasks(snapshot: dict, stop: dict) -> list[dict]:
    """What a stop on the map is there for, as far as the quest log tells: the turn-in of a quest whose NPC stands
    there, an objective whose source is there (the marks section does not say)."""
    from .route import _PLACE, _SOURCE, split_line
    out = []

    def near(zone, x, y) -> bool:
        return (str(zone).lower() == str(stop.get("zone") or "").lower() or not stop.get("zone")) and \
            abs(float(x) - stop["x"]) <= 1.0 and abs(float(y) - stop["y"]) <= 1.0
    for q in snapshot.get("quests") or []:
        if not isinstance(q, dict) or not q.get("title"):
            continue
        for place in str(q.get("turnIn") or "").split(";"):
            m = _PLACE.match(place.strip())
            if m and near(m.group("zone"), m.group("x"), m.group("y")):
                out.append({"kind": "turn_in", "quest": str(q["title"])})
        for line in q.get("whereToGet") or []:
            for src in split_line(line)[1].split("; "):
                m = _SOURCE.match(src)
                if m and near(m.group("zone"), m.group("x"), m.group("y")):
                    out.append({"kind": "objective", "quest": str(q["title"])})
    return out


def _comes_before(a: dict, b: dict) -> bool:
    """a comes before b for their quests' sake (Map.lua comesBefore): a quest is picked up before the rest of it is
    done, and its objectives before it is turned in."""
    return any(t.get("quest") and t.get("quest") == u.get("quest")
               and ((t.get("kind") == "pick_up" and u.get("kind") != "pick_up")
                    or (t.get("kind") == "objective" and u.get("kind") == "turn_in"))
               for t in a.get("tasks") or [] for u in b.get("tasks") or [])


def _stop_key(stop: dict) -> tuple:
    return re.sub(r"^\d+[.)]\s*", "", stop["title"]).strip("[] ").lower(), round(stop["x"], 1), round(stop["y"], 1)


def _route_view(ctx: "TurnContext", stops: list[dict], added: list[dict], removed: set, first: list) -> list[dict]:
    """The route as the add-on will list it after this answer: the stops on the map without the ones taken off, the
    ones named in an order first, and every stop this answer adds where it costs the least way (the add-on's own rule,
    Map.lua bestSlot: it applies the order, puts the added stops in, then applies the order once more)."""
    from .route import MAP_ASPECT, _scale, _xy
    player = ctx.snapshot.get("player") or {}
    home = str(player.get("map") or player.get("zone") or "").lower()
    start = _xy(player.get("coords"))
    scale = _scale(ctx.snapshot, start, str(player.get("map") or player.get("zone") or "")) if start else None
    here = {"zone": home, "x": start[0], "y": start[1]} if start else None

    def zone(stop: dict) -> str:
        return str(stop.get("zone") or home).lower()

    def way(a: dict | None, b: dict) -> float:
        if not a or zone(a) != zone(b):
            return FAR_M
        sx, sy = scale if scale and zone(a) == home else (MAP_ASPECT[0] * 30, MAP_ASPECT[1] * 30)
        return (((a["x"] - b["x"]) * sx) ** 2 + ((a["y"] - b["y"]) * sy) ** 2) ** 0.5

    def slot(route: list[dict], e: dict) -> int:
        lo, hi = -1, len(route)  # it goes in before route[i], lo < i <= hi
        for i, st in enumerate(route):
            if not st.get("done"):
                if _comes_before(st, e):
                    lo = i
                if hi == len(route) and _comes_before(e, st):
                    hi = i
        if lo >= hi:
            lo = -1
        prev = here
        last = next((st for st in reversed(route) if not st.get("done")), None)
        at, best = hi, way(last or prev, e) if hi == len(route) else float("inf")
        for i, st in enumerate(route):
            if not st.get("done"):
                cost = way(e, st) + ((way(prev, e) - way(prev, st)) if prev else 0)
                if lo < i <= hi and cost < best:
                    at, best = i, cost
                prev = st
        return at

    def ordered(route: list[dict]) -> list[dict]:
        front = [next(st for st in route if _stop_key(st) == k) for k in first if any(_stop_key(st) == k for st in route)]
        return front + [st for st in route if st not in front]
    for st in stops:
        st.setdefault("tasks", _stop_tasks(ctx.snapshot, st))
    route = ordered([st for st in stops if _stop_key(st) not in removed])
    for e in added:
        if _stop_key(e) not in removed:
            route.insert(slot(route, e) if route else 0, e)
    return ordered(route)


def _extra_stops(value) -> list[dict]:
    """plan_route's `extra` as the planner takes it: the stops with a title and a place, at most ten."""
    out = []
    for e in value if isinstance(value, list) else []:
        try:
            x, y = float(e["x"]), float(e["y"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(e.get("title") or "").strip("[] ")[:EXTRA_TITLE].strip()  # a mark is "20. " and 60 characters
        if not title or not (0 <= x <= 100 and 0 <= y <= 100):
            continue
        out.append({"title": title, "zone": str(e.get("zone") or "").strip()[:60], "x": round(x, 1), "y": round(y, 1),
                    **({"note": str(e["note"]).strip()[:120]} if e.get("note") else {}),
                    **({"mob": str(e["mob"]).strip("[] ")[:60]} if e.get("mob") else {}),
                    **({"when": e["when"]} if e.get("when") in ("first", "last") else {}),
                    **({"tasks": e["tasks"]} if isinstance(e.get("tasks"), list) else {}),
                    **({"placed": True} if e.get("placed") else {})})
    return out[:10]


def propose_route(ctx: "TurnContext", action: dict) -> None:
    """A route change the model proposes by itself: one card per answer, the latest proposal."""
    ctx.actions[:] = [a for a in ctx.actions if a.get("op") not in ("route", "route_edit")]
    ctx.actions.append({"id": next_action_id(), "addon": "Map", **action})


def _already_marked(ctx: "TurnContext", mark: dict, add: bool) -> str | None:
    """Why a mark is not set: the place is on the route already (this answer's marks, or the map's when the mark is
    added to them). The same spot under any name, or the same name close by (\"Corithras Moonrage\" and \"Corithras
    Moonrage (Crown of the Earth)\")."""
    def bare(title: str) -> str:
        return re.sub(r"^\d+[.)]\s*", "", str(title)).strip()

    def name(title: str) -> str:
        return re.sub(r"\s*\([^)]*\)\s*$", "", bare(title)).strip("[] ").lower()

    def same(a: dict, b: dict) -> bool:
        za, zb = str(a.get("zone") or "").lower(), str(b.get("zone") or "").lower()
        if za and zb and za != zb:
            return False
        dx, dy = abs(a["x"] - b["x"]), abs(a["y"] - b["y"])
        return (dx <= 0.3 and dy <= 0.3) or (name(a["title"]) == name(b["title"]) and dx <= 1.0 and dy <= 1.0)

    def refused(where: str, n: int, other: dict) -> str:
        return (f"Not marked: {bare(mark['title'])} is already on {where} as stop {n} ({bare(other['title'])}). "
                "A place is one stop; say in the answer everything to do there.")

    for n, other in enumerate(ctx.marks, 1):
        if same(mark, other):
            return refused("the route", n, other)
    if add and (not ctx.marks or not ctx.replaces_marks):  # the map's route stays: this one joins it
        # a stop that is done is not in the way: marking its place again makes it a stop to do
        lines = [str(line) for line in ctx.snapshot.get("marks") or []]
        stops = [(n, {"title": m.group(1), "x": float(m.group(2)), "y": float(m.group(3))})
                 for n, (line, m) in enumerate(((line, _MARK_LINE.match(line)) for line in lines), 1)
                 if m and not line.endswith("(done)")]
        for n, other in stops:
            if same(mark, other):
                return refused("the map", n, other)
    return None


def _no_way_to(stops: list[dict], here: str) -> dict | None:
    """The first stop of a route that stands in another zone than the one the route is in by then: each stop is in the
    zone the player starts in, or in the zone the way before it leads into. None when the route can be walked."""
    cur = here.lower()
    for st in stops:
        zone = str(st.get("zone") or "").lower()
        if zone and cur and zone != cur:
            return st
        to = next((str(t.get("zone") or "") for t in st.get("tasks") or [] if t.get("kind") == "travel"), "")
        if to:
            cur = to.lower()
    return None


_STEP = {"pick_up": 0, "objective": 1, "turn_in": 2}  # what of a quest comes before what


def _before_its_quest(stops: list[dict]) -> tuple[dict, dict, dict] | None:
    """The first stop of a route with a step of a quest that an earlier step of the same quest comes after (a turn-in
    before the stop where the quest is picked up or its objective done): (the stop, its task, the stop that has to
    come first). Quests are told apart by their number, by their title when the task has none."""
    def key(t: dict):
        return t.get("id") if t.get("id") is not None else str(t.get("quest") or "").lower() or None

    for n, st in enumerate(stops):
        for t in st.get("tasks") or []:
            if t.get("kind") not in _STEP or key(t) is None:
                continue
            for later in stops[n + 1:]:
                if any(u.get("kind") in _STEP and key(u) == key(t) and _STEP[u["kind"]] < _STEP[t["kind"]]
                       for u in later.get("tasks") or []):
                    return st, t, later
    return None


def _order_refused(stops: list[dict], before: list[dict]) -> str | None:
    """Why an order of the route cannot be set: a quest's turn-in before its pick-up or objective, when the route as
    it was had them the right way round."""
    bad = _before_its_quest(stops)
    if not bad or _before_its_quest(before):
        return None
    st, t, first = bad
    name = lambda s: re.sub(r"^[0-9]+[.)] *", "", str(s["title"]))  # noqa: E731
    return (f"Nothing changed: in this order {name(st)} comes before {name(first)}, and [{t.get('quest')}] is "
            f"{'turned in' if t['kind'] == 'turn_in' else 'done'} at {name(st)} only after {name(first)}. A stop keeps "
            "everything that is done there. When something else at that stop should be done sooner, call plan_route "
            "with that quest in first (and the zones in then in the order the player goes): it makes a stop of its "
            "own for what can be done there now, and leaves the rest where it belongs.")


def _edit_answer_route(ctx: "TurnContext", remove: list[str], order: list[str], stop: list[str],
                       clear: bool, go: list[str]) -> tuple[str, bool]:
    """edit_route after this answer marked a route (plan_route, then a change to it): the change applies to these marks,
    which replace the ones on the map, not to the route on the map."""
    def bare(title: str) -> str:
        return re.sub(r"^\d+[.)]\s*", "", title).strip("[] ").lower()

    def numbered(some: list[dict]) -> str:
        return ", ".join(f"{n}. {re.sub(r'^[0-9]+[.)]\s*', '', m['title'])}" for n, m in enumerate(some, 1))

    marks = list(ctx.marks)

    def needed(some: list[dict]) -> dict | None:
        """In an update of the route (the player asked for nothing): a stop where something of a quest of the quest
        log is to be done."""
        if not getattr(ctx, "route_update", False):
            return None
        log_ids = {q.get("id") for q in ctx.snapshot.get("quests") or [] if isinstance(q, dict)}
        return next((m for m in some for t in m.get("tasks") or []
                     if t.get("kind") in ("objective", "turn_in") and t.get("id") in log_ids), None)
    held = needed(marks if clear else [])
    if held:
        return ("Nothing changed: the route has stops for quests of the quest log ("
                + re.sub(r"^[0-9]+[.)] *", "", held["title"]) + " among them), and an update of the route keeps "
                "what the quest log needs. The player asked for nothing: leave the route as plan_route made it."), True
    if clear:
        del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]
        ctx.route_marked = False
        ctx.route_edit, ctx.edit_notes["route"] = {"clear": True}, ["cleared the route"]
        return "The route is cleared: no marks of yours are on the map.", False

    def pick(wanted: list[str]) -> list[dict] | str:
        found: list[dict] = []
        for name in wanted:
            key = name.rstrip(".")
            if key.isdigit():
                hit = marks[int(key) - 1] if 1 <= int(key) <= len(marks) else None
            else:
                hit = next((m for m in marks if bare(m["title"]) == bare(name) and m not in found), None)
            if hit is None:
                return name
            if hit not in found:
                found.append(hit)
        return found

    gone, first, target = pick(remove), pick(order), pick(go)
    for missing in (gone, first, target):
        if isinstance(missing, str):
            return (f"Nothing changed: no stop called {missing}. The stops of the route you marked in this answer: "
                    f"{numbered(marks)}."), True
    if target and target[0] in gone:
        return f"Nothing changed: {re.sub(r'^[0-9]+[.)] *', '', target[0]['title'])} is taken off the route here.", True
    held = needed(gone)
    if held:
        return ("Nothing changed: " + re.sub(r"^[0-9]+[.)] *", "", held["title"]) + " is where something of a quest of "
                "the quest log is to be done, and an update of the route keeps what the quest log needs."), True
    watched = [t["name"] for t in ctx.track]
    if stop and stop[0].lower() == "all":
        unwatched = watched
    else:
        unwatched = []
        for name in stop:
            hit = next((w for w in watched if w.lower() == name.strip("[] ").lower()), None)
            if hit is None:
                return (f"Nothing changed: nothing called {name} is watched for with this route. "
                        + ("Watched for: " + ", ".join(watched) + "." if watched else "Nothing is watched for.")), True
            unwatched.append(hit)
    adds = {id(m): a for m, a in zip(ctx.marks, ctx.mark_adds)}
    first = [m for m in first if m not in gone]
    left = first + [m for m in marks if m not in gone and m not in first]
    # An order that cannot be walked zone by zone (a stop in one zone after the stops of another, with no way between
    # them) is not the model's to set: the planner orders the zones, and makes the ways.
    player = ctx.snapshot.get("player") or {}
    here = str(player.get("map") or player.get("zone") or "")
    lost = _no_way_to(left, here)
    if first and lost and not _no_way_to(marks, here):
        return (f"Nothing changed: in this order {re.sub(r'^[0-9]+[.)] *', '', lost['title'])} ({lost.get('zone')}) comes "
                "after stops of another zone with no way between them. The order of the zones is plan_route's: call it "
                "with then in the order the player goes, and when on the stop they want first or last; it puts every "
                "way between the zones on the route."), True
    refused = first and _order_refused(left, marks)
    if refused:
        return refused, True
    if any(re.match(r"^\d+[.)]", m["title"]) for m in marks):  # the numbers follow the new order
        for n, m in enumerate([m for m in left if re.match(r"^\d+[.)]", m["title"])], 1):
            m["title"] = f"{n}. {re.sub(r'^[0-9]+[.)]\s*', '', m['title'])}"[:60]
    ctx.marks[:] = left
    ctx.mark_adds[:] = [adds.get(id(m), False) for m in left]
    mobs = {str(m.get("mob")).lower() for m in left if m.get("mob")}
    ctx.track[:] = [t for t in ctx.track if t["name"].lower() in mobs and t["name"] not in unwatched]
    said = []
    if gone:
        said.append("Taken off the route: " + ", ".join(re.sub(r"^[0-9]+[.)]\s*", "", m["title"]) for m in gone) + ".")
        ctx.notes.append("took off the route: " + ", ".join(re.sub(r"^[0-9]+[.)]\s*", "", m["title"]) for m in gone))
    if first:
        ctx.notes.append("put the route in another order")
    if unwatched:
        said.append("No longer watched for: " + ", ".join(unwatched) + ".")
        ctx.notes.append("stopped watching for: " + ", ".join(unwatched))
    said.append(f"The route now: {numbered(left) if left else 'no stops left'}. These marks replace the ones on the map; "
                "do not mark them again.")
    if target:
        _go_to(ctx, {"title": re.sub(r"^[0-9]+[.)]\s*", "", target[0]["title"]), "x": target[0]["x"], "y": target[0]["y"]})
        said.append(f"The arrow leads to {ctx.go_to['title']} now.")
    return " ".join(said), False


def _go_to(ctx: "TurnContext", stop: dict) -> None:
    """The stop the player heads to now: the arrow leads there (edit_route go_to)."""
    ctx.go_to = stop
    ctx.notes = [n for n in ctx.notes if not n.startswith("the arrow leads to ")] + [f"the arrow leads to {stop['title']} now"]


def edit_route(ctx: "TurnContext", args: dict) -> tuple[str, bool]:
    """The edit_route tool: change the route that is on the player's map (the snapshot's marks and tracked sections)
    without marking it again. Either everything asked for fits, or nothing changes. A later call in the same answer
    takes the place of an earlier one for the route; creatures to stop watching for add up. With propose=true nothing
    changes: the player gets the change as a card to accept."""
    def names(value) -> list[str]:
        value = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        return [str(v).strip() for v in value if str(v).strip()]

    def bare(title: str) -> str:
        return re.sub(r"^\d+[.)]\s*", "", title).strip("[] ").lower()

    stops = _map_stops(ctx.snapshot)
    # the stops this answer adds to the route (mark_on_map with add): the order can name them; they go where they cost
    # the least way, as the add-on puts them
    added = [] if ctx.replaces_marks else [{"title": re.sub(r"^\d+[.)]\s*", "", m["title"]), "x": m["x"], "y": m["y"],
                                            "zone": m.get("zone") or "", "added": True, "tasks": m.get("tasks") or []} for m in ctx.marks]
    tracked = [re.split(r" \(also counts|: ", str(line))[0] for line in ctx.snapshot.get("tracked") or []]
    remove, order, stop = names(args.get("remove")), names(args.get("order")), names(args.get("stop_tracking"))
    go = names(args.get("go_to"))[:1]
    clear = args.get("clear") is True
    if not (remove or order or stop or clear or go):
        return "Nothing changed: say what to change (remove, order, clear, go_to or stop_tracking).", True
    if ctx.marks and ctx.replaces_marks and args.get("propose") is not True:
        return _edit_answer_route(ctx, remove, order, stop, clear, go)
    if (remove or order or clear) and not stops:
        return "Nothing changed: nothing of yours is marked on the map.", True

    def numbered(some: list[dict]) -> str:
        return ", ".join(f"{n}. {s['title']}" + (" (added in this answer)" if s.get("added") else "")
                         + (" (done)" if s.get("done") else "") for n, s in enumerate(some, 1))

    def pick(wanted: list[str], among: list[dict]) -> list[dict] | str:
        """The stops named by their number in the marks section or by their title; a name when one fits no stop."""
        found: list[dict] = []
        for name in wanted:
            key = name.rstrip(".")
            if key.isdigit():
                hit = among[int(key) - 1] if 1 <= int(key) <= len(among) else None
            else:
                hit = next((s for s in among if s["title"].lower() == bare(name) and s not in found), None)
            if hit is None:
                return name
            if hit not in found:
                found.append(hit)
        return found

    gone, first, target = pick(remove, stops), pick(order, stops + added), pick(go, stops + added)
    for missing in (gone, first, target):
        if isinstance(missing, str):
            return (f"Nothing changed: no stop called {missing}. The stops on the map: {numbered(stops)}."
                    + (f" Added in this answer: {', '.join(s['title'] for s in added)}." if added else "")), True
    # In an update of the route (the player asked for nothing), a stop a quest of the quest log still needs stays:
    # quests of one chain can share a title, and the log tells them apart by number, not by name.
    if getattr(ctx, "route_update", False) and args.get("propose") is not True:
        needed = [(s, t) for s in (stops if clear else gone) for t in _stop_tasks(ctx.snapshot, s)]
        if needed:
            s, t = needed[0]
            what = "turned in" if t["kind"] == "turn_in" else "done for it"
            return (f"Nothing changed: {s['title']} is where [{t['quest']}] of the quest log is {what}, and an update "
                    "of the route keeps what the quest log needs (two quests of one chain can have one title: the log "
                    "tells them apart by number)."), True
    if stop and stop[0].lower() == "all":
        unwatched = tracked
    else:
        unwatched = []
        for name in stop:
            hit = next((t for t in tracked if t.lower() == name.strip("[] ").lower()), None)
            if hit is None:
                return (f"Nothing changed: nothing called {name} is watched for. "
                        + ("Watched for: " + ", ".join(tracked) + "." if tracked else "Nothing is watched for.")), True
            unwatched.append(hit)

    said, notes, edit = [], [], {}
    propose = args.get("propose") is True
    # the calls of one answer add up: what an earlier one took off stays off, and its order holds until another is given
    state = {"remove": set(), "first": []} if propose else ctx.route_state
    removed = state["remove"] | {_stop_key(s) for s in gone}
    front = [_stop_key(s) for s in first] if first else state["first"]
    if target and _stop_key(target[0]) in removed:
        return f"Nothing changed: {target[0]['title']} is taken off the route here.", True
    if clear:
        edit = {"clear": True}
        said.append("The route is cleared: no marks of yours are on the map.")
        notes.append("cleared the route")
        if not propose:
            state["remove"], state["first"] = set(), []
    elif gone or first or removed or front:
        left = _route_view(ctx, stops, added, removed, front)
        # (the stops of the map say what they are for as far as the quest log tells)
        tasks = lambda some: [dict(s, tasks=s.get("tasks") or _stop_tasks(ctx.snapshot, s)) for s in some]  # noqa: E731
        refused = first and _order_refused(tasks(left), tasks(stops))
        if refused:
            return refused, True
        if removed:
            edit["remove"] = [_bare(s) for s in stops if _stop_key(s) in removed]  # in the order of the route
        gone = [s for s in stops if s in gone]  # in the order of the route
        if gone:
            said.append("Taken off the route: " + ", ".join(s["title"] for s in gone) + ".")
            notes.append("took off the route: " + ", ".join(s["title"] for s in gone))
        if front:
            edit["order"] = [_bare(s) for s in left]
            if first:
                notes.append("put the route in another order")
        said.append("The route now: " + (numbered(left) if left else "no stops left") + ".")
        if not propose:
            state["remove"], state["first"] = removed, front
    if target and not clear:
        said.append(f"The arrow leads to {target[0]['title']} now.")
        if propose:
            edit["go_to"] = _bare(target[0])
    if propose:
        summary = " ".join(said + (["No longer watched for: " + ", ".join(unwatched) + "."] if unwatched else []))
        propose_route(ctx, {"op": "route_edit", "edit": edit, "untrack": unwatched, "summary": summary})
        return "Proposed to the player (a card with Accept and Reject), nothing changed yet: " + summary, False
    if target and not clear:
        _go_to(ctx, _bare(target[0]))
    if edit:
        notes = [n for n in ctx.edit_notes.get("route", []) if n not in notes and edit.get("clear") is None] + notes
        ctx.route_edit, ctx.edit_notes["route"] = edit, notes
    if unwatched:
        ctx.untrack += [name for name in unwatched if name not in ctx.untrack]
        ctx.edit_notes["untrack"] = ["stopped watching for: " + ", ".join(ctx.untrack)]
        said.append("No longer watched for: " + ", ".join(unwatched) + ".")
    return " ".join(said), False


class TurnContext:
    """Everything the tools of one answer work on."""

    def __init__(self, msg: dict, writer: InboxWriter, snapshot: dict, cancel: threading.Event,
                 sources: list[dict]):
        self.msg_id = msg.get("msgId")
        self.writer = writer
        self.snapshot = snapshot
        self.cancel = cancel
        self.sources = sources
        self.actions: list[dict] = []
        self.marks: list[dict] = []
        self.mark_adds: list[bool] = []  # per mark_on_map call: the model asked to keep the earlier marks
        self.route_marked = False  # the marks so far are plan_route's own
        self.track: list[dict] = []  # creatures the game watches for with these marks: {"name", "quest"}
        self.route_edit: dict | None = None  # edit_route: what the game changes in the route that is on the map
        self.untrack: list[str] = []  # edit_route: creatures the game stops watching for
        self.go_to: dict | None = None  # edit_route: the stop the player heads to now {"title", "x", "y"}
        self.edit_notes: dict[str, list[str]] = {}  # what edit_route did, for did()
        self.route_state: dict = {"remove": set(), "first": []}  # edit_route so far in this answer: they add up
        self.phase_cb: Callable[[str], None] = lambda p: None
        self.pre_urls: set[str] = set()
        self.known: dict = {}  # addon options the game reported in this answer (validates proposals)
        self.offered: dict[str, dict] = {}  # quests of other zones read with zone_quests: plan_route can pick them up
        self.zones_read: set[str] = set()  # the zones whose quests were read in this answer
        self.want_screen = bool(msg.get("wantScreen"))
        self.route_update = Service._is_route_update(msg)  # the add-on's own update of the route on the map
        self.ah_items: dict[str, dict] = {}  # items the auction search returned (name -> id, price)
        self.ah_list: list[dict] | None = None  # the player's shopping list (auctionator_list), priced now
        self.ah_list_name: str | None = None  # ... and the name the card saves it under in Auctionator
        self.ah_open: bool | None = None
        self.web_search: Callable[[str], tuple[str, list[dict]]] | None = None  # Gemini: grounded search
        self.web = False  # the model of this answer has a web search (whatever the provider's tool for it is)
        self.conv = str(msg.get("conv") or "default")
        self.tool_calls = 0  # calls of the app's tools that reached the app in this answer (Claude Code)
        self.notes: list[str] = []  # what the answer did that neither marks nor actions show (memory)

    def did(self) -> list[str]:
        """What this answer did besides its text, in words the model reads in the following turns."""
        out = [note for kind in ("route", "untrack") for note in self.edit_notes.get(kind, [])]  # the game edits first
        for i, m in enumerate(self.marks):
            how = "added to the marks on the map"
            if self.replaces_marks:
                how = "marked on the map (earlier marks removed)" if i == 0 else "marked on the map"
            out.append(f"{how}: {m['title']} ({m['x']}, {m['y']})")
        if self.ah_list:
            out.append("auction card shown to the player (prices of that moment): "
                       + "; ".join(f"{it.get('name')} {it.get('price') or 'no price in the last scan'}"
                                   for it in self.ah_list))
        for a in self.actions:
            op = a.get("op")
            if op == "vendor_sell":
                out.append("proposed selling: " + ", ".join(str(i.get("name")) for i in a.get("items") or []))
            elif op == "vendor_repair":
                out.append(f"proposed repairing for {a.get('cost')}")
            elif op == "abandon_quest":
                out.append(f"proposed abandoning [{a.get('title')}]")
            elif op == "auction_post":
                out.append("proposed posting on the auction house (waits for the player's clicks): " + "; ".join(
                    f"{lot.get('name')} ×{lot.get('count')} at {lot.get('unit'):g}c each"
                    for lot in a.get("lots") or []))
            elif op == "auction_cancel":
                out.append("proposed cancelling auctions: " + ", ".join(
                    f"{x.get('name')} ×{x.get('count')}" for x in a.get("auctions") or []))
            elif op == "route":
                out.append("proposed a new route (waits for the player's Accept): "
                           + ", ".join(str(s.get("title")) for s in a.get("stops") or []))
            elif op == "route_edit":
                out.append("proposed changing the route (waits for the player's Accept): " + str(a.get("summary") or ""))
            else:
                out.append(f"proposed a change in {a.get('addon')}: {str(a.get('description') or '')[:80]}")
        if self.track:
            out.append("watching for: " + ", ".join(t["name"] for t in self.track))
        return out + self.notes

    def watch(self, name: str, quest: str | None = None) -> str | None:
        """The name the game watches for after this call; None when it does not (no name, or the list is full)."""
        name = str(name or "").strip("[] ")[:60]
        for t in self.track:
            if t["name"].lower() == name.lower():
                return t["name"]
        if not name or len(self.track) >= MAX_TRACKED:
            return None
        self.track.append({"name": name, **({"quest": quest} if quest else {})})
        return name

    @property
    def replaces_marks(self) -> bool:
        """This answer's marks replace the marks of earlier answers, unless every one of them was an addition."""
        return not (self.mark_adds and all(self.mark_adds) and len(self.mark_adds) == len(self.marks))

    def used_sources(self, answer: str, limit: int = 8) -> list[dict]:
        """Sources for the answer card: what the model looked up itself, plus pre-fetched knowledge-base pages only
        when the answer actually talks about them."""
        low = (answer or "").lower()
        out = []
        for src in self.sources:
            if src.get("url") in self.pre_urls:
                title = str(src.get("title") or "").lower().replace("(forever)", "").strip()
                if not title or title not in low:
                    continue
            out.append(src)
        return out[:limit]

    def remember_auction(self, res: dict | None) -> None:
        """The items an auction search returned (for the card) and whether the auction house is open."""
        for it in (res or {}).get("items") or []:
            if it.get("name") and it.get("id"):
                self.ah_items[str(it["name"])] = {"id": it["id"], "name": it["name"], "price": it.get("price"),
                                                  **{k: it[k] for k in ("cannotUse", "tooLowLevel") if it.get(k)}}
        if res and res.get("ahOpen") is not None:
            self.ah_open = bool(res.get("ahOpen"))

    def auction_card(self, answer: str) -> list[dict] | None:
        """Items for the Auction house card ("Search on the AH", "Save as list"): the player's shopping list when
        this answer called auctionator_list (priced now; an item without a price in the last scan has none); else the
        auction items of this answer's searches that the answer names in [brackets]."""
        if self.ah_list is not None:
            return [{k: it[k] for k in ("id", "name", "price") if it.get(k)} for it in self.ah_list] or None
        found = {k.lower(): v for k, v in self.ah_items.items()}
        named = {n.lower() for n in bracket_names(answer or "")}
        picked = [v for k, v in found.items() if k in named]
        return picked[:10] or None

    def auction_list(self) -> dict:
        """The name the card saves its items under in Auctionator, when the model gave the list; else the add-on
        uses its own."""
        return {"auctionList": self.ah_list_name} if self.ah_list and self.ah_list_name else {}

    def add_source(self, src: dict) -> None:
        if src.get("url") and all(x["url"] != src["url"] for x in self.sources):
            self.sources.append({k: src.get(k) for k in ("title", "url", "date")})


class Service:
    fetch_route_quests = True  # a route is planned with the quests of its zones read from the game (tests: off)
    name_language = True  # the language of a conversation is named by a small model for the add-on's messages (tests: off)

    def __init__(self, settings: config.SettingsStore, kb: KB | None, addons_dir: Path,
                 client_factory: Callable[[str], OpenRouter] = OpenRouter, lookahead: int = 200,
                 gemini_factory: Callable[[str], Gemini] = Gemini, keep_notes: bool = False):
        self.store = settings
        self.keep_notes = keep_notes  # the notes of each conversation are kept up to date (a small model call a turn)
        self.kb = kb
        self.addons_dir = Path(addons_dir)
        self._notes_lock, self._noting = threading.Lock(), set()  # conversations whose notes are being written
        # the ways between zones of this client, when the player has the map add-on that lists them
        from . import ways
        found = ways.use_icons(self.addons_dir / "Leatrix_Maps" / "Leatrix_Maps_Icons.lua")
        log.info("ways between zones: %s", f"{found} crossings and rides from Leatrix Maps" if found
                 else "only the few built in (Leatrix Maps is not installed)")
        # other add-ons of the player whose data files the app reads (their tables are private to them in the game)
        self.map_notes, self.pet_skills = MapNotes(self.addons_dir), PetSkills(self.addons_dir)
        if self.map_notes.installed():
            ways.use_zone_levels(self.map_notes.zone_levels())
            ways.use_transports({side: self.map_notes.transports(side) for side in "AH"})
        self.client_factory = client_factory
        self.lookahead = lookahead
        self.assembler = FrameAssembler()
        self.gemini_factory = gemini_factory
        self.memory = MemoryStore(config.data_dir() / "memory.json")
        self.convs = ConversationStore(config.data_dir() / "convs")
        self._flights: dict | None = None  # per character: the flight masters the game last named
        self._shopping: dict | None = None  # per conversation: the auction shopping list
        self._languages: dict[str, str] = {}  # per conversation: the language the player writes in, named by a model
        self._char = "?"
        self.last_cost: float | None = None
        self._balance: tuple[float, float] | None = None  # (value, fetched_at)
        self._key_info: tuple[dict, float] | None = None  # OpenRouter's /key: the free models' requests of the day
        self._models: tuple[list[dict], float] | None = None
        self._gemini_models: tuple[list[dict], float] | None = None
        self._gemini_down: dict[str, float] = {}  # model the fallback answers for -> when it is checked again
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.on_event: Callable[[str], None] = lambda s: None
        self.last_question: dict | None = None
        self._turn: TurnContext | None = None  # answer in progress (tools called by Claude Code land here)
        self._game_pending: dict[str, dict] = {}
        self._zone_quests: dict[tuple, tuple] = {}  # (character, zone) -> (when read, its quests), for routes
        self._zone_quests_failed = -1e9  # when the game last did not answer
        self._req_ids = itertools.count(1)
        self._bridge = None
        self._claude: claude_code.ClaudeSession | None = None
        # a second session for the updates of a route, when the player chose another model for them
        self._claude_route: claude_code.ClaudeSession | None = None
        self._route_plans: dict | None = None  # conversation -> how its route was planned (see _plans)
        self._claude_lock = threading.Lock()
        self.screen_for: Callable[[int], bytes | None] = lambda msg_id: None  # set by the app (screenshot watcher)

    # ---------------------------------------------------------------- input
    def on_frame(self, frame: Frame) -> None:
        data = self.assembler.add(frame)
        if data is None:
            return
        try:
            msg = json.loads(zlib.decompress(data).decode("utf-8"))
        except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as e:
            log.error("cannot decode message %s: %s", frame.msg_id, e)
            return
        self.handle(msg)

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "game_result":  # the game finished work the model asked for; no reply goes back
            self._game_result(msg)
            return
        if kind == "game_results":  # ... several pieces of work that were through together, in one message
            for one in msg.get("results") or []:
                if isinstance(one, dict):
                    self._game_result(one)
            return
        if kind == "debug":  # the add-on's own log, sent while the app asks for it: written down, no reply
            self._debug_log(msg)
            return
        safe = {k: v for k, v in msg.items() if k not in ("snapshot", "value")}
        log.info("message %s", safe)
        if kind == "question":
            activity.info("Question: %s", str(msg.get("text") or "")[:90])
            log.info("game data: %s", msg.get("perf"))
        elif kind in _REQUEST_NAMES:
            activity.info("Request: %s", _REQUEST_NAMES[kind])
        self._char = str(msg.get("char") or self._char)
        for conv in msg.get("deleteConvs") or []:
            self.convs.delete(str(conv))
        writer = InboxWriter(self.addons_dir, str(msg.get("session")), int(msg.get("nextInbox") or 1),
                             pool_size(self.addons_dir), self.lookahead)
        if kind in ("memory_add", "memory_delete", "memory_clear", "memory_list"):
            text = None
            if kind == "memory_add":
                item = self.memory.add(self._char, str(msg.get("value") or ""))
                text = "Added to memory." if item else "Nothing added (empty or duplicate)."
            elif kind == "memory_delete":
                self.memory.delete(self._char, int(msg.get("value") or 0))
                text = "Entry deleted."
            elif kind == "memory_clear":
                self.memory.clear(self._char)
                text = "Memory cleared."
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "text": text,
                         "status": self.status()})
            return
        if kind == "question":
            self._save_snapshot(msg)
            sess = str(msg.get("session"))
            with self._lock:
                old = self._cancel.get(sess)
                if old:
                    old.set()
                ev = threading.Event()
                self._cancel[sess] = ev
            # the AI in use goes with the first reply: the game header follows a change made in the app window
            writer.send({"kind": "answer", "final": False, "msgId": msg.get("msgId"), "text": "",
                         "phase": "Thinking…", "retryAfter": RETRY_THINKING_S, "ai": self.ai_status()})
            threading.Thread(target=self._answer_safe, args=(msg, writer, ev), daemon=True).start()
        elif kind == "status":
            writer.send({"kind": "status", "final": True, "msgId": msg.get("msgId"), "status": self.status(True)})
        elif kind in _MOVED_SETTINGS:
            writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                         "text": "AI settings are in the ForeverAI app window now. Type /reload to update the add-on."})
        elif kind == "new_conversation":
            pass
        else:
            log.warning("unknown message type %s", kind)

    # ---------------------------------------------------------------- settings (changed in the app window)
    def set_provider(self, value: str) -> tuple[bool, str]:
        """Returns (ok, message for the player)."""
        if value == "claude_code":
            info = claude_code.detect(force=True)
            if not info or not info.get("loggedIn"):
                return False, "Claude Code is not installed or not signed in (Keys & sign-in…)."
        elif value in ("openrouter", "gemini"):
            if not config.read_key(value):
                return False, f"No {PROVIDER_NAMES[value]} key yet. Enter it in Keys & sign-in… first."
        else:
            return False, "Unknown provider."
        self.store.update(provider=value)
        self.prewarm_later()
        s = self.store.settings
        label = {"claude_code": f"Claude Code · {model_name(s.claude_model)} · thinking {EFFORT_NAMES.get(s.claude_effort, s.claude_effort)}",
                 "gemini": f"Google Gemini · {s.gemini_model}"}.get(value, f"OpenRouter · {s.model}")
        activity.info("AI provider: %s", label)
        return True, "AI provider: " + label

    def _model_problem(self, provider: str, model: str) -> str | None:
        """Why this model cannot be used with the provider; None when it can."""
        if provider == "claude_code":
            known = {m["id"] for m in claude_code.CLAUDE_MODELS} | {"sonnet", "opus", "haiku", "fable"}
            return None if model in known or model.startswith("claude-") else f"{model} is not a Claude model."
        if provider == "gemini":
            ids = {m["id"] for m in self.gemini_models()}
            if not gemini.is_chat_model(model) or (ids and model not in ids and not model.endswith("-latest")):
                return f"{model} is not a Gemini chat model available to this key."
            return None
        ids = {m["id"] for m in self.models()}
        return f"Model {model} does not exist on OpenRouter." if ids and model not in ids else None

    def set_model(self, model: str) -> tuple[bool, str]:
        """A model of the current provider; returns (ok, message for the player)."""
        model = str(model or "").strip()
        provider = self.store.settings.provider
        if not model:
            return False, "Choose a model or enter its ID."
        problem = self._model_problem(provider, model)
        if problem:
            return False, problem
        if provider == "claude_code":
            self.store.update(claude_model=model)
            self.prewarm_later()
            text = f"Claude model: {model_name(model)}"
        elif provider == "gemini":
            self.store.update(gemini_model=model)
            self._gemini_down.pop(model, None)  # chosen again: it gets another chance
            text = f"Gemini model: {model}"
        else:
            self.store.update(model=model)
            text = f"Model: {model}"
        activity.info(text)
        return True, text

    def set_route_model(self, model: str) -> tuple[bool, str]:
        """The model that answers the add-on's own updates of a route (the player switched them on in the game), for
        the current provider; "" means the model of questions. Returns (ok, message for the player)."""
        model = str(model or "").strip()
        provider = self.store.settings.provider
        chosen = dict(self.store.settings.route_model or {})
        if not model:
            chosen.pop(provider, None)
            text = "Route updates: the same model as questions"
        else:
            problem = self._model_problem(provider, model)
            if problem:
                return False, problem
            chosen[provider] = model
            text = "Route updates: " + (model_name(model) if provider == "claude_code" else model)
        self.store.update(route_model=chosen)
        activity.info(text)
        return True, text

    def model_for(self, msg: dict, provider: str) -> str:
        """The model that answers this message: the one chosen for route updates when the add-on asks for one, else
        the model of questions."""
        s = self.store.settings
        main = {"claude_code": s.claude_model, "gemini": s.gemini_model}.get(provider, s.model)
        return (self._is_route_update(msg) and (s.route_model or {}).get(provider)) or main

    @staticmethod
    def _is_route_update(msg: dict) -> bool:
        """The add-on's own request to update the route (an add-on that does not say "news" yet sends the text)."""
        text = str(msg.get("text") or "")
        return msg.get("news") is True or text.startswith("[Quest log changed]") or text.startswith("[Route check]")

    def set_save_logs(self, on: bool) -> tuple[bool, str]:
        """Write the log files or not (the checkbox in the app window)."""
        from . import logs
        self.store.update(save_logs=bool(on))
        logs.set_enabled(bool(on))
        return True, "Log files are written from now on." if on else "Log files are no longer written."

    def clear_logs(self) -> tuple[bool, str]:
        from . import logs
        n = logs.clear()
        return True, f"{n} log file{'s' if n != 1 else ''} removed." if n else "There were no log files."

    def set_route_effort(self, effort: str) -> tuple[bool, str]:
        """The thinking level of the add-on's own updates of a route (Claude Code); "" means the one of questions."""
        effort = str(effort or "").strip()
        if effort and effort not in claude_code.EFFORTS:
            return False, "Unknown thinking level."
        self.store.update(route_effort=effort)
        text = ("Route updates: thinking " + EFFORT_NAMES.get(effort, effort) if effort
                else "Route updates: the same thinking level as questions")
        activity.info(text)
        return True, text

    def effort_for(self, msg: dict) -> str:
        """The thinking level this message is answered with (Claude Code)."""
        s = self.store.settings
        return (self._is_route_update(msg) and s.route_effort) or s.claude_effort

    def set_effort(self, effort: str) -> tuple[bool, str]:
        if effort not in claude_code.EFFORTS:
            return False, "Unknown thinking level."
        self.store.update(claude_effort=effort)
        self.prewarm_later()
        text = f"Thinking level: {EFFORT_NAMES.get(effort, effort)}"
        activity.info(text)
        return True, text

    def set_search_mode(self, mode: str) -> tuple[bool, str]:
        if mode not in SEARCH_MODES:
            return False, "Unknown web search mode."
        self.store.update(search_mode=mode)
        self.prewarm_later()
        text = f"Web search: {SEARCH_MODES[mode]}"
        activity.info(text)
        return True, text

    def model_choices(self, provider: str) -> list[dict]:
        """The models offered in the app window for a provider: {"id", "name", "price"} (price: OpenRouter only).
        May ask the provider for its model list: call it off the Tk thread."""
        s = self.store.settings
        if provider == "claude_code":
            return [{"id": m["id"], "name": m["name"], "price": None} for m in claude_code.CLAUDE_MODELS]
        if provider == "gemini":
            return [dict(m, price=None) for m in self.gemini_status()["models"]]
        by_id = {m["id"]: m for m in self.models()} if config.read_key("openrouter") else {}
        ids = [s.model] + [f for f in s.favorites if f != s.model][:9]
        ids += [f for f in self.free_models(by_id) if f not in ids]
        free_note = self.free_note() if any(is_free(mid) for mid in ids) else None
        out = []
        for mid in ids:
            m = by_id.get(mid)
            if is_free(mid):
                price = free_note
            else:
                price = f"${m['in']:.2f} / ${m['out']:.2f} per 1M tokens" if m and m.get("in") is not None else None
            out.append({"id": mid, "name": mid, "price": price})
        return out

    @staticmethod
    def free_models(by_id: dict[str, dict]) -> list[str]:
        """The free OpenRouter models that chat and call tools: the most used first (FREE_MODELS), newer ones after
        them, the free router last. Only the ones OpenRouter still lists, once its list is known."""
        if not by_id:
            return FREE_MODELS + [FREE_ROUTER]
        usable = [mid for mid, m in by_id.items() if is_free(mid) and m.get("free", True) and m.get("tools", True)
                  and m.get("text", True)]
        known = [mid for mid in FREE_MODELS if mid in usable]
        return known + [mid for mid in usable if mid not in known and mid != FREE_ROUTER] + \
            [mid for mid in usable if mid == FREE_ROUTER]

    def free_note(self) -> str:
        """What a free model costs: nothing, within OpenRouter's limit of requests."""
        left = self.free_requests()
        if left:
            return f"Free · {left['left']} of {left['limit']} requests left today"
        return "Free · 50 requests a day, 20 a minute"

    def save_key(self, provider: str, key: str, use: bool = False) -> tuple[bool, str]:
        """Check the key with the provider and save it; ``use`` also switches to that provider.
        Returns (ok, message for the player)."""
        key = key.strip()
        name = PROVIDER_NAMES[provider]
        if not key or any(c.isspace() for c in key):
            return False, f"That does not look like a {name} key."
        factory = self.gemini_factory if provider == "gemini" else self.client_factory
        try:
            factory(key).key_info()
        except Exception as e:  # invalid key or network
            return False, f"{name} rejected the key ({str(e)[:80]}). Nothing changed."
        where = f" in {keystore.NAME}" if config.write_key(provider, key) else ""
        if not where and config.secure_keys():
            activity.warning("No system keyring available: the key is in %s, readable only by you", config.env_path())
        if provider == "openrouter":
            self._balance = self._models = self._key_info = None
        else:
            self._gemini_models = None
        if use:
            self.store.update(provider=provider)
        activity.info("%s key saved%s%s", name, where, " · AI provider: " + name if use else "")
        return True, f"Key saved{where}." + (f" Using {name}." if use else "")

    # ---------------------------------------------------------------- status
    def _refresh(self, what: str) -> None:
        key = config.read_key("gemini" if what == "gemini_models" else "openrouter")
        if not key:
            return
        try:
            if what == "gemini_models":
                self._gemini_models = (self.gemini_factory(key).models(), time.time())
            elif what == "models":
                self._models = (self.client_factory(key).models(), time.time())
            elif what == "key":
                self._key_info = (self.client_factory(key).key_info(), time.time())
            else:
                self._balance = (self.client_factory(key).credits(), time.time())
        except Exception as e:
            # the free requests left are only shown: without them the window says what the limit is
            (log.info if what == "key" else log.warning)("%s refresh failed: %s", what, e)
            if what == "gemini_models" and self._gemini_models is None:
                self._gemini_models = ([], time.time() - 3300)  # do not block every status; retry in 5 minutes
            if what == "key" and self._key_info is None:
                self._key_info = ({}, time.time())  # not known; asked again in the background after a minute

    def _refresh_later(self, what: str) -> None:
        """Refresh a cached value in the background so replies never wait on the network."""
        flag = f"_refreshing_{what}"
        if getattr(self, flag, False):
            return
        setattr(self, flag, True)

        def run():
            try:
                self._refresh(what)
            finally:
                setattr(self, flag, False)
        threading.Thread(target=run, daemon=True).start()

    def models(self) -> list[dict]:
        if self._models is None:
            self._refresh("models")  # first use: nothing cached yet
        elif time.time() - self._models[1] > 3600:
            self._refresh_later("models")
        return self._models[0] if self._models else []

    def gemini_models(self) -> list[dict]:
        if self._gemini_models is None:
            self._refresh("gemini_models")  # first use: nothing cached yet
        elif time.time() - self._gemini_models[1] > 3600:
            self._refresh_later("gemini_models")
        return self._gemini_models[0] if self._gemini_models else []

    def gemini_status(self) -> dict:
        s = self.store.settings
        key = config.read_key("gemini")
        available = {m["id"]: m for m in self.gemini_models()} if key else {}
        ids = [s.gemini_model] + [m for m in gemini.FAVORITE_MODELS if m != s.gemini_model]
        if available:  # aliases (…-latest) are not listed by the API but work
            ids = [m for m in ids if m in available or m.endswith("-latest") or m == s.gemini_model]
        return {"hasKey": bool(key), "keyMask": config.mask_key(key), "model": s.gemini_model,
                "models": [{"id": m, "name": (available.get(m) or {}).get("name") or m} for m in ids[:8]]}

    def balance(self, fresh: bool = False) -> float | None:
        if self._balance is None or fresh:
            self._refresh("balance")
        elif time.time() - self._balance[1] > 60:
            self._refresh_later("balance")
        return self._balance[0] if self._balance else None

    def free_requests(self, fresh: bool = False, cached: bool = False) -> dict | None:
        """{"left", "limit"}: the free models' requests left today on OpenRouter (None: not known); ``cached`` never
        asks OpenRouter."""
        if not cached and config.read_key("openrouter"):
            if self._key_info is None or fresh:
                self._refresh("key")
            elif time.time() - self._key_info[1] > 60:
                self._refresh_later("key")
        info = (self._key_info[0] if self._key_info else {}).get("free_model_daily_requests") or {}
        try:
            return {"left": max(0, int(info["remaining"])), "limit": int(info["limit"])}
        except (KeyError, TypeError, ValueError):
            return None

    def search_mode(self, model: str | None = None) -> str:
        """The web search an answer from ``model`` (the model of questions when not given) uses: the chosen one, but
        none with a free OpenRouter model while the balance (the one known now) does not cover a few paid searches."""
        s = self.store.settings
        if s.provider == "openrouter" and is_free(model or s.model) and s.search_mode != "never":
            bal = self._balance[0] if self._balance else None
            if bal is None or bal < FREE_SEARCH_MIN_BALANCE:
                return "never"
        return s.search_mode

    def _spent(self, cost: float | None, free_requests: int = 0) -> None:
        """Apply the known cost of an answer to the cached balance (and the requests of a free model to the ones left
        today) and verify it in the background."""
        if self._balance and cost:
            self._balance = (self._balance[0] - cost, self._balance[1])
        self._refresh_later("balance")
        if free_requests:
            info = (self._key_info[0] if self._key_info else {}).get("free_model_daily_requests")
            if isinstance(info, dict) and isinstance(info.get("remaining"), int):
                info["remaining"] = max(0, info["remaining"] - free_requests)
            self._refresh_later("key")

    def _status_after(self, cost: float | None, free_requests: int = 0) -> dict:
        self._spent(cost, free_requests)
        return self.status()

    def _debug_log(self, msg: dict) -> None:
        """The add-on's log lines go to data/debug.log (kept under 2 MB; the older half moves to debug.log.1), when
        the player has the log files switched on."""
        from . import logs
        if not logs.enabled():
            return
        path = config.data_dir() / "debug.log"
        who = str(msg.get("char") or "?")
        text = "".join(f"{datetime.fromtimestamp(int(e.get('t') or 0)).strftime('%Y-%m-%d %H:%M:%S')} {who} "
                       f"{str(e.get('k'))}: {str(e.get('m'))}\n" for e in msg.get("entries") or [] if isinstance(e, dict))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix(".log.1"))
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            log.warning("debug log: %s", e)

    def status(self, fresh: bool = False) -> dict:
        """What the add-on shows (the AI in use, balance, memory); the AI settings themselves are in the app window."""
        s = self.store.settings
        bal = self.balance(fresh) if config.read_key("openrouter") else None
        free = s.provider == "openrouter" and is_free(s.model)
        return {
            "balance": round(bal, 4) if bal is not None else None,
            "balanceAt": datetime.now().strftime("%H:%M"),
            "lowBalance": s.low_balance,
            "lastCost": self.last_cost,
            "kbDate": self.kb.meta("kb_date") if self.kb else None,
            "hotfixDate": self.kb.meta("hotfix_date") if self.kb else None,
            "kbPages": self.kb.count_pages() if self.kb else 0,
            "addonSV": self.addon_sv(),
            "memory": self.memory.get(self._char),
            "debugLog": config.dev_mode(),  # the add-on sends its log while running against a source checkout
            **self.ai_status(),
            # a free model: the requests left today instead of the balance ({} while not known)
            **({"free": self.free_requests(fresh) or {}} if free else {}),
        }

    def ai_status(self) -> dict:
        """The AI in use, as the game header and the minimap button show it."""
        s = self.store.settings
        return {"provider": s.provider, "model": s.model, "searchMode": self.search_mode(),
                "claude": {"model": s.claude_model, "effort": s.claude_effort}, "gemini": {"model": s.gemini_model}}

    def addon_sv(self) -> dict:
        if not hasattr(self, "_addon_sv"):
            from .addon_meta import saved_variables
            try:
                self._addon_sv = saved_variables(self.addons_dir)
            except OSError:
                self._addon_sv = {}
        return self._addon_sv

    # ---------------------------------------------------------------- answering
    def _save_snapshot(self, msg: dict) -> None:
        """The game data of the last question, kept beside the log files for looking into a wrong answer."""
        from . import logs
        if not logs.enabled():
            return
        try:
            p = config.data_dir() / "last_question.json"
            p.write_text(json.dumps({"text": msg.get("text"), "snapshot": msg.get("snapshot")}, ensure_ascii=False),
                         encoding="utf-8")
        except OSError:
            pass

    def _answer_safe(self, msg: dict, writer: InboxWriter, cancel: threading.Event) -> None:
        try:
            self._answer(msg, writer, cancel)
        except Exception as e:  # never leave the player without a reply
            log.exception("answer failed")
            writer.send({"kind": "error", "final": True, "msgId": msg.get("msgId"),
                         "text": f"ForeverAI error: {str(e)[:200]}", "status": self.status()})

    def _answer(self, msg: dict, writer: InboxWriter, cancel: threading.Event) -> None:
        msg_id = msg.get("msgId")
        question = str(msg.get("text") or "").strip()
        snapshot = msg.get("snapshot") or {}
        s = self.store.settings
        conv = str(msg.get("conv") or "default")
        history = self.convs.history(conv)
        # The add-on's own update of a route is a small job that comes often: the route is planned again here, the
        # way the model planned it, and only the game data a route needs is read again. The model checks the result
        # and says one line, instead of reading everything, calling the planner and writing an answer.
        update = self._is_route_update(msg)
        self._saw_log(conv, snapshot)
        snapshot = self._with_flights(str(msg.get("char") or ""), snapshot)
        pre_hits = []
        if self.kb and not update:
            names = entity_names(snapshot)
            pre_hits = self.kb.search_titles(names, k=3)
        memory = self.memory.get(self._char)
        sources: list[dict] = [{"title": h.title, "url": h.url, "date": h.ts[:10]} for h in pre_hits]
        ctx = TurnContext(msg, writer, snapshot, cancel, sources)
        ctx.pre_urls = {x["url"] for x in sources}  # guessed from names in the game, not chosen by the model
        planned = self.plan_again(ctx) if update else None
        if planned and msg.get("fast") and not self._same_route(ctx):
            # at once: the route planned again the way the model planned it; the model checks it after (the add-on
            # asks for that a moment later), so that the player sees the change without waiting for a model
            writer.send({"kind": "answer", "final": True, "msgId": msg_id, "text": self._route_news(ctx),
                         "marks": ctx.marks, "marksReplace": True, "track": ctx.track, "routePlanned": True,
                         **({"marksMore": True} if getattr(ctx, "route_more", False) else {}),
                         **({"route": ctx.route_edit} if ctx.route_edit else {}),
                         "check": True, "status": self.status()})
            activity.info(self._route_news(ctx))
            return
        if planned and self._same_route(ctx) and not msg.get("check"):
            # the route planned again is the route on the map: nothing for a model to check, and nothing to say
            del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]
            writer.send({"kind": "answer", "final": True, "msgId": msg_id, "text": "Route checked: nothing to change.",
                         "routeUnchanged": True, "status": self.status()})
            activity.info("Route checked: nothing to change")
            return
        if not update:
            from . import ways
            level = (snapshot.get("player") or {}).get("level")
            entrances = ways.dungeons(level=int(level)) if isinstance(level, (int, float)) else []
            if entrances:
                snapshot = dict(snapshot, dungeons=entrances)
        messages = build_messages(question, snapshot, history, pre_hits, now=datetime.now(),
                                  kb_date=self.kb.meta("kb_date") if self.kb else None,
                                  hotfix_date=self.kb.meta("hotfix_date") if self.kb else None,
                                  memory=memory, auto=bool(msg.get("auto")), route_update=update, planned=planned,
                                  notes=self.convs.notes(conv),
                                  route_plan=self._plans().get(conv) if snapshot.get("marks") else None,
                                  language=self._language(conv, history) if msg.get("auto") else None)
        self._turn = ctx
        try:
            if s.provider == "claude_code":
                self._answer_claude(msg, ctx, messages, conv)
            else:
                self._answer_api(msg, ctx, messages, "gemini" if s.provider == "gemini" else "openrouter")
        finally:
            if self._turn is ctx:
                self._turn = None
        self._note_later(conv, auto=bool(msg.get("auto")))

    # ---------------------------------------------------------------- the notes of a conversation
    def _note_later(self, conv: str, auto: bool = False) -> None:
        """Bring the notes of the conversation up to date in the background: after every message of the player, and
        after a run of the add-on's own messages (route updates) that would otherwise push turns out unseen."""
        waiting = self.convs.unnoted(conv) if self.keep_notes else []
        if not waiting or (auto and len(waiting) < NOTES_AUTO_TURNS):
            return
        with self._notes_lock:
            if conv in self._noting:
                return
            self._noting.add(conv)
        threading.Thread(target=self._note_safe, args=(conv,), daemon=True, name="notes").start()

    def _note_safe(self, conv: str) -> None:
        try:
            for _ in range(3):  # turns answered meanwhile are taken in too
                turns = self.convs.unnoted(conv)
                if not turns:
                    break
                text = self._ask_plain(NOTES_SYSTEM, notes_prompt(self.convs.notes(conv), turns))
                if not text:
                    break
                self.convs.set_notes(conv, text.strip()[:NOTES_MAX_CHARS], max(int(t.get("n") or 0) for t in turns))
                log.info("notes of conversation %s brought up to date (%d turns, %d characters)", conv, len(turns), len(text))
        except Exception:
            log.exception("notes of a conversation failed")
        finally:
            with self._notes_lock:
                self._noting.discard(conv)

    def _ask_plain(self, system: str, prompt: str) -> str | None:
        """One question to the player's AI provider without tools or game data, for the app's own errands. A
        cheap model of the provider answers (Haiku with Claude Code, the fallback model elsewhere), not the one
        chosen for questions: it runs after every message. None when it could not."""
        s = self.store.settings
        if s.provider == "claude_code":
            info = claude_code.detect_cached()
            if not info or not info.get("loggedIn"):
                return None
            return claude_code.ask_once(info["path"], config.data_dir() / "claude_session" / "notes", system, prompt)
        provider = "gemini" if s.provider == "gemini" else "openrouter"
        key = config.read_key(provider)
        if not key:
            return None
        client = (self.gemini_factory if provider == "gemini" else self.client_factory)(key)
        text = ""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        cheap = gemini.FALLBACK_MODEL if provider == "gemini" else (s.fallback_model or self.model_for({}, provider))
        for kind, val in client.chat_stream(cheap, messages, [], "never", 1200):
            if kind == "text":
                text += val
            elif kind == "error":
                return None
        return text.strip() or None

    def _answer_api(self, msg: dict, ctx: TurnContext, messages: list[dict], provider: str) -> None:
        """An answer from an API with OpenAI-style chat (OpenRouter or Gemini), running the tool rounds here."""
        msg_id, writer, cancel, sources = ctx.msg_id, ctx.writer, ctx.cancel, ctx.sources
        question = str(msg.get("text") or "").strip()
        s = self.store.settings
        is_gemini = provider == "gemini"
        key = config.read_key(provider)
        if not key:
            writer.send({"kind": "error", "final": True, "msgId": msg_id, "status": self.status(),
                         "text": f"No {PROVIDER_NAMES[provider]} key. Enter it in the ForeverAI app window "
                                 "(Keys & sign-in…)."})
            return
        client = (self.gemini_factory if is_gemini else self.client_factory)(key)
        model = self.model_for(msg, provider)
        free = not is_gemini and is_free(model)
        # a free model is followed by the free router, never by a paid model
        fallback = gemini.FALLBACK_MODEL if is_gemini else FREE_ROUTER if free else s.fallback_model
        if free and self._key_info is None:
            self._refresh_later("key")  # the free requests left, for the reply
        if free and s.search_mode != "never":
            self.balance()  # paid web search with a free model needs a known balance
        search_mode = s.search_mode if is_gemini else self.search_mode(model)
        if search_mode != s.search_mode:
            log.info("web search off: %s is free and the balance does not cover paid searches", model)
        ctx.web = search_mode != "never"
        if is_gemini and model in self._gemini_down:
            log.info("%s could not answer recently: %s answers", model, fallback)
            self._recheck_gemini(client, model)
            model = fallback
        if is_gemini:
            def search(query: str) -> tuple[str, list[dict]]:
                return client.web_search(model, query)
            ctx.web_search = search
            if s.search_mode == "always":
                messages[0]["content"] += "\n\nAlways check current information with web_search before answering."
        shot = self.screen_image(ctx)
        if shot:
            model_info = {"image": True} if is_gemini else ({m["id"]: m for m in self.models()}.get(model) or {})
            last = messages[-1]
            if model_info.get("image"):
                last["content"] = [{"type": "text", "text": last["content"] + "\n\n" + SCREEN_NOTE},
                                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + shot}}]
            else:
                last["content"] += f"\n\n(The player attached a screenshot, but {model} cannot read images.)"
        text = ""
        phase = "Thinking…"
        cost = 0.0
        started = time.monotonic()
        last_sent = [0.0, "", ""]  # time, text, phase
        tried_fallback = False
        answered_by = None  # the model OpenRouter says answered (the free router picks one)
        requests = 0

        def flush(force: bool = False) -> None:
            now = time.monotonic()
            if cancel.is_set():
                return
            elapsed = now - started
            if not force:
                if elapsed < FIRST_FLUSH_S or now - last_sent[0] < FLUSH_EVERY_S:
                    return
                if text == last_sent[1] and phase == last_sent[2]:
                    return
            writer.send({"kind": "answer", "final": False, "msgId": msg_id, "text": md_to_wow(text) if text else "",
                         "phase": phase, "retryAfter": RETRY_TEXT_S if text else RETRY_THINKING_S})
            last_sent[:] = [now, text, phase]

        def set_phase(p: str) -> None:
            nonlocal phase
            if p:
                phase = p
                flush(force=True)
        ctx.phase_cb = set_phase
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            calls = None
            round_text = ""
            error = None
            unavailable = False  # Gemini: this model cannot answer now, another one may
            requests += 1
            for kind, val in client.chat_stream(model, messages, TOOLS, search_mode, s.max_answer_tokens):
                if cancel.is_set():
                    log.info("answer %s cancelled", msg_id)
                    return
                if kind == "text":
                    text += val
                    round_text += val
                    phase = "Writing…"
                    flush()
                elif kind == "annotations":
                    for a in val:
                        uc = a.get("url_citation") or {}
                        if uc.get("url") and all(x["url"] != uc["url"] for x in sources):
                            sources.append({"title": uc.get("title") or uc["url"], "url": uc["url"], "date": ""})
                    phase = "Searched the web…"
                elif kind == "usage":
                    cost += float(val.get("cost") or 0)
                elif kind == "tool_calls":
                    calls = val
                elif kind == "model":
                    answered_by = val
                elif kind in ("error", "unavailable"):
                    error, unavailable = val, kind == "unavailable"
            if error:
                retry = unavailable if is_gemini else ("404" in error or "Model" in error or "400" in error)
                if not text and not tried_fallback and fallback and fallback != model and retry:
                    tried_fallback = True
                    if is_gemini:
                        self._gemini_down[model] = time.time() + GEMINI_RECHECK_S
                        activity.warning("%s could not answer, %s takes over until it can again: %s", model,
                                         fallback, error)
                        gemini.drop_signatures(messages)
                        set_phase(f"{model} is unavailable, trying {fallback}…")
                    else:
                        phase = f"Fallback model {fallback}…"
                    model = fallback
                    rounds -= 1
                    continue
                writer.send({"kind": "error", "final": True, "msgId": msg_id, "text": error, "status": self.status()})
                return
            if not calls:
                break
            messages.append({"role": "assistant", "content": round_text or None, "tool_calls": [
                {"id": c.id or f"call_{i}", "type": "function", "function": {"name": c.name, "arguments": c.arguments},
                 **({"extra_content": c.extra} if c.extra else {})}
                for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                log.info("tool %s %s", c.name, {k: str(v)[:60] for k, v in (c.args() or {}).items()})
                activity.info("Tool: %s", c.name)
                result, _ = self.run_tool(ctx, c.name, c.args())
                messages.append({"role": "tool", "tool_call_id": c.id or f"call_{i}", "content": result})
        self.last_cost = None if is_gemini else round(cost, 6)
        if free and answered_by:
            model = answered_by
        no_cost = free and not cost
        self.ensure_marks(ctx, text)
        self._keep_turn(ctx, question, text)
        final_text = md_to_wow(text) if text else "(no answer)"
        writer.send({"kind": "answer", "final": True, "msgId": msg_id, "text": final_text, "sources": ctx.used_sources(text),
                     "actions": ctx.actions, "marks": ctx.marks, "marksReplace": ctx.replaces_marks,
                     "track": ctx.track, "route": ctx.route_edit, "untrack": ctx.untrack, "goTo": ctx.go_to, "routeUnchanged": self._checked_unchanged(msg, ctx),
                     "routePlanned": ctx.route_marked and bool(ctx.marks),
                     **({"marksMore": True} if getattr(ctx, "route_more", False) and ctx.route_marked else {}),
                     "cost": None if no_cost else self.last_cost, "model": model,
                     **({"costNote": "Gemini API"} if is_gemini else {"costNote": "no cost"} if no_cost else {}),
                     "links": self.links(text, ctx), "auction": ctx.auction_card(text), **ctx.auction_list(),
                     "secs": round(time.monotonic() - started, 1),
                     "status": self.status() if is_gemini else self._status_after(cost, requests if free else 0)})
        self.on_event(f"Last answer: {model}" + ("" if is_gemini else ", no cost" if no_cost
                                                 else f", ${self.last_cost:.4f}"))

    def _recheck_gemini(self, client: Gemini, model: str) -> None:
        """Probe a Gemini model that could not answer, in the background so that no question waits for it, at most
        every GEMINI_RECHECK_S; once it can answer, the next question uses it again."""
        if self._gemini_down.get(model, 0) > time.time():
            return
        self._gemini_down[model] = time.time() + GEMINI_RECHECK_S

        def check() -> None:
            problem = client.probe(model)
            if problem:
                log.info("%s still cannot answer: %s", model, problem)
            elif self._gemini_down.pop(model, None) is not None:
                activity.info("%s can answer again", model)
        threading.Thread(target=check, daemon=True, name="gemini-recheck").start()

    def links(self, md: str, ctx: TurnContext | None = None) -> list[dict]:
        """Item/spell/quest ids for names written in [brackets], so the game can make them clickable."""
        out = []
        found = {k.lower(): v for k, v in (ctx.ah_items if ctx else {}).items()}  # items from the auction search
        for name in bracket_names(md)[:40]:
            if name.lower() in found:
                out.append({"name": name, "kind": "item", "id": found[name.lower()]["id"]})
                continue
            if not self.kb:
                continue
            try:
                ent = self.kb.entity(name)
            except Exception:
                ent = None
            if ent:
                out.append({"name": name, "kind": ent[0], "id": ent[1]})
        return out

    def _keep_turn(self, ctx: TurnContext, question: str, answer: str) -> None:
        """The finished answer goes into the conversation, with what it did."""
        seen = ctx.snapshot.get("sinceLastAnswer") if isinstance(ctx.snapshot, dict) else None
        self.convs.append(ctx.conv, self._char, question, answer, ctx.did(),
                          seen=seen if isinstance(seen, list) else None)

    # ---------------------------------------------------------------- routes
    def _route_quests(self, ctx: TurnContext, zone: str) -> list[dict]:
        """The quests the character can pick up in a zone, as the game's quest database has them now, for planning
        a route: read once and kept for a few minutes. Nothing when the game does not answer (and then it is not asked
        again for a while: a route is not held up for it)."""
        key = (self._char, zone.strip().lower())
        if not key[1] or not self.fetch_route_quests:
            return []
        now = time.monotonic()
        held = self._zone_quests.get(key)
        if held and now - held[0] < ZONE_QUESTS_KEEP_S:
            return held[1]
        if key[1] in ctx.zones_read or now - self._zone_quests_failed < ZONE_QUESTS_RETRY_S:
            return []
        ctx.phase_cb(f"Reading the quests of {zone}…")
        res = self.game_call(ctx, "zone_quests", {"zone": zone}, timeout=ZONE_QUESTS_WAIT_S)
        ctx.zones_read.add(key[1])
        if not res or res.get("error"):
            self._zone_quests_failed = now
            return []
        quests = [{k: q[k] for k in _QUEST_KEYS if q.get(k) is not None}
                  for q in res.get("quests") or [] if isinstance(q, dict) and q.get("title") and q.get("where")]
        self._zone_quests[key] = (now, quests)
        return quests

    def _plan_route(self, ctx: TurnContext, args: dict, along: list | None = None) -> tuple[str, bool]:
        """The plan_route tool: the route computed from the game data, marked or proposed as the arguments say.
        ``along``: for the add-on's update, the quests the route took along on the way when it was planned."""
        from .route import plan_route
        ctx.phase_cb("Planning the route…")

        def titles(value) -> list[str]:
            return [str(v) for v in value] if isinstance(value, list) else ([value] if isinstance(value, str) and value else [])
        try:
            end = (float(args["end_x"]), float(args["end_y"]))
        except (KeyError, TypeError, ValueError):
            end = None
        new_quests = args.get("new_quests") is True or titles(args.get("new_quests"))
        skip, last, end_title = titles(args.get("skip")), titles(args.get("last")), str(args.get("end_title") or "")[:60]
        first, use = titles(args.get("first")), [u[:60] for u in titles(args.get("use"))]

        def point(hop: dict, key: str) -> tuple[float, float] | None:
            try:
                return float(hop[key + "_x"]), float(hop[key + "_y"])
            except (KeyError, TypeError, ValueError):
                return None
        then = args.get("then")
        hops = [{"zone": str(hop["zone"]).strip()[:60], "via": point(hop, "via"), "arrive": point(hop, "arrive"),
                 "via_title": str(hop.get("via_title") or "").strip()[:60]}
                for hop in ([then] if isinstance(then, dict) else then if isinstance(then, list) else [])
                if isinstance(hop, dict) and str(hop.get("zone") or "").strip()]
        extra = _extra_stops(args.get("extra"))
        # the quests of other zones looked up with zone_quests (in this answer, or when the route was planned) are
        # offered to the planner like the ones around the player
        wanted = {str(t).strip("[] ").lower() for t in (new_quests if isinstance(new_quests, list) else [])}
        # ... also the ones the route on the map was planned with in an earlier answer: asked to plan again, the model
        # names the same quests and does not look the zone up a second time. (Without this the quest of another zone
        # fell off the new plan, and the model put its giver back by hand, at the end of the route.)
        earlier = (self._plans().get(ctx.conv) or {}).get("offered") if getattr(ctx, "conv", None) else None
        for q in list(args.get("offered") or []) + list(earlier or []):
            if isinstance(q, dict) and q.get("title"):
                ctx.offered.setdefault(_quest_key(q), q)
        # A route is made with the quests of every zone it goes through, read from the game now: the ones that can
        # be done along it are on it from the start, each whole (its giver, what it wants, its turn-in), instead of
        # coming up one by one when the player gets there. An update reads nothing: it keeps what the route has.
        if along is None:
            player = ctx.snapshot.get("player") or {}
            for zone in dict.fromkeys([str(player.get("map") or player.get("zone") or "")] + [hop["zone"] for hop in hops]):
                for q in self._route_quests(ctx, zone):
                    ctx.offered.setdefault(_quest_key(q), q)
        else:
            wanted |= {str(t).strip("[] ").lower() for t in along if not isinstance(t, int)}
        wanted_ids = {t for t in along or [] if isinstance(t, int)}
        # (what the game says around the player has the distance; where a quest's places are comes from its zone)
        nearby = [dict({k: v for k, v in ctx.offered.get(_quest_key(q), {}).items()
                        if k in ("whereToGet", "turnIn", "type", "next", "nextId", "startsWhenTaken")}, **q)
                  for q in ctx.snapshot.get("nearbyQuests") or [] if isinstance(q, dict)]
        seen = {_quest_key(q) for q in nearby}
        far = [q for key, q in ctx.offered.items()
               if (along is None or str(q.get("title") or "").lower() in wanted or q.get("id") in wanted_ids)
               and key not in seen]
        # A quest that was in the quest log since the route was made and is not any more is through (turned in, or
        # dropped by the player): it is not a quest to pick up again, whatever the plan remembers of it.
        in_log = {q["id"] for q in ctx.snapshot.get("quests") or [] if isinstance(q, dict) and isinstance(q.get("id"), int)}
        had = {i for i in args.get("taken") or [] if isinstance(i, int)}
        # (what the app saw in the log with any message of this conversation, and what the game itself says is turned
        # in: a quest taken and turned in between two messages was never seen in the log)
        had |= {i for i in (self._plans().get(ctx.conv) or {}).get("taken") or [] if isinstance(i, int)}
        had |= {int(i) for i in ctx.snapshot.get("routeDone") or [] if isinstance(i, (int, float))}
        through = had - in_log
        nearby = [q for q in nearby if q.get("id") not in through]
        far = [q for q in far if q.get("id") not in through]
        # an update keeps the source the route has for each objective (a route planned anew chooses again)
        keep = (self._plans().get(getattr(ctx, "conv", None)) or {}).get("sources") if along is not None else None
        snapshot = dict(ctx.snapshot, nearbyQuests=nearby + far, through=sorted(through),
                        **({"keepSources": keep} if isinstance(keep, dict) and keep else {}))
        args = dict(args, offered=far) if far else {k: v for k, v in args.items() if k != "offered"}
        def planned(snap: dict) -> dict:
            return plan_route(snap, new_quests=new_quests, skip=skip, last=last, end=end,
                              end_title=end_title or None, first=first, then=hops, use=use, extra=extra,
                              avoid=[a[:60] for a in titles(args.get("avoid"))], along=along)
        route = planned(snapshot)
        if along is None:
            # the zones the route only passes through have quests too (a letter to the smith of the village on the
            # way): read them, and plan once more when they brought any
            more = []
            for hop in route.get("targets") or []:
                for q in self._route_quests(ctx, str(hop.get("zone") or "")):
                    key = _quest_key(q)
                    if key not in ctx.offered and key not in seen:
                        ctx.offered[key] = q
                        more.append(q)
            if more:
                far = far + more
                snapshot = dict(snapshot, nearbyQuests=nearby + far)
                route = planned(snapshot)
        text = route["text"]
        propose = args.get("propose") is True
        if route["stops"] and (propose or args.get("mark") is not False):
            # what the route holds and why goes into the conversation: an update later keeps to it
            def way(hop: dict) -> str:
                via, arrive = hop["via"], hop["arrive"]
                return (f"then {hop['zone']}"
                        + (f" by {hop['title'] or 'the way'} ({via[0]:g}, {via[1]:g})" if via else "")
                        + (f", arriving at {arrive[0]:g}, {arrive[1]:g}" if arrive else ""))
            choices = [f"left out {', '.join(f'[{t}]' for t in skip)}" if skip else "",
                       f"first {', '.join(f'[{t}]' for t in first)}" if first else "",
                       f"objectives from {', '.join(use)}" if use else "",
                       f"with the stops asked for: {', '.join(e['title'] for e in extra)}" if extra else "",
                       f"last {', '.join(f'[{t}]' for t in last)}" if last else "",
                       ("picking up every quest nearby" if new_quests is True else
                        f"picking up {', '.join(f'[{t}]' for t in new_quests)}" if new_quests else ""),
                       f"ends near {end_title or 'the given point'} ({end[0]:g}, {end[1]:g})" if end else "",
                       *[way(hop) for hop in route["ways"]]]
            ctx.notes = [n for n in ctx.notes if not n.startswith("planned the route")]
            ctx.notes.append("planned the route: " + ("; ".join(c for c in choices if c) or "nothing left out"))
        if propose and route["stops"]:
            stops = route["stops"][:MAX_ROUTE_MARKS]
            watched: list[dict] = []
            for stop in stops:
                mob = stop.get("mob")
                if mob and len(watched) < MAX_TRACKED and all(w["name"].lower() != mob.lower() for w in watched):
                    watched.append({"name": mob, **({"quest": stop["quest"]} if stop.get("quest") else {})})
            propose_route(ctx, {"op": "route", "track": watched, "stops": [
                {"title": f"{n}. {stop['title']}"[:60], "x": round(stop["x"], 1), "y": round(stop["y"], 1),
                 "zone": stop["zone"], **({"mob": stop["mob"]} if stop.get("mob") else {}),
                 **({"tasks": stop["tasks"]} if stop.get("tasks") else {})}
                for n, stop in enumerate(stops, 1)]})
            text += ("\nProposed to the player as a new route: a card with Accept and Reject. The marks on the map "
                     "stay until they accept it; do not mark these stops.")
        elif args.get("mark") is not False and route["stops"]:
            del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]  # the route is the whole set of marks of this answer
            ctx.route_edit = None  # and takes the place of the route that was edited
            ctx.edit_notes.pop("route", None)
            for stop in route["stops"][:MAX_ROUTE_MARKS]:
                if stop.get("mob"):
                    ctx.watch(stop["mob"], stop.get("quest"))
            for n, stop in enumerate(route["stops"][:MAX_ROUTE_MARKS], 1):
                ctx.marks.append({"title": f"{n}. {stop['title']}"[:60], "x": round(stop["x"], 1),
                                  "y": round(stop["y"], 1), "zone": stop["zone"],
                                  **({"mob": stop["mob"]} if stop.get("mob") else {}),
                                  **({"tasks": [{k: v for k, v in t.items() if k in ("kind", "quest", "id", "label", "zone", "spots", "at")}
                                                for t in stop["tasks"]]} if stop.get("tasks") else {}),
                                  # what the stop is for, when the player asked for it: its own words. A quest's
                                  # stop says it by its tasks (the note asked for along with it goes too, when it
                                  # adds something)
                                  **({"note": stop.get("asked_note") if stop.get("asked") else stop["what"]}
                                     if stop.get("extra") and (stop.get("asked_note") if stop.get("asked") else stop.get("what"))
                                     else {})})
                ctx.mark_adds.append(False)
            ctx.route_marked = True
            # the plan has more stops than the map holds: the add-on keeps the ones it has that the sent part leaves
            # out (they come later), and the line in the chat does not call them gone
            ctx.route_more = len(route["stops"]) > MAX_ROUTE_MARKS
            ctx.route_all = [str(st["title"]) for st in route["stops"]]
            # (of the quests read, the plan keeps the ones the route has: an update plans with those)
            # (all of each: around the player the game names a quest without its places, and an update that planned
            # with that alone lost the turn-in of a quest the route had whole)
            kept = wanted | {str(t).lower() for t in route.get("along") or [] if not isinstance(t, int)}
            kept_ids = {t for t in route.get("along") or [] if isinstance(t, int)}
            mine = [{k: q[k] for k in _QUEST_KEYS if q.get(k) is not None}
                    for q in nearby + far if str(q.get("title") or "").lower() in kept or q.get("id") in kept_ids]
            self._remember_plan(ctx.conv, dict({k: v for k, v in args.items() if k != "offered"}, extra=extra,
                                               taken=sorted(had | in_log), **({"offered": mine} if mine else {})), route)
            count = len(ctx.marks)
            which = f"The {count} stops are" if count == len(route["stops"]) else f"The first {count} stops are"
            text += (f"\n{which} marked on the map in this order (the marks of earlier answers are off the map); "
                     "do not mark them again. To mark other stops instead, call mark_on_map for each of them.")
        return text, False

    @staticmethod
    def _still_wanted(plan: dict, snapshot: dict) -> list[dict]:
        """The stops the player asked for (the plan's extra) that are not done: not on the route yet, on the map and
        not done, or sharing their place with a stop that is not done (then they are on the map under its name)."""
        lines = [(str(line), _MARK_LINE.match(str(line))) for line in snapshot.get("marks") or []]
        on_map = {m.group(1).lower(): line.endswith("(done)") for line, m in lines if m}

        def waits(e: dict) -> bool:
            if on_map.get(str(e["title"]).lower()) is False:
                return True
            return any(not line.endswith("(done)") and abs(float(m.group(2)) - e["x"]) <= 0.3
                       and abs(float(m.group(3)) - e["y"]) <= 0.3 for line, m in lines if m)
        return [e for e in plan.get("extra") or [] if isinstance(e, dict) and (not e.get("placed") or waits(e))]

    def _route_left(self, ctx: TurnContext) -> list[tuple[str, str]]:
        """The stops still to do of a planned route that is on the map: (title, zone). Empty when the marks on the
        map are no planned route, or all of it is done."""
        if not self._plans().get(getattr(ctx, "conv", None)):
            return []
        return [(m.group(1), z.group(1).strip() if z else "")
                for line, m, z in ((str(line), _MARK_LINE.match(str(line)), _MARK_ZONE.match(str(line)))
                                   for line in ctx.snapshot.get("marks") or [])
                if m and not line.endswith("(done)")]

    def _keep_asked(self, ctx: TurnContext, args: dict) -> tuple[dict, list[str]]:
        """plan_route as the model called it, with the stops the player asked for earlier that are not done: they stay
        on the route unless this call leaves them out by name (skip) or names them itself. Returns the arguments and
        the titles kept."""
        plan = self._plans().get(getattr(ctx, "conv", None)) or {}
        if not plan or not ctx.snapshot.get("marks"):
            return args, []
        named = _extra_stops(args.get("extra"))
        skip = {str(t).strip("[] ").lower() for t in (args.get("skip") if isinstance(args.get("skip"), list) else [])}

        def given(e: dict) -> bool:
            return any(str(n["title"]).lower() == str(e["title"]).lower()
                       or (abs(n["x"] - e["x"]) <= 0.3 and abs(n["y"] - e["y"]) <= 0.3
                           and str(n.get("zone") or "").lower() == str(e.get("zone") or "").lower()) for n in named)
        wanted = self._still_wanted(plan, ctx.snapshot)
        kept = [e for e in wanted if not given(e) and str(e["title"]).lower() not in skip][:max(0, 10 - len(named))]
        # (one this call names again keeps what the plan remembers of it and the call does not say: wanted first or
        # last, what is to be done there)
        again = []
        for n in args.get("extra") if isinstance(args.get("extra"), list) else []:
            was = next((e for e in wanted if isinstance(n, dict) and str(n.get("title") or "").strip("[] ").lower()
                        == str(e["title"]).lower()), None)
            again.append(dict({k: was[k] for k in ("when", "note", "tasks") if was.get(k) is not None}, **n) if was else n)
        if not kept and again == args.get("extra"):
            return args, []
        return dict(args, extra=again + kept), [str(e["title"]) for e in kept]

    def _keep_zones(self, ctx: TurnContext, args: dict) -> tuple[dict, list[str]]:
        """plan_route as the model called it, with the zones where the route on the map still has stops to do and a
        quest of the quest log (one this call does not skip) has its turn-in or something to get: they stay on the
        route, before the zone the call names last (where the player wants to end)."""
        from .route import _PLACE, _SOURCE, split_line
        if not self._plans().get(getattr(ctx, "conv", None)):
            return args, []
        then = args.get("then")
        hops = [then] if isinstance(then, dict) else list(then) if isinstance(then, list) else []
        waiting = self._waiting_zones(ctx, hops)
        if not waiting:
            return args, []
        skip = {str(t).strip("[] ").lower() for t in (args.get("skip") if isinstance(args.get("skip"), list) else [])}
        needed: set[str] = set()
        for q in ctx.snapshot.get("quests") or []:
            if not isinstance(q, dict) or str(q.get("title") or "").strip().lower() in skip:
                continue
            for place in str(q.get("turnIn") or "").split(";"):
                m = _PLACE.match(place.strip())
                if m:
                    needed.add(m.group("zone").strip().lower())
            for line in q.get("whereToGet") or []:
                for src in split_line(line)[1].split("; "):
                    m = _SOURCE.match(src)
                    if m:
                        needed.add(m.group("zone").strip().lower())
        kept = [z for z in waiting if z.lower() in needed]
        if not kept:
            return args, []
        added = [{"zone": z} for z in kept]
        return dict(args, then=hops[:-1] + added + hops[-1:] if hops else added), kept

    def _as_update(self, ctx: TurnContext, args: dict) -> tuple[dict, list]:
        """plan_route called in an update of the route: like the update the app makes itself, it adds no quest to
        pick up that the route was not planned with. The quests taken along are the plan's, and of the quests named,
        the ones the plan named (the player asks for others in a message of their own)."""
        plan = self._plans().get(getattr(ctx, "conv", None)) or {}
        along = [t if isinstance(t, int) else str(t) for t in plan.get("along") or []]
        asked, named = args.get("new_quests"), plan.get("new_quests")
        if asked not in (None, "", []) and named is not True:
            allowed = {str(t).strip("[] ").lower() for t in named or []} if isinstance(named, list) else set()
            args = dict(args, new_quests=[t for t in (asked if isinstance(asked, list) else [])
                                          if str(t).strip("[] ").lower() in allowed])
        # ... and it drops nothing the route has for the quest log: a quest the log held when the route was made is not
        # skipped (one just taken may be), and the zones where stops still wait stay on the route
        had = {int(i) for i in plan.get("taken") or [] if isinstance(i, (int, float))}
        held = {str(q.get("title") or "").strip().lower() for q in ctx.snapshot.get("quests") or []
                if isinstance(q, dict) and q.get("id") in had}
        skip = args.get("skip")
        if isinstance(skip, list):
            before = {str(t).strip("[] ").lower() for t in plan.get("skip") or []}
            args = dict(args, skip=[t for t in skip if str(t).strip("[] ").lower() not in held - before])
        then = args.get("then")
        hops = [then] if isinstance(then, dict) else list(then) if isinstance(then, list) else []
        waiting = self._waiting_zones(ctx, hops)
        if waiting:
            args = dict(args, then=hops + [{"zone": z} for z in waiting])
        return args, along

    def _waiting_zones(self, ctx: TurnContext, hops: list) -> list[str]:
        """The zones where stops of the route on the map still wait, that neither the player's zone nor the zones
        named are: a player who left a zone before its stops were done (a flight, a hearthstone, their own way) does
        not lose them at the next update."""
        player = ctx.snapshot.get("player") or {}
        here = str(player.get("map") or player.get("zone") or "").lower()
        named = {str(h.get("zone") or "").lower() for h in hops if isinstance(h, dict)}
        waiting: list[str] = []
        for line in ctx.snapshot.get("marks") or []:
            m = _MARK_ZONE.match(str(line))
            if m and not str(line).endswith("(done)"):
                z = m.group(1).strip()
                if z.lower() not in named | {here} and z not in waiting:
                    waiting.append(z)
        return waiting

    def _plans(self) -> dict:
        """The arguments of the last route planned in each conversation (kept across restarts of the app)."""
        if self._route_plans is None:
            try:
                self._route_plans = json.loads((config.data_dir() / "route_plans.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._route_plans = {}
        return self._route_plans

    def _remember_plan(self, conv: str, args: dict, route: dict) -> None:
        """How the route on the map was planned: the model's choices, and the zones as the planner took them (with the
        ways it filled in). An update plans it again the same way without asking the model for them."""
        plan = {k: args[k] for k in ("skip", "first", "last", "use", "avoid", "new_quests", "offered", "end_x", "end_y",
                                     "end_title", "removed") if args.get(k) not in (None, "", [])}
        # the quests taken along on the way (their givers stood by the route): an update keeps these and adds none
        along = list(dict.fromkeys(list(args.get("along") or []) + list(route.get("along") or [])))
        if along:
            plan["along"] = along
        if args.get("taken"):  # the quests the quest log held since the route was made
            plan["taken"] = list(args["taken"])
        # the source the route took for each objective, as the game data named it (with its place): an update keeps it
        sources = {k: v for k, v in (route.get("sources") or {}).items() if isinstance(v, str) and v}
        if sources:
            plan["sources"] = sources
        # the zones gone on into, not the ways: those are found again from where the player stands then (the model's
        # own way is kept only where the app knows none)
        plan["then"] = [{"zone": w["zone"], **({"via_title": w["title"]} if w.get("title") else {}),
                         **({"via_x": w["via"][0], "via_y": w["via"][1]} if w.get("via") else {}),
                         **({"arrive_x": w["arrive"][0], "arrive_y": w["arrive"][1]} if w.get("arrive") else {})}
                        for w in route.get("targets") or []]
        # the stops asked for stay on the route until they are done: "placed" says the route held them
        placed = {str(t).lower() for st in (route.get("stops") or [])[:MAX_ROUTE_MARKS] if st.get("extra")
                  for t in [st["title"]] + list(st.get("asked") or [])}
        extras = [dict(e, placed=True) if str(e["title"]).lower() in placed else {k: v for k, v in e.items() if k != "placed"}
                  for e in args.get("extra") or []]
        if extras:
            plan["extra"] = extras
        plans = self._plans()
        plans.pop(conv, None)
        plans[conv] = plan
        for old in list(plans)[:-30]:
            del plans[old]
        self._save_plans()

    SHOPPING_MAX = 20  # items on the shopping list of a conversation (and on its card)
    SHOPPING_NAME = "ForeverAI: Shopping"  # the Auctionator list it is saved as, when the model names none

    def _shop(self) -> dict:
        """The auction shopping list of each conversation: {"name", "items": [{"id", "name"}]} (kept across restarts
        of the app; a list of an older version is its items)."""
        if self._shopping is None:
            try:
                self._shopping = json.loads((config.data_dir() / "shopping.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._shopping = {}
        return self._shopping

    def _shopping_list(self, ctx: TurnContext, args: dict) -> tuple[str, bool]:
        """auctionator_list: the player's shopping list of this conversation, the Auction house card under the answer
        ("Search on the AH", "Save as list"). What the answer recommends is added to what earlier answers put on it,
        in its order; the whole list is priced from the newest scan by the app itself. An item leaves it when the
        player has it, when the character cannot use it (unless the player asked for it), or when the model takes it
        off by name; one the last scan has no price for stays, marked so."""
        def names(value) -> list[str]:
            value = [value] if isinstance(value, (str, int)) else value if isinstance(value, list) else []
            # (Auctionator's search strings use " ; ^ for exact, terms and categories)
            out = [re.sub(r'["^;]', "", str(x)).strip().strip("[]").strip()[:80] for x in value if x not in (None, "")]
            return [x for x in out if x]
        installed = {str(a.get("name")) for a in (ctx.snapshot.get("addons") or []) if isinstance(a, dict)}
        if installed and "Auctionator" not in installed:
            return "Not shown: Auctionator is not installed, so there is nothing to search or save the items in.", True
        conv = str(getattr(ctx, "conv", None) or "")
        stored = self._shop().get(conv)
        stored = stored if isinstance(stored, dict) else {"items": stored if isinstance(stored, list) else []}
        kept = [dict(it) for it in stored.get("items") or [] if isinstance(it, dict) and it.get("name")]
        title = re.sub(r"\s+", " ", re.sub(r'["^;|]', "", str(args.get("name") or ""))).strip()[:40]
        list_name = f"ForeverAI: {title}" if title else str(stored.get("name") or self.SHOPPING_NAME)
        new, remove = names(args.get("items")), {n.lower() for n in names(args.get("remove"))}
        if len({n.lower() for n in new}) > self.SHOPPING_MAX:
            return f"Not shown: a card holds at most {self.SHOPPING_MAX} items; give the ones worth buying.", True
        off: list[tuple[str, str]] = []  # (name, why it left the list)
        for it in list(kept):
            if str(it["name"]).lower() in remove or str(it.get("id")) in remove:
                kept.remove(it)
                off.append((it["name"], "taken off as you asked"))
        held = {str(it["name"]).lower() for it in kept} | {str(it.get("id")) for it in kept}
        new = [n for n in dict.fromkeys(new) if n.lower() not in held and n.lower() not in remove]
        new = list({n.lower(): n for n in reversed(new)}.values())[::-1]  # (one of each, whatever the case)
        if not kept and not new:
            if not off and not remove:
                ctx.ah_list = []  # (no card: not the items of this answer's searches either)
                return "Not shown: give the exact item names.", True
            ctx.ah_list = []
            self._shop().pop(conv, None)
            self._save_shop()
            return ("The shopping list is empty: no card is shown."
                    + ("" if not off else " Off the list: " + "; ".join(f"[{n}]: {why}" for n, why in off) + ".")), False
        # (the items this answer's searches priced need no second search)
        searched = {k.lower(): v for k, v in ctx.ah_items.items()}
        wanted = [{"ask": it.get("id") or it["name"], "name": it["name"], "id": it.get("id"), "anyway": it.get("anyway"),
                   "before": True} for it in kept]
        wanted += [{"ask": n, "name": n, "id": None, "before": False} for n in new]
        ask = [w["ask"] for w in wanted if str(w["name"]).lower() not in searched]
        res: dict | None = {"items": [], "missing": []}
        if ask:
            req, err = auction_request({"items": ask})
            if err:
                return err, True
            ctx.phase_cb("Checking the prices…")
            res = self.game_call(ctx, "auction_search", req)
        failed = None if res and not res.get("error") else str((res or {}).get("error") or "the game did not respond in time")
        priced = {} if failed else {str(it.get("name") or "").lower(): it for it in res.get("items") or [] if it.get("name")}
        by_id = {str(it.get("id")): it for it in priced.values() if it.get("id")}
        missing = {} if failed else {str(m.get("name") or "").lower(): m for m in res.get("missing") or [] if isinstance(m, dict)}
        missing.update({str(m.get("id")): m for m in ([] if failed else res.get("missing") or [])
                        if isinstance(m, dict) and m.get("id")})
        include = args.get("include_unusable") is True
        # (gear the player wears or has in the bags is had; materials and other things that stack are bought on top
        # of what the bags hold)
        bags = (ctx.snapshot.get("bags") or {}).get("items") if isinstance(ctx.snapshot.get("bags"), dict) else []
        owned = {n.lower() for n in bracket_names(json.dumps(ctx.snapshot.get("equipment") or "", ensure_ascii=False))}
        owned |= {str(b.get("item") or "").strip("[] ").lower() for b in bags or [] if isinstance(b, dict) and b.get("facts")}
        found: list[dict] = []
        for w in wanted:
            low = str(w["name"]).lower()
            hit = searched.get(low) or by_id.get(str(w["ask"])) or priced.get(low)
            if hit:
                it = {"id": hit.get("id"), "name": hit.get("name") or w["name"], "price": hit.get("price"),
                      **{k: hit[k] for k in ("cannotUse", "tooLowLevel") if hit.get(k)}}
            else:
                m = missing.get(str(w["ask"])) or missing.get(low) or {}
                it = {"id": w["id"] or m.get("id"), "name": w["name"] or m.get("name"), "price": None,
                      "why": "prices could not be checked now" if failed else str(m.get("why") or "no price in the last scan"),
                      **({"lastPrice": m["lastPrice"]} if m.get("lastPrice") else {})}
            it["before"], it["anyway"] = w["before"], bool(w.get("anyway")) or (include and not w["before"])
            why = it.get("cannotUse") or it.get("tooLowLevel")
            if why and not it["anyway"]:
                off.append((it["name"], f"the character cannot use it ({why}); do not recommend it, and when it matters "
                                        "say what it requires"))
            elif str(it["name"]).lower() in owned:
                off.append((it["name"], "the player has it now"))  # (bought, or got elsewhere)
            elif any(str(it["name"]).lower() == str(x["name"]).lower() for x in found):
                continue
            else:
                found.append(it)
        dropped = found[self.SHOPPING_MAX:]
        found = found[:self.SHOPPING_MAX]
        for it in found:
            if it.get("id") and it.get("price"):
                ctx.ah_items[str(it["name"])] = {"id": it["id"], "name": it["name"], "price": it["price"]}
        if res and res.get("ahOpen") is not None:
            ctx.ah_open = bool(res.get("ahOpen"))
        ctx.ah_list, ctx.ah_list_name = found, list_name
        self._shop()[conv] = {"name": list_name, "items": [
            {k: v for k, v in (("id", it.get("id")), ("name", it["name"]), ("anyway", it["anyway"] or None)) if v}
            for it in found]}
        self._save_shop()
        lines = [f"The player's shopping list, the Auction house card under this answer ({len(found)} items): with one "
                 f"click the player searches them on the auction house or saves them as the Auctionator shopping list "
                 f"\"{list_name}\". Prices of the newest scan" + (f" ({res['lastScan']})" if res and res.get("lastScan") else "")
                 + ":"]
        for it in found:
            price = it.get("price") or ("no price in the last scan (" + it["why"]
                                        + (f"; last seen at {it['lastPrice']}" if it.get("lastPrice") else "") + ")")
            line = f"- [{it['name']}]" + (f" (item {it['id']})" if it.get("id") else "") + f" — {price}"
            line += " (on the list from an earlier answer)" if it["before"] else " (added now)"
            if it.get("cannotUse") or it.get("tooLowLevel"):
                line += (f" — the character cannot use it ({it.get('cannotUse') or it.get('tooLowLevel')}); on the list "
                         "because the player asked")
            if not it.get("id") and not it.get("price") and not failed:
                line += " — the price data does not know this name: check that it is the item's exact name"
            lines.append(line)
        if not found:
            lines.append("- nothing")
        if dropped:
            lines.append(f"Not on the card (it holds {self.SHOPPING_MAX}): " + ", ".join(f"[{it['name']}]" for it in dropped))
        if off:
            lines.append("Off the list:")
            lines += [f"- [{n}]: {why}" for n, why in off]
        if failed:
            lines.append(f"The prices could not be checked now ({failed}): the card holds the items without prices. Say "
                         "so, and give no price as current.")
        else:
            lines.append("These are the prices to write; a price from an earlier answer that differs is out of date.")
        lines.append("The list is the player's for the whole conversation: an answer about one slot adds to it. Take an "
                     "item off (remove) only when the player does not want it or you now advise against it, and say so. "
                     "Say in a few words that the card is there; do not tell the player to make the list by hand.")
        return "\n".join(lines), False

    def _save_shop(self) -> None:
        try:
            (config.data_dir() / "shopping.json").write_text(json.dumps(self._shopping or {}, ensure_ascii=False),
                                                              encoding="utf-8")
        except OSError:
            log.exception("the shopping list could not be saved")

    def _language(self, conv: str, history: list[dict]) -> str | None:
        """The language the player writes in in this conversation, for the answers to the add-on's own messages
        (which are English): named once by a small model from the player's last own message. None when it cannot be
        told (no message of the player's yet, or no answer)."""
        if not self.name_language:
            return None
        if conv in self._languages:
            return self._languages[conv] or None
        from .prompt import ADDON_MESSAGES
        said = next((str(t.get("q") or "") for t in reversed(history or [])
                     if str(t.get("q") or "").strip() and not str(t.get("q")).startswith(ADDON_MESSAGES)), "")
        if not said:
            return None
        try:
            named = self._ask_plain("You name the language a message is written in. Answer with one English word, the "
                                    "name of the language, and nothing else. Names of game things in it (items, quests, "
                                    "places, abilities) are English in every player's message and do not count.",
                                    said[:400]) or ""
        except Exception:
            log.exception("the language of the conversation could not be named")
            named = ""
        word = named.strip().strip(".").split()[0] if named.strip() else ""
        self._languages[conv] = word[:30] if word.isalpha() else ""
        return self._languages[conv] or None

    def _with_flights(self, char: str, snapshot: dict) -> dict:
        """The flight masters the game last named for this character, when the game data of a question comes without
        them (asked while the add-on was still reading the quest database): a route planned without them knows no
        flight the character has. Kept across restarts of the app."""
        path = config.data_dir() / "flights.json"
        if self._flights is None:
            try:
                self._flights = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._flights = {}
        now = snapshot.get("flightMasters")
        if isinstance(now, list) and now:
            if self._flights.get(char) != now:
                self._flights[char] = now
                try:
                    path.write_text(json.dumps(self._flights, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    log.exception("the flight masters could not be saved")
            return snapshot
        kept = self._flights.get(char)
        if not kept:
            return snapshot
        log.info("the game data has no flight masters: the ones last seen for %s are used", char)
        return dict(snapshot, flightMasters=list(kept), flightMastersFrom="an earlier question")

    def _saw_log(self, conv: str, snapshot: dict) -> None:
        """The quests the quest log holds, noted with every message of a conversation that has a route: one that is
        gone from the log later is through, and an update does not plan it again."""
        plan = self._plans().get(conv)
        if plan is None:
            return
        seen = {q["id"] for q in snapshot.get("quests") or [] if isinstance(q, dict) and isinstance(q.get("id"), int)}
        seen |= {int(i) for i in snapshot.get("routeDone") or [] if isinstance(i, (int, float))}
        had = {i for i in plan.get("taken") or [] if isinstance(i, int)}
        if not seen <= had:
            plan["taken"] = sorted(had | seen)
            self._save_plans()

    def _amend_plan(self, ctx: TurnContext) -> None:
        """The model changed the planned route by hand in this answer: a stop taken off stays off, and a creature it
        marked for a quest's objective is where that objective comes from (use), when the route is planned again."""
        plan = self._plans().get(ctx.conv)
        if plan is None:
            return
        edit = ctx.route_edit or {}
        removed = [] if edit.get("clear") else [str(st["title"]) for st in edit.get("remove") or []]
        added = [m for m, by_hand in zip(ctx.marks, ctx.mark_adds) if by_hand]  # set with mark_on_map add
        use = [str(m["mob"]) for m in added if m.get("mob")
               and any(t.get("kind") == "objective" for t in m.get("tasks") or [])]
        # any other stop added by hand is one the player asked for: it stays on the route through its updates
        home = str((ctx.snapshot.get("player") or {}).get("map") or (ctx.snapshot.get("player") or {}).get("zone") or "")
        extras = list(plan.get("extra") or [])
        for m in added:
            title = re.sub(r"^\d+[.)]\s*", "", str(m["title"]))[:EXTRA_TITLE].strip()
            if m.get("mob") in use or any(str(e["title"]).lower() == title.lower() for e in extras):
                continue
            extras.append({"title": title, "zone": m.get("zone") or home, "x": m["x"], "y": m["y"], "placed": True,
                           **({"note": m["note"]} if m.get("note") else {}), **({"mob": m["mob"]} if m.get("mob") else {}),
                           **({"tasks": m["tasks"]} if m.get("tasks") else {})})
        gone = {t.lower() for t in removed}
        extras = [e for e in extras if str(e["title"]).lower() not in gone]
        new = dict(plan, removed=list(dict.fromkeys(list(plan.get("removed") or []) + removed)),
                   # (a source the model took off the route is not the chosen source any more)
                   use=list(dict.fromkeys([u for u in plan.get("use") or [] if str(u).strip().lower() not in gone] + use)),
                   extra=extras)
        new = {k: v for k, v in new.items() if v not in (None, "", [])}
        if new != plan:
            self._plans()[ctx.conv] = new
            self._save_plans()

    def _save_plans(self) -> None:
        plans = self._plans()
        try:
            path = config.data_dir() / "route_plans.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(plans, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _checked_unchanged(msg: dict, ctx: TurnContext) -> bool:
        """The model checked the planner's route and called no tool: the route on the map stays, no marks go."""
        if msg.get("check") and ctx.tool_calls == 0:
            del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]
            return True
        return False

    @staticmethod
    def _route_news(ctx: TurnContext) -> str:
        """What the planner changed in the route, in a few words: "Route updated: + Elder; − Giver." """
        old_at = [(m.group(1), float(m.group(2)), float(m.group(3)))
                  for line, m in ((str(line), _MARK_LINE.match(str(line))) for line in ctx.snapshot.get("marks") or [])
                  if m and not line.endswith("(done)")]
        old = [t for t, _, _ in old_at]
        new = [re.sub(r"^\d+[.)]\s*", "", m["title"]) for m in ctx.marks]
        planned = list(getattr(ctx, "route_all", None) or new)  # (what the plan has after the stops sent counts too)
        # (a stop is the same stop with or without what stands in brackets after its name, and a stop at the same
        # place under another name, two NPCs side by side, is the same stop too: the add-on takes it for it)
        def name(t: str) -> str:
            return re.sub(r"\s*\(.*$", "", t).strip().lower() or t.lower()
        renamed = {(name(t), name(re.sub(r"^\d+[.)]\s*", "", m["title"]))) for t, x, y in old_at for m in ctx.marks
                   if abs(m["x"] - x) <= 0.3 and abs(m["y"] - y) <= 0.3}
        added = [t for t in new if name(t) not in {name(o) for o in old} and not any(b == name(t) for _, b in renamed)]
        gone = [t for t in old if name(t) not in {name(n) for n in planned} and not any(a == name(t) for a, _ in renamed)]
        parts = ([("+ " + ", ".join(added))] if added else []) + ([("− " + ", ".join(gone))] if gone else [])
        return ("Route updated: " + "; ".join(parts) + ".") if parts else "Route updated."

    @staticmethod
    def _same_route(ctx: TurnContext) -> bool:
        """Are this answer's marks the stops that are left on the map? In whatever order: the planner starts from
        where the player stands now, and the add-on keeps the stops of an update in the order they have on the map
        (the route does not turn round because the player walked on)."""
        left = sorted((m.group(1).lower(), float(m.group(2)), float(m.group(3)))
                      for line, m in ((str(line), _MARK_LINE.match(str(line))) for line in ctx.snapshot.get("marks") or [])
                      if m and not line.endswith("(done)"))
        new = sorted((re.sub(r"^\d+[.)]\s*", "", m["title"]).lower(), m["x"], m["y"]) for m in ctx.marks)
        return len(left) == len(new) and all(a[0] == b[0] and abs(a[1] - b[1]) < 0.06 and abs(a[2] - b[2]) < 0.06
                                             for a, b in zip(left, new))

    def plan_again(self, ctx: TurnContext) -> str | None:
        """For the add-on's update of a route: the route planned again from the game data as it is now, the way the
        model planned it last in this conversation, and set as this answer's marks (the model can still change it).
        Returns the planner's text for the model; None when no plan is remembered or nothing is left to route."""
        plan = self._plans().get(ctx.conv)
        if plan is None or not ctx.snapshot.get("marks"):
            return None
        # a stop the player asked for that the route held is done once the map shows it done or no longer has it
        plan = dict(plan, extra=self._still_wanted(plan, ctx.snapshot))
        # The zones where stops of the route on the map still wait go on the route too: a player who left a zone
        # before its stops were done (a flight, a hearthstone) kept losing them at the next update.
        waiting = self._waiting_zones(ctx, list(plan.get("then") or []))
        if waiting:
            plan = dict(plan, then=list(plan.get("then") or []) + [{"zone": z} for z in waiting])
        # (an update adds no quest to pick up that the route was not planned with)
        text, _ = self._plan_route(ctx, dict(plan), along=[t if isinstance(t, int) else str(t) for t in plan.get("along") or []])
        if not ctx.marks:
            # nothing of the route is left to do (its quests are done, dropped, or wanted in zones it does not go to):
            # the stops still on the map go (a stop the player locked stays)
            left = [line for line in ctx.snapshot.get("marks") or []
                    if _MARK_LINE.match(str(line)) and not str(line).endswith("(done)")]
            if not left:
                return None
            ctx.route_edit = {"clear": True}
            return (text + "\nNothing of the route is left to do: the stops still on the map are taken off it. Say "
                    "what the player can do next, if anything.")
        # what the player took off the route stays off, and so does what the model took off by hand
        off = next((str(line).partition(": ")[2] for line in ctx.snapshot.get("marks") or []
                    if str(line).startswith("Taken off the route by the player: ")), "")
        gone = {name.strip().lower() for name in off.split(",") if name.strip()}
        by_model = [t for t in plan.get("removed") or [] if t.strip().lower() not in gone and any(
            re.sub(r"^\d+[.)]\s*", "", m["title"]).lower() == t.strip().lower() for m in ctx.marks)]
        gone |= {t.strip().lower() for t in by_model}

        def way(m: dict) -> bool:  # a way into a zone: the route needs it whenever it goes there
            return any(t.get("kind") == "travel" for t in m.get("tasks") or [])
        if gone:
            kept = [m for m in ctx.marks if way(m) or re.sub(r"^\d+[.)]\s*", "", m["title"]).lower() not in gone]
            for n, m in enumerate(kept, 1):
                m["title"] = f"{n}. {re.sub(r'^[0-9]+[.)] *', '', m['title'])}"[:60]
            mobs = {str(m.get("mob")).lower() for m in kept if m.get("mob")}
            ctx.marks[:], ctx.mark_adds[:] = kept, [False] * len(kept)
            ctx.track[:] = [t for t in ctx.track if t["name"].lower() in mobs]
            if off:
                text += "\nNot marked, because the player took them off the route: " + off + "."
            if by_model:
                text += "\nNot marked, because you took them off the route earlier: " + ", ".join(by_model) + "."
        return text if ctx.marks else None

    # ---------------------------------------------------------------- tools (both providers)
    def run_tool(self, ctx: TurnContext | None, name: str, args: dict) -> tuple[str, bool]:
        """Execute one model tool call for the answer in progress. Returns (text for the model, is_error)."""
        if ctx is None:
            return "No question in progress; the tool is not available.", True
        ctx.tool_calls += 1
        phase = claude_code.tool_phase(name)
        if name == "search_knowledge_base":
            query = str(args.get("query", ""))
            ctx.phase_cb(f"Searching knowledge base: {query[:40]}")
            text, found = run_kb_search(self.kb, query)
            if not found and ctx.web:
                text += KB_MISS_WEB
            for f in found:  # search hits count as sources only if the answer actually talks about them
                ctx.add_source(f)
                ctx.pre_urls.add(f["url"])
            return text, False
        if name == "propose_addon_change":
            action, text = validate_proposal(args, ctx.snapshot, ctx.known)
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "mark_on_map":
            mark, text = validate_mark(args)
            player = ctx.snapshot.get("player") or {}
            here = str(player.get("map") or player.get("zone") or "").strip().lower()
            there = str((mark or {}).get("zone") or args.get("zone") or "").strip()
            # A planned route with stops still to do is on the map, and this answer neither planned a route nor
            # cleared that one: the route stands. A mark joins it; it does not take its place.
            cleared = bool(ctx.route_edit and ctx.route_edit.get("clear"))
            left = [] if ctx.route_marked or cleared else self._route_left(ctx)
            joins = bool(left) or getattr(ctx, "route_update", False)
            reached = {here} | {z.lower() for _, z in left if z}
            if mark and joins and here and there and there.lower() not in reached:
                return (f"Not marked: {mark['title']} is in {there}, a zone the route on the map does not reach, and a "
                        f"mark has no way there. Call plan_route with it in extra and {{\"zone\": \"{there}\"}} in "
                        "then (the zones in the order the player goes): the planner makes the way there and back "
                        "stops of the route and keeps what the route has."), True
            # after edit_route a mark is an extra stop: replacing the route would undo the edit
            add = args.get("add") is True or ctx.route_edit is not None or joins
            if mark and ctx.route_marked and not add:  # the model marks its own choice of the route's stops
                del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]
            refused = mark and _already_marked(ctx, mark, add)
            if refused:
                return refused, True
            if mark and not add:  # the model's own marks instead of the route; an extra stop keeps it a route
                ctx.route_marked = False
            limit = MAX_ROUTE_MARKS if ctx.route_marked else MAX_MARKS  # a planned route holds more
            if mark and len(ctx.marks) >= limit:
                return f"Not marked: an answer holds at most {limit} marks, and {mark['title']} would be one more.", True
            if mark:
                ctx.marks.append(mark)
                ctx.mark_adds.append(add)
                text = text.rstrip(".") + (", added to your earlier marks." if add else
                                           "; the marks of your earlier answers are off the map.")
                if left and args.get("add") is not True:
                    text += (" The route on the map stays, with its stops still to do (" + ", ".join(t for t, _ in left[:8])
                             + "): this mark is one more stop of it. To take that route off the map, call edit_route "
                             "with clear first.")
                if add and ctx.route_marked:
                    text += (" It is the last stop of the route planned in this answer; to have it where it costs the "
                             "least way, call plan_route again with it in extra instead.")
                # what is to be done there: the stop leaves the route when that is done, not when it is reached
                quest, task = str(args.get("quest") or "").strip("[] ")[:80], str(args.get("task") or "")
                if quest and task in ("objective", "turn_in", "pick_up"):
                    # (with the quest's number, as the planner's stops have it: from the quest log, or from the quests
                    # offered for a pick-up)
                    pool = ctx.snapshot.get("nearbyQuests" if task == "pick_up" else "quests") or []
                    qid = next((q.get("id") for q in pool if isinstance(q, dict) and isinstance(q.get("id"), int)
                                and str(q.get("title") or "").lower() == quest.lower()), None)
                    mark["tasks"] = [{"kind": task, "quest": quest, **({"id": qid} if qid is not None else {})}]
                mob = str(args.get("mob") or "").strip("[] ")[:60]
                if mob:
                    watched = ctx.watch(mob)
                    if watched:
                        mark["mob"] = watched  # the stop knows its creature: taken off the route, the watch ends
                    text += (f" The game watches for {watched}: a living one in sight is marked on the screen and announced."
                             if watched else f" {mob} is not watched for: the game watches for at most {MAX_TRACKED} "
                                             "creatures at once.")
                stops = _map_stops(ctx.snapshot) if add and not ctx.replaces_marks else []
                if stops:
                    added = [{"title": re.sub(r"^\d+[.)]\s*", "", m["title"]), "x": m["x"], "y": m["y"],
                              "zone": m.get("zone") or "", "added": True, "tasks": m.get("tasks") or []} for m in ctx.marks]
                    view = _route_view(ctx, stops, added, ctx.route_state["remove"], ctx.route_state["first"])
                    at = next((n for n, st in enumerate(view, 1) if st is added[-1]), None)
                    if at:
                        text += (f" On the route it is stop {at} of {len(view)}, where it costs the least way"
                                 + (f" (after {view[at - 2]['title']})" if at > 1 else "")
                                 + "; name it in edit_route order to put it elsewhere, go_to to head there now.")
                if add:
                    self._amend_plan(ctx)  # an update of the route keeps the stop's creature as its objective's source
            return text, mark is None
        if name == "edit_route":
            ctx.phase_cb("Changing the route…")
            text, err = edit_route(ctx, args)
            if not err and args.get("propose") is not True:
                self._amend_plan(ctx)  # an update of the route keeps the change
            return text, err
        if name == "plan_route":
            args, kept = self._keep_asked(ctx, args)
            zones: list[str] = []
            if getattr(ctx, "route_update", False):
                text, err = self._plan_route(ctx, *self._as_update(ctx, args))
            else:
                args, zones = self._keep_zones(ctx, args)
                text, err = self._plan_route(ctx, args)
            if zones and not err:
                text += ("\nKept on the route, because it still has stops there for quests of the quest log: "
                         + ", ".join(zones) + ". Leave a quest out by its name in skip only when the player no longer "
                         "wants it done now.")
            if not err and ctx.snapshot.get("flightMastersFrom"):
                text += ("\nThe flight paths of the character are the ones the game named in an earlier question: "
                         "this one was asked before the add-on had read them.")
            if kept and not err:
                text += ("\nKept on the route, because the player asked for them earlier and they are not done: "
                         + ", ".join(kept) + ". Leave one out (its name in skip) only when the player no longer wants it.")
            return text, err
        if name == "save_memory":
            if phase:
                ctx.phase_cb(phase)
            item = self.memory.add(self._char, str(args.get("text", "")))
            if not item:
                return "Not saved (empty or already saved).", True
            ctx.notes.append(f"saved to memory: {item['text']}")
            return f"Saved to memory (id {item['id']}).", False
        if name == "delete_memory":
            try:
                item_id = int(args.get("id"))
            except (TypeError, ValueError):
                return "Give the entry id (a number).", True
            ok = self.memory.delete(self._char, item_id)
            if ok:
                ctx.notes.append(f"deleted memory entry {item_id}")
            return ("Deleted from memory." if ok else f"No entry with id {item_id}."), not ok
        if name == "fetch_url":
            url = str(args.get("url", ""))
            ctx.phase_cb(f"Reading page: {url[:45]}")
            text = run_fetch(url)
            if url.startswith("http") and not text.startswith(("Rejected", "Could not", "The site", "The page")):
                title = text.split("\n", 1)[0].removeprefix("Title: ").strip() if text.startswith("Title:") else url
                ctx.add_source({"title": title, "url": url, "date": ""})
            return text, False
        if name == "propose_vendor_action":
            action, text = validate_vendor(args, ctx.snapshot, self._vendor_guard(ctx, args))
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "propose_auctions":
            from . import market
            if not isinstance((ctx.snapshot.get("windows") or {}).get("auctionHouse"), dict):
                return ("Rejected: the auction house window is not open (no windows.auctionHouse). Tell the player to "
                        "open it at an auctioneer and ask again."), True
            picked, left = market.wanted_items(args, ctx.snapshot)
            if not picked:
                return "Rejected: nothing to post. " + "; ".join(f"{s['name']}: {s['why']}" for s in left), True
            ctx.phase_cb("Checking the auction house prices…")
            res = self.game_call(ctx, "auction_check", {"items": [{"id": p["bag"]["id"],
                                                                   "name": str(p["bag"].get("item")).strip("[]")}
                                                                  for p in picked]}, timeout=70.0)
            if not res or (res.get("error") and not res.get("items")):
                return ("The prices could not be checked: " + str((res or {}).get("error") or "the game did not respond in "
                        "time") + ". Nothing is proposed."), True
            action, text = market.build_post(picked, left, res, args.get("hours"), str(args.get("reason") or "").strip()[:300],
                                             next_action_id())
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "my_auctions":
            from . import market
            ctx.phase_cb("Reading your auctions…")
            res = self.game_call(ctx, "auction_owned", {})
            return market.format_owned(res), not res or bool(res.get("error"))
        if name == "propose_auction_cancel":
            from . import market
            ctx.phase_cb("Reading your auctions…")
            action, text = market.build_cancel(args, self.game_call(ctx, "auction_owned", {}), next_action_id())
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "propose_quest_abandon":
            action, text = validate_abandon(args, ctx.snapshot)
            if action:
                ctx.actions.append(action)
            return text, action is None
        if name == "search_auction_house":
            req, err = auction_request(args)
            if err:
                return err, True
            ctx.phase_cb("Searching the auction house…")
            res = self.game_call(ctx, "auction_search", req)
            ctx.remember_auction(res)
            text = format_auction(res)
            if res and not res.get("error") and res.get("items"):
                text += ("\nWhat you recommend buying goes into auctionator_list before you answer (the items you "
                         "advise against do not): the card the player gets lists exactly those.")
            return text, not res or bool(res.get("error"))
        if name == "compare_sell_prices":
            entries, req, err = sell_price_request(ctx.snapshot, args)
            if err:
                return err, True
            res = None
            if req:
                ctx.phase_cb("Comparing vendor and auction prices…")
                res = self.game_call(ctx, "auction_search", req)
                if res and res.get("ahOpen") is not None:
                    ctx.ah_open = bool(res.get("ahOpen"))
            return format_sell_prices(entries, res), False
        if name == "auctionator_list":
            return self._shopping_list(ctx, args)
        if name == "web_search":
            if not ctx.web_search:
                return "Web search is not available with this AI provider.", True
            query = str(args.get("query", "")).strip()[:200]
            if not query:
                return "Give a search query.", True
            ctx.phase_cb(f"Searching the web: {query[:40]}")
            text, found = ctx.web_search(query)
            for f in found:
                ctx.add_source({"title": f.get("title"), "url": f.get("url"), "date": ""})
            return text, not found and text.startswith("Web search failed")
        if name == "zone_quests":
            from . import ways
            zone = str(args.get("zone", "")).strip("[] ")[:60]
            if not zone:
                return "Give the zone (its English name).", True
            ctx.phase_cb(f"Reading the quests of {zone}…")
            res = self.game_call(ctx, "zone_quests", {"zone": zone})
            for q in (res or {}).get("quests") or []:
                if isinstance(q, dict) and q.get("title") and q.get("where"):
                    ctx.offered[_quest_key(q)] = {k: q[k] for k in _QUEST_KEYS if q.get(k) is not None}
            ctx.zones_read.add(zone.lower())
            return format_zone_quests(zone, res, ways.dungeons(zone=zone)), not res or bool(res.get("error"))
        if name == "gear_upgrades":
            slot = str(args.get("slot") or "").strip("[] ")[:30]
            ctx.phase_cb("Reading the gear list…")
            res = self.game_call(ctx, "gear_upgrades", {"slot": slot})
            return format_gear_upgrades(res), not res or bool(res.get("error"))
        if name == "way_to":
            from . import ways
            zone = str(args.get("zone") or "").strip("[] ")[:60]
            if not zone:
                return "Give the zone to get to (its English name).", True
            try:
                xy = (float(args["x"]), float(args["y"])) if args.get("x") is not None and args.get("y") is not None else None
            except (TypeError, ValueError):
                xy = None
            ctx.phase_cb(f"Finding the way to {zone}…")
            avoid = [str(a) for a in args.get("avoid") or [] if isinstance(a, str)][:6]
            return ways.describe_way(ways.world_for(ctx.snapshot or {}, avoid), ctx.snapshot or {}, zone, xy)
        if name == "map_notes":
            from . import ways
            ctx.phase_cb("Reading the map notes…")
            return format_map_notes(self.map_notes, str(args.get("zone") or "").strip("[] ")[:60],
                                    str(args.get("what") or "").strip("[] ")[:40], ctx.snapshot or {},
                                    ways.world_for(ctx.snapshot or {}).links)
        if name == "pet_abilities":
            from . import ways
            ctx.phase_cb("Reading the pet abilities…")
            return format_pet_abilities(self.pet_skills, str(args.get("family") or "").strip("[] ")[:40],
                                        str(args.get("ability") or "").strip("[] ")[:40], ctx.snapshot or {},
                                        ways.world_for(ctx.snapshot or {}))
        if name == "addon_options":
            addon = str(args.get("addon", "")).strip()[:60]
            query = str(args.get("query", "")).strip()[:120]
            if not addon:
                return "Give the add-on name (folder from the addons list).", True
            ctx.phase_cb(f"Checking {addon} options…")
            res = self.game_call(ctx, "addon_options", {"addon": addon, "query": query})
            remember_options(ctx.known, addon, res)
            return format_addon_options(addon, res), not res or bool(res.get("error"))
        return f"Unknown tool {name}", True

    MARK_CLAIM = re.compile(r"marked|on the map|on your map", re.IGNORECASE)
    COORDS = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*,\s*(\d{1,2}(?:\.\d)?)(?![\d.])")

    def ensure_marks(self, ctx: TurnContext, text: str) -> None:
        """The answer says a place is marked on the map but the model never called mark_on_map: mark the
        coordinates the answer gives, named after the NPC/quest/mob the game data has at that spot."""
        if ctx.marks or not text or not self.MARK_CLAIM.search(text):
            return
        names: dict[str, str] = {}
        snap = ctx.snapshot or {}
        for line in (snap.get("zoneNPCs") or []) + (snap.get("nearbyMobs") or []):
            if isinstance(line, str):
                m = self.COORDS.search(line.split("—", 1)[-1])
                if m:
                    names.setdefault(f"{float(m.group(1)):.1f},{float(m.group(2)):.1f}", re.split(r" <|, level| —", line)[0])
        for q in snap.get("quests") or []:
            if isinstance(q, dict):
                for field in ("questGiver", "turnIn"):
                    for part in str(q.get(field) or "").split(";"):
                        m = self.COORDS.search(part)
                        if m:
                            names.setdefault(f"{float(m.group(1)):.1f},{float(m.group(2)):.1f}", part.split(" (")[0].strip())
        seen = set()
        for m in self.COORDS.finditer(text):
            key = f"{float(m.group(1)):.1f},{float(m.group(2)):.1f}"
            if key in seen or len(ctx.marks) >= 4:
                continue
            seen.add(key)
            mark, _ = validate_mark({"title": names.get(key, "Point"), "x": m.group(1), "y": m.group(2)})
            if mark:
                ctx.marks.append(mark)
        if ctx.marks:
            log.info("answer claimed a map mark without calling the tool; marked %s", [m["title"] for m in ctx.marks])

    def tool_from_bridge(self, name: str, args: dict) -> tuple:
        log.info("tool %s %s", name, {k: str(v)[:60] for k, v in (args or {}).items()})
        activity.info("Tool: %s", name)
        text, is_error = self.run_tool(self._turn, name, args)
        side = self._claude_route
        session = side if side is not None and side.busy else self._claude
        if session is not None:  # the result stays in the session's context like the questions do
            session.sent_chars = getattr(session, "sent_chars", 0) + len(text)
        return text, is_error

    def screen_image(self, ctx: TurnContext) -> str | None:
        """The screen at the moment of the question (JPEG, base64), only when the player ticked "Screen"."""
        if not ctx.want_screen:
            return None
        jpeg = self.screen_for(ctx.msg_id)
        if not jpeg:
            log.warning("no screenshot kept for question %s", ctx.msg_id)
            return None
        import base64
        return base64.b64encode(jpeg).decode("ascii")

    def _vendor_guard(self, ctx: TurnContext, args: dict):
        """The check of a vendor sale against auction prices: what the player's auction add-on last saw for each item
        (asked of the game once for the whole list). Without that add-on only the item itself is judged."""
        from . import market
        from .prompt import PROFESSION_TOOLS
        merchant = (ctx.snapshot or {}).get("windows")
        if (args.get("op") != "sell" or args.get("player_named") is True
                or not (isinstance(merchant, dict) and merchant.get("merchant"))):
            return None
        by_id, by_name = market._bag_index(ctx.snapshot or {})
        ids = []
        for want in args.get("items") or []:
            b = by_id.get(int(want)) if isinstance(want, int) or str(want).isdigit() else by_name.get(str(want).strip("[] ").lower())
            if b and b["id"] not in ids:
                ids.append(b["id"])
        prices, scanned = {}, False
        if ids:
            ctx.phase_cb("Checking auction prices…")
            res = self.game_call(ctx, "auction_search", {"items": [{"id": i} for i in ids[:40]], "maxAgeDays": 30},
                                 timeout=VENDOR_PRICES_S) or {}
            prices = {int(it["id"]): it for it in res.get("items") or [] if isinstance(it, dict) and it.get("id")}
            scanned = bool(res.get("lastScan")) and int(res.get("dbItems") or 0) >= 300

        def guard(b: dict):
            price = prices.get(int(b["id"]))
            tool = PROFESSION_TOOLS.get(str(b.get("item") or "").strip("[]").lower())
            return market.vendor_guard(b, price, scanned, tool), market.vendor_reason(b, price)
        return guard

    def game_call(self, ctx: TurnContext, op: str, args: dict, timeout: float = 45.0) -> dict | None:
        """Ask the game (through the reply inboxes) to do something and wait for its answer frame."""
        rid = f"{ctx.msg_id}-{next(self._req_ids)}"
        done = threading.Event()
        slot = {"event": done, "result": None}
        req = {"id": rid, "op": op, **args}
        with self._lock:
            self._game_pending[rid] = slot
        ctx.writer.requests.append(req)
        ctx.writer.resend()
        deadline = time.monotonic() + timeout
        try:
            while not done.wait(0.2):
                if ctx.cancel.is_set() or time.monotonic() > deadline:
                    break
        finally:
            with self._lock:
                self._game_pending.pop(rid, None)
            if req in ctx.writer.requests:
                ctx.writer.requests.remove(req)
        return slot["result"]

    def _game_result(self, msg: dict) -> None:
        rid = str(msg.get("id") or "")
        with self._lock:
            slot = self._game_pending.get(rid)
        log.info("game result %s (%s)", rid, "waiting" if slot else "nobody waiting")
        if slot:
            res = msg.get("result")
            slot["result"] = res if isinstance(res, dict) else {"error": "empty result from the game"}
            slot["event"].set()

    # ---------------------------------------------------------------- Claude Code provider
    def _bridge_info(self) -> tuple[str, str]:
        if self._bridge is None:
            from .bridge import Bridge
            self._bridge = Bridge(self.tool_from_bridge)
        return self._bridge.mcp_url, self._bridge.token

    def _claude_system(self, system: str) -> str:
        s = self.store.settings
        system += ("\n\nThe foreverai MCP server provides: search_knowledge_base, search_auction_house, compare_sell_prices, "
                   "addon_options, propose_addon_change, propose_quest_abandon, propose_vendor_action, mark_on_map, "
                   "auctionator_list, plan_route, edit_route, save_memory, delete_memory, fetch_url. Read web pages "
                   "(e.g. build links) only with fetch_url — it gets past bot blocking.")
        if s.search_mode == "always":
            system += " Always check current information on the web (WebSearch) before answering."
        return system

    def _new_claude_session(self, exe: str, system: str, model: str | None = None,
                            effort: str | None = None) -> claude_code.ClaudeSession:
        s = self.store.settings
        url, token = self._bridge_info()
        # its own folder (it holds the address of this app's tools), also for sessions started in the same instant
        workdir = config.data_dir() / "claude_session" / f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        return claude_code.ClaudeSession(exe, workdir, system, model or s.claude_model, effort or s.claude_effort,
                                         s.search_mode, url, token)

    def _drop_claude(self, side: bool = False) -> None:
        if side:
            old, self._claude_route = self._claude_route, None
        else:
            old, self._claude = self._claude, None
        if old:
            old.close()
            import shutil
            threading.Thread(target=lambda: (time.sleep(2), shutil.rmtree(old.workdir, ignore_errors=True)),
                             daemon=True).start()

    def claude_session_for(self, system: str, conv: str, model: str | None = None,
                           effort: str | None = None) -> tuple[claude_code.ClaudeSession, bool] | None:
        """A running session for this conversation; (session, continues) — continues=False means the session is new
        to this conversation and the earlier turns must be sent along with the question.

        ``model`` or ``effort`` other than those of questions (the ones chosen for route updates) get a session of
        their own, kept beside the first, so that an update does not restart the session of questions. The two take
        turns in one conversation, and neither has seen the other's turns: the earlier turns go along then."""
        info = claude_code.detect_cached()
        if not info or not info.get("loggedIn"):
            return None
        s = self.store.settings
        model, effort = model or s.claude_model, effort or s.claude_effort
        side = (model, effort) != (s.claude_model, s.claude_effort)
        key = (model, effort, s.search_mode, system)
        with self._claude_lock:
            cur = self._claude_route if side else self._claude
            if cur and (not cur.alive() or cur.busy or cur.key != key or cur.turns >= MAX_SESSION_TURNS
                        or getattr(cur, "sent_chars", 0) >= MAX_SESSION_CHARS
                        or (cur.conv is not None and cur.conv != conv and not side)):
                self._drop_claude(side)
                cur = None
            if cur is None:
                cur = self._new_claude_session(info["path"], system, model, effort)
                if side:
                    self._claude_route = cur
                else:
                    self._claude = cur
            continues = cur.conv == conv and not side
            cur.conv = conv
            if side and self._claude is not None:
                self._claude.conv = None  # the session of questions has not seen this update
            return cur, continues

    def prewarm_later(self) -> None:
        threading.Thread(target=self._prewarm_safe, daemon=True, name="claude-prewarm").start()

    def _prewarm_safe(self) -> None:
        try:
            self.prewarm_claude()
        except Exception:
            log.exception("claude prewarm failed")

    def prewarm_claude(self) -> None:
        """Start a Claude Code session ahead of the first question (start-up takes ~1-2 s plus MCP)."""
        s = self.store.settings
        if s.provider != "claude_code":
            return
        info = claude_code.detect_cached()
        if not info or not info.get("loggedIn"):
            return
        messages = build_messages("", {}, [], [], now=datetime.now(), kb_date=self.kb.meta("kb_date") if self.kb else None,
                                  hotfix_date=self.kb.meta("hotfix_date") if self.kb else None)
        system = self._claude_system(messages[0]["content"])
        key = (s.claude_model, s.claude_effort, s.search_mode, system)
        with self._claude_lock:
            if self._claude and self._claude.alive() and self._claude.key == key:
                return
            self._drop_claude()
            self._claude = self._new_claude_session(info["path"], system)
            log.info("claude session prewarmed (%s, effort %s)", s.claude_model, s.claude_effort)
            activity.info("Claude session ready")

    def _answer_claude(self, msg: dict, ctx: TurnContext, messages: list[dict], conv: str,
                       again: bool = False) -> None:
        msg_id, writer, cancel, sources = ctx.msg_id, ctx.writer, ctx.cancel, ctx.sources
        question = str(msg.get("text") or "").strip()
        s = self.store.settings
        system = self._claude_system(messages[0]["content"])
        model = self.model_for(msg, "claude_code")
        got = self.claude_session_for(system, conv, model, self.effort_for(msg))
        if got is None:
            writer.send({"kind": "error", "final": True, "msgId": msg_id, "status": self.status(),
                         "text": "Claude Code is not available or not signed in. Sign in (run claude auth login in a terminal) or switch "
                                 "the AI provider to OpenRouter or Gemini in Settings."})
            return
        session, continues = got
        ctx.web = s.search_mode != "never"  # the session's WebSearch
        # a session that already holds this conversation only needs the new question
        _, prompt = claude_code.flatten_messages([messages[0], messages[-1]] if continues else messages)
        shot = self.screen_image(ctx)
        images = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": shot}}] if shot else []
        if shot:
            prompt += "\n\n" + SCREEN_NOTE
        started = time.monotonic()
        state = {"text": "", "phase": "Thinking…", "sent": (0.0, "", "")}

        def flush(force: bool = False) -> None:
            now = time.monotonic()
            t, last_text, last_phase = state["sent"]
            if cancel.is_set():
                return
            if not force and (now - started < FIRST_FLUSH_S or now - t < FLUSH_EVERY_S):
                return
            if state["text"] == last_text and state["phase"] == last_phase:
                return
            writer.send({"kind": "answer", "final": False, "msgId": msg_id, "phase": state["phase"],
                         "text": md_to_wow(state["text"]) if state["text"] else "",
                         "retryAfter": RETRY_TEXT_S if state["text"] else RETRY_THINKING_S})
            state["sent"] = (now, state["text"], state["phase"])

        def set_phase(p: str) -> None:
            if p:
                state["phase"] = p
                flush(force=True)
        ctx.phase_cb = set_phase
        final, broken, ended = None, False, None
        for kind, val in session.turn(prompt, images):
            if cancel.is_set():
                log.info("claude answer %s cancelled", msg_id)
                break
            if kind == "ended":
                ended = str(val)  # the session ended in the middle of the answer: see below
                break
            if kind == "unsettled":
                # The session had not finished its previous answer: it is in no known state. The question goes to
                # a new session (with the earlier turns), once; a route update must not be lost to it.
                if not again:
                    log.warning("claude session still busy with its previous answer: asking again on a new session")
                    broken = True
                    break
                kind = "error"
            if kind == "tool_error":
                # A tool of the app answered the model with an error, and no call has reached the app: the session
                # has lost its link to the tools. Whatever it writes now is written without them.
                if ctx.tool_calls == 0 and not again:
                    log.warning("claude session cannot reach the app's tools (%s): asking again on a new session", val)
                    broken = True
                    break
                continue
            if kind == "text":
                state["text"], state["phase"] = str(val), "Writing…"
                flush()
            elif kind == "phase":
                state["phase"] = str(val)
                flush()
            elif kind == "final":
                final = str(val or state["text"])
            elif kind == "error":
                writer.send({"kind": "error", "final": True, "msgId": msg_id, "text": str(val),
                             "status": self.status()})
                break
        # The session ended in the middle of the answer. The route plan_route made is put on the map, with a line of
        # the app's own; with nothing done yet, the question goes to a new session, once.
        if ended is not None and not cancel.is_set():
            if ctx.route_marked and ctx.marks:
                log.warning("claude session ended in the middle of an answer: the route made is sent with a line of the app's")
                with self._claude_lock:  # (the session is gone: the next question starts a new one)
                    if self._claude is session:
                        self._drop_claude()
                    elif self._claude_route is session:
                        self._drop_claude(side=True)
                final = ("The route is on the map: " + ", ".join(re.sub(r"^\d+[.)]\s*", "", m["title"]) for m in ctx.marks)
                         + ". (The AI stopped before it could say more: ask again for the details.)")
            elif ctx.tool_calls == 0 and not again:
                log.warning("claude session ended in the middle of an answer: asking again on a new session")
                broken = True
            else:
                writer.send({"kind": "error", "final": True, "msgId": msg_id, "text": ended, "status": self.status()})
        # The turn started without the app's tools (not connected yet) and none was used: the answer was written
        # without the planner and the marks. It is not shown; a new session is asked.
        missing = getattr(session, "mcp_missing", None)
        if final is not None and missing and ctx.tool_calls == 0 and not again and not cancel.is_set():
            log.warning("claude session had no %s: asking again on a new session", ", ".join(missing))
            final, broken = None, True
        # The model says the tools do not work and called none (it happens now and then with the tools there): the
        # player would get no route and no marks for nothing. Asked again on a new session, once.
        if final is not None and ctx.tool_calls == 0 and not again and not cancel.is_set() and _NO_TOOLS.search(final):
            log.warning("claude answer says the tools do not work, and called none: asking again on a new session")
            final, broken = None, True
        if final is None:
            with self._claude_lock:
                if self._claude is session:
                    self._drop_claude()
                elif self._claude_route is session:
                    self._drop_claude(side=True)
            if broken and not cancel.is_set():
                self._answer_claude(msg, ctx, messages, conv, again=True)
            return
        text = final or state["text"]
        self.ensure_marks(ctx, text)
        self._keep_turn(ctx, question, text)
        self.last_cost = None
        writer.send({"kind": "answer", "final": True, "msgId": msg_id,
                     "text": md_to_wow(text) if text else "(no answer)", "links": self.links(text, ctx),
                     "auction": ctx.auction_card(text), **ctx.auction_list(),
                     "marks": ctx.marks, "marksReplace": ctx.replaces_marks, "track": ctx.track,
                     "route": ctx.route_edit, "untrack": ctx.untrack, "goTo": ctx.go_to, "routeUnchanged": self._checked_unchanged(msg, ctx),
                     "routePlanned": ctx.route_marked and bool(ctx.marks),
                     **({"marksMore": True} if getattr(ctx, "route_more", False) and ctx.route_marked else {}),
                     "sources": ctx.used_sources(text), "actions": ctx.actions,
                     "model": model_name(model),
                     "costNote": "Claude subscription", "secs": round(time.monotonic() - started, 1),
                     "status": self.status()})
        self.on_event(f"Last answer: Claude Code {model}")

    def close(self) -> None:
        with self._claude_lock:
            self._drop_claude()
            self._drop_claude(side=True)

