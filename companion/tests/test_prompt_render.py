"""How the game data is written out for the model: what was only noise in real conversations is shortened."""

import json
from pathlib import Path

from foreverai.prompt import render_snapshot

BASE = json.loads((Path(__file__).resolve().parents[1] / "evals" / "base_snapshot.json").read_text(encoding="utf-8"))


def section(text: str, name: str) -> str:
    return text.split(f"## {name}\n", 1)[1].split("\n\n## ", 1)[0]


def test_players_nearby_are_summed_up():
    """Forty players with class, race, guild and target were 6,000 characters in every question and made the model
    warn about PvP in every answer."""
    snap = {"player": {"name": "Rossmann"}, "nearby": [
        {"class": "Rogue", "race": "Night Elf", "name": "Velivs Nightblade-Realm", "seen": "20s ago (nameplate)",
         "level": 5, "targeting": "Rossmann", "pvp": True},
        {"class": "Hunter", "race": "Night Elf", "name": "Playerone", "seen": "55s ago (nameplate)", "level": 10,
         "targeting": "Dazalar"},
        {"guild": "Renowned", "class": "Warlock", "race": "Human", "name": "Vincent Ferox-Realm", "seen": "487s ago (nameplate)",
         "level": 17, "inspect": {"items": ["[Wraps] (ilvl 11)"]}}]}
    assert section(render_snapshot(snap), "nearby") == (
        "3 players noticed nearby, levels 5-17: Velivs Nightblade 5 Rogue, Playerone 10 Hunter, Vincent Ferox 17 Warlock\n"
        "- targeting the player: Velivs Nightblade, level 5 Rogue, PvP flagged, 20s ago")


def test_a_quest_that_is_ready_needs_no_story():
    quests = ["Quest log: 2/40",
              {"id": 1, "title": "Letter", "status": "READY TO TURN IN", "level": 7, "tracked": True,
               "turnIn": "Postman (Teldrassil 65.0, 50.0)", "rewards": "320 XP", "objectiveText": "Bring the letter.",
               "description": "A long story. " * 30},
              {"id": 2, "title": "Hunt", "status": "in progress", "level": 8, "objectives": "0/8 Boar slain [TO DO]",
               "description": "The boars are west of here, past the bridge.", "objectiveText": "Kill 8 boars."}]
    out = section(render_snapshot({"quests": quests}), "quests")
    assert "A long story" not in out and "tracked" not in out
    assert '"status":"READY TO TURN IN"' in out and "Postman (Teldrassil 65.0, 50.0)" in out and "320 XP" in out
    assert "past the bridge" in out  # the text of a quest in progress says where to go


def test_empty_equipment_slots_take_one_line():
    eq = [{"item": "(empty)", "slot": "Head"}, {"item": "[Woodland Tunic]", "slot": "Chest", "ilvl": 5},
          {"item": "(empty)", "slot": "Neck"}, {"slot": "Ammo", "item": "[Sharp Arrow]", "count": 1387}]
    assert section(render_snapshot({"equipment": eq}), "equipment") == (
        '- {"item":"[Woodland Tunic]","slot":"Chest","ilvl":5}\n'
        '- {"slot":"Ammo","item":"[Sharp Arrow]","count":1387}\n'
        "- empty slots: Head, Neck")


def test_the_real_snapshot_got_shorter():
    text = render_snapshot(BASE)
    assert len(section(text, "nearby")) < 300
    assert len(text) < 38000  # 42,500 before; the action bars shrink further in the add-on itself


def test_profession_tools_in_the_bags_are_named():
    """A Skinning Knife looks like a weak dagger (and was sold as one); what it is for is a fact, so the data says it."""
    bags = {"freeSlots": 3, "items": [
        {"item": "[Skinning Knife]", "id": 7005, "count": 1, "facts": {"stats": "DPS +1.3", "vsEquipped": "DPS -0.8"}},
        {"item": "[Mining Pick]", "id": 2901, "count": 1}, {"item": "[Linen Cloth]", "id": 2589, "count": 12}]}
    out = section(render_snapshot({"bags": bags}), "bags")
    assert '"item":"[Skinning Knife]","id":7005,"count":1,"professionTool":"Skinning"' in out
    assert '"item":"[Mining Pick]","id":2901,"count":1,"professionTool":"Mining"' in out
    assert out.count("professionTool") == 2 and bags["items"][0].get("professionTool") is None  # the snapshot stays as it came


def test_tracked_creatures_come_right_after_the_marks():
    text = render_snapshot({"quests": ["Quest log: 1/40"], "marks": ["1. Screecher — Teldrassil 64.2, 69.2 (24 m)"],
                            "tracked": ["Strigid Screecher: none in sight now; not seen since it is tracked"],
                            "nearbyQuests": [{"title": "x"}]})
    assert text.index("## marks") < text.index("## tracked") < text.index("## nearbyQuests")
    assert "- Strigid Screecher: none in sight now; not seen since it is tracked" in section(text, "tracked")
