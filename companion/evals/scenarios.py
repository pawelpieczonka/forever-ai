"""Scenarios: questions players really asked (most in Polish, with typos, as typed in the game), each with the game
state it was asked in and checks on the answer. They start from evals/base_snapshot.json; a turn's ``patch`` changes
that state and stays for the following turns of the scenario."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from . import checks as c
from .engine import apply_patch, base_snapshot


@dataclass
class Turn:
    question: str
    checks: list
    patch: dict | None = None
    memory: list[str] = field(default_factory=list)  # added to the player's memory before the question
    full: dict | None = None  # a whole snapshot instead of a patch (replayed real questions)

    def snapshot(self, current: dict) -> dict:
        return copy.deepcopy(self.full) if self.full is not None else apply_patch(current, self.patch or {})


@dataclass
class Scenario:
    name: str
    why: str  # what went wrong in real conversations, or what must keep working
    turns: list[Turn]
    history: list[dict] = field(default_factory=list)  # earlier turns: {"q", "a", "did"}


def _quests(changes: dict[int, dict]) -> list:
    """The base quest log with some quests changed (by id); a None value removes a field."""
    out = []
    for q in base_snapshot()["quests"]:
        if isinstance(q, dict) and q.get("id") in changes:
            q = apply_patch(q, changes[q["id"]])
        out.append(q)
    return out


TAMING_ROD_TIP = {"ageSeconds": 4, "item": "[Taming Rod]", "lines": (
    "Taming Rod\nQuest Item\nUnique\nUse: Begins taming a Strigid Screecher to be your companion for 10 minutes.  If "
    "you lose the beast's attention for any reason, the taming process will fail.")}
SCREECHER = {"classification": "normal", "reaction": "neutral", "creatureType": "Beast", "level": 8,
             "name": "Strigid Screecher", "tooltip": "Strigid Screecher\nLevel 8\nBeast"}
AT_THE_SCREECHER = {"target": SCREECHER, "tooltip": TAMING_ROD_TIP, "windows": None}
TAMED = {"quests": _quests({6102: {"status": "READY TO TURN IN", "objectives": "Tame a Strigid Screecher [DONE]",
                                   "whereToGet": None}}),
         "target": None, "windows": None,
         "pet": {"name": "Strigid Screecher", "level": 8, "debuffs": ["Tame Strigid Screecher (590s)"],
                 "tooltip": "Strigid Screecher\nEvalhunter's Pet\nLevel 8"}}
VENDOR = {"windows": {"merchant": {
    "npc": "Aldia", "canRepair": False,
    "items": ["Refreshing Spring Water — 0g 0s 25c", "Tough Jerky — 0g 0s 25c", "Small Brown Pouch — 0g 5s 0c"],
    "note": "Vendor window open: propose_vendor_action op=sell / op=repair are available."}}}
REWARDS = {"windows": {"questDialog": {
    "npc": "Dazalar", "title": "Taming the Beast", "reward": "You have done well, Evalhunter.", "money": "0g 0s 0c",
    "rewardChoices": [
        {"item": "[Hunting Gloves]", "tooltip": "Hunting Gloves\nHands  [Leather]\n31 Armor\n+1 Agility",
         "facts": {"quality": "common", "ilvl": 12, "stats": "Agility +1", "armor": 31,
                   "vsEquipped": "instead of [Worn Leather Gloves]: Agility +1, Armor +9", "sellPrice": "0g 0s 62c"}},
        {"item": "[Forest Cloth Belt]", "tooltip": "Forest Cloth Belt\nWaist  [Cloth]\n9 Armor",
         "facts": {"quality": "common", "ilvl": 12, "stats": "NONE — this item has no stats (read from the game)",
                   "armor": 9, "vsEquipped": "instead of [Ragged Leather Belt]: Armor -9", "sellPrice": "0g 0s 48c"}}]}}}
NEW_QUEST = {"windows": {"questDialog": {
    "npc": "Athridas Bearmantle", "title": "Ursal the Mauler", "money": "0g 0s 0c",
    "text": "The Gnarlpine tribe of furbolgs has grown violent. Their leader, Ursal the Mauler, drives them. He must "
            "be stopped. You will find him at their camp in the north of Starbreeze Village.",
    "objectives": "Kill Ursal the Mauler and return to Athridas Bearmantle in Dolanaar."}},
    "player": {"coords": "56.0, 57.4"}}
ROUTE = {"q": "uloz mi trase pod obecne questy i oznacz",
         "a": "Trasa:\n1. **Strigid Screecher** (64.2, 69.2), 24 m: oswój go [Taming Rod].\n2. **Dazalar** (56.7, 59.5): "
              "oddaj [Taming the Beast].\n3. **Sentinel Eralya Leafshadow** (37.5, 36.8): oddaj [The Lost Runner].\n"
              "4. **Oracle Glade Moonwell** (38.4, 34.1): napełnij fiolkę do [Crown of the Earth].",
         "did": ["marked on the map: Strigid Screecher (64.2, 69.2)", "marked on the map: Dazalar (56.7, 59.5)",
                 "marked on the map: Sentinel Eralya Leafshadow (37.5, 36.8)",
                 "marked on the map: Oracle Glade Moonwell (38.4, 34.1)"]}
ROUTE_MARKS = ["1. Strigid Screecher — Teldrassil 64.2, 69.2 (24 m)", "2. Dazalar — Teldrassil 56.7, 59.5 (444 m)",
               "3. Sentinel Eralya Leafshadow — Teldrassil 37.5, 36.8 (1590 m)",
               "4. Oracle Glade Moonwell — Teldrassil 38.4, 34.1 (1602 m)"]
A_QUEST_NEXT_TO_THE_ROUTE = {"marks": ROUTE_MARKS, "nearbyQuests": [
    {"id": 489, "title": "Seek Redemption!", "giver": "Syral Bladeleaf", "level": 7, "minLevel": 4, "distance": "38 m",
     "where": "Teldrassil 64.5, 68.9", "objective": "Collect 3 Fel Cones and bring them to Syral Bladeleaf."}]
    + base_snapshot()["nearbyQuests"]}
BUILD = ("Build: Gladius Gaming \"Forever Solo Hunter\" (BM, Solo). Talents: Endurance Training 5, Unleashed Fury 5, "
         "Summon Hawk, Ferocity 5, Bestial Wrath. Stat: Agility.")
NO_AMMO = [e for e in base_snapshot()["equipment"] if e.get("slot") != "Ammo"] + [
    {"slot": "Ammo", "item": "(empty: nothing in the ammunition slot)"}]
QUIET = [c.calls("mark_on_map", 0, 0), c.never_says(r"PvP")]
PL = c.language("pl")

SCENARIOS = [
    Scenario("combat_question", "A yes/no question in a fight got ~200 words and three headings.", [
        Turn("bije mnie to normalne?", [c.max_words(50), c.max_headings(0), PL, *QUIET],
             patch={**AT_THE_SCREECHER, "player": {"inCombat": True},
                    "target": {**SCREECHER, "reaction": "hostile", "target": "Evalhunter"},
                    "recentEvents": ["12s ago [combat] Entered combat"] + base_snapshot()["recentEvents"]})],
        history=[{"q": "jakiego moba oznaczyc i jaki item uzyc?",
                  "a": "Zaznacz **Strigid Screechera** i użyj [Taming Rod] (**F11**). Stój w miejscu do końca paska; "
                       "w tym czasie będzie cię bił, to normalne."}]),
    Scenario("vague_this_is_a_mob", "A vague question about what is in front of the player.", [
        Turn("co dlaej przeciez to jest mob", [c.says(r"Taming Rod"), c.max_words(90), c.max_headings(1), *QUIET],
             patch=AT_THE_SCREECHER)]),
    Scenario("mark_turn_in", "Asked to mark a place: one mark, at once, no long report.", [
        Turn("oznacz mi gdzie mam oddac tego questa z oswajaniem",
             [c.calls("mark_on_map", 1, 1), c.marks(56.7, 59.5), c.max_words(60),
              c.never_says(r"napisz .{0,12}ok", r"potwierd[zź]")], patch=TAMED)]),
    Scenario("replace_marks", "\"jezus mozesz oznaczyc nowe\": asked for new marks, the AI said it cannot remove the "
                              "old ones and waited for an \"ok\".", [
        Turn("oznacz gdzie oddac the lost runner", [c.calls("mark_on_map", 1, 1), c.marks(37.5, 36.8), c.max_words(60)]),
        Turn("usun stare znaczniki i oznacz mi tylko trenera first aid",
             [c.calls("mark_on_map", 1, 1), c.marks(55.3, 56.8), c.max_words(60),
              c.never_says(r"nie mog[ęe]", r"napisz .{0,12}ok", r"r[ęe]cznie")])]),
    Scenario("keep_the_route", "A route is marked; something new appears next to it. The plan must stay.", [
        Turn("co mam tu zrobic?", [c.says(r"Taming Rod"), c.max_words(90), *QUIET,
                                   c.never_says(r"now[aąe] tras", r"zmieni(am|łem|ć) tras")],
             patch={**AT_THE_SCREECHER, **A_QUEST_NEXT_TO_THE_ROUTE}),
        Turn("ok mam go", [c.says(r"Dazalar"), c.max_words(70), *QUIET],
             patch={**TAMED, "marks": [m.replace(f"{n + 2}. ", f"{n + 1}. ") for n, m in enumerate(ROUTE_MARKS[1:])]})],
        history=[ROUTE]),
    Scenario("sell_junk_at_vendor", "\"sprzedales mi mieczyk do skorowania\": tools and materials were sold.", [
        Turn("sprzedaj smieci", [c.calls("propose_vendor_action", 1, 1), c.sells("Cracked Shortbow", "2773"),
                                 c.keeps("Skinning Knife", "7005"), c.keeps("Light Leather", "2318"),
                                 c.keeps("Linen Bandage", "1251"), c.keeps("Small Spider Leg", "5465"),
                                 c.keeps("Taming Rod", "15923"), c.max_words(80)], patch=VENDOR)]),
    Scenario("what_can_i_sell", "\"masz za bardzo proste myslenie\": only gray items were looked at, then the advice "
                                "changed twice.", [
        Turn("mam zawalone eq, co z tego moge sprzedac a co zostawic?",
             [c.says(r"Cracked Shortbow"), c.calls("propose_vendor_action", 0, 0), c.max_words(170), *QUIET])]),
    Scenario("reward_choice", "\"co wybrac?\" with the reward window open.", [
        Turn("co wybrac?", [c.says(r"Hunting Gloves"), c.max_words(40), c.max_headings(0), PL, *QUIET],
             patch={**TAMED, **REWARDS})]),
    Scenario("take_quest", "\"brac tego questa?\" with a quest offer open (asked 17 times).", [
        Turn("brac tego questa?", [c.max_words(60), c.max_headings(0), PL, *QUIET], patch=NEW_QUEST)]),
    Scenario("player_action", "The note after Accept needs one or two lines, not a new plan.", [
        Turn("[Player action] Accepted: sell 1 item types for 39c — done.",
             [c.max_words(35), c.max_headings(0), c.calls("propose_vendor_action", 0, 0), *QUIET], patch=VENDOR)],
        history=[{"q": "sprzedaj smieci", "a": "Proponuję sprzedać [Cracked Shortbow] za 39c.",
                  "did": ["proposed selling: [Cracked Shortbow]"]}]),
    Scenario("what_now", "\"co teraz?\" without history: the next step, briefly.", [
        Turn("co teraz?", [c.says(r"Screecher"), c.max_words(90), c.calls("mark_on_map", 0, 1), c.never_says(r"PvP")])]),
    Scenario("full_route", "A route was asked for 22 times: every stop marked, in a sensible order.", [
        Turn("uloz mi optymalna trase pod wszystkie questy i oznacz ja",
             [c.calls("plan_route", 1, 1), c.marked(3, 8), c.marks(56.7, 59.5), c.says(r"Moonwell"), c.max_words(260), PL])]),
    Scenario("no_repeats", "The same advice and warnings came back in every answer of a conversation.", [
        Turn("co teraz?", [c.says(r"Screecher"), c.max_words(90)], patch=AT_THE_SCREECHER),
        Turn("ile mi brakuje do 12 lvl?", [c.says(r"7[\s.,  ]?832"), c.max_words(70), c.max_headings(0), *QUIET]),
        Turn("ok", [c.max_words(20), c.max_headings(0), *QUIET])]),
    Scenario("english_question", "The answer follows the language of the question.", [
        Turn("where do I turn in The Lost Runner?",
             [c.says(r"Eralya"), c.language("en"), c.calls("mark_on_map", 0, 1), c.max_words(70)])]),
    Scenario("item_without_a_description", "\"Z pamięci (Classic), może być nieaktualne\" appeared several times in "
                                           "one answer.", [
        Turn("[Light Armor Kit] po co to?", [c.max_words(90), c.max_headings(1), c.at_most(1, r"Classic"), PL, *QUIET])]),
    Scenario("mark_again", "\"oznacz jeszcze raz\" came 5 times; here the player cleared the marks and wants them back.", [
        Turn("wyczyscilem znaczniki, oznacz trase jeszcze raz", [c.marked(3, 8), c.marks(56.7, 59.5), c.marks(37.5, 36.8),
                                                                 c.max_words(120), c.never_says(r"PvP")])],
        history=[ROUTE]),
    Scenario("advice_did_not_work", "The ammunition slot: four answers of guesses, up to \"disable Baganator and "
                                    "/reload\".", [
        Turn("nadal to samo o co chodzi mam strzaly przeciez",
             [c.never_says(r"Baganator", r"/reload", r"/run", r"EquipItemByName"), c.max_words(90),
              c.at_most(1, r"Classic"), *QUIET],
             patch={"recentEvents": ["6s ago [error] Must have ammo equipped in the ammo slot (x3)"]
                    + base_snapshot()["recentEvents"], "windows": None, "equipment": NO_AMMO})],
        history=[{"q": "czemu nie moge uzywac luku???",
                  "a": "Gra zgłasza \"Must have ammo equipped in the ammo slot\": strzały nie są założone. Kliknij "
                       "prawym przyciskiem [Sharp Arrow] w torbie."}]),
    Scenario("where_to_buy", "A plain \"where do I buy it\" in few words, with an English item name: the vendor, the "
                             "distance, a mark, in the player's language.", [
        Turn("gdzie kupie coarse thread?",
             [c.says(r"Narret Shadowgrove|Terunne Bearshaper"), c.calls("mark_on_map", 1, 2), c.max_words(70), PL])]),
    Scenario("mob_is_not_there", "\"nie ma zadnego tego moba dookola\": the spawn point from the database was "
                                 "repeated.", [
        Turn("nie ma tu zadnego screechera dookola", [c.max_words(70), c.max_headings(0), PL, c.never_says(r"PvP")],
             patch={"target": None, "windows": None, "around": None})],
        history=[{"q": "co teraz?", "a": "Oswój [Strigid Screecher]: najbliższy jest 24 m od ciebie (64.2, 69.2). "
                                         "Zaznacz go i użyj [Taming Rod] (**F11**)."}]),
    Scenario("tame_is_not_kill", "The quest data said \"kill Strigid Screecher\" for a creature that has to be tamed, and a "
                                 "route told the player to kill it.", [
        Turn("jak zrobic tego questa z sowa?", [c.says(r"Taming Rod"), c.never_says(r"\bzabij", r"\bzabić"), c.max_words(90), PL,
                                               *QUIET])]),
    Scenario("talents_from_the_build", "The build is in memory and the talent window is open.", [
        Turn("co tu wybrac?", [c.says(r"Endurance Training"), c.max_words(80), *QUIET], memory=[BUILD])]),
]
