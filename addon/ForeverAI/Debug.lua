-- ForeverAI: what the add-on sees and decides, for finding what goes wrong. The last lines are kept (/fai log shows
-- them), and while the ForeverAI app asks for them (it does when it runs from source, on a developer's computer) they
-- go to the app every few seconds, in a small message of their own; the app writes them to data/debug.log.
local ADDON, ns = ...
local D = {}
ns.DebugLog = D
local KEEP, BATCH, GAP_S = 300, 60, 8 -- lines kept; lines per message; seconds between two messages
local lines, unsent = {}, {}
local lastSent = -GAP_S

local function live() return ns.db and type(ns.db.status) == "table" and ns.db.status.debugLog == true end

-- one line of the log: kind ("track", "route", "answer", "error"...) and what happened
function ns.Log(kind, text)
  local e = { t = time(), k = tostring(kind), m = tostring(text) }
  lines[#lines + 1] = e
  if #lines > KEEP then table.remove(lines, 1) end
  if live() then
    unsent[#unsent + 1] = e
    if #unsent > KEEP then table.remove(unsent, 1) end
  end
end

function D.Lines(n)
  local out = {}
  for i = math.max(1, #lines - (n or 20) + 1), #lines do
    out[#out + 1] = date("%H:%M:%S", lines[i].t) .. " " .. lines[i].k .. ": " .. lines[i].m
  end
  return out
end

-- the player's own questions and the add-on's updates always go first
local function flush()
  if not live() then
    wipe(unsent)
    return
  end
  if #unsent == 0 or GetTime() - lastSent < GAP_S then return end
  if ns.pending or ns.collecting or not (ns.Transport and ns.Transport.Send) or ns.Transport.sending or not ns.char then
    return
  end
  local batch = {}
  for i = 1, math.min(BATCH, #unsent) do batch[i] = unsent[i] end
  local msg = ns.BaseMessage("debug")
  msg.deleteConvs = nil -- those go with a question, which the app answers
  msg.entries = batch
  lastSent = GetTime()
  ns.Transport.Send(msg, function(ok)
    if ok then
      for _ = 1, #batch do table.remove(unsent, 1) end
    end
  end)
end

C_Timer.NewTicker(1, function() pcall(flush) end)
