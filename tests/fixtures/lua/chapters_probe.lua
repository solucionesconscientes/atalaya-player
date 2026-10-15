-- Calls the pure functions of mpv-config/script-modules/mu/chapters.lua for tests/test_mu_chapters.py, inside a
-- headless mpv started with --load-scripts=no (the module never touches mp, but this is the Lua mpv really runs).
--   script-message-to chapters_probe call <id> <function> <JSON array of arguments>
--   → user-data/chapters-probe/<id> = { ok = true, value = <what it returned> } | { ok = false, error = <text> }
local mp = require('mp')
local utils = require('mp.utils')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local chapters = require('mu.chapters')
local unpack = unpack or table.unpack   -- mpv builds with Lua 5.2 too (this machine's does)

mp.register_script_message('call', function(id, name, args)
  local ok, value = pcall(function()
    local fn = chapters[name]
    if type(fn) ~= 'function' then error('mu.chapters has no function ' .. tostring(name)) end
    return fn(unpack(utils.parse_json(args or '[]') or {}))
  end)
  if ok then
    mp.set_property_native('user-data/chapters-probe/' .. id, { ok = true, value = value })
  else
    mp.set_property_native('user-data/chapters-probe/' .. id, { ok = false, error = tostring(value) })
  end
end)

mp.set_property_native('user-data/chapters-probe/ready', true)
