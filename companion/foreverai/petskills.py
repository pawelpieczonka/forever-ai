"""What a hunter's pet can learn and which beast teaches it, from the player's Tamed add-on.

Tamed keeps its tables private in the game, so the app reads its data files from the player's AddOns folder (nothing of
it is shipped with ForeverAI). A hunter learns a pet ability by taming a beast that knows that rank and fighting beside
it; after that the hunter teaches it to any pet whose family can learn it, for training points."""

from __future__ import annotations

import re
from pathlib import Path

from . import luadata
from .ways import MAP_IDS, region_of

ADDON = "Tamed"
FLAVORS = ("Vanilla", "Forever")  # the files the add-on merges on this client, in its order
_RANK = re.compile(r"^(?P<name>.+?) \(Rank (?P<rank>\d+)\)")
TEACHERS = 4


class PetSkills:
    def __init__(self, addons_dir):
        self.folder = Path(addons_dir) / ADDON / "src" / "data" if addons_dir else None
        self._data: tuple[dict, dict] | None = None

    def installed(self) -> bool:
        return bool(self.folder and (self.folder / "TameableNPCs-Vanilla.lua").is_file())

    def data(self) -> tuple[dict, dict]:
        """(abilities, beasts) as the add-on merges them: a later file's fields replace an earlier one's."""
        if self._data is None:
            abilities: dict = {}
            beasts: dict = {}
            for flavor in FLAVORS if self.folder else ():
                for into, file, call in ((abilities, "TameableAbilities", "MergeTameableAbilities"),
                                         (beasts, "TameableNPCs", "MergeTameableNPCs")):
                    try:
                        patch = luadata.passed(luadata.read(self.folder / f"{file}-{flavor}.lua"), call)
                    except luadata.LuaError:
                        patch = None
                    for key, row in (patch.items() if isinstance(patch, dict) else ()):
                        if isinstance(row, dict):
                            into.setdefault(str(key), {}).update(row)
            self._data = (abilities, beasts)
        return self._data


def _levels(beast: dict) -> tuple[int, int]:
    found = [int(n) for n in re.findall(r"\d+", str(beast.get("level_range") or ""))]
    return (found[0], found[-1]) if found else (0, 0)


def _camp(coords) -> tuple[float, float] | None:
    """The spawn point in the middle of the others: a place to look from."""
    pts = [(float(c[0]), float(c[1])) for c in coords or [] if isinstance(c, list) and len(c) >= 2]
    if not pts:
        return None
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return min(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)


def _beast_line(beast: dict, zone: str, spot, far) -> str:
    low, high = _levels(beast)
    level = f"level {low}" if low == high else f"level {low}-{high}"
    kind = ", ".join(x for x in (str(beast.get("family") or ""), level, str(beast.get("classification") or "").lower()) if x)
    count = len(beast.get("coords") or [])
    away = "" if far is None else " (in the player's zone)" if far == 0 else f" (about {int(round(far, -1))} m of travel)"
    return (f"{beast.get('name')} ({kind}) — {zone} {spot[0]:.1f}, {spot[1]:.1f}"
            + (f", {count} spawn points" if count > 1 else "") + away)


def _teachers(beasts: dict, key: str, rank: int, hunter_level: int, cost) -> list[str]:
    """The beasts that know a rank and can be tamed at this level, the easiest to reach first."""
    found = []
    for beast in beasts.values():
        if not any(isinstance(a, dict) and a.get("key") == key and a.get("rank") == rank for a in beast.get("abilities") or []):
            continue
        zone, spot = MAP_IDS.get(beast.get("ui_map_id")), _camp(beast.get("coords"))
        low, _ = _levels(beast)
        if not zone or not spot or low > hunter_level:
            continue
        far = cost(zone, spot)
        rare = 1 if beast.get("classification") else 0  # a rare or elite beast is the last one to send a hunter to
        found.append((rare, far is None, far or 0.0, -low, _beast_line(beast, zone, spot, far)))
    return [line for *_, line in sorted(found)[:TEACHERS]]


def format_pet_abilities(skills: PetSkills, family: str, ability: str, snapshot: dict, world=None) -> tuple[str, bool]:
    """The pet_abilities tool: text for the model, and whether it is an error."""
    if not skills.installed():
        return ("The player does not have the Tamed add-on installed, so pet abilities and the beasts that teach them "
                "cannot be read. Answer from the game data and the web."), True
    abilities, beasts = skills.data()
    snapshot = snapshot or {}
    player, pet = snapshot.get("player") or {}, snapshot.get("pet") or {}
    hunter_level = int(player.get("level") or 60)
    pet_level = int(pet.get("level") or hunter_level) if isinstance(pet, dict) else hunter_level
    family = str(family or (pet.get("family") if isinstance(pet, dict) else "") or "").strip()
    wanted = str(ability or "").strip().lower()
    here = str(player.get("map") or player.get("zone") or "")
    try:
        at = tuple(float(v) for v in str(player.get("coords") or "").split(","))[:2]
    except ValueError:
        at = ()

    def cost(zone, spot):
        if world is None or len(at) != 2:
            return None
        way = world.route(region_of(here, at), at, region_of(zone, spot), spot)
        return way[0] if way else None

    has = {}
    for line in (pet.get("abilities") or []) if isinstance(pet, dict) else []:
        m = _RANK.match(str(line))
        if m:
            has[m.group("name").lower()] = int(m.group("rank"))
    families = sorted({str(f) for a in abilities.values() for f in a.get("learned_by") or []}
                      | {str(b.get("family")) for b in beasts.values() if b.get("family")})
    match = next((f for f in families if f.lower() == family.lower()), None)
    if family and not match and not wanted:
        return f"No pet family is called '{family}'. Families: {', '.join(families)}.", True
    out = [f"Pet abilities learned from tamed beasts (from the player's Tamed add-on; hunter level {hunter_level}"
           + (f", the pet is a level {pet_level} {pet.get('family')}" if isinstance(pet, dict) and pet.get("family") else "")
           + "). The hunter learns a rank by taming a beast that knows it and fighting beside it until it is learned, "
             "then teaches it to any pet with their own Beast Training spell (no trainer is needed for it); a rank the "
             "hunter learned before needs no new tame."]
    listed = 0
    for key, row in sorted(abilities.items(), key=lambda kv: str(kv[1].get("name") or kv[0])):
        name = str(row.get("name") or key)
        if wanted and wanted not in (key.lower(), name.lower()):
            continue
        learned_by = [str(f) for f in row.get("learned_by") or []]
        if match and learned_by and match not in learned_by and not wanted:
            continue
        ranks = sorted((r for r in row.get("ranks") or [] if isinstance(r, dict) and r.get("rank")), key=lambda r: r["rank"])
        usable = [r for r in ranks if int(r.get("pet_level") or 1) <= pet_level]
        later = next((r for r in ranks if int(r.get("pet_level") or 1) > pet_level), None)
        # the client may call it by a longer name than the add-on's file (Screech is "Demoralizing Screech" here)
        mine = max([rank for known, rank in has.items() if known == name.lower() or known.endswith(" " + name.lower())],
                   default=0)
        who = "every family" if not learned_by else ", ".join(learned_by)
        cannot = bool(match and learned_by and match not in learned_by)
        line = f"- {name}" + (f" — a {match} cannot learn it (families: {who})" if cannot else "")
        if wanted and not cannot and not match:
            line += f" (families: {who})"
        if mine:
            line += f": the pet has rank {mine}"
        elif not cannot:
            line += ": the pet does not have it"
        out.append(line)
        listed += 1
        if usable and not cannot:
            top = usable[-1]
            if top["rank"] > mine:
                out.append(f"    rank {top['rank']} is open now (pet level {top.get('pet_level')}, "
                           f"{top.get('training_cost')} training points). Beasts that know it:")
                teachers = _teachers(beasts, key, top["rank"], hunter_level, cost)
                out += [f"      {t}" for t in teachers] or ["      none the hunter can tame at this level is listed"]
            else:
                out.append("    that is the highest rank the pet's level allows")
        if later and not cannot:
            out.append(f"    next: rank {later['rank']} at pet level {later.get('pet_level')}")
    if not listed:
        known = ", ".join(sorted(str(a.get("name") or k) for k, a in abilities.items()))
        return f"No pet ability is called '{ability}'. Abilities: {known}.", True
    if match and not wanted:
        tame = []
        for beast in beasts.values():
            low, high = _levels(beast)
            zone, spot = MAP_IDS.get(beast.get("ui_map_id")), _camp(beast.get("coords"))
            if str(beast.get("family")) == match and zone and spot and hunter_level - 6 <= low <= hunter_level:
                far = cost(zone, spot)
                tame.append((far is None, far or 0.0, -low, _beast_line(beast, zone, spot, far)))
        if tame:
            diet = next((b.get("diet") for b in beasts.values() if str(b.get("family")) == match and b.get("diet")), None)
            out.append(f"{match} beasts the hunter can tame near this level"
                       + (f" (they eat {', '.join(diet)})" if isinstance(diet, list) else "") + ":")
            out += [f"- {line}" for *_, line in sorted(tame)[:5]]
    return "\n".join(out), False
