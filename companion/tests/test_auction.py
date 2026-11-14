import json

import shutil

import subprocess

import threading

import time

from pathlib import Path

import pytest

from foreverai.tools import auction_request, format_auction, format_sell_prices, sell_price_request

from test_service import _read_inbox, _send, env  # noqa: F401  (fixture)

ADDON = Path(__file__).resolve().parents[2] / "addon" / "ForeverAI"

def test_auction_request_maps_filters():
    req, err = auction_request({"weapon_type": "1h_sword", "slot": "one-hand", "max_price_gold": 100,
                                "upgrades_only": True})
    assert err is None
    assert req["classIDs"] == [2] and req["subclassIDs"] == [7]
    assert req["equipLocs"] == ["INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND"]
    assert req["maxCopper"] == 1_000_000 and req["upgradesOnly"] and req["usableOnly"]
    assert req["sort"] == "upgrade" and req["limit"] == 15 and req["maxAgeDays"] == 7
    req, _ = auction_request({"query": "Linen Cloth", "category": "trade_goods", "limit": 99})
    assert req["classIDs"] == [7] and req["sort"] == "price" and req["limit"] == 30
    for bad in ({}, {"weapon_type": "laser"}, {"weapon_type": "dagger", "armor_type": "cloth"},
                {"query": "x", "max_price_gold": "lots"}, {"armor_type": "plate", "category": "weapon"}):
        req, err = auction_request(bad)
        assert req is None and err.startswith("Rejected")

def test_auction_request_exact_items():
    req, err = auction_request({"items": ["Linen Cloth", "[Wool Cloth]", 2589, "2589", "Silk Cloth (item 4306)", "",
                                          True, {"id": 2592}, "linen cloth"], "query": "ignored", "slot": "head"})
    assert err is None
    # duplicates (also by case) dropped, filters ignored, older prices allowed by default
    assert req == {"items": [{"name": "Linen Cloth"}, {"name": "Wool Cloth"}, {"id": 2589}, {"id": 4306}, {"id": 2592}],
                   "maxAgeDays": 30}
    assert auction_request({"items": "Linen Cloth", "max_age_days": 3})[0] == {"items": [{"name": "Linen Cloth"}],
                                                                                "maxAgeDays": 3}
    for bad, why in (({"items": [""]}, "item names"), ({"items": list(range(1, 42))}, "at most 40"),
                     ({"items": {"a": 1}}, "must be a list")):
        req, err = auction_request(bad)
        assert req is None and why in err

def test_format_auction_exact_items():
    text = format_auction({"source": "Auctionator", "realm": "Forever", "lastScan": "1 h ago (full scan)", "exact": True,
                           "maxAgeDays": 30, "ahOpen": True,
                           "items": [{"id": 2589, "name": "Linen Cloth", "price": "12s 0c", "seen": "yesterday",
                                      "available": 40}],
                           "missing": [{"id": 2592, "name": "Wool Cloth", "why": "no auction price seen in the last 30 days"},
                                       {"name": "Moon Dust", "why": "unknown item name (not in the game's item cache): "
                                                                    "give the item id"}]})
    assert "Prices of the requested items" in text and "last 30 days" in text
    assert "- [Linen Cloth] (item 2589) — 12s 0c | seen yesterday, 40 available" in text
    assert "- [Wool Cloth] (item 2592): no auction price seen in the last 30 days" in text
    assert "- [Moon Dust]: unknown item name" in text and "window is open" in text
    assert "No prices found." in format_auction({"exact": True, "items": []})
    gone = format_auction({"exact": True, "items": [], "missing": [
        {"id": 105, "name": "Old Sword", "why": "not listed in the last full scan", "lastPrice": "30g 0s 0c",
         "lastSeen": "20 days ago"}]})
    assert ("- [Old Sword] (item 105): not listed in the last full scan (last seen 20 days ago at 30g 0s 0c; that "
            "offer is gone)") in gone

def test_format_auction():
    text = format_auction({"source": "Auctionator", "realm": "Forever-EU", "lastScan": "2 h ago (full scan)",
                           "dbItems": 1200, "matched": 3, "sort": "upgrade", "upgradeMethod": "Pawn (the player's scales)",
                           "skipped": {"not usable": 2, "too high level": 0}, "notLoaded": 4, "ahOpen": True,
                           "items": [{"id": 1, "name": "Gleaming Sword", "price": "45g 0s 0c", "seen": "today",
                                      "available": 2, "quality": "uncommon", "ilvl": 25, "requiresLevel": 20,
                                      "slot": "One-Hand", "type": "One-Handed Swords", "stats": "DPS +12.5",
                                      "vsEquipped": "instead of [Rusty Sword]: DPS +4", "pawn": "+8.0% upgrade"}]})
    assert "last scan: 2 h ago (full scan)" in text and "1200 items" in text and "Left out: 2 not usable." in text
    assert "- [Gleaming Sword] (item 1) — 45g 0s 0c | seen today, 2 available" in text
    assert "Pawn: +8.0% upgrade" in text and "4 matching items" in text and "window is open" in text
    assert "the upgrades first (the biggest first), then the other items by their Pawn value" in text
    assert "Pawn value" not in format_auction({"items": [], "upgradeMethod": "comparison with the equipped item"})
    left = format_auction({"items": [], "skipped": {"no longer listed (not in the last full scan)": 3, "not usable": 0}})
    assert "Left out: 3 no longer listed (not in the last full scan)." in left
    assert "failed: Auctionator is not installed" in format_auction({"error": "Auctionator is not installed"})
    assert "did not respond" in format_auction(None)

def test_search_tool_round_trip_and_card(env):  # noqa: F811
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    writer = InboxWriter(addons, "S", 1, 20, 4)
    writer.send({"kind": "answer", "final": False, "msgId": 3, "text": "", "phase": "x"})
    ctx = TurnContext({"msgId": 3}, writer, {}, threading.Event(), [])
    out = {}
    t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "search_auction_house", {
        "weapon_type": "1h_sword", "max_price_gold": 100})))
    t.start()
    deadline, req = time.time() + 5, None
    while time.time() < deadline and not req:
        req = (_read_inbox(addons, 1).get("requests") or [None])[0]
        time.sleep(0.02)
    assert req["op"] == "auction_search" and req["subclassIDs"] == [7] and req["maxCopper"] == 1_000_000
    _send(svc, {"type": "game_result", "id": req["id"], "result": {
        "source": "Auctionator", "dbItems": 50, "matched": 1, "ahOpen": False,
        "items": [{"id": 2042, "name": "Gleaming Sword", "price": "45g 0s 0c", "seen": "today"}]}})
    t.join(5)
    text, err = out["r"]
    assert not err and "[Gleaming Sword] (item 2042)" in text
    answer = "Buy **[Gleaming Sword]** for 45g."
    assert ctx.auction_card(answer) == [{"id": 2042, "name": "Gleaming Sword", "price": "45g 0s 0c"}]
    assert ctx.auction_card("Nothing worth it.") is None
    assert {"name": "Gleaming Sword", "kind": "item", "id": 2042} in svc.links(answer, ctx)

def test_exact_items_tool_round_trip_and_shopping_card(env):  # noqa: F811
    """A shopping list: one call with the exact items, and the "Search on the AH" card offers the ones the answer names."""
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    writer = InboxWriter(addons, "S", 1, 20, 4)
    writer.send({"kind": "answer", "final": False, "msgId": 4, "text": "", "phase": "x"})
    ctx = TurnContext({"msgId": 4}, writer, {}, threading.Event(), [])
    out = {}
    t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "search_auction_house", {
        "items": ["Linen Cloth", "2592", "Coarse Thread"]})))
    t.start()
    deadline, req = time.time() + 5, None
    while time.time() < deadline and not req:
        req = (_read_inbox(addons, 1).get("requests") or [None])[0]
        time.sleep(0.02)
    assert req["op"] == "auction_search" and req["items"] == [{"name": "Linen Cloth"}, {"id": 2592}, {"name": "Coarse Thread"}]
    _send(svc, {"type": "game_result", "id": req["id"], "result": {
        "source": "Auctionator", "exact": True, "maxAgeDays": 30, "matched": 2, "ahOpen": True,
        "items": [{"id": 2589, "name": "Linen Cloth", "price": "12s 0c", "seen": "today"},
                  {"id": 2592, "name": "Wool Cloth", "price": "35s 0c", "seen": "yesterday"}],
        "missing": [{"name": "Coarse Thread", "why": "unknown item name (not in the game's item cache): give the item id"}]}})
    t.join(5)
    text, err = out["r"]
    assert not err and "[Linen Cloth] (item 2589) — 12s 0c" in text and "[Coarse Thread]: unknown item name" in text
    answer = "Buy 20 **[Linen Cloth]** and 5 [Wool Cloth]; get [Coarse Thread] from a trade goods vendor."
    assert ctx.auction_card(answer) == [{"id": 2589, "name": "Linen Cloth", "price": "12s 0c"},
                                        {"id": 2592, "name": "Wool Cloth", "price": "35s 0c"}]

def test_auctionator_list_puts_any_items_on_the_card(env):  # noqa: F811
    """The real answer: the player asked for a filter in Auctionator for the Darkmoon Faire items, and the model said it
    cannot make one and told them to type a list by hand. The card searched only items the auction search had priced
    in the same answer; now the model puts any items on it, and the card saves them as a shopping list too."""
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    snap = {"addons": [{"name": "Auctionator"}, {"name": "Questie"}]}
    ctx = TurnContext({"msgId": 4}, InboxWriter(addons, "S", 1, 20, 4), snap, threading.Event(), [])
    ctx.ah_items["Green Iron Bracers"] = {"id": 3835, "name": "Green Iron Bracers", "price": "22s 0c"}
    ctx.writer.send({"kind": "answer", "final": False, "msgId": 4, "text": "", "phase": "x"})
    out = {}
    t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "auctionator_list", {
        "items": ["[Small Furry Paw]", "green iron bracers", "Small Furry Paw", 'Torn "Bear" Pelt;^', " ",
                  "Soft Bushy Tail"], "name": "Darkmoon Faire; turn-ins"})))
    t.start()
    deadline, req = time.time() + 5, None
    while time.time() < deadline and not req:
        req = (_read_inbox(addons, 1).get("requests") or [None])[0]
        time.sleep(0.02)
    # the prices of the items the card has none for, from the last scan, in one search
    assert req["op"] == "auction_search" and req["items"] == [
        {"name": "Small Furry Paw"}, {"name": "Torn Bear Pelt"}, {"name": "Soft Bushy Tail"}]
    _send(svc, {"type": "game_result", "id": req["id"], "result": {
        "source": "Auctionator", "exact": True, "maxAgeDays": 30, "matched": 1, "ahOpen": False,
        "items": [{"id": 5134, "name": "Small Furry Paw", "price": "4s 10c", "seen": "today"}],
        "missing": [{"name": "Torn Bear Pelt", "id": 11407, "why": "no auction price seen in the last 30 days"},
                    {"name": "Soft Bushy Tail", "why": "unknown item name (not in the game's item cache): give the item id"}]}})
    t.join(5)
    text, err = out["r"]
    assert not err and "(4 items)" in text and '"ForeverAI: Darkmoon Faire turn-ins"' in text
    assert "do not tell the player to make the list by hand" in text
    assert "[Small Furry Paw] (item 5134) — 4s 10c" in text
    assert "[Soft Bushy Tail] — no price in the last scan (unknown item name" in text
    assert "the price data does not know this name" in text
    # the listed items in their order, priced where an auction search found them, whatever the answer names
    card = [{"id": 5134, "name": "Small Furry Paw", "price": "4s 10c"},
            {"id": 3835, "name": "Green Iron Bracers", "price": "22s 0c"},
            {"id": 11407, "name": "Torn Bear Pelt"}, {"name": "Soft Bushy Tail"}]
    assert ctx.auction_card("Here is the list.") == card
    assert ctx.auction_list() == {"auctionList": "ForeverAI: Darkmoon Faire turn-ins"}
    # without the tool: the old card, the auction items the answer names, saved under the add-on's own name
    plain = TurnContext({"msgId": 5, "conv": "plain"}, ctx.writer, snap, threading.Event(), [])  # (a list of its own)
    plain.ah_items["Green Iron Bracers"] = card[1]
    assert plain.auction_card("Buy [Green Iron Bracers].") == [card[1]] and plain.auction_list() == {}
    assert plain.auction_card("Buy [Small Furry Paw].") is None

    for args, why in (({"items": []}, "give the exact item names"), ({"items": ["[]", '"']}, "give the exact item names"),
                      ({"items": [f"Item {n}" for n in range(21)]}, "at most 20 items")):
        text, err = svc.run_tool(plain, "auctionator_list", args)
        assert err and why in text
    no_auctionator = TurnContext({"msgId": 6}, ctx.writer, {"addons": [{"name": "Questie"}]}, threading.Event(), [])
    text, err = svc.run_tool(no_auctionator, "auctionator_list", {"items": ["Linen Cloth"]})
    assert err and "Auctionator is not installed" in text and no_auctionator.auction_card("[Linen Cloth]") is None
    # one name, not a list; no answer from the game (the turn ends): the card holds it without a price
    plain.cancel.set()
    text, err = svc.run_tool(plain, "auctionator_list", {"items": "Linen Cloth"})
    assert not err and "did not respond" in text and plain.auction_card("") == [{"name": "Linen Cloth"}]
    assert plain.auction_list() == {"auctionList": "ForeverAI: Shopping"}

def test_the_answer_carries_the_auctionator_list(env):  # noqa: F811
    from foreverai.openrouter import ToolCall
    from foreverai.service import Service
    from test_service import FakeClient, _wait_final
    svc, addons, _ = env

    class Client(FakeClient):
        listed = False

        def chat_stream(self, model, messages, tools, search_mode, max_tokens):
            if not Client.listed:
                Client.listed = True
                yield "tool_calls", [ToolCall("c1", "auctionator_list", json.dumps(
                    {"items": ["Small Furry Paw", "Soft Bushy Tail"], "name": "Darkmoon Faire"}))]
                return
            yield "text", "The card below saves them as an Auctionator list."

    svc = Service(svc.store, None, addons, client_factory=Client, lookahead=4)
    svc.handle({"type": "question", "msgId": 3, "session": "S", "nextInbox": 3, "conv": "c", "text": "filter",
                "snapshot": {"addons": [{"name": "Auctionator"}]}})
    deadline, req = time.time() + 5, None
    while time.time() < deadline and not req:
        req = next((r for n in range(3, 7) for r in _read_inbox(addons, n).get("requests") or []), None)
        time.sleep(0.02)
    _send(svc, {"type": "game_result", "id": req["id"], "result": {
        "source": "Auctionator", "exact": True, "maxAgeDays": 30, "matched": 1,
        "items": [{"id": 4582, "name": "Soft Bushy Tail", "price": "1s 5c", "seen": "today"}],
        "missing": [{"name": "Small Furry Paw", "why": "no auction price seen in the last 30 days"}]}})
    d = _wait_final(addons, 3)
    assert d["auction"] == [{"name": "Small Furry Paw"}, {"id": 4582, "name": "Soft Bushy Tail", "price": "1s 5c"}]
    assert d["auctionList"] == "ForeverAI: Darkmoon Faire"
    svc.handle({"type": "question", "msgId": 4, "session": "S", "nextInbox": 7, "conv": "c", "text": "thanks",
                "snapshot": {}})
    assert "auctionList" not in _wait_final(addons, 7)  # each answer its own card

BAGS = {"freeSlots": 5, "items": [
    {"item": "[Hearthstone]", "id": 6948, "count": 1, "bound": True, "tooltip": "Soulbound\nUnique", "quality": "common"},
    {"item": "[Linen Cloth]", "id": 2589, "count": 12, "sellCopper": 13, "tooltip": "Crafting Reagent",
     "quality": "common"},
    {"item": "[Wool Cloth]", "id": 2592, "count": 10, "sellCopper": 33, "tooltip": "Crafting Reagent", "quality": "common"},
    {"item": "[Tough Jerky]", "id": 117, "count": 5, "sellCopper": 1, "quality": "common"},
    {"item": "[Rabbit's Foot]", "id": 3300, "count": 1, "sellCopper": 105, "junk": True, "quality": "poor"},
    {"item": "[Slimy Murloc Scale]", "id": 5784, "count": 4, "sellCopper": 75, "quality": "common"},
    {"item": "[Fishliver Oil]", "id": 1322, "count": 2, "sellCopper": 68, "bound": True, "tooltip": "Soulbound"},
    {"item": "[Crawler Leg]", "id": 5385, "count": 4, "bound": True, "tooltip": "Quest Item"},
    {"item": "[Conjured Bread]", "id": 1113, "count": 10, "tooltip": "Conjured Item"},
    {"item": "[Sealed Writ]", "id": 262888, "count": 1, "quality": "rare"},
    {"item": "[Glinting Steel Sword]", "id": 3101, "count": 1, "sellCopper": 1830, "quality": "uncommon",
     "facts": {"cannotUse": "missing skill: One-Handed Swords"}}]}

GAME_PRICES = {"source": "Auctionator", "lastScan": "1 h ago (full scan)", "exact": True, "maxAgeDays": 30,
               "items": [{"id": 2589, "name": "Linen Cloth", "price": "11c", "copper": 11, "seen": "today", "available": 340},
                         {"id": 2592, "name": "Wool Cloth", "price": "74c", "copper": 74, "seen": "today", "available": 248},
                         {"id": 117, "name": "Tough Jerky", "price": "4c", "copper": 4, "seen": "today"},
                         {"id": 262888, "name": "Sealed Writ", "price": "2g 0s 0c", "copper": 20000, "seen": "today"},
                         {"id": 3101, "name": "Glinting Steel Sword", "price": "25g 40s 0c", "copper": 254000,
                          "seen": "today", "available": 3}],
               "missing": [{"id": 5784, "name": "Slimy Murloc Scale", "why": "not listed in the last full scan",
                            "lastPrice": "1s 0c", "lastSeen": "3 days ago"},
                           {"id": 3300, "name": "Rabbit's Foot", "why": "no auction price seen in the last 30 days"}]}

def test_sell_price_request_prices_what_the_auction_house_can_take():
    entries, req, err = sell_price_request({"bags": BAGS}, {})
    assert err is None
    # bound items and items without any sale value are not asked about; quest items, the Hearthstone and conjured
    # food are left out altogether
    assert req == {"items": [{"id": i} for i in (2589, 2592, 117, 3300, 5784, 262888, 3101)], "maxAgeDays": 30}
    assert [e.get("name") for e in entries if "name" in e] == [
        "Linen Cloth", "Wool Cloth", "Tough Jerky", "Rabbit's Foot", "Slimy Murloc Scale", "Fishliver Oil",
        "Sealed Writ", "Glinting Steel Sword"]
    assert entries[-1] == {"skipped": ["Hearthstone", "Crawler Leg", "Conjured Bread"]}
    only, req, _ = sell_price_request({"bags": BAGS}, {"items": ["wool cloth", "[Fishliver Oil]", "117"]})
    assert [e["name"] for e in only] == ["Wool Cloth", "Tough Jerky", "Fishliver Oil"]
    assert req == {"items": [{"id": 2592}, {"id": 117}], "maxAgeDays": 30}
    assert sell_price_request({"bags": BAGS}, {"items": ["Crawler Leg"]})[2].startswith("Rejected: none of these")
    assert sell_price_request({}, {})[2].startswith("Rejected: the game data has no bags")
    big = {"items": [{"item": f"[Thing {i}]", "id": 1000 + i, "count": 1, "sellCopper": 5} for i in range(150)]}
    assert len(sell_price_request({"bags": big}, {})[1]["items"]) == 100  # what one game call prices

def test_sell_prices_sort_the_bags_into_lists():
    entries, _, _ = sell_price_request({"bags": BAGS}, {})
    text = format_sell_prices(entries, GAME_PRICES)
    assert text.startswith("The player's bag items: what a vendor pays and what the auction house pays after its 5% "
                           "cut (the lowest buyout in Auctionator; last scan: 1 h ago (full scan)). An item is worth "
                           "the auction house when the whole stack brings at least 10% and 50c more there;")
    groups = {g.split("\n", 1)[0]: g.split("\n")[1:] for g in text.split("\n\n")[1:]}
    # the most valuable first; the AH pays less (Linen: 10c after the cut), too little more (Jerky: 10c more for the
    # stack, under 50c) or nothing (an offer that is gone, no listing at all)
    assert groups["Better sold to a vendor (4; together 5s 66c):"] == [
        "- [Slimy Murloc Scale] x4 — vendor 3s 0c (75c each); not listed on the AH in the last scan (last seen 3 days "
        "ago at 1s 0c)",
        "- [Linen Cloth] x12 (crafting material) — vendor 1s 56c (13c each); AH 1s 20c after the cut (10c each), "
        "seen today, 340 listed",
        "- [Rabbit's Foot] (junk) — vendor 1s 5c; nobody listed it on the AH in the last 30 days",
        "- [Tough Jerky] x5 — vendor 5c (1c each); AH 15c after the cut (3c each), seen today"]
    # by what the AH brings more than a vendor
    assert groups["Worth the auction house (2; together 24g 20s 0c):"] == [
        "- [Glinting Steel Sword] (uncommon) — AH 24g 13s 0c after the cut, seen today, 3 listed; vendor 18s 30c",
        "- [Wool Cloth] x10 (crafting material) — AH 7s 0c after the cut (70c each), seen today, 248 listed; "
        "vendor 3s 30c (33c each)"]
    assert groups["Soulbound (only a vendor buys these) (1; together 1s 36c):"] == [
        "- [Fishliver Oil] x2 — vendor 1s 36c (68c each)"]
    assert groups["Vendors do not buy these (1):"] == [
        "- [Sealed Writ] (rare) — AH 1g 90s 0c after the cut, seen today; vendors do not buy it"]
    assert "Not priced (quest items, conjured items, no sale value): [Hearthstone], [Crawler Leg], [Conjured Bread]" in text
    assert text.endswith("check that this character has no use for it (Bags and selling); leave those out and say in "
                         "one line which ones you kept.")

def test_sell_prices_without_auction_prices():
    """Without Auctionator (or an answer from the game) the vendor prices still come, but nothing is called better
    at a vendor; items an older add-on did not price are marked as not checked."""
    entries, _, _ = sell_price_request({"bags": BAGS}, {})
    text = format_sell_prices(entries, {"error": "Auctionator is not installed or not enabled"})
    assert "Auction prices could not be read (Auctionator is not installed or not enabled)" in text
    assert "Better sold to a vendor" not in text and "Worth the auction house" not in text
    assert ("What a vendor pays (not compared with the auction house) (6; together 27s 26c):\n"
            "- [Glinting Steel Sword] (uncommon) — vendor 18s 30c; auction price not known") in text
    assert "the game did not respond in time" in format_sell_prices(entries, None)
    some = {**GAME_PRICES, "items": GAME_PRICES["items"][:2], "missing": []}  # the add-on answered for two items
    text = format_sell_prices(entries, some)
    assert "- [Rabbit's Foot] (junk) — vendor 1s 5c; auction price not checked" in text
    assert "Worth the auction house (1; together 7s 0c):" in text

def test_compare_sell_prices_tool_round_trip(env):  # noqa: F811
    """The app prices the bags from the game data in one auction_search call and gives the model the lists."""
    from foreverai.service import InboxWriter, TurnContext
    svc, addons, _ = env
    writer = InboxWriter(addons, "S", 1, 20, 4)
    writer.send({"kind": "answer", "final": False, "msgId": 5, "text": "", "phase": "x"})
    ctx = TurnContext({"msgId": 5}, writer, {"bags": BAGS}, threading.Event(), [])
    phases = []
    ctx.phase_cb = phases.append
    out = {}
    t = threading.Thread(target=lambda: out.update(r=svc.run_tool(ctx, "compare_sell_prices", {})))
    t.start()
    deadline, req = time.time() + 5, None
    while time.time() < deadline and not req:
        req = (_read_inbox(addons, 1).get("requests") or [None])[0]
        time.sleep(0.02)
    assert req["op"] == "auction_search" and len(req["items"]) == 7 and req["maxAgeDays"] == 30
    _send(svc, {"type": "game_result", "id": req["id"], "result": {**GAME_PRICES, "ahOpen": False}})
    t.join(5)
    text, err = out["r"]
    assert not err and "Better sold to a vendor (4; together 5s 66c):" in text
    assert phases == ["Comparing vendor and auction prices…"] and ctx.ah_open is False
    text, err = svc.run_tool(TurnContext({"msgId": 6}, writer, {}, threading.Event(), []), "compare_sell_prices", {})
    assert err and "no bags section" in text

LUA_HARNESS = r"""
local addonDir = arg[1]
local now = 1000000
function time(t) if t then return 1577836800 end return now end -- day 0 = 2020-01-01
local clock = 0
function debugprofilestop() clock = clock + 0.01 return clock end
local frameT = 0
function GetTime() return frameT end
local frames = {}
function CreateFrame()
  local f = { scripts = {}, shown = true }
  function f:SetScript(k, fn) self.scripts[k] = fn end
  function f:Hide() self.shown = false end
  function f:Show() self.shown = true end
  function f:RegisterEvent() end
  frames[#frames + 1] = f
  return f
end
local today = math.floor((now - 1577836800) / 86400)
local items = {
  [100] = { "Gleaming Sword", 2, 7, "INVTYPE_WEAPON", 25, 20 },
  [101] = { "Huge Sword", 2, 7, "INVTYPE_WEAPON", 40, 45 },      -- too high level
  [102] = { "Cheap Sword", 2, 7, "INVTYPE_WEAPONMAINHAND", 10, 5 },
  [103] = { "Battle Axe", 2, 1, "INVTYPE_2HWEAPON", 25, 20 },    -- wrong type
  [104] = { "Pricey Sword", 2, 7, "INVTYPE_WEAPON", 30, 25 },    -- over budget
  [105] = { "Old Sword", 2, 7, "INVTYPE_WEAPON", 22, 18 },       -- price too old
  [106] = { "Slow Sword", 2, 7, "INVTYPE_WEAPON", 21, 18 },      -- not cached yet, arrives later
}
local cached = { [100] = true, [101] = true, [102] = true, [103] = true, [104] = true, [105] = true }
items[109] = { "Tinker Sword", 2, 7, "INVTYPE_WEAPON", 26, 20 } -- needs a profession the character does not have
cached[109] = true
items[107] = { "Linen Cloth", 7, 5, "", 5, 0 }  -- not cached yet: its name comes from the server
items[108] = { "Wool Cloth", 7, 5, "", 15, 0 }  -- no price in the data
local instantNames = { ["Gleaming Sword"] = 100, ["Old Sword"] = 105, ["Wool Cloth"] = 108 } -- names the client resolves
C_Item = {
  GetItemInfoInstant = function(id)
    if type(id) == "string" then id = instantNames[id] if not id then return nil end end
    local i = items[id] return id, "Weapon", "Swords", i[4], 0, i[2], i[3]
  end,
  GetItemInfo = function(id)
    local i = items[id]
    if not cached[id] then return nil end
    return i[1], "|cff1eff00|Hitem:" .. id .. "|h[" .. i[1] .. "]|h|r", 2, i[5], i[6], "Weapon", "One-Handed Swords"
  end,
  IsItemDataCachedByID = function(id) return cached[id] == true end,
  RequestLoadItemDataByID = function(id) cached[id] = "soon" end,
}
GetInventoryItemLink = function() return nil end
local ns = { safe = function(fn, ...) return fn(...) end, stripCodes = function(s) return s end,
  On = function() end }
ns.Collect = { knownLinks = { ["Cheap Sword"] = "|cffffffff|Hitem:102::::|h[Cheap Sword]|h|r" },
  ItemFacts = function(link, compare)
  local id = tonumber(link:match("item:(%d+)"))
  local i = items[id]
  return { quality = "uncommon", stats = "DPS +" .. i[5], tooLowLevel = i[6] > 30 or nil, requiresLevel = i[6],
           cannotUse = id == 109 and "not met: Requires Engineering (150)" or nil,
           vsEquipped = "instead of (empty slot): DPS +" .. i[5] }
end, unmetFrom = "tooltip data" }
Auctionator = { API = { v1 = { MultiSearchExact = function() end } }, State = { CurrentRealm = "Forever" } }
local function day(d) return { [tostring(today - d)] = 1 } end
AUCTIONATOR_PRICE_DATABASE = { __dbversion = 6, Forever = {
  ["100"] = { m = 450000, h = day(0), a = { [tostring(today)] = 3 } },
  ["101"] = { m = 600000, h = day(1) }, ["102"] = { m = 10000, h = day(2) }, ["103"] = { m = 200000, h = day(0) },
  ["104"] = { m = 2000000, h = day(0) }, ["105"] = { m = 300000, h = day(20) }, ["106"] = { m = 350000, h = day(0) },
  ["g:100:30"] = { m = 900000, h = day(0) }, ["p:55"] = { m = 1, h = day(0) },
  ["107"] = { m = 1200, h = day(1), a = { [tostring(today - 1)] = 40 } }, ["109"] = { m = 400000, h = day(0) } } }
AUCTIONATOR_SAVEDVARS = { TimeOfLastReplicateScan = now - 3 * 86400 } -- a full scan 3 days ago saw all but Old Sword
local chunk = assert(loadfile(addonDir .. "/Auction.lua"))
chunk("ForeverAI", ns)
local result
ns.Auction.Search({ classIDs = { 2 }, subclassIDs = { 7 }, equipLocs = { "INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND" },
  maxCopper = 1000000, maxAgeDays = 7, usableOnly = true, upgradesOnly = false, sort = "upgrade", limit = 15 },
  function(r) result = r end)
for _ = 1, 200 do
  if result then break end
  frameT = frameT + 0.05
  for _, f in ipairs(frames) do
    if f.shown and f.scripts.OnUpdate then f.scripts.OnUpdate(f, 0.016) end
  end
  if cached[106] == "soon" and frameT > 1 then cached[106] = true end -- the server answers after a second
end
assert(result, "no result")
local names = {}
for _, it in ipairs(result.items) do names[#names + 1] = it.name .. "=" .. it.price .. "@" .. it.seen end
print("ITEMS " .. table.concat(names, ","))
print("META " .. tostring(result.source) .. "|" .. tostring(result.dbItems) .. "|" .. tostring(result.matched) .. "|"
  .. tostring(result.lastScan) .. "|" .. tostring(result.skipped["too high level"]) .. "|" .. tostring(result.notLoaded))
local left = {}
for _, u in ipairs(result.unusable or {}) do left[#left + 1] = u.name .. "=" .. u.price .. ":" .. u.why end
print("UNUSABLE " .. tostring(result.skipped["not usable"]) .. "|" .. tostring(result.usableCheck) .. "|" .. table.concat(left, ","))
local ok, why = ns.Auction.ShowInAuctionator({ "Gleaming Sword" })
print("SHOW " .. tostring(ok) .. "|" .. tostring(why))
-- a shopping list: only through Auctionator's public API, each item an exact search, also with the AH closed
local function j(...)
  local t = {}
  for i = 1, select("#", ...) do t[i] = tostring((select(i, ...))) end
  return table.concat(t, "|")
end
print("SAVE0 " .. j(ns.Auction.SaveAsList("ForeverAI: Faire", { "Small Furry Paw" })))
local saved = {}
Auctionator.API.v1.CreateShoppingList = function(caller, name, strings)
  saved[#saved + 1] = caller .. ">" .. name .. ">" .. table.concat(strings, ",")
end
print("SAVE1 " .. j(ns.Auction.SaveAsList("ForeverAI: Faire",
  { "Small Furry Paw", 'Torn "Bear" Pelt;^', "", "Soft Bushy Tail" })) .. "|" .. saved[1])
print("SAVE2 " .. j(ns.Auction.SaveAsList(nil, { "Linen Cloth" })) .. "|" .. saved[2])
print("SAVE3 " .. j(ns.Auction.SaveAsList("x", { "", '"' })) .. "|" .. #saved)
Auctionator.API.v1.CreateShoppingList = function() error("list broken") end
local okErr, err = ns.Auction.SaveAsList("x", { "Linen Cloth" })
print("SAVE4 " .. tostring(okErr) .. "|" .. (tostring(err):gsub("^.-: ", "")))

-- exact items: ids, names the client knows, a name from the player's bags, a name only the price data's cached items
-- have, an item whose name must be loaded, one without a price, an unknown name; the price data stays untouched
local before = 0
for _ in pairs(AUCTIONATOR_PRICE_DATABASE.Forever) do before = before + 1 end
local function run(req)
  local res
  ns.Auction.Search(req, function(r) res = r end)
  for _ = 1, 400 do
    if res then break end
    frameT = frameT + 0.05
    for _, f in ipairs(frames) do
      if f.shown and f.scripts.OnUpdate then f.scripts.OnUpdate(f, 0.016) end
    end
    if cached[107] == "soon" and frameT > 20 then cached[107] = true end
  end
  return res
end
local function show(res)
  local found, gone = {}, {}
  for _, it in ipairs(res.items) do found[#found + 1] = it.name .. "=" .. it.price .. "@" .. it.seen .. "#" .. tostring(it.available) end
  for _, m in ipairs(res.missing or {}) do
    gone[#gone + 1] = tostring(m.name) .. ":" .. tostring(m.id) .. ":" .. m.why
      .. (m.lastPrice and (":" .. m.lastPrice .. "@" .. m.lastSeen) or "")
  end
  return table.concat(found, ",") .. " || " .. table.concat(gone, ";")
end
local exact = run({ items = { { id = 100 }, { name = "Old Sword" }, { name = "Cheap Sword" }, { name = "Battle Axe" },
  { id = 107 }, { name = "Wool Cloth" }, { name = "Nothing Like This" } }, maxAgeDays = 30 })
print("EXACT " .. show(exact))
print("EXACTMETA " .. tostring(exact.exact) .. "|" .. exact.matched .. "|" .. exact.maxAgeDays .. "|" .. tostring(exact.lastScan))
print("EXACT7 " .. show(run({ items = { { name = "Old Sword" }, { id = 100 } }, maxAgeDays = 7 })))
-- asked for by the player: the search returns it, and says why the character cannot use it
local all, flagged = run({ classIDs = { 2 }, subclassIDs = { 7 }, maxCopper = 1000000, maxAgeDays = 7, usableOnly = false,
  sort = "upgrade", limit = 15 }), {}
for _, it in ipairs(all.items) do if it.cannotUse then flagged[#flagged + 1] = it.name .. ":" .. it.cannotUse end end
print("ASKED " .. tostring(all.unusable) .. "|" .. table.concat(flagged, ","))
local one = run({ items = { { id = 109 }, { id = 101 } }, maxAgeDays = 30 })
print("EXACTUSE " .. one.items[1].name .. ":" .. tostring(one.items[1].cannotUse) .. "|" .. one.items[2].name .. ":"
  .. tostring(one.items[2].tooLowLevel))
local after = 0
for _ in pairs(AUCTIONATOR_PRICE_DATABASE.Forever) do after = after + 1 end
print("UNTOUCHED " .. tostring(before == after) .. "|" .. AUCTIONATOR_PRICE_DATABASE.Forever["100"].m)

-- the modern AH's full scan (TimeOfLastBrowseScan) an hour ago saw most items: what it did not see is no longer listed
local SWORDS = { classIDs = { 2 }, subclassIDs = { 7 }, equipLocs = { "INVTYPE_WEAPON", "INVTYPE_WEAPONMAINHAND" },
  maxCopper = 1000000, maxAgeDays = 7, usableOnly = true, upgradesOnly = false, sort = "upgrade", limit = 15 }
local function list(res)
  local out = {}
  for _, it in ipairs(res.items) do out[#out + 1] = it.name .. (it.pawn and (" [" .. it.pawn .. "]") or "") end
  return table.concat(out, ",")
end
AUCTIONATOR_SAVEDVARS = { TimeOfLastBrowseScan = now - 3600 }
local fresh = run(SWORDS)
print("FRESH " .. list(fresh) .. "|" .. fresh.lastScan .. "|" .. fresh.skipped["no longer listed (not in the last full scan)"])
print("FRESHEXACT " .. show(run({ items = { { id = 102 }, { id = 100 }, { id = 107 } }, maxAgeDays = 30 })))
-- a full scan the player stopped early (it saw fewer items than the week before it): the older prices stay in
local full = AUCTIONATOR_PRICE_DATABASE.Forever
AUCTIONATOR_PRICE_DATABASE.Forever = { ["100"] = full["100"], ["101"] = full["101"], ["102"] = full["102"],
  ["107"] = full["107"] }
local partial = run(SWORDS)
print("PARTIAL " .. list(partial) .. "|" .. partial.lastScan)
AUCTIONATOR_PRICE_DATABASE.Forever = full
-- no full scan known (only searches): no cutoff, and no scan time
AUCTIONATOR_SAVEDVARS = {}
local none = run(SWORDS)
print("NOSCAN " .. list(none) .. "|" .. tostring(none.lastScan))
-- Pawn: the upgrades first, then the rest by their value in the player's scale, with the equipped item's value
AUCTIONATOR_SAVEDVARS = { TimeOfLastBrowseScan = now - 3 * 86400 }
local values = { [100] = 30, [102] = 10, [106] = 50 }
PawnIsInitialized = true
PawnGetItemData = function(link)
  local id = tonumber(link:match("item:(%d+)"))
  return { ID = id, Values = { { "Hunter", values[id] or 0, 0 } } }
end
PawnIsItemAnUpgrade = function(item)
  if item.ID == 102 then return { { PercentUpgrade = 0.05, LocalizedScaleName = "Hunter" } } end
  return {}
end
PawnGetItemDataForInventorySlot = function(slot) return { Values = { { "Hunter", 60, 0 } } } end
GetInventoryItemLink = function(unit, slot) if slot == 16 then return "[Fang of Magmatus]" end end
local pawn = run(SWORDS)
print("PAWN " .. list(pawn) .. "|" .. pawn.upgradeMethod)
"""

def _harness(tmp_path) -> dict:
    """The lines the harness prints, by their first word: run by lua5.1 where it is installed, else by the Lua 5.1 the
    add-on simulator uses."""
    if shutil.which("lua5.1"):
        script = tmp_path / "harness.lua"
        script.write_text(LUA_HARNESS)
        run = subprocess.run(["lua5.1", str(script), str(ADDON)], capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        out = run.stdout
    else:
        from lupa import lua51
        lua, printed = lua51.LuaRuntime(unpack_returned_tuples=True), []
        lua.globals().print = lambda *a: printed.append("\t".join(str(x) for x in a))
        lua.globals().arg = lua.table_from([str(ADDON)])
        lua.execute(LUA_HARNESS)
        out = "\n".join(printed)
    return dict(line.split(" ", 1) for line in out.strip().splitlines())

def test_auction_lua_reads_auctionator_prices(tmp_path):
    """Auction.lua against a mocked game: filters, item data loading, sorting and the Auctionator search."""
    lines = _harness(tmp_path)
    items = lines["ITEMS"].split(",")
    # in budget, right type, recent, usable; the late-loading item is included; the empty slot makes all upgrades
    assert sorted(i.split("=")[0] for i in items) == ["Cheap Sword", "Gleaming Sword", "Slow Sword"]
    assert "Gleaming Sword=45g 0s 0c@today" in items and "Cheap Sword=1g 0s 0c@2 days ago" in items
    source, db_items, matched, last_scan, too_high, not_loaded = lines["META"].split("|")
    assert source == "Auctionator" and db_items == "11" and matched == "3" and last_scan == "3 days ago (full scan)"
    assert too_high == "1" and not_loaded == "nil"
    assert lines["SHOW"] == "false|Open the auction house first."
    assert lines["SAVE0"] == "false|This Auctionator cannot save shopping lists."  # an Auctionator without the API
    assert lines["SAVE1"] == ('true|ForeverAI: Faire|ForeverAI>ForeverAI: Faire>"Small Furry Paw","Torn Bear Pelt",'
                              '"Soft Bushy Tail"')
    assert lines["SAVE2"] == 'true|ForeverAI|ForeverAI>ForeverAI>"Linen Cloth"'  # the add-on's own list name
    assert lines["SAVE3"] == "false|Nothing to save.|2"
    assert lines["SAVE4"] == "false|list broken"
    json.dumps(lines)

def test_auction_lua_leaves_out_what_the_character_cannot_use(tmp_path):
    """From the game: the answer recommended [Green Tinted Goggles], which requires Engineering (150), to a character
    without Engineering. A search leaves such an item out and names it with what it requires; asked for, it comes back
    with the requirement on it; and the prices of exact items (the shopping list) carry it too."""
    lines = _harness(tmp_path)
    assert "Tinker Sword" not in lines["ITEMS"]
    assert lines["UNUSABLE"] == "1|tooltip data|Tinker Sword=40g 0s 0c:not met: Requires Engineering (150)"
    assert lines["ASKED"] == "nil|Tinker Sword:not met: Requires Engineering (150)"
    assert lines["EXACTUSE"] == "Tinker Sword:not met: Requires Engineering (150)|Huge Sword:requires level 45"

def test_auction_lua_exact_items(tmp_path):
    """Exact items (a shopping list): ids and names resolved without a search, prices in one pass, names loaded."""
    lines = _harness(tmp_path)
    found, missing = lines["EXACT"].split(" || ")
    # in the requested order; the gear's cheapest variant; the late name arrives; the bags name the Cheap Sword;
    # the Battle Axe is found by the names of the priced items the client has cached; the Old Sword's offer was gone
    # by the full scan 3 days ago, so its price is only the last one seen
    assert found.split(",") == ["Gleaming Sword=45g 0s 0c@today#3", "Cheap Sword=1g 0s 0c@2 days ago#nil",
                                "Battle Axe=20g 0s 0c@today#nil", "Linen Cloth=12s 0c@yesterday#40"]
    assert missing.split(";") == ["Old Sword:105:not listed in the last full scan:30g 0s 0c@20 days ago",
                                  "Wool Cloth:108:no auction price seen in the last 30 days",
                                  "Nothing Like This:nil:unknown item name (not in the game's item cache): give the item id"]
    assert lines["EXACTMETA"] == "true|4|30|3 days ago (full scan)"
    assert lines["EXACT7"] == "Gleaming Sword=45g 0s 0c@today#3 || Old Sword:105:no auction price seen in the last 7 days"
    assert lines["UNTOUCHED"] == "true|450000"  # ForeverAI only reads Auctionator's data


def test_auction_lua_leaves_out_what_the_last_full_scan_did_not_see(tmp_path):
    """Prices of items the last full scan did not see are offers that are gone (the prices the player called wrong):
    left out of a search, the last price seen for an exact item. A scan stopped early or no full scan cuts nothing."""
    lines = _harness(tmp_path)
    # the scan an hour ago saw the Gleaming and the Slow Sword, not the Cheap Sword (2 days) or the Huge Sword
    # (yesterday); the modern AH's TimeOfLastBrowseScan is the scan's time
    assert lines["FRESH"] == "Slow Sword,Gleaming Sword|1 h ago (full scan)|2"
    found, missing = lines["FRESHEXACT"].split(" || ")
    assert found == "Gleaming Sword=45g 0s 0c@today#3"
    assert missing.split(";") == ["Cheap Sword:102:not listed in the last full scan:1g 0s 0c@2 days ago",
                                  "Linen Cloth:107:not listed in the last full scan:12s 0c@yesterday"]
    assert lines["PARTIAL"] == ("Cheap Sword,Gleaming Sword|1 h ago (full scan, stopped early: older prices are "
                                "included)")
    assert lines["NOSCAN"] == "Cheap Sword,Slow Sword,Gleaming Sword|nil"


def test_auction_lua_ranks_gear_by_pawn_value(tmp_path):
    """"The best swords": with Pawn the upgrades come first, then the other items by their value in the player's
    scale, not by price, so the best ones are there also when none of them beats the equipped weapon."""
    lines = _harness(tmp_path)
    items, method = lines["PAWN"].split("|")
    assert items.split(",") == [
        "Cheap Sword [+5.0% upgrade (Hunter); value 10.0 vs 60.0 for [Fang of Magmatus]]",
        "Slow Sword [not an upgrade; value 50.0 vs 60.0 for [Fang of Magmatus]]",
        "Gleaming Sword [not an upgrade; value 30.0 vs 60.0 for [Fang of Magmatus]]"]
    assert method == "Pawn (the player's scales)"

def test_swords_and_bag_prices_end_to_end_in_the_simulator(tmp_path):
    """The two questions through the add-on and the app: the add-on's real Auction.lua and Collect.lua on the
    simulator's market (lua/market.lua: Auctionator after a full scan an hour ago, swords on offer, things in the
    bags), the scripted AI calling the tools as a model would."""
    pytest.importorskip("lupa")
    from datetime import datetime

    from addonsim import App, Sim
    game, data = tmp_path / "game", tmp_path / "data"
    swords = {"text": "Swords.", "tools": [{"name": "search_auction_house",
                                            "args": {"weapon_type": "1h_sword", "max_price_gold": 50}}]}
    bags = {"text": "Vendor list.", "tools": [{"name": "compare_sell_prices", "args": {}}]}
    with App(game, data, replies=[swords, bags], pool=60) as app:
        sim = Sim(game, realtime=True, clock=datetime(2026, 11, 11, 18, 0), market=True).start()
        try:
            sim.ask("Pokaż mi najlepsze miecze jednoręczne, które mogę nosić poniżej 50 golda")
            sim.wait_answer(60)
            sim.ask("Jakie są itemy w moich bagach, które mogę sprzedać u vendora, bo nie opłaca mi się ich "
                    "sprzedawać na AH")
            sim.wait_answer(60)
            assert sim.errors == []
        finally:
            sim.close()
    (found, ), (sell, ) = ([r["result"] for r in app.tool_results if r["tool"] == name]
                           for name in ("search_auction_house", "compare_sell_prices"))
    # the hunter has Swords: every one-handed sword in the budget it can wear, best first (no Pawn: by damage per
    # second over the worn sword), with the scan's prices; the sword only an older scan saw and the one above the
    # hunter's level are left out, the two-hander is not a one-handed sword
    rows = [line for line in found.splitlines() if line.startswith("- [")]
    assert [r.split("]")[0][3:] for r in rows] == ["Brackwater Saber", "Glinting Steel Sword", "Ironwood Blade",
                                                   "Militia Shortsword"]
    assert rows[0].startswith("- [Brackwater Saber] (item 3102) — 41g 50s 0c | seen today, 1 available | uncommon")
    assert "last scan: 1 h ago (full scan)" in found and "cannotUse" not in found
    assert "Left out: 1 no longer listed (not in the last full scan), 1 too high level." in found
    assert "Better sold to a vendor (5; together 5s 81c):" in sell and "Worth the auction house (2; together 9s 16c):" in sell
    assert "- [Linen Cloth] x12 (crafting material) — vendor 1s 56c (13c each); AH 1s 20c after the cut" in sell
    assert "- [Slimy Murloc Scale] x4 (crafting material) — vendor 3s 0c (75c each); not listed on the AH in the last " \
           "scan (last seen 3 days ago at 1s 0c)" in sell
    assert "Soulbound (only a vendor buys these) (1; together 1s 36c):\n- [Fishliver Oil] x2" in sell
    assert "Not priced (quest items, conjured items, no sale value): [Hearthstone], [Crawler Leg]" in sell

def test_the_search_names_what_it_left_out_and_why():
    text = format_auction({"source": "Auctionator", "dbItems": 900, "matched": 1, "sort": "upgrade",
                           "skipped": {"not usable": 14, "too high level": 2}, "usableCheck": "tooltip data",
                           "unusable": [{"id": 4385, "name": "Green Tinted Goggles", "price": "16s 0c",
                                         "why": "not met: Requires Engineering (150)"}],
                           "items": [{"id": 1, "name": "Cured Leather Cap", "price": "20s 0c"}]})
    assert ("Left out because this character cannot use them: [Green Tinted Goggles] 16s 0c (not met: Requires "
            "Engineering (150)); and 13 more.") in text
    assert "Only armor and weapon skills were checked" not in text
    # a client that gives no requirements: the model is told the check was not whole
    assert "Only armor and weapon skills were checked" in format_auction({"items": [], "usableCheck": "skills only"})
    # exact items say it on their line
    text = format_auction({"exact": True, "items": [{"id": 4385, "name": "Green Tinted Goggles", "price": "16s 0c",
                                                     "cannotUse": "not met: Requires Engineering (150)"}]})
    assert "- [Green Tinted Goggles] (item 4385) — 16s 0c | cannotUse: not met: Requires Engineering (150)" in text
