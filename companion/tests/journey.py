"""A walk through the game with a route on the map, for tests: the real add-on (in the add-on simulator), the real
planner and update path of the app, a small piece of the world (NPCs, creatures, objects, quests, the crossings between
zones), and a player who goes where the arrow points and does there what the stop is for.

It is not a model of the game: the player teleports nowhere, walks in straight lines in steps, takes a quest from the
NPC the stop names, "kills" what an objective wants by standing at its stop, turns in at the NPC, and walks through a
crossing into the next zone. After every step the route is looked at: nothing that is done may come back, nothing may
be on it twice, no stop for a quest the player did not take may appear, the arrow must not get stuck.

    j = Journey(tmp_path, monkeypatch, WORLD, at=("Redridge Mountains", 21.8, 44.8), level=18)
    j.plan({"then": [{"zone": "Westfall"}]})      # the AI's plan_route call, scripted
    j.follow()                                     # walk the route to its end
    assert j.problems == []
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

from addonsim import Sim, install_game

# zone -> (map id, area id, map size in metres)
ZONES = {"Darkshore": (1439, 148, (5989, 3993)), "Ashenvale": (1440, 331, (5273, 3515)),
         "Elwynn Forest": (1429, 12, (3174, 2116)), "Westfall": (1436, 40, (3200, 2134)),
         "Redridge Mountains": (1433, 44, (1985, 1324)), "Duskwood": (1431, 10, (2469, 1646)),
         "Stormwind City": (1453, 1519, (1589, 1059))}
MAP_ZONE = {v[0]: k for k, v in ZONES.items()}
# the crossings between them, as Leatrix Maps has them: (zone, x, y, zone on the other side, x, y there)
CROSSINGS = [("Elwynn Forest", 93.2, 72.3, "Redridge Mountains", 3.3, 73.1),
             ("Elwynn Forest", 21.0, 79.6, "Westfall", 62.0, 17.9),
             ("Elwynn Forest", 32.2, 49.7, "Stormwind City", 74.5, 92.3),
             ("Redridge Mountains", 8.5, 88.1, "Duskwood", 94.2, 10.3),
             ("Darkshore", 37.5, 94.8, "Ashenvale", 29.7, 13.6)]


def _lua(value) -> str:
    """A Python value as a Lua literal."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return "{ " + ", ".join(f"[{_lua(k)}] = {_lua(v)}" for k, v in value.items()) + " }"
    return "{ " + ", ".join(_lua(v) for v in value) + " }"


def _plain(value):
    """What the simulator returns from Lua, with text as str and lists as lists."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, dict):
        keys = list(value)
        if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [_plain(value[k]) for k in sorted(keys)]
        return {_plain(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class World:
    """NPCs and creatures (who), objects (thing), items and quests, by number. A place is (zone, x, y); a creature or
    an object may have several. ``done``: the quests the character has turned in before (their next parts are open)."""

    def __init__(self):
        self.npcs, self.objects, self.items, self.quests, self.done = {}, {}, {}, {}, set()
        self.flight: dict[int, str] = {}  # flight masters: number -> the name of their flight path ("Town, Zone")

    def who(self, name: str, zone: str, x: float, y: float, level: int | None = None, more=(), nid: int | None = None,
            flags: int | None = None, sub: str | None = None, friendly: str | None = "AH") -> int:
        nid = nid or 100 + len(self.npcs)
        self.npcs[nid] = {"name": name, "zone": zone, "at": [(x, y), *more], "level": level,
                          "flags": flags if flags is not None else (0 if level else 2), "sub": sub, "friendly": friendly}
        return nid

    def thing(self, name: str, zone: str, x: float, y: float, more=(), oid: int | None = None) -> int:
        oid = oid or 500 + len(self.objects)
        self.objects[oid] = {"name": name, "zone": zone, "at": [(x, y), *more]}
        return oid

    def stuff(self, name: str, iid: int, drops=(), objects=(), vendors=()) -> int:
        """An item a quest wants: who drops it, which objects hold it, who sells it."""
        self.items[iid] = {"name": name, "npcDrops": list(drops), "objectDrops": list(objects), "vendors": list(vendors)}
        return iid

    def quest(self, qid: int, title: str, level: int, giver: int, end: int | None = None, kill=(), use=(), items=(),
              min_level: int = 1, after: int | None = None, then: int | None = None, text: str | None = None) -> int:
        """kill / use / items: [(creature, object or item number, how many)]; after: the quest that has to be turned in
        first; then: the quest that follows this one."""
        self.quests[qid] = {"title": title, "level": level, "min": min_level, "giver": giver, "end": end or giver,
                            "kill": list(kill), "use": list(use), "items": list(items), "after": after, "then": then,
                            "text": text}
        return qid

    def objectives(self, qid: int) -> list[tuple[str, int]]:
        """What the quest log lists for a quest: (label, how many)."""
        q = self.quests[qid]
        return ([(self.npcs[n]["name"], count) for n, count in q["kill"]]
                + [(self.objects[o]["name"], count) for o, count in q["use"]]
                + [(self.items[i]["name"], count) for i, count in q["items"]])

    def kills(self, qid: int) -> int:
        return len(self.quests[qid]["kill"])

    @classmethod
    def from_questie(cls, zones: list[str], level: int, race_bit: int = 8, class_bit: int = 4, near: int = 4) -> "World":
        """The real quests of these zones from the quest database installed for the game (tests/questiedb.py): the
        ones a level ``level`` Night Elf hunter can take (quest level up to ``near`` above, not grey), given and
        turned in by an NPC of these zones, with everything they want there. A quest whose earlier part is not among
        them counts as done before."""
        import re

        import questiedb as Q
        areas = {ZONES[z][1]: z for z in zones}
        w = cls()
        words = {w_: n for n, w_ in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                              "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}

        def text(v) -> str:
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v or "")

        def group(value, n: int):
            """The n-th group (from 1) of a list the database keeps as a list or, when sparse, as a map."""
            if isinstance(value, dict):
                return value.get(n) or []
            return value[n - 1] if isinstance(value, list) and len(value) >= n and value[n - 1] else []

        def where(entity) -> tuple[str, list] | None:
            for area, pts in Q.spawns(entity).items():
                if area in areas:
                    return areas[area], pts
            return None

        def person(nid: int) -> int | None:
            if nid in w.npcs:
                return nid
            e = Q.npc(nid)
            place = where(e)
            if not e or not place:
                return None
            zone, pts = place
            flags = int(e.get("npcFlags") or 0)
            hostile = not text(e.get("friendlyToFaction"))
            w.who(text(e["name"]), zone, pts[0][0], pts[0][1], level=int(e.get("minLevel") or 1) if hostile else None,
                  more=pts[1:12], nid=nid, flags=flags, sub=text(e.get("subName")) or None,
                  friendly=text(e.get("friendlyToFaction")) or None)
            return nid

        def obj(oid: int) -> int | None:
            if oid in w.objects:
                return oid
            e = Q.thing(oid)
            place = where(e)
            if not e or not place:
                return None
            w.thing(text(e["name"]), place[0], place[1][0][0], place[1][0][1], more=place[1][1:12], oid=oid)
            return oid

        def count(label: str, said: str, default: int) -> int:
            m = re.search(r"(\d+|" + "|".join(words) + r")\s+(?:\w+\s+){0,3}?" + re.escape(label.split()[-1]), said, re.I)
            if not m:
                return default
            n = m.group(1).lower()
            return int(n) if n.isdigit() else words.get(n, default)

        gray = level - 5 - level // 10
        for qid in Q.quest_ids():
            q = Q.quest(qid)
            if not q or not q.get("startedBy"):
                continue
            races, classes = int(q.get("requiredRaces") or 0), int(q.get("requiredClasses") or 0)
            if (races and not races & race_bit) or (classes and not classes & class_bit):
                continue
            qlevel, minimum = int(q.get("questLevel") or 0), int(q.get("requiredLevel") or 0)
            if minimum > level or qlevel > level + near or qlevel <= gray:
                continue
            givers = [n for n in Q.ids(group(q.get("startedBy"), 1)) if person(n)]
            ends = [n for n in Q.ids(group(q.get("finishedBy"), 1)) if person(n)]
            if not givers or not ends:
                continue
            said = " ".join(text(t) for t in q.get("objectivesText") or [])
            objs = q.get("objectives") or {}
            objs = {int(k): v for k, v in (objs.items() if isinstance(objs, dict) else enumerate(objs, 1))}
            kill, use, items, whole = [], [], [], not objs.get(5)
            for row in objs.get(1) or []:
                n = person(Q.ids(row[:1])[0]) if row else None
                if n:
                    kill.append((n, count(w.npcs[n]["name"], said, 5)))
                else:
                    whole = False
            for row in objs.get(2) or []:
                o = obj(Q.ids(row[:1])[0]) if row else None
                if o:
                    use.append((o, 1))
                else:
                    whole = False
            for row in objs.get(3) or []:
                iid = Q.ids(row[:1])[0] if row else None
                it = Q.item(iid) if iid else None
                if not it:
                    whole = False
                    continue
                drops = [n for n in Q.ids(it.get("npcDrops"))[:6] if person(n)]
                boxes = [o for o in Q.ids(it.get("objectDrops"))[:6] if obj(o)]
                sellers = [n for n in Q.ids(it.get("vendors"))[:6] if person(n)]
                if not (drops or boxes or sellers):
                    whole = False
                    continue
                w.stuff(text(it["name"]), iid, drops, boxes, sellers)
                items.append((iid, count(text(it["name"]), said, 1)))
            if not whole:
                continue
            before = (Q.ids(q.get("preQuestSingle")) or Q.ids(q.get("preQuestGroup")) or [None])[0]
            w.quest(qid, text(q["name"]), qlevel, givers[0], ends[0], kill, use, items, min_level=minimum, after=before,
                    then=int(q["nextQuestInChain"]) if q.get("nextQuestInChain") else None, text=said or None)
        for qid, q in w.quests.items():  # an earlier part that is not here: the character has done it
            if q["after"] and q["after"] not in w.quests:
                w.done.add(q["after"])
        w.flight_masters(zones)
        return w

    def flight_masters(self, zones: list[str]) -> None:
        """The flight masters of these zones (from the quest database), as NPCs of this world."""
        import questiedb as Q
        areas = {ZONES[z][1]: z for z in zones}
        for key in Q._store():
            if not (key.startswith("X-Npc-") and key.endswith("-S")):
                continue
            nid = key[len("X-Npc-"):-2]
            if not nid.isdigit():
                continue
            e = Q.npc(int(nid))
            if not e or not int(e.get("npcFlags") or 0) & 8 or "A" not in str(e.get("friendlyToFaction") or b"").replace("b'", ""):
                continue
            for area, pts in Q.spawns(e).items():
                if area in areas and int(nid) not in self.npcs:
                    name = e["name"].decode() if isinstance(e["name"], bytes) else str(e["name"])
                    sub = e.get("subName")
                    self.who(name, areas[area], pts[0][0], pts[0][1], nid=int(nid), flags=int(e["npcFlags"]),
                             sub=sub.decode() if isinstance(sub, bytes) else sub, friendly="A")
                    self.flight[int(nid)] = name

    def lua(self) -> str:
        """The quest add-on (Questie) and the quest log of the game, as the add-on reads them."""
        def spawns(e):
            return {ZONES[e["zone"]][1]: [list(p) for p in e["at"]]}
        npcs = {n: {"name": e["name"], "spawns": spawns(e), "zoneID": ZONES[e["zone"]][1], "minLevel": e["level"],
                    "maxLevel": e["level"], "npcFlags": e["flags"], "subName": e["sub"],
                    "friendlyToFaction": e["friendly"], "factionID": 1 if e["friendly"] else 99}
                for n, e in self.npcs.items()}
        objects = {o: {"name": e["name"], "spawns": spawns(e)} for o, e in self.objects.items()}
        items = {i: dict(e) for i, e in self.items.items()}
        quests = {}
        for qid, q in self.quests.items():
            texts = [q["text"]] if q["text"] else (
                [f"Kill {count} {self.npcs[n]['name']}." for n, count in q["kill"]]
                + [f"Find {self.objects[o]['name']}." for o, count in q["use"]]
                + [f"Bring {count} {self.items[i]['name']}." for i, count in q["items"]])
            quests[qid] = {"name": q["title"], "questLevel": q["level"], "requiredLevel": q["min"],
                           "zoneOrSort": ZONES[self.npcs[q["giver"]]["zone"]][1], "startedBy": [[q["giver"]]],
                           "finishedBy": [[q["end"]]], "objectivesText": texts or ["Speak with them."],
                           "objectives": [[[n] for n, _ in q["kill"]], [[o] for o, _ in q["use"]], [[i] for i, _ in q["items"]]],
                           "after": q["after"], "nextQuestInChain": q["then"]}
        areas = {area: zone for zone, (_, area, _) in ZONES.items()}
        maps = {mid: area for _, (mid, area, _) in ZONES.items()}
        return f"""
TEST_log, TEST_done = {{}}, {_lua({q: True for q in self.done})}
local quests, npcs, objects, items = {_lua(quests)}, {_lua(npcs)}, {_lua(objects)}, {_lua(items)}
local areas, areaOfMap = {_lua(areas)}, {_lua(maps)}
local function entry(id) for _, q in ipairs(TEST_log) do if q.id == id then return q end end end
C_QuestLog.GetNumQuestLogEntries = function() return #TEST_log end
C_QuestLog.GetInfo = function(i) local q = TEST_log[i] return q and {{ title = q.title, questID = q.id, isHeader = false, level = q.level }} end
C_QuestLog.IsComplete = function(id) local q = entry(id) return q and q.complete or false end
C_QuestLog.GetQuestObjectives = function(id) local q = entry(id) return q and q.objectives or {{}} end
C_QuestLog.IsQuestFlaggedCompleted = function(id) return TEST_done[id] == true end
local db = {{ NPCPointers = {{}}, factionTemplate = {{ [1] = 0, [99] = 1 }},  -- (the flags as QuestieDB has them for Forever)
  npcFlags = {{ GOSSIP = 1, QUEST_GIVER = 2, VENDOR = 4, FLIGHT_MASTER = 8, TRAINER = 16, INNKEEPER = 128, BANKER = 256,
               AUCTIONEER = 4096, STABLEMASTER = 8192, REPAIR = 16384 }} }}
for id in pairs(npcs) do db.NPCPointers[id] = true end
function db.GetQuest(a, b) return quests[b or a] end
function db.GetNPC(a, b) return npcs[b or a] end
function db.GetObject(a, b) return objects[b or a] end
function db.GetQuestTagInfo() end
function db.QueryNPCSingle(id, field) local n = npcs[id] return n and n[field] end
function db.QueryObjectSingle(id, field) local o = objects[id] return o and o[field] end
function db.QueryItemSingle(id, field) local i = items[id] return i and i[field] end
function db.QueryQuestSingle(id, field) local q = quests[id] return q and q[field] end
local available = {{ __availableQuests = {{}} }}
function TEST_offer()
  local out = {{}}
  for id, q in pairs(quests) do
    if not entry(id) and not TEST_done[id] and (not q.after or TEST_done[q.after]) then out[id] = true end
  end
  available.__availableQuests = out
end
TEST_offer()
local zones = {{ GetAreaIdByUiMapId = function(_, mapID) return areaOfMap[mapID] end,
                GetUiMapIdByAreaId = function(_, area) for m, a in pairs(areaOfMap) do if a == area then return m end end end }}
QuestieLoader = {{ ImportModule = function(_, name)
  if name == "QuestieDB" then return db elseif name == "AvailableQuests" then return available
  elseif name == "ZoneDB" then return zones end
  error("no module") end }}
C_Map.GetAreaInfo = function(id) return areas[id] end
local unitName = UnitName
UnitName = function(u) if u == "npc" then return TEST_npc end return unitName(u) end
TEST_trace = {{}}
ForeverAI.Log = function(kind, line) TEST_trace[#TEST_trace + 1] = tostring(kind) .. ": " .. tostring(line) end
"""


class _Model:
    """Stands in for the AI model when the app asks it (an update the planner could not make alone, a check): it
    keeps what the app planned and says so in a line. What a real model does is tried with the real model."""

    def __init__(self, key):
        self.key = key

    def credits(self):
        return 5.0

    def models(self):
        return [{"id": "journey/model", "in": 0.0, "out": 0.0}]

    def key_info(self):
        return {"label": "journey"}

    def chat_stream(self, model, messages, tools=None, search_mode="auto", max_tokens=1800):
        yield "text", "Route checked."
        yield "usage", {"cost": 0.0}
        yield "done", "stop"


class _Writer:
    """Where the app writes its answer: kept, and handed to the add-on by the journey."""

    def __init__(self):
        self.sent, self.requests = [], []

    def send(self, payload: dict) -> None:
        self.sent.append(payload)

    def resend(self) -> None:
        pass


# the points of the flight map as the game names them, for the flight masters of the journeys
FLIGHT_POINTS = {"Thor": "Sentinel Hill, Westfall", "Dungar Longdrink": "Stormwind, Elwynn",
                 "Ariena Stormfeather": "Lakeshire, Redridge", "Felicia Maline": "Darkshire, Duskwood",
                 "Gryth Thurden": "Ironforge, Dun Morogh", "Thorgrum Borrelson": "Thelsamar, Loch Modan"}


class Journey:
    STEP_M = 45  # how far the player gets between two looks of the add-on

    def __init__(self, tmp_path: Path, monkeypatch, world: World, at: tuple[str, float, float], level: int = 18,
                 live: bool = False):
        """``live``: the real model of the player's settings plans the route and checks the updates (it costs)."""
        from foreverai import claude_code, config, ways
        from foreverai.service import Service
        self.world, self.problems, self.trace, self.updates, self.walked = world, [], [], [], 0.0
        self.log: dict[int, list[bool]] = {}  # the quests in the quest log: which objectives are done
        self.done: set[int] = set(world.done)
        game = tmp_path / "game"
        install_game(game, pool=120)
        addons = game / "Interface" / "AddOns"
        (addons / "Leatrix_Maps").mkdir(parents=True, exist_ok=True)
        rows: dict[str, list[str]] = {}
        for a, ax, ay, b, bx, by in CROSSINGS:
            rows.setdefault(a, []).append(f'{{"Arrow", {ax}, {ay}, L["{b}"], nil, arTex, nil, nil, nil, nil, nil, 0, {ZONES[b][0]}}},')
            rows.setdefault(b, []).append(f'{{"Arrow", {bx}, {by}, L["{a}"], nil, arTex, nil, nil, nil, nil, nil, 0, {ZONES[a][0]}}},')
        (addons / "Leatrix_Maps" / "Leatrix_Maps_Icons.lua").write_text("\n".join(
            f"\t\t--[[{zone}]] [{ZONES[zone][0]}] = {{\n\t\t\t" + "\n\t\t\t".join(lines) + "\n\t\t}," for zone, lines in rows.items()),
            encoding="utf-8")
        self.live = live
        monkeypatch.setenv("FOREVERAI_DATA", str(tmp_path / "data"))
        monkeypatch.setattr(Service, "fetch_route_quests", True)
        if live:
            import shutil
            (tmp_path / "data").mkdir(parents=True, exist_ok=True)
            shutil.copy(Path(__file__).resolve().parents[1] / "data" / "settings.json", tmp_path / "settings.json")
            self.svc = Service(config.SettingsStore(tmp_path / "settings.json"), None, addons)
        else:
            monkeypatch.setattr(claude_code, "detect", lambda force=False: None)
            monkeypatch.setattr(config, "env_path", lambda: tmp_path / ".env")
            (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnop\n")
            self.svc = Service(config.SettingsStore(tmp_path / "settings.json"), None, addons, client_factory=_Model)
        monkeypatch.setattr(ways, "use_icons", ways.use_icons)  # (the crossings above stay the world's until the test ends)
        self.svc.game_call = self._game
        self.tools: list[tuple[str, dict]] = []  # what the model called (live)
        run_tool = self.svc.run_tool

        self.results: list[tuple[str, str]] = []  # ... and what each call answered (its first lines)

        def recorded(ctx, name, args):
            self.tools.append((name, dict(args or {})))
            text, err = run_tool(ctx, name, args)
            self.results.append((name, ("ERROR " if err else "") + str(text)[:1800]))
            return text, err
        self.svc.run_tool = recorded
        self.sim = Sim(game).start()
        self.place(*at)
        self.sim._wow.player.level = level
        self.sim.lua(world.lua())
        self.sim.lua("ForeverAI.Npcs.BuildIndex()")
        self.sim.slash("/fai")
        self.sim.lua("ForeverAI.db.autoRoute = true")
        # the stops the creature tracker takes off (an objective it saw done) go into the story
        self.sim.lua("""local done = ForeverAI.Map.Done
          ForeverAI.Map.Done = function(names)
            local o = {} for k in pairs(names) do o[#o + 1] = k end
            table.sort(o)
            TEST_trace[#TEST_trace + 1] = "tracker: done with " .. table.concat(o, ", ")
            return done(names)
          end""")
        self.sim.run(32)  # past the loading screen: the quest log counts

    def close(self) -> None:
        from foreverai import ways
        self.sim.close()
        self.svc.close()
        ways.use_icons(None)

    # ------------------------------------------------------------------------------------------- the player
    @property
    def zone(self) -> str:
        return MAP_ZONE[self.sim._wow.player.mapID]

    @property
    def at(self) -> tuple[float, float]:
        return self.sim._wow.player.x * 100, self.sim._wow.player.y * 100

    def place(self, zone: str, x: float, y: float) -> None:
        p = self.sim._wow.player
        # (a Python str would reach the add-on as userdata, which the game data cannot hold)
        p.mapID, p.zone, p.x, p.y = ZONES[zone][0], zone.encode(), x / 100, y / 100
        p.subzone = b""

    def meters(self, zone: str, a, b) -> float:
        w, h = ZONES[zone][2]
        return math.hypot((a[0] - b[0]) / 100 * w, (a[1] - b[1]) / 100 * h)

    def walk(self, x: float, y: float) -> None:
        """On foot, in a straight line, the add-on looking on."""
        start, far = self.at, self.meters(self.zone, self.at, (x, y))
        steps = max(1, math.ceil(far / self.STEP_M))
        for k in range(1, steps + 1):
            self.place(self.zone, start[0] + (x - start[0]) * k / steps, start[1] + (y - start[1]) * k / steps)
            self.sim.run(2.1)
            self._ack()
        self.walked += far

    def _push(self, *events: bytes) -> None:
        rows = []
        for qid, done in self.log.items():
            q = self.world.quests[qid]
            texts = [{"text": f"{count if ok else 0}/{count} {label}" + (" slain" if n < len(q["kill"]) else ""), "finished": ok}
                     for n, ((label, count), ok) in enumerate(zip(self.world.objectives(qid), done))]
            rows.append({"id": qid, "title": q["title"], "level": q["level"], "complete": all(done), "objectives": texts})
        self.sim.lua(f"TEST_log = {_lua(rows)} TEST_done = {_lua({q: True for q in self.done})} TEST_offer()")
        for event in events:
            self.sim._wow.fire(event)

    def take(self, qid: int) -> None:
        self.log[qid] = [False] * len(self.world.objectives(qid))
        self.note(f"took [{self.world.quests[qid]['title']}]")
        self._push(b"QUEST_ACCEPTED", b"QUEST_LOG_UPDATE")

    def finish(self, qid: int, label: str | None = None) -> None:
        """An objective of a quest is done (all of them with no label)."""
        for n, (name, _) in enumerate(self.world.objectives(qid)):
            if label is None or name == label:
                self.log[qid][n] = True
        self.note(f"did {label or 'everything'} for [{self.world.quests[qid]['title']}]")
        self._push(b"QUEST_LOG_UPDATE")

    def turn_in(self, qid: int) -> None:
        del self.log[qid]
        self.done.add(qid)
        self.note(f"turned in [{self.world.quests[qid]['title']}]")
        self._push(b"QUEST_TURNED_IN", b"QUEST_LOG_UPDATE")

    def know(self, *names: str) -> None:
        """The flight paths the character has: of these flight masters (as the add-on keeps them after a flight map).
        A path has the name of its point on the flight map, as in the game ("Sentinel Hill, Westfall"), not the flight
        master's: that is what a route has to say."""
        zone_of = {e["name"]: e["zone"] for e in self.world.npcs.values()}
        paths = {n: FLIGHT_POINTS.get(n) or f"{n}'s Roost, {zone_of.get(n, self.zone)}" for n in names}
        self.sim.lua(f"""local t = ForeverAI.char.taxi or {{ known = {{}}, masters = {{}} }}
          for name, path in pairs({_lua(paths)}) do t.known[path] = true t.masters[name] = path end
          t.v, t.at = 2, time()
          ForeverAI.char.taxi = t""")

    def _flight_master(self, within: float = 40.0) -> dict | None:
        for nid in self.world.flight:
            e = self.world.npcs[nid]
            if e["zone"] == self.zone and self.meters(self.zone, self.at, e["at"][0]) <= within:
                return e
        return None

    def _open(self, who: dict) -> None:
        """The player talks to a flight master: the flight map opens, the path is theirs."""
        self.know(who["name"])
        self.sim.lua(f"TEST_npc = {_lua(who['name'])}")
        self.sim._wow.fire(b"TAXIMAP_OPENED")
        self.sim._wow.fire(b"TAXIMAP_CLOSED")
        self.sim.lua("TEST_npc = nil")
        self.note(f"opened the flight path of {who['name']}")

    def note(self, line: str) -> None:
        self.trace.append(f"{self.zone} {self.at[0]:.1f}, {self.at[1]:.1f}: {line}")

    # ------------------------------------------------------------------------------------------- the app
    def _game(self, ctx, op, args, timeout=45.0):
        if op != "zone_quests":
            return None
        return _plain(self.sim.to_py(self.sim.lua("return ForeverAI.Collect.ZoneQuests(...)", str(args.get("zone") or ""))))

    def _questions(self) -> list[dict]:
        """What the add-on asked the app and is not answered yet (the ones seen while the player walked first)."""
        waiting, self._asked = list(getattr(self, "_asked", [])), []
        return waiting + [m for m in self.sim.sent_messages() if m.get("type") == "question"]

    def _ack(self) -> None:
        """The app says at once that it is at a question (the add-on hears it, and waits for the answer as long as it
        takes); the answer comes when the player is at the next stop."""
        new = [m for m in self.sim.sent_messages() if m.get("type") == "question"]
        for msg in new:
            self.sim.deliver({"kind": "answer", "final": False, "msgId": msg["msgId"], "text": "", "phase": "Thinking…",
                              "retryAfter": 3.0})
        self._asked = list(getattr(self, "_asked", [])) + new

    def ask_live(self, text: str) -> list[str]:
        """The player asks in their own words, and the real model answers (it calls plan_route itself)."""
        self.sim.ask(text)
        msg = None
        for _ in range(60):
            self.sim.run(0.5)
            msg = next(iter(self._questions()), None)
            if msg:
                break
        assert msg, "the add-on sent no question"
        writer = _Writer()
        self.svc._answer(msg, writer, threading.Event())
        answer = writer.sent[-1]
        self.answer = answer.get("text")
        self.sim.deliver(answer)
        self.sim.run(4)
        self.note("asked: " + text)
        self.note("route: " + ", ".join(self.titles()))
        self.check("after planning")
        return self.titles()

    def say(self, text: str) -> list[str]:
        """Half way through the route the player asks for something in their own words (the real model answers). The
        words ask for nothing to go: whatever was still to do on the route is on it after the answer."""
        before = [s for s in self.stops() if not s["done"]]
        titles = self.ask_live(text)
        after = [s for s in self.stops() if not s["done"]]
        has = {f"{t['kind']}:{t['id'] or t['quest'] or t['zone']}:{t['label']}" for s in after for t in s["tasks"]}
        for s in before:
            for t in s["tasks"]:
                if t["kind"] in ("objective", "turn_in") and t["id"] in self.log and not t.get("did") \
                        and f"{t['kind']}:{t['id'] or t['quest'] or t['zone']}:{t['label']}" not in has:
                    self.problem(f"after the player's words ({text!r}) {s['title']} lost what was to do there: "
                                 f"{t['kind']} [{t['quest']}]")
            if not s["tasks"] and not any(o["title"] == s["title"] or self._same(o, s) for o in after):
                self.problem(f"after the player's words ({text!r}) the stop {s['title']} is gone from the route")
        return titles

    def plan(self, args: dict | None = None, text: str = "Plan a route through my quests.") -> list[str]:
        """The player asks for a route, and the AI calls plan_route with ``args`` (scripted: what a model would name)."""
        from foreverai.service import TurnContext
        self.sim.ask(text)
        msg = None
        for _ in range(60):
            self.sim.run(0.5)
            msg = next(iter(self._questions()), None)
            if msg:
                break
        assert msg, "the add-on sent no question"
        ctx = TurnContext(msg, _Writer(), msg.get("snapshot") or {}, threading.Event(), [])
        ctx.conv = str(msg.get("conv") or "default")
        text_out, err = self.svc.run_tool(ctx, "plan_route", dict(args or {}))
        assert not err, text_out
        self.plan_text = text_out
        self.sim.deliver({"kind": "answer", "final": True, "msgId": msg["msgId"], "text": "The route is on the map.",
                          "marks": ctx.marks, "marksReplace": True, "track": ctx.track, "routePlanned": True})
        self.sim.run(4)
        self.note("route: " + ", ".join(self.titles()))
        self.check("after planning")
        return self.titles()

    def pump(self, seconds: float = 8.0) -> None:
        """Time passes; what the add-on asks the app (an update of the route, its check) is answered."""
        left = seconds
        while left > 0:
            self.sim.run(1.0)
            left -= 1.0
            for msg in self._questions():
                before = self.stops()
                writer = _Writer()
                if msg.get("check") and self.live:  # the real model looks at what the planner did
                    self.svc._answer(msg, writer, threading.Event())
                elif msg.get("check"):  # the model looks at the planner's route and changes nothing
                    writer.send({"kind": "answer", "final": True, "msgId": msg["msgId"], "routeUnchanged": True,
                                 "text": "Route checked: nothing to change."})
                else:
                    self.svc._answer(msg, writer, threading.Event())
                assert writer.sent, f"the app did not answer {msg.get('text')!r}"
                answer = writer.sent[-1]
                if answer.get("kind") == "error":  # the route is not updated: the player is left with the old one
                    self.problem(f"an update got an error: {answer.get('text')}")
                traced = len(self._addon_trace())
                self.sim.deliver(answer)
                # (the add-on reads it at its own pace: after a long wait, every few seconds)
                for _ in range(12):
                    self.sim.run(1)
                    if self.sim.lua("return ForeverAI.pending == nil or ForeverAI.pending.msgId ~= %d" % msg["msgId"]):
                        break
                self.sim.run(2)
                after = self.stops()
                # a quest was taken while it was written: the add-on puts none of it on the map or in the chat, and
                # asks again (the answer to that is judged)
                stale = any("an update was overtaken" in line for line in self._addon_trace()[traced:])
                self.updates.append({"asked": msg.get("text"), "said": answer.get("text"),
                                     "new": [s["title"] for s in after if not any(self._same(s, o) for o in before)],
                                     "gone": [s["title"] for s in before if not s["done"] and not any(self._same(s, o) for o in after)]})
                self.note(f"update ({msg.get('text')}): {answer.get('text')}")
                self._judge_update(before, after)
                for s in after:
                    old = next((o for o in before if not o["done"] and self._same(s, o)), None)
                    if old and not s["done"] and (abs(old["x"] - s["x"]) > 0.35 or abs(old["y"] - s["y"]) > 0.35):
                        self.problem(f"the update moved {s['title']} from {old['x']:.1f}, {old['y']:.1f} to {s['x']:.1f}, {s['y']:.1f}")
                # the line in the chat says what happened to the route on the map, not what the app sent
                said = str(answer.get("text") or "")
                if said.startswith("Route updated: + ") and not stale:
                    # (what the add-on put on the route: a stop it ticked off at once, done since the question, is too.
                    # A stop's name can hold a comma: the names known are taken out of the line, what is left is not on it)
                    put = [line.partition("new: ")[2].partition("; gone:")[0] for line in self._addon_trace()[traced:]
                           if line.startswith("route: update:")]
                    rest = said[len("Route updated: + "):].split("; −")[0].rstrip(".")
                    for title in sorted({s["title"] for s in after if not s["done"]}, key=len, reverse=True):
                        rest = rest.replace(title, "")
                    missing = [t.strip() for t in rest.split(",") if t.strip() and not any(t.strip() in p for p in put)]
                    if missing:
                        self.problem(f"the news says the route got {', '.join(missing)}, which is not on it")
                left = max(left, 6.0)  # its check comes a moment later

    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        if a["zone"] != b["zone"] or a["for"] != b["for"]:
            return False
        return bool(a["for"]) or (abs(a["x"] - b["x"]) < 0.35 and abs(a["y"] - b["y"]) < 0.35)

    # ------------------------------------------------------------------------------------------- the route
    def stops(self) -> list[dict]:
        raw = _plain(self.sim.to_py(self.sim.lua("""
          local out, cur = {}, nil
          for i, s in ipairs(ForeverAI.Map.List()) do if s.current then cur = i end end
          for i, mk in ipairs(ForeverAICharDB.marks or {}) do
            local tasks = {}
            for _, t in ipairs(mk.tasks or {}) do
              tasks[#tasks + 1] = { kind = t.kind, id = t.id or 0, quest = t.quest or "", label = t.label or "", zone = t.zone or "",
                                    did = t.did == true }
            end
            out[#out + 1] = { title = (tostring(mk.title):gsub("^%d+[%.%)]%s*", "")), map = mk.mapID, x = mk.x, y = mk.y,
                              done = mk.done == true, current = i == cur, tasks = tasks, n = i }
          end
          return out""")) or [])
        for s in raw:
            s["zone"] = MAP_ZONE.get(s["map"], "?")
            s["tasks"] = s.get("tasks") or []
            s["for"] = sorted(f"{t['kind']}:{t['id'] or t['quest'] or t['zone']}" + (f":{t['label']}" if t["label"] else "")
                              for t in s["tasks"])
        return raw

    def titles(self) -> list[str]:
        return [s["title"] + (" (done)" if s["done"] else "") for s in self.stops()]

    def problem(self, line: str) -> None:
        self.problems.append(line)
        self.note("PROBLEM: " + line)

    def check(self, when: str) -> None:
        """What must hold of the route on the map at any time."""
        left = [s for s in self.stops() if not s["done"]]
        seen: dict[str, str] = {}
        order: dict[int, int] = {}
        rank = {"pick_up": 1, "objective": 2, "turn_in": 3}
        whats = _plain(self.sim.to_py(self.sim.lua(
            "local out = {} for i, s in ipairs(ForeverAI.Map.List()) do out[i] = s.what or '' end return out")) or [])
        for s, what in zip(self.stops(), whats):
            parts = [p.strip().lower() for p in str(what).replace(";", ",").split(",") if p.strip()]
            if len(parts) != len(set(parts)):
                self.problem(f"{when}: {s['title']} says a thing twice: {what}")
        for s in left:
            if s["title"].lower().startswith("the way round"):
                self.problem(f"{when}: a way round nobody walked twice is on the route")
            for t in s["tasks"]:
                qid = t["id"]
                if t.get("did"):  # done already, at a stop that stays for the rest: the stop says only what is left
                    continue
                if t["kind"] == "pick_up" and (qid in self.log or qid in self.done):
                    self.problem(f"{when}: {s['title']} is to pick up [{t['quest']}], which is "
                                 + ("in the log" if qid in self.log else "turned in"))
                if t["kind"] in ("objective", "turn_in") and qid in self.done:
                    self.problem(f"{when}: {s['title']} is for [{t['quest']}], which is turned in")
                if t["kind"] in rank:
                    key = f"{t['kind']}:{qid}:{t['label']}"
                    if key in seen and t["kind"] != "objective":
                        self.problem(f"{when}: [{t['quest']}] has two stops to {t['kind'].replace('_', ' ')}: {seen[key]}, {s['title']}")
                    seen[key] = s["title"]
                    if rank[t["kind"]] < order.get(qid, 0):
                        self.problem(f"{when}: {s['title']} ({t['kind']} [{t['quest']}]) comes after a later step of that quest")
                    order[qid] = max(order.get(qid, 0), rank[t["kind"]])

    def _judge_update(self, before: list[dict], after: list[dict]) -> None:
        """An update of the route may add what a quest in the log needs (and the next part of a chain), and may take
        off what is done. Compared task by task: a stop that stays may get a task more (the next part of a quest
        turned in there) or lose one."""
        chain = {q["then"] for q in self.world.quests.values() if q["then"]}
        planned = self._planned(before) | self._planned(after)

        def key(t):
            return f"{t['kind']}:{t['id'] or t['quest'] or t['zone']}:{t['label']}"
        had = {key(t): (s, t) for s in before if not s["done"] for t in s["tasks"]}
        has = {key(t): (s, t) for s in after if not s["done"] for t in s["tasks"]}
        for k, (s, t) in has.items():
            if k in had:
                continue
            if t["kind"] == "pick_up" and t["id"] not in chain and t["id"] not in self._planned(before):
                self.problem(f"the update put {s['title']} on the route to pick up [{t['quest']}], which the route did not have")
            if t["kind"] in ("objective", "turn_in") and t["id"] not in self.log and t["id"] not in planned:
                self.problem(f"the update put {s['title']} on the route for [{t['quest']}], which the player does not have")
        for k, (s, t) in had.items():
            if k in has or t["kind"] not in ("objective", "turn_in") or t["id"] not in self.log:
                continue
            state = dict(zip([n for n, _ in self.world.objectives(t["id"])], self.log[t["id"]]))
            if t["kind"] == "turn_in" or not state.get(t["label"], False):
                self.problem(f"the update took {s['title']} off the route, which [{t['quest']}] still needs")
        self.check("after an update")

    @staticmethod
    def _planned(stops: list[dict]) -> set[int]:
        return {t["id"] for s in stops for t in s["tasks"] if t["kind"] == "pick_up"}

    # ------------------------------------------------------------------------------------------- following it
    def follow(self, steps: int = 60, until=None, wander=None) -> None:
        """The player goes from stop to stop, as the arrow says, and does what each is for; ``until(journey)``
        ends the walk early. ``wander`` (a random.Random): now and then the player clicks another stop of the list and
        goes there first, one they can do something at."""
        for _ in range(steps):
            stops = self.stops()
            clicked = False
            if wander and wander.random() < 0.4:
                can = [s for s in stops if not s["done"] and s["zone"] == self.zone and self._doable(s)]
                if can:
                    clicked = True
                    pick = wander.choice(can)
                    self.note(f"clicked {pick['n']}. {pick['title']}")
                    self.sim.lua(f"ForeverAI.Map.SetCurrent({pick['n']})")
                    self.sim.run(1)
                    stops = self.stops()
            cur = next((s for s in stops if s["current"]), None) or next((s for s in stops if not s["done"]), None)
            if cur is None or (until and until(self)):
                return
            if cur["zone"] != self.zone:
                self.problem(f"the arrow leads to {cur['title']} in {cur['zone']}, and no way there comes before it")
                self.place(cur["zone"], cur["x"], cur["y"])
            if not clicked and any(t["kind"] == "travel" for t in cur["tasks"]):
                behind = [s["title"] for s in stops if s["n"] < cur["n"] and not s["done"] and s["zone"] == self.zone
                          and self._doable(s)]
                if behind:
                    self.problem(f"the arrow leads out of {self.zone} through {cur['title']} with stops left behind "
                                 f"in it: {', '.join(behind)}")
            self.walk(cur["x"], cur["y"])
            self.note(f"at stop {cur['n']}. {cur['title']} {cur['for']}")
            self.act(cur)
            self.pump()
            self.check(f"after {cur['title']}")
            again = next((s for s in self.stops() if s["n"] == cur["n"] and self._same(s, cur)), None)
            if again and not again["done"] and again["current"]:
                self.problem(f"the arrow is stuck on {cur['title']} {cur['for']}: nothing the player can do there finishes it")
                self.sim.lua(f'ForeverAI.Map.Remove({cur["n"]}, "player")')
                self.pump(4)
        self.problem(f"the route did not end within {steps} stops")

    def _still_to_do(self, qid: int, here: dict) -> bool:
        """Has the route another stop, not done, for an objective of this quest?"""
        return any(t["kind"] == "objective" and t["id"] == qid and not t.get("did")
                   for s in self.stops() if not s["done"] and s["n"] != here["n"] for t in s["tasks"])

    def _awaits(self, qid: int) -> bool:
        """Has the route a stop, not done, to pick this quest up?"""
        return any(t["kind"] == "pick_up" and t["id"] == qid and not t.get("did")
                   for s in self.stops() if not s["done"] for t in s["tasks"])

    def _doable(self, stop: dict) -> bool:
        """Can the player do something at this stop now (a quest to take that is theirs to take, an objective of a
        quest in the log, a turn-in of a finished one)? A way into a zone only from the zone it leaves."""
        for t in stop["tasks"]:
            qid = t["id"]
            if t["kind"] == "travel" and stop["zone"] == self.zone:
                return True
            if t["kind"] == "pick_up" and qid not in self.log and qid not in self.done:
                before = (self.world.quests.get(qid) or {}).get("after")
                if not before or before in self.done:  # (the next part of a chain opens once the part before is done)
                    return True
            if t["kind"] == "objective" and qid in self.log:
                return True
            if t["kind"] == "turn_in" and qid in self.log and all(self.log[qid]):
                return True
        return not stop["tasks"] and stop["zone"] == self.zone

    def act(self, stop: dict) -> None:
        """What a player does at a stop: everything it is for, as far as the game lets them. At a quest NPC they
        talk to him first (his window opens, and closes when they are done)."""
        talks = any(t["kind"] in ("pick_up", "turn_in") for t in stop["tasks"])
        if talks:
            self.sim._wow.fire(b"QUEST_GREETING")
        try:
            self._act(stop)
        finally:
            if talks:
                self.sim._wow.fire(b"QUEST_FINISHED")

    def _act(self, stop: dict) -> None:
        for t in stop["tasks"]:
            if t.get("did"):  # done already (the stop stays for the rest): the add-on does not show it
                continue
            qid, q = t["id"], self.world.quests.get(t["id"])
            if t["kind"] == "travel" and self._flight_master():
                # a flight master: the flight map opens, and the player flies to the one of that zone they know
                here = self._flight_master()
                self._open(here)
                known = _plain(self.sim.to_py(self.sim.lua(
                    "local o = {} for n in pairs(ForeverAI.char.taxi.masters or {}) do o[#o + 1] = n end return o"))) or []
                to = next((self.world.npcs[n] for n in self.world.flight if self.world.npcs[n]["zone"] == t["zone"]
                           and self.world.npcs[n]["name"] in known), None)
                if not to:
                    self.problem(f"{stop['title']} flies into {t['zone']}, where the character knows no flight master")
                    continue
                self.note(f"flies to {to['name']}, {t['zone']}")
                self.sim.lua("UnitOnTaxi = function() return true end")
                self.place(to["zone"], *to["at"][0])
                self.sim._wow.fire(b"ZONE_CHANGED_NEW_AREA")
                self.sim.run(6)
                self.sim.lua("UnitOnTaxi = function() return false end")
                self.sim.run(3)
            elif t["kind"] == "travel":
                way = next((c for c in CROSSINGS if c[0] == self.zone and c[3] == t["zone"]), None) or \
                      next(((c[3], c[4], c[5], c[0], c[1], c[2]) for c in CROSSINGS if c[3] == self.zone and c[0] == t["zone"]), None)
                if not way:
                    self.problem(f"{stop['title']} is a way into {t['zone']}, and there is none from {self.zone}")
                    continue
                if self.meters(self.zone, self.at, (way[1], way[2])) > 120:
                    self.problem(f"{stop['title']} is {self.meters(self.zone, self.at, (way[1], way[2])):.0f} m from the crossing into {t['zone']}")
                self.note(f"through to {t['zone']}")
                self.place(way[3], way[4], way[5])
                self.sim._wow.fire(b"ZONE_CHANGED_NEW_AREA")
                self.sim.run(3)
            elif q is None:
                self.problem(f"{stop['title']} is for a quest the world does not have: [{t['quest']}]")
            elif t["kind"] == "pick_up":
                giver = self.world.npcs[q["giver"]]
                if qid in self.log or qid in self.done or (q["after"] and q["after"] not in self.done):
                    continue  # nothing to take (the check says so when that is wrong)
                if giver["zone"] != self.zone or self.meters(self.zone, self.at, giver["at"][0]) > 40:
                    self.problem(f"{stop['title']} is to pick up [{q['title']}], and {giver['name']} is not there")
                    continue
                self.take(qid)
            elif t["kind"] in ("objective", "turn_in") and qid not in self.log and self._awaits(qid):
                continue  # its quest is still to be picked up at another stop (a stop the player chose out of order)
            elif t["kind"] == "objective":
                if qid not in self.log:
                    self.problem(f"{stop['title']} is for [{q['title']}], which is not in the log")
                    continue
                self.finish(qid, t["label"])
            elif t["kind"] == "turn_in" and qid in self.log and not all(self.log[qid]) and self._still_to_do(qid, stop):
                continue  # its objectives are at stops still to come (a stop the player chose early)
            elif t["kind"] == "turn_in":
                end = self.world.npcs[q["end"]]
                if qid not in self.log or not all(self.log[qid]):
                    self.problem(f"{stop['title']} is to turn in [{q['title']}], which is not "
                                 + ("in the log" if qid not in self.log else "done"))
                    continue
                if end["zone"] != self.zone or self.meters(self.zone, self.at, end["at"][0]) > 40:
                    self.problem(f"{stop['title']} is to turn in [{q['title']}], and {end['name']} is not there")
                    continue
                self.turn_in(qid)
        if not stop["tasks"]:  # a place, or somebody to deal with
            who = next((n for n in self.world.npcs.values() if n["zone"] == self.zone and not n["level"]
                        and self.meters(self.zone, self.at, n["at"][0]) <= 25), None)
            if who and who["flags"] & 8:
                self._open(who)
            elif who:
                self.sim.lua(f"TEST_npc = {_lua(who['name'])}")
                self.sim._wow.fire(b"GOSSIP_SHOW")
                self.sim._wow.fire(b"GOSSIP_CLOSED")
                self.sim.lua("TEST_npc = nil")
            self.sim.run(3)

    def _addon_trace(self) -> list[str]:
        return [str(x) for x in (_plain(self.sim.to_py(self.sim.lua("return TEST_trace"))) or [])]

    def story(self) -> str:
        addon = [x for x in self._addon_trace() if "route:" in x or "tracker:" in x]
        return "\n".join(self.trace) + "\n--- the add-on:\n" + "\n".join(addon)
