-- ForeverAI: the player's professions (known recipes, their difficulty, reagents), read while a profession window is
-- open and cached per character, because the window is closed when the player asks "what should I craft to get to
-- 150?" or "can I make anything worth selling?". Optional: what a profession trainer offers.
-- The player's filters and collapsed headers stay as they are (a collapsed header is opened and closed again in the
-- same frame); the reading is spread over frames, so the game never stalls on a profession with hundreds of recipes.
local ADDON, ns = ...
local PR = {}
ns.Professions = PR

local BUDGET_MS = 2       -- reading work per frame
local DEBOUNCE_S = 0.5    -- the window's update events come in bursts
local SELF_S = 0.5        -- update events caused by our own header expanding are not a reason to read again
local MAX_RECIPES = 400
local MAX_TRAINER = 80
local DIFFICULTY = { [0] = "optimal", [1] = "medium", [2] = "easy", [3] = "trivial" } -- Enum.TradeskillRelativeDifficulty
local KNOWN_DIFF = { optimal = true, medium = true, easy = true, trivial = true }

local safe = function(fn, ...) return ns.safe(fn, ...) end

local function store()
  local c = ns.char
  if not c then return nil end
  c.professions = type(c.professions) == "table" and c.professions or {}
  return c.professions
end

local function itemIdOf(link)
  return type(link) == "string" and tonumber(link:match("item:(%d+)")) or nil
end

------------------------------------------------------------------------ which API this client has
local function retailApi()
  local U = C_TradeSkillUI
  return U and U.GetAllRecipeIDs and U.GetRecipeInfo and U or nil
end

local function classicApi(kind)
  if kind == "craft" then
    if not (GetNumCrafts and GetCraftInfo) then return nil end
    return { kind = "craft", line = GetCraftDisplaySkillLine, num = GetNumCrafts,
      info = function(i) local name, _, kind2, avail, expanded = GetCraftInfo(i) return name, kind2, avail, expanded end,
      itemLink = GetCraftItemLink, numReagents = GetCraftNumReagents, reagent = GetCraftReagentInfo,
      reagentLink = GetCraftReagentItemLink, expand = ExpandCraftSkillLine, collapse = CollapseCraftSkillLine }
  end
  if not (GetNumTradeSkills and GetTradeSkillInfo) then return nil end
  return { kind = "tradeskill", line = GetTradeSkillLine, num = GetNumTradeSkills,
    info = function(i) local name, kind2, avail, expanded, _, skillUps = GetTradeSkillInfo(i) return name, kind2, avail, expanded, skillUps end,
    itemLink = GetTradeSkillItemLink, numReagents = GetTradeSkillNumReagents, reagent = GetTradeSkillReagentInfo,
    reagentLink = GetTradeSkillReagentItemLink, made = GetTradeSkillNumMade, expand = ExpandTradeSkillSubClass,
    collapse = CollapseTradeSkillSubClass, linked = IsTradeSkillLinked }
end

-- another player's profession (a link in chat) or a guild's: never stored as the player's own
local function foreign()
  local U = C_TradeSkillUI
  if U then
    for _, fn in ipairs({ "IsTradeSkillLinked", "IsTradeSkillGuild", "IsNPCCrafting", "IsRuneforging" }) do
      if U[fn] and safe(U[fn]) then return true end
    end
  end
  return (IsTradeSkillLinked and safe(IsTradeSkillLinked)) and true or false
end

------------------------------------------------------------------------ the reading job
local job, lastDone, selfUntil = nil, {}, 0 -- selfUntil: update events from our own header opening come until then
local runner = CreateFrame("Frame")
runner:Hide()

local function addRecipe(j, r)
  if #j.recipes + #j.gray >= MAX_RECIPES then j.capped = true return end
  if r.d == "trivial" then
    j.gray[#j.gray + 1] = r.n -- gray recipes give no skill-ups: their names are enough
  else
    j.recipes[#j.recipes + 1] = r
  end
end

-- retail: every recipe by id, learned or not, whatever the filters show
local function retailStep(j)
  local U = j.U
  while j.i <= #j.ids do
    if debugprofilestop() - j.slice > BUDGET_MS then return false end
    local id = j.ids[j.i]
    j.i = j.i + 1
    local info = safe(U.GetRecipeInfo, id)
    if type(info) == "table" and info.name and info.learned ~= false and not info.isDummyRecipe then
      local diff = DIFFICULTY[info.relativeDifficulty] or (KNOWN_DIFF[tostring(info.difficulty)] and info.difficulty) or nil
      -- WoW Forever leaves numAvailable empty ("can make" is counted from the bags later) but tells where a recipe
      -- turns gray (maxTrivialLevel)
      local r = { n = info.name, s = id, d = diff, c = info.numAvailable, g = tonumber(info.maxTrivialLevel),
                  u = (info.numSkillUps or 1) > 1 and info.numSkillUps or nil }
      if diff ~= "trivial" then
        local out = U.GetRecipeOutputItemData and safe(U.GetRecipeOutputItemData, id)
        r.i = type(out) == "table" and out.itemID or itemIdOf(info.hyperlink) or nil
        local sch = U.GetRecipeSchematic and safe(U.GetRecipeSchematic, id, false)
        if type(sch) == "table" then
          r.i = r.i or sch.outputItemID
          if (sch.quantityMax or 1) > 1 then r.m = { sch.quantityMin or 1, sch.quantityMax } end
          local reagents = {}
          for _, slot in ipairs(sch.reagentSlotSchematics or {}) do
            local basic = slot.reagentType == nil or (Enum and Enum.CraftingReagentType and slot.reagentType == Enum.CraftingReagentType.Basic)
            local first = slot.reagents and slot.reagents[1]
            if basic and slot.required ~= false and first and first.itemID then
              local name = safe(C_Item.GetItemNameByID, first.itemID) or safe(C_Item.GetItemInfo, first.itemID)
              reagents[#reagents + 1] = { n = name, i = first.itemID, q = slot.quantityRequired or 1 }
            end
          end
          if #reagents > 0 then r.r = reagents end
        end
      end
      addRecipe(j, r)
    elseif type(info) == "table" and info.name and info.learned == false then
      j.unlearned = j.unlearned + 1
    end
  end
  return true
end

local function classicRecipe(j, i)
  local A = j.A
  local name, kind, avail, _, skillUps = A.info(i)
  if not name or kind == "header" or kind == "subheader" then return end
  local r = { n = name, d = KNOWN_DIFF[kind] and kind or nil, c = avail, u = (skillUps or 1) > 1 and skillUps or nil }
  if r.d ~= "trivial" then
    r.i = A.itemLink and itemIdOf(safe(A.itemLink, i))
    if A.made then
      local lo, hi = safe(A.made, i)
      if (hi or 1) > 1 then r.m = { lo or 1, hi } end
    end
    local reagents = {}
    for k = 1, math.min(8, (A.numReagents and safe(A.numReagents, i)) or 0) do
      local rn, _, need, have = safe(A.reagent, i, k)
      if rn then
        reagents[#reagents + 1] = { n = rn, i = A.reagentLink and itemIdOf(safe(A.reagentLink, i, k)), q = need or 1, h = have }
      end
    end
    if #reagents > 0 then r.r = reagents end
  end
  addRecipe(j, r)
end

-- classic: the visible list; a collapsed header is opened, read and closed again within the same frame (a long one
-- over several frames, a part in each: the player never sees it open)
local function classicStep(j)
  local A = j.A
  if (safe(A.num) or 0) ~= j.n then j.changed = true return true end -- the player changed the list: read again later
  while j.i <= j.n do
    if debugprofilestop() - j.slice > BUDGET_MS then return false end
    local i = j.i
    local name, kind, _, expanded = A.info(i)
    if (kind == "header" or kind == "subheader") and not expanded and A.expand and A.collapse then
      selfUntil = GetTime() + SELF_S
      local okE = pcall(A.expand, i)
      local count, k = (safe(A.num) or j.n) - j.n, j.hdrDone or 0
      if okE and count > k then
        repeat
          k = k + 1
          classicRecipe(j, i + k)
        until k >= count or debugprofilestop() - j.slice > BUDGET_MS
      end
      pcall(A.collapse, i)
      if (safe(A.num) or 0) ~= j.n then j.broken = "a collapsed header did not close again" return true end
      if okE and k < count then
        j.hdrDone = k -- the rest of this header in the next frame
        return false
      end
      j.hdrDone, j.i = nil, i + 1
      if okE then j.expanded = j.expanded + 1 end
    else
      j.i = i + 1
      if name then classicRecipe(j, i) end
    end
  end
  return true
end

-- filters hide recipes from the classic list; they are left as they are and the reading only adds to the cache
local function classicFiltered(A)
  if A.kind ~= "tradeskill" then return nil end
  if GetTradeSkillSubClassFilter and safe(GetTradeSkillSubClassFilter, 0) == false then return "a category filter" end
  if GetTradeSkillInvSlotFilter and safe(GetTradeSkillInvSlotFilter, 0) == false then return "a slot filter" end
  local box = TradeSkillFrameAvailableFilterCheckButton
  if box and box.GetChecked and box:GetChecked() then return "\"Have materials\"" end
  local search = GetTradeSkillItemNameFilter and safe(GetTradeSkillItemNameFilter)
  if type(search) == "string" and search ~= "" then return "a search text" end
  return nil
end

local function finish(j)
  job = nil
  runner:Hide()
  if j.broken or j.changed then
    j.retries = (j.retries or 0) + 1
    if j.retries <= 3 then C_Timer.After(1, function() PR.Read(j.source, j.retries) end) end
    PR.lastProblem = j.broken or "the list changed while it was read"
    return
  end
  local db = store()
  if not (db and j.name) then return end
  local entry = { name = j.name, rank = j.rank, maxRank = j.maxRank, at = time(), api = j.api, recipes = j.recipes,
                  gray = j.gray, unlearned = j.unlearned > 0 and j.unlearned or nil, capped = j.capped or nil }
  local old = db[j.name]
  if j.partial and type(old) == "table" then
    -- filters hid part of the list: what was read replaces its old entry, the rest of the old cache stays
    local seen = {}
    for _, r in ipairs(j.recipes) do seen[r.n] = true end
    for _, n in ipairs(j.gray) do seen[n] = true end
    for _, r in ipairs(old.recipes or {}) do if not seen[r.n] then entry.recipes[#entry.recipes + 1] = r end end
    for _, n in ipairs(old.gray or {}) do if not seen[n] then entry.gray[#entry.gray + 1] = n end end
    entry.partial = j.partial
  end
  db[j.name] = entry
  lastDone[j.name] = { at = GetTime(), ms = j.ms, frames = j.frames, expanded = j.expanded }
  PR.lastRead = lastDone[j.name]
  if j.again then C_Timer.After(DEBOUNCE_S, function() PR.Read(j.source) end) end -- it changed while it was read
end

runner:SetScript("OnUpdate", function()
  local j = job
  if not j then runner:Hide() return end
  j.frames = j.frames + 1
  j.slice = debugprofilestop()
  local ok, done = pcall(j.step, j)
  j.ms = j.ms + (debugprofilestop() - j.slice)
  if not ok then
    job = nil
    runner:Hide()
    PR.lastProblem = tostring(done)
    ns.lastError = "professions: " .. tostring(done)
  elseif done then
    finish(j)
  end
end)

-- start reading the open profession window (source: "tradeskill" or "craft")
function PR.Read(source, retries)
  if job then job.again = true return end
  if foreign() then return end
  local j = { source = source or "tradeskill", recipes = {}, gray = {}, unlearned = 0, i = 1, frames = 0, ms = 0,
              expanded = 0, retries = retries }
  local U = j.source == "tradeskill" and retailApi()
  if U then
    if U.IsTradeSkillReady and not safe(U.IsTradeSkillReady) then return end
    local base = U.GetChildProfessionInfo and safe(U.GetChildProfessionInfo)
    if type(base) ~= "table" or not base.professionName or (base.maxSkillLevel or 0) == 0 then
      base = U.GetBaseProfessionInfo and safe(U.GetBaseProfessionInfo)
    end
    local ids = safe(U.GetAllRecipeIDs)
    if type(base) ~= "table" or type(ids) ~= "table" then return end
    j.name = base.parentProfessionName or base.professionName -- "Tailoring", not an expansion's tier of it
    j.rank, j.maxRank = base.skillLevel, base.maxSkillLevel
    j.U, j.ids, j.api, j.step = U, ids, "C_TradeSkillUI", retailStep
  else
    local A = classicApi(j.source)
    if not A then return end
    local name, rank, maxRank = safe(A.line)
    if not name or name == "" or name == "UNKNOWN" then return end
    j.name, j.rank, j.maxRank = name, rank, maxRank
    j.A, j.n, j.api, j.step = A, safe(A.num) or 0, A.kind == "craft" and "GetCraftInfo" or "GetTradeSkillInfo", classicStep
    j.partial = classicFiltered(A)
  end
  if not j.name then return end
  job = j
  runner:Show()
end

------------------------------------------------------------------------ trainers (optional)
local function readTrainer()
  if not (GetNumTrainerServices and GetTrainerServiceInfo) then return end
  local db = store()
  if not db then return end
  local byProf = {}
  for i = 1, math.min(MAX_TRAINER, safe(GetNumTrainerServices) or 0) do
    local name, sub, kind = safe(GetTrainerServiceInfo, i)
    if name and kind ~= "header" and kind ~= "used" then
      local skill, level = nil, nil
      if GetTrainerServiceSkillReq then skill, level = safe(GetTrainerServiceSkillReq, i) end
      if skill and level and db[skill] then
        local cost = safe(GetTrainerServiceCost, i)
        byProf[skill] = byProf[skill] or {}
        table.insert(byProf[skill], { n = name, rank = (sub and sub ~= "") and sub or nil, cost = cost, skill = level,
          lvl = GetTrainerServiceLevelReq and safe(GetTrainerServiceLevelReq, i) or nil, ok = kind == "available" })
      end
    end
  end
  for prof, list in pairs(byProf) do
    db[prof].trainer = { at = time(), npc = safe(UnitName, "npc"), offers = list }
  end
end

------------------------------------------------------------------------ events
local pending = {}
local function soon(source)
  if GetTime() < selfUntil then return end
  if pending[source] then return end
  pending[source] = true
  C_Timer.After(DEBOUNCE_S, function()
    pending[source] = nil
    PR.Read(source)
  end)
end

local handlers = {
  TRADE_SKILL_SHOW = function() soon("tradeskill") end,
  TRADE_SKILL_UPDATE = function() soon("tradeskill") end,
  TRADE_SKILL_LIST_UPDATE = function() soon("tradeskill") end,
  TRADE_SKILL_DATA_SOURCE_CHANGED = function() soon("tradeskill") end,
  NEW_RECIPE_LEARNED = function() soon("tradeskill") end,
  CRAFT_SHOW = function() soon("craft") end,
  CRAFT_UPDATE = function() soon("craft") end,
  TRADE_SKILL_CLOSE = function() if job and job.source == "tradeskill" then job = nil runner:Hide() end end,
  CRAFT_CLOSE = function() if job and job.source == "craft" then job = nil runner:Hide() end end,
  TRAINER_SHOW = function() C_Timer.After(0.3, function() pcall(readTrainer) end) end,
}
local f = CreateFrame("Frame")
for event in pairs(handlers) do pcall(f.RegisterEvent, f, event) end
f:SetScript("OnEvent", function(_, event, ...)
  local h = handlers[event]
  if h then pcall(h, ...) end
end)
PR.handlers = handlers

------------------------------------------------------------------------ what the AI gets
-- bags (and bank) are counted now, not when the window was read: "can make now" follows what the player carries
local function count(id, bank)
  if not id then return nil end
  local fn = (C_Item and C_Item.GetItemCount) or GetItemCount
  return fn and safe(fn, id, bank or nil) or nil
end

function PR.ItemId(name)
  local db = ns.char and ns.char.professions
  if type(db) ~= "table" or type(name) ~= "string" then return nil end
  local low = name:lower()
  for _, p in pairs(db) do
    for _, r in ipairs(type(p) == "table" and p.recipes or {}) do
      if r.i and r.n and r.n:lower() == low then return r.i end
      for _, g in ipairs(r.r or {}) do
        if g.i and g.n and g.n:lower() == low then return g.i end
      end
    end
  end
  return nil
end

function PR.Section()
  local db = ns.char and ns.char.professions
  if type(db) ~= "table" or not next(db) then return nil end
  local now, out = time(), {}
  local budget = ns.Collect and ns.Collect.Budget or function() end
  for name, p in pairs(db) do
    if type(p) == "table" then
      local e = { rank = p.rank, maxRank = p.maxRank, readAgo = now - (p.at or now), partial = p.partial, capped = p.capped,
                  unlearned = p.unlearned, gray = (#(p.gray or {}) > 0) and p.gray or nil }
      local recipes = {}
      for _, r in ipairs(p.recipes or {}) do
        budget()
        local x = { n = r.n, i = r.i, d = r.d, u = r.u, m = r.m, g = r.g }
        if r.r then
          local can, reagents = nil, {}
          for _, g in ipairs(r.r) do
            local have = count(g.i) or g.h or 0
            local bank = count(g.i, true)
            if not g.n and g.i then -- the item was not in the game's cache when the window was read
              g.n = safe(C_Item.GetItemNameByID, g.i) or safe(C_Item.GetItemInfo, g.i)
              if not g.n and C_Item.RequestLoadItemDataByID then pcall(C_Item.RequestLoadItemDataByID, g.i) end
            end
            reagents[#reagents + 1] = { n = g.n, i = g.i, q = g.q, h = have, b = (bank and bank > have) and (bank - have) or nil }
            local n = math.floor(have / math.max(1, g.q or 1))
            can = can and math.min(can, n) or n
          end
          x.r, x.c = reagents, can
        else
          x.c = r.c
        end
        recipes[#recipes + 1] = x
      end
      e.recipes = #recipes > 0 and recipes or nil
      if p.trainer then
        e.trainer = { npc = p.trainer.npc, readAgo = now - (p.trainer.at or now), offers = p.trainer.offers }
      end
      out[name] = e
    end
  end
  return next(out) and out or nil
end

------------------------------------------------------------------------ /fai professions
function PR.Debug()
  local U = retailApi()
  ns.print(string.format("profession API: C_TradeSkillUI %s, GetTradeSkillInfo %s, GetCraftInfo %s, trainer %s",
    U and "yes" or "no", GetTradeSkillInfo and "yes" or "no", GetCraftInfo and "yes" or "no",
    GetTrainerServiceInfo and "yes" or "no"))
  local db = ns.char and ns.char.professions
  if type(db) ~= "table" or not next(db) then
    ns.print("no profession read yet: open a profession window once")
  else
    for name, p in pairs(db) do
      local colors = {}
      for _, r in ipairs(p.recipes or {}) do colors[r.d or "?"] = (colors[r.d or "?"] or 0) + 1 end
      local parts = {}
      for _, d in ipairs({ "optimal", "medium", "easy", "?" }) do if colors[d] then parts[#parts + 1] = d .. " " .. colors[d] end end
      ns.print(string.format("%s %s/%s: %d recipes (%s), %d gray%s%s; read %s ago with %s%s", name, tostring(p.rank),
        tostring(p.maxRank), #(p.recipes or {}), table.concat(parts, ", "), #(p.gray or {}),
        p.unlearned and (", " .. p.unlearned .. " not learned") or "", p.partial and (", partly (" .. p.partial .. ")") or "",
        math.floor((time() - (p.at or time())) / 60) .. " min", tostring(p.api),
        p.trainer and (", trainer " .. #p.trainer.offers .. " offers") or ""))
    end
  end
  local r = PR.lastRead
  if r then ns.print(string.format("last read: %.1f ms over %d frames, %d collapsed headers opened and closed", r.ms, r.frames, r.expanded)) end
  if PR.lastProblem then ns.print("last problem: " .. PR.lastProblem) end
end

if ns.Collect and ns.Collect.sections then
  ns.Collect.sections.professions = PR.Section
end
