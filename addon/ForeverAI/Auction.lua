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

local function lastScan()
  local sv = type(AUCTIONATOR_SAVEDVARS) == "table" and AUCTIONATOR_SAVEDVARS or {}
  local best, kind = 0, nil
  for key, name in pairs({ TimeOfLastReplicateScan = "full scan", TimeOfLastGetAllScan = "full scan",
                           TimeOfBrowseScan = "browse scan" }) do
    local t = tonumber(sv[key])
    if t and t > best then best, kind = t, name end
  end
  if best == 0 then return nil end
  return ago(time() - best) .. " (" .. kind .. ")"
end

-- newest day of an entry and its count of listings on that day
local function freshness(entry, today)
  local newest
  for day in pairs(type(entry.h) == "table" and entry.h or {}) do
    local d = tonumber(day)
    if d and (not newest or d > newest) then newest = d end
  end
  if not newest then return nil end
  local avail = type(entry.a) == "table" and tonumber(entry.a[tostring(newest)]) or nil
  return today - newest, avail
end

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
    if id and price and price > 0 and (not maxC or price <= maxC) and price >= minC then
      local age, avail = freshness(entry, today)
      if age and age <= maxAge then
        local _, _, _, equipLoc, _, classID, subclassID = C_Item.GetItemInfoInstant(id)
        if classID and (not classes or classes[classID]) and (not subclasses or subclasses[subclassID])
          and (not locs or locs[equipLoc]) then
          local have = byId[id]
          if not have or price < have.copper then
            byId[id] = { id = id, copper = price, age = age, available = avail, equipLoc = equipLoc, classID = classID }
          end
        end
      end
    end
  end
  job.dbItems = n
  local list = {}
  for _, c in pairs(byId) do list[#list + 1] = c end
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
  local out, skipped, notLoaded = {}, { ["not usable"] = 0, ["too high level"] = 0, ["not an upgrade"] = 0 }, 0
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
          seen = c.age == 0 and "today" or (c.age == 1 and "yesterday" or (c.age .. " days ago")), available = c.available,
          quality = facts and facts.quality, ilvl = ilvl, requiresLevel = reqLevel and reqLevel > 1 and reqLevel or nil,
          slot = (c.equipLoc and c.equipLoc ~= "" and _G[c.equipLoc]) or nil, type = subType,
          stats = facts and facts.stats, vsEquipped = facts and facts.vsEquipped, cannotUse = facts and facts.cannotUse,
          tooLowLevel = facts and facts.tooLowLevel and ("requires level " .. tostring(reqLevel)) or nil }
        if c.classID == 2 or c.classID == 4 then
          local pawn = pawnUpgrade(link)
          if pawn then
            e.pawn, e.score = pawn.text, pawn.pct * 1000
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
    return a.copper < b.copper
  end)
  local limit = math.min(30, tonumber(req.limit) or 15)
  local items = {}
  for i = 1, math.min(limit, #out) do
    local e = out[i]
    e.score = nil
    items[i] = e
  end
  return { source = "Auctionator", realm = job.realm, lastScan = lastScan(), dbItems = job.dbItems,
    matched = #out, items = items, notLoaded = notLoaded > 0 and notLoaded or nil, skipped = skipped,
    upgradeMethod = method, sort = req.sort or "upgrade", ahOpen = AH.IsOpen() }
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

-- req: classIDs, subclassIDs, equipLocs, query, maxCopper, minCopper, maxAgeDays, usableOnly, upgradesOnly, sort, limit
function AH.Search(req, reply)
  local db, err, realm = priceTable()
  if not db then reply({ error = err }) return end
  local job = { req = req or {}, db = db, realm = realm, reply = reply, slice = debugprofilestop() }
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
