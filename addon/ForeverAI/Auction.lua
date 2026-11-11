-- ForeverAI: auction house prices from the player's auction add-on, for the AI's search_auction_house tool.
-- Only reads: Auctionator's price data (its public API v1 and the realm's price table) and Pawn's upgrade check.
-- The other add-ons' code and data are never changed.
local ADDON, ns = ...
local AH = {}
ns.Auction = AH
local safe = ns.safe

local CALLER = "ForeverAI"
local BUDGET_MS = 4       -- work per frame, so the game never stalls on a large price table
local LOAD_MAX = 250      -- item data requested from the server per search
local LOAD_WAIT_S = 6     -- how long to wait for that item data
local HANDS = { INVTYPE_WEAPON = true, INVTYPE_WEAPONMAINHAND = true, INVTYPE_WEAPONOFFHAND = true,
  INVTYPE_SHIELD = true, INVTYPE_HOLDABLE = true, INVTYPE_2HWEAPON = true }

------------------------------------------------------------------------ the auction house window
local ahOpen = false
ns.On("AUCTION_HOUSE_SHOW", function() ahOpen = true end)
ns.On("AUCTION_HOUSE_CLOSED", function() ahOpen = false end)
function AH.IsOpen()
  local f = AuctionHouseFrame or AuctionFrame
  return ahOpen or (f and f.IsShown and f:IsShown()) or false
end

------------------------------------------------------------------------ Auctionator
local function auctionator()
  return Auctionator and Auctionator.API and Auctionator.API.v1 and Auctionator
end

-- the price table of the current realm: dbKey -> { m = last lowest buyout, h/l = per-day prices, a = per-day count }
local function priceTable()
  local A = auctionator()
  if not A then return nil, "Auctionator is not installed or not enabled; prices need an auction add-on" end
  local realm = A.State and A.State.CurrentRealm
  local db = realm and type(AUCTIONATOR_PRICE_DATABASE) == "table" and AUCTIONATOR_PRICE_DATABASE[realm]
  if type(db) ~= "table" and A.Database and type(A.Database.db) == "table" then db = A.Database.db end
  if type(db) ~= "table" then return nil, "Auctionator has no prices for this realm yet: open the auction house and scan" end
  return db, nil, realm
end

local function scanDay(t)
  local zero = Auctionator and Auctionator.Constants and Auctionator.Constants.SCAN_DAY_0
    or time({ year = 2020, month = 1, day = 1, hour = 0 })
  return math.floor(((t or time()) - zero) / 86400)
end

local function ago(seconds)
  if not seconds or seconds < 0 then return nil end
  if seconds < 3600 then return math.max(1, math.floor(seconds / 60)) .. " min ago" end
  if seconds < 86400 * 2 then return math.floor(seconds / 3600) .. " h ago" end
  return math.floor(seconds / 86400) .. " days ago"
end

-- when Auctionator's last full scan started: a replicate scan, a legacy GetAll scan or the modern AH's browse scan
-- (TimeOfLastBrowseScan, set only by the Full Scan button and the autoscan)
local function fullScanTime()
  local sv = type(AUCTIONATOR_SAVEDVARS) == "table" and AUCTIONATOR_SAVEDVARS or {}
  local best = 0
  for _, key in ipairs({ "TimeOfLastReplicateScan", "TimeOfLastGetAllScan", "TimeOfLastBrowseScan" }) do
    local t = tonumber(sv[key])
    if t and t > best then best = t end
  end
  return best > 0 and best or nil
end

local function lastScan(job)
  local t = fullScanTime()
  if not t then return nil end
  local early = job and job.scanDay and not job.cutoff
  return ago(time() - t) .. (early and " (full scan, stopped early: older prices are included)" or " (full scan)")
end

-- days since an entry was last seen, its count of listings on that day and that day
local function freshness(entry, today)
  local newest
  for day in pairs(type(entry.h) == "table" and entry.h or {}) do
    local d = tonumber(day)
    if d and (not newest or d > newest) then newest = d end
  end
  if not newest then return nil end
  local avail = type(entry.a) == "table" and tonumber(entry.a[tostring(newest)]) or nil
  return today - newest, avail, newest
end

-- An item the last full scan did not see was not listed then (sold, expired or cancelled): its price is an offer that
-- is gone. Every pass over the price data counts the items seen on the scan's day or later and those seen only in the
-- week before it; when the scan saw at least as many, it finished and its day becomes the cutoff. A scan the player
-- stopped early sets none, and the older prices stay in.
local function countForScan(job, newest)
  if not (job.scanDay and newest) then return end
  if newest >= job.scanDay then
    job.inScan = job.inScan + 1
  elseif newest >= job.scanDay - 7 then
    job.before = job.before + 1
  end
end

local function settleCutoff(job)
  if job.scanDay and job.inScan > 0 and job.inScan >= job.before then job.cutoff = job.scanDay end
end

local function current(job, offer) return not job.cutoff or offer.newest >= job.cutoff end

local function seenText(age) return age == 0 and "today" or (age == 1 and "yesterday" or (age .. " days ago")) end

local function itemIdOf(key)
  if type(key) ~= "string" then return nil end
  return tonumber(key) or tonumber(key:match("^gr?:(%d+)"))
end

------------------------------------------------------------------------ upgrades
local function pawnReady() return PawnIsInitialized and PawnGetItemData and PawnIsItemAnUpgrade and true or false end

local function pawnUpgrade(link)
  if not pawnReady() then return nil end
  local okI, item = pcall(PawnGetItemData, link)
  if not okI or type(item) ~= "table" then return nil end
  local ok, info = pcall(PawnIsItemAnUpgrade, item)
  if not ok then return nil end
  if type(info) ~= "table" or #info == 0 then return { pct = 0, text = "not an upgrade" } end
  local best = info[1]
  for _, u in ipairs(info) do if (u.PercentUpgrade or u[3] or 0) > (best.PercentUpgrade or best[3] or 0) then best = u end end
  local pct = tonumber(best.PercentUpgrade or best[3]) or 0
  local scale = best.LocalizedScaleName or best[2] or best.ScaleName or best[1]
  return { pct = pct, text = string.format("+%.1f%% upgrade%s", pct * 100, scale and (" (" .. ns.stripCodes(tostring(scale)) .. ")") or "") }
end

-- Pawn's value of an item in one of the player's scales (Values: { scale name, value, ... } for each scale shown in
-- tooltips): what ranks "the best" items, also when none of them beats what is equipped. job.pawnScale keeps the
-- scale the first item was valued in, so every item of a search is valued in the same one.
local function scaleValue(job, item)
  if type(item) ~= "table" or type(item.Values) ~= "table" then return nil end
  local pick
  for _, v in ipairs(item.Values) do
    if type(v) == "table" and v[1] == (job.pawnScale or v[1]) then pick = v break end
  end
  if not pick or not tonumber(pick[2]) then return nil end
  job.pawnScale = pick[1]
  return tonumber(pick[2])
end

local function pawnValue(job, link)
  if not pawnReady() then return nil end
  local ok, item = pcall(PawnGetItemData, link)
  return ok and scaleValue(job, item) or nil
end

-- the value of what is equipped in an item's slots (the weaker one for rings and trinkets; an empty slot is 0)
local function equippedValue(job, slots)
  if not PawnGetItemDataForInventorySlot then return nil end
  local key = table.concat(slots, ",")
  job.equipped = job.equipped or {}
  if job.equipped[key] == nil then
    local worst, name = nil, nil
    for _, slot in ipairs(slots) do
      local link, v = GetInventoryItemLink("player", slot), 0
      if link then -- an item Pawn cannot value is left out, not taken as 0
        local ok, item = pcall(PawnGetItemDataForInventorySlot, slot)
        v = ok and scaleValue(job, item) or nil
      end
      if v and (not worst or v < worst) then worst, name = v, link and ns.stripCodes(link) or "(empty slot)" end
    end
    job.equipped[key] = worst and { value = worst, name = name } or false
  end
  return job.equipped[key] or nil
end

-- without Pawn: weapons by damage per second, other gear by item level, against the item in the same slot
local function statGain(facts, equipLoc, ilvl)
  local slots = ns.Auction.slotsFor(equipLoc)
  local worst
  for _, slot in ipairs(slots) do
    local cur = GetInventoryItemLink("player", slot)
    local gain
    if not cur then
      gain = 1000
    elseif HANDS[equipLoc] and equipLoc ~= "INVTYPE_SHIELD" and equipLoc ~= "INVTYPE_HOLDABLE" then
      local stats = (C_Item and C_Item.GetItemStats or GetItemStats)
      local mine = stats and safe(stats, cur) or {}
      local new = facts and facts.dps
      local old = mine and (mine.ITEM_MOD_DAMAGE_PER_SECOND_SHORT or 0) or 0
      gain = (new or 0) - old
    else
      local curIlvl = select(4, safe(C_Item.GetItemInfo, cur)) or 0
      gain = (ilvl or 0) - curIlvl
    end
    if not worst or gain < worst then worst = gain end
  end
  return worst
end

local SLOTS = {
  INVTYPE_HEAD = { 1 }, INVTYPE_NECK = { 2 }, INVTYPE_SHOULDER = { 3 }, INVTYPE_CHEST = { 5 }, INVTYPE_ROBE = { 5 },
  INVTYPE_WAIST = { 6 }, INVTYPE_LEGS = { 7 }, INVTYPE_FEET = { 8 }, INVTYPE_WRIST = { 9 }, INVTYPE_HAND = { 10 },
  INVTYPE_FINGER = { 11, 12 }, INVTYPE_TRINKET = { 13, 14 }, INVTYPE_CLOAK = { 15 }, INVTYPE_WEAPON = { 16 },
  INVTYPE_2HWEAPON = { 16 }, INVTYPE_WEAPONMAINHAND = { 16 }, INVTYPE_WEAPONOFFHAND = { 17 }, INVTYPE_SHIELD = { 17 },
  INVTYPE_HOLDABLE = { 17 }, INVTYPE_RANGED = { 18 }, INVTYPE_RANGEDRIGHT = { 18 }, INVTYPE_THROWN = { 18 },
  INVTYPE_RELIC = { 18 },
}
function AH.slotsFor(equipLoc) return SLOTS[equipLoc] or {} end

------------------------------------------------------------------------ search
local function set(list)
  if type(list) ~= "table" or #list == 0 then return nil end
  local s = {}
  for _, v in ipairs(list) do s[v] = true end
  return s
end

local function words(query)
  local out = {}
  for w in tostring(query or ""):lower():gmatch("[%w']+") do out[#out + 1] = w end
  return out
end

local function money(c)
  c = math.floor((tonumber(c) or 0) + 0.5)
  local g, s, cu = math.floor(c / 10000), math.floor(c / 100) % 100, c % 100
  if g > 0 then return string.format("%dg %ds %dc", g, s, cu) end
  if s > 0 then return string.format("%ds %dc", s, cu) end
  return cu .. "c"
end
ns.Coins = money -- copper as "1g 2s 3c", for the other parts of the add-on

-- step 1 (in slices over several frames): every priced item that passes the filters the game knows instantly
local function collect(job)
  local req, today = job.req, scanDay()
  local classes, subclasses, locs = set(req.classIDs), set(req.subclassIDs), set(req.equipLocs)
  local maxC, minC, maxAge = tonumber(req.maxCopper), tonumber(req.minCopper) or 0, tonumber(req.maxAgeDays) or 7
  local byId, n = {}, 0
  for key, entry in pairs(job.db) do
    n = n + 1
    if n % 200 == 0 and debugprofilestop() - job.slice > BUDGET_MS then coroutine.yield() end
    local id = itemIdOf(key)
    local price = type(entry) == "table" and tonumber(entry.m)
    if id and price and price > 0 then
      local age, avail, newest = freshness(entry, today)
      countForScan(job, newest)
      if age and age <= maxAge and (not maxC or price <= maxC) and price >= minC then
        local _, _, _, equipLoc, _, classID, subclassID = C_Item.GetItemInfoInstant(id)
        if classID and (not classes or classes[classID]) and (not subclasses or subclasses[subclassID])
          and (not locs or locs[equipLoc]) then
          -- every price of the item (gear can have several keys); the cheapest one still listed wins below
          byId[id] = byId[id] or { id = id, equipLoc = equipLoc, classID = classID, offers = {} }
          table.insert(byId[id].offers, { copper = price, age = age, available = avail, newest = newest })
        end
      end
    end
  end
  job.dbItems = n
  settleCutoff(job)
  local list = {}
  job.gone = 0
  for _, c in pairs(byId) do
    for _, o in ipairs(c.offers) do
      if current(job, o) and (not c.copper or o.copper < c.copper) then
        c.copper, c.age, c.available = o.copper, o.age, o.available
      end
    end
    c.offers = nil
    if c.copper then list[#list + 1] = c else job.gone = job.gone + 1 end
  end
  -- with a budget, the items closest to it are the likely upgrades; for "price" the cheapest first
  table.sort(list, function(a, b)
    if req.sort == "price" then return a.copper < b.copper end
    return a.copper > b.copper
  end)
  job.candidates = list
end

-- step 2: names, links and facts (item data the client does not have yet is requested from the server first)
local function describe(job)
  local req = job.req
  local want = words(req.query)
  local out, notLoaded = {}, 0
  local skipped = { ["not usable"] = 0, ["too high level"] = 0, ["not an upgrade"] = 0,
    ["no longer listed (not in the last full scan)"] = job.gone or 0 }
  local method = pawnReady() and "Pawn (the player's scales)" or
    "comparison with the equipped item (weapons: damage per second, other gear: item level)"
  for i, c in ipairs(job.candidates) do
    if i % 25 == 0 and debugprofilestop() - job.slice > BUDGET_MS then coroutine.yield() end
    local name, link, _, ilvl, reqLevel, _, subType = safe(C_Item.GetItemInfo, c.id)
    if not name then
      notLoaded = notLoaded + 1
    else
      local low, ok = name:lower(), true
      for _, w in ipairs(want) do if not low:find(w, 1, true) then ok = false break end end
      local facts = ok and ns.Collect.ItemFacts(link, true) or nil
      if ok and req.usableOnly and facts and facts.cannotUse then skipped["not usable"] = skipped["not usable"] + 1 ok = false end
      if ok and req.usableOnly and facts and facts.tooLowLevel then skipped["too high level"] = skipped["too high level"] + 1 ok = false end
      if ok then
        local e = { id = c.id, name = name, price = money(c.copper), copper = c.copper,
          seen = seenText(c.age), available = c.available,
          quality = facts and facts.quality, ilvl = ilvl, requiresLevel = reqLevel and reqLevel > 1 and reqLevel or nil,
          slot = (c.equipLoc and c.equipLoc ~= "" and _G[c.equipLoc]) or nil, type = subType,
          stats = facts and facts.stats, vsEquipped = facts and facts.vsEquipped, cannotUse = facts and facts.cannotUse,
          tooLowLevel = facts and facts.tooLowLevel and ("requires level " .. tostring(reqLevel)) or nil }
        if c.classID == 2 or c.classID == 4 then
          local pawn = pawnUpgrade(link)
          if pawn then
            e.pawn, e.score = pawn.text, pawn.pct * 1000
            -- upgrades first by how much; then, also among the items that are no upgrade, by the scale's value
            e.value = pawnValue(job, link)
            local mine = e.value and equippedValue(job, AH.slotsFor(c.equipLoc))
            if e.value then
              e.pawn = string.format("%s; value %.1f%s", pawn.text, e.value,
                mine and string.format(" vs %.1f for %s", mine.value, mine.name) or "")
            end
          else
            local stats = (C_Item and C_Item.GetItemStats or GetItemStats)
            local raw = stats and safe(stats, link) or {}
            local gain = statGain({ dps = raw and raw.ITEM_MOD_DAMAGE_PER_SECOND_SHORT }, c.equipLoc, ilvl)
            e.score = gain
          end
          if req.upgradesOnly and (e.score or 0) <= 0 then
            skipped["not an upgrade"] = skipped["not an upgrade"] + 1
            e = nil
          end
        end
        if e then out[#out + 1] = e end
      end
    end
  end
  table.sort(out, function(a, b)
    if req.sort == "price" then return a.copper < b.copper end
    if req.sort == "ilvl" and (a.ilvl or 0) ~= (b.ilvl or 0) then return (a.ilvl or 0) > (b.ilvl or 0) end
    if (a.score or 0) ~= (b.score or 0) then return (a.score or 0) > (b.score or 0) end
    if (a.value or 0) ~= (b.value or 0) then return (a.value or 0) > (b.value or 0) end
    return a.copper < b.copper
  end)
  local limit = math.min(30, tonumber(req.limit) or 15)
  local items = {}
  for i = 1, math.min(limit, #out) do
    local e = out[i]
    e.score, e.value = nil, nil
    items[i] = e
  end
  return { source = "Auctionator", realm = job.realm, lastScan = lastScan(job), dbItems = job.dbItems,
    matched = #out, items = items, notLoaded = notLoaded > 0 and notLoaded or nil, skipped = skipped,
    upgradeMethod = method, sort = req.sort or "upgrade", ahOpen = AH.IsOpen() }
end

------------------------------------------------------------------------ exact items (a shopping list, reagents, crafted items)
local EXACT_MAX = 100 -- the model asks for up to 40; the app prices the whole bags (compare_sell_prices) in one call

-- an item name -> its id, from what the game already knows (no server request)
local function idByName(name)
  local link = ns.Collect and ns.Collect.knownLinks and ns.Collect.knownLinks[name]
  local id = link and tonumber(link:match("item:(%d+)"))
  if id then return id end
  if ns.Professions and ns.Professions.ItemId then
    id = ns.Professions.ItemId(name)
    if id then return id end
  end
  return C_Item.GetItemInfoInstant and (safe(C_Item.GetItemInfoInstant, name)) or nil
end

-- step 1 (in slices): every requested item's lowest price; names the game could not tell are matched against the
-- names of the priced items it has in its cache
local function exactScan(job)
  local req, today = job.req, scanDay()
  local maxAge = tonumber(req.maxAgeDays) or 30
  local wanted, byName = {}, {}
  job.asked = {}
  for i, it in ipairs(req.items) do
    if i > EXACT_MAX then break end
    local name = type(it) == "table" and type(it.name) == "string" and it.name or nil
    local e = { ask = name or tostring(it.id), id = type(it) == "table" and tonumber(it.id) or nil }
    if not e.id and name then e.id = idByName(name) end
    job.asked[#job.asked + 1] = e
    if e.id then
      wanted[e.id] = wanted[e.id] or {}
      table.insert(wanted[e.id], e)
    elseif name then
      byName[name:lower()] = e
    end
  end
  local unnamed, n = next(byName) ~= nil, 0
  for key, entry in pairs(job.db) do
    n = n + 1
    if n % 200 == 0 and debugprofilestop() - job.slice > BUDGET_MS then coroutine.yield() end
    local id = itemIdOf(key)
    if id and type(entry) == "table" then
      if unnamed and not wanted[id] then
        local name = safe(C_Item.GetItemInfo, id) -- only items in the client's cache have a name here
        local e = name and byName[name:lower()]
        if e then
          e.id, wanted[id], byName[name:lower()] = id, { e }, nil
          unnamed = next(byName) ~= nil
        end
      end
      local price = tonumber(entry.m)
      if price and price > 0 then
        local age, avail, newest = freshness(entry, today)
        countForScan(job, newest)
        if wanted[id] and age and age <= maxAge then
          for _, e in ipairs(wanted[id]) do
            e.offers = e.offers or {}
            table.insert(e.offers, { copper = price, age = age, available = avail, newest = newest })
          end
        end
      end
    end
  end
  job.dbItems, job.maxAge = n, maxAge
  settleCutoff(job)
  -- the cheapest price still listed; otherwise the latest offer that is gone, as the last price seen
  for _, e in ipairs(job.asked) do
    for _, o in ipairs(e.offers or {}) do
      if current(job, o) then
        if not e.copper or o.copper < e.copper then e.copper, e.age, e.available = o.copper, o.age, o.available end
      elseif not e.last or o.newest > e.last.newest then
        e.last = o
      end
    end
  end
end

local function exactResult(job)
  local items, missing = {}, {}
  for _, e in ipairs(job.asked) do
    local name = e.id and safe(C_Item.GetItemInfo, e.id)
    if not e.id then
      missing[#missing + 1] = { name = e.ask, why = "unknown item name (not in the game's item cache): give the item id" }
    elseif not e.copper and e.last then
      missing[#missing + 1] = { id = e.id, name = name or e.ask, why = "not listed in the last full scan",
        lastPrice = money(e.last.copper), lastCopper = e.last.copper, lastSeen = seenText(e.last.age) }
    elseif not e.copper then
      missing[#missing + 1] = { id = e.id, name = name or e.ask,
        why = "no auction price seen in the last " .. job.maxAge .. " days" }
    else
      items[#items + 1] = { id = e.id, name = name or e.ask, price = money(e.copper), copper = e.copper,
        seen = seenText(e.age), available = e.available }
    end
  end
  return { source = "Auctionator", realm = job.realm, lastScan = lastScan(job), dbItems = job.dbItems, exact = true,
    maxAgeDays = job.maxAge, matched = #items, items = items, missing = #missing > 0 and missing or nil,
    sort = "the requested order", ahOpen = AH.IsOpen() }
end

local runner = CreateFrame("Frame")
runner:Hide()
local jobs = {}

local function finish(job, result)
  for i, j in ipairs(jobs) do if j == job then table.remove(jobs, i) break end end
  if #jobs == 0 then runner:Hide() end
  local ok, err = pcall(job.reply, result)
  if not ok then ns.lastError = "auction reply: " .. tostring(err) end
end

runner:SetScript("OnUpdate", function()
  local job = jobs[1]
  if not job then runner:Hide() return end
  job.slice = debugprofilestop()
  if job.waitUntil then
    -- item data on its way from the server: go on once it is all there or the wait is over (checked 5x a second)
    if GetTime() < (job.nextCheck or 0) then return end
    job.nextCheck = GetTime() + 0.2
    local cached = C_Item.IsItemDataCachedByID or function(id) return C_Item.GetItemInfo(id) ~= nil end
    local missing = 0
    for _, c in ipairs(job.candidates) do
      if not cached(c.id) then missing = missing + 1 end
    end
    if missing > 0 and GetTime() < job.waitUntil then return end
    job.waitUntil = nil
  end
  local ok, res = coroutine.resume(job.co)
  if not ok then
    finish(job, { error = "searching the price data failed (" .. tostring(res) .. "); if Auctionator is scanning, try again after the scan" })
  elseif coroutine.status(job.co) == "dead" then
    finish(job, res)
  end
end)

-- req: classIDs, subclassIDs, equipLocs, query, maxCopper, minCopper, maxAgeDays, usableOnly, upgradesOnly, sort, limit;
-- or items = { { id = 2589 }, { name = "Wool Cloth" }, ... } for the prices of exactly these items
function AH.Search(req, reply)
  local db, err, realm = priceTable()
  if not db then reply({ error = err }) return end
  local scanT = fullScanTime()
  local job = { req = req or {}, db = db, realm = realm, reply = reply, slice = debugprofilestop(),
    scanDay = scanT and scanDay(scanT), inScan = 0, before = 0 }
  if type(job.req.items) == "table" and #job.req.items > 0 then
    -- exact items: their prices, then their names (loaded from the server once, like a search's candidates)
    job.co = coroutine.create(function()
      exactScan(job)
      job.candidates = {}
      for _, e in ipairs(job.asked) do
        if e.id and not safe(C_Item.GetItemInfo, e.id) and C_Item.RequestLoadItemDataByID then
          pcall(C_Item.RequestLoadItemDataByID, e.id)
          job.candidates[#job.candidates + 1] = { id = e.id }
        end
      end
      if #job.candidates > 0 then
        job.waitUntil = GetTime() + LOAD_WAIT_S
        coroutine.yield()
      end
      return exactResult(job)
    end)
    jobs[#jobs + 1] = job
    runner:Show()
    return
  end
  job.co = coroutine.create(function()
    collect(job)
    -- the candidates the client knows nothing about yet are requested, then step 2 waits for them (once)
    local asked = 0
    local keep = {}
    for _, c in ipairs(job.candidates) do
      if #keep >= LOAD_MAX then break end
      keep[#keep + 1] = c
      if not C_Item.GetItemInfo(c.id) and C_Item.RequestLoadItemDataByID then
        pcall(C_Item.RequestLoadItemDataByID, c.id)
        asked = asked + 1
      end
    end
    job.candidates = keep
    if asked > 0 then
      job.waitUntil = GetTime() + LOAD_WAIT_S
      coroutine.yield()
    end
    return describe(job)
  end)
  jobs[#jobs + 1] = job
  runner:Show()
end

------------------------------------------------------------------------ show the recommended items in Auctionator
function AH.ShowInAuctionator(names)
  local A = auctionator()
  if not (A and A.API.v1.MultiSearchExact) then return false, "Auctionator is not available." end
  if not AH.IsOpen() then return false, "Open the auction house first." end
  local terms = {}
  for _, n in ipairs(names or {}) do
    n = tostring(n or ""):gsub("[\";^]", "")
    if n ~= "" then terms[#terms + 1] = n end
  end
  if #terms == 0 then return false, "Nothing to search." end
  local ok, e = pcall(A.API.v1.MultiSearchExact, CALLER, terms)
  if not ok then return false, tostring(e) end
  return true
end
