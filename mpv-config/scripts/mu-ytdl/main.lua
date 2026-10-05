-- mu-ytdl: yt-dlp integration for MPV-UOS. Points mpv's ytdl_hook at the vendored yt-dlp, switches video / audio-only
-- keeping the position, "Calidad" (all formats from `yt-dlp -J` via mpvd), "Descargar" (presets + options) and a
-- live "Descargas" panel fed by mpvd push events. Script name: mu_ytdl.
-- H42 · "Abrir o descargar" is the single door: one text box that takes a link, several links, a playlist, a whole
-- channel, a local path, a .txt of links or, left empty, the clipboard; then one question, play or download. The old
-- doors ("Abrir URL", "Buscar en YouTube", "Pegar") live on as key bindings and inside this one.
-- Bindings: ytdl-gate, ytdl-menu, ytdl-toggle-audio, ytdl-quality, ytdl-download, ytdl-downloads, open-url,
-- yt-search (see input.conf).
-- Everything about ytdl_hook below was verified against the script embedded in mpv 0.41 (docs/MPV_YTDL.md).
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local N = nav.new()
local prefs = require('mu.prefs')
local tr = require('mu.i18n').t

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-ytdl-event'
local URL_EVENT = 'mu-ytdl-url-event'       -- one callback message per palette: `search` events carry no menu type
local SEARCH_EVENT = 'mu-ytdl-search-event'
local MENU = 'mu-ytdl'
local URL_MENU = 'mu-ytdl-url'              -- palettes get their own type (a palette cannot be update-menu'd in/out)
local SEARCH_MENU = 'mu-ytdl-search'
local GATE_MENU = 'mu-ytdl-gate'            -- H42: the single box ("Abrir o descargar")
local GATE_EVENT = 'mu-ytdl-gate-event'
local OUR_MENUS = { [MENU] = true, [URL_MENU] = true, [SEARCH_MENU] = true, [GATE_MENU] = true }

local opts = {
  ytdl_path = '',                   -- override: path(s) for ytdl_hook (default <root>/vendor/bin/yt-dlp, then PATH)
  audio_format = 'bestaudio/best',  -- ytdl-format used in audio-only mode
  osd_seconds = 3,
  notify_done = true,               -- OSD when a download finishes
  panel_hz = 4,                     -- max refresh rate of the downloads panel
  -- JS runtime enabled for ytdl_hook from the very first load (yt-dlp only enables deno by default; a runtime that
  -- is not installed is just reported as unavailable). mpvd later refines it with the exact path. Empty = don't.
  js_runtimes = 'node',
  search_limit = 15,                -- results per YouTube search
  clipboard_text = '',              -- tests: fixed clipboard contents instead of mpv's clipboard/text
}
options.read_options(opts, 'mu-ytdl')
-- remembered choices: the download options (container, subtitles…). H60 dropped "prefer_audio": an internet video
-- always opens with its picture, and taking the video away is a thing of the moment (alt+a, or minimizing).
local P = prefs.ns('mu-ytdl', { dl_options = {}, pick_preset = 'video_1080' })

local platform = mp.get_property_native('platform') or ''
local is_windows = platform == 'windows'
-- The global video format (mpv.conf, uosc's stream quality, or what mu-prefs restores). A file-local format (the
-- audio-only reload, a quality picked for this file) also shows up in `ytdl-format` while that file plays, so only
-- global changes are tracked.
local global_video_format = mp.get_property('ytdl-format') or ''
mp.observe_property('ytdl-format', 'string', function(_, value)
  if value and value ~= '' and not mp.get_property_native('option-info/ytdl-format/set-locally') then
    global_video_format = value
  end
end)
local function default_video_format() return global_video_format end

local ROOT_TITLE = 'Descargas y conversión'

local state = {
  active = false,        -- current file was resolved by ytdl_hook
  url = '',              -- original URL (mpv `path`)
  title = '',
  mode = 'video',        -- video | audio
  format = '',           -- ytdl-format in effect for the current file
  current_ids = {},      -- format ids yt-dlp picked (from ytdl_hook's JSON)
  seed = nil,            -- raw -J JSON captured from ytdl_hook (handed to mpvd to avoid a second run)
  view = '',
  stack = {},
  downloads = {},        -- id -> item (from events / ytdl.downloads.list)
  download_order = {},
  last_event = nil,
  last_error = '',
  hook_path = '',
  dl_options = nil,      -- {container, subtitles, chapters, thumbnail, metadata, sponsorblock, playlist}
  presets = nil,
  info = nil,            -- last ytdl.info result (per url)
  items = {},            -- compact copy of the menu last shown (tests/diagnostics)
  force_open = false,
  clipboard = nil,       -- clipboard URL read when the "Abrir URL" palette opened
  search_query = '',     -- YouTube search palette: last submitted query, its status and result count
  search_status = '',    -- '' | idle | loading | done | error | url
  search_results = 0,
  results = nil,         -- {query, rows} of the last successful search (shown again when coming back to it)
  gate_query = '',       -- H42/A1: lo que se ha pegado en la caja de «Abrir o descargar»
  gate = nil,            -- H42/A2: lo reconocido {kind, url|urls|path, count, list, entries, title}
  site = nil,            -- H37/D4: respuesta de ytdl.site_support para la URL pegada
  pick = nil,            -- H37/D2: {entries, sel, fmt, srt, common, common_srt, source, url, title}
}

local set_button_state -- defined with the bindings below

local function count_active()
  local n = 0
  for _, d in pairs(state.downloads) do
    if d.status == 'queued' or d.status == 'running' then n = n + 1 end
  end
  return n
end

-- codecs decoded in hardware and the playback format that suits them (mpvd ytdl.hw, H31)
local hw = { format = '', names = {} }

local function publish()
  mp.set_property_native('user-data/mu/ytdl', {
    hw_format = hw.format,
    active = state.active, url = state.url, mode = state.mode, format = state.format, title = state.title,
    -- H60 · quitar el vídeo ya no recarga, así que `mode` no lo refleja: lo dice la pista de verdad
    audio_only = mp.get_property('vid') == 'no',
    current_ids = state.current_ids, view = state.view, depth = #state.stack, downloads_active = count_active(),
    last_event = state.last_event or '', last_error = state.last_error, hook_path = state.hook_path,
    items = state.items, search_query = state.search_query, search_status = state.search_status,
    search_results = state.search_results,
    gate_query = state.gate_query, site = state.site,
    gate = state.gate and { kind = state.gate.kind, count = state.gate.count or 1,
                            list = state.gate.list or false } or nil,
    pick = state.pick and { total = #state.pick.entries, marked = (function()
      local n = 0
      for i = 1, #state.pick.entries do if state.pick.sel[i] then n = n + 1 end end
      return n
    end)(), common = state.pick.common, common_srt = state.pick.common_srt, source = state.pick.source } or nil,
  })
end

-- uosc only exposes the open menu's type in user-data, so keep a compact copy of what we showed.
local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 300 then break end
    local names = {}
    for _, a in ipairs(it.actions or {}) do table.insert(names, a.name) end
    table.insert(out, {
      title = it.title or '', hint = it.hint or '', icon = it.icon or '', value = it.value or '',
      active = it.active or false, submenu = it.items ~= nil and #it.items or 0, actions = names,
    })
  end
  state.items = out
end

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

-- ---------------------------------------------------------------------------------------------
-- ytdl_hook wiring: vendored binary first, then whatever `yt-dlp` is on PATH (hot-applied; ytdl_hook observes
-- `options/script-opts` and re-searches the executable on the next load).

local function detect_root()
  local env = os.getenv('MPV_UOS_ROOT')
  if env and env ~= '' then return env end
  local conf = mp.command_native({ 'expand-path', '~~/' }) or ''
  local parent = conf:match('^(.*)[/\\][^/\\]+[/\\]?$')
  return parent or ''
end

local function file_exists(p)
  local info = p ~= '' and utils.file_info(p)
  return info and info.is_file
end

-- ytdl-raw-options set by this script before mpvd answered: mpvd may refine them (e.g. js-runtimes=node:/usr/bin/node
-- or a vendored deno); a value the user set in mpv.conf or on the command line is never touched.
local raw_defaults = {}

local function apply_default_raw_options()
  if opts.js_runtimes == '' then return end
  local current = mp.get_property_native('ytdl-raw-options') or {}
  if current['js-runtimes'] ~= nil then return end
  -- Synchronous on purpose: mpv waits for the scripts' main chunk before loading the first file, so even
  -- `mpv-uos <url>` runs its first yt-dlp with the runtime (mpvd's answer comes ~0.5 s later).
  mp.commandv('change-list', 'ytdl-raw-options', 'append', 'js-runtimes=' .. opts.js_runtimes)
  raw_defaults['js-runtimes'] = opts.js_runtimes
end

local function apply_hook_path()
  apply_default_raw_options()
  local paths = {}
  if opts.ytdl_path ~= '' then
    table.insert(paths, opts.ytdl_path)
  else
    local exe = is_windows and 'yt-dlp.exe' or 'yt-dlp'
    -- a read-only install (the AppImage) keeps its updatable yt-dlp in MPV_UOS_VENDOR_BIN, like mpvd does
    local vendor_bin = os.getenv('MPV_UOS_VENDOR_BIN') or ''
    if vendor_bin ~= '' and file_exists(utils.join_path(vendor_bin, exe)) then
      table.insert(paths, utils.join_path(vendor_bin, exe))
    end
    local root = detect_root()
    if root ~= '' then
      local vendored = utils.join_path(utils.join_path(utils.join_path(root, 'vendor'), 'bin'), exe)
      if file_exists(vendored) then table.insert(paths, vendored) end
    end
    table.insert(paths, 'yt-dlp')
  end
  local value = table.concat(paths, is_windows and ';' or ':')
  mp.commandv('change-list', 'script-opts', 'append', 'ytdl_hook-ytdl_path=' .. value)
  state.hook_path = value
  msg.info('ytdl_hook-ytdl_path=' .. value)
end

local function apply_raw_options(raw)
  local current = mp.get_property_native('ytdl-raw-options') or {}
  for k, v in pairs(raw or {}) do
    v = tostring(v)
    local cur = current[k]
    -- only keys nobody set, or still holding our own early default (`append` replaces an existing key)
    if cur == nil or (raw_defaults[k] ~= nil and cur == raw_defaults[k]) then
      raw_defaults[k] = nil
      if cur ~= v then
        mp.commandv('change-list', 'ytdl-raw-options', 'append', k .. '=' .. v)
        msg.info('ytdl-raw-options += ' .. k .. '=' .. v)
      end
    end
  end
end

-- «usar mi sesión del navegador» (H19): the same --cookies-from-browser for playback (ytdl_hook) as for downloads;
-- removed again when the user turns it off. A value set by the user in mpv.conf is never touched.
local cookies_applied = nil
local function apply_cookies(browser)
  local current = (mp.get_property_native('ytdl-raw-options') or {})['cookies-from-browser']
  if current ~= nil and current ~= cookies_applied then return end
  if browser and browser ~= '' then
    if current ~= browser then
      mp.commandv('change-list', 'ytdl-raw-options', 'append', 'cookies-from-browser=' .. browser)
    end
    cookies_applied = browser
  elseif cookies_applied then
    mp.commandv('change-list', 'ytdl-raw-options', 'remove', 'cookies-from-browser')
    cookies_applied = nil
  end
end

local hook_synced = false
local function sync_hook_with_mpvd()
  if hook_synced or not rpc.connected() then return end
  hook_synced = true
  rpc.call('ytdl.hw', nil, function(err, res)
    if err or type(res) ~= 'table' then return end
    hw.format = res.playback_format or ''
    hw.names = res.names or {}
    publish()
  end, 30)
  rpc.call('ytdl.hook', nil, function(err, cfg)
    if err then hook_synced = false; return end
    apply_raw_options(cfg.raw_options)
    apply_cookies(cfg.cookies_browser)
    if opts.ytdl_path == '' and cfg.ytdl_path and cfg.ytdl_path ~= '' and not state.hook_path:find(cfg.ytdl_path, 1, true) then
      -- mpvd found something better (e.g. $MPV_UOS_YTDLP or a freshly updated vendor copy): put it first.
      local value = cfg.ytdl_path .. (is_windows and ';' or ':') .. state.hook_path
      mp.commandv('change-list', 'script-opts', 'append', 'ytdl_hook-ytdl_path=' .. value)
      state.hook_path = value
      publish()
    end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- current file: is it a ytdl stream? which formats did yt-dlp pick?

local function split_ids(fid)
  local ids = {}
  for id in tostring(fid or ''):gmatch('[^+]+') do table.insert(ids, id) end
  return ids
end

local function is_network_url(p)
  return p ~= nil and (p:match('^https?://') ~= nil or p:match('^ytdl://') ~= nil)
end

mp.register_event('file-loaded', function()
  local path = mp.get_property('path') or ''
  local res = mp.get_property_native('user-data/mpv/ytdl/json-subprocess-result')
  local vid = mp.get_property_native('vid')
  state.active = is_network_url(path) and type(res) == 'table' and res.status == 0
  state.url = state.active and path or ''
  state.mode = (vid == false or vid == 'no') and 'audio' or 'video'
  state.format = mp.get_property('options/ytdl-format') or ''
  state.current_ids = {}
  state.seed = nil
  state.title = mp.get_property('media-title') or ''
  if state.active and type(res.stdout) == 'string' and #res.stdout > 0 then
    state.seed = res.stdout
    local info = utils.parse_json(res.stdout)
    if type(info) == 'table' then
      state.current_ids = split_ids(info.format_id)
      if type(info.requested_formats) == 'table' then
        state.current_ids = {}
        for _, f in ipairs(info.requested_formats) do table.insert(state.current_ids, tostring(f.format_id)) end
      end
      if info.title then state.title = info.title end
    end
  end
  if state.info and state.info.url ~= state.url then state.info = nil end
  publish()
  set_button_state()
end)

P:on_change(function(reason)
  if reason == 'reset' then state.dl_options = nil end  -- back to mpvd's defaults on next use
end)

mp.register_event('end-file', function()
  state.active = false
  state.url = ''
  state.seed = nil
  publish()
end)

-- A URL the stable yt-dlp could not open is tried once more with the nightly build (H19): sites change faster than
-- releases. ytdl_hook's search path gets the nightly first for that one load and goes back to normal afterwards.
local nightly = { tried = {}, active_for = nil, saved_path = nil, loading = '', ytdl_status = nil }

-- ytdl_hook deletes its result in on_after_end_file, before end-file reaches scripts: keep what it said while loading
mp.register_event('start-file', function()
  nightly.loading = mp.get_property('path') or ''
  nightly.ytdl_status = nil
end)
mp.observe_property('user-data/mpv/ytdl/json-subprocess-result', 'native', function(_, v)
  if type(v) == 'table' then nightly.ytdl_status = v.status end
end)
-- the observer alone races with end-file under load (property changes and events are not ordered): read the result
-- synchronously right after ytdl_hook ran (on_load for known sites, on_load_fail for the rest; hooks run in ascending
-- priority) and before its on_after_end_file deletes it
local function read_ytdl_result()
  local v = mp.get_property_native('user-data/mpv/ytdl/json-subprocess-result')
  if type(v) == 'table' then nightly.ytdl_status = v.status end
end
mp.add_hook('on_load', 11, read_ytdl_result)
mp.add_hook('on_load_fail', 11, read_ytdl_result)
mp.add_hook('on_after_end_file', 1, read_ytdl_result)

local function restore_hook_path()
  if nightly.saved_path then
    mp.commandv('change-list', 'script-opts', 'append', 'ytdl_hook-ytdl_path=' .. nightly.saved_path)
    state.hook_path = nightly.saved_path
    nightly.saved_path, nightly.active_for = nil, nil
    publish()
  end
end

mp.register_event('end-file', function(ev)
  local url = ev and ev.reason == 'error' and nightly.loading or ''
  if nightly.active_for and nightly.active_for ~= url then restore_hook_path() end
  if url == '' or not is_network_url(url) or nightly.tried[url] or not rpc.connected() then return end
  if nightly.ytdl_status == nil or nightly.ytdl_status == 0 then return end   -- not a yt-dlp failure
  nightly.tried[url] = true
  state.last_event = 'nightly-retry'
  publish()
  rpc.call('ytdl.nightly', nil, function(err, nb)
    if err or not nb or not nb.available then
      msg.info('nightly yt-dlp not available: ' .. (err and err.message or 'no'))
      return
    end
    nightly.saved_path = nightly.saved_path or state.hook_path
    nightly.active_for = url
    local value = nb.path .. (is_windows and ';' or ':') .. nightly.saved_path
    mp.commandv('change-list', 'script-opts', 'append', 'ytdl_hook-ytdl_path=' .. value)
    state.hook_path = value
    publish()
    osd(tr('Probando con yt-dlp nightly (%s)…'):format(nb.version or ''))
    mp.commandv('loadfile', url, 'replace')
  end, 120)
end)

mp.register_event('file-loaded', function()
  local path = mp.get_property('path') or ''
  if nightly.active_for and nightly.active_for ~= path then restore_hook_path() end
  if nightly.active_for == path then
    state.last_event = 'nightly-ok'
    publish()
    mp.add_timeout(1, restore_hook_path)   -- the next URL goes through the stable build again
  end
end)

-- ---------------------------------------------------------------------------------------------
-- reload with another ytdl-format at the same position (pause/speed/volume are global → preserved)

local function reload(format, audio_only)
  if not state.active then
    osd(tr('No hay ningún vídeo de yt-dlp cargado'))
    return
  end
  local pos = mp.get_property_number('time-pos') or 0
  local o = { ['ytdl-format'] = format, start = string.format('%.3f', math.max(0, pos - 0.001)) }
  o.vid = audio_only and 'no' or 'auto'
  local url = state.url
  state.mode = audio_only and 'audio' or 'video'
  state.format = format
  state.active = false -- until file-loaded confirms the reload (tests wait for active again)
  publish()
  local ok = mp.command_native({ 'loadfile', url, 'replace', -1, o })
  if ok == nil then osd(tr('No se pudo recargar %s'):format(url)) end
end

-- ¿Está sonando solo el audio? Lo dice la pista, no lo que creamos recordar.
local function audio_only()
  return mp.get_property('vid') == 'no'
end

-- H60 · quitar el vídeo es deseleccionar la pista, y punto: para este fichero (file-local vid=no), así que el
-- siguiente se abre limpio. Antes, en internet, esto recargaba con `bestaudio/best`; medido, no hacía falta —
-- deseleccionar la pista ya corta la descarga del vídeo (35 % de los datos) y tarda 0,03 s en vez de segundos,
-- sin contar una reproducción nueva ni obligar a la sala a rehacer su relay. ADR-096.
local function toggle_audio()
  local path = mp.get_property('path') or ''
  if path == '' then osd(tr('No hay nada abierto')) return end
  local vid = mp.get_property('vid')
  if vid == 'no' then
    mp.set_property('file-local-options/vid', 'auto')
    osd(tr('🎬 Vídeo'))
  else
    local v = mp.get_property_native('current-tracks/video')
    if type(v) ~= 'table' or v.image then osd(tr('Este archivo no tiene vídeo')) return end
    mp.set_property('file-local-options/vid', 'no')
    osd(tr('🎧 Solo audio: el vídeo no se decodifica'))
  end
end

-- mpv.conf's ytdl-format: while it is still this one (the user did not pick another, which mu-prefs would remember),
-- internet videos use the format that suits this machine's hardware decoding (H31, mpvd ytdl.hw)
local FACTORY_FORMAT = 'bestvideo[height<=?1080][vcodec^=avc1]+bestaudio/bestvideo[height<=?1080]+bestaudio/best'

-- Runs before ytdl_hook's on_load (priority 10): a format chosen for this file (loadfile options) is left alone.
mp.add_hook('on_load', 9, function()
  local path = mp.get_property('path') or ''
  if not (path:match('^https?://') or path:match('^ytdl://')) then return end
  if mp.get_property_native('option-info/ytdl-format/set-locally') then return end
  if hw.format ~= '' and mp.get_property('ytdl-format') == FACTORY_FORMAT then
    mp.set_property('file-local-options/ytdl-format', hw.format)
  end
end)

-- ---------------------------------------------------------------------------------------------
-- menus

local function base_menu(title, items, extra)
  local menu = {
    type = MENU, title = title, items = items, callback = { SCRIPT, EVENT },
    on_close = 'callback', keep_open = false, search_submenus = true,
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return N:frame(menu, state.stack)
end

local function show(title, items, extra)
  remember(items)
  publish()
  if uosc.open_type() == MENU and not state.force_open then
    uosc.update(base_menu(title, items, extra))
  else
    uosc.open(base_menu(title, items, extra))
  end
  state.force_open = false
end

local views = {}

local function open_view(spec, push)
  if push ~= false then table.insert(state.stack, spec) end
  state.view = spec.name
  state.items = {}   -- H63: nunca se publica una vista nueva con las filas de la anterior
  publish()
  views[spec.name](spec.args or {})
end

local function reopen_current()
  local spec = state.stack[#state.stack]
  if spec then open_view(spec, false) end
end

local function require_mpvd(title)
  if rpc.connected() then return true end
  local core = mp.get_property_native('user-data/mu/core') or {}
  show(title, {
    { title = tr('mpvd no está disponible'), hint = core.mpvd or '', icon = 'error', selectable = false, muted = true },
    { title = tr('Reintentar conexión'), icon = 'refresh', value = { view = 'root', ensure = true } },
  })
  return false
end

local function short_format()
  if #state.current_ids > 0 then return table.concat(state.current_ids, '+') end
  return state.format ~= '' and state.format or 'auto'
end

-- Key bound to one of our script-bindings in input.conf (for hints), or nil.
local function key_for(name)
  local cmd = 'script-binding ' .. SCRIPT .. '/' .. name
  for _, b in ipairs(mp.get_property_native('input-bindings') or {}) do
    if b.cmd == cmd and not b.is_weak then return b.key end
  end
  return nil
end

views.root = function()
  -- H42/A1-A3 · una sola puerta. Antes había tres filas distintas aquí («Abrir URL…», «Buscar en YouTube…» y
  -- «Descargar…») y había que saber de antemano qué ibas a pegar. Siguen en el teclado (ctrl+u, ctrl+f).
  local items = {
    { title = tr('Abrir o descargar…'), hint = key_for('ytdl-gate') or 'un enlace, varios, una lista, un archivo',
      icon = 'add_link', value = { view = 'gate' }, separator = true },
  }
  if state.active then
    table.insert(items, {
      title = audio_only() and 'Volver al vídeo' or 'Quitar el vídeo',
      hint = audio_only() and 'ahora: solo audio' or 'ahora: con imagen',
      icon = audio_only() and 'movie' or 'videocam_off',
      value = { toggle = true },
    })
    table.insert(items, { title = tr('Calidad'), hint = short_format(), icon = 'high_quality', value = { view = 'quality' } })
    table.insert(items, { title = tr('Descargar'), icon = 'download', value = { view = 'download' } })
  else
    table.insert(items, { title = tr('Con una URL abierta: solo audio, calidad y descargar'),
                          icon = 'info', selectable = false, muted = true, align = 'center' })
  end
  local n = count_active()
  table.insert(items, { title = tr('Descargas'), hint = n > 0 and (tostring(n) .. ' activas') or nil, icon = 'downloading',
                        value = { view = 'downloads' } })
  -- H20: conversions and the unified tasks panel live in mu-convert (opened as a child: ⌫ comes back here)
  table.insert(items, { title = tr('Convertir…'), hint = tr('MP4, más pequeño, solo audio, GIF'), icon = 'transform',
                        value = { child = 'convert-menu' } })
  table.insert(items, { title = tr('Tareas'), hint = tr('descargas y conversiones'), icon = 'pending_actions',
                        value = { child = 'tasks-menu' }, separator = true })
  table.insert(items, { title = tr('Ajustes de descarga'), icon = 'tune', value = { view = 'dl_settings' } })
  table.insert(items, { title = tr('Estado de yt-dlp'), icon = 'settings', value = { view = 'status' } })
  show(ROOT_TITLE, items)
end

-- quality ---------------------------------------------------------------------------------------

local function is_current(id)
  for _, cur in ipairs(state.current_ids) do if cur == id then return true end end
  return false
end

local function quality_item(row, group)
  local solo_audio = row.kind == 'audio'
  local format
  if row.kind == 'video' then format = row.id .. '+ba/' .. row.id else format = row.id end
  return {
    title = row.label, hint = row.hint ~= '' and row.hint or nil, active = is_current(row.id),
    icon = solo_audio and 'audiotrack' or (row.kind == 'video' and 'videocam' or 'movie'),
    value = { quality = { format = format, audio_only = solo_audio, id = row.id, group = group } },
    actions = { { name = 'download', icon = 'download', label = tr('Descargar este formato') } },
  }
end

local function quality_items(info)
  local groups = {
    { key = 'combined', title = tr('Vídeo + audio') }, { key = 'video', title = tr('Solo vídeo (+ mejor audio)') },
    { key = 'audio', title = tr('Solo audio') },
  }
  local sections = {}
  for _, g in ipairs(groups) do
    local rows = info.formats and info.formats[g.key] or {}
    if #rows > 0 then
      local items = {}
      for _, r in ipairs(rows) do table.insert(items, quality_item(r, g.key)) end
      table.insert(sections, { title = g.title, hint = tostring(#rows), items = items })
    end
  end
  if #sections == 1 then return sections[1].items end
  table.insert(sections, 1, { title = tr('Automático (mejor ≤1080p)'), hint = default_video_format(), icon = 'auto_awesome',
                              value = { quality = { format = default_video_format(), audio_only = false, id = 'auto' } } })
  return sections
end

local function with_info(cb)
  if state.info and state.info.url == state.url then cb(nil, state.info) return end
  rpc.call('ytdl.info', { url = state.url, seed = state.seed }, function(err, info)
    if not err then
      info.url = state.url
      state.info = info
    end
    cb(err, info)
  end, 150)
end

views.quality = function()
  if not state.active then show(tr('Calidad'), uosc.message_items(tr('Abre una URL de yt-dlp primero'), 'info')) return end
  if not require_mpvd('Calidad') then return end
  show(tr('Calidad'), uosc.loading_items(tr('Consultando formatos…')))
  with_info(function(err, info)
    if err then show(tr('Calidad'), uosc.message_items(fail(err, 'ytdl.info'), 'error')) return end
    local items = quality_items(info)
    if #items == 0 then items = uosc.message_items(tr('yt-dlp no devolvió formatos'), 'info') end
    show(tr('Calidad · %s'):format(info.title or ''), items, {
      footnote = tr('Enter cambia en caliente · Tab descarga ese formato · / busca · ⌫ atrás'),
    })
  end)
end

local function set_quality(q)
  reload(q.format, q.audio_only)
  osd((q.audio_only and '🎧 ' or '🎬 ') .. 'Calidad: ' .. q.format)
end

-- download --------------------------------------------------------------------------------------

local function default_dl_options(settings)
  return {
    container = settings.container or 'mp4', subtitles = settings.subtitles == true,
    chapters = settings.chapters ~= false, thumbnail = settings.thumbnail == true,
    metadata = settings.metadata ~= false, sponsorblock = settings.sponsorblock or 'none', playlist = false,
    subs_mode = 'embed', sub_langs = settings.sub_langs or 'orig,es.*,en.*',
  }
end

local function with_presets(cb)
  if state.presets then cb(nil, state.presets) return end
  rpc.call('ytdl.presets', nil, function(err, res)
    if not err then
      state.presets = res
      if not state.dl_options then
        state.dl_options = default_dl_options(res.settings or {})
        for k, v in pairs(P:get('dl_options')) do state.dl_options[k] = v end  -- the user's last choices
        state.dl_options.playlist = false  -- "whole playlist" is per download, never remembered
      end
    end
    cb(err, res)
  end)
end

local function bool_hint(v) return v and '✓' or '–' end

local function opt_item(title, hint, icon, name)
  return { title = title, hint = hint, icon = icon, value = { opt = name }, keep_open = true }
end

local SB_HINT = { none = 'no', mark = 'marcar capítulos', remove = 'quitar segmentos' }
-- subtitles: off → inside the video → .srt next to it (H19); languages cycle through these
local SUBS_HINT = { off = 'no', embed = 'dentro del vídeo', file = 'archivo SRT aparte' }
local SUB_LANGS = {
  { 'orig,es.*,en.*', 'originales + es + en' }, { 'orig', 'solo el original' }, { 'es.*', 'español' },
  { 'en.*', 'inglés' }, { 'all,-live_chat', 'todos' },
}
local function sub_langs_label(v)
  for _, l in ipairs(SUB_LANGS) do if l[1] == v then return l[2] end end
  return v or ''
end

local function options_items(o)
  return {
    opt_item('Contenedor de vídeo', o.container, 'inventory_2', 'container'),
    opt_item('Subtítulos', SUBS_HINT[o.subtitles and (o.subs_mode or 'embed') or 'off'], 'subtitles', 'subtitles'),
    opt_item('Idiomas de los subtítulos', sub_langs_label(o.sub_langs), 'translate', 'sub_langs'),
    opt_item('Capítulos', bool_hint(o.chapters), 'bookmarks', 'chapters'),
    opt_item('Miniatura como portada', bool_hint(o.thumbnail), 'image', 'thumbnail'),
    opt_item('Metadatos', bool_hint(o.metadata), 'sell', 'metadata'),
    opt_item('SponsorBlock', SB_HINT[o.sponsorblock] or 'no', 'block', 'sponsorblock'),
    opt_item('Lista de reproducción completa', bool_hint(o.playlist), 'playlist_play', 'playlist'),
  }
end

local function toggle_option(name)
  local o = state.dl_options
  if not o then return end  -- download options not loaded yet (mpvd has not answered ytdl.presets)
  if name == 'container' then
    local order = { mp4 = 'mkv', mkv = 'webm', webm = 'mp4' }
    o.container = order[o.container] or 'mp4'
  elseif name == 'sponsorblock' then
    local order = { none = 'mark', mark = 'remove', remove = 'none' }
    o.sponsorblock = order[o.sponsorblock] or 'none'
  elseif name == 'subtitles' then
    if not o.subtitles then o.subtitles, o.subs_mode = true, 'embed'
    elseif o.subs_mode ~= 'file' then o.subs_mode = 'file'
    else o.subtitles, o.subs_mode = false, 'embed' end
  elseif name == 'sub_langs' then
    local nxt = 1
    for i, l in ipairs(SUB_LANGS) do if l[1] == o.sub_langs then nxt = i % #SUB_LANGS + 1 end end
    o.sub_langs = SUB_LANGS[nxt][1]
  else
    o[name] = not o[name]
  end
end

-- args.url/args.title: download something other than the current file (a YouTube search result).
views.download = function(args)
  local target = args.url
  if not target and not state.active then
    show(tr('Descargar'), uosc.message_items(tr('Abre una URL de yt-dlp primero'), 'info'))
    return
  end
  local target_title = target and (args.title or target) or state.title
  if not require_mpvd('Descargar') then return end
  show(tr('Descargar'), uosc.loading_items())
  with_presets(function(err, res)
    if err then show(tr('Descargar'), uosc.message_items(fail(err, 'ytdl.presets'), 'error')) return end
    local o = state.dl_options
    -- H31: the same label as in «Calidad». The download asks for the codec this machine decodes in hardware, so if we
    -- know of any, what comes down plays «fluido en tu equipo»; if we know of none, it will be decoded by the processor.
    local hw_hint = #hw.names > 0 and (hw.names[1] .. ' · fluido en tu equipo') or 'exigente (por procesador)'
    local video, audio, subs = {}, {}, {}
    for _, p in ipairs(res.presets or {}) do
      local it = { title = p.title, icon = p.group == 'audio' and 'audiotrack' or 'movie',
                   value = { preset = p.id, url = target, title = target and target_title or nil } }
      if p.group == 'video' then it.hint = hw_hint end
      if p.group == 'audio' then table.insert(audio, it)
      elseif p.group == 'subs' then
        it.icon, it.hint = tr('subtitles'), sub_langs_label(o.sub_langs)
        table.insert(subs, it)
      else table.insert(video, it) end
    end
    local items = {
      { title = tr('Vídeo'), hint = o.container .. ' · ' .. hw_hint, items = video },
      { title = tr('Audio'), hint = tostring(#audio), items = audio },
      { title = tr('Opciones'),
        hint = (o.subtitles and 'subs ' or '') .. (o.sponsorblock ~= 'none' and 'SB ' or '') .. o.container,
        items = options_items(o) },
    }
    for i, it in ipairs(subs) do table.insert(items, 2 + i, it) end
    show(tr('Descargar · %s'):format(target_title or ''), items, {
      footnote = tr('Enter descarga con el preset · Opciones: Enter alterna · ⌫ atrás'),
    })
  end)
end

local function start_download(params)
  params.url = params.url or state.url
  params.title = params.title or state.title
  params.options = state.dl_options and {
    container = state.dl_options.container, subtitles = state.dl_options.subtitles, chapters = state.dl_options.chapters,
    thumbnail = state.dl_options.thumbnail, metadata = state.dl_options.metadata,
    subs_mode = state.dl_options.subs_mode, sub_langs = state.dl_options.sub_langs,
    sponsorblock = state.dl_options.sponsorblock, playlist = state.dl_options.playlist,
  } or nil
  for _, k in ipairs({ 'container', 'playlist', 'playlist_items' }) do  -- with a preset only `options` count
    if params[k] ~= nil then
      params.options = params.options or {}
      params.options[k] = params[k]
      params[k] = nil
    end
  end
  rpc.call('ytdl.download', params, function(err, item)
    if err then osd(tr('Descarga: %s'):format(fail(err, 'ytdl.download'))) return end
    state.downloads[item.id] = item
    table.insert(state.download_order, 1, item.id)
    publish()
    set_button_state()
    osd(tr('⬇ En cola: %s · %s\n→ %s'):format(item.title or item.url, item.description or '',
                                                item.out_dir or ''))
  end, 30)
end

-- downloads panel ---------------------------------------------------------------------------------

local STATUS_ICON = {
  queued = 'schedule', running = 'downloading', done = 'check_circle', failed = 'error', cancelled = 'cancel',
}

local function download_item(d)
  local actions = {}
  if d.status == 'queued' or d.status == 'running' then
    table.insert(actions, { name = 'cancel', icon = 'cancel', label = tr('Cancelar') })
  else
    table.insert(actions, { name = 'retry', icon = 'refresh', label = tr('Repetir') })
    table.insert(actions, { name = 'remove', icon = 'delete', label = tr('Quitar de la lista') })
  end
  local hint = d.message or d.status
  if d.status == 'failed' and d.error and d.error ~= '' then hint = tr('error') end
  return {
    title = (d.title or d.url or '?') .. '  ·  ' .. (d.description or ''),
    hint = hint, icon = STATUS_ICON[d.status] or 'help', value = { download = d.id },
    muted = d.status == 'cancelled' or nil, bold = d.status == 'running' or nil, actions = actions,
    keep_open = true,
  }
end

local function downloads_items()
  local items = {}
  local seen = {}
  for _, id in ipairs(state.download_order) do
    local d = state.downloads[id]
    if d and not seen[id] then
      seen[id] = true
      table.insert(items, download_item(d))
    end
  end
  if #items == 0 then
    items = uosc.message_items(tr('Sin descargas. Pega algo en «Abrir o descargar»'), 'download')
  else
    table.insert(items, { title = tr('Limpiar terminadas'), icon = 'cleaning_services', value = { clear = true },
                          separator = true, actions = {}, keep_open = true })
  end
  -- H42/A4 · el panel web es un enlace que hay que llevarse a otro aparato: se abre aquí o se copia con Tab
  table.insert(items, { title = tr('Panel de descargas en el navegador'), hint = tr('Tab copia el enlace'),
                        icon = 'open_in_browser', value = { panel = true }, separator = #items > 0,
                        actions = { { name = 'copy', icon = 'content_copy', label = tr('Copiar el enlace') } } })
  return items
end

local function merge_list(rows)
  state.downloads = {}
  state.download_order = {}
  for _, d in ipairs(rows) do
    state.downloads[d.id] = d
    table.insert(state.download_order, d.id)
  end
end

views.downloads = function()
  if not require_mpvd('Descargas') then return end
  rpc.call('ytdl.downloads.list', nil, function(err, rows)
    if err then show(tr('Descargas'), uosc.message_items(fail(err, 'ytdl.downloads.list'), 'error')) return end
    merge_list(rows)
    publish()
    show(tr('Descargas'), downloads_items(),
         { footnote = tr('Tab: cancelar / repetir / quitar · Enter muestra detalles · ⌫ atrás') })
  end)
end

local panel_timer = nil
local function refresh_panel()
  if state.view ~= 'downloads' or uosc.open_type() ~= MENU then return end
  if panel_timer then return end
  panel_timer = mp.add_timeout(1 / math.max(1, opts.panel_hz), function()
    panel_timer = nil
    if state.view == 'downloads' and uosc.open_type() == MENU then
      local items = downloads_items()
      remember(items)
      publish()
      uosc.update(base_menu('Descargas', items, { footnote = tr('Tab: cancelar / repetir / quitar · ⌫ atrás') }))
    end
  end)
end

local function download_details(id)
  rpc.call('ytdl.downloads.get', { id = id }, function(err, d)
    if err then osd(fail(err, 'ytdl.downloads.get')) return end
    local lines = { d.title or d.url, d.description or '', 'Estado: ' .. (d.message or d.status) }
    if d.error and d.error ~= '' then table.insert(lines, d.error) end
    for _, f in ipairs(d.outputs or {}) do table.insert(lines, '→ ' .. f) end
    if #(d.outputs or {}) == 0 then table.insert(lines, 'Carpeta: ' .. (d.out_dir or '')) end
    mp.osd_message(table.concat(lines, '\n'), 6)
  end)
end

local function download_action(id, action)
  local method = ({ cancel = 'ytdl.downloads.cancel', retry = 'ytdl.downloads.retry', remove = 'ytdl.downloads.remove' })[action]
  if not method then return end
  rpc.call(method, { id = id }, function(err, res)
    if err then osd(fail(err, method)) return end
    if action == 'remove' then state.downloads[id] = nil end
    if action == 'retry' and type(res) == 'table' then state.downloads[id] = res end
    if state.view == 'downloads' then open_view({ name = 'downloads' }, false) end
  end)
end

-- status ------------------------------------------------------------------------------------------

views.status = function()
  if not require_mpvd('Estado de yt-dlp') then return end
  show(tr('Estado de yt-dlp'), uosc.loading_items())
  rpc.call('ytdl.status', nil, function(err, st)
    if err then show(tr('Estado de yt-dlp'), uosc.message_items(fail(err, 'ytdl.status'), 'error')) return end
    local b = st.binary or {}
    local up = st.update or {}
    local s = st.settings or {}
    local items = {
      { title = tr('Versión'), hint = st.version or 'no encontrado', icon = 'info', selectable = false },
      { title = tr('Binario'), hint = (b.source or '') .. ' · ' .. (b.path or ''), icon = 'terminal', selectable = false },
      { title = tr('Runtime JS'), icon = 'javascript', selectable = false,
        hint = b.js_runtime and (b.js_runtime.name .. ' ' .. (b.js_runtime.version or ''))
          or 'ninguno (YouTube puede omitir formatos)' },
      { title = tr('Decodifica por hardware'), icon = 'memory', selectable = false,
        hint = #hw.names > 0 and table.concat(hw.names, ', ') or 'desconocido (se usa H.264)' },
      { title = tr('Versión nightly (reintentos)'), icon = 'nightlight', selectable = false,
        hint = (st.nightly and st.nightly.version ~= '') and st.nightly.version or 'se descarga si hace falta' },
      { title = tr('Suplantación de navegador (TikTok…)'), icon = 'masks', selectable = false,
        hint = st.impersonate and 'disponible' or 'falta curl_cffi (tools/install.sh --extras)' },
      { title = tr('Carpeta de vídeo'), hint = s.video_dir_resolved or '', icon = 'folder', selectable = false },
      { title = tr('Carpeta de audio'), hint = s.audio_dir_resolved or '', icon = 'folder', selectable = false },
      { title = tr('Actualización automática diaria'), hint = bool_hint(up.auto), icon = 'update',
        value = { setting = 'auto_update' }, keep_open = true },
      { title = tr('Buscar actualización ahora'), hint = up.latest and up.latest ~= '' and ('última: ' .. up.latest) or nil,
        icon = 'system_update_alt', value = { update = 'check' } },
    }
    if up.update_available then
      table.insert(items, { title = tr('Instalar %s'):format(up.latest), icon = 'download',
                            value = { update = 'apply' }, bold = true })
    end
    if up.error and up.error ~= '' then
      table.insert(items, { title = up.error, icon = 'error', selectable = false, muted = true })
    end
    show(tr('Estado de yt-dlp'), items)
  end)
end

local function update_action(kind)
  osd(kind == 'apply' and 'Instalando yt-dlp…' or 'Buscando actualización…')
  local function done(err, st)
    if err then osd(fail(err, 'ytdl.update')) return end
    if st.error and st.error ~= '' then osd(tr('yt-dlp: %s'):format(st.error))
    elseif kind == 'apply' then osd(tr('yt-dlp actualizado a %s'):format(st.installed or '?'))
    elseif st.update_available then
      osd(tr('Disponible yt-dlp %s (instalado %s)'):format(st.latest, st.installed or '?'))
    else osd(tr('yt-dlp al día (%s)'):format(st.installed or '?')) end
    if state.view == 'status' then open_view({ name = 'status' }, false) end
  end
  -- 'apply' takes no parameters: sending force there answered «parámetros no válidos» and never installed anything
  if kind == 'apply' then rpc.call('ytdl.update.apply', nil, done, 600)
  else rpc.call('ytdl.update.check', { force = true }, done, 600) end
end

-- ---------------------------------------------------------------------------------------------
-- "Abrir URL" and "Buscar en YouTube" palettes

local URL_SCHEMES = {
  http = true, https = true, ytdl = true, rtmp = true, rtmps = true, rtmpe = true, rtsp = true, rtsps = true,
  rtp = true, srt = true, udp = true, mms = true, mmsh = true, ftp = true, ftps = true, sftp = true, file = true,
}

local function trim(s)
  return (tostring(s or ''):gsub('^%s+', ''):gsub('%s+$', ''))
end

-- Something typed or pasted → a URL mpv can open, or nil. Bare "youtu.be/…" or "www.…" get https:// prepended.
local function as_url(text)
  local s = trim(text)
  if s == '' or s:find('%s') then return nil end
  local scheme = s:match('^(%a[%w+.-]*)://')
  if scheme then return URL_SCHEMES[scheme:lower()] and s or nil end
  if s:match('^www%.[%w-]+%.%a') or s:match('^[%w-]+%.[%w.-]*%a/%S*$') then return 'https://' .. s end
  return nil
end

-- Whatever is in the clipboard (mpv 0.41 `clipboard/text`; empty without a backend). The single box classifies the
-- whole thing, so it has to see every line, not just the first one.
local function clipboard_raw()
  local text = opts.clipboard_text
  if text == '' then
    local ok, value = pcall(mp.get_property, 'clipboard/text')
    text = ok and value or ''
  end
  return tostring(text or '')
end

-- URL on the first line of the clipboard, or nil.
local function clipboard_url()
  return as_url(clipboard_raw():match('^%s*([^\r\n]*)'))
end

local function ellipsize(s, n)
  if #s <= n then return s end
  return s:sub(1, n - 1) .. '…'
end

local function fmt_duration(sec)
  sec = tonumber(sec)
  if not sec or sec <= 0 then return nil end
  sec = math.floor(sec + 0.5)
  local h, m, s = math.floor(sec / 3600), math.floor(sec % 3600 / 60), sec % 60
  if h > 0 then return string.format('%d:%02d:%02d', h, m, s) end
  return string.format('%d:%02d', m, s)
end

local function close_menus()
  for t in pairs(OUR_MENUS) do uosc.close(t) end
end

local function load_url(url, append, title)
  if append then
    mp.commandv('loadfile', url, 'append-play')
    osd(tr('➕ Añadido a la lista: %s'):format(title or url))
  else
    mp.commandv('loadfile', url, 'replace')
    osd(tr('Abriendo… %s'):format(title or url))
  end
end

local ITEM_ACTIONS = {
  { name = 'append', icon = 'playlist_add', label = tr('Añadir a la lista') },
  { name = 'download', icon = 'download', label = tr('Descargar') },
}

local function open_item(url)
  return { title = tr('Abrir %s'):format(url), icon = 'play_arrow', value = { open = url }, actions = ITEM_ACTIONS }
end

-- open_url: palette with instant updates (search_debounce 0): what is typed becomes "Abrir <url>" or
-- "Buscar «texto» en YouTube"; the clipboard URL (read once when the palette opens) is offered as "Pegar: …".
local function url_items(query)
  local typed = trim(query)
  local url = as_url(typed)
  local items = {}
  if url then
    table.insert(items, open_item(url))
  elseif typed ~= '' then
    table.insert(items, { title = tr('Buscar «%s» en YouTube'):format(typed), icon = 'youtube_searched_for',
                          value = { yt_search = typed } })
  end
  if state.clipboard and state.clipboard ~= url then
    table.insert(items, { title = tr('Pegar: %s'):format(ellipsize(state.clipboard, 100)), icon = 'content_paste',
                          value = { open = state.clipboard }, actions = ITEM_ACTIONS })
  end
  if #items == 0 then
    items = uosc.message_items(tr('YouTube, Twitch, archive.org, radios… o texto para buscarlo en YouTube'), 'link')
  end
  return items
end

local function url_menu(items, extra)
  local menu = {
    type = URL_MENU, title = tr('Pega o escribe una URL y pulsa Enter'), items = items, callback = { SCRIPT, URL_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    footnote = tr('Enter abre · Tab: añadir a la lista o descargar · ctrl+v pega · ⌫ atrás'),
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return menu
end

views.open_url = function(args)
  state.clipboard = clipboard_url()
  local query = trim(args.query)
  local items = url_items(query)
  remember(items)
  publish()
  uosc.open(url_menu(items, { search_suggestion = query ~= '' and query or nil }))
end

-- update-menu goes out before the state is published: whoever sees the new state (tests) and then sends a key to
-- uosc has its key queued after the update.
local function url_typed(query)
  local items = url_items(query)
  remember(items)
  uosc.update(url_menu(items))
  publish()
end

-- ---------------------------------------------------------------------------------------------
-- H19: several URLs at once, a list or channel with check boxes, download settings

local function urls_in(text)
  local out, seen = {}, {}
  for u in tostring(text or ''):gmatch('https?://[^%s"\'<>]+') do
    u = u:gsub('[.,;)]+$', '')
    if not seen[u] then seen[u] = true; table.insert(out, u) end
  end
  return out
end

-- D1 · la caja donde se pega lo que sea. Lo que se teclea se clasifica aquí mismo: una ruta de .txt, un enlace, veinte
-- enlaces, o una URL de las que yt-dlp no sabe abrir (guardados de Instagram, favoritos de TikTok: D4), que se dice
-- antes de intentarlo en vez de fallar con un error de yt-dlp.
local LINK_FILE_EXT = { txt = true, list = true, urls = true, csv = true }
local LINK_FILE_MAX = 4 * 1024 * 1024   -- un listado de enlaces no pesa más que esto; un vídeo, sí

-- ¿Es una lista de enlaces en texto? Se mira la extensión y el tamaño ANTES de leer: pasarle un .mkv de 4 GB a
-- read_links cargaría la película entera en memoria para buscar «https://».
local function is_link_file(path, info)
  local ext = tostring(path or ''):match('%.([%a%d]+)$')
  if not ext or not LINK_FILE_EXT[ext:lower()] then return false end
  return not info or not info.size or info.size <= LINK_FILE_MAX
end

local function read_links(path)
  local fh = io.open(path, 'r')
  if not fh then return nil end
  local text = fh:read(LINK_FILE_MAX) or ''
  fh:close()
  local out = {}
  for linea in (text .. '\n'):gmatch('([^\n]*)\n') do
    if not linea:match('^%s*[#;]') then
      for _, u in ipairs(urls_in(linea)) do table.insert(out, u) end
    end
  end
  return out
end

-- H37/D4 · lo que yt-dlp no puede abrir, dicho con su alternativa antes de intentarlo. Filas sin acción: no se
-- ofrece nada que vaya a fallar.
local function site_rows(url)
  local aviso = state.site and state.site.url == url and state.site or nil
  if not aviso then return {}, true end
  local rows = {}
  if aviso.supported == false then
    rows[#rows + 1] = { title = aviso.message, icon = 'block', selectable = false, muted = true }
    if aviso.alternative ~= '' then
      rows[#rows + 1] = { title = aviso.alternative, icon = 'lightbulb', selectable = false, muted = true }
    end
    return rows, false
  end
  if aviso.warning and aviso.warning ~= '' then
    rows[#rows + 1] = { title = aviso.warning, icon = 'warning', selectable = false, muted = true }
  end
  if aviso.private then
    rows[#rows + 1] = { title = aviso.private_hint or '', icon = 'cookie', selectable = false, muted = true }
  end
  return rows, true
end

-- D2/D3 · una sola lista con casillas, venga de una lista de reproducción, de un canal, de veinte enlaces pegados o de
-- un .txt. La primera fila fija el formato de todos («Para todos: Audio · Opus 128») y cada fila puede llevar el suyo
-- con su acción; igual con los subtítulos SRT. Lo que se descarga son solo las filas marcadas.
local PICK_DEFAULT = 'video_1080'

local function pick_preset_title(id)
  for _, pr in ipairs((state.presets and state.presets.presets) or {}) do
    if pr.id == id then return pr.title end
  end
  return id
end

local function pick_count()
  local pk = state.pick
  if not pk then return 0, 0 end
  local n = 0
  for i = 1, #pk.entries do if pk.sel[i] then n = n + 1 end end
  return n, #pk.entries
end

local function pick_set_entries(entries, source, url, title, asked)
  local sel = {}
  for i = 1, #entries do sel[i] = true end
  state.pick = { entries = entries, sel = sel, fmt = {}, srt = {}, source = source, url = url, asked = asked,
                 title = title or '', common = (state.pick and state.pick.common) or P:get('pick_preset') or PICK_DEFAULT,
                 common_srt = (state.pick and state.pick.common_srt) or false }
end

-- ---------------------------------------------------------------------------------------------
-- H42 · una sola puerta. Un único campo de texto acepta un enlace, veinte, una lista de reproducción, un canal
-- entero, una ruta local, un .txt con enlaces o, si se deja vacío, lo que haya en el portapapeles. Después hay UNA
-- sola pregunta, reproducir o descargar, y la de descargar cae en la pantalla de siempre (views.picklist).

local GATE_TITLE = 'Abrir o descargar'

-- Se le pregunta a mpvd por la URL que se acaba de pegar (H37/D4). Es una llamada por URL nueva y se guarda.
local function ask_site(url, cb)
  if url == '' or (state.site and state.site.url == url) then cb() return end
  rpc.call('ytdl.site_support', { url = url }, function(err, res)
    if not err and type(res) == 'table' then state.site = res else state.site = { url = url, supported = true } end
    cb()
  end, 15)
end

-- Una URL que huele a lista o a canal. Solo para esas se le pregunta a mpvd cuántos elementos trae, que es una
-- llamada de red: un vídeo suelto no la necesita y así «Reproducir» no espera a nada.
local LIST_HINTS = { '[?&]list=', '/playlist', '/@[%w%-_.]+', '/channel/', '/c/', '/user/', '/videos/?$',
                     '/streams/?$', '/podcasts/?$' }

local function looks_like_list(url)
  for _, pat in ipairs(LIST_HINTS) do if url:match(pat) then return true end end
  return false
end

local function plural(n, one, many)
  if n == 1 then return '1 ' .. one end
  return tostring(n) .. ' ' .. many
end

-- Qué es lo que se ha escrito o pegado, o nil si no se reconoce nada (entonces la caja ofrece buscarlo en YouTube).
local function gate_classify(text)
  local typed = trim(text)
  if typed == '' then return nil end
  local urls = urls_in(typed)
  if #urls > 1 then
    return { kind = 'links', urls = urls, count = #urls, title = plural(#urls, 'enlace', 'enlaces') }
  end
  if #urls == 1 then
    -- H44/C6 · una invitación a una sala (…/s/<sala>#k=<token>) no se reproduce ni se descarga: se entra en ella.
    -- Es lo que más a mano llega por aquí, porque llega pegado desde el móvil como cualquier otro enlace.
    if urls[1]:match('^https?://[^%s]+/s/[%w_-]+#k=.') then
      return { kind = 'room', url = urls[1], count = 1 }
    end
    return { kind = 'url', url = urls[1], count = 1, list = looks_like_list(urls[1]) }
  end
  local bare = as_url(typed)                 -- «youtube.com/…» sin esquema
  if bare then return { kind = 'url', url = bare, count = 1, list = looks_like_list(bare) } end
  local path = mp.command_native({ 'expand-path', typed }) or typed
  local info = utils.file_info(path)
  if not info then return nil end
  if info.is_dir then return { kind = 'file', path = path, count = 1, dir = true } end
  if is_link_file(path, info) then
    local links = read_links(path) or {}
    if #links == 0 then return { kind = 'empty_list', path = path, count = 0 } end
    return { kind = 'links', urls = links, count = #links,
             title = plural(#links, 'enlace', 'enlaces') .. ' de ' .. typed }
  end
  return { kind = 'file', path = path, count = 1 }
end

local function gate_row(what, from_clipboard)
  local title, hint, icon
  if what.kind == 'room' then
    title, hint, icon = 'Entrar en esa sala', 'ver juntos con quien te lo ha pasado', 'login'
  elseif what.kind == 'links' then
    title, icon = 'Seguir con ' .. (what.title or plural(what.count, 'enlace', 'enlaces')), 'checklist'
  elseif what.kind == 'url' then
    title = what.list and 'Seguir con esa lista o canal' or 'Seguir con ese enlace'
    hint, icon = ellipsize(what.url, 60), 'link'
  elseif what.dir then
    title, hint, icon = 'Seguir con esa carpeta', ellipsize(what.path, 60), 'folder'
  else
    title, hint, icon = 'Seguir con ese archivo', ellipsize(what.path, 60), 'folder_open'
  end
  if from_clipboard then hint = tr('del portapapeles · %s'):format(hint or '') end
  return { title = title, hint = hint, icon = icon, value = { gate = what } }
end

local function gate_items(query)
  local typed = trim(query)
  local what = gate_classify(typed)
  local items = {}
  if what and what.kind == 'url' then
    local rows, ok = site_rows(what.url)
    for _, r in ipairs(rows) do items[#items + 1] = r end
    if not ok then return items end          -- nada que pulsar: fallaría
  end
  if what and what.kind == 'empty_list' then
    return uosc.message_items(tr('Ese archivo no tiene ningún enlace'), 'description')
  end
  if what then
    items[#items + 1] = gate_row(what, false)
  elseif typed ~= '' then
    items[#items + 1] = { title = tr('Buscar «%s» en YouTube'):format(typed), icon = 'youtube_searched_for',
                          value = { yt_search = typed } }
  else
    -- la caja vacía ya ofrece lo que haya en el portapapeles: es el «Pegar URL o ruta copiada» de antes
    local pegado = gate_classify(clipboard_raw())
    if pegado and pegado.kind ~= 'empty_list' then items[#items + 1] = gate_row(pegado, true) end
  end
  if typed == '' then
    -- H64 · sin teclado no se puede pegar ni escribir una ruta (una Raspberry en el salón), así que la puerta
    -- única lleva también al explorador de carpetas, que se maneja solo con arriba, abajo y aceptar
    items[#items + 1] = { title = tr('Explorar las carpetas del equipo…'), icon = 'folder_open',
                          hint = tr('discos, pinchos USB y tus carpetas'), separator = #items > 0 or nil,
                          value = { explore = true } }
  end
  if #items == 0 then
    items = uosc.message_items(tr('Pega o escribe: un enlace, varios, una lista, un canal, un archivo o una carpeta'),
                               'add_link')
  end
  return items
end

local function gate_menu(items, extra)
  local menu = {
    type = GATE_MENU, title = GATE_TITLE, items = items, callback = { SCRIPT, GATE_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    footnote = tr('ctrl+v pega · un enlace, varios, una lista, un canal, un archivo · vacío usa el portapapeles · ⌫ atrás'),
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return menu
end

views.gate = function(args)
  state.gate = nil
  state.site = nil
  state.gate_query = trim(args.query)
  local items = gate_items(state.gate_query)
  remember(items)
  publish()
  uosc.open(gate_menu(items, { search_suggestion = state.gate_query ~= '' and state.gate_query or nil }))
end

local function gate_typed(query)
  state.gate_query = trim(query)
  local top = state.stack[#state.stack]
  if top and top.name == 'gate' then top.args = { query = state.gate_query } end   -- «atrás» conserva el texto
  local urls = urls_in(state.gate_query)
  ask_site(urls[1] or as_url(state.gate_query) or '', function()
    if state.view ~= 'gate' then return end
    local items = gate_items(state.gate_query)
    remember(items)
    uosc.update(gate_menu(items))
    publish()
  end)
end

-- A2 · la única pregunta. El número de elementos se dice siempre: 1 para un enlace o un archivo, los que haya para
-- varios enlaces, y los que diga mpvd para una lista o un canal.
local function what_desc(what)
  if what.kind == 'links' then return 'Varios enlaces' end
  if what.kind == 'file' then return what.dir and 'Una carpeta de tu equipo' or 'Un archivo de tu equipo' end
  if what.list or what.entries then return 'Una lista o un canal' end
  return 'Un enlace de internet'
end

local function show_what(what, status)
  local cuenta = plural(what.count or 1, 'elemento', 'elementos')
  local items = {
    { title = what_desc(what), hint = status or cuenta, icon = 'info', selectable = false, muted = true },
    { title = tr('Reproducir'), hint = cuenta, icon = 'play_arrow', value = { gate_play = true } },
  }
  if what.kind == 'file' then
    items[#items + 1] = { title = tr('Descargar'), hint = cuenta .. ' · ya está aquí: convertir', icon = 'transform',
                          value = { gate_download = true } }
  else
    items[#items + 1] = { title = tr('Descargar'), hint = cuenta, icon = 'download', value = { gate_download = true } }
  end
  show(tr('¿Reproducir o descargar?'), items, { footnote = tr('Enter elige · ⌫ vuelve a la caja con el texto puesto') })
end

views.what = function(args)
  local what = args.what or state.gate
  if type(what) ~= 'table' then
    show(tr('¿Reproducir o descargar?'), uosc.message_items(tr('No hay nada que abrir'), 'info'))
    return
  end
  state.gate = what
  publish()
  if what.kind == 'url' and what.list and not what.entries and rpc.connected() then
    show_what(what, 'mirando qué trae…')
    rpc.call('ytdl.playlist', { url = what.url }, function(err, res)
      if state.view ~= 'what' then return end
      if not err and type(res) == 'table' and #(res.entries or {}) > 0 then
        what.entries, what.count, what.title = res.entries, #res.entries, res.title or what.url
      else
        what.list = false                     -- no era una lista: un enlace suelto y punto
      end
      state.gate = what
      publish()
      show_what(what)
    end, 90)
    return
  end
  show_what(what)
end

local function gate_play()
  local g = state.gate or {}
  if g.kind == 'links' then
    for i, u in ipairs(g.urls or {}) do mp.commandv('loadfile', u, i == 1 and 'replace' or 'append-play') end
    osd(tr('Abriendo %s'):format(plural(#(g.urls or {}), tr('enlace'), tr('enlaces'))))
  elseif g.kind == 'file' then
    load_url(g.path, false)
  elseif g.url then
    load_url(g.url, false)
  end
  close_menus()
end

local function gate_download()
  local g = state.gate or {}
  if g.kind == 'file' then
    -- un archivo que ya está aquí no se baja: se convierte, que es lo mismo pero dicho bien
    local crumbs = {}
    for _, c in ipairs(N.parent.crumbs or {}) do crumbs[#crumbs + 1] = c end
    for _, spec in ipairs(state.stack) do crumbs[#crumbs + 1] = spec.title end
    nav.open_child('mu_convert', 'convert-menu', crumbs, state.view)
    return
  end
  if g.kind == 'links' then
    local entries = {}
    for _, u in ipairs(g.urls or {}) do entries[#entries + 1] = { url = u, title = u } end
    pick_set_entries(entries, 'links', nil, g.title)
    open_view({ name = 'picklist', args = {} })
  elseif g.entries then
    -- la lista ya se resolvió para poder decir cuántos elementos trae: no se vuelve a preguntar
    pick_set_entries(g.entries, 'url', g.url, g.title, g.url)
    open_view({ name = 'picklist', args = {} })
  elseif g.url then
    state.pick = nil
    open_view({ name = 'picklist', args = { url = g.url } })
  end
end

views.picklist = function(args)
  if not require_mpvd('Elegir qué bajar') then return end
  local pk = state.pick
  if args.url and (not pk or pk.asked ~= args.url) then
    show(tr('Elegir qué bajar'), uosc.loading_items(tr('Mirando qué trae…')))
    rpc.call('ytdl.playlist', { url = args.url }, function(err, res)
      if state.view ~= 'picklist' then return end
      -- Si no se puede leer como lista (un vídeo suelto, o una web que no deja), se trata como un enlace y punto: una
      -- URL que no sea una lista no tiene por qué impedir bajarla.
      if err or type(res) ~= 'table' or #(res.entries or {}) == 0 then
        pick_set_entries({ { url = args.url, title = args.url } }, 'links', nil, args.url, args.url)
      else
        pick_set_entries(res.entries, 'url', args.url, res.title or args.url, args.url)
      end
      reopen_current()
    end, 90)
    return
  end
  if not pk then show(tr('Elegir qué bajar'), uosc.message_items(tr('No hay nada que elegir'), 'info')) return end
  with_presets(function()
    if state.view ~= 'picklist' then return end
    local n, total = pick_count()
    local propios = 0
    for i = 1, total do if pk.fmt[i] or pk.srt[i] ~= nil then propios = propios + 1 end end
    local items = {
      { title = tr('Para todos: %s'):format(pick_preset_title(pk.common)),
        hint = propios > 0 and (propios .. ' con formato propio') or 'Enter para cambiarlo',
        icon = 'tune', value = { pick_fmt = 'all' } },
      { title = tr('Subtítulos (SRT) aparte'), hint = pk.common_srt and 'sí' or 'no', active = pk.common_srt,
        icon = 'closed_caption', value = { pick_srt = 'all' }, keep_open = true },
      { title = n == total and 'Desmarcar todo' or 'Marcar todo', hint = n .. ' de ' .. total,
        icon = n == total and 'check_box' or 'check_box_outline_blank', value = { pick_all = true }, keep_open = true },
      { title = tr('Descargar %s'):format(n), icon = 'download', value = { pick_download = true }, muted = n == 0,
        separator = true },
    }
    for i, e in ipairs(pk.entries) do
      local marcas = {}
      if e.duration then marcas[#marcas + 1] = fmt_duration(e.duration) end
      if pk.fmt[i] then marcas[#marcas + 1] = pick_preset_title(pk.fmt[i]) end
      if pk.srt[i] ~= nil then marcas[#marcas + 1] = pk.srt[i] and 'con SRT' or 'sin SRT' end
      items[#items + 1] = {
        title = e.title or e.url or ('#' .. i), hint = table.concat(marcas, ' · '),
        icon = pk.sel[i] and 'check_box' or 'check_box_outline_blank', value = { pick_toggle = i }, keep_open = true,
        actions = { { name = 'formato', icon = 'tune', label = tr('Formato solo para este') },
                    { name = 'srt', icon = 'closed_caption', label = tr('SRT solo para este') } },
      }
    end
    local titulo = pk.source == 'url' and ('Elegir · ' .. ellipsize(pk.title or '', 46)) or 'Elegir qué bajar'
    show(titulo, items, { footnote = tr('Enter marca / desmarca · la primera fila cambia el formato de todos · ⌫ atrás') })
  end)
end

views.pickfmt = function(args)
  if not require_mpvd('Formato') then return end
  with_presets(function(err)
    if state.view ~= 'pickfmt' then return end
    if err then show(tr('Formato'), uosc.message_items(fail(err, 'ytdl.presets'), 'error')) return end
    local pk = state.pick or {}
    local target = args.target
    local actual = target == 'all' and pk.common or (pk.fmt and pk.fmt[target]) or nil
    local items = {}
    if target ~= 'all' then
      items[#items + 1] = { title = tr('Como todos (%s)'):format(pick_preset_title(pk.common or PICK_DEFAULT)),
                            icon = 'done_all', active = actual == nil, value = { pick_choose = { target = target } } }
    end
    for _, pr in ipairs((state.presets and state.presets.presets) or {}) do
      if pr.group ~= 'subs' then
        items[#items + 1] = { title = pr.title, icon = pr.group == 'audio' and 'audiotrack' or 'movie',
                              active = actual == pr.id,
                              value = { pick_choose = { target = target, preset = pr.id } } }
      end
    end
    show(target == 'all' and 'Formato para todos' or 'Formato de este', items)
  end)
end

-- Se descarga por grupos: todas las filas que comparten formato y SRT van en una sola llamada. Si es una lista entera
-- sin nada propio, se usa el camino de siempre (carpeta propia y numeración), que es más bonito.
local function pick_download()
  local pk = state.pick
  if not pk then return end
  local n = select(1, pick_count())
  if n == 0 then osd(tr('Marca al menos uno')) return end
  local propios = false
  for i = 1, #pk.entries do if pk.fmt[i] or pk.srt[i] ~= nil then propios = true end end
  local srt_opts = function(on)
    if not on then return nil end
    return { subtitles = true, subs_mode = 'file' }
  end
  if pk.source == 'url' and not propios then
    local idx = {}
    for i = 1, #pk.entries do if pk.sel[i] then table.insert(idx, tostring(i)) end end
    local all = #idx == #pk.entries
    local params = { url = pk.url, title = pk.title, preset = pk.common, playlist = true,
                     playlist_items = not all and table.concat(idx, ',') or nil }
    if pk.common_srt then
      params.options = { subtitles = true, subs_mode = 'file' }
    end
    start_download(params)
    return
  end
  local grupos, orden = {}, {}
  for i = 1, #pk.entries do
    if pk.sel[i] then
      local preset = pk.fmt[i] or pk.common
      local srt = pk.srt[i]
      if srt == nil then srt = pk.common_srt end
      local clave = preset .. (srt and '+srt' or '')
      if not grupos[clave] then
        grupos[clave] = { preset = preset, srt = srt, urls = {} }
        table.insert(orden, clave)
      end
      table.insert(grupos[clave].urls, pk.entries[i].url)
    end
  end
  local pendientes, total = #orden, 0
  for _, clave in ipairs(orden) do
    local g = grupos[clave]
    rpc.call('ytdl.download.batch', { urls = g.urls, preset = g.preset, options = srt_opts(g.srt), notify = SCRIPT },
      function(err, res)
        pendientes = pendientes - 1
        if err then osd(tr('Descargar: %s'):format(fail(err, 'ytdl.download.batch')))
        else total = total + (res.count or 0) end
        if pendientes == 0 then
          osd(string.format('⬇ %d descargas en cola', total))
          state.stack = { { name = 'root', title = ROOT_TITLE } }
          state.force_open = true
          open_view({ name = 'downloads' })
        end
      end, 60)
  end
end

local RATE_STEPS = { '', '500K', '1M', '2M', '5M', '10M' }
local BROWSER_STEPS = { '', 'firefox', 'chrome', 'chromium', 'brave', 'edge', 'vivaldi', 'opera' }

views.dl_settings = function()
  if not require_mpvd('Ajustes de descarga') then return end
  rpc.call('ytdl.settings.get', nil, function(err, st)
    if state.view ~= 'dl_settings' then return end
    if err then show(tr('Ajustes de descarga'), uosc.message_items(fail(err, 'ytdl.settings.get'), 'error')) return end
    state.dl_settings = st
    show(tr('Ajustes de descarga'), {
      { title = tr('Descargas a la vez'), hint = tostring(st.concurrent or 2), icon = 'stacks',
        value = { dlset = 'concurrent' } },
      { title = tr('Límite de velocidad'), hint = (st.rate_limit or '') ~= '' and (st.rate_limit .. 'B/s') or 'sin límite',
        icon = 'speed', value = { dlset = 'rate_limit' } },
      { title = tr('No repetir lo ya descargado'), hint = st.archive and 'sí' or 'no', active = st.archive, icon = 'history',
        value = { dlset = 'archive' } },
      { title = tr('Listas y canales en su carpeta, numerados'), hint = st.list_folders and 'sí' or 'no',
        active = st.list_folders, icon = 'folder_special', value = { dlset = 'list_folders' } },
      { title = tr('Usar mi sesión del navegador'), hint = (st.cookies_browser or '') ~= '' and st.cookies_browser or 'no',
        active = (st.cookies_browser or '') ~= '', icon = 'cookie', value = { dlset = 'cookies_browser' }, separator = true },
      { title = tr('Solo para lo que ya puedes ver con tu cuenta; nunca contenido con DRM'), icon = 'info',
        selectable = false, muted = true },
    }, { footnote = tr('Enter cambia · se aplica a las descargas nuevas y a lo próximo que abras · ⌫ atrás') })
  end, 15)
end

local function dl_setting(key)
  local st = state.dl_settings or {}
  local value
  if key == 'concurrent' then
    value = ((tonumber(st.concurrent) or 2) % 4) + 1
  elseif key == 'rate_limit' or key == 'cookies_browser' then
    local steps = key == 'rate_limit' and RATE_STEPS or BROWSER_STEPS
    local cur = st[key] or ''
    local nxt = 1
    for i, r in ipairs(steps) do if r == cur then nxt = i % #steps + 1 end end
    value = steps[nxt]
  else
    value = not st[key]
  end
  rpc.call('ytdl.settings.set', { [key] = value }, function(err)
    if err then osd(tr('Ajustes: %s'):format(fail(err, 'ytdl.settings.set'))) end
    if key == 'cookies_browser' and not err then apply_cookies(value) end
    reopen_current()
  end, 15)
end

-- yt_search: submit palette (Enter with nothing selected searches; typing deselects), results from ytdl.search.
local SEARCH_HELP = 'Escribe y pulsa Enter para buscar en YouTube'
local search_seq = 0

local function result_items(rows)
  local items = {}
  for _, r in ipairs(rows or {}) do
    local hint = {}
    local duration = fmt_duration(r.duration)
    if r.is_live then
      table.insert(hint, 'EN DIRECTO')
    elseif duration then
      table.insert(hint, duration)
    end
    if type(r.channel) == 'string' and r.channel ~= '' then table.insert(hint, r.channel) end
    table.insert(items, {
      title = r.title or r.url, hint = #hint > 0 and table.concat(hint, ' · ') or nil,
      icon = r.is_live and 'live_tv' or 'smart_display', bold = r.is_live or nil,
      value = { result = { url = r.url, title = r.title } },
    })
  end
  return items
end

local function search_menu(items, extra)
  local menu = {
    type = SEARCH_MENU, title = tr('Buscar en YouTube'), items = items, callback = { SCRIPT, SEARCH_EVENT },
    search_style = 'palette', search_debounce = 'submit', on_search = 'callback', on_close = 'callback',
    item_actions = ITEM_ACTIONS,
    footnote = tr('Enter busca / reproduce · Tab: añadir a la lista o descargar · ⌫ atrás'),
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return menu
end

local function set_search(query, status, n)
  state.search_query, state.search_status, state.search_results = query, status, n or 0
end

views.yt_search = function(args)
  local query = trim(args.query)
  local extra = { search_suggestion = query ~= '' and query or nil }
  local items
  if query ~= '' and state.results and state.results.query == query then
    -- back from «Descargar»: the same results, no new search
    items = result_items(state.results.rows)
    set_search(query, 'done', #state.results.rows)
  else
    items = uosc.message_items(SEARCH_HELP, 'search')
    set_search(query, 'idle')
    extra.search_submit = query ~= '' or nil
  end
  remember(items)
  publish()
  uosc.open(search_menu(items, extra))
end

local function show_search(items)
  remember(items)
  uosc.update(search_menu(items))
  publish()
end

local function run_search(query)
  local q = trim(query)
  search_seq = search_seq + 1
  local seq = search_seq
  local top = state.stack[#state.stack]
  if top and top.name == 'yt_search' then top.args = { query = q } end  -- «back» returns to this query
  if q == '' then
    set_search(q, 'idle')
    show_search(uosc.message_items(SEARCH_HELP, 'search'))
    return
  end
  local url = as_url(q)
  if url then
    set_search(q, 'url')
    show_search({ open_item(url) })
    return
  end
  if not rpc.connected() then
    set_search(q, 'error')
    mp.commandv('script-message-to', 'mu_core', 'mu-ensure')
    show_search(uosc.message_items(tr('mpvd no está disponible: reintentando la conexión, vuelve a pulsar Enter'), 'error'))
    return
  end
  set_search(q, 'loading')
  show_search(uosc.loading_items('Buscando «' .. q .. '» en YouTube…'))
  rpc.call('ytdl.search', { query = q, limit = opts.search_limit }, function(err, rows)
    if seq ~= search_seq then return end
    if err then
      set_search(q, 'error')
      show_search(uosc.message_items(tr('No se pudo buscar: %s'):format(fail(err, 'ytdl.search')), 'error'))
      return
    end
    state.results = { query = q, rows = rows }
    set_search(q, 'done', #rows)
    local items = result_items(rows)
    if #items == 0 then items = uosc.message_items(tr('Sin resultados para «%s»'):format(q), 'search_off') end
    show_search(items)
  end, 45)
end

-- ---------------------------------------------------------------------------------------------
-- events from uosc (one handler; `source` = the menu type whose callback fired)

local function on_event(source, json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.open or v.result then
      local target = v.result or { url = v.open }
      if ev.action == 'download' then
        open_view({ name = 'download', args = { url = target.url, title = target.title } })
      elseif ev.action == 'append' then
        load_url(target.url, true, target.title)
      else
        load_url(target.url, false, target.title)
        close_menus()
      end
    elseif v.explore then
      uosc.close(GATE_MENU)
      mp.commandv('script-message-to', 'mu_library', 'mu-library-explore')
    elseif v.yt_search then
      local top = state.stack[#state.stack]
      if top and top.name == 'open_url' then top.args = { query = v.yt_search } end  -- «back» keeps the text
      open_view({ name = 'yt_search', args = { query = v.yt_search } })
    elseif v.toggle then
      toggle_audio()
      uosc.close(MENU)
    elseif v.quality then
      if ev.action == 'download' then
        start_download({ kind = 'exact', format = v.quality.format, title = state.title })
      else
        set_quality(v.quality)
        uosc.close(MENU)
      end
    elseif v.preset then
      start_download({ preset = v.preset, url = v.url, title = v.title })
      uosc.close(MENU)
    elseif v.opt then
      toggle_option(v.opt)
      local remembered = {}
      for k, val in pairs(state.dl_options or {}) do if k ~= 'playlist' then remembered[k] = val end end
      P:set('dl_options', remembered)
      reopen_current()
    elseif v.download then
      if ev.action then download_action(v.download, ev.action) else download_details(v.download) end
    elseif v.panel then
      mp.commandv('script-message-to', 'mu_remote', ev.action == 'copy' and 'mu-remote-downloads-copy'
                                                    or 'mu-remote-downloads')
      if ev.action ~= 'copy' then close_menus() end
    elseif v.clear then
      rpc.call('ytdl.downloads.clear', nil, function() open_view({ name = 'downloads' }, false) end)
    elseif v.update then
      update_action(v.update)
    elseif v.setting == 'auto_update' then
      rpc.call('ytdl.settings.get', nil, function(err, s)
        if err then return end
        rpc.call('ytdl.settings.set', { auto_update = not s.auto_update }, function() open_view({ name = 'status' }, false) end)
      end)
    elseif v.gate and v.gate.kind == 'room' then
      -- una sala no tiene esa pregunta: se entra. Lo hace mu-share, que es quien sabe de salas.
      close_menus()
      osd(tr('Entrando en la sala…'))
      mp.commandv('script-message-to', 'mu_share', 'mu-share-join', v.gate.url)
    elseif v.gate then
      -- A2 · de la caja a la única pregunta. La caja se queda debajo en la pila: ⌫ vuelve a ella con el texto puesto.
      open_view({ name = 'what', args = { what = v.gate } })
    elseif v.gate_play then
      gate_play()
    elseif v.gate_download then
      gate_download()
    elseif v.pick_url then
      -- D1: una URL cualquiera. mpvd dice qué trae (una lista, un canal o un solo vídeo) y se enseña igual.
      state.pick = nil
      state.stack = { { name = 'root', title = ROOT_TITLE } }
      state.force_open = uosc.open_type() ~= MENU
      open_view({ name = 'picklist', args = { url = v.pick_url } })
    elseif v.pick_links then
      local entries = {}
      for _, u in ipairs(v.pick_links) do entries[#entries + 1] = { url = u, title = u } end
      pick_set_entries(entries, 'links', nil, v.title)
      state.stack = { { name = 'root', title = ROOT_TITLE } }
      state.force_open = uosc.open_type() ~= MENU
      open_view({ name = 'picklist', args = {} })
    elseif v.pick_toggle then
      local i = v.pick_toggle
      if ev.action == 'formato' then            -- las acciones de la fila NO marcan ni desmarcan
        open_view({ name = 'pickfmt', args = { target = i } })
      elseif ev.action == 'srt' then            -- sí → no → como todos
        local cur = state.pick.srt[i]
        if cur == nil then state.pick.srt[i] = true
        elseif cur then state.pick.srt[i] = false
        else state.pick.srt[i] = nil end
        reopen_current()
      else
        state.pick.sel[i] = not state.pick.sel[i]
        reopen_current()
      end
    elseif v.pick_all then
      local all = true
      for i = 1, #state.pick.entries do if not state.pick.sel[i] then all = false end end
      for i = 1, #state.pick.entries do state.pick.sel[i] = not all end
      reopen_current()
    elseif v.pick_srt then
      state.pick.common_srt = not state.pick.common_srt
      reopen_current()
    elseif v.pick_fmt then
      open_view({ name = 'pickfmt', args = { target = v.pick_fmt } })
    elseif v.pick_choose then
      local t = v.pick_choose.target
      if t == 'all' then
        state.pick.common = v.pick_choose.preset
        P:set('pick_preset', v.pick_choose.preset)
      else
        state.pick.fmt[t] = v.pick_choose.preset   -- nil = como todos
      end
      table.remove(state.stack)
      reopen_current()
    elseif v.pick_download then
      pick_download()
    elseif v.dlset then
      dl_setting(v.dlset)
    elseif v.child then
      local crumbs = {}
      for _, c in ipairs(N.parent.crumbs or {}) do crumbs[#crumbs + 1] = c end
      for _, spec in ipairs(state.stack) do crumbs[#crumbs + 1] = spec.title end
      nav.open_child(v.script or 'mu_convert', v.child, crumbs, state.view)
    elseif v.view then
      if v.ensure then mp.commandv('script-message-to', 'mu_core', 'mu-ensure') end
      if v.view == 'root' then state.stack = {} end
      open_view({ name = v.view, args = v })
    end
  elseif ev.type == 'search' then
    if source == URL_MENU then url_typed(ev.query or '')
    elseif source == SEARCH_MENU then run_search(ev.query or '')
    elseif source == GATE_MENU then gate_typed(ev.query or '') end
  elseif ev.type == 'back' then
    table.remove(state.stack)
    if #state.stack == 0 then
      -- palettes (open URL, YouTube search) close: ⌫ there is mostly "delete text"; menus return to their opener
      if source ~= MENU or not N:leave() then close_menus() end
    else
      reopen_current()
    end
  end
  -- `close` is not used for state (racy, ADR-013): the observer below owns the reset
end

mp.register_script_message(EVENT, function(json) on_event(MENU, json) end)
-- mu-convert (opened from «Convertir…» / «Tareas») went back past its root: show this menu again
mp.register_script_message('mu-nav-return', function()
  if not uosc.available() then return end
  state.stack = {}
  open_view({ name = 'root' })
end)
mp.register_script_message(URL_EVENT, function(json) on_event(URL_MENU, json) end)
mp.register_script_message(SEARCH_EVENT, function(json) on_event(SEARCH_MENU, json) end)
mp.register_script_message(GATE_EVENT, function(json) on_event(GATE_MENU, json) end)

local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if OUR_MENUS[t or ''] then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    if OUR_MENUS[uosc.open_type() or ''] then return end
    -- H63/N3 · si acabamos de pedir nuestro menú y uosc aún no lo ha confirmado, no hay nada que olvidar
    if uosc.asking(MENU) then return end
    -- H63/N2 · ni si estamos esperando a mpvd: al pulsar una fila, uosc cierra el menú y nosotros lo reabrimos
    -- cuando llega la respuesta. Esperar un plazo era una carrera que se pierde con el equipo cargado; lo que
    -- hay que mirar es si queda algo en vuelo.
    if rpc.pending() > 0 then return end
    if #state.stack > 0 or state.view ~= '' then
      state.stack = {}
      state.view = ''
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- events pushed by mpvd (download progress)

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'download' or type(ev.download) ~= 'table' then return end
  local d = ev.download
  local prev = state.downloads[d.id]
  state.downloads[d.id] = d
  if not prev then table.insert(state.download_order, 1, d.id) end
  state.last_event = { id = d.id, status = d.status, progress = d.progress, message = d.message }
  publish()
  if opts.notify_done and (not prev or prev.status ~= d.status) then
    if d.status == 'done' then
      osd(tr('✓ Descarga completada: %s\n→ %s'):format(d.title or d.url,
                                                     (d.outputs or {})[1] or d.out_dir or ''))
    elseif d.status == 'failed' then
      osd(tr('✗ Descarga fallida: %s\n%s'):format(d.title or d.url, d.error or ''))
    end
  end
  set_button_state()
  refresh_panel()
end)

-- ---------------------------------------------------------------------------------------------
-- bindings and controls button

set_button_state = function()
  if not uosc.available() then return end
  local n = count_active()
  uosc.set_button('mu-ytdl', {
    icon = 'download', tooltip = 'yt-dlp: calidad y descargas (alt+y)', active = n > 0,
    badge = n > 0 and tostring(n) or nil,
    command = { 'script-binding', SCRIPT .. '/ytdl-menu' },
  })
end

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  open_view({ name = 'root' })
end

-- H42 · la puerta única: tecla propia, entrada del menú principal (mu-menu la abre como hija) y mensaje para otros
-- scripts, con un texto inicial opcional.
local function open_gate(text)
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  open_view({ name = 'gate', args = { query = text } })
end

N:binding('ytdl-gate', open_gate)
mp.register_script_message('mu-ytdl-gate', open_gate)
N:binding('ytdl-menu', open_root)
mp.add_key_binding(nil, 'ytdl-toggle-audio', toggle_audio)
local function open_under_root(view)
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  open_view({ name = view })
end
N:binding('ytdl-quality', function() open_under_root('quality') end)
N:binding('ytdl-download', function() open_under_root('download') end)
N:binding('ytdl-downloads', function() open_under_root('downloads') end)

-- Palettes: bindings, plus messages with an optional initial text for other scripts
-- (`script-message-to mu_ytdl mu-ytdl-open-url [texto]`, `script-message-to mu_ytdl mu-ytdl-search [consulta]`;
-- a search query is submitted right away).
local function open_palette(name, text)
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  open_view({ name = name, args = { query = text } })
end
mp.add_key_binding(nil, 'open-url', function() open_palette('open_url') end)
mp.add_key_binding(nil, 'yt-search', function() open_palette('yt_search') end)
mp.register_script_message('mu-ytdl-open-url', function(text) open_palette('open_url', text) end)
mp.register_script_message('mu-ytdl-search', function(query) open_palette('yt_search', query) end)

mp.register_script_message('uosc-version', set_button_state)
-- el estado tiene que decir si está sonando solo el audio, también cuando se cambia por tecla o por otro script
mp.observe_property('vid', 'string', function() publish() end)

mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button_state() end
  if core and core.mpvd == 'connected' then
    sync_hook_with_mpvd()
  end
end)

apply_hook_path()
publish()
msg.info('mu-ytdl loaded')
