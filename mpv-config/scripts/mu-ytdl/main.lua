-- mu-ytdl: yt-dlp integration for MPV-UOS. Points mpv's ytdl_hook at the vendored yt-dlp, switches video / audio-only
-- keeping the position, "Calidad" (all formats from `yt-dlp -J` via mpvd), "Descargar" (presets + options) and a
-- live "Descargas" panel fed by mpvd push events, plus two palettes: "Abrir URL" (typed/pasted URL or clipboard) and
-- "Buscar en YouTube" (ytdl.search in mpvd). Script name: mu_ytdl.
-- Bindings: ytdl-menu, ytdl-toggle-audio, ytdl-quality, ytdl-download, ytdl-downloads, open-url, yt-search
-- (see input.conf).
-- Everything about ytdl_hook below was verified against the script embedded in mpv 0.41 (docs/MPV_YTDL.md).
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-ytdl-event'
local URL_EVENT = 'mu-ytdl-url-event'       -- one callback message per palette: `search` events carry no menu type
local SEARCH_EVENT = 'mu-ytdl-search-event'
local MENU = 'mu-ytdl'
local URL_MENU = 'mu-ytdl-url'              -- palettes get their own type (a palette cannot be update-menu'd in/out)
local SEARCH_MENU = 'mu-ytdl-search'
local OUR_MENUS = { [MENU] = true, [URL_MENU] = true, [SEARCH_MENU] = true }

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
-- remembered choices: "solo audio" for internet videos and the download options (container, subtitles…)
local P = prefs.ns('mu-ytdl', { prefer_audio = false, dl_options = {} })

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
}

local set_button_state -- defined with the bindings below

local function count_active()
  local n = 0
  for _, d in pairs(state.downloads) do
    if d.status == 'queued' or d.status == 'running' then n = n + 1 end
  end
  return n
end

local function publish()
  mp.set_property_native('user-data/mu/ytdl', {
    active = state.active, url = state.url, mode = state.mode, format = state.format, title = state.title,
    current_ids = state.current_ids, view = state.view, depth = #state.stack, downloads_active = count_active(),
    last_event = state.last_event or '', last_error = state.last_error, hook_path = state.hook_path,
    items = state.items, search_query = state.search_query, search_status = state.search_status,
    search_results = state.search_results,
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
    local root = detect_root()
    if root ~= '' then
      local vendored = utils.join_path(utils.join_path(utils.join_path(root, 'vendor'), 'bin'),
        is_windows and 'yt-dlp.exe' or 'yt-dlp')
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

local hook_synced = false
local function sync_hook_with_mpvd()
  if hook_synced or not rpc.connected() then return end
  hook_synced = true
  rpc.call('ytdl.hook', nil, function(err, cfg)
    if err then hook_synced = false; return end
    apply_raw_options(cfg.raw_options)
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

-- ---------------------------------------------------------------------------------------------
-- reload with another ytdl-format at the same position (pause/speed/volume are global → preserved)

local function reload(format, audio_only)
  if not state.active then
    osd('No hay ningún vídeo de yt-dlp cargado')
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
  if ok == nil then osd('No se pudo recargar ' .. url) end
end

local function toggle_audio()
  if not state.active then
    osd('No hay ningún vídeo de yt-dlp cargado')
    return
  end
  if state.mode == 'audio' then
    reload(default_video_format(), false)
    osd('🎬 Vídeo')
    P:set('prefer_audio', false)
  else
    reload(opts.audio_format, true)
    osd('🎧 Solo audio (' .. opts.audio_format .. ') · se recordará')
    P:set('prefer_audio', true)
  end
end

-- "Solo audio" remembered: internet videos open without video. Runs before ytdl_hook's on_load (priority 10) so the
-- audio format is the one yt-dlp resolves; a format chosen for this file (loadfile options) is left alone.
mp.add_hook('on_load', 9, function()
  if not P:get('prefer_audio') then return end
  local path = mp.get_property('path') or ''
  if not (path:match('^https?://') or path:match('^ytdl://')) then return end
  local tv = mp.get_property_native('user-data/mu/iptv') or {}
  if type(tv.current) == 'table' and tv.current.url == path then return end  -- TV channels keep their video
  if mp.get_property_native('option-info/ytdl-format/set-locally') then return end
  mp.set_property('file-local-options/ytdl-format', opts.audio_format)
  mp.set_property('file-local-options/vid', 'no')
end)

-- ---------------------------------------------------------------------------------------------
-- menus

local function base_menu(title, items, extra)
  local menu = {
    type = MENU, title = title, items = items, callback = { SCRIPT, EVENT },
    on_close = 'callback', keep_open = false, search_submenus = true,
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return menu
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
    { title = 'mpvd no está disponible', hint = core.mpvd or '', icon = 'error', selectable = false, muted = true },
    { title = 'Reintentar conexión', icon = 'refresh', value = { view = 'root', ensure = true } },
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
  local items = {
    { title = 'Abrir URL…', hint = key_for('open-url'), icon = 'link', value = { view = 'open_url' } },
    { title = 'Buscar en YouTube…', hint = key_for('yt-search'), icon = 'youtube_searched_for',
      value = { view = 'yt_search' }, separator = true },
  }
  if state.active then
    table.insert(items, {
      title = state.mode == 'audio' and 'Volver al vídeo' or 'Solo audio',
      hint = state.mode == 'audio' and 'ahora: audio' or 'ahora: vídeo', icon = state.mode == 'audio' and 'movie' or 'headphones',
      value = { toggle = true },
    })
    table.insert(items, { title = 'Calidad', hint = short_format(), icon = 'high_quality', value = { view = 'quality' } })
    table.insert(items, { title = 'Descargar', icon = 'download', value = { view = 'download' } })
  else
    table.insert(items, { title = 'Con una URL abierta: solo audio, calidad y descargar',
                          icon = 'info', selectable = false, muted = true, align = 'center' })
  end
  local n = count_active()
  table.insert(items, { title = 'Descargas', hint = n > 0 and (tostring(n) .. ' activas') or nil, icon = 'downloading',
                        value = { view = 'downloads' }, separator = true })
  table.insert(items, { title = 'Estado de yt-dlp', icon = 'settings', value = { view = 'status' } })
  show('yt-dlp', items)
end

-- quality ---------------------------------------------------------------------------------------

local function is_current(id)
  for _, cur in ipairs(state.current_ids) do if cur == id then return true end end
  return false
end

local function quality_item(row, group)
  local audio_only = row.kind == 'audio'
  local format
  if row.kind == 'video' then format = row.id .. '+ba/' .. row.id else format = row.id end
  return {
    title = row.label, hint = row.hint ~= '' and row.hint or nil, active = is_current(row.id),
    icon = audio_only and 'audiotrack' or (row.kind == 'video' and 'videocam' or 'movie'),
    value = { quality = { format = format, audio_only = audio_only, id = row.id, group = group } },
    actions = { { name = 'download', icon = 'download', label = 'Descargar este formato' } },
  }
end

local function quality_items(info)
  local groups = {
    { key = 'combined', title = 'Vídeo + audio' }, { key = 'video', title = 'Solo vídeo (+ mejor audio)' },
    { key = 'audio', title = 'Solo audio' },
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
  table.insert(sections, 1, { title = 'Automático (mejor ≤1080p)', hint = default_video_format(), icon = 'auto_awesome',
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
  if not state.active then show('Calidad', uosc.message_items('Abre una URL de yt-dlp primero', 'info')) return end
  if not require_mpvd('Calidad') then return end
  show('Calidad', uosc.loading_items('Consultando formatos…'))
  with_info(function(err, info)
    if err then show('Calidad', uosc.message_items(fail(err, 'ytdl.info'), 'error')) return end
    local items = quality_items(info)
    if #items == 0 then items = uosc.message_items('yt-dlp no devolvió formatos', 'info') end
    show('Calidad · ' .. (info.title or ''), items, {
      footnote = 'Enter cambia en caliente · Tab descarga ese formato · / busca · ⌫ atrás',
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

local function options_items(o)
  return {
    opt_item('Contenedor de vídeo', o.container, 'inventory_2', 'container'),
    opt_item('Subtítulos (es, en) incrustados', bool_hint(o.subtitles), 'subtitles', 'subtitles'),
    opt_item('Capítulos', bool_hint(o.chapters), 'bookmarks', 'chapters'),
    opt_item('Miniatura como portada', bool_hint(o.thumbnail), 'image', 'thumbnail'),
    opt_item('Metadatos', bool_hint(o.metadata), 'sell', 'metadata'),
    opt_item('SponsorBlock', SB_HINT[o.sponsorblock] or 'no', 'block', 'sponsorblock'),
    opt_item('Lista de reproducción completa', bool_hint(o.playlist), 'playlist_play', 'playlist'),
  }
end

local function toggle_option(name)
  local o = state.dl_options
  if name == 'container' then
    local order = { mp4 = 'mkv', mkv = 'webm', webm = 'mp4' }
    o.container = order[o.container] or 'mp4'
  elseif name == 'sponsorblock' then
    local order = { none = 'mark', mark = 'remove', remove = 'none' }
    o.sponsorblock = order[o.sponsorblock] or 'none'
  else
    o[name] = not o[name]
  end
end

-- args.url/args.title: download something other than the current file (a YouTube search result).
views.download = function(args)
  local target = args.url
  if not target and not state.active then
    show('Descargar', uosc.message_items('Abre una URL de yt-dlp primero', 'info'))
    return
  end
  local target_title = target and (args.title or target) or state.title
  if not require_mpvd('Descargar') then return end
  show('Descargar', uosc.loading_items())
  with_presets(function(err, res)
    if err then show('Descargar', uosc.message_items(fail(err, 'ytdl.presets'), 'error')) return end
    local o = state.dl_options
    local video, audio = {}, {}
    for _, p in ipairs(res.presets or {}) do
      local it = { title = p.title, icon = p.group == 'audio' and 'audiotrack' or 'movie',
                   value = { preset = p.id, url = target, title = target and target_title or nil } }
      if p.group == 'audio' then table.insert(audio, it) else table.insert(video, it) end
    end
    local items = {
      { title = 'Vídeo', hint = o.container, items = video },
      { title = 'Audio', hint = tostring(#audio), items = audio },
      { title = 'Opciones', hint = (o.subtitles and 'subs ' or '') .. (o.sponsorblock ~= 'none' and 'SB ' or '') .. o.container,
        items = options_items(o) },
    }
    show('Descargar · ' .. (target_title or ''), items, {
      footnote = 'Enter descarga con el preset · Opciones: Enter alterna · ⌫ atrás',
    })
  end)
end

local function start_download(params)
  params.url = params.url or state.url
  params.title = params.title or state.title
  params.options = state.dl_options and {
    container = state.dl_options.container, subtitles = state.dl_options.subtitles, chapters = state.dl_options.chapters,
    thumbnail = state.dl_options.thumbnail, metadata = state.dl_options.metadata,
    sponsorblock = state.dl_options.sponsorblock, playlist = state.dl_options.playlist,
  } or nil
  if params.container then
    params.options = params.options or {}
    params.options.container = params.container
    params.container = nil
  end
  rpc.call('ytdl.download', params, function(err, item)
    if err then osd('Descarga: ' .. fail(err, 'ytdl.download')) return end
    state.downloads[item.id] = item
    table.insert(state.download_order, 1, item.id)
    publish()
    set_button_state()
    osd('⬇ En cola: ' .. (item.title or item.url) .. ' · ' .. (item.description or '') .. '\n→ ' .. (item.out_dir or ''))
  end, 30)
end

-- downloads panel ---------------------------------------------------------------------------------

local STATUS_ICON = {
  queued = 'schedule', running = 'downloading', done = 'check_circle', failed = 'error', cancelled = 'cancel',
}

local function download_item(d)
  local actions = {}
  if d.status == 'queued' or d.status == 'running' then
    table.insert(actions, { name = 'cancel', icon = 'cancel', label = 'Cancelar' })
  else
    table.insert(actions, { name = 'retry', icon = 'refresh', label = 'Repetir' })
    table.insert(actions, { name = 'remove', icon = 'delete', label = 'Quitar de la lista' })
  end
  local hint = d.message or d.status
  if d.status == 'failed' and d.error and d.error ~= '' then hint = 'error' end
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
  if #items == 0 then return uosc.message_items('Sin descargas. Abre una URL y usa «Descargar»', 'download') end
  table.insert(items, { title = 'Limpiar terminadas', icon = 'cleaning_services', value = { clear = true }, separator = true,
                        actions = {}, keep_open = true })
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
    if err then show('Descargas', uosc.message_items(fail(err, 'ytdl.downloads.list'), 'error')) return end
    merge_list(rows)
    publish()
    show('Descargas', downloads_items(), { footnote = 'Tab: cancelar / repetir / quitar · Enter muestra detalles · ⌫ atrás' })
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
      uosc.update(base_menu('Descargas', items, { footnote = 'Tab: cancelar / repetir / quitar · ⌫ atrás' }))
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
  show('Estado de yt-dlp', uosc.loading_items())
  rpc.call('ytdl.status', nil, function(err, st)
    if err then show('Estado de yt-dlp', uosc.message_items(fail(err, 'ytdl.status'), 'error')) return end
    local b = st.binary or {}
    local up = st.update or {}
    local s = st.settings or {}
    local items = {
      { title = 'Versión', hint = st.version or 'no encontrado', icon = 'info', selectable = false },
      { title = 'Binario', hint = (b.source or '') .. ' · ' .. (b.path or ''), icon = 'terminal', selectable = false },
      { title = 'Runtime JS', icon = 'javascript', selectable = false,
        hint = b.js_runtime and (b.js_runtime.name .. ' ' .. (b.js_runtime.version or ''))
          or 'ninguno (YouTube puede omitir formatos)' },
      { title = 'Carpeta de vídeo', hint = s.video_dir_resolved or '', icon = 'folder', selectable = false },
      { title = 'Carpeta de audio', hint = s.audio_dir_resolved or '', icon = 'folder', selectable = false },
      { title = 'Actualización automática diaria', hint = bool_hint(up.auto), icon = 'update',
        value = { setting = 'auto_update' }, keep_open = true },
      { title = 'Buscar actualización ahora', hint = up.latest and up.latest ~= '' and ('última: ' .. up.latest) or nil,
        icon = 'system_update_alt', value = { update = 'check' } },
    }
    if up.update_available then
      table.insert(items, { title = 'Instalar ' .. up.latest, icon = 'download', value = { update = 'apply' }, bold = true })
    end
    if up.error and up.error ~= '' then
      table.insert(items, { title = up.error, icon = 'error', selectable = false, muted = true })
    end
    show('Estado de yt-dlp', items)
  end)
end

local function update_action(kind)
  osd(kind == 'apply' and 'Instalando yt-dlp…' or 'Buscando actualización…')
  rpc.call(kind == 'apply' and 'ytdl.update.apply' or 'ytdl.update.check', { force = true }, function(err, st)
    if err then osd(fail(err, 'ytdl.update')) return end
    if st.error and st.error ~= '' then osd('yt-dlp: ' .. st.error)
    elseif kind == 'apply' then osd('yt-dlp actualizado a ' .. (st.installed or '?'))
    elseif st.update_available then osd('Disponible yt-dlp ' .. st.latest .. ' (instalado ' .. (st.installed or '?') .. ')')
    else osd('yt-dlp al día (' .. (st.installed or '?') .. ')') end
    if state.view == 'status' then open_view({ name = 'status' }, false) end
  end, 600)
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

-- URL on the first line of the clipboard, or nil (mpv 0.41 `clipboard/text`; unavailable without a backend).
local function clipboard_url()
  local text = opts.clipboard_text
  if text == '' then
    local ok, value = pcall(mp.get_property, 'clipboard/text')
    text = ok and value or ''
  end
  return as_url(tostring(text or ''):match('^%s*([^\r\n]*)'))
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
    osd('➕ Añadido a la lista: ' .. (title or url))
  else
    mp.commandv('loadfile', url, 'replace')
    osd('Abriendo… ' .. (title or url))
  end
end

local ITEM_ACTIONS = {
  { name = 'append', icon = 'playlist_add', label = 'Añadir a la lista' },
  { name = 'download', icon = 'download', label = 'Descargar' },
}

local function open_item(url)
  return { title = 'Abrir ' .. url, icon = 'play_arrow', value = { open = url }, actions = ITEM_ACTIONS }
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
    table.insert(items, { title = 'Buscar «' .. typed .. '» en YouTube', icon = 'youtube_searched_for',
                          value = { yt_search = typed } })
  end
  if state.clipboard and state.clipboard ~= url then
    table.insert(items, { title = 'Pegar: ' .. ellipsize(state.clipboard, 100), icon = 'content_paste',
                          value = { open = state.clipboard }, actions = ITEM_ACTIONS })
  end
  if #items == 0 then
    items = uosc.message_items('YouTube, Twitch, archive.org, radios… o texto para buscarlo en YouTube', 'link')
  end
  return items
end

local function url_menu(items, extra)
  local menu = {
    type = URL_MENU, title = 'Pega o escribe una URL y pulsa Enter', items = items, callback = { SCRIPT, URL_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    footnote = 'Enter abre · Tab: añadir a la lista o descargar · ctrl+v pega · ⌫ atrás',
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
    type = SEARCH_MENU, title = 'Buscar en YouTube', items = items, callback = { SCRIPT, SEARCH_EVENT },
    search_style = 'palette', search_debounce = 'submit', on_search = 'callback', on_close = 'callback',
    item_actions = ITEM_ACTIONS,
    footnote = 'Enter busca / reproduce · Tab: añadir a la lista o descargar · ⌫ atrás',
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
    show_search(uosc.message_items('mpvd no está disponible: reintentando la conexión, vuelve a pulsar Enter', 'error'))
    return
  end
  set_search(q, 'loading')
  show_search(uosc.loading_items('Buscando «' .. q .. '» en YouTube…'))
  rpc.call('ytdl.search', { query = q, limit = opts.search_limit }, function(err, rows)
    if seq ~= search_seq then return end
    if err then
      set_search(q, 'error')
      show_search(uosc.message_items('No se pudo buscar: ' .. fail(err, 'ytdl.search'), 'error'))
      return
    end
    state.results = { query = q, rows = rows }
    set_search(q, 'done', #rows)
    local items = result_items(rows)
    if #items == 0 then items = uosc.message_items('Sin resultados para «' .. q .. '»', 'search_off') end
    show_search(items)
  end, 45)
end

-- ---------------------------------------------------------------------------------------------
-- events from uosc (one handler; `source` = the menu type whose callback fired)

local function on_event(source, json)
  local ev = utils.parse_json(json or '') or {}
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
    elseif v.clear then
      rpc.call('ytdl.downloads.clear', nil, function() open_view({ name = 'downloads' }, false) end)
    elseif v.update then
      update_action(v.update)
    elseif v.setting == 'auto_update' then
      rpc.call('ytdl.settings.get', nil, function(err, s)
        if err then return end
        rpc.call('ytdl.settings.set', { auto_update = not s.auto_update }, function() open_view({ name = 'status' }, false) end)
      end)
    elseif v.view then
      if v.ensure then mp.commandv('script-message-to', 'mu_core', 'mu-ensure') end
      if v.view == 'root' then state.stack = {} end
      open_view({ name = v.view, args = v })
    end
  elseif ev.type == 'search' then
    if source == URL_MENU then url_typed(ev.query or '') elseif source == SEARCH_MENU then run_search(ev.query or '') end
  elseif ev.type == 'back' then
    table.remove(state.stack)
    if #state.stack == 0 then close_menus() else reopen_current() end
  end
  -- `close` is not used for state (racy, ADR-013): the observer below owns the reset
end

mp.register_script_message(EVENT, function(json) on_event(MENU, json) end)
mp.register_script_message(URL_EVENT, function(json) on_event(URL_MENU, json) end)
mp.register_script_message(SEARCH_EVENT, function(json) on_event(SEARCH_MENU, json) end)

local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if OUR_MENUS[t or ''] then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    if OUR_MENUS[uosc.open_type() or ''] then return end
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
      osd('✓ Descarga completada: ' .. (d.title or d.url) .. '\n→ ' .. ((d.outputs or {})[1] or d.out_dir or ''))
    elseif d.status == 'failed' then
      osd('✗ Descarga fallida: ' .. (d.title or d.url) .. '\n' .. (d.error or ''))
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
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'root' })
end

mp.add_key_binding(nil, 'ytdl-menu', open_root)
mp.add_key_binding(nil, 'ytdl-toggle-audio', toggle_audio)
mp.add_key_binding(nil, 'ytdl-quality', function() state.stack = { { name = 'root' } }; open_view({ name = 'quality' }) end)
mp.add_key_binding(nil, 'ytdl-download', function() state.stack = { { name = 'root' } }; open_view({ name = 'download' }) end)
mp.add_key_binding(nil, 'ytdl-downloads', function() state.stack = { { name = 'root' } }; open_view({ name = 'downloads' }) end)

-- Palettes: bindings, plus messages with an optional initial text for other scripts
-- (`script-message-to mu_ytdl mu-ytdl-open-url [texto]`, `script-message-to mu_ytdl mu-ytdl-search [consulta]`;
-- a search query is submitted right away).
local function open_palette(name, text)
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = name, args = { query = text } })
end
mp.add_key_binding(nil, 'open-url', function() open_palette('open_url') end)
mp.add_key_binding(nil, 'yt-search', function() open_palette('yt_search') end)
mp.register_script_message('mu-ytdl-open-url', function(text) open_palette('open_url', text) end)
mp.register_script_message('mu-ytdl-search', function(query) open_palette('yt_search', query) end)

mp.register_script_message('uosc-version', set_button_state)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button_state() end
  if core and core.mpvd == 'connected' then sync_hook_with_mpvd() end
end)

apply_hook_path()
publish()
msg.info('mu-ytdl loaded')
