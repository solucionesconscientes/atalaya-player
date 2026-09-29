-- Persistent user preferences shared by the mu-* scripts: one JSON file, <data_dir>/prefs.json, with one object per
-- namespace ("mpv", "mu-av", "mu-study"...). Precedence: --script-opts from the command line > prefs.json >
-- script-opts/*.conf > code defaults (see NS:apply_opts). Kill switch: MPV_UOS_PREFS=0 or mu-prefs-enabled=no.
--
--   local prefs = require('mu.prefs')
--   local P = prefs.ns('mu-av', { filters = {}, light = false })   -- defaults fix each key's type
--   P:get('filters') · P:set('light', true) · P:unset(k) · P:reset() · P:on_change(function(reason) ... end)
--   prefs.flush() · prefs.path()
--
-- Every script reads the file when it creates its namespace, but only ONE script per mpv process writes it (mu-prefs,
-- which calls prefs.become_writer()): the others send each change as `script-message-to mu_prefs mu-prefs-put`.
-- mpv runs every script in its own thread, so letting each one rewrite the file would lose updates when two of them
-- flush at the same time (typically on quit). The writer debounces (1.5 s, at most 5 s), flushes on shutdown and
-- writes atomically: re-read the file, merge only the keys changed by this process (last change wins per key across
-- instances), write prefs.json.tmp-<pid> and rename it over prefs.json (remove + rename on Windows).
-- A corrupt file is moved to prefs.json.corrupt-<timestamp> (warned once); invalid keys are dropped one by one.
-- A reset moves the file to prefs.json.bak-<timestamp> (never deleted) and broadcasts `script-message mu-prefs-reset`:
-- each namespace forgets its values and calls its on_change callbacks with reason 'reset'.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')

local M = {}

local FILE = 'prefs.json'
local WRITER = 'mu_prefs'          -- script name of the single writer (mpv-config/scripts/mu-prefs.lua)
local DEBOUNCE = 1.5
local MAX_DELAY = 5
local FORMAT_VERSION = 1

local platform = mp.get_property_native('platform') or ''
local is_windows = platform == 'windows'

local enabled = nil        -- lazily computed kill switch
local loaded = false
local data = {}            -- ns -> key -> value, as read from disk (plus this process' changes)
local spaces = {}          -- ns -> namespace object
local writer = false
local dirty = {}           -- writer only: ns -> key -> { value = v } | { delete = true }
local dirty_since = nil
local flush_timer = nil
local status = { writes = 0, last_write = 0, last_error = '', corrupt = '', backup = '', warned = false }
local status_cb = nil

-- ---------------------------------------------------------------------------------------------
-- helpers

local function copy(v)
  if type(v) ~= 'table' then return v end
  local out = {}
  for k, x in pairs(v) do out[k] = copy(x) end
  return out
end

-- numbers compare with a relative tolerance: mpv keeps some options as single-precision floats (sub-scale 1.3 reads
-- back as 1.2999999523) and the file stores a rounded decimal
local function num_eq(a, b)
  return a == b or math.abs(a - b) <= 1e-6 * math.max(1, math.abs(a), math.abs(b))
end

local function deep_eq(a, b)
  if type(a) ~= type(b) then return false end
  if type(a) == 'number' then return num_eq(a, b) end
  if type(a) ~= 'table' then return a == b end
  for k, v in pairs(a) do if not deep_eq(v, b[k]) then return false end end
  for k in pairs(b) do if a[k] == nil then return false end end
  return true
end

local function is_array(t)
  local n = 0
  for _ in pairs(t) do n = n + 1 end
  return n == #t
end

local function finite(n) return n == n and n ~= math.huge and n ~= -math.huge end

-- JSON with sorted keys and one key per line, so the file stays readable and diff-friendly.
local function encode(v, indent)
  indent = indent or ''
  local t = type(v)
  if t == 'table' then
    if is_array(v) then
      local parts = {}
      for _, x in ipairs(v) do table.insert(parts, encode(x, indent)) end
      return '[' .. table.concat(parts, ', ') .. ']'
    end
    local keys = {}
    for k in pairs(v) do table.insert(keys, tostring(k)) end
    table.sort(keys)
    local lines = {}
    local inner = indent .. '  '
    for _, k in ipairs(keys) do
      table.insert(lines, inner .. utils.format_json(k) .. ': ' .. encode(v[k], inner))
    end
    return '{\n' .. table.concat(lines, ',\n') .. '\n' .. indent .. '}'
  elseif t == 'number' then
    if not finite(v) then return 'null' end
    if v == math.floor(v) and math.abs(v) < 1e15 then return string.format('%d', v) end
    for _, f in ipairs({ '%.6g', '%.9g', '%.12g' }) do
      local s = string.format(f, v)
      if num_eq(tonumber(s), v) then return s end
    end
    return string.format('%.17g', v)
  elseif t == 'boolean' then
    return tostring(v)
  elseif t == 'string' then
    return utils.format_json(v)
  end
  return 'null'
end
M._encode = encode   -- exposed for the module tests

local function timestamp() return os.date('%Y%m%d-%H%M%S') end

local function file_exists(p)
  local info = utils.file_info(p)
  return info ~= nil and info.is_file
end

-- ---------------------------------------------------------------------------------------------
-- location and kill switch

-- Same rules as mpvd/config.py default_data_dir(): $MPV_UOS_DATA_DIR (exported by bin/mpv-uos), else the platform's
-- user data directory.
function M.data_dir()
  local env = os.getenv('MPV_UOS_DATA_DIR')
  if env and env ~= '' then return env end
  if is_windows then
    local appdata = os.getenv('APPDATA')
    if not appdata or appdata == '' then appdata = utils.join_path(os.getenv('USERPROFILE') or '', 'AppData\\Roaming') end
    return utils.join_path(appdata, 'mpv-uos')
  end
  local home = os.getenv('HOME') or ''
  if platform == 'darwin' then return home .. '/Library/Application Support/mpv-uos' end
  local xdg = os.getenv('XDG_DATA_HOME')
  if xdg and xdg ~= '' then return utils.join_path(xdg, 'mpv-uos') end
  return home .. '/.local/share/mpv-uos'
end

function M.path() return utils.join_path(M.data_dir(), FILE) end

function M.enabled()
  if enabled == nil then
    local env = os.getenv('MPV_UOS_PREFS')
    if env == '0' or env == 'no' or env == 'false' then
      enabled = false
    else
      local o = { enabled = true }
      options.read_options(o, 'mu-prefs')
      enabled = o.enabled
    end
  end
  return enabled
end

-- True when --script-opts on the command line (or mpv.conf) sets <ident>-<key>: that value wins over prefs.json.
function M.cli_opt(ident, key)
  local so = mp.get_property_native('options/script-opts') or {}
  return so[ident .. '-' .. key] ~= nil
end

local function ensure_dir(dir)
  local info = utils.file_info(dir)
  if info and info.is_dir then return true end
  local args = is_windows and { 'cmd', '/d', '/c', 'mkdir', dir } or { 'mkdir', '-p', dir }
  mp.command_native({ name = 'subprocess', args = args, playback_only = false, capture_stdout = true,
    capture_stderr = true })
  info = utils.file_info(dir)
  return info ~= nil and info.is_dir
end

-- ---------------------------------------------------------------------------------------------
-- disk

-- Returns the decoded table, {} when the file does not exist, or nil + 'corrupt'.
local function read_disk()
  local f = io.open(M.path(), 'rb')
  if not f then return {} end
  local s = f:read('*a') or ''
  f:close()
  if s:match('^%s*$') then return {} end
  local t = utils.parse_json(s)
  if type(t) ~= 'table' or (next(t) ~= nil and is_array(t)) then return nil, 'corrupt' end
  for ns, v in pairs(t) do
    if type(v) ~= 'table' and ns ~= '_version' then t[ns] = nil end
  end
  return t
end

-- Writer only: move an unreadable file aside (never delete it) and tell the user once.
local function quarantine()
  local target = M.path() .. '.corrupt-' .. timestamp()
  if file_exists(target) then target = target .. '-' .. utils.getpid() end
  local ok = os.rename(M.path(), target)
  status.corrupt = ok and target or ''
  msg.warn('prefs.json dañado: ' .. (ok and ('movido a ' .. target) or 'no se pudo mover') ..
    '; se usan los valores por defecto')
  if not status.warned then
    status.warned = true
    mp.osd_message('Preferencias dañadas: se usan los valores por defecto (copia en ' ..
      (target:match('[^/\\]+$') or target) .. ')', 6)
  end
end

local function write_disk(tbl)
  local dir = M.data_dir()
  if not ensure_dir(dir) then return false, 'no se pudo crear ' .. dir end
  local path = M.path()
  local tmp = path .. '.tmp-' .. utils.getpid()
  local f, err = io.open(tmp, 'wb')
  if not f then return false, tostring(err) end
  local ok, werr = f:write(encode(tbl) .. '\n')
  f:close()
  if not ok then os.remove(tmp); return false, tostring(werr) end
  local renamed, rerr = os.rename(tmp, path)
  if not renamed and is_windows then
    os.remove(path)
    renamed, rerr = os.rename(tmp, path)
  end
  if not renamed then os.remove(tmp); return false, tostring(rerr) end
  return true
end

local function load()
  if loaded then return end
  loaded = true
  data = {}
  if not M.enabled() then return end
  local t, err = read_disk()
  if not t then
    if writer then quarantine() else msg.verbose('prefs.json unreadable (' .. err .. '): using defaults') end
    return
  end
  t._version = nil
  data = t
end

local function notify_status()
  if status_cb then pcall(status_cb, M.status()) end
end

function M.status()
  return { enabled = M.enabled(), path = M.path(), writes = status.writes, last_write = status.last_write,
    last_error = status.last_error, corrupt = status.corrupt, backup = status.backup, pending = dirty_since ~= nil }
end

function M.on_status(fn) status_cb = fn end

-- ---------------------------------------------------------------------------------------------
-- writer

function M.flush()
  if not M.enabled() then return true end
  if not writer then
    mp.commandv('script-message-to', WRITER, 'mu-prefs-flush')
    return true
  end
  if flush_timer then flush_timer:kill(); flush_timer = nil end
  if next(dirty) == nil then dirty_since = nil; return true end
  local disk, err = read_disk()
  if not disk then quarantine(); disk = {} end
  disk._version = nil
  for ns, keys in pairs(dirty) do
    local target = type(disk[ns]) == 'table' and disk[ns] or {}
    for k, op in pairs(keys) do
      if op.delete then target[k] = nil else target[k] = copy(op.value) end
    end
    disk[ns] = next(target) ~= nil and target or nil
  end
  disk._version = FORMAT_VERSION
  local ok, werr = write_disk(disk)
  if ok then
    dirty = {}
    dirty_since = nil
    status.writes = status.writes + 1
    status.last_write = os.time()
    status.last_error = ''
  else
    status.last_error = werr or err or 'error'
    msg.warn('prefs: no se pudo escribir ' .. M.path() .. ': ' .. status.last_error)
  end
  notify_status()
  return ok
end

local function schedule()
  local now = mp.get_time()
  dirty_since = dirty_since or now
  if flush_timer then flush_timer:kill(); flush_timer = nil end
  if now - dirty_since >= MAX_DELAY then M.flush() return end
  flush_timer = mp.add_timeout(DEBOUNCE, function() flush_timer = nil; M.flush() end)
  notify_status()
end

local function record(ns, key, op)
  if writer then
    dirty[ns] = dirty[ns] or {}
    dirty[ns][key] = op
    schedule()
  else
    mp.commandv('script-message-to', WRITER, 'mu-prefs-put', ns, key, utils.format_json(op))
  end
end

-- Moves prefs.json to prefs.json.bak-<timestamp> and tells every script to go back to its defaults.
function M.reset_all()
  if not writer then
    mp.commandv('script-message-to', WRITER, 'reset')
    return nil
  end
  if flush_timer then flush_timer:kill(); flush_timer = nil end
  dirty = {}
  dirty_since = nil
  local bak = ''
  if file_exists(M.path()) then
    bak = M.path() .. '.bak-' .. timestamp()
    if file_exists(bak) then bak = bak .. '-' .. utils.getpid() end
    local ok, err = os.rename(M.path(), bak)
    if not ok then
      status.last_error = 'no se pudo mover prefs.json: ' .. tostring(err)
      msg.warn(status.last_error)
      bak = ''
    end
  end
  status.backup = bak
  notify_status()
  mp.commandv('script-message', 'mu-prefs-reset', bak)
  return bak
end

function M.become_writer()
  if writer then return end
  writer = true
  loaded = false   -- re-read as the writer so a corrupt file gets quarantined
  load()
  for _, space in pairs(spaces) do space:_reload() end
  mp.register_script_message('mu-prefs-put', function(ns, key, json)
    if not M.enabled() or not ns or not key then return end
    local op = utils.parse_json(json or '')
    if type(op) ~= 'table' then return end
    if op.delete then
      if data[ns] then data[ns][key] = nil end
      record(ns, key, { delete = true })
    elseif op.value ~= nil then
      data[ns] = data[ns] or {}
      data[ns][key] = copy(op.value)
      record(ns, key, { value = op.value })
    end
  end)
  mp.register_script_message('mu-prefs-flush', function() M.flush() end)
  mp.register_event('shutdown', function() M.flush() end)
end

-- ---------------------------------------------------------------------------------------------
-- namespaces

local NS = {}
NS.__index = NS

function NS:_valid(key, value)
  local def = self.defaults[key]
  if def == nil or value == nil then return false end
  if type(value) ~= type(def) then return false end
  if type(value) == 'number' and not finite(value) then return false end
  if self.validate and not self.validate(key, value) then return false end
  return true
end

-- (re)build the validated view of this namespace from `data`; invalid values are dropped one by one
function NS:_reload()
  self.values = {}
  local stored = data[self.name]
  if type(stored) ~= 'table' then return end
  for k, v in pairs(stored) do
    if self.defaults[k] ~= nil then
      if self:_valid(k, v) then
        self.values[k] = v
      else
        msg.warn(string.format('prefs: %s.%s tiene un valor no válido (%s); se descarta', self.name, tostring(k),
          utils.format_json(v) or '?'))
        stored[k] = nil
        record(self.name, k, { delete = true })
      end
    end
  end
end

function NS:get(key)
  local v = self.values[key]
  if v == nil then v = self.defaults[key] end
  return copy(v)
end

function NS:has(key) return self.values[key] ~= nil end

function NS:all() return copy(self.values) end

function NS:default(key) return copy(self.defaults[key]) end

-- Stores a value chosen by the user (never call it from property observers or automatic code paths).
-- nil removes the key. Returns true when something changed.
function NS:set(key, value)
  if value == nil then return self:unset(key) end
  if not self:_valid(key, value) then
    msg.warn(string.format('prefs: valor no válido para %s.%s: %s', self.name, tostring(key),
      utils.format_json(value) or '?'))
    return false
  end
  if deep_eq(self:get(key), value) then return false end
  if type(value) == 'number' then value = tonumber(encode(value)) end   -- same number as the file will hold
  self.values[key] = copy(value)
  if not M.enabled() then return true end
  data[self.name] = data[self.name] or {}
  data[self.name][key] = copy(value)
  record(self.name, key, { value = value })
  return true
end

function NS:unset(key)
  if self.values[key] == nil then return false end
  self.values[key] = nil
  if not M.enabled() then return true end
  if data[self.name] then data[self.name][key] = nil end
  record(self.name, key, { delete = true })
  return true
end

-- Forgets every stored value of this namespace (the script restores its own defaults).
function NS:reset()
  local keys = {}
  for k in pairs(self.values) do table.insert(keys, k) end
  for _, k in ipairs(keys) do self:unset(k) end
end

function NS:on_change(fn) table.insert(self.callbacks, fn) end

-- Overlays stored values on script options read with mp.options, unless the key came from --script-opts.
function NS:apply_opts(opts, ident, keys)
  for _, k in ipairs(keys) do
    if self:has(k) and not M.cli_opt(ident, k) then opts[k] = self:get(k) end
  end
end

-- validate(key, value) -> bool is optional (extra checks on top of the type of each default).
function M.ns(name, defaults, validate)
  if spaces[name] then return spaces[name] end
  load()
  local space = setmetatable({ name = name, defaults = copy(defaults or {}), validate = validate, values = {},
    callbacks = {} }, NS)
  spaces[name] = space
  if M.enabled() then space:_reload() end
  return space
end

mp.register_script_message('mu-prefs-reset', function()
  data = {}
  for _, space in pairs(spaces) do
    space.values = {}
    for _, fn in ipairs(space.callbacks) do
      local ok, err = pcall(fn, 'reset')
      if not ok then msg.error('prefs reset callback (' .. space.name .. '): ' .. tostring(err)) end
    end
  end
end)

return M
