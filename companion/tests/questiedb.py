"""The quest database the add-on reads in the game (QuestieDB, "baked" mode), read here for tests: the real quests,
NPCs, objects and items of a few zones, to walk routes through (tests/journey.py). Nothing of it is copied into the
repository; the tests that use it are skipped where the add-on is not installed.

QuestieDB keeps every entity in the metadata of its .toc file: "## X-Quest-<id>-S: <base64 CBOR>" holds the scalar
fields of quest <id> (a map field number -> value, and "p", a bit mask of the table fields that are there), and
"## X-Quest-<id>-<field>: <base64 CBOR>" each table field. A long value is "~N~" with N parts "<key>-1" ... "<key>-N".
Field numbers: see QuestieDB/src/meta/*Meta.lua."""

from __future__ import annotations

import base64
import struct
from functools import lru_cache
from pathlib import Path

ADDONS = Path("E:/wow forever/World of Warcraft/_forever_/Interface/AddOns")
TOC = ADDONS / "QuestieDB" / "QuestieDB_Forever.toc"

QUEST = {"name": 1, "startedBy": 2, "finishedBy": 3, "requiredLevel": 4, "questLevel": 5, "requiredRaces": 6,
         "requiredClasses": 7, "objectivesText": 8, "objectives": 10, "preQuestGroup": 12, "preQuestSingle": 13,
         "zoneOrSort": 17, "nextQuestInChain": 22, "specialFlags": 24}
NPC = {"name": 1, "minLevel": 4, "maxLevel": 5, "rank": 6, "spawns": 7, "zoneID": 9, "friendlyToFaction": 13,
       "subName": 14, "npcFlags": 15}
OBJECT = {"name": 1, "spawns": 4, "zoneID": 5}
ITEM = {"name": 1, "npcDrops": 2, "objectDrops": 3, "vendors": 14}


def available() -> bool:
    return TOC.exists()


def _cbor(data: bytes, i: int = 0):
    """One CBOR item at data[i:] -> (value, next index). Enough of CBOR for QuestieDB: numbers, text, arrays, maps,
    simple values and floats; tags are passed through."""
    first = data[i]
    major, info = first >> 5, first & 31
    i += 1
    if info < 24:
        arg = info
    elif info == 24:
        arg, i = data[i], i + 1
    elif info == 25:
        arg, i = struct.unpack(">H", data[i:i + 2])[0], i + 2
    elif info == 26:
        if major == 7:
            return struct.unpack(">f", data[i:i + 4])[0], i + 4
        arg, i = struct.unpack(">I", data[i:i + 4])[0], i + 4
    elif info == 27:
        if major == 7:
            return struct.unpack(">d", data[i:i + 8])[0], i + 8
        arg, i = struct.unpack(">Q", data[i:i + 8])[0], i + 8
    else:
        raise ValueError(f"CBOR: indefinite lengths are not read (byte {first:#x})")
    if major == 7 and info == 25:  # a half float
        h = arg
        exp, frac = (h >> 10) & 0x1F, h & 0x3FF
        val = (frac / 1024.0) * 2 ** -14 if exp == 0 else float("inf") if exp == 31 else (1 + frac / 1024.0) * 2 ** (exp - 15)
        return (-val if h & 0x8000 else val), i
    if major == 0:
        return arg, i
    if major == 1:
        return -1 - arg, i
    if major == 2:
        return data[i:i + arg], i + arg
    if major == 3:
        return data[i:i + arg].decode("utf-8", "replace"), i + arg
    if major == 4:
        out = []
        for _ in range(arg):
            v, i = _cbor(data, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(arg):
            k, i = _cbor(data, i)
            v, i = _cbor(data, i)
            out[k] = v
        return out, i
    if major == 6:
        return _cbor(data, i)
    if major == 7:
        return {20: False, 21: True, 22: None, 23: None}.get(arg), i
    raise ValueError("CBOR: unknown major type")


def _decode(text: str):
    return _cbor(base64.b64decode(text))[0]


@lru_cache(maxsize=1)
def _store() -> dict[str, str]:
    out = {}
    with open(TOC, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## X-") and ": " in line:
                key, _, value = line[3:].rstrip("\n").partition(": ")
                out[key] = value
    return out


def _stored(key: str) -> str | None:
    store = _store()
    value = store.get(key)
    if value and value.startswith("~") and value.endswith("~") and value[1:-1].isdigit():
        return "".join(store[f"{key}-{n}"] for n in range(1, int(value[1:-1]) + 1))
    return value


def _entity(prefix: str, eid: int, fields: dict[str, int]) -> dict | None:
    row = _stored(f"X-{prefix}-{eid}-S")
    if row is None:
        return None
    scalars = _decode(row)
    out = {}
    for name, n in fields.items():
        if n in scalars:
            out[name] = scalars[n]
        else:
            table = _stored(f"X-{prefix}-{eid}-{n}")
            if table is not None:
                out[name] = _decode(table)
    return out


@lru_cache(maxsize=None)
def quest(qid: int) -> dict | None:
    return _entity("Quest", qid, QUEST)


@lru_cache(maxsize=None)
def npc(nid: int) -> dict | None:
    return _entity("Npc", nid, NPC)


@lru_cache(maxsize=None)
def thing(oid: int) -> dict | None:
    return _entity("Object", oid, OBJECT)


@lru_cache(maxsize=None)
def item(iid: int) -> dict | None:
    return _entity("Item", iid, ITEM)


@lru_cache(maxsize=1)
def quest_ids() -> list[int]:
    ids = set()
    for key in _store():
        if key.startswith("X-Quest-") and key.endswith("-S"):
            middle = key[len("X-Quest-"):-2]
            if middle.isdigit():
                ids.add(int(middle))
    return sorted(ids)


def ids(value) -> list[int]:
    """Numbers out of a list the database gives in one of its shapes: [1, 2], [[1, 2]], {1: [...]}."""
    if isinstance(value, dict):
        value = [value[k] for k in sorted(value)]
    out = []
    for v in value or []:
        if isinstance(v, (int, float)):
            out.append(int(v))
        elif isinstance(v, (list, dict)):
            out += ids(v)
    return out


def spawns(entity: dict | None) -> dict[int, list[tuple[float, float]]]:
    """area id -> the places, as the database has them ({area: [[x, y], ...]})."""
    out = {}
    raw = (entity or {}).get("spawns") or {}
    for area, pts in (raw.items() if isinstance(raw, dict) else enumerate(raw)):
        places = [(float(p[0]), float(p[1])) for p in pts or [] if isinstance(p, (list, tuple)) and len(p) >= 2 and p[0] > 0]
        if places:
            out[int(area)] = places
    return out
