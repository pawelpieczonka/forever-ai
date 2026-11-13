-- ForeverAI: selling on the auction house. The AI proposes, the player accepts with a click, and only then anything
-- leaves the bags.
--   Check: with the auction house open, what an item sells for right now (a search of the game for exactly that item:
--          how many offers, from how many sellers, at which prices), and what the price data of the player's auction
--          add-on remembers of it.
--   Owned: the player's own auctions.
--   Post / Cancel: one item is posted or one auction cancelled per click. The game takes these only from a click of
--          the player, so a card with several asks for a click each.
-- This server has the game's newer auction house (AuctionHouseFrame, C_AuctionHouse): what stacks is sold by the piece
-- at a price for one, with no stack sizes; an auction runs for one of three lengths (here 2, 8 or 24 hours). The calls
-- and their order are the ones the auction add-on Auctionator makes on this client.
-- Nothing in this file may fail while it loads: a function missing here would leave the AI without an answer.
local ADDON, ns = ...
local M = {}
ns.Market = M

local CHECK_MAX = 12       -- items looked up per request
local ANSWER_WAIT_S = 4    -- how long the game may take to answer one search before it is asked again
local ATTEMPTS = 3         -- how often one item is asked for
local READY_WAIT_S = 10    -- waiting for the auction house to take the next request
local HOURS = { 2, 8, 24 } -- what the three lengths are on this server (the auction add-on knows, when installed)
local COMMODITY_SORT = { { sortOrder = 0, reverseSort = false } } -- by price, the cheapest first
local ITEM_SORT = { { sortOrder = 4, reverseSort = false } }      -- by buyout, the cheapest first

local function CA() return C_AuctionHouse end
local function has(name) return type(C_AuctionHouse) == "table" and type(C_AuctionHouse[name]) == "function" end
local function call(name, ...)
  if not has(name) then return nil end
  local res = { pcall(C_AuctionHouse[name], ...) }
  if not res[1] then
    if ns.Log then ns.Log("market", name .. " failed: " .. tostring(res[2])) end
    return nil
  end
  return unpack(res, 2)
end
local function log(text) if ns.Log then ns.Log("market", text) end end
local function isOpen() return ns.Auction and ns.Auction.IsOpen() or false end
local function ready() return not has("IsThrottledMessageSystemReady") or call("IsThrottledMessageSystemReady") == true end

-- events are registered one by one: a name this client does not know must not take the others with it
local function listen(frame, events)
  for _, e in ipairs(events) do pcall(frame.RegisterEvent, frame, e) end
end

function M.Available()
  return has("SendSearchQuery") and has("GetItemCommodityStatus") and has("PostCommodity") and has("PostItem")
    and type(ItemLocation) == "table" and type(ItemLocation.CreateFromBagAndSlot) == "function"
end

function M.Durations()
  local d = Auctionator and Auctionator.Constants and Auctionator.Constants.Durations
  if type(d) == "table" and tonumber(d.Short) and tonumber(d.Medium) and tonumber(d.Long) then
    return { tonumber(d.Short), tonumber(d.Medium), tonumber(d.Long) }
  end
  return { HOURS[1], HOURS[2], HOURS[3] }
end

-- the game's number of the length nearest to ``hours`` (1, 2 or 3), and how many hours that is
function M.RunTime(hours)
  local list, best = M.Durations(), 2
  hours = tonumber(hours) or list[2]
  for i, h in ipairs(list) do
    if math.abs(h - hours) < math.abs(list[best] - hours) then best = i end
  end
  return best, list[best]
end

-- how many of an item the bags hold, and where the first stack the game has not locked lies
local function bagUnits(id)
  local n, first = 0, nil
  for bag = 0, 4 do
    for slot = 1, (C_Container.GetContainerNumSlots(bag) or 0) do
      local info = C_Container.GetContainerItemInfo(bag, slot)
      if info and info.itemID == id then
        n = n + (tonumber(info.stackCount) or 1)
        if not first and not info.isLocked then first = { bag, slot } end
      end
    end
  end
  return n, first
end
M.BagUnits = bagUnits

local function locationOf(slot)
  if not slot or type(ItemLocation) ~= "table" then return nil end
  local ok, loc = pcall(ItemLocation.CreateFromBagAndSlot, ItemLocation, slot[1], slot[2])
  return ok and loc or nil
end

-- stackable things are "commodities" to this auction house: sold by the piece, every seller's in one list
local function isCommodity(loc)
  local status = loc and call("GetItemCommodityStatus", loc)
  local kinds = Enum and Enum.ItemCommodityStatus
  return status ~= nil and status == (kinds and kinds.Commodity or 2)
end

------------------------------------------------------------------------ what an item sells for now
local function describe(id, name)
  local itemName, _, quality, _, _, _, _, maxStack, _, _, vendor = ns.safe(C_Item.GetItemInfo, id)
  local have, slot = bagUnits(id)
  local e = { id = id, name = itemName or name, quality = quality, maxStack = maxStack, vendor = vendor, have = have,
              history = ns.Auction and ns.Auction.History and ns.Auction.History(id) or nil }
  local loc = locationOf(slot)
  if have == 0 then
    e.failed = "not in the bags any more"
  elseif not loc then
    e.failed = "the game has the item locked right now"
  elseif has("IsSellItemValid") and call("IsSellItemValid", loc, false) == false then
    e.failed = "the game does not take this item on the auction house (bound, or not for sale)"
  else
    e.commodity = isCommodity(loc) or nil
    -- what the game takes as a deposit for all of them, for each of its three lengths
    local deposits = {}
    for d = 1, 3 do
      local v
      if e.commodity then v = call("CalculateCommodityDeposit", id, d, have) else v = call("CalculateItemDeposit", loc, d, have) end
      if not tonumber(v) then break end
      deposits[d] = tonumber(v)
    end
    if #deposits == 3 then e.deposits = deposits end
  end
  return e, loc
end

-- one level of price: what others offer there, and what is the player's own
local function addOffer(out, byPrice, sellers, unit, quantity, owners, total, mine, mineUnits)
  local level = byPrice[unit]
  if not level then
    level = { unit = unit, auctions = 0, units = 0 }
    byPrice[unit] = level
  end
  local named = 0
  for _, owner in ipairs(type(owners) == "table" and owners or {}) do
    if owner ~= "player" then
      named = named + 1
      sellers[owner] = true
    end
  end
  local others = math.max(named, (tonumber(total) or 0) - (mine and 1 or 0))
  if mine then
    level.mine = (level.mine or 0) + 1
    out.own = out.own + 1
    if not out.ownLow or unit < out.ownLow then out.ownLow = unit end
  end
  if others > 0 or not mine then -- an offer of somebody else's (the player's own are not the market to price against)
    local theirs = math.max(0, quantity - (mine and (tonumber(mineUnits) or 0) or 0))
    level.auctions, level.units = level.auctions + 1, level.units + theirs
    out.auctions, out.units = out.auctions + 1, out.units + theirs
    if others > named then out.unnamed = math.max(out.unnamed or 0, others - named) end
  end
end

local function finishRead(out, byPrice, sellers)
  local levels = {}
  for _, level in pairs(byPrice) do levels[#levels + 1] = level end
  table.sort(levels, function(a, b) return a.unit < b.unit end)
  while #levels > 8 do table.remove(levels) end
  local count = 0
  for _ in pairs(sellers) do count = count + 1 end
  out.sellers, out.levels = count + (out.unnamed or 0), levels
  return out
end

local function readCommodity(id)
  local out, byPrice, sellers = { auctions = 0, units = 0, own = 0, bidOnly = 0 }, {}, {}
  for i = 1, tonumber((call("GetNumCommoditySearchResults", id))) or 0 do
    local r = call("GetCommoditySearchResultInfo", id, i)
    if type(r) == "table" and tonumber(r.unitPrice) and tonumber(r.quantity) and r.quantity > 0 then
      local mine = r.containsOwnerItem or (type(r.owners) == "table" and r.owners[1] == "player") or false
      addOffer(out, byPrice, sellers, r.unitPrice, r.quantity, r.owners, r.totalNumberOfOwners, mine, r.numOwnerItems)
    end
  end
  if has("HasFullCommoditySearchResults") and call("HasFullCommoditySearchResults", id) == false then out.more = true end
  return finishRead(out, byPrice, sellers)
end

local function readItems(key)
  local out, byPrice, sellers = { auctions = 0, units = 0, own = 0, bidOnly = 0 }, {}, {}
  for i = 1, tonumber((call("GetNumItemSearchResults", key))) or 0 do
    local r = call("GetItemSearchResultInfo", key, i)
    if type(r) == "table" then
      local quantity, buyout = tonumber(r.quantity) or 1, tonumber(r.buyoutAmount) or 0
      if buyout <= 0 then
        out.bidOnly = out.bidOnly + 1
      else
        addOffer(out, byPrice, sellers, math.floor(buyout / quantity + 0.5), quantity, r.owners, r.totalNumberOfOwners,
          r.containsOwnerItem or false, quantity)
      end
    end
  end
  if has("HasFullItemSearchResults") and call("HasFullItemSearchResults", key) == false then out.more = true end
  return finishRead(out, byPrice, sellers)
end

local checker = CreateFrame("Frame")
checker:Hide()
local job
listen(checker, { "COMMODITY_SEARCH_RESULTS_UPDATED", "ITEM_SEARCH_RESULTS_UPDATED" })
checker:SetScript("OnEvent", function(_, event, what)
  local cur = job and job.current
  if not cur then return end
  local id = type(what) == "table" and what.itemID or what
  if tonumber(id) == cur.e.id then cur.answered = true end
end)

local function finishCheck(err)
  local j = job
  job = nil
  checker:Hide()
  if not j then return end
  if err then log("check ended: " .. err) end
  local ok, e = pcall(j.reply, { items = j.out, error = err, durations = M.Durations(), ahOpen = isOpen() })
  if not ok then ns.lastError = "auction check reply: " .. tostring(e) end
end

local function sendSearch(cur)
  cur.attempts, cur.sent, cur.answered = cur.attempts + 1, GetTime(), false
  if cur.e.commodity then
    call("SendSearchQuery", cur.key, COMMODITY_SORT, true)
  elseif has("SendSellSearchQuery") then
    call("SendSellSearchQuery", cur.key, ITEM_SORT, true)
  else
    call("SendSearchQuery", cur.key, ITEM_SORT, true)
  end
end

local function takeResults(j, cur)
  local e = cur.e
  local market = e.commodity and readCommodity(e.id) or readItems(cur.key)
  for k, v in pairs(market) do e[k] = v end
  log(string.format("%s (%s): %d offers from %d sellers, %d of the player's own, lowest %s", tostring(e.name),
    e.commodity and "sold by the piece" or "item", e.auctions, e.sellers, e.own,
    e.levels[1] and tostring(e.levels[1].unit) or "none"))
  j.out[#j.out + 1] = e
  j.current = nil
end

checker:SetScript("OnUpdate", function()
  local j = job
  if not j then checker:Hide() return end
  local now = GetTime()
  if not isOpen() then finishCheck("the auction house window was closed") return end
  local cur = j.current
  if cur and cur.sent then
    if cur.answered then
      -- (the game sometimes answers a search with nothing although there is something: asked again, like the
      -- auction add-on does; an item nobody sells answers "all results, none of them")
      local full, quantity
      if cur.e.commodity then
        full, quantity = call("HasFullCommoditySearchResults", cur.e.id), tonumber((call("GetCommoditySearchResultsQuantity", cur.e.id))) or 0
      else
        full, quantity = call("HasFullItemSearchResults", cur.key), tonumber((call("GetItemSearchResultsQuantity", cur.key))) or 0
      end
      local known = not has("HasSearchResults") or call("HasSearchResults", cur.key)
      if (not known or (full == false and quantity == 0)) and cur.attempts < ATTEMPTS then
        cur.sent = nil
      else
        takeResults(j, cur)
      end
    elseif now - cur.sent > ANSWER_WAIT_S then
      if cur.attempts < ATTEMPTS then
        cur.sent = nil
      else
        cur.e.failed = "the auction house did not answer the search"
        log(tostring(cur.e.name) .. ": no answer after " .. cur.attempts .. " searches")
        j.out[#j.out + 1] = cur.e
        j.current = nil
      end
    end
    return
  end
  if not cur then
    local nextItem = j.items[#j.out + 1]
    if not nextItem then finishCheck() return end
    local e, loc = describe(nextItem.id, nextItem.name)
    if e.failed then
      j.out[#j.out + 1] = e
      return
    end
    local key
    if e.commodity then
      key = call("MakeItemKey", e.id)
    else -- an item with a level or a suffix: every variant is searched, as the auction add-on does when selling
      key = { itemID = e.id, itemLevel = 0, itemSuffix = 0, battlePetSpeciesID = 0 }
    end
    if not key then
      e.failed = "the game gave no auction key for the item"
      j.out[#j.out + 1] = e
      return
    end
    cur = { e = e, key = key, attempts = 0, since = now }
    j.current = cur
  end
  -- the auction house takes one request at a time, and knows an item only once its data is loaded
  if not ready() or (has("GetItemKeyInfo") and call("GetItemKeyInfo", cur.key) == nil and now - cur.since < 3) then
    cur.blocked = cur.blocked or now
    if now - cur.blocked > READY_WAIT_S then
      finishCheck("the auction house is busy (another add-on is scanning?); try again in a moment")
    end
    return
  end
  cur.blocked = nil
  sendSearch(cur)
end)

-- req.items = { { id = 3173, name = "Bear Meat" }, ... }; reply({ items = { ... }, durations, ahOpen, error })
function M.Check(req, reply)
  if not isOpen() then reply({ error = "the auction house window is not open", ahOpen = false }) return end
  if not M.Available() then
    reply({ error = "this game client has no auction house functions an add-on can use" })
    return
  end
  if job then reply({ error = "another price check is still running" }) return end
  local items = {}
  for _, it in ipairs(type(req) == "table" and type(req.items) == "table" and req.items or {}) do
    local id = type(it) == "table" and tonumber(it.id)
    if id and #items < CHECK_MAX then items[#items + 1] = { id = id, name = it.name } end
  end
  if #items == 0 then reply({ error = "no items to check" }) return end
  log("checking " .. #items .. " items")
  job = { items = items, out = {}, reply = reply }
  checker:Show()
end

------------------------------------------------------------------------ the player's own auctions
local function leftText(seconds)
  seconds = tonumber(seconds)
  if not seconds then return nil end
  if seconds < 3600 then return math.max(1, math.floor(seconds / 60)) .. " min" end
  return string.format("%.0f h", seconds / 3600)
end

local function ownedList()
  local out = {}
  local sold = Enum and Enum.AuctionStatus and Enum.AuctionStatus.Sold or 1
  for i = 1, tonumber((call("GetNumOwnedAuctions"))) or 0 do
    local a = call("GetOwnedAuctionInfo", i)
    if type(a) == "table" and a.auctionID then
      local id = type(a.itemKey) == "table" and a.itemKey.itemID or nil
      local name = id and ns.safe(C_Item.GetItemInfo, id) or (type(a.itemLink) == "string" and a.itemLink:match("%[(.-)%]")) or nil
      local count = tonumber(a.quantity) or 1
      local unit = tonumber(a.buyoutAmount) or 0 -- the price of one piece
      out[#out + 1] = { n = i, auctionID = a.auctionID, id = id, name = name or ("item " .. tostring(id)), count = count,
                        unit = unit, buyout = unit * count, bid = (tonumber(a.bidAmount) or 0) > 0 and tonumber(a.bidAmount) or nil,
                        bidder = a.bidder and true or nil, sold = a.status == sold or nil, left = leftText(a.timeLeftSeconds) }
    end
  end
  return out
end

local ownedFrame = CreateFrame("Frame")
local ownedAt, cancelled, cancelledAt = 0, {}, 0
listen(ownedFrame, { "OWNED_AUCTIONS_UPDATED", "AUCTION_CANCELED" })
ownedFrame:SetScript("OnEvent", function(_, event, auctionID)
  if event == "AUCTION_CANCELED" then
    cancelledAt = GetTime() -- (the number of the auction does not always come with it)
    if auctionID then cancelled[auctionID] = true end
  else
    ownedAt = GetTime()
  end
end)

-- asks the game for the list and calls ``after`` once it has answered (or a moment later, when it says nothing)
local function refreshOwned(after)
  local asked = GetTime()
  if ready() then call("QueryOwnedAuctions", {}) end
  local waited = 0
  local ticker
  ticker = C_Timer.NewTicker(0.2, function()
    waited = waited + 0.2
    if ownedAt >= asked or waited >= 1.6 then
      ticker:Cancel()
      pcall(after)
    end
  end)
end

function M.Owned(reply)
  if not isOpen() then reply({ error = "the auction house window is not open", ahOpen = false }) return end
  if not has("GetNumOwnedAuctions") then reply({ error = "this game client has no list of auctions an add-on can read" }) return end
  refreshOwned(function()
    local list = ownedList()
    log("own auctions: " .. #list)
    local ok, e = pcall(reply, { auctions = list, ahOpen = isOpen() })
    if not ok then ns.lastError = "owned auctions reply: " .. tostring(e) end
  end)
end

------------------------------------------------------------------------ posting (inside the player's click)
local POST_WAIT_S = 5 -- how long the game may take to take the auction

local postFrame = CreateFrame("Frame")
local created, postFailed = 0, nil
listen(postFrame, { "AUCTION_HOUSE_AUCTION_CREATED", "AUCTION_MULTISELL_FAILURE", "AUCTION_HOUSE_SHOW_ERROR" })
postFrame:SetScript("OnEvent", function(_, event)
  if event == "AUCTION_HOUSE_AUCTION_CREATED" then created = created + 1 else postFailed = event end
end)

-- What the game takes as the deposit for a lot as it stands now (copper); nil when it cannot say (the window is
-- closed, the item is not in the bags). For the card, when the player changed the count or the length on it.
function M.Deposit(lot)
  local id, count = tonumber(lot.id), math.floor(tonumber(lot.count) or 0)
  if not id or count < 1 or not isOpen() then return nil end
  local _, slot = bagUnits(id)
  local loc = locationOf(slot)
  if not loc then return nil end
  local duration = M.RunTime(lot.hours)
  local v
  if isCommodity(loc) then v = call("CalculateCommodityDeposit", id, duration, count) else v = call("CalculateItemDeposit", loc, duration, count) end
  return tonumber(v)
end

-- lot = { id, name, count, unit (copper for one piece), hours }. Runs in the click on the card: the game takes an
-- auction only from a click of the player. Returns false, why; or true, and ``done(posted, wanted, deposit paid,
-- why it ended short or nil)`` is called when the game has taken (or refused) it.
function M.Post(lot, done)
  if not isOpen() then return false, "The auction house window is closed." end
  if not M.Available() then return false, "This game client has no way for an add-on to post an auction." end
  local id, count, unit = tonumber(lot.id), math.floor(tonumber(lot.count) or 0), math.floor((tonumber(lot.unit) or 0) + 0.5)
  if not id or count < 1 or unit < 1 then return false, "The lot is not complete." end
  local have, slot = bagUnits(id)
  if have < count then
    return false, string.format("Only %d of %s left in the bags.", have, tostring(lot.name or id))
  end
  local loc = locationOf(slot)
  if not loc then return false, "The item is locked by the game right now; try again." end
  if has("IsSellItemValid") and call("IsSellItemValid", loc, false) == false then
    return false, "The game does not take this item on the auction house (bound, or not for sale)."
  end
  local duration = M.RunTime(lot.hours)
  local commodity = isCommodity(loc)
  local deposit = commodity and call("CalculateCommodityDeposit", id, duration, count)
    or (not commodity and call("CalculateItemDeposit", loc, duration, count)) or 0
  deposit = tonumber(deposit) or 0
  local money = GetMoney()
  if deposit > money then return false, "Not enough money for the deposit (" .. ns.Coins(deposit) .. ")." end
  created, postFailed = 0, nil
  local frame = AuctionHouseFrame
  -- (a client that asks to confirm a post returns true: the game's own window then shows its question)
  if commodity then
    log(string.format("PostCommodity %s x%d at %d each, length %d, deposit %d", tostring(lot.name), count, unit, duration, deposit))
    local ok, confirm = pcall(C_AuctionHouse.PostCommodity, loc, duration, count, unit)
    if not ok then log("PostCommodity failed: " .. tostring(confirm)) return false, "The game refused the auction: " .. tostring(confirm) end
    if confirm and frame and frame.CommoditiesSellFrame and frame.CommoditiesSellFrame.CachePendingPost then
      pcall(frame.CommoditiesSellFrame.CachePendingPost, frame.CommoditiesSellFrame, loc, duration, count, unit)
    end
  else
    local buyout = unit
    log(string.format("PostItem %s x%d at %d each, length %d, deposit %d", tostring(lot.name), count, buyout, duration, deposit))
    local ok, confirm = pcall(C_AuctionHouse.PostItem, loc, duration, count, nil, buyout)
    if not ok then log("PostItem failed: " .. tostring(confirm)) return false, "The game refused the auction: " .. tostring(confirm) end
    if confirm and frame and frame.ItemSellFrame and frame.ItemSellFrame.CachePendingPost then
      pcall(frame.ItemSellFrame.CachePendingPost, frame.ItemSellFrame, loc, duration, count, nil, buyout)
    end
  end
  -- the result is what the bags hold once the game has answered: the pieces are gone, or they are not
  local waited, last, still = 0, nil, 0
  local ticker
  ticker = C_Timer.NewTicker(0.25, function()
    waited = waited + 0.25
    local posted = math.max(0, have - bagUnits(id))
    still = posted == last and still + 1 or 0
    last = posted
    if posted >= count or postFailed or waited >= POST_WAIT_S or (posted > 0 and still >= 6) or not isOpen() then
      ticker:Cancel()
      local why = posted < count and (postFailed and "the game reported an error" or isOpen() and "the game did not take it"
        or "the auction house was closed") or nil
      log(string.format("posted %d of %d %s%s", posted, count, tostring(lot.name), why and (": " .. why) or ""))
      if done then pcall(done, posted, count, math.max(0, money - GetMoney()), why) end
    end
  end)
  return true
end

------------------------------------------------------------------------ cancelling (inside the player's click)
-- auction = { auctionID, id, name, count, unit }: one of the player's own. Returns false, why; or true, and
-- done(cancelled) is called when the game has answered.
function M.Cancel(auction, done)
  if not isOpen() then return false, "The auction house window is closed." end
  if not has("CancelAuction") then return false, "This game client has no way for an add-on to cancel an auction." end
  local function find()
    local same
    for _, a in ipairs(ownedList()) do
      if not a.sold then
        if auction.auctionID and a.auctionID == auction.auctionID then return a end
        if not same and a.id == auction.id and a.count == auction.count and a.unit == auction.unit then same = a end
      end
    end
    return not auction.auctionID and same or nil
  end
  local which = find()
  if not which then return false, "This auction is no longer among yours (sold, expired or cancelled)." end
  local aid = which.auctionID
  if has("CanCancelAuction") and call("CanCancelAuction", aid) == false then
    return false, "The game does not let this auction be cancelled right now."
  end
  local cost = tonumber((call("GetCancelCost", aid))) or 0
  if cost > GetMoney() then return false, "Cancelling it costs " .. ns.Coins(cost) .. " (it has a bid), more than you have." end
  cancelled[aid] = nil
  local asked = GetTime()
  log(string.format("CancelAuction %s x%d (auction %s, cost %d)", tostring(which.name), which.count, tostring(aid), cost))
  local ok, err = pcall(C_AuctionHouse.CancelAuction, aid)
  if not ok then log("CancelAuction failed: " .. tostring(err)) return false, "The game refused to cancel it: " .. tostring(err) end
  -- the game says so when the auction is gone; when it says nothing for a while, the list of auctions is asked
  -- for again and looked through
  local waited, again = 0, false
  local ticker
  ticker = C_Timer.NewTicker(0.3, function()
    waited = waited + 0.3
    local gone = cancelled[aid] or cancelledAt >= asked
    if not gone and waited >= 1.5 and not again and isOpen() then
      again = true
      if ready() then call("QueryOwnedAuctions", {}) end
    end
    local known = gone
    if not gone and waited >= 4 and isOpen() and ownedAt >= asked then -- a fresh list: is it still on it?
      gone, known = true, true
      for _, a in ipairs(ownedList()) do
        if a.auctionID == aid then gone = false end
      end
    end
    if gone or waited >= 4 or not isOpen() then
      ticker:Cancel()
      -- the window closed, or the list never came: not known is not the same as not cancelled
      local unsure = not gone and not known
      log("cancelled: " .. (unsure and "not known (the game did not say)" or tostring(gone)))
      if done then pcall(done, gone, cost, unsure) end
    end
  end)
  return true, which
end
