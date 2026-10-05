-- Shared JSON-RPC client for mu-* scripts. Requests travel through mpv itself:
--   script-message mu-rpc <json-rpc request> <this script>   (mpvd listens as an IPC client)
--   script-message-to <this script> mu-reply <json-rpc response>
-- Usage: local rpc = require('mu.rpc'); rpc.call('ping', nil, function(err, result) ... end)
local mp = require('mp')
local utils = require('mp.utils')

local M = { timeout = 15 }
local pending = {}
local next_id = 0
local SCRIPT = mp.get_script_name()

function M.connected()
  local core = mp.get_property_native('user-data/mu/core')
  return core ~= nil and core.mpvd == 'connected'
end

function M.call(method, params, cb, timeout)
  next_id = next_id + 1
  local id = next_id
  local req = { jsonrpc = '2.0', id = id, method = method }
  if params ~= nil then req.params = params end
  local payload = utils.format_json(req)
  if not payload then
    if cb then cb({ code = -32700, message = 'cannot encode params for ' .. method }) end
    return nil
  end
  local timer = mp.add_timeout(timeout or M.timeout, function()
    local p = pending[id]
    pending[id] = nil
    if p and p.cb then p.cb({ code = -32000, message = 'timeout waiting for mpvd (' .. method .. ')' }) end
  end)
  pending[id] = { cb = cb, timer = timer }
  mp.commandv('script-message', 'mu-rpc', payload, SCRIPT)
  return id
end

-- H63/N2 · cuántas peticiones hay esperando respuesta. Lo usan los vigilantes de los menús: al pulsar una fila,
-- uosc cierra el menú y el módulo lo reabre CUANDO CONTESTA mpvd, así que entre una cosa y otra el módulo no se
-- puede dar por perdido. Antes se esperaba un plazo (0,2 s) y con el equipo cargado la ida y vuelta tarda más.
function M.pending()
  local n = 0
  for _ in pairs(pending) do n = n + 1 end
  return n
end

function M.cancel_all()
  for _, p in pairs(pending) do p.timer:kill() end
  pending = {}
end

mp.register_script_message('mu-reply', function(payload)
  local resp = utils.parse_json(payload or '')
  if type(resp) ~= 'table' then return end
  local p = pending[resp.id]
  if not p then return end
  pending[resp.id] = nil
  p.timer:kill()
  if p.cb then p.cb(resp.error, resp.result) end
end)

return M
