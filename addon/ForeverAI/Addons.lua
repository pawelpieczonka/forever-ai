-- ForeverAI: installed addons, settings dumps and approved changes (set_setting / toggle_addon / slash_command)
local ADDON, ns = ...
local A = {}
ns.Addons = A

local function isOwnHelper(name)
  return name:find("^ForeverAI_Inbox") or name == "ForeverAI_Data" or name == "ForeverAI_Probe"
end

function A.List()
  local out = {}
  for i = 1, C_AddOns.GetNumAddOns() do
    local name, title, notes, loadable, reason = C_AddOns.GetAddOnInfo(i)
    if name and not isOwnHelper(name) then
      local enabled = C_AddOns.GetAddOnEnableState(name, UnitName("player")) > 0
      out[#out + 1] = {
        name = name, title = ns.stripCodes(title), version = C_AddOns.GetAddOnMetadata(i, "Version"),
        loaded = C_AddOns.IsAddOnLoaded(i) or nil, enabled = enabled or nil,
        loadOnDemand = C_AddOns.IsAddOnLoadOnDemand(i) or nil,
      }
    end
  end
  return out
end

------------------------------------------------------------------------ settings dump
local MAX_ENTRIES, MAX_DEPTH = 500, 5

local function dumpValue(v, depth, budget)
  local t = type(v)
  if t == "boolean" or t == "number" then return v end
  if t == "string" then return ns.Cut(v, 1000) end
  if t ~= "table" or depth > MAX_DEPTH then return nil end
  local out, keys = {}, {}
  for k in pairs(v) do
    if type(k) == "string" or type(k) == "number" then keys[#keys + 1] = k end
  end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  for _, k in ipairs(keys) do
    if budget.n <= 0 then break end
    local child = dumpValue(v[k], depth + 1, budget)
    if child ~= nil then
      budget.n = budget.n - 1
      out[tostring(k)] = child
    end
  end
  return next(out) and out or nil
end

-- candidate saved-variable globals of an addon: names sent by the companion (parsed from TOC) + heuristics
local function svNames(addon)
  local names, seen = {}, {}
  local known = ns.db.addonSV and ns.db.addonSV[addon]
  for _, n in ipairs(known or {}) do
    if not seen[n] then seen[n] = true names[#names + 1] = n end
  end
  if #names == 0 then
    local lower = addon:lower()
    for k, v in pairs(_G) do
      if type(k) == "string" and type(v) == "table" and k:lower():find("^" .. lower) and
        (k:find("DB") or k:find("Config") or k:find("Settings") or k:find("Options") or k:find("Saved") or k:find("Vars")) then
        if not seen[k] then seen[k] = true names[#names + 1] = k end
      end
    end
  end
  return names
end

local function charProfileKey()
  return (UnitName("player") or "?") .. " - " .. (GetRealmName() or "?")
end

-- AceDB keeps only non-default values in SavedVariables; defaults live on the db object
local function aceDefaults(sv)
  if not LibStub then return nil end
  local AceDB = LibStub("AceDB-3.0", true)
  if not AceDB or not AceDB.db_registry then return nil end
  for db in pairs(AceDB.db_registry) do
    if db.sv == sv then return db.defaults and db.defaults.profile end
  end
end

local function dumpMerged(raw, defaults, depth, budget)
  if type(defaults) ~= "table" then return dumpValue(raw, depth, budget) end
  if depth > MAX_DEPTH then return nil end
  local keys, seen = {}, {}
  for _, src in ipairs({ raw or {}, defaults }) do
    for k in pairs(src) do
      if (type(k) == "string" or type(k) == "number") and not seen[k] then seen[k] = true keys[#keys + 1] = k end
    end
  end
  table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
  local out = {}
  for _, k in ipairs(keys) do
    if budget.n <= 0 then break end
    local rv = raw and rawget(raw, k)
    local dv = defaults[k]
    local child
    if type(rv) == "table" or type(dv) == "table" then
      child = dumpMerged(type(rv) == "table" and rv or nil, type(dv) == "table" and dv or nil, depth + 1, budget)
    else
      local v = rv
      if v == nil then v = dv end
      child = dumpValue(v, depth + 1, budget)
    end
    if child ~= nil then
      budget.n = budget.n - 1
      out[tostring(k)] = child
    end
  end
  return next(out) and out or nil
end

function A.DumpSettings(addon)
  local vars = {}
  local budget = { n = MAX_ENTRIES }
  for _, name in ipairs(svNames(addon)) do
    local v = _G[name]
    if type(v) == "table" then
      if type(v.profiles) == "table" and type(v.profileKeys) == "table" then
        local profile = v.profileKeys[charProfileKey()] or "Default"
        local dumped = dumpMerged(v.profiles[profile], aceDefaults(v), 2, budget)
        vars[name] = { profiles = { [profile] = dumped or {} }, ["(active profile)"] = profile }
      else
        vars[name] = dumpValue(v, 1, budget)
      end
    elseif v ~= nil then
      vars[name] = dumpValue(v, 1, budget)
    end
  end
  if not next(vars) then return nil end
  return { addon = addon, vars = vars, note = "Paths: variable name and keys separated by dots." }
end

function A.SettingsForQuestion(question)
  local q = question:lower()
  for i = 1, C_AddOns.GetNumAddOns() do
    local name, title = C_AddOns.GetAddOnInfo(i)
    if name and not isOwnHelper(name) and C_AddOns.IsAddOnLoaded(i) then
      local plain = ns.stripCodes(title or ""):lower()
      local n = name:lower()
      if (#n >= 4 and q:find(n, 1, true)) or (#plain >= 4 and q:find(plain, 1, true)) then
        return A.DumpSettings(name)
      end
    end
  end
  return nil
end

------------------------------------------------------------------------ real options (AceConfig) — search, read, set
-- Most big addons (Questie, Details, DBM, Bartender...) describe their settings as an AceConfig options tree: every
-- option has a name, a description, a type, allowed values and its own get/set. Using that tree is exactly what the
-- addon's options window does, so a change made this way takes effect immediately.
local INHERIT = { "get", "set", "handler", "disabled", "hidden" }
local MAX_NODES = 4000

local function registry()
  return LibStub and LibStub("AceConfigRegistry-3.0", true)
end

local function appsFor(addon)
  local reg, out = registry(), {}
  if not reg or not reg.tables then return out end
  local a = (addon or ""):lower()
  for app in pairs(reg.tables) do
    local l = tostring(app):lower()
    if a == "" or l == a or l:find(a, 1, true) or a:find(l, 1, true) then out[#out + 1] = app end
  end
  table.sort(out)
  return out
end

local function rootOf(app)
  local reg = registry()
  local t = reg and reg.tables and reg.tables[app]
  if type(t) == "function" then
    local ok, res = pcall(t, "dialog", "ForeverAI-1.0")
    t = ok and res or nil
  end
  return type(t) == "table" and t or nil
end

local function makeInfo(root, app, keys, opt, inh)
  local info = { options = root, appName = app, option = opt, type = opt.type, handler = inh.handler,
                 arg = opt.arg, uiType = "dialog", uiName = "ForeverAI-1.0" }
  info[0] = ""
  for i, k in ipairs(keys) do info[i] = k end
  return info
end

local function call(fn, inh, info, ...)
  if type(fn) == "function" then return pcall(fn, info, ...) end
  if type(fn) == "string" and type(inh.handler) == "table" and type(inh.handler[fn]) == "function" then
    return pcall(inh.handler[fn], inh.handler, info, ...)
  end
  return false, "no function"
end

local function evalField(v, info, inh)
  if type(v) == "function" or (type(v) == "string" and inh and type(inh.handler) == "table"
      and type(inh.handler[v]) == "function") then
    local ok, res = call(v, inh, info)
    return ok and res or nil
  end
  return v
end

local function text(v) return (ns.stripCodes(tostring(v or "")):gsub("\n+", " ")) end

local LEAF = { toggle = true, select = true, multiselect = true, range = true, input = true, keybinding = true,
               color = false, execute = true }

-- describe one option: name, description, type, current value, allowed values
local function describe(root, app, keys, opt, inh)
  local info = makeInfo(root, app, keys, opt, inh)
  local d = { app = app, path = table.concat(keys, "."), type = opt.type,
              name = text(evalField(opt.name, info, inh)), desc = ns.Cut(text(evalField(opt.desc, info, inh)), 2000) }
  if d.desc == "" then d.desc = nil end
  if opt.type ~= "execute" and opt.type ~= "multiselect" then
    local ok, v = call(inh.get, inh, info)
    if ok and (type(v) == "boolean" or type(v) == "number" or type(v) == "string") then d.value = v end
  end
  if opt.type == "select" or opt.type == "multiselect" then
    local values = evalField(opt.values, info, inh)
    if type(values) == "table" then
      d.values = {}
      local n = 0
      for k, label in pairs(values) do
        n = n + 1
        if n > 60 then break end
        d.values[tostring(k)] = text(label)
      end
      if opt.type == "multiselect" then
        -- current state of every choice: { key = true/false }
        d.value = {}
        for k in pairs(values) do
          local ok, v = call(inh.get, inh, info, k)
          d.value[tostring(k)] = ok and v and true or false
        end
      end
    end
  elseif opt.type == "range" then
    d.min, d.max, d.step = opt.softMin or opt.min, opt.softMax or opt.max, opt.step or opt.bigStep
  end
  if evalField(inh.disabled, info, inh) == true then d.disabled = true end
  return d
end

-- walk an options tree; visit(keys, opt, inherited) for every leaf option that is not hidden
local function walk(root, app, visit)
  local nodes = 0
  local function rec(group, keys, inh)
    if type(group.args) ~= "table" then return end
    for key, opt in pairs(group.args) do
      nodes = nodes + 1
      if nodes > MAX_NODES then return end
      if type(opt) == "table" then
        local child = {}
        for _, f in ipairs(INHERIT) do child[f] = opt[f] ~= nil and opt[f] or inh[f] end
        local ck = {}
        for i, k in ipairs(keys) do ck[i] = k end
        ck[#ck + 1] = key
        local info = makeInfo(root, app, ck, opt, child)
        if evalField(opt.hidden, info, child) ~= true then
          if opt.type == "group" then
            if #ck < 10 then rec(opt, ck, child) end
          elseif LEAF[opt.type] then
            visit(ck, opt, child)
          end
        end
      end
    end
  end
  local inh = {}
  for _, f in ipairs(INHERIT) do inh[f] = root[f] end
  rec(root, {}, inh)
end

local function locate(app, path)
  local root = rootOf(app)
  if not root then return nil end
  local found
  walk(root, app, function(keys, opt, inh)
    if not found and table.concat(keys, ".") == path then found = { keys = keys, opt = opt, inh = inh } end
  end)
  if not found then return nil end
  return root, found.keys, found.opt, found.inh
end

local function words(query)
  local out = {}
  for w in tostring(query or ""):lower():gmatch("[%w_]+") do
    if #w >= 2 then out[#out + 1] = w end
  end
  return out
end

-- flatten saved variables (for addons without an AceConfig tree)
local function flattenSV(prefix, v, out, depth)
  if #out >= 3000 or depth > 7 then return end
  if type(v) == "table" then
    for k, child in pairs(v) do
      if type(k) == "string" or type(k) == "number" then flattenSV(prefix .. "." .. tostring(k), child, out, depth + 1) end
    end
  elseif type(v) == "boolean" or type(v) == "number" or type(v) == "string" then
    out[#out + 1] = { path = prefix, value = type(v) == "string" and ns.Cut(v, 1000) or v }
  end
end

local function slashCommandsFor(addon)
  local out, a = {}, (addon or ""):lower()
  for key in pairs(SlashCmdList) do
    local k = tostring(key):lower()
    if a ~= "" and (k:find(a, 1, true) or a:find(k, 1, true)) then
      for n = 1, 6 do
        local alias = _G["SLASH_" .. key .. n]
        if not alias then break end
        out[#out + 1] = alias
      end
    end
  end
  return out
end

-- result for the AI: matching options of an addon
function A.FindOptions(addon, query)
  local ws = words(query)
  local function score(s)
    if #ws == 0 then return 1 end
    local n, l = 0, s:lower()
    for _, w in ipairs(ws) do if l:find(w, 1, true) then n = n + 1 end end
    return n
  end
  local apps = appsFor(addon)
  local matches = {}
  for _, app in ipairs(apps) do
    local root = rootOf(app)
    if root then
      walk(root, app, function(keys, opt, inh)
        local info = makeInfo(root, app, keys, opt, inh)
        local hay = table.concat(keys, " ") .. " " .. text(evalField(opt.name, info, inh)) .. " " ..
          text(evalField(opt.desc, info, inh))
        local s = score(hay)
        if s > 0 then matches[#matches + 1] = { s = s, keys = keys, opt = opt, inh = inh, root = root, app = app } end
      end)
    end
  end
  table.sort(matches, function(x, y) if x.s ~= y.s then return x.s > y.s end return #x.keys < #y.keys end)
  local out = { addon = addon, apps = apps, options = {} }
  for i = 1, math.min(#matches, #ws == 0 and 60 or 25) do
    local m = matches[i]
    out.options[i] = describe(m.root, m.app, m.keys, m.opt, m.inh)
  end
  if #apps == 0 then
    -- no options tree: search the addon's saved variables by key names
    local flat = {}
    for _, name in ipairs(svNames(addon)) do flattenSV(name, _G[name], flat, 1) end
    local raw = {}
    for _, e in ipairs(flat) do
      if score(e.path) > 0 then raw[#raw + 1] = e end
      if #raw >= 40 then break end
    end
    out.savedVariables = raw
    out.note = "No AceConfig options tree; these are raw saved variables (change with op=set_setting and path)."
  end
  out.slashCommands = slashCommandsFor(addon)
  out.matched = #matches
  return out
end

function A.GetOption(app, path)
  local root, keys, opt, inh = locate(app, path)
  if not root then return nil end
  local info = makeInfo(root, app, keys, opt, inh)
  if opt.type == "multiselect" then
    local state = {}
    for k in pairs(evalField(opt.values, info, inh) or {}) do
      local ok, v = call(inh.get, inh, info, k)
      state[k] = ok and v and true or false
    end
    return state
  end
  local ok, v = call(inh.get, inh, info)
  if ok then return v end
  return nil
end

function A.SetOption(app, path, value)
  local root, keys, opt, inh = locate(app, path)
  if not root then return false, "Option not found: " .. tostring(path) end
  local info = makeInfo(root, app, keys, opt, inh)
  if evalField(inh.disabled, info, inh) == true then return false, "This option is currently disabled in the add-on." end
  if opt.type == "toggle" and type(value) ~= "boolean" and not (opt.tristate and value == nil) then
    return false, "This option takes true/false."
  elseif opt.type == "range" then
    if type(value) ~= "number" then return false, "This option takes a number." end
    if (opt.min and value < opt.min) or (opt.max and value > opt.max) then return false, "Value out of range." end
  elseif opt.type == "select" then
    local values = evalField(opt.values, info, inh)
    if type(values) == "table" and values[value] == nil and values[tonumber(value) or ""] == nil then
      return false, "Value not allowed."
    end
    if values and values[value] == nil and tonumber(value) and values[tonumber(value)] ~= nil then value = tonumber(value) end
  end
  if opt.type == "multiselect" then
    -- value = { key = true/false, ... }; every listed choice is set on its own, like ticking boxes in the window
    if type(value) ~= "table" then return false, "This option takes a set of {key = true/false}." end
    local values = evalField(opt.values, info, inh) or {}
    for k, v in pairs(value) do
      local key = values[k] ~= nil and k or (tonumber(k) and values[tonumber(k)] ~= nil and tonumber(k)) or nil
      if key == nil then return false, "Choice not allowed: " .. tostring(k) end
      local ok, err = call(inh.set, inh, info, key, v and true or false)
      if not ok then return false, "The add-on rejected the change: " .. tostring(err) end
    end
    local reg = registry()
    if reg and reg.NotifyChange then pcall(reg.NotifyChange, reg, app) end
    return true
  end
  local ok, err = call(inh.set, inh, info, value)
  if not ok then return false, "The add-on rejected the change: " .. tostring(err) end
  local reg = registry()
  if reg and reg.NotifyChange then pcall(reg.NotifyChange, reg, app) end
  return true
end

------------------------------------------------------------------------ applying changes
local function splitPath(path)
  local parts = {}
  for seg in tostring(path):gmatch("[^%.]+") do parts[#parts + 1] = seg end
  return parts
end

local function resolve(path)
  local parts = splitPath(path)
  if #parts < 2 then return nil end
  local t = _G[parts[1]]
  for i = 2, #parts - 1 do
    if type(t) ~= "table" then return nil end
    local seg = parts[i]
    local nxt = t[seg]
    if nxt == nil and tonumber(seg) then nxt = t[tonumber(seg)] end
    t = nxt
  end
  if type(t) ~= "table" then return nil end
  local last = parts[#parts]
  if t[last] == nil and tonumber(last) and t[tonumber(last)] ~= nil then last = tonumber(last) end
  return t, last
end

local function now() return date("%Y-%m-%d %H:%M") end

function A.Current(action)
  if action.op == "set_option" then return A.GetOption(action.app, action.path) end
  if action.op ~= "set_setting" then return nil end
  local t, key = resolve(action.path)
  if not t then return nil end
  return t[key]
end

-- ``done``: a sale to the vendor takes a few seconds (one stack at a time, each answered by the server); it is
-- called when the sale is over with (stacks sold, stacks to sell, copper gained, why it ended early or nil).
local SALE_STEP_S, SALE_SETTLE_S = 0.15, 4 -- between two stacks; how long the last answers are waited for

function A.Apply(action, done)
  local entry = { at = now(), addon = action.addon, op = action.op, path = action.path, command = action.command,
    id = action.id }
  if action.op == "vendor_sell" then
    if not ns.MerchantOpen() then return false, "The vendor window is closed." end
    local want = {}
    for _, it in ipairs(action.items or {}) do if tonumber(it.id) then want[tonumber(it.id)] = it end end
    local queue = {}
    for bag = 0, 5 do
      for slot = 1, (C_Container.GetContainerNumSlots(bag) or 0) do
        local info = C_Container.GetContainerItemInfo(bag, slot)
        if info and info.itemID and want[info.itemID] and not info.isLocked then
          queue[#queue + 1] = { bag, slot, info.itemID }
        end
      end
    end
    if #queue == 0 then return false, "These items are no longer in your bags." end
    local before = GetMoney()
    -- a stack is sold when its slot no longer holds the item; one the game still has locked is not answered yet
    local function left()
      local there, waiting = 0, 0
      for _, q in ipairs(queue) do
        local info = C_Container.GetContainerItemInfo(q[1], q[2])
        if info and info.itemID == q[3] then
          there = there + 1
          if info.isLocked then waiting = waiting + 1 end
        end
      end
      return there, waiting
    end
    local function finish(why)
      local there = left()
      if done then pcall(done, #queue - there, #queue, GetMoney() - before, why) end
    end
    -- The clicks are out: the result is what the bags hold once the game has answered them all. Answered means
    -- that every stack is gone, or that none is locked and the bags stood still for a moment (a stack the game has
    -- not answered yet is not one the vendor did not take, and not every game locks it meanwhile).
    local function settle(why)
      local waited, last, still = 0, nil, 0
      local wait
      wait = C_Timer.NewTicker(0.2, function()
        waited = waited + 0.2
        local there, waiting = left()
        still = there == last and still + 1 or 0
        last = there
        if there == 0 or (waiting == 0 and still >= 3) or waited >= SALE_SETTLE_S then
          wait:Cancel()
          finish(why)
        end
      end)
    end
    -- one stack at a time, like right-clicking with the vendor open
    local i = 0
    local ticker
    ticker = C_Timer.NewTicker(SALE_STEP_S, function()
      i = i + 1
      local q = queue[i]
      if not q or not ns.MerchantOpen() then
        ticker:Cancel()
        settle(q and "the vendor window was closed" or nil)
        return
      end
      C_Container.UseContainerItem(q[1], q[2])
    end)
    entry.old, entry.new = (#queue .. " stacks"), "sold"
    entry.items = action.items
  elseif action.op == "vendor_repair" then
    if not ns.MerchantOpen() then return false, "The vendor window is closed." end
    if not (CanMerchantRepair and CanMerchantRepair()) then return false, "This vendor cannot repair." end
    local cost, needed = GetRepairAllCost()
    if not needed or not cost or cost <= 0 then return false, "Nothing to repair." end
    if GetMoney() < cost then return false, "Not enough gold to repair." end
    RepairAllItems()
    entry.old, entry.new = cost, "repaired"
  elseif action.op == "abandon_quest" then
    local qid = tonumber(action.questId)
    if not qid or not (C_QuestLog.GetLogIndexForQuestID and C_QuestLog.GetLogIndexForQuestID(qid)) then
      return false, "This quest is no longer in the quest log."
    end
    C_QuestLog.SetSelectedQuest(qid)
    if C_QuestLog.SetAbandonQuest then C_QuestLog.SetAbandonQuest() elseif SetAbandonQuest then SetAbandonQuest() end
    if C_QuestLog.AbandonQuest then C_QuestLog.AbandonQuest() elseif AbandonQuest then AbandonQuest() end
    entry.old, entry.new, entry.questId = action.title, "abandoned", qid
  elseif action.op == "set_option" then
    local old = A.GetOption(action.app, action.path)
    local ok, err = A.SetOption(action.app, action.path, action.value)
    if not ok then return false, err end
    entry.app, entry.old, entry.new = action.app, old, action.value
  elseif action.op == "set_setting" then
    local t, key = resolve(action.path)
    if not t or t[key] == nil then return false, "Setting not found: " .. tostring(action.path) end
    local old = t[key]
    if type(old) ~= type(action.value) then return false, "Value type differs from the current one (" .. type(old) .. ")." end
    t[key] = action.value
    entry.old, entry.new = old, action.value
  elseif action.op == "toggle_addon" then
    local enabled = C_AddOns.GetAddOnEnableState(action.addon, UnitName("player")) > 0
    if action.value then C_AddOns.EnableAddOn(action.addon) else C_AddOns.DisableAddOn(action.addon) end
    C_AddOns.SaveAddOns()
    entry.old, entry.new = enabled, action.value
  elseif action.op == "slash_command" then
    local word, rest = action.command:match("^(/%S+)%s*(.-)$")
    if not word then return false, "Invalid command." end
    word = word:lower()
    local handlerKey
    for key in pairs(SlashCmdList) do
      for n = 1, 12 do
        local alias = _G["SLASH_" .. key .. n]
        if not alias then break end
        if alias:lower() == word then handlerKey = key break end
      end
      if handlerKey then break end
    end
    if not handlerKey then return false, "Command not found: " .. word end
    local ok, err = pcall(SlashCmdList[handlerKey], rest or "")
    if not ok then return false, "The command failed: " .. tostring(err) end
    entry.old, entry.new = "-", action.command
  else
    return false, "Unsupported operation."
  end
  table.insert(ns.db.changeLog, entry)
  while #ns.db.changeLog > 100 do table.remove(ns.db.changeLog, 1) end
  return true, entry
end

function A.Undo(entry)
  if entry.undone then return false, "Already undone." end
  if entry.op == "abandon_quest" then
    return false, "An abandoned quest cannot be restored; pick it up again from the NPC."
  end
  if entry.op == "vendor_repair" then return false, "A repair cannot be undone." end
  if entry.op == "vendor_sell" then
    -- buy the sold items back (the vendor's buyback list, same price), while the window is open
    if not ns.MerchantOpen() then return false, "Buyback requires the vendor window to be open." end
    local names = {}
    for _, it in ipairs(entry.items or {}) do names[(tostring(it.name or ""):gsub("^%[", ""):gsub("%]$", ""))] = true end
    local bought = 0
    for b = (GetNumBuybackItems and GetNumBuybackItems() or 0), 1, -1 do
      local name = GetBuybackItemInfo(b)
      if name and names[name] then BuybackItem(b) bought = bought + 1 end
    end
    if bought == 0 then return false, "These items are not in the buyback list." end
    entry.undone = true
    return true
  end
  if entry.op == "set_option" then
    local ok, err = A.SetOption(entry.app, entry.path, entry.old)
    if not ok then return false, err end
  elseif entry.op == "set_setting" then
    local t, key = resolve(entry.path)
    if not t then return false, "The setting no longer exists." end
    t[key] = entry.old
  elseif entry.op == "toggle_addon" then
    if entry.old then C_AddOns.EnableAddOn(entry.addon) else C_AddOns.DisableAddOn(entry.addon) end
    C_AddOns.SaveAddOns()
  else
    return false, "A command cannot be undone automatically."
  end
  entry.undone = true
  table.insert(ns.db.changeLog, { at = now(), addon = entry.addon, op = "undo", path = entry.path, old = entry.new, new = entry.old })
  return true
end
