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
from .inbox import pool_size, write_inbox
from .kb.store import KB
from .memory import ConversationStore, MemoryStore
from .gemini import Gemini
from .openrouter import FREE_MODELS, FREE_ROUTER, OpenRouter, is_free
from .prompt import build_messages, entity_names
from .tools import (TOOLS, auction_request, auctionator_list, format_addon_options, format_auction, format_sell_prices,
                    next_action_id, remember_options, run_fetch, run_kb_search, sell_price_request, validate_abandon,
                    validate_mark, validate_proposal, validate_vendor)
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
RETRY_TEXT_S = 2.0      # the addon reads the next inbox this soon while text is streaming
RETRY_THINKING_S = 3.0  # ... and this soon while the model is still thinking / using tools
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

    def send(self, payload: dict) -> bool:
        last = min(self.pool, self.base + self.lookahead - 1)
        if self.base > last:
            log.warning("inbox pool exhausted (base=%s pool=%s)", self.base, self.pool)
            return False
        with self._lock:
            self.seq += 1
            body = {"v": 1, "session": self.session, "seq": self.seq, **payload}
            if not payload.get("final"):
                self.last = dict(payload)
                if self.requests:
                    body["requests"] = list(self.requests)
                    body["retryAfter"] = 1.0  # the game should pick the request up quickly
            for n in range(self.base, last + 1):
                write_inbox(self.addons_dir, n, body)
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
_MARK_LINE = re.compile(r"^\d+\. (.+) — .*? (\d+(?:\.\d+)?), (\d+(?:\.\d+)?)(?: \((?:\d+ m|done)\))?$")


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


def _edit_answer_route(ctx: "TurnContext", remove: list[str], order: list[str], stop: list[str],
                       clear: bool) -> tuple[str, bool]:
    """edit_route after this answer marked a route (plan_route, then a change to it): the change applies to these marks,
    which replace the ones on the map, not to the route on the map."""
    def bare(title: str) -> str:
        return re.sub(r"^\d+[.)]\s*", "", title).strip("[] ").lower()

    def numbered(some: list[dict]) -> str:
        return ", ".join(f"{n}. {re.sub(r'^[0-9]+[.)]\s*', '', m['title'])}" for n, m in enumerate(some, 1))

    marks = list(ctx.marks)
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

    gone, first = pick(remove), pick(order)
    for missing in (gone, first):
        if isinstance(missing, str):
            return (f"Nothing changed: no stop called {missing}. The stops of the route you marked in this answer: "
                    f"{numbered(marks)}."), True
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
    return " ".join(said), False


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

    stops = [{"title": m.group(1), "x": float(m.group(2)), "y": float(m.group(3))}
             for m in (_MARK_LINE.match(str(line)) for line in ctx.snapshot.get("marks") or []) if m]
    # the stops this answer adds to the route (mark_on_map with add): the order can name them, they follow the rest
    added = [] if ctx.replaces_marks else [{"title": re.sub(r"^\d+[.)]\s*", "", m["title"]), "x": m["x"], "y": m["y"]}
                                           for m in ctx.marks]
    tracked = [re.split(r" \(also counts|: ", str(line))[0] for line in ctx.snapshot.get("tracked") or []]
    remove, order, stop = names(args.get("remove")), names(args.get("order")), names(args.get("stop_tracking"))
    clear = args.get("clear") is True
    if not (remove or order or stop or clear):
        return "Nothing changed: say what to change (remove, order, clear or stop_tracking).", True
    if ctx.marks and ctx.replaces_marks and args.get("propose") is not True:
        return _edit_answer_route(ctx, remove, order, stop, clear)
    if (remove or order or clear) and not stops:
        return "Nothing changed: nothing of yours is marked on the map.", True

    def numbered(some: list[dict]) -> str:
        return ", ".join(f"{n}. {s['title']}" for n, s in enumerate(some, 1))

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

    gone, first = pick(remove, stops), pick(order, stops + added)
    for missing in (gone, first):
        if isinstance(missing, str):
            return (f"Nothing changed: no stop called {missing}. The stops on the map: {numbered(stops)}."
                    + (f" Added in this answer: {', '.join(s['title'] for s in added)}." if added else "")), True
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
    if clear:
        edit = {"clear": True}
        said.append("The route is cleared: no marks of yours are on the map.")
        notes.append("cleared the route")
    elif gone or first:
        gone = [s for s in stops if s in gone]  # in the order of the route
        first = [s for s in first if s not in gone]
        left = first + [s for s in stops + added if s not in gone and s not in first]
        if gone:
            edit["remove"] = gone
            said.append("Taken off the route: " + ", ".join(s["title"] for s in gone) + ".")
            notes.append("took off the route: " + ", ".join(s["title"] for s in gone))
        if first:
            edit["order"] = left
            notes.append("put the route in another order")
        said.append("The route now: " + (numbered(left) if left else "no stops left") + ".")
    if propose:
        summary = " ".join(said + (["No longer watched for: " + ", ".join(unwatched) + "."] if unwatched else []))
        propose_route(ctx, {"op": "route_edit", "edit": edit, "untrack": unwatched, "summary": summary})
        return "Proposed to the player (a card with Accept and Reject), nothing changed yet: " + summary, False
    if edit:
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
        self.edit_notes: dict[str, list[str]] = {}  # what edit_route did, for did()
        self.phase_cb: Callable[[str], None] = lambda p: None
        self.pre_urls: set[str] = set()
        self.known: dict = {}  # addon options the game reported in this answer (validates proposals)
        self.want_screen = bool(msg.get("wantScreen"))
        self.ah_items: dict[str, dict] = {}  # items the auction search returned (name -> id, price)
        self.ah_open: bool | None = None
        self.ah_list: dict | None = None  # auctionator_list: {"name": the Auctionator list, "items": exact names}
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
        for a in self.actions:
            op = a.get("op")
            if op == "vendor_sell":
                out.append("proposed selling: " + ", ".join(str(i.get("name")) for i in a.get("items") or []))
            elif op == "vendor_repair":
                out.append(f"proposed repairing for {a.get('cost')}")
            elif op == "abandon_quest":
                out.append(f"proposed abandoning [{a.get('title')}]")
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
                self.ah_items[str(it["name"])] = {"id": it["id"], "name": it["name"], "price": it.get("price")}
        if res and res.get("ahOpen") is not None:
            self.ah_open = bool(res.get("ahOpen"))

    def auction_card(self, answer: str) -> list[dict] | None:
        """Items for the Auction house card ("Search on the AH", "Save as list"): the ones the model put on it with
        auctionator_list, priced where the auction search found them, or else the auction items the answer
        recommends (named in [brackets])."""
        found = {k.lower(): v for k, v in self.ah_items.items()}
        if self.ah_list:
            return [found.get(n.lower()) or {"name": n} for n in self.ah_list["items"]]
        named = {n.lower() for n in bracket_names(answer or "")}
        picked = [v for k, v in found.items() if k in named]
        return picked[:10] or None

    def auction_list(self) -> dict:
        """The name the card saves its items under in Auctionator, when the model gave the list; else the add-on
        uses its own."""
        return {"auctionList": self.ah_list["name"]} if self.ah_list else {}

    def add_source(self, src: dict) -> None:
        if src.get("url") and all(x["url"] != src["url"] for x in self.sources):
            self.sources.append({k: src.get(k) for k in ("title", "url", "date")})


class Service:
    def __init__(self, settings: config.SettingsStore, kb: KB | None, addons_dir: Path,
                 client_factory: Callable[[str], OpenRouter] = OpenRouter, lookahead: int = 200,
                 gemini_factory: Callable[[str], Gemini] = Gemini):
        self.store = settings
        self.kb = kb
        self.addons_dir = Path(addons_dir)
        self.client_factory = client_factory
        self.lookahead = lookahead
        self.assembler = FrameAssembler()
        self.gemini_factory = gemini_factory
        self.memory = MemoryStore(config.data_dir() / "memory.json")
        self.convs = ConversationStore(config.data_dir() / "convs")
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
        return msg.get("news") is True or str(msg.get("text") or "").startswith("[Quest log changed]")

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
        pre_hits = []
        if self.kb and not update:
            names = entity_names(snapshot)
            pre_hits = self.kb.search_titles(names, k=3)
        memory = self.memory.get(self._char)
        sources: list[dict] = [{"title": h.title, "url": h.url, "date": h.ts[:10]} for h in pre_hits]
        ctx = TurnContext(msg, writer, snapshot, cancel, sources)
        ctx.pre_urls = {x["url"] for x in sources}  # guessed from names in the game, not chosen by the model
        planned = self.plan_again(ctx) if update else None
        if planned and self._same_route(ctx):
            # the route planned again is the route on the map: nothing for a model to check, and nothing to say
            del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]
            writer.send({"kind": "answer", "final": True, "msgId": msg_id, "text": "Route checked: nothing to change.",
                         "routeUnchanged": True, "status": self.status()})
            activity.info("Route checked: nothing to change")
            return
        messages = build_messages(question, snapshot, history, pre_hits, now=datetime.now(),
                                  kb_date=self.kb.meta("kb_date") if self.kb else None,
                                  hotfix_date=self.kb.meta("hotfix_date") if self.kb else None,
                                  memory=memory, auto=bool(msg.get("auto")), route_update=update, planned=planned)
        self._turn = ctx
        try:
            if s.provider == "claude_code":
                self._answer_claude(msg, ctx, messages, conv)
            else:
                self._answer_api(msg, ctx, messages, "gemini" if s.provider == "gemini" else "openrouter")
        finally:
            if self._turn is ctx:
                self._turn = None

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
                     "track": ctx.track, "route": ctx.route_edit, "untrack": ctx.untrack,
                     "routePlanned": ctx.route_marked and bool(ctx.marks),
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
        self.convs.append(ctx.conv, self._char, question, answer, ctx.did())

    # ---------------------------------------------------------------- routes
    def _plan_route(self, ctx: TurnContext, args: dict) -> tuple[str, bool]:
        """The plan_route tool: the route computed from the game data, marked or proposed as the arguments say."""
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
        first = titles(args.get("first"))

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
        route = plan_route(ctx.snapshot, new_quests=new_quests, skip=skip, last=last, end=end,
                           end_title=end_title or None, first=first, then=hops)
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
                                  **({"tasks": stop["tasks"]} if stop.get("tasks") else {})})
                ctx.mark_adds.append(False)
            ctx.route_marked = True
            self._remember_plan(ctx.conv, args, route)
            count = len(ctx.marks)
            which = f"The {count} stops are" if count == len(route["stops"]) else f"The first {count} stops are"
            text += (f"\n{which} marked on the map in this order (the marks of earlier answers are off the map); "
                     "do not mark them again. To mark other stops instead, call mark_on_map for each of them.")
        return text, False

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
        plan = {k: args[k] for k in ("skip", "first", "last", "new_quests", "end_x", "end_y", "end_title")
                if args.get(k) not in (None, "", [])}
        plan["then"] = [{"zone": w["zone"], **({"via_title": w["title"]} if w["title"] else {}),
                         **({"via_x": w["via"][0], "via_y": w["via"][1]} if w["via"] else {}),
                         **({"arrive_x": w["arrive"][0], "arrive_y": w["arrive"][1]} if w["arrive"] else {})}
                        for w in route.get("ways") or []]
        plans = self._plans()
        plans.pop(conv, None)
        plans[conv] = plan
        for old in list(plans)[:-30]:
            del plans[old]
        try:
            path = config.data_dir() / "route_plans.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(plans, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _same_route(ctx: TurnContext) -> bool:
        """Are this answer's marks the stops that are left on the map, in the same order?"""
        left = [(m.group(1).lower(), float(m.group(2)), float(m.group(3)))
                for line, m in ((str(line), _MARK_LINE.match(str(line))) for line in ctx.snapshot.get("marks") or [])
                if m and not line.endswith("(done)")]
        new = [(re.sub(r"^\d+[.)]\s*", "", m["title"]).lower(), m["x"], m["y"]) for m in ctx.marks]
        return len(left) == len(new) and all(a[0] == b[0] and abs(a[1] - b[1]) < 0.06 and abs(a[2] - b[2]) < 0.06
                                             for a, b in zip(left, new))

    def plan_again(self, ctx: TurnContext) -> str | None:
        """For the add-on's update of a route: the route planned again from the game data as it is now, the way the
        model planned it last in this conversation, and set as this answer's marks (the model can still change it).
        Returns the planner's text for the model; None when no plan is remembered or nothing is left to route."""
        plan = self._plans().get(ctx.conv)
        if plan is None or not ctx.snapshot.get("marks"):
            return None
        text, _ = self._plan_route(ctx, dict(plan))
        if not ctx.marks:
            return None
        # what the player took off the route stays off
        off = next((str(line).partition(": ")[2] for line in ctx.snapshot.get("marks") or []
                    if str(line).startswith("Taken off the route by the player: ")), "")
        gone = {name.strip().lower() for name in off.split(",") if name.strip()}
        if gone:
            kept = [m for m in ctx.marks if re.sub(r"^\d+[.)]\s*", "", m["title"]).lower() not in gone]
            for n, m in enumerate(kept, 1):
                m["title"] = f"{n}. {re.sub(r'^[0-9]+[.)] *', '', m['title'])}"[:60]
            mobs = {str(m.get("mob")).lower() for m in kept if m.get("mob")}
            ctx.marks[:], ctx.mark_adds[:] = kept, [False] * len(kept)
            ctx.track[:] = [t for t in ctx.track if t["name"].lower() in mobs]
            text += "\nNot marked, because the player took them off the route: " + off + "."
        return text if ctx.marks else None

    # ---------------------------------------------------------------- tools (both providers)
    def run_tool(self, ctx: TurnContext | None, name: str, args: dict) -> tuple[str, bool]:
        """Execute one model tool call for the answer in progress. Returns (text for the model, is_error)."""
        if ctx is None:
            return "No question in progress; the tool is not available.", True
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
            # after edit_route a mark is an extra stop: replacing the route would undo the edit
            add = args.get("add") is True or ctx.route_edit is not None
            if mark and ctx.route_marked and not add:  # the model marks its own choice of the route's stops
                del ctx.marks[:], ctx.mark_adds[:], ctx.track[:]
            refused = mark and _already_marked(ctx, mark, add)
            if refused:
                return refused, True
            if mark and not add:  # the model's own marks instead of the route; an extra stop keeps it a route
                ctx.route_marked = False
            if mark and len(ctx.marks) >= MAX_MARKS:
                return f"Not marked: an answer holds at most {MAX_MARKS} marks, and {mark['title']} would be one more.", True
            if mark:
                ctx.marks.append(mark)
                ctx.mark_adds.append(add)
                text = text.rstrip(".") + (", added to your earlier marks." if add else
                                           "; the marks of your earlier answers are off the map.")
                # what is to be done there: the stop leaves the route when that is done, not when it is reached
                quest, task = str(args.get("quest") or "").strip("[] ")[:80], str(args.get("task") or "")
                if quest and task in ("objective", "turn_in", "pick_up"):
                    mark["tasks"] = [{"kind": task, "quest": quest}]
                mob = str(args.get("mob") or "").strip("[] ")[:60]
                if mob:
                    watched = ctx.watch(mob)
                    if watched:
                        mark["mob"] = watched  # the stop knows its creature: taken off the route, the watch ends
                    text += (f" The game watches for {watched}: a living one in sight is marked on the screen and announced."
                             if watched else f" {mob} is not watched for: the game watches for at most {MAX_TRACKED} "
                                             "creatures at once.")
            return text, mark is None
        if name == "edit_route":
            ctx.phase_cb("Changing the route…")
            return edit_route(ctx, args)
        if name == "plan_route":
            return self._plan_route(ctx, args)
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
            action, text = validate_vendor(args, ctx.snapshot)
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
            return format_auction(res), not res or bool(res.get("error"))
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
            lst, text = auctionator_list(args, ctx.snapshot)
            if not lst:
                return text, True
            ctx.ah_list = lst
            # the card's prices, and the names the price data does not know (a quest's name, a translated page's)
            priced = {k.lower() for k in ctx.ah_items}
            req, _ = auction_request({"items": [n for n in lst["items"] if n.lower() not in priced]})
            if req:
                ctx.phase_cb("Looking up the auction prices…")
                res = self.game_call(ctx, "auction_search", req)
                ctx.remember_auction(res)
                text += "\n\nWhat the last scan of the player's auction add-on says about them:\n" + format_auction(res)
            return text, False
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
        if self._turn is not None:
            self._turn.tool_calls += 1
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
        final, broken = None, False
        for kind, val in session.turn(prompt, images):
            if cancel.is_set():
                log.info("claude answer %s cancelled", msg_id)
                break
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
                     "route": ctx.route_edit, "untrack": ctx.untrack,
                     "routePlanned": ctx.route_marked and bool(ctx.marks),
                     "sources": ctx.used_sources(text), "actions": ctx.actions,
                     "model": model_name(model),
                     "costNote": "Claude subscription", "secs": round(time.monotonic() - started, 1),
                     "status": self.status()})
        self.on_event(f"Last answer: Claude Code {model}")

    def close(self) -> None:
        with self._claude_lock:
            self._drop_claude()
            self._drop_claude(side=True)

