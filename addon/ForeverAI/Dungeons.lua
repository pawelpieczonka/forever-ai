-- ForeverAI: dungeons for the AI (dungeon_plan), from the DungeonJournal add-on (ForeverDungeonJournal) when it is
-- installed: its dungeons with their levels, entrances, quests and bosses, read and never changed; and what of that is
-- open to this character, from the quest log and Questie (done, in the log, the level, the quests before it). A long
-- route plans a dungeon run with it: the dungeon's quests picked up on the way, the entrance, the turn-ins after.
local ADDON, ns = ...
local D = {}
ns.Dungeons = D
local safe = ns.safe

local MAX_QUESTS, MAX_BOSSES = 14, 12

local function journal()
  local fdj = rawget(_G, "ForeverDungeonJournal_NS")
  return type(fdj) == "table" and type(fdj.DB) == "table" and fdj or nil
end

local function key(name) return (tostring(name or ""):lower():gsub("^the ", ""):gsub("[^%w]", "")) end

-- the dungeon of this name: exact first, then one whose name starts with it, then one that holds it ("gnome")
local function find(db, name)
  local want = key(name)
  if want == "" then return nil end
  local starts, holds
  for n, d in pairs(db) do
    if type(d) == "table" and type(d.quests) == "table" then
      local k = key(n)
      if k == want then return n, d end
      if k:find(want, 1, true) == 1 then starts = starts or n end
      if k:find(want, 1, true) then holds = holds or n end
    end
  end
  local n = starts or holds
  return n, n and db[n]
end

local function levels(d)
  local lo, hi = tostring(d.level or ""):match("(%d+)%s*%-%s*(%d+)")
  return tonumber(lo), tonumber(hi)
end

local function entrance(d)
  local e = type(d.entrance) == "table" and d.entrance
  if not (e and tonumber(e.mapID) and tonumber(e.x)) then return nil end
  local info = C_Map.GetMapInfo and safe(C_Map.GetMapInfo, e.mapID)
  return { zone = type(info) == "table" and info.name or nil, x = math.floor(e.x * 1000 + 0.5) / 10,
           y = math.floor(e.y * 1000 + 0.5) / 10, detail = e.detail }
end

-- is this quest of the journal one for this character: its faction, its class, not one the journal hides (an
-- optional step of a chain), not one for other races ("Night Elf" or "NightElf")
local function forMe(q, faction, class, races)
  if type(q) ~= "table" or not q.id or not q.name or q.unavailable or q.listHidden or q.hideFromMainList then return false end
  local f = q.faction or q.factionOnly
  if f and f ~= "Both" and f ~= faction then return false end
  if type(q.classOnly) == "string" and class and q.classOnly:lower() ~= class:lower() then return false end
  for _, r in ipairs(type(q.unavailableRaces) == "table" and q.unavailableRaces or {}) do
    local want = tostring(r):lower():gsub("[^%a]", "")
    for _, mine in ipairs(races) do
      if want == tostring(mine):lower():gsub("[^%a]", "") then return false end
    end
  end
  return true
end

local function says(text, what) return type(text) == "string" and text:lower():find(what, 1, true) ~= nil end

local function itemLine(it)
  if type(it) ~= "table" or not it[2] then return nil end
  local n = tonumber(it[3]) or 1
  return (n > 1 and (n .. " ") or "") .. tostring(it[2]) .. (type(it[4]) == "string" and (" (" .. it[4] .. ")") or "")
end

-- Who gives the quest and where, as zone_quests says it (giver: the NPC's name; where: "Zone x, y"), so that a route
-- can stop there: Questie's data, else the journal's own place of the quest's start (its startMap, or the table of
-- start places), else its words alone (no stop).
local function giverOf(fdj, QDB, q)
  local list = QDB and ns.Collect.QuestieGivers(QDB, safe(QDB.QueryQuestSingle, q.id, "startedBy"))
  local name, where
  if type(list) == "string" then name, where = list:match("^([^;(]-) %(([^()]- %d+%.?%d*, %d+%.?%d*)%)") end
  if name then return name, where end
  local m = (type(q.startMap) == "table" and q.startMap) or (type(fdj.QUEST_START_MAPS) == "table" and fdj.QUEST_START_MAPS[q.id])
  if type(m) == "table" and tonumber(m.mapID) and tonumber(m.x) and tonumber(m.y) then
    local info = C_Map.GetMapInfo and safe(C_Map.GetMapInfo, m.mapID)
    local who = m.targetName or (type(m.label) == "string" and (m.label:match("^(.-)%s+\226\128\148") or m.label))
    if type(info) == "table" and info.name and who then
      return who, string.format("%s %.1f, %.1f", info.name, m.x * 100, m.y * 100)
    end
  end
  return (type(q.pickup) == "string" and q.pickup) or (type(list) == "string" and list) or nil, nil
end

local function isDone(id) return C_QuestLog.IsQuestFlaggedCompleted and safe(C_QuestLog.IsQuestFlaggedCompleted, id) and true or false end
local function inLog(id) return C_QuestLog.GetLogIndexForQuestID and safe(C_QuestLog.GetLogIndexForQuestID, id) and true or false end

-- one quest of the dungeon as the AI gets it, with its state for this character
local function questOf(fdj, QDB, q, name, myLevel)
  local C = ns.Collect
  local e = { id = q.id, title = q.name, level = tonumber(q.level), minLevel = tonumber(q.requires),
              objective = q.objective, xp = (C.QuestXP and C.QuestXP(q.id)) or tonumber(q.liveXPFallback) }
  local finishedBy = QDB and safe(QDB.QueryQuestSingle, q.id, "finishedBy")
  e.giver, e.where = giverOf(fdj, QDB, q)
  e.turnIn = (finishedBy and C.QuestieGivers(QDB, finishedBy)) or q.turnin
  -- given or turned in inside the dungeon (by somebody there, or an item that drops there): no stop outside for it
  if q.startsInDungeon or q.startItem or says(q.pickup, "inside") then e.givenInside = true end
  if says(q.turnin, "inside") then e.turnedInside = true end
  local bring = {}
  for _, it in ipairs(type(q.requiredItems) == "table" and q.requiredItems or { q.requiredItem }) do
    bring[#bring + 1] = itemLine(it)
  end
  if #bring > 0 then e.bring = table.concat(bring, "; ") end
  e.provided = itemLine(q.providedItem)
  e.warning = type(q.warning) == "string" and q.warning or nil
  e.note = type(q.note) == "string" and ns.Cut(q.note, 240) or nil
  if q.repeatable then e.repeatable = true end
  -- what the route needs: its objectives are inside the dungeon, so its turn-in waits until the quest log says done
  e.whereToGet = { ns.Cut(tostring(q.objective or "what it asks for"), 160) .. ": inside " .. name .. " (no place on a zone map)" }
  if isDone(q.id) then
    e.state = "done"
  elseif inLog(q.id) then
    e.state = "in the quest log"
  else
    local needs = {}
    local chain = type(fdj.QUEST_PREREQ_CHAINS) == "table" and fdj.QUEST_PREREQ_CHAINS[q.id] or nil
    for _, step in ipairs(type(chain) == "table" and chain or {}) do
      if type(step) == "table" and step.id and not isDone(step.id) then
        needs[#needs + 1] = "[" .. tostring(step.name or C.QuestName(QDB, step.id)) .. "]" .. (inLog(step.id) and " (in the quest log)" or "")
      end
    end
    if #needs == 0 and QDB and C.StillNeeds then -- (the journal knows no chain: Questie's own data)
      local more, shut = C.StillNeeds(QDB, q.id)
      if shut then e.blocked = shut end
      for i, pid in ipairs(more or {}) do
        if i <= 4 then needs[#needs + 1] = "[" .. C.QuestName(QDB, pid) .. "]" .. (inLog(pid) and " (in the quest log)" or "") end
      end
    end
    if #needs > 0 then e.needs = needs end
    if e.blocked then
      e.state = "not for this character"
    elseif e.minLevel and e.minLevel > myLevel then
      e.state = "opens at level " .. e.minLevel
    elseif #needs > 0 then
      e.state = "needs other quests first"
    else
      e.state = "can be taken now"
    end
  end
  return e
end

-- The dungeon of this name: its levels, where it is, its entrance, its quests for this character and its bosses. With
-- no name: the dungeons for about the character's level, each with how many of its quests are open now.
function D.Plan(name)
  local fdj = journal()
  if not fdj then
    return { error = "the DungeonJournal add-on (ForeverDungeonJournal) is not installed or not loaded" }
  end
  local QDB = ns.Collect.QuestieDB and ns.Collect.QuestieDB()
  local myLevel = UnitLevel("player") or 1
  local faction = UnitFactionGroup and safe(UnitFactionGroup, "player") or nil
  local _, classFile = UnitClass("player")
  local races = { safe(UnitRace, "player") } -- (its name and its file name)
  if not name or tostring(name):gsub("%s", "") == "" then
    local out = {}
    for n, d in pairs(fdj.DB) do
      local lo, hi = levels(type(d) == "table" and d or {})
      if lo and hi and lo <= myLevel + 4 and hi >= myLevel - 2 and type(d.quests) == "table" then
        local open, all = 0, 0
        for _, q in ipairs(d.quests) do
          if forMe(q, faction, classFile, races) then
            all = all + 1
            if not isDone(q.id) and (tonumber(q.requires) or 0) <= myLevel then open = open + 1 end
          end
        end
        local e = entrance(d)
        out[#out + 1] = { name = n, level = d.level, location = d.location, quests = all, open = open,
                          entrance = e and string.format("%s %.1f, %.1f", e.zone or "?", e.x, e.y) or nil }
      end
    end
    table.sort(out, function(a, b) return (tonumber(tostring(a.level):match("%d+")) or 0) < (tonumber(tostring(b.level):match("%d+")) or 0) end)
    return { dungeons = out, level = myLevel }
  end
  local n, d = find(fdj.DB, name)
  if not d then return { error = "the DungeonJournal has no dungeon called " .. tostring(name) } end
  local lo, hi = levels(d)
  local out = { name = n, level = d.level, min = lo, max = hi, location = d.location, about = d.description,
                entrance = entrance(d), quests = {}, bosses = {}, character = myLevel }
  for _, q in ipairs(d.quests) do
    if forMe(q, faction, classFile, races) and #out.quests < MAX_QUESTS then
      out.quests[#out.quests + 1] = questOf(fdj, QDB, q, n, myLevel)
    end
  end
  for _, b in ipairs(type(d.bosses) == "table" and d.bosses or {}) do
    if type(b) == "table" and b.name and #out.bosses < MAX_BOSSES then
      out.bosses[#out.bosses + 1] = { name = b.name, about = type(b.description) == "string" and ns.Cut(b.description, 160) or nil,
                                      rare = (b.rare or b.optional) and true or nil }
    end
  end
  return out
end
