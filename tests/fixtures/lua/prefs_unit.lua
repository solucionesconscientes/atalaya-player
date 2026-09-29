-- Unit checks of mpv-config/script-modules/mu/prefs.lua, run by tests/test_prefs.py inside a headless mpv started with
-- --load-scripts=no (this script is then the only one, so it is the writer). Results go to user-data/prefs-unit:
-- { done = true, failures = { "<check>: <detail>", ... }, checks = <n> }.
local mp = require('mp')
local utils = require('mp.utils')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local prefs = require('mu.prefs')

local failures, checks = {}, 0
local function check(name, cond, detail)
  checks = checks + 1
  if not cond then table.insert(failures, name .. (detail and (': ' .. tostring(detail)) or '')) end
end

prefs.become_writer()

local expected_dir = os.getenv('MPV_UOS_DATA_DIR')
check('path uses MPV_UOS_DATA_DIR', prefs.path() == utils.join_path(expected_dir, 'prefs.json'), prefs.path())
check('enabled by default', prefs.enabled() == true)

-- prefs.json was prepared by the test: {"unit": {"n": 5, "s": 7, "t": ["x"], "extra": 1}, "other": {"k": "v"}}
local P = prefs.ns('unit', { n = 1, s = 'a', b = false, t = {}, f = 0.5 }, function(key, v)
  if key == 'n' then return v >= 0 end
  return true
end)
check('stored number read', P:get('n') == 5, P:get('n'))
check('wrong type dropped', P:get('s') == 'a' and not P:has('s'), P:get('s'))
check('stored table read', type(P:get('t')) == 'table' and P:get('t')[1] == 'x')
check('default when missing', P:get('b') == false and not P:has('b'))
check('unknown key ignored', P:get('extra') == nil)

check('set wrong type refused', P:set('n', 'x') == false and P:get('n') == 5)
check('validator refuses', P:set('n', -3) == false and P:get('n') == 5)
check('set equal is a no-op', P:set('n', 5) == false)
check('set default when unset is a no-op', P:set('b', false) == false and not P:has('b'))
check('set changes', P:set('b', true) == true and P:get('b') == true)
check('tolerant float compare', P:set('f', 0.5 + 1e-9) == false)
check('set float', P:set('f', 1.3) == true)
local t = P:get('t')
t[1] = 'mutated'
check('get returns a copy', P:get('t')[1] == 'x')
check('unset', P:unset('t') == true and not P:has('t') and #P:get('t') == 0)
check('unset twice is a no-op', P:unset('t') == false)

-- encoder: sorted keys, readable numbers, arrays inline, strings escaped
local enc = prefs._encode({ z = 1, a = { 'x', 'y' }, m = { q = true }, f = 1.2999999523162842, s = 'dí "hola"' })
local back = utils.parse_json(enc)
check('encode round trip', type(back) == 'table' and back.z == 1 and back.a[2] == 'y' and back.m.q == true
  and back.s == 'dí "hola"', enc)
check('encode rounds floats', enc:find('"f": 1.3', 1, true) ~= nil, enc)
check('encode sorts keys', enc:find('"a"', 1, true) < enc:find('"z"', 1, true), enc)

prefs.flush()
local f = io.open(prefs.path(), 'rb')
local saved = f and utils.parse_json(f:read('*a')) or {}
if f then f:close() end
check('flush merged with disk', type(saved.unit) == 'table' and saved.unit.n == 5 and saved.unit.b == true
  and saved.unit.f == 1.3 and saved.unit.t == nil and saved.unit.s == nil, utils.format_json(saved))
check('unknown keys and namespaces kept', type(saved.unit) == 'table' and saved.unit.extra == 1
  and type(saved.other) == 'table' and saved.other.k == 'v', utils.format_json(saved))
check('format version', saved._version == 1)
check('status after write', prefs.status().writes == 1 and prefs.status().last_error == '')

-- a second namespace from the same script, then a reset: backup file + callbacks with 'reset'
local Q = prefs.ns('unit2', { on = false })
Q:set('on', true)
local reasons = {}
P:on_change(function(reason) table.insert(reasons, reason) end)
prefs.flush()
local bak = prefs.reset_all()
check('reset made a backup', bak ~= nil and bak:find('prefs.json.bak-', 1, true) ~= nil
  and utils.file_info(bak) ~= nil and utils.file_info(prefs.path()) == nil, bak)

mp.add_timeout(0.3, function()
  check('reset callback', reasons[1] == 'reset', utils.format_json(reasons))
  check('reset forgets values', P:get('n') == 1 and not P:has('b') and Q:get('on') == false)
  mp.set_property_native('user-data/prefs-unit', { done = true, failures = failures, checks = checks })
end)
