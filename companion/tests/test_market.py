"""Selling on the auction house and to a vendor: the player asks, the AI names the items, the app sets the price by
rules from what the auction house lists at that moment, and nothing leaves the bags before a click on the card.

The player's words: "wskaże jakie rzeczy wystawić, za ile ... do akceptacji; itemy które są coś warte powinien sprawdzać,
czy mają odpowiednią ilość aukcji, żeby potwierdzić, że to obiektywna cena ... żeby nie sprzedał przypadkiem czegoś
rare!!" The examples are the meat in the player's bags.

This server has the game's newer auction house (AuctionHouseFrame, C_AuctionHouse): what stacks is sold by the piece.
The first version was written for the old one and stopped loading in the game ("attempt to call a nil value"): the
auction house of the simulator below answers the way the game's does, and the simulator refuses the old one's events."""

import pytest

from foreverai import market
from foreverai.tools import validate_vendor

BEAR = {"id": 3173, "item": "[Bear Meat]", "count": 13, "sellCopper": 15, "quality": "common", "tooltip": "Crafting Reagent"}
CLAM = {"id": 5503, "item": "[Clam Meat]", "count": 8, "sellCopper": 16, "quality": "common", "tooltip": "Crafting Reagent"}
COYOTE = {"id": 2673, "item": "[Coyote Meat]", "count": 1, "sellCopper": 10, "quality": "common", "tooltip": "Crafting Reagent"}
SNOUT = {"id": 731, "item": "[Goretusk Snout]", "count": 4, "sellCopper": 27, "quality": "common", "tooltip": "Crafting Reagent"}
PACKAGE = {"id": 2806, "item": "[Package for Stormpike]", "count": 1, "bound": True, "tooltip": "Quest Item\nUnique"}
BELT = {"id": 1154, "item": "[Belt of the People's Militia]", "count": 1, "sellCopper": 102, "quality": "common", "bound": True,
        "facts": {"stats": "NONE — this item has no stats (read from the game)"}}
KNIFE = {"id": 7005, "item": "[Skinning Knife]", "count": 1, "sellCopper": 2, "quality": "common", "facts": {"stats": "DPS +1.3"}}
RING = {"id": 9999, "item": "[Glowing Ring]", "count": 1, "sellCopper": 300, "quality": "uncommon", "facts": {"stats": "Agility +3"}}
PELT = {"id": 4865, "item": "[Ruined Pelt]", "count": 5, "sellCopper": 5, "quality": "poor"}
SNAP = {"bags": {"items": [BEAR, CLAM, COYOTE, SNOUT, PACKAGE, BELT, KNIFE, RING, PELT]},
        "windows": {"auctionHouse": {"open": True, "lengthsHours": "2, 8, 24"}, "merchant": {"canRepair": False}}}


def levels(*rows):
    return [{"unit": u, "auctions": n, "units": q} for u, n, q in rows]


def bear_market(**more):
    """Bear meat as the auction house lists it: three prices, six sellers, the cheapest 19c a piece."""
    m = {"id": 3173, "name": "Bear Meat", "quality": 1, "maxStack": 10, "vendor": 15, "have": 13, "commodity": True,
         "auctions": 3, "sellers": 6, "units": 88, "own": 0, "levels": levels((19, 1, 30), (20, 1, 44), (25, 1, 14))}
    m.update(more)
    return m


# ------------------------------------------------------------------------------------------- the price of a piece

def test_a_price_confirmed_by_several_sellers_is_undercut_by_a_copper():
    unit, basis, why = market.price_of(bear_market(), 15, 8)
    assert (unit, why) == (18.0, None)
    # six sellers on three price lines: the offers are counted by the sellers
    assert basis == "sure price: 6 offers from 6 sellers, lowest 19c each"


def test_a_few_offers_give_a_price_marked_as_weak():
    m = bear_market(auctions=2, sellers=2, levels=levels((42, 1, 8), (45, 1, 4)), vendor=27,
                    history={"last": 41, "days": [{"ago": 1, "low": 38, "high": 45}, {"ago": 3, "low": 45, "high": 45}]})
    unit, basis, why = market.price_of(m, 27, 8)
    assert unit == 41.0 and basis.startswith("weakly confirmed: 2 offers from 2 sellers, lowest 42c each")
    # many lines, all of one or two sellers: their price is not the market's
    assert market.price_of(bear_market(auctions=6, sellers=2), 15, 8)[1].startswith("weakly confirmed: ")
    # a price far from what the scans of the last days saw says so
    odd = bear_market(auctions=2, sellers=2, levels=levels((200, 2, 30)),
                      history={"days": [{"ago": 1, "low": 20, "high": 22}, {"ago": 2, "low": 21, "high": 24}]})
    assert market.price_of(odd, 15, 8)[1].endswith("; your scans saw 20c–21c each on 2 days of the last two weeks")


def test_one_offer_or_none_is_no_price():
    one = bear_market(auctions=1, sellers=1, levels=levels((60, 1, 5)),
                      history={"days": [{"ago": 2, "low": 20, "high": 60}]})
    unit, basis, why = market.price_of(one, 16, 8)
    assert unit is None
    assert why == ("only one offer now (60c each): no sure price; your scans saw 20c each on 1 day of the last two "
                   "weeks. The player can name a price")
    none = bear_market(auctions=0, sellers=0, levels=[])
    assert market.price_of(none, 16, 8)[2] == "nobody sells it now: no sure price; no price history either. The player can name a price"
    # the player names one: it stands, with what the market shows beside it
    assert market.price_of(one, 16, 8, asked=50) == (50.0, "price set by the player; now: 1 offer from 1 seller, lowest 60c each", None)
    assert market.price_of(none, 16, 8, asked=50)[1] == "price set by the player; nobody else sells it now"


def test_a_lowest_offer_far_under_the_rest_is_a_slip_not_the_market():
    m = bear_market(auctions=4, levels=levels((5, 1, 10), (19, 1, 30), (20, 2, 34)))
    unit, basis, _ = market.price_of(m, 3, 8)
    assert unit == 18.0 and basis.endswith("; 1 at 5c left aside (under half of the next price)")
    # three offers at that price are the market, however low
    crowd = bear_market(auctions=9, levels=levels((5, 3, 30), (19, 3, 30), (20, 3, 14)))
    assert market.price_of(crowd, 1, 8)[0] == 4.0


def test_the_players_own_lowest_auction_is_matched_not_undercut():
    m = bear_market(own=1, ownLow=18)
    unit, basis, _ = market.price_of(m, 15, 8)
    assert unit == 18.0 and basis.endswith("; matched to your own auction, the lowest there is")
    # someone is under it: the market is undercut as usual
    assert market.price_of(bear_market(own=1, ownLow=25), 15, 8)[0] == 18.0


def test_what_a_vendor_pays_about_as_much_for_does_not_go_to_auction():
    m = bear_market(vendor=10, levels=levels((12, 2, 30), (13, 3, 40)))
    unit, _, why = market.price_of(m, 10, 8)
    assert unit is None
    assert why == ("the auction house pays about what a vendor does (11c each less its cut, vendor 10c): sell it to a vendor")
    # bear meat at 18c against 15c at the vendor is worth the auction
    assert market.price_of(bear_market(), 15, 8)[0] == 18.0


def test_the_length_is_the_games_nearest_to_what_was_asked():
    """"Jeżeli nie ma przez usera podane, na ile czasu, to domyślnie 12h": this server has 2, 8 and 24 hours."""
    assert market.nearest_hours(None, [2, 8, 24]) == 8
    assert market.nearest_hours(12, [2, 8, 24]) == 8
    assert market.nearest_hours(24, [2, 8, 24]) == 24
    assert market.nearest_hours(48, [2, 8, 24]) == 24
    assert market.nearest_hours(1, None) == 2
    assert market.nearest_hours(12, [12, 24, 48]) == 12  # a client that has it


# ------------------------------------------------------------------------------------------- the proposal

def check_result():
    return {"durations": [2, 8, 24], "ahOpen": True, "items": [
        bear_market(),
        {"id": 5503, "name": "Clam Meat", "vendor": 16, "have": 8, "commodity": True, "auctions": 1, "sellers": 1,
         "levels": levels((60, 1, 5)),
         "history": {"days": [{"ago": 1, "low": 20, "high": 60}, {"ago": 4, "low": 35, "high": 40}]}},
        {"id": 2673, "name": "Coyote Meat", "vendor": 10, "have": 1, "commodity": True, "auctions": 2, "sellers": 4,
         "levels": levels((11, 2, 30))},
        {"id": 731, "name": "Goretusk Snout", "vendor": 27, "have": 4, "commodity": True, "auctions": 2, "sellers": 2,
         "levels": levels((42, 1, 4), (45, 1, 4))}]}


def test_the_card_for_the_meat_in_the_bags():
    args = {"items": [{"item": "Bear Meat"}, {"item": "Clam Meat"}, {"item": 2673}, {"item": "Goretusk Snout"},
                      {"item": "Package for Stormpike"}, {"item": "Wool Cloth"}], "reason": "na AH"}
    picked, left = market.wanted_items(args, SNAP)
    assert [p["bag"]["id"] for p in picked] == [3173, 5503, 2673, 731]
    assert left == [{"name": "Package for Stormpike", "why": "bound to the character or a quest item: it cannot be auctioned"},
                    {"name": "Wool Cloth", "why": "not in the bags"}]
    action, text = market.build_post(picked, left, check_result(), None, "na AH", 7)
    assert action["op"] == "auction_post" and action["hours"] == 8 and action["id"] == 7
    # sold by the piece: one auction of all thirteen, at a price for one
    assert [(lot["name"], lot["count"], lot["unit"]) for lot in action["lots"]] == [("Bear Meat", 13, 18.0), ("Goretusk Snout", 4, 41.0)]
    assert action["lots"][0]["basis"] == "sure price: 6 offers from 6 sellers, lowest 19c each"
    assert action["lots"][1]["basis"].startswith("weakly confirmed: 2 offers from 2 sellers")
    why = {s["name"]: s["why"] for s in action["leftOut"]}
    assert why["Clam Meat"].startswith("only one offer now (60c each): no sure price; your scans saw 20c–35c each on 2 days")
    assert why["Coyote Meat"].startswith("the auction house pays about what a vendor does (10c each less its cut, vendor 10c)")
    # 13 × 18c + 4 × 41c = 3s 98c; the deposit is an estimate from the vendor value
    assert action["total"].startswith("3s 98c in all when everything sells; deposit about ")
    assert (action["sum"], action["net"], action["cut"], action["depositExact"]) == (398, 378, 5, False)
    # the game's own deposit for the thirteen at each of its lengths (2, 8, 24 h): the one for 8 h, not an estimate
    res = check_result()
    for m in res["items"]:
        m["deposits"] = [25, 100, 300]
    exact, said = market.build_post(*market.wanted_items({"items": ["Bear Meat"]}, SNAP), res, None, "", 1)
    assert (exact["deposit"], exact["depositExact"]) == (100, True) and "deposit 1s 0c (returned with a sale)" in said
    # ten of the thirteen: ten thirteenths of it
    part, _ = market.build_post(*market.wanted_items({"items": [{"item": "Bear Meat", "count": 10}]}, SNAP), res, None, "", 1)
    assert part["deposit"] == 77
    assert "- [Bear Meat] ×13 at 18c each (2s 34c in all) — sure price: 6 offers from 6 sellers" in text
    assert "one click of the player per item (2 items)" in text and "- [Clam Meat]: only one offer now" in text


def test_a_price_a_count_and_a_length_the_player_names():
    args = {"items": [{"item": "Clam Meat", "price_copper": 50, "count": 5}], "hours": 12}
    picked, left = market.wanted_items(args, SNAP)
    action, text = market.build_post(picked, left, check_result(), 12, "", 1)
    assert [(lot["count"], lot["unit"], lot["hours"]) for lot in action["lots"]] == [(5, 50.0, 8)]
    assert action["lots"][0]["basis"] == "price set by the player; now: 1 offer from 1 seller, lowest 60c each"
    assert "The game has no 12 h auctions (its lengths: 2, 8, 24 h): 8 h is the nearest." in text
    # more than the bags hold is cut to what is there
    picked, left = market.wanted_items({"items": [{"item": "Bear Meat", "count": 99}]}, SNAP)
    assert market.build_post(picked, left, check_result(), None, "", 1)[0]["lots"][0]["count"] == 13


def test_nothing_to_post_says_why():
    picked, left = market.wanted_items({"items": ["Clam Meat", "Coyote Meat"]}, SNAP)
    action, text = market.build_post(picked, left, check_result(), None, "", 1)
    assert action is None and text.startswith("Nothing to post.\nNot posted:\n- [Clam Meat]: only one offer now")
    # an item the game could not look up
    picked, left = market.wanted_items({"items": ["Bear Meat"]}, SNAP)
    res = {"durations": [2, 8, 24], "items": [{"id": 3173, "failed": "the auction house did not answer the search"}]}
    assert "- [Bear Meat]: the auction house did not answer the search" in market.build_post(picked, left, res, None, "", 1)[1]
    # more items than one request looks up: the rest is named, not dropped in silence
    many = {"bags": {"items": [dict(BEAR, id=100 + n, item=f"[Thing {n}]") for n in range(15)]}}
    picked, left = market.wanted_items({"items": [f"Thing {n}" for n in range(15)]}, many)
    assert len(picked) == 12 and [s["name"] for s in left] == ["Thing 12", "Thing 13", "Thing 14"]


# ------------------------------------------------------------------------------------------- own auctions

OWNED = {"ahOpen": True, "auctions": [
    {"n": 1, "auctionID": 501, "id": 3173, "name": "Bear Meat", "count": 10, "unit": 18, "buyout": 180, "left": "8 h"},
    {"n": 2, "auctionID": 502, "id": 3173, "name": "Bear Meat", "count": 3, "unit": 18, "buyout": 54, "left": "8 h"},
    {"n": 3, "auctionID": 503, "id": 731, "name": "Goretusk Snout", "count": 4, "unit": 41, "buyout": 164, "bid": 150, "left": "45 min"},
    {"n": 4, "auctionID": 504, "id": 2589, "name": "Linen Cloth", "count": 20, "unit": 20, "buyout": 400, "sold": True}]}


def test_my_auctions_as_the_model_reads_them():
    text = market.format_owned(OWNED)
    assert "1. [Bear Meat] ×10 — 18c each (1s 80c in all), no bid, 8 h left" in text
    assert "3. [Goretusk Snout] ×4 — 41c each (1s 64c in all), bid 1s 50c, 45 min left" in text
    assert "4. [Linen Cloth] ×20 — 20c each (4s 0c in all), SOLD, the money waits in the mailbox" in text
    assert market.format_owned({"auctions": []}) == "The character has no auctions up."
    assert "not open" in market.format_owned({"error": "the auction house window is not open"})


def test_cancelling_one_auction_by_its_number_or_all_of_an_item():
    """"Możliwość anulowania konkretnej z wystawionych aukcji (też po akceptacji)"."""
    action, text = market.build_cancel({"auctions": [2], "reason": "za tanio"}, OWNED, 5)
    assert action["op"] == "auction_cancel" and action["description"] == "za tanio"
    # the game's own number of the auction goes along: it is that one that is cancelled, wherever it stands by then
    assert action["auctions"] == [{"auctionID": 502, "id": 3173, "name": "Bear Meat", "count": 3, "unit": 18, "left": "8 h"}]
    assert "one click of the player per auction: [Bear Meat] ×3 (18c each)" in text and "deposit" in text
    by_name, _ = market.build_cancel({"auctions": ["bear meat"]}, OWNED, 5)
    assert [a["auctionID"] for a in by_name["auctions"]] == [501, 502]
    assert "HAS A BID" in market.build_cancel({"auctions": [3]}, OWNED, 5)[1]
    # a sold one is no auction to cancel; a number that is not there says so
    gone, text = market.build_cancel({"auctions": [4, 9]}, OWNED, 5)
    assert gone is None and text.startswith("Rejected: nothing to cancel. 4: no such auction")


# ------------------------------------------------------------------------------------------- vendor sales

def guard(prices, scanned=True):
    def check(b):
        price = prices.get(b["id"])
        tool = "Skinning" if b["id"] == 7005 else None
        return market.vendor_guard(b, price, scanned, tool), market.vendor_reason(b, price)
    return check


def test_a_vendor_sale_takes_what_is_worth_the_same_there_and_keeps_the_rest():
    prices = {3173: {"copper": 40, "available": 74}, 2673: {"copper": 11, "available": 30}}
    args = {"op": "sell", "reason": "śmieci", "items": ["Bear Meat", "Coyote Meat", "Ruined Pelt", "Skinning Knife",
                                                         "Glowing Ring", "Belt of the People's Militia", "Clam Meat"]}
    action, text = validate_vendor(args, SNAP, guard(prices))
    sold = {i["name"]: i for i in action["items"]}
    assert set(sold) == {"[Coyote Meat]", "[Ruined Pelt]", "[Belt of the People's Militia]", "[Clam Meat]"}
    assert sold["[Coyote Meat]"]["why"] == "vendor 10c each, auction house 11c: not worth posting"
    assert sold["[Ruined Pelt]"]["why"] == "vendor 5c each, no auctions of it seen"
    assert "[Bear Meat]: KEPT, worth more on the auction house: about 40c each there (74 for sale at the last scan), 15c at the vendor" in text
    assert "[Skinning Knife]: KEPT, needed for Skinning" in text
    assert "[Glowing Ring]: KEPT, uncommon quality: not junk" in text


def test_nothing_rare_goes_by_accident():
    """"Żeby nie sprzedał przypadkiem czegoś rare!!": a green item is never junk, whatever the auction house shows for
    it; and with no scan of the auction house a material has no known price, which is not the same as no value."""
    assert market.vendor_guard(RING, None, True, None) == "uncommon quality: not junk (auction it, or name it yourself to sell it)"
    assert market.vendor_guard(RING, {"copper": 1}, True, None).startswith("uncommon quality")
    assert market.vendor_guard(dict(RING, quality="rare"), None, True, None).startswith("rare quality")
    assert market.vendor_guard(dict(KNIFE), None, True, None) == "gear with stats: not junk"
    assert market.vendor_guard(CLAM, None, False, None) == "a crafting material with no auction price known (scan the auction house first)"
    assert market.vendor_guard(CLAM, None, True, None) is None       # scanned, and nobody sells it: no market
    assert market.vendor_guard(PELT, None, False, None) is None      # grey junk needs no scan
    # one offer far above the vendor price: few offers, but not junk
    assert market.vendor_guard(CLAM, {"copper": 500, "available": 1}, True, None).startswith("worth more on the auction house")


def test_what_the_player_names_is_sold_as_asked():
    args = {"op": "sell", "reason": "x", "items": ["Glowing Ring"], "player_named": True}
    action, text = validate_vendor(args, SNAP, guard({}))
    assert [i["name"] for i in action["items"]] == ["[Glowing Ring]"] and "KEPT" not in text
    args["player_named"] = False
    assert validate_vendor(args, SNAP, guard({}))[0] is None


# ------------------------------------------------------------------------------------------- in the game (simulator)
# An auction house that answers like the game's newer one, as far as the add-on uses it: searches by item, answered
# with an event a moment later; what stacks is listed by price with its sellers; posting by the piece.

GAME = r"""
BAG = { { id = 3173, n = 10 }, { id = 3173, n = 3 }, { id = 731, n = 4 }, { id = 2512, n = 1, gear = true },
        { id = 2806, n = 1, bound = true } }
C_Container.GetContainerNumSlots = function(bag) return bag == 0 and 16 or 0 end
C_Container.GetContainerItemInfo = function(bag, slot)
  local s = bag == 0 and BAG[slot]
  if s and s.n > 0 then return { itemID = s.id, stackCount = s.n, isLocked = s.locked or false } end
end
local NAMES = { [3173] = { "Bear Meat", 15 }, [731] = { "Goretusk Snout", 27 }, [2512] = { "Bent Staff", 9 }, [2806] = { "Package", 0 } }
C_Item.GetItemInfo = function(id)
  local n = NAMES[id]
  if n then return n[1], "|Hitem:" .. id .. "|h[" .. n[1] .. "]|h", 1, 15, 5, "Trade Goods", "Meat", 10, "", 0, n[2] end
end
READY, SEARCHES, POSTED, CANCELLED, OWNED, NEXT_ID, MONEY = true, {}, {}, {}, {}, 500, 15444
SILENT, EMPTY_FIRST, MORE, REFUSE, THROW, CONFIRM, LOCKED, COST, QUIET, STUCK, CONFIRMED = nil
GetMoney = function() return MONEY end
local CA = C_AuctionHouse
CA.IsThrottledMessageSystemReady = function() return READY end
CA.IsSellItemValid = function(loc) local s = BAG[loc.slot] return not (s and s.bound) end
CA.GetItemCommodityStatus = function(loc) local s = BAG[loc.slot] return (s and s.gear) and 1 or 2 end
CA.MakeItemKey = function(id) return { itemID = id, itemLevel = 0, itemSuffix = 0, battlePetSpeciesID = 0 } end
CA.GetItemKeyInfo = function(key) return { itemName = "x" } end
local function answer(key, kind)
  SEARCHES[#SEARCHES + 1] = kind .. ":" .. key.itemID
  if SILENT then return end
  C_Timer.After(0.3, function()
    if kind == "sell" then FIRE("ITEM_SEARCH_RESULTS_UPDATED", key) else FIRE("COMMODITY_SEARCH_RESULTS_UPDATED", key.itemID) end
  end)
end
CA.SendSearchQuery = function(key, sorts, separate) answer(key, "search") end
CA.SendSellSearchQuery = function(key, sorts, separate) answer(key, "sell") end
CA.HasSearchResults = function(key) return not (EMPTY_FIRST and #SEARCHES < 2) end
CA.HasFullCommoditySearchResults = function(id) return not MORE end
CA.HasFullItemSearchResults = function(key) return true end
CA.GetCommoditySearchResultsQuantity = function(id) local q = 0 for _, r in ipairs(MARKET[id] or {}) do q = q + r.quantity end return q end
CA.GetItemSearchResultsQuantity = function(key) return #(ITEMS[key.itemID] or {}) end
CA.GetNumCommoditySearchResults = function(id) return #(MARKET[id] or {}) end
CA.GetCommoditySearchResultInfo = function(id, i) return (MARKET[id] or {})[i] end
CA.GetNumItemSearchResults = function(key) return #(ITEMS[key.itemID] or {}) end
CA.GetItemSearchResultInfo = function(key, i) return (ITEMS[key.itemID] or {})[i] end
CA.CalculateCommodityDeposit = function(id, duration, count) return 2 * count end
CA.CalculateItemDeposit = function(loc, duration, count) return 30 end
local function take(id, count)
  for _, s in ipairs(BAG) do
    if s.id == id and count > 0 then local t = math.min(s.n, count) s.n = s.n - t count = count - t end
  end
end
local function created(id, count, unit, deposit)
  if REFUSE then return end
  NEXT_ID = NEXT_ID + 1
  local aid = NEXT_ID
  take(id, count)
  MONEY = MONEY - deposit
  OWNED[#OWNED + 1] = { auctionID = aid, itemKey = { itemID = id }, quantity = count, buyoutAmount = unit, status = 0, timeLeftSeconds = 8 * 3600 }
  C_Timer.After(0.3, function() FIRE("AUCTION_HOUSE_AUCTION_CREATED", aid) end)
end
CA.PostCommodity = function(loc, duration, count, unit)
  if THROW then error("Usage: PostCommodity(item, duration, quantity, unitPrice)") end
  POSTED[#POSTED + 1] = table.concat({ "commodity", BAG[loc.slot].id, duration, count, unit }, ",")
  created(BAG[loc.slot].id, count, unit, 2 * count)
  return CONFIRM
end
CA.PostItem = function(loc, duration, count, bid, buyout)
  POSTED[#POSTED + 1] = table.concat({ "item", BAG[loc.slot].id, duration, count, tostring(bid), buyout }, ",")
  created(BAG[loc.slot].id, count, buyout, 30)
end
CA.QueryOwnedAuctions = function() C_Timer.After(0.2, function() FIRE("OWNED_AUCTIONS_UPDATED") end) end
CA.GetNumOwnedAuctions = function() return #OWNED end
CA.GetOwnedAuctionInfo = function(i) return OWNED[i] end
CA.CanCancelAuction = function(id) return not LOCKED end
CA.GetCancelCost = function(id) return COST or 0 end
CA.CancelAuction = function(id)
  CANCELLED[#CANCELLED + 1] = id
  if STUCK then return end
  for i, a in ipairs(OWNED) do if a.auctionID == id then table.remove(OWNED, i) break end end
  if not QUIET then C_Timer.After(0.3, function() FIRE("AUCTION_CANCELED", id) end) end
end
AuctionHouseFrame = { IsShown = function() return AH_SHOWN end,
  CommoditiesSellFrame = { CachePendingPost = function(self, loc, duration, count, unit) CONFIRMED = count .. "x" .. unit end } }
-- bear meat: three prices from six sellers (one of them without a name in the list), and the player's own ten at 30c
MARKET = { [3173] = {
  { unitPrice = 19, quantity = 30, owners = { "Anna", "Bob", "Cid" }, totalNumberOfOwners = 3 },
  { unitPrice = 20, quantity = 44, owners = { "Anna", "Dee", "Eve" }, totalNumberOfOwners = 4 },
  { unitPrice = 25, quantity = 14, owners = { "Bob" }, totalNumberOfOwners = 1 },
  { unitPrice = 30, quantity = 10, owners = { "player" }, totalNumberOfOwners = 1, containsOwnerItem = true, numOwnerItems = 10 } } }
ITEMS = { [2512] = { { buyoutAmount = 120, quantity = 1, owners = { "Fay" }, totalNumberOfOwners = 1 },
                     { buyoutAmount = 150, quantity = 1, owners = { "Gus" }, totalNumberOfOwners = 1 },
                     { bidAmount = 40, quantity = 1, owners = { "Hal" }, totalNumberOfOwners = 1 } } }
"""


@pytest.fixture
def game(tmp_path):
    pytest.importorskip("lupa")
    from addonsim import Sim, install_game
    install_game(tmp_path / "game", pool=40)
    sim = Sim(tmp_path / "game").start()
    sim.slash("/fai")
    sim.lua("FIRE = ...", sim._wow.fire)
    sim.lua(GAME)
    yield sim
    sim.close()


def text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def open_ah(sim):
    sim.lua("AH_SHOWN = true")
    sim._wow.fire(b"AUCTION_HOUSE_SHOW")


CHECK = """
  RESULT = nil
  ForeverAI.Market.Check({ items = { %s } }, function(r) RESULT = r end)
"""
MEAT = '{ id = 3173, name = "Bear Meat" }, { id = 731, name = "Goretusk Snout" }'
SHOW = """
  local r = RESULT
  if not r then return "no result" end
  if r.error and not (r.items and #r.items > 0) then return "error: " .. r.error end
  local out = {}
  for _, e in ipairs(r.items) do
    local lv = {}
    for _, l in ipairs(e.levels or {}) do lv[#lv + 1] = l.unit .. "x" .. l.auctions .. "/" .. l.units .. (l.mine and ("+" .. l.mine .. " mine") or "") end
    out[#out + 1] = table.concat({ tostring(e.name), tostring(e.commodity), tostring(e.auctions), tostring(e.sellers), tostring(e.own),
      tostring(e.ownLow), tostring(e.bidOnly), table.concat(lv, " "), tostring(e.vendor), tostring(e.have), tostring(e.more),
      tostring(e.failed) }, "|")
  end
  return table.concat(out, "\\n") .. "\\n" .. table.concat(r.durations or {}, ",") .. (r.error and ("\\nerror: " .. r.error) or "")
"""


def test_the_addon_file_loads_whole(game):
    """What broke in the game: a file that stopped loading half way left the AI's request with "attempt to call a nil
    value". Every function the app asks for is there, and so are the cards."""
    for name in ("Check", "Owned", "Post", "Cancel", "Durations", "RunTime", "Available"):
        assert text(game.lua(f"return type(ForeverAI.Market.{name})")) == "function", name
    assert text(game.lua("return type(ForeverAI.Auction.History)")) == "function"
    assert text(game.lua("return type(ForeverAI.UI.SellCard)")) == "function"
    assert game.errors == []
    # the simulator refuses the events this client does not have, as the game does
    with pytest.raises(Exception, match="unknown event"):
        game.lua('CreateFrame("Frame"):RegisterEvent("AUCTION_ITEM_LIST_UPDATE")')


def test_the_requests_of_the_app_reach_the_auction_module(game):
    """The whole way of a request, as the app sends it: no error, an answer for each."""
    game.lua("""
      SENT = {}
      ForeverAI.Transport.Send = function(msg, done) SENT[#SENT + 1] = msg if done then done(true) end end
    """)
    open_ah(game)
    game.lua("""ForeverAI.HandleRequests({ { id = "r1", op = "auction_check", items = { { id = 3173, name = "Bear Meat" } } },
                                           { id = "r2", op = "auction_owned" } })""")
    game.run(4)
    got = text(game.lua("""
      local out = {}
      for _, sent in ipairs(SENT) do
        for _, m in ipairs(sent.results or { sent }) do  -- (results that were through together come as one message)
          out[#out + 1] = m.id .. ":" .. tostring(m.result.error) .. ":" .. tostring(m.result.items and #m.result.items) .. ":" .. tostring(m.result.auctions and #m.result.auctions)
        end
      end
      table.sort(out)
      return table.concat(out, " ")
    """))
    assert got == "r1:nil:1:nil r2:nil:nil:0"


def test_the_addon_looks_the_meat_up_on_the_auction_house(game):
    assert text(game.lua(CHECK % MEAT + "return RESULT and RESULT.error")) == "the auction house window is not open"
    open_ah(game)
    game.lua(CHECK % (MEAT + ', { id = 2512, name = "Bent Staff" }'))
    game.run(4)
    lines = text(game.lua(SHOW)).split("\n")
    # three prices of other sellers (the player's own ten at 30c are counted apart), six sellers with the one the
    # list does not name; the pieces at each price
    assert lines[0] == "Bear Meat|true|3|6|1|30|0|19x1/30 20x1/44 25x1/14 30x0/0+1 mine|15|13|nil|nil"
    assert text(game.lua("return table.concat(RESULT.items[1].deposits, ',')")) == "26,26,26"  # the game's own, per length
    assert lines[1] == "Goretusk Snout|true|0|0|0|nil|0||27|4|nil|nil"  # nobody sells it
    # an item that does not stack: its auctions one by one, the one with a bid only apart
    assert lines[2] == "Bent Staff|nil|2|2|0|nil|1|120x1/1 150x1/1|9|1|nil|nil"
    assert lines[3] == "2,8,24"
    # what stacks is searched as the game's shops do, an item as the auction add-on does when selling
    assert text(game.lua("return table.concat(SEARCHES, ' ')")) == "search:3173 search:731 sell:2512"
    assert game.errors == []


def test_a_search_answered_with_nothing_is_asked_again(game):
    """The game sometimes answers a search with no result although there is one: the auction add-on asks again."""
    open_ah(game)
    game.lua("EMPTY_FIRST = true")
    game.lua(CHECK % '{ id = 3173, name = "Bear Meat" }')
    game.run(3)
    assert text(game.lua(SHOW)).split("\n")[0].startswith("Bear Meat|true|3|6|")
    assert game.lua("return #SEARCHES") == 2


def test_a_search_the_game_never_answers_does_not_hang(game):
    open_ah(game)
    game.lua("SILENT = true")
    game.lua(CHECK % MEAT)
    game.run(30)
    lines = text(game.lua(SHOW)).split("\n")
    assert lines[0].endswith("|the auction house did not answer the search") and lines[1].endswith("|the auction house did not answer the search")
    assert game.lua("return #SEARCHES") == 6  # three tries each, then the next item
    # a second check can run afterwards
    game.lua("SILENT = nil")
    game.lua(CHECK % '{ id = 3173, name = "Bear Meat" }')
    game.run(2)
    assert text(game.lua(SHOW)).split("\n")[0].startswith("Bear Meat|true|3|6|")


def test_a_busy_auction_house_is_waited_for_and_then_given_up(game):
    open_ah(game)
    game.lua("READY = false")
    game.lua(CHECK % '{ id = 3173, name = "Bear Meat" }')
    game.run(3)
    assert game.lua("return RESULT == nil and #SEARCHES == 0") is True  # waiting, nothing sent
    game.lua("READY = true")
    game.run(2)
    assert text(game.lua(SHOW)).split("\n")[0].startswith("Bear Meat|true|3|6|")
    game.lua("READY = false")
    game.lua(CHECK % '{ id = 3173, name = "Bear Meat" }')
    game.run(12)
    assert text(game.lua(SHOW)) == "error: the auction house is busy (another add-on is scanning?); try again in a moment"


def test_what_cannot_be_auctioned_is_told_item_by_item(game):
    open_ah(game)
    game.lua(CHECK % '{ id = 2806, name = "Package" }, { id = 4444, name = "Gone" }, { id = 3173, name = "Bear Meat" }')
    game.run(3)
    lines = text(game.lua(SHOW)).split("\n")
    assert lines[0].endswith("|the game does not take this item on the auction house (bound, or not for sale)")
    assert lines[1].endswith("|not in the bags any more")
    assert lines[2].startswith("Bear Meat|true|3|6|")  # the rest is still looked up
    assert text(game.lua("return table.concat(SEARCHES, ' ')")) == "search:3173"  # no search for what cannot go


def test_the_window_closed_in_the_middle_of_a_check(game):
    open_ah(game)
    game.lua("SILENT = true")
    game.lua(CHECK % MEAT)
    game.run(1)
    game.lua("AH_SHOWN = false")
    game._wow.fire(b"AUCTION_HOUSE_CLOSED")
    game.run(1)
    assert text(game.lua("return RESULT.error")) == "the auction house window was closed"


def test_a_client_without_these_functions_says_so(game):
    open_ah(game)
    game.lua("C_AuctionHouse = {}")
    assert text(game.lua(CHECK % MEAT + "return RESULT.error")) == "this game client has no auction house functions an add-on can use"
    assert text(game.lua("local ok, why = ForeverAI.Market.Post({ id = 3173, count = 1, unit = 5 }) return tostring(ok) .. '|' .. why")) == (
        "false|This game client has no way for an add-on to post an auction.")
    game.lua("ForeverAI.Market.Owned(function(r) RESULT = r end)")
    assert text(game.lua("return RESULT.error")) == "this game client has no list of auctions an add-on can read"
    assert game.errors == []


def test_more_offers_than_one_page_are_marked(game):
    open_ah(game)
    game.lua("MORE = true")
    game.lua(CHECK % '{ id = 3173, name = "Bear Meat" }')
    game.run(2)
    assert text(game.lua(SHOW)).split("\n")[0].split("|")[10] == "true"


CARD = """
  FEEDBACK = {}
  ForeverAI.ActionFeedback = function(line) FEEDBACK[#FEEDBACK + 1] = line end
  ForeverAI.UI.UpdateAnswer({ final = true, msgId = 5, text = "Post these.", actions = {
    { id = 1, op = "auction_post", addon = "Auction house", hours = 8, description = "mięso na AH",
      total = "3s 98c in all when everything sells; deposit 34c", sum = 398, net = 378, cut = 5, deposit = 34, depositExact = true,
      lots = { { id = 3173, name = "Bear Meat", count = 13, unit = 18, hours = 8, basis = "sure price: 6 offers from 6 sellers, lowest 19c each" },
               { id = 731, name = "Goretusk Snout", count = 4, unit = 41, hours = 8 } },
      leftOut = { { name = "Clam Meat", why = "only one offer now" } } } } })
"""


# the card's fields as they stand: the length that is lit, then each row
FIELDS = """
  local e, out = ForeverAI.UI.sellCard, {}
  for _, b in ipairs(e.lengths) do if b.active then out[#out + 1] = b:GetText() end end
  for i, row in ipairs(e.rows) do
    out[#out + 1] = (row.name:GetText():gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""):gsub("|H.-|h%[(.-)%]|h", "%1"):gsub("^%d+%. ", "")) .. " ×" .. row.count.edit:GetText()
      .. " at " .. row.price.edit:GetText() .. " = " .. (row.total:GetText():gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", ""))
  end
  return table.concat(out, " | ")
"""


def test_the_card_posts_one_item_per_click_and_tells_the_ai_once(game):
    open_ah(game)
    game.lua(CARD)
    game.step(2)
    shown = "\n".join(game.texts())
    # an item a line with what it comes to, what the price stands on under it, then the money on two short lines;
    # the AI's own sentence about the card is in its answer, not on the card again
    assert "Post on the auction house · one click per item" in shown
    # how many, the price of one and the length are fields, filled in with what the AI proposed
    assert text(game.lua(FIELDS)) == "8 h | Bear Meat ×13 at 18c = 2s 34c | Goretusk Snout ×4 at 41c = 1s 64c"
    assert "sure price: 6 offers from 6 sellers, lowest 19c each" in shown and "• Clam Meat: only one offer now" in shown
    assert "Total 3s 98c  · 3s 78c for you after the 5% cut" in shown and "Deposit 34c, returned when it sells" in shown
    assert "mięso na AH" not in shown and "nothing happens until you click" not in shown
    assert text(game.lua("return #POSTED .. '|' .. #FEEDBACK")) == "0|0"  # nothing until the click
    game.click("Post 1/2")
    # all thirteen pieces in one auction (from both stacks), 18c a piece, the game's number for 8 hours is 2
    assert text(game.lua("return POSTED[1]")) == "commodity,3173,2,13,18"
    game.run(1)
    assert game.lua("return #FEEDBACK") == 0
    game.click("Post 2/2")
    assert text(game.lua("return POSTED[2]")) == "commodity,731,2,4,41"
    game.run(1)
    assert text(game.lua("return FEEDBACK[1]")) == (
        "Accepted: post on the auction house — Bear Meat ×13 at 18c each: posted, deposit 26c; "
        "Goretusk Snout ×4 at 41c each: posted, deposit 8c.")
    assert game.lua("return BAG[1].n + BAG[2].n + BAG[3].n") == 0 and game.errors == []
    # what the card says at the end: short, an item each
    assert "Posted Bear Meat ×13 (deposit 26c)  ·  Posted Goretusk Snout ×4 (deposit 8c)" in "\n".join(game.texts())


def test_an_item_that_does_not_stack_is_posted_with_a_buyout(game):
    open_ah(game)
    ok = text(game.lua("""
      local ok, why = ForeverAI.Market.Post({ id = 2512, name = "Bent Staff", count = 1, unit = 119, hours = 24 }, function(p, w, paid, why) DONE = p .. "/" .. w .. "/" .. paid .. "/" .. tostring(why) end)
      return tostring(ok) .. "|" .. tostring(why)
    """))
    game.run(1)
    assert ok == "true|nil" and text(game.lua("return POSTED[1] .. ' ' .. DONE")) == "item,2512,3,1,nil,119 1/1/30/nil"


def test_a_post_the_game_refuses_is_reported_and_the_next_one_goes_on(game):
    open_ah(game)
    game.lua(CARD)
    game.lua("REFUSE = true")
    game.click("Post 1/2")
    game.run(6)  # the bags are watched for a few seconds: nothing left them
    game.lua("REFUSE = nil")
    game.click("Post 2/2")
    game.run(1)
    line = text(game.lua("return FEEDBACK[1]"))
    assert "Bear Meat ×13 at 18c each: 0 of 13 posted (the game did not take it)" in line
    assert line.endswith("Goretusk Snout ×4 at 41c each: posted, deposit 8c.")


def test_posts_that_cannot_start_say_why(game):
    post = """
      local ok, why = ForeverAI.Market.Post({ id = %s, name = "Bear Meat", count = %s, unit = %s, hours = 8 })
      return tostring(ok) .. "|" .. tostring(why)
    """
    assert text(game.lua(post % (3173, 13, 18))) == "false|The auction house window is closed."
    open_ah(game)
    assert text(game.lua(post % (3173, 20, 18))) == "false|Only 13 of Bear Meat left in the bags."
    assert text(game.lua(post % (3173, 0, 18))) == "false|The lot is not complete."
    assert text(game.lua(post % (2806, 1, 18))) == "false|The game does not take this item on the auction house (bound, or not for sale)."
    game.lua("MONEY = 5")
    assert text(game.lua(post % (3173, 13, 18))) == "false|Not enough money for the deposit (26c)."
    game.lua("MONEY = 15444 THROW = true")
    assert text(game.lua(post % (3173, 13, 18))).startswith("false|The game refused the auction: ")
    game.lua("THROW = nil BAG[1].locked = true BAG[2].locked = true")
    assert text(game.lua(post % (3173, 13, 18))) == "false|The item is locked by the game right now; try again."
    assert game.lua("return #POSTED") == 0 and game.errors == []


def test_a_post_the_game_wants_confirmed_goes_to_the_games_own_question(game):
    open_ah(game)
    game.lua("CONFIRM = true")
    game.lua('ForeverAI.Market.Post({ id = 3173, name = "Bear Meat", count = 13, unit = 18, hours = 8 })')
    assert text(game.lua("return CONFIRMED")) == "13x18"


def test_reject_and_stopping_half_way(game):
    open_ah(game)
    game.lua(CARD)
    game.click("Reject")
    assert text(game.lua("return FEEDBACK[1]")) == "Rejected: post on the auction house — nothing was done."
    game.lua(CARD)
    game.click("Post 1/2")
    game.run(1)
    game.click("Reject")  # the second one is not wanted after all
    assert text(game.lua("return FEEDBACK[1]")) == "Accepted: post on the auction house — Bear Meat ×13 at 18c each: posted, deposit 26c."
    assert game.lua("return #POSTED") == 1
    # the auction house closed before the click: nothing is posted, and it says why
    game.lua(GAME)
    game.lua(CARD)
    game.lua("AH_SHOWN = false")
    game._wow.fire(b"AUCTION_HOUSE_CLOSED")
    game.click("Post 1/2")
    game.click("Reject")
    assert game.lua("return #POSTED") == 0
    assert "failed (The auction house window is closed.)" in text(game.lua("return FEEDBACK[1]"))


OWN = """
  OWNED = { { auctionID = 501, itemKey = { itemID = 3173 }, quantity = 10, buyoutAmount = 18, status = 0, timeLeftSeconds = 28000 },
            { auctionID = 502, itemKey = { itemID = 3173 }, quantity = 3, buyoutAmount = 18, status = 0, timeLeftSeconds = 2000 },
            { auctionID = 503, itemKey = { itemID = 731 }, quantity = 4, buyoutAmount = 41, bidAmount = 150, bidder = "Zed", status = 0, timeLeftSeconds = 7200 },
            { auctionID = 504, itemKey = { itemID = 2512 }, quantity = 1, buyoutAmount = 120, status = 1 } }
"""
CANCEL_CARD = """
  FEEDBACK = {}
  ForeverAI.ActionFeedback = function(line) FEEDBACK[#FEEDBACK + 1] = line end
  ForeverAI.UI.UpdateAnswer({ final = true, msgId = 6, text = "Take it down.", actions = {
    { id = 2, op = "auction_cancel", addon = "Auction house", description = "za tanio",
      auctions = { { auctionID = %s, id = 3173, name = "Bear Meat", count = 3, unit = 18, left = "33 min" } } } } })
"""


def test_own_auctions_are_read(game):
    open_ah(game)
    game.lua(OWN)
    game.lua("ForeverAI.Market.Owned(function(r) RESULT = r end)")
    game.run(1)
    assert text(game.lua("""
      local out = {}
      for _, a in ipairs(RESULT.auctions) do
        out[#out + 1] = table.concat({ a.n, a.auctionID, a.name, a.count, a.unit, a.buyout, tostring(a.bid), tostring(a.sold), tostring(a.left) }, ",")
      end
      return table.concat(out, "|")
    """)) == ("1,501,Bear Meat,10,18,180,nil,nil,8 h|2,502,Bear Meat,3,18,54,nil,nil,33 min|"
              "3,503,Goretusk Snout,4,41,164,150,nil,2 h|4,504,Bent Staff,1,120,120,nil,true,nil")


def test_cancelling_the_one_auction_that_was_named(game):
    open_ah(game)
    game.lua(OWN)
    game.lua(CANCEL_CARD % 502)
    game.step(2)
    shown = "\n".join(game.texts())
    assert "Cancel auctions · the deposit is not returned" in shown and "[Bear Meat] ×3  18c each, 33 min left" in shown  # (the item is a link)
    game.click("Cancel")  # one auction: no counting
    game.run(1)
    # the one of three pieces, by the game's number of the auction (not the ten at the same price)
    assert text(game.lua("return CANCELLED[1] .. '|' .. #OWNED")) == "502|3"
    assert text(game.lua("return FEEDBACK[1]")) == "Accepted: cancel auctions — Bear Meat ×3: cancelled."
    assert game.errors == []


def test_a_window_closed_right_after_the_click_is_not_reported_as_not_cancelled(game):
    """From the game: the auction was asked to be cancelled, the player closed the window, and the card said "still
    listed" and "Rejected". Whether the game cancelled it is not known then, and the click was no refusal."""
    open_ah(game)
    game.lua(OWN)
    game.lua("QUIET = true")  # the game says nothing in time
    game.lua(CANCEL_CARD % 502)
    game.click("Cancel")
    game.lua("AH_SHOWN = false")
    game._wow.fire(b"AUCTION_HOUSE_CLOSED")
    game.run(2)
    assert text(game.lua("return FEEDBACK[1]")) == (
        "Accepted: cancel auctions — Bear Meat ×3: asked to cancel, but the game did not say whether it did (the window "
        "was closed too soon): look at my_auctions.")


def test_cancelling_in_the_cases_that_go_wrong(game):
    cancel = """
      DONE = nil
      local ok, why = ForeverAI.Market.Cancel({ auctionID = %s, id = 3173, name = "Bear Meat", count = 3, unit = 18 }, function(gone, cost, unsure) DONE = tostring(gone) .. "/" .. tostring(cost) .. (unsure and "/unsure" or "") end)
      return tostring(ok) .. "|" .. tostring(type(why) == "string" and why or "")
    """
    game.lua(OWN)
    assert text(game.lua(cancel % 502)) == "false|The auction house window is closed."
    open_ah(game)
    # sold or gone meanwhile
    assert text(game.lua(cancel % 999)) == "false|This auction is no longer among yours (sold, expired or cancelled)."
    assert text(game.lua(cancel % 504)) == "false|This auction is no longer among yours (sold, expired or cancelled)."
    # the game does not allow it now
    game.lua("LOCKED = true")
    assert text(game.lua(cancel % 502)) == "false|The game does not let this auction be cancelled right now."
    # an auction with a bid costs money to cancel
    game.lua("LOCKED = nil COST = 99999")
    assert text(game.lua(cancel % 503)).startswith("false|Cancelling it costs 9g 99s 99c (it has a bid)")
    game.lua("COST = 7")
    assert text(game.lua(cancel % 503)) == "true|"
    game.run(1)
    assert text(game.lua("return DONE")) == "true/7"
    # the game says nothing after the cancel: the list is read again, and the auction is not on it
    game.lua("COST = nil QUIET = true")
    assert text(game.lua(cancel % 502)) == "true|"
    game.run(5)
    assert text(game.lua("return DONE")) == "true/0"
    # the game did not cancel it: still listed, and said so
    game.lua("QUIET = nil STUCK = true")
    assert text(game.lua(cancel % 501)) == "true|"
    game.run(5)
    assert text(game.lua("return DONE")) == "false/0"
    # the game's word for a cancelled auction without its number is still its word
    game.lua("""STUCK = nil
      C_AuctionHouse.CancelAuction = function(id)
        for i, a in ipairs(OWNED) do if a.auctionID == id then table.remove(OWNED, i) break end end
        C_Timer.After(0.3, function() FIRE("AUCTION_CANCELED") end)
      end""")
    assert text(game.lua(cancel % 501)) == "true|"
    game.run(1)
    assert text(game.lua("return DONE")) == "true/0"
    assert game.errors == []


# ---------------------------------------------------------------- the fields of the card
# The player's words: "zamiast na sztywno wyświetlać za ile / ilość / czas aukcji, robić to w edytowalnych inputach,
# które są predefiniowane: model je wstępnie uzupełnia, ale jeżeli chcemy, możemy to zedytować (po prostu klikamy i
# zmieniamy cyfrę)".
def test_what_stands_in_the_fields_is_what_is_posted(game):
    open_ah(game)
    game.lua(CARD)
    game.step(2)
    game.lua("""
      local e = ForeverAI.UI.sellCard
      e.rows[1].count.edit:SetText("10")
      e.rows[1].price.edit:SetText("25c")
    """)
    game.click("24 h")
    game.step(2)
    shown = "\n".join(game.texts())
    assert text(game.lua(FIELDS)) == "24 h | Bear Meat ×10 at 25c = 2s 50c | Goretusk Snout ×4 at 41c = 1s 64c"
    # the money follows the fields; the deposit is the game's for what stands there now (2c a piece in this game)
    assert "Total 4s 14c  · 3s 93c for you after the 5% cut" in shown and "Deposit 28c, returned when it sells" in shown
    game.click("Post 1/2")
    assert text(game.lua("return POSTED[1]")) == "commodity,3173,3,10,25"  # ten of them, 25c each, the game's 3 = 24 h
    game.run(1)
    # a lot that is through is closed; the next one can still be changed
    assert text(game.lua("local e = ForeverAI.UI.sellCard return tostring(e.rows[1].price.edit:IsEnabled()) .. tostring(e.rows[2].price.edit:IsEnabled())")) == "falsetrue"
    game.lua('ForeverAI.UI.sellCard.rows[2].price.edit:SetText("1s 5c")')
    game.click("Post 2/2")
    assert text(game.lua("return POSTED[2]")) == "commodity,731,3,4,105"
    game.run(1)
    # the AI hears what was posted, and that it is not what it proposed
    assert text(game.lua("return FEEDBACK[1]")) == (
        "Accepted: post on the auction house — Bear Meat ×10 at 25c each (changed by the player on the card; proposed "
        "×13 at 18c each): posted, deposit 20c; Goretusk Snout ×4 at 1s 5c each (changed by the player on the card; "
        "proposed ×4 at 41c each): posted, deposit 8c; posted for 24 h, the player's choice on the card.")
    assert game.lua("return BAG[1].n + BAG[2].n") == 3 and game.errors == []  # three pieces of meat stay in the bags


def test_a_field_that_cannot_be_read_posts_nothing(game):
    open_ah(game)
    game.lua(CARD)
    game.step(2)
    usable = "return tostring(ForeverAI.UI.sellCard.buttons[1]:IsEnabled())"
    price = "ForeverAI.UI.sellCard.rows[1].price.edit"
    count = "ForeverAI.UI.sellCard.rows[1].count.edit"
    # a bare number could be copper, silver or gold: it is not taken for any of them
    game.lua(price + ':SetText("25")')
    game.step(2)
    assert text(game.lua(usable)) == "false" and "The price of one, like 1g 20s 5c." in "\n".join(game.texts())
    game.lua("ForeverAI.UI.sellCard.buttons[1]:Click()")  # (the game does not click a button that is off; neither does this)
    assert game.lua("return #POSTED") == 0
    game.lua(price + ':SetText("1s 2c")')
    game.step(2)
    assert text(game.lua(usable)) == "true" and "The price of one" not in "\n".join(game.texts())
    # more than the bags hold, nothing, half a piece
    for wrong, said in (("14", "You have 13 of Bear Meat."), ("0", "How many: a whole number, 1 or more."),
                        ("2.5", "How many: a whole number, 1 or more."), ("", "How many: a whole number, 1 or more.")):
        game.lua(count + ':SetText("%s")' % wrong)
        game.step(2)
        assert text(game.lua(usable)) == "false" and said in "\n".join(game.texts()), wrong
    # leaving the field puts back the last value that could be read
    game.lua(count + ":SetFocus() " + count + ":ClearFocus()")
    game.step(2)
    assert text(game.lua("return " + count + ":GetText()")) == "13" and text(game.lua(usable)) == "true"
    game.lua(count + ':SetText("5")')
    game.click("Post 1/2")
    assert text(game.lua("return POSTED[1]")) == "commodity,3173,2,5,102" and game.errors == []


def test_a_price_is_read_the_way_it_is_written(game):
    read = lambda s: game.lua('return ForeverAI.UI.ParseCoins("%s")' % s)
    assert [read(s) for s in ("18c", "1s 20c", "1g 20s 5c", "2G", "1s20c", " 3 s ")] == [18, 120, 12005, 20000, 120, 300]
    assert [read(s) for s in ("25", "", "0c", "abc", "1c 2s", "1.5s", "-3c")] == [None] * 7


def test_a_card_without_changes_says_nothing_about_changes(game):
    open_ah(game)
    game.lua(CARD)
    game.click("8 h")  # the length it had
    game.click("Post 1/2")
    game.run(1)
    game.click("Post 2/2")
    game.run(1)
    assert text(game.lua("return FEEDBACK[1]")) == (
        "Accepted: post on the auction house — Bear Meat ×13 at 18c each: posted, deposit 26c; "
        "Goretusk Snout ×4 at 41c each: posted, deposit 8c.")


def test_the_fields_of_the_card_follow_each_other(game):
    """The player: "there is no way to change the stacking on the AH card; that should be changeable too, and the rest
    computed dynamically after a change"; and of a total that could be typed: "the final valuation should not be
    editable". This auction house sells by the piece: the card says how many the bags hold, takes all of them at a
    click, and what a lot comes to is shown beside the fields and follows them."""
    open_ah(game)
    game.lua(CARD)
    game.step(2)
    row = "ForeverAI.UI.sellCard.rows[1]"
    have = "return (" + row + ".have:GetText():gsub('|c%x%x%x%x%x%x%x%x', ''):gsub('|r', ''))"
    assert text(game.lua(have)) == "of 13"
    assert text(game.lua(FIELDS)) == "8 h | Bear Meat ×13 at 18c = 2s 34c | Goretusk Snout ×4 at 41c = 1s 64c"
    assert game.lua("return " + row + ".total.edit == nil")  # shown, not a field
    # another count, another price of one: the total follows
    game.lua(row + '.count.edit:SetText("5")')
    game.step(2)
    assert text(game.lua(FIELDS)).startswith("8 h | Bear Meat ×5 at 18c = 90c |")
    game.lua(row + '.price.edit:SetText("40c")')
    game.step(2)
    assert text(game.lua(FIELDS)).startswith("8 h | Bear Meat ×5 at 40c = 2s 0c |")
    assert "Total 3s 64c" in "\n".join(game.texts())  # 5 x 40c + 4 x 41c
    # all of them at a click
    game.lua(row + ".all:Click()")
    game.step(2)
    assert text(game.lua(FIELDS)).startswith("8 h | Bear Meat ×13 at 40c = 5s 20c |")
    game.click("Post 1/2")
    assert text(game.lua("return POSTED[1]")) == "commodity,3173,2,13,40" and game.errors == []


def test_the_card_shows_the_prices_of_the_market_as_figures(game):
    """The player: "a better display of the average price and the lowest would be useful": the card had one grey
    sentence. The app sends the figures, the card shows them: lowest, average, vendor, offers and sellers."""
    m = bear_market(history={"days": [{"ago": 1, "low": 17}, {"ago": 3, "low": 21}, {"ago": 30, "low": 5}]})
    unit, basis, _ = market.price_of(m, 15, 8)
    facts = market.price_facts(m, 15, basis)
    # the average is by the piece: 30 at 19c, 44 at 20c, 14 at 25c
    assert facts == {"offers": 6, "sellers": 6, "low": 19, "avg": 20, "seenLow": 17, "seenHigh": 21, "seenDays": 2, "vendor": 15}
    weak = bear_market(auctions=2, sellers=2, levels=levels((42, 1, 8), (45, 1, 4)), vendor=27)
    assert market.price_facts(weak, 27, market.price_of(weak, 27, 8)[1])["notes"] == ["weakly confirmed"]
    aside = bear_market(auctions=4, levels=levels((5, 1, 10), (19, 1, 30), (20, 2, 34)))
    assert market.price_facts(aside, 3, market.price_of(aside, 3, 8)[1])["notes"] == [
        "1 at 5c left aside (under half of the next price)"]
    open_ah(game)
    game.lua("""ForeverAI.UI.UpdateAnswer({ final = true, msgId = 9, text = "Post it.", actions = {
      { id = 1, op = "auction_post", addon = "Auction house", hours = 8, cut = 5, deposit = 20, depositExact = true,
        lots = { { id = 3173, name = "Bear Meat", count = 2, unit = 39, hours = 8, basis = "sure price: 28 offers",
                   market = { offers = 28, sellers = 17, low = 40, avg = 43, vendor = 15, seenLow = 34, seenHigh = 41,
                              seenDays = 5, notes = { "weakly confirmed" } } } } } } })""")
    game.step(2)
    shown = "\n".join(game.texts())
    assert "Lowest 40c    Average 43c    Vendor 15c    28 offers · 17 sellers" in shown
    assert "your scans, last 2 weeks: 34c–41c on 5 days · weakly confirmed" in shown and "sure price: 28 offers" not in shown
    assert game.errors == []


def test_the_items_on_the_cards_show_their_tooltip(game):
    """The player: "the items to sell and the list below should be interactive: when I hover one, the information of
    what it gives shows, as when I hover it in my equipment". The names on the cards are item links, and hovering
    one shows the game's tooltip of that item; the character's own link when it has the item."""
    game.lua("""
      ForeverAI.Collect.knownLinks["Bear Meat"] = "|cffffffff|Hitem:3173::::::::18:::::|h[Bear Meat]|h|r"
      TIPS = {}
      local plain = GameTooltip.SetHyperlink
      GameTooltip.SetHyperlink = function(self, link) TIPS[#TIPS + 1] = link return plain(self, link) end
    """)
    open_ah(game)
    game.lua(CARD)
    game.lua("""ForeverAI.UI.AuctionCard({ { id = 6511, name = "Gloves of the Fang", price = "1g 10s" } })""")
    game.lua("""ForeverAI.UI.ActionCard({ id = 5, op = "vendor_sell", addon = "Vendor", total = "1s 80c",
      items = { { id = 5503, name = "[Cracked Shell]", count = 12, price = "1s 80c", why = "grey" } } })""")
    game.step(2)
    hover = """
      local name = ...
      local f = EnumerateFrames()
      while f do
        for _, r in ipairs({ f:GetRegions() }) do
          local t = r.GetText and r:GetText()
          local link = t and t:match("(|Hitem:[^|]+)|h%[" .. name .. "%]|h")
          if link and f:GetHyperlinksEnabled() then
            f:GetScript("OnHyperlinkEnter")(f, link:sub(3))
            local shown = GameTooltip:IsShown()
            f:GetScript("OnHyperlinkLeave")(f)
            return shown and TIPS[#TIPS] or "not shown"
          end
        end
        f = EnumerateFrames(f)
      end
      return "no link"
    """
    assert text(game.lua(hover, "Bear Meat")) == "item:3173::::::::18:::::"  # the one in the bags
    assert text(game.lua(hover, "Goretusk Snout")) == "item:731"
    assert text(game.lua(hover, "Gloves of the Fang")) == "item:6511"
    assert text(game.lua(hover, "Cracked Shell")) == "item:5503"
    assert not game.lua("return GameTooltip:IsShown()")
    # what the card says and posts is the same as before
    assert text(game.lua(FIELDS)) == "8 h | Bear Meat ×13 at 18c = 2s 34c | Goretusk Snout ×4 at 41c = 1s 64c"
    assert game.errors == []
