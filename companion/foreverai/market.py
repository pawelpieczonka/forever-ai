"""Selling: what goes on the auction house, in which stacks and for how much, and what a vendor may have.

The model picks the items; the prices and the guards are rules here, worked out from what the game found (a search of
the auction house for each item, the price data of the player's auction add-on). A price is proposed only when the
market shows one: several auctions from several sellers. Nothing is posted or sold before the player's click."""

from __future__ import annotations

import math

AH_CUT = 0.05                       # what the auction house keeps of a sale
SURE_AUCTIONS, SURE_SELLERS = 5, 3  # from here on a lowest price is the market's, not one seller's
DEFAULT_HOURS = 12                  # what the player asked for; the game has three lengths and takes the nearest
MISTAKE = 0.5                       # a lowest offer under this part of the next price is taken for a slip
DEPOSIT_DAY = 0.15                  # deposit: about this part of the vendor value for 24 hours (returned on a sale)
VENDOR_NEAR = 1.25                  # the auction house "pays about the same" up to this much over the vendor
MAX_ITEMS = 12  # looked up in one request (a search of the auction house each)
QUALITY = {0: "poor", 1: "common", 2: "uncommon", 3: "rare", 4: "epic", 5: "legendary"}
KEPT_QUALITIES = ("uncommon", "rare", "epic", "legendary", "artifact")


def coins(copper) -> str:
    try:
        c = int(round(float(copper)))
    except (TypeError, ValueError):
        return "?"
    g, s, c = c // 10000, c // 100 % 100, c % 100
    return " ".join(([f"{g}g"] if g else []) + ([f"{s}s"] if s or g else []) + [f"{c}c"])


def _history(market: dict) -> tuple[float | None, str]:
    """The middle of the lowest prices the scans of the last days saw, and words for it."""
    days = [d for d in ((market.get("history") or {}).get("days") or []) if isinstance(d, dict) and d.get("low")]
    days = [d for d in days if (d.get("ago") if d.get("ago") is not None else 99) <= 14]
    if not days:
        return None, ""
    lows = sorted(float(d["low"]) for d in days)
    mid = lows[len(lows) // 2]
    span = coins(lows[0]) if lows[0] == lows[-1] else f"{coins(lows[0])}–{coins(lows[-1])}"
    return mid, f"your scans saw {span} each on {len(days)} day{'s' if len(days) != 1 else ''} of the last two weeks"


def _levels(market: dict) -> list[dict]:
    return sorted((lv for lv in market.get("levels") or [] if isinstance(lv, dict) and (lv.get("auctions") or 0) > 0
                   and (lv.get("unit") or 0) > 0), key=lambda lv: lv["unit"])


def price_facts(market: dict, vendor: float, basis: str = "") -> dict:
    """The prices a lot's price stands on, as figures for the card: the lowest and the average of what is offered now
    (by the piece, over the price levels the game listed: its cheapest), how many offers from how many sellers, what
    the player's scans saw in the last two weeks, the vendor's price, and what else was said of the price."""
    levels = _levels(market)
    sellers = int(market.get("sellers") or 0)
    count = max(int(market.get("auctions") or 0), sellers)
    out: dict = {"offers": count, "sellers": sellers}
    if market.get("more"):
        out["more"] = True
    if levels:
        out["low"] = int(round(levels[0]["unit"]))
        weights = [float(lv.get("units") or 0) or float(lv.get("auctions") or 0) for lv in levels]
        if sum(weights) > 0:
            out["avg"] = int(round(sum(lv["unit"] * w for lv, w in zip(levels, weights)) / sum(weights)))
    days = [float(d["low"]) for d in ((market.get("history") or {}).get("days") or [])
            if isinstance(d, dict) and d.get("low") and (d.get("ago") if d.get("ago") is not None else 99) <= 14]
    if days:
        out["seenLow"], out["seenHigh"], out["seenDays"] = int(round(min(days))), int(round(max(days))), len(days)
    if vendor:
        out["vendor"] = int(round(vendor))
    # what the basis says beyond the figures ("weakly confirmed", an offer left aside, the player's own price)
    head, _, rest = basis.partition(": ")
    notes = [head] if head and head != "sure price" and not head.startswith("price set by the player") else []
    if basis.startswith("price set by the player"):
        notes.append("price set by you")
    notes += [part.strip() for part in rest.split("; ")[1:] if part.strip() and not part.strip().startswith("your scans saw")]
    if notes:
        out["notes"] = notes
    return out


def price_of(market: dict, vendor: float, hours: int, asked: float | None = None) -> tuple[float | None, str, str | None]:
    """The price of one piece: (copper, what it stands on, None) or (None, "", why no price is proposed)."""
    levels = _levels(market)
    # (what sells by the piece is listed by price, several sellers on one line: the offers are at least the sellers)
    sellers = int(market.get("sellers") or 0)
    count = max(int(market.get("auctions") or 0), sellers)
    mid, seen = _history(market)
    now = (f"{count}{'+' if market.get('more') else ''} offer{'s' if count != 1 else ''} from {sellers} seller"
           f"{'s' if sellers != 1 else ''}" + (f", lowest {coins(levels[0]['unit'])} each" if levels else ""))
    if asked:
        return float(asked), "price set by the player" + (f"; now: {now}" if count else "; nobody else sells it now"), None
    if count <= 1:
        return None, "", ((f"only one offer now ({coins(levels[0]['unit'])} each)" if levels else "nobody sells it now")
                          + ": no sure price" + (f"; {seen}" if seen else "; no price history either")
                          + ". The player can name a price")
    low, note = levels[0], ""
    if (len(levels) > 1 and low["auctions"] <= 2 and count - low["auctions"] >= 2
            and low["unit"] < MISTAKE * levels[1]["unit"]):
        note = f"; {low['auctions']} at {coins(low['unit'])} left aside (under half of the next price)"
        low = levels[1]
    unit = max(1.0, math.ceil(low["unit"]) - 1.0)
    own = market.get("ownLow")
    if own and own <= low["unit"]:
        unit, note = float(own), note + "; matched to your own auction, the lowest there is"
    sure = count >= SURE_AUCTIONS and sellers >= SURE_SELLERS
    basis = ("sure price: " if sure else "weakly confirmed: ") + now + note
    if mid and not (0.5 * mid <= unit <= 2 * mid):
        basis += f"; {seen}"
    # (the deposit comes back with a sale, so it is not part of what the sale brings)
    if vendor and unit * (1 - AH_CUT) <= vendor * 1.1:
        return None, "", (f"the auction house pays about what a vendor does ({coins(unit)} each less its cut, vendor "
                          f"{coins(vendor)}): sell it to a vendor")
    return unit, basis, None


def deposit(vendor: float, units: int, hours: int) -> float:
    """About what the game takes as a deposit (it names the exact sum when the auction is posted)."""
    return (vendor or 0) * units * DEPOSIT_DAY * hours / 24.0


def nearest_hours(hours, lengths: list | None) -> int:
    lengths = [int(h) for h in lengths or [] if isinstance(h, (int, float))] or [2, 8, 24]
    try:
        want = float(hours) if hours not in (None, "") else DEFAULT_HOURS
    except (TypeError, ValueError):
        want = DEFAULT_HOURS
    return min(lengths, key=lambda h: (abs(h - want), h))


def _bag_index(snapshot: dict) -> tuple[dict, dict]:
    bags = [b for b in ((snapshot.get("bags") or {}).get("items") or []) if isinstance(b, dict) and b.get("id")]
    return ({int(b["id"]): b for b in bags}, {str(b.get("item", "")).strip("[]").lower(): b for b in bags})


def wanted_items(args: dict, snapshot: dict) -> tuple[list[dict], list[dict]]:
    """The model's picks against the bags: ([{bag item, asked price/stack/count}], [{name, why}] for what cannot go)."""
    by_id, by_name = _bag_index(snapshot)
    picked, left, seen = [], [], set()
    for want in (args.get("items") or [])[:MAX_ITEMS * 2]:
        spec = want if isinstance(want, dict) else {"item": want}
        key = spec.get("item")
        b = by_id.get(int(key)) if isinstance(key, int) or str(key).isdigit() else by_name.get(str(key or "").strip("[] ").lower())
        if not b:
            left.append({"name": str(key), "why": "not in the bags"})
            continue
        if b["id"] in seen:
            continue
        seen.add(b["id"])
        tip = str(b.get("tooltip") or "")
        if b.get("bound") or "Soulbound" in tip or "Quest Item" in tip:
            left.append({"name": str(b.get("item")).strip("[]"), "why": "bound to the character or a quest item: it cannot be auctioned"})
            continue

        def number(name):
            try:
                return float(spec[name]) if spec.get(name) not in (None, "", 0) else None
            except (TypeError, ValueError):
                return None
        picked.append({"bag": b, "price": number("price_copper"), "count": number("count")})
    for extra in picked[MAX_ITEMS:]:
        left.append({"name": str(extra["bag"].get("item")).strip("[]"),
                     "why": f"more than {MAX_ITEMS} items in one request: ask for it in the next one"})
    return picked[:MAX_ITEMS], left


def build_post(picked: list[dict], left: list[dict], res: dict, hours_asked, reason: str, action_id: int) -> tuple[dict | None, str]:
    """The game's price check -> the proposal (an action for the card) and the text for the model."""
    hours = nearest_hours(hours_asked, res.get("durations"))
    found = {int(m["id"]): m for m in res.get("items") or [] if isinstance(m, dict) and m.get("id")}
    lots, lines, total, fee, exact = [], [], 0.0, 0.0, True
    lengths = [int(h) for h in res.get("durations") or [] if isinstance(h, (int, float))]
    for p in picked:
        b = p["bag"]
        name = str(b.get("item")).strip("[]")
        m = found.get(int(b["id"]))
        if not m or m.get("failed"):
            left.append({"name": name, "why": (m or {}).get("failed") or "the game returned no prices for it"})
            continue
        vendor = float(m.get("vendor") or b.get("sellCopper") or 0)
        units = int(min(p["count"] or 10 ** 9, m.get("have") or b.get("count") or 1))
        unit, basis, why = price_of(m, vendor, hours, p["price"])
        if unit is None:
            left.append({"name": name, "why": why})
            continue
        # this auction house sells by the piece: one auction of all of them, at a price for one
        facts = price_facts(m, vendor, basis)
        lots.append({"id": int(b["id"]), "name": name, "count": units, "unit": unit, "hours": hours, "basis": basis,
                     "market": facts})
        lines.append(f"- [{name}] ×{units} at {coins(unit)} each ({coins(unit * units)} in all) — {basis}"
                     + (f"; the offers average {coins(facts['avg'])} each" if facts.get("avg") else ""))
        total += unit * units
        # the game's own figure for the deposit when it gave one (for all the pieces in the bags), else an estimate
        own = m.get("deposits")
        if isinstance(own, list) and hours in lengths and len(own) > lengths.index(hours) and (m.get("have") or 0) > 0:
            fee += float(own[lengths.index(hours)]) * units / float(m["have"])
        else:
            fee += deposit(vendor, units, hours)
            exact = False
    said = "" if hours_asked in (None, "") or float(hours_asked) == hours else (
        f" The game has no {hours_asked:g} h auctions (its lengths: "
        f"{', '.join(str(h) for h in res.get('durations') or [2, 8, 24])} h): {hours} h is the nearest.")
    skipped = ("\nNot posted:\n" + "\n".join(f"- [{s['name']}]: {s['why']}" for s in left)) if left else ""
    if not lots:
        return None, "Nothing to post." + skipped + said
    summary = (f"{coins(total)} in all when everything sells; deposit {'' if exact else 'about '}{coins(fee)} (returned "
               f"with a sale), the auction house keeps {int(AH_CUT * 100)}% of a sale")
    action = {"id": action_id, "op": "auction_post", "addon": "Auction house", "lots": lots, "hours": hours,
              "leftOut": left, "total": summary, "description": reason,
              "sum": round(total), "net": round(total * (1 - AH_CUT)), "cut": int(AH_CUT * 100),
              "deposit": round(fee), "depositExact": exact}
    text = (f"Proposed on a card, {hours} h auctions, one click of the player per item ({len(lots)} item"
            f"{'s' if len(lots) != 1 else ''}); nothing is posted until then:\n" + "\n".join(lines) + f"\n{summary}."
            + skipped + said + "\nTell the player what goes for how much and on what each price stands, and what was left "
            "out and why. The prices are the app's rules from the auctions the game listed just now: do not change them "
            "in your answer.")
    return action, text


def format_owned(res: dict | None) -> str:
    if not res:
        return "The game did not respond in time; the auctions could not be read."
    if res.get("error"):
        return f"Reading the auctions failed: {res['error']}."
    rows = [a for a in res.get("auctions") or [] if isinstance(a, dict)]
    if not rows:
        return "The character has no auctions up."
    lines = ["The character's auctions (number: item, price, state):"]
    for a in rows:
        state = ("SOLD, the money waits in the mailbox" if a.get("sold") else
                 (f"bid {coins(a['bid'])}" if a.get("bid") else "no bid") + (f", {a['left']} left" if a.get("left") else ""))
        each = a.get("unit") if a.get("unit") is not None else (a.get("buyout") or 0) / max(1, a.get("count") or 1)
        lines.append(f"{a.get('n')}. [{a.get('name')}] ×{a.get('count')} — {coins(each)} each"
                     + (f" ({coins(each * (a.get('count') or 1))} in all)" if (a.get("count") or 1) > 1 else "")
                     + f", {state}")
    lines.append("To take some down, call propose_auction_cancel with their numbers; the deposit of a cancelled auction is lost.")
    return "\n".join(lines)


def build_cancel(args: dict, res: dict | None, action_id: int) -> tuple[dict | None, str]:
    """The auctions to cancel, picked by number (from my_auctions) or by item name, against the list the game holds now."""
    if not res or res.get("error"):
        return None, format_owned(res)
    rows = [a for a in res.get("auctions") or [] if isinstance(a, dict) and not a.get("sold")]
    picked, problems = [], []
    for want in args.get("auctions") or []:
        if isinstance(want, (int, float)) or str(want).strip().isdigit():
            hit = [a for a in rows if a.get("n") == int(want)]
        else:
            low = str(want).strip("[] ").lower()
            hit = [a for a in rows if str(a.get("name") or "").lower() == low]
        if not hit:
            problems.append(f"{want}: no such auction of the character's now")
        picked += [a for a in hit if a not in picked]
    if not picked:
        return None, "Rejected: nothing to cancel. " + "; ".join(problems) + "\n" + format_owned(res)
    action = {"id": action_id, "op": "auction_cancel", "addon": "Auction house",
              "auctions": [{"auctionID": a.get("auctionID"), "id": a.get("id"), "name": a.get("name"),
                            "count": a.get("count"), "unit": a.get("unit"), "left": a.get("left")} for a in picked],
              "description": str(args.get("reason") or "").strip()[:300]}
    text = ("Cancelling proposed on a card, one click of the player per auction: "
            + "; ".join(f"[{a.get('name')}] ×{a.get('count')} ({coins(a.get('unit'))} each"
                        + (", HAS A BID" if a.get("bid") else "") + ")" for a in picked)
            + ". The deposit of a cancelled auction is not returned: say so." + (" Not found: " + "; ".join(problems) if problems else ""))
    return action, text


# ---------------------------------------------------------------------------------------------- vendor sales
def vendor_guard(item: dict, price: dict | None, scanned: bool, tool: str | None) -> str | None:
    """Why a bag item is NOT sold to a vendor as junk (None: it may go). ``price``: what the player's auction add-on
    last saw it for ({"copper", "available"}), None when it holds nothing; ``scanned``: its price data is filled
    (the auction house was scanned), so "no price" means nobody sells the item."""
    quality = str(item.get("quality") or "").lower()
    name = str(item.get("item") or "").strip("[]")
    if tool:
        return f"needed for {tool}"
    if quality in KEPT_QUALITIES:
        return f"{quality} quality: not junk (auction it, or name it yourself to sell it)"
    stats = str((item.get("facts") or {}).get("stats") or "") if isinstance(item.get("facts"), dict) else ""
    if stats and not stats.startswith("NONE"):
        return "gear with stats: not junk"
    vendor = float(item.get("sellCopper") or 0)
    if price and price.get("copper") and vendor:
        net = float(price["copper"]) * (1 - AH_CUT)
        if net > vendor * VENDOR_NEAR:
            return (f"worth more on the auction house: about {coins(price['copper'])} each there"
                    + (f" ({price['available']} for sale at the last scan)" if price.get("available") else "")
                    + f", {coins(vendor)} at the vendor")
        return None
    if not scanned and "Crafting Reagent" in str(item.get("tooltip") or ""):
        return "a crafting material with no auction price known (scan the auction house first)"
    return None


def vendor_reason(item: dict, price: dict | None) -> str:
    vendor = float(item.get("sellCopper") or 0)
    if price and price.get("copper"):
        return f"vendor {coins(vendor)} each, auction house {coins(price['copper'])}: not worth posting"
    return f"vendor {coins(vendor)} each, no auctions of it seen"
