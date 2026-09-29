-- mu-core: bridge between mpv and the mpvd companion daemon.
--
-- mpv's Lua has no sockets, so this script never holds a connection. Instead it runs a short
-- `python -m mpvd ensure --attach <ipc>` subprocess that starts the daemon if needed and asks it to
-- attach to THIS mpv (mpvd then connects to our --input-ipc-server). From then on every mu-* script
-- talks to mpvd through mpv itself:
--   request:  mp.commandv('script-message', 'mu-rpc', <json-rpc 2.0 request>, mp.get_script_name())
--   reply:    script-message-to <that script> mu-reply <json-rpc 2.0 response>
-- State is published under user-data/mu/core (mpvd: disconnected|starting|connected|error).
-- Options (script-opts/mu-core.conf or --script-opts=mu-core-<key>=<value>): see `opts` below.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')

local VERSION = '0.3.0'

local opts = {
  autostart = true,          -- start/attach mpvd on load
  python = '',               -- interpreter with the mpvd package (default: <root>/.venv/bin/python)
  retry_seconds = 10,        -- base delay between failed ensure attempts (exponential backoff, max 120 s)
  max_retries = 20,
  watchdog_seconds = 10,     -- ping period while connected (0 = off)
  load_errors = true,        -- explain on screen why a file/URL/channel could not be opened
  rpc_timeout = 15,          -- seconds before a pending call fails with a timeout error
  ensure_timeout = 20,       -- seconds the ensure subprocess may take (daemon start + attach)
}
options.read_options(opts, 'mu-core')

-- ---------------------------------------------------------------------------------------------
-- environment

local function detect_root()
  local env = os.getenv('MPV_UOS_ROOT')
  if env and env ~= '' then return env end
  local cfg = mp.get_property('config-dir')
  if cfg and cfg ~= '' then
    local parent = utils.split_path(cfg)
    return (parent:gsub('[/\\]+$', ''))
  end
  return ''
end

local state = {
  version = VERSION,
  root = detect_root(),
  ipc = mp.get_property('input-ipc-server') or '',
  platform = mp.get_property_native('platform') or 'unknown',
  uosc = false,
  uosc_version = '',
  mpvd = 'disconnected',
  mpvd_socket = '',
  mpvd_version = '',
  session = '',
  error = '',
  attempts = 0,
}

local function publish()
  mp.set_property_native('user-data/mu/core', state)
end

local function is_windows() return state.platform == 'windows' end

local function file_exists(path)
  local info = utils.file_info(path)
  return info ~= nil and info.is_file
end

local function python_path()
  if opts.python ~= '' then return opts.python end
  if state.root ~= '' then
    local candidate = is_windows() and utils.join_path(state.root, '.venv\\Scripts\\python.exe')
      or utils.join_path(state.root, '.venv/bin/python')
    if file_exists(candidate) then return candidate end
  end
  return is_windows() and 'python' or 'python3'
end

local function is_absolute(path)
  return path:sub(1, 1) == '/' or path:match('^%a:[/\\]') ~= nil or path:sub(1, 2) == '\\\\'
end

local function abs_ipc_path()
  local ipc = state.ipc
  if ipc == '' or is_absolute(ipc) then return ipc end
  local cwd = mp.get_property('working-directory') or utils.getcwd() or ''
  return utils.join_path(cwd, ipc)
end

-- ---------------------------------------------------------------------------------------------
-- RPC plumbing (usable by other scripts through the same messages)

local pending = {}
local next_id = 0

local function rpc(method, params, cb, timeout)
  next_id = next_id + 1
  local id = next_id
  local req = { jsonrpc = '2.0', id = id, method = method }
  if params ~= nil then req.params = params end
  local payload = utils.format_json(req)
  if not payload then
    if cb then cb({ code = -32700, message = 'cannot encode params' }) end
    return nil
  end
  local timer = mp.add_timeout(timeout or opts.rpc_timeout, function()
    local p = pending[id]
    pending[id] = nil
    if p and p.cb then p.cb({ code = -32000, message = 'timeout waiting for mpvd (' .. method .. ')' }) end
  end)
  pending[id] = { cb = cb, timer = timer, method = method }
  mp.commandv('script-message', 'mu-rpc', payload, mp.get_script_name())
  return id
end

mp.register_script_message('mu-reply', function(payload)
  local resp = utils.parse_json(payload or '')
  if type(resp) ~= 'table' then return end
  local p = pending[resp.id]
  if not p then return end
  pending[resp.id] = nil
  if p.timer then p.timer:kill() end
  if p.cb then p.cb(resp.error, resp.result) end
end)

-- ---------------------------------------------------------------------------------------------
-- daemon lifecycle

local ensure -- forward declaration
local retry_timer = nil

local function schedule_retry()
  if state.attempts >= opts.max_retries then
    msg.warn('mpvd: giving up after ' .. state.attempts .. ' attempts')
    return
  end
  local delay = math.min(opts.retry_seconds * (2 ^ math.max(0, state.attempts - 1)), 120)
  if retry_timer then retry_timer:kill() end
  retry_timer = mp.add_timeout(delay, ensure)
  msg.verbose(string.format('mpvd: retrying in %.0fs (attempt %d)', delay, state.attempts + 1))
end

local function last_json_line(text)
  local last = nil
  for line in (text or ''):gmatch('[^\r\n]+') do
    if line:sub(1, 1) == '{' then last = line end
  end
  return last and utils.parse_json(last) or nil
end

ensure = function()
  if not opts.autostart or state.mpvd == 'starting' then return end
  if state.ipc == '' then
    state.mpvd = 'error'
    state.error = 'mpv started without --input-ipc-server (use bin/mpv-uos)'
    publish()
    msg.warn(state.error)
    return
  end
  state.mpvd = 'starting'
  state.attempts = state.attempts + 1
  publish()
  local args = { python_path(), '-m', 'mpvd', 'ensure', '--attach', abs_ipc_path(),
                 '--pid', tostring(utils.getpid()), '--timeout', tostring(opts.ensure_timeout) }
  if state.root ~= '' then
    table.insert(args, '--root')
    table.insert(args, state.root)
  end
  mp.command_native_async({
    name = 'subprocess', args = args, playback_only = false,
    capture_stdout = true, capture_stderr = true,
  }, function(ok, res, err)
    local info = ok and res and last_json_line(res.stdout) or nil
    local sess_pid = info and info.ok and info.session and tonumber(info.session.pid) or nil
    if sess_pid and sess_pid ~= utils.getpid() then
      -- mpvd handed us another player's session (same socket path): never talk through it
      info = { ok = false, error = 'mpvd session belongs to pid ' .. sess_pid .. ', not ' .. utils.getpid() }
    end
    if info and info.ok then
      state.mpvd = 'connected'
      state.mpvd_socket = info.socket or ''
      state.session = info.session and info.session.id or state.session
      state.error = ''
      state.attempts = 0
      msg.info(string.format('mpvd attached (session %s, socket %s%s)', state.session, state.mpvd_socket,
        info.started and ', started now' or ''))
    else
      state.mpvd = 'error'
      state.error = (info and info.error) or (res and res.stderr ~= '' and res.stderr) or err or 'ensure failed'
      msg.warn('mpvd: ' .. tostring(state.error):sub(1, 300))
      schedule_retry()
    end
    publish()
  end)
end

-- mpvd announces itself right after attaching (also when it reconnects on its own).
mp.register_script_message('mu-hello', function(payload)
  local hello = utils.parse_json(payload or '') or {}
  state.mpvd = 'connected'
  state.session = hello.session or state.session
  state.mpvd_version = hello.version or ''
  state.mpvd_socket = hello.socket or state.mpvd_socket
  state.error = ''
  publish()
  msg.verbose('mpvd hello: session ' .. tostring(state.session) .. ' v' .. state.mpvd_version)
end)

-- Watchdog: a missed ping means the daemon died; re-run ensure.
if opts.watchdog_seconds > 0 then
  mp.add_periodic_timer(opts.watchdog_seconds, function()
    if state.mpvd ~= 'connected' then return end
    rpc('ping', nil, function(err)
      if err then
        state.mpvd = 'disconnected'
        state.error = err.message or 'ping failed'
        publish()
        msg.warn('mpvd: watchdog lost the daemon; reconnecting')
        ensure()
      end
    end, math.min(opts.rpc_timeout, opts.watchdog_seconds))
  end)
end

-- ---------------------------------------------------------------------------------------------
-- script messages for other scripts, tests and the console

-- script-message-to mu_core mu-call <method> [params-json]  → result in user-data/mu/last_reply
mp.register_script_message('mu-call', function(method, params_json)
  local params = nil
  if params_json and params_json ~= '' then params = utils.parse_json(params_json) end
  local id = rpc(method, params, function(err, result)
    mp.set_property_native('user-data/mu/last_reply', {
      method = method, ok = err == nil, error = err or '', result = result == nil and '' or result,
    })
    if err then
      mp.osd_message('mpvd ' .. method .. ': ' .. tostring(err.message), 3)
    end
  end)
  mp.set_property_native('user-data/mu/last_reply', { method = method, pending = true, id = id or 0 })
end)

mp.register_script_message('mu-ensure', ensure)

-- Events pushed by mpvd (job progress for jobs submitted with notify=mu_core): kept for tests/diagnostics.
local event_count = 0
mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  event_count = event_count + 1
  ev.seq = event_count
  mp.set_property_native('user-data/mu/last_event', ev)
end)

-- ---------------------------------------------------------------------------------------------
-- uosc detection (broadcast on load + fallback on its user-data)

mp.register_script_message('uosc-version', function(v)
  state.uosc = true
  state.uosc_version = v or ''
  publish()
end)
mp.observe_property('user-data/osc/margins', 'native', function(_, value)
  if value ~= nil and not state.uosc then
    state.uosc = true
    publish()
  end
end)

-- ---------------------------------------------------------------------------------------------
-- load errors: mpv only logs why a file/URL/channel failed ("[ytdl_hook] ERROR: …", "[stream] Failed to open …")
-- and the window just stays empty. Catch the last error line and explain it in Spanish when the load fails.

local load = { path = '', errors = {} }  -- every error line logged while opening the current file

local EXPLAIN = {
  -- { lua pattern on the lowercased error text, explanation }
  { 'drm', 'el contenido está protegido con DRM y no se puede reproducir' },
  { 'sign in', 'la plataforma exige iniciar sesión (o no deja reproducirlo fuera de su web)' },
  { 'log in', 'la plataforma exige iniciar sesión (o no deja reproducirlo fuera de su web)' },
  { 'logged%-in', 'la plataforma exige iniciar sesión (o no deja reproducirlo fuera de su web)' },
  { 'login', 'la plataforma exige iniciar sesión (o no deja reproducirlo fuera de su web)' },
  { 'private video', 'es un vídeo privado' },
  { 'your country', 'no está disponible en tu país (geobloqueo)' },
  { 'your location', 'no está disponible en tu país (geobloqueo)' },
  { 'geo.?restrict', 'no está disponible en tu país (geobloqueo)' },
  { 'geo.?block', 'no está disponible en tu país (geobloqueo)' },
  { 'unsupported url', 'yt-dlp no reconoce esta dirección' },
  { 'unavailable', 'el vídeo ya no está disponible' },
  { 'not available', 'el vídeo ya no está disponible' },
  { 'has been removed', 'el vídeo ya no está disponible' },
  { 'http error 403', 'el servidor ha denegado el acceso (403)' },
  { '403 forbidden', 'el servidor ha denegado el acceso (403)' },
  { 'http error 404', 'no se encuentra en el servidor (404)' },
  { '404 not found', 'no se encuentra en el servidor (404)' },
  { 'timed out', 'el servidor no responde (tiempo agotado)' },
  { 'timeout', 'el servidor no responde (tiempo agotado)' },
  { 'no such file', 'el archivo no existe' },
  { 'permission denied', 'no hay permiso para leer el archivo' },
  { 'recognize file format', 'formato no reconocido' },
  { 'unrecognized file format', 'formato no reconocido' },
  { 'connection refused', 'no se pudo conectar con el servidor' },
  { 'failed to open', 'no se pudo abrir la dirección (servidor caído o sin conexión)' },
}

-- The most specific explanation found in any of the lines (EXPLAIN is ordered from specific to generic).
local function explain(lines, file_error)
  local all = table.concat(lines, ' | '):lower()
  for _, e in ipairs(EXPLAIN) do
    if all:find(e[1]) then return e[2] end
  end
  local fe = tostring(file_error or ''):lower()
  if fe:find('no audio or video') then return 'no llega audio ni vídeo (canal caído o señal cortada)' end
  if fe:find('unrecognized') then return 'formato no reconocido' end
  for _, l in ipairs(lines) do  -- yt-dlp's own words beat generic demuxer noise
    if l:find('^ERROR:') then return (l:gsub('^ERROR:%s*', ''):sub(1, 140)) end
  end
  if lines[1] then return lines[1]:sub(1, 140) end
  return fe ~= '' and fe or 'error desconocido'
end

local function display_name(path)
  local tv = mp.get_property_native('user-data/mu/iptv') or {}
  if type(tv.current) == 'table' and tv.current.url == path and tv.current.name then
    return '«' .. tv.current.name .. '»', true
  end
  if path:find('^%a[%w+.-]*://') then
    local short = path:gsub('^%a[%w+.-]*://', ''):gsub('^www%.', '')
    return #short > 60 and (short:sub(1, 57) .. '…') or short, false
  end
  local _, file = utils.split_path(path)
  return '«' .. (file ~= '' and file or path) .. '»', false
end

if opts.load_errors then
  mp.enable_messages('error')
  mp.register_event('log-message', function(e)
    local prefix = e.prefix or ''
    if prefix:find('^mu[_-]') or prefix == 'uosc' or prefix == 'thumbfast' then return end
    local text = (e.text or ''):gsub('%s+$', '')
    if text == '' or #load.errors >= 20 then return end
    table.insert(load.errors, text)
  end)
  mp.register_event('start-file', function()
    load.path = mp.get_property('path') or ''
    load.errors = {}
  end)
  local function tv_fallbacks()
    local tv = mp.get_property_native('user-data/mu/iptv') or {}
    return tonumber(tv.fallbacks) or 0, tonumber(tv.alternatives_left) or 0
  end
  mp.register_event('end-file', function(ev)
    if ev.reason ~= 'error' then return end
    local path = load.path ~= '' and load.path or ''
    local name, is_tv = display_name(path)
    local reason = explain(load.errors, ev.file_error)
    local raw = table.concat(load.errors, ' | '):sub(1, 400)
    state.last_load_error = { path = path, reason = reason, raw = raw, at = os.time() }
    publish()
    msg.warn('load failed: ' .. path .. ' · ' .. reason .. ' · ' .. raw)
    local before, left = tv_fallbacks()
    -- A TV channel with more sources: mu-iptv tries the next one ("Probando otra fuente…"); only speak if it did not.
    -- Both scripts handle this end-file in no particular order, hence the short wait.
    mp.add_timeout(0.25, function()
      local after = tv_fallbacks()
      if is_tv and (after > before or left > 0) then return end
      local text = '⚠ No se pudo abrir ' .. (name ~= '' and name or 'el archivo') .. ':\n' .. reason
      if is_tv then text = text .. '\nalt+↑ / alt+↓ para probar otro canal' end
      mp.osd_message(text, 8)
    end)
  end)
end

-- ---------------------------------------------------------------------------------------------

publish()
msg.info(string.format('mu-core %s · root=%s · ipc=%s · platform=%s', VERSION, state.root, state.ipc, state.platform))
ensure()
