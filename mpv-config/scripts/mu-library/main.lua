-- mu-library: «Biblioteca» (H22, ADR-050). Menu over mpvd's library.* (folders chosen by the user, scanned in the
-- background): Películas / Series › temporada › episodio with progress (✓ / %), Buscar, Carpetas (add the folder of the
-- current file or type a path, remove, rescan), Ajustes (automatic next episode, TMDB metadata with the user's key,
-- subtitles from OpenSubtitles with the user's account) and «Buscar subtítulos en internet» for the current file.
-- Next episode: at the end of an episode of the library (eof, nothing else in the playlist) a cancellable countdown
-- (Esc cancels, Enter plays now) loads the following one; mu-intro's "skip credits" already moves to the next file
-- itself (playlist-next / insert-next), so there is never a second jump. Start screen rows «seguir viendo» /
-- «siguiente episodio» are published ready for mu-menu in user-data/mu/library/home (refresh: script-message
-- mu-library-home). Script name: mu_library. State: user-data/mu/library.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')
local nav = require('mu.nav')
local tr = require('mu.i18n').t
local N = nav.new()

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-library-event'
local MENU = 'mu-library'
local INPUT = 'mu-library-input'           -- palette used as a text box (search, a path, keys)
local INPUT_EVENT = 'mu-library-input-event'
local ROOT_TITLE = 'Biblioteca'
local SUB_TITLE = 'OpenSubtitles'

local opts = {
  auto_next = true,          -- play the next episode of the library when one ends
  countdown_seconds = 5,     -- 0 = at once
  home_rows = 6,             -- «seguir viendo» / «siguiente episodio» rows for the start screen
  osd_seconds = 3,
  open_command = '',         -- program that opens a URL ('' = xdg-open / open / explorer); tests use `true`
}
options.read_options(opts, 'mu-library')
local P = prefs.ns('mu-library', { auto_next = opts.auto_next })
P:apply_opts(opts, 'mu-library', { 'auto_next' })

local state = {
  view = '', stack = {}, items = {}, last_error = '', force_open = false, input = nil,
  home = {}, home_raw = {}, counts = {}, settings = {}, scanning = false, scan_progress = 0,
  path = '', next = nil, current = nil, subs = nil, last_subs = '', last_lang = '', subs_status = '',
  quota = nil,   -- C4: cupo de OpenSubtitles que queda hoy, tal como lo cuenta mpvd
}
local cd = nil   -- running countdown { left=, last=, next=, timer= }

local function publish()
  mp.set_property_native('user-data/mu/library', {
    view = state.view, depth = #state.stack, items = state.items, last_error = state.last_error,
    input = state.input and state.input.mode or '', home = state.home, auto_next = opts.auto_next,
    quota = state.quota and state.quota.text or '',
    countdown = cd and math.max(0, math.ceil(cd.left)) or 0,
    next_path = state.next and state.next.path or '', next_title = state.next and (state.next.full_title or '') or '',
    next_source = state.next and state.next.source or '',
    scanning = state.scanning, scan_progress = state.scan_progress, counts = state.counts,
    subs_status = state.subs_status, last_subs = state.last_subs,
  })
end

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

local function hms(s)
  s = math.floor(tonumber(s) or 0)
  if s >= 3600 then return string.format('%d:%02d:%02d', s / 3600, (s % 3600) / 60, s % 60) end
  return string.format('%d:%02d', s / 60, s % 60)
end

local function basename(p) return (tostring(p or ''):match('[^/\\]+$')) or tostring(p or '') end
local function dirname(p) return (tostring(p or ''):match('^(.*)[/\\][^/\\]*$')) or '' end
local function strip_file(p) return (tostring(p or ''):gsub('^file://', '')) end
local function current_path() return strip_file(mp.get_property('path') or '') end
local function is_local(p) return p ~= '' and p:match('^%a[%w+.-]*://') == nil end

-- ✓ watched · 45 % started · nothing when new
local function progress_hint(it)
  if it.finished then return '✓' end
  local p = tonumber(it.progress) or 0
  if p > 0.005 then return string.format('%d %%', math.floor(p * 100 + 0.5)) end
  return nil
end

-- ---------------------------------------------------------------------------------------------
-- start screen rows (ready-made uosc items for mu-menu)

local function home_item(r)
  local hint
  if r.row == 'next' then hint = tr('Siguiente episodio')
  elseif r.duration and r.duration > 0 then hint = hms(r.position) .. ' / ' .. hms(r.duration)
  else hint = progress_hint(r) end
  return { title = r.full_title or r.title or basename(r.path), hint = hint,
           icon = r.row == 'next' and 'skip_next' or (r.kind == 'episode' and 'live_tv' or 'movie'),
           value = { open = r.path, library = true, row = r.row } }
end

local function refresh_home(cb)
  if not rpc.connected() then if cb then cb() end return end
  rpc.call('library.continue', { limit = opts.home_rows }, function(err, rows)
    if err then fail(err, 'library.continue')
    else
      state.home_raw = rows or {}
      local items = {}
      for _, r in ipairs(state.home_raw) do table.insert(items, home_item(r)) end
      state.home = items
      publish()
    end
    if cb then cb() end
  end, 15)
end

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 300 then break end
    table.insert(out, { title = it.title or '', hint = it.hint or '', value = it.value or '', active = it.active or false,
                        submenu = it.items ~= nil and #it.items or 0 })
  end
  state.items = out
end

local function base_menu(title, items, extra)
  local menu = { type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, on_close = 'callback',
                 keep_open = true, search_submenus = false }
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

local function reopen_current(force)
  local spec = state.stack[#state.stack]
  if spec then
    state.force_open = force or false
    open_view(spec, false)
  end
end

local function require_mpvd(title)
  if rpc.connected() then return true end
  show(title, uosc.message_items(tr('mpvd no está conectado'), 'error'))
  return false
end

local function still(view) return state.view == view end

views.root = function()
  if not require_mpvd(ROOT_TITLE) then return end
  show(ROOT_TITLE, uosc.loading_items())
  rpc.call('library.status', nil, function(err, st)
    if not still('root') then return end
    if err then show(ROOT_TITLE, uosc.message_items(fail(err, 'library.status'), 'error')) return end
    state.counts = { movies = st.movies, shows = st.shows, episodes = st.episodes, folders = st.folders }
    state.settings = st.settings or {}
    state.scanning = st.scanning
    publish()
    refresh_home(function()
      if not still('root') then return end
      local items = {}
      if #state.home > 0 then
        table.insert(items, { title = tr('Seguir viendo'), hint = tostring(#state.home), icon = 'history',
                              items = state.home, separator = true })
      end
      if state.next and state.next.source == 'library' then
        table.insert(items, { title = tr('Siguiente episodio'), hint = state.next.full_title, icon = 'skip_next',
                              value = { play = state.next.path }, separator = true })
      end
      if (st.folders or 0) == 0 then
        table.insert(items, { title = tr('Tu biblioteca está vacía: añade la carpeta de tus películas o series'),
                              icon = 'info', selectable = false, muted = true })
      end
      table.insert(items, { title = tr('Películas'), hint = tostring(st.movies or 0), icon = 'movie',
                            value = { view = 'movies' } })
      table.insert(items, { title = tr('Series'), hint = st.shows and st.shows > 0
                              and string.format('%d · %d episodios', st.shows, st.episodes or 0) or '0',
                            icon = 'live_tv', value = { view = 'shows' } })
      table.insert(items, { title = tr('Buscar en la biblioteca…'), icon = 'search', value = { input = 'search' } })
      table.insert(items, { title = tr('Explorar las carpetas del equipo'), icon = 'folder_open',
                            hint = tr('discos, pinchos USB y tus carpetas'), value = { view = 'explore' },
                            separator = true })
      local here = current_path()
      if is_local(here) then
        table.insert(items, { title = tr('Buscar subtítulos en internet'), icon = 'subtitles',
                              hint = state.settings.osub_active and SUB_TITLE or 'desactivado',
                              value = { view = 'subs' } })
      end
      local scan = state.scanning and (st.progress and string.format('escaneando %d %%', math.floor(st.progress * 100)))
      table.insert(items, { title = tr('Carpetas'), hint = scan or tostring(st.folders or 0), icon = 'folder',
                            value = { view = 'folders' } })
      table.insert(items, { title = tr('Ajustes'), icon = 'settings', value = { view = 'settings' } })
      show(ROOT_TITLE, items, { footnote = tr('Enter abre · ⌫ atrás · Esc cierra') })
    end)
  end, 15)
end

-- H64 · explorar las carpetas del equipo. Es la única forma de elegir una película SIN TECLADO, que es el caso de
-- una Raspberry conectada al televisor. De ahí dos decisiones de forma: (a) lo que se puede hacer con una carpeta
-- son FILAS y no acciones de Tab, porque el mando de la tele no tiene Tab; y (b) hay una fila «Subir», porque en
-- ese mando la tecla «atrás» cierra el menú (es `close`), no sube un nivel.
local PLACE_ICON = { user = 'folder_special', library = 'video_library', drive = 'usb', home = 'home' }
local KIND_ICON = { dir = 'folder', video = 'movie', audio = 'music_note', image = 'image', playlist = 'queue_music' }
local browse_seq = 0

local function size_hint(n)
  n = tonumber(n) or 0
  if n >= 1024 * 1024 * 1024 then return string.format('%.1f GB', n / 1073741824) end
  if n >= 1024 * 1024 then return string.format('%.0f MB', n / 1048576) end
  return string.format('%.0f kB', math.max(1, n / 1024))
end

local function count_hint(n)
  n = tonumber(n) or 0
  if n < 0 then return 'no se puede leer' end
  if n == 0 then return 'vacía' end
  return n .. (n == 1 and ' elemento' or ' elementos')
end

views.explore = function()
  local title = tr('Explorar carpetas')
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  rpc.call('files.places', nil, function(err, res)
    if not still('explore') then return end
    if err then show(title, uosc.message_items(fail(err, 'files.places'), 'error')) return end
    local items = {}
    for _, pl in ipairs((res or {}).places or {}) do
      items[#items + 1] = { title = pl.title, hint = pl.path, icon = PLACE_ICON[pl.kind] or 'folder',
                            value = { view = 'browse', path = pl.path } }
    end
    if #items == 0 then items = uosc.message_items(tr('No encuentro ninguna carpeta por donde empezar'), 'info') end
    show(title, items, { footnote = tr('Enter entra en la carpeta · ⌫ atrás') })
  end, 15)
end

views.browse = function(args)
  local path = tostring(args.path or '')
  local title = basename(path) ~= '' and basename(path) or path
  if not require_mpvd(title) then return end
  browse_seq = browse_seq + 1
  local seq = browse_seq
  show(title, uosc.loading_items())
  rpc.call('files.browse', { path = path }, function(err, res)
    -- cada carpeta es un marco más de la pila, así que todas se llaman «browse»: sin este testigo, entrar rápido
    -- en dos carpetas pintaría la primera respuesta que llegase, que no tiene que ser la de dentro
    if seq ~= browse_seq or not still('browse') then return end
    if err then show(title, uosc.message_items(fail(err, 'files.browse'), 'error')) return end
    local items = {}
    if (res.parent or '') ~= '' then
      items[#items + 1] = { title = tr('Subir a %s'):format(basename(res.parent) ~= '' and basename(res.parent)
                                                       or res.parent),
                            icon = 'arrow_upward', value = { view = 'browse', path = res.parent } }
    end
    if (res.files or 0) > 0 then
      items[#items + 1] = { title = tr('Reproducir toda esta carpeta'), icon = 'playlist_play',
                            hint = count_hint(res.files), value = { play = res.path } }
    end
    items[#items + 1] = { title = tr('Añadir esta carpeta a la biblioteca'), icon = 'video_library',
                          value = { add = res.path }, separator = true }
    for _, e in ipairs(res.entries or {}) do
      items[#items + 1] = { title = e.name, icon = KIND_ICON[e.kind] or 'insert_drive_file',
                            hint = e.dir and count_hint(e.items) or size_hint(e.size),
                            value = e.dir and { view = 'browse', path = e.path } or { play = e.path } }
    end
    if res.truncated then
      items[#items + 1] = { title = tr('Hay más de lo que caben en la lista'), icon = 'info', selectable = false,
                            muted = true, hint = tr('entra en una subcarpeta') }
    end
    show(title, items, { footnote = res.path .. ' · Enter abre · ⌫ atrás' })
  end, 20)
end

local function play_item(it, title)
  local here = current_path()
  return { title = title or it.label or it.title, hint = progress_hint(it), icon = it.finished and 'check' or 'play_arrow',
           value = { play = it.path }, active = it.path == here or nil, muted = (it.exists == false) or nil }
end

views.movies = function()
  if not require_mpvd('Películas') then return end
  show(tr('Películas'), uosc.loading_items())
  rpc.call('library.list', { kind = 'movies' }, function(err, rows)
    if not still('movies') then return end
    if err then show(tr('Películas'), uosc.message_items(fail(err, 'library.list'), 'error')) return end
    local items = {}
    for _, m in ipairs(rows or {}) do table.insert(items, play_item(m, m.label)) end
    if #items == 0 then items = uosc.message_items(tr('No hay películas en tus carpetas'), 'movie') end
    show(tr('Películas'), items, { footnote = tr('Enter reproduce · ⌫ atrás'), search_style = 'on_demand' })
  end, 15)
end

views.shows = function()
  if not require_mpvd('Series') then return end
  show(tr('Series'), uosc.loading_items())
  rpc.call('library.list', { kind = 'shows' }, function(err, rows)
    if not still('shows') then return end
    if err then show(tr('Series'), uosc.message_items(fail(err, 'library.list'), 'error')) return end
    local items = {}
    for _, s in ipairs(rows or {}) do
      local hint = string.format('%d/%d ✓', s.watched or 0, s.episodes or 0)
      if s.watched == s.episodes and (s.episodes or 0) > 0 then hint = '✓'
      elseif (s.watched or 0) == 0 then hint = string.format('%d episodios', s.episodes or 0) end
      table.insert(items, { title = s.title, hint = hint, icon = 'live_tv',
                            value = { view = 'show', key = s.key, title = s.title } })
    end
    if #items == 0 then items = uosc.message_items(tr('No hay series en tus carpetas'), 'live_tv') end
    show(tr('Series'), items, { footnote = tr('Enter abre · ⌫ atrás'), search_style = 'on_demand' })
  end, 15)
end

views.show = function(args)
  local title = args.title or 'Serie'
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  rpc.call('library.list', { show = args.key }, function(err, res)
    if not still('show') then return end
    if err then show(title, uosc.message_items(fail(err, 'library.list'), 'error')) return end
    local items = {}
    for _, s in ipairs(res.seasons or {}) do
      local hint = (s.watched == s.episodes) and '✓' or string.format('%d/%d', s.watched or 0, s.episodes or 0)
      table.insert(items, { title = s.label, hint = hint, icon = 'folder',
                            value = { view = 'season', key = args.key, season = s.season, title = s.label } })
    end
    show(title, items, { footnote = tr('Enter abre · ⌫ atrás') })
  end, 15)
end

views.season = function(args)
  local title = args.title or 'Temporada'
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  rpc.call('library.list', { show = args.key, season = args.season }, function(err, res)
    if not still('season') then return end
    if err then show(title, uosc.message_items(fail(err, 'library.list'), 'error')) return end
    local items, sel = {}, nil
    for i, e in ipairs(res.episodes or {}) do
      table.insert(items, play_item(e))
      if not sel and not e.finished then sel = i end
    end
    show(title, items, { footnote = tr('Enter reproduce · ⌫ atrás'), selected_index = sel })
  end, 15)
end

views.folders = function()
  if not require_mpvd('Carpetas') then return end
  show(tr('Carpetas'), uosc.loading_items())
  rpc.call('library.folders.list', nil, function(err, rows)
    if not still('folders') then return end
    if err then show(tr('Carpetas'), uosc.message_items(fail(err, 'library.folders.list'), 'error')) return end
    local items = {}
    for _, f in ipairs(rows or {}) do
      table.insert(items, { title = f.path, icon = f.exists and 'folder' or 'folder_off',
        hint = f.exists and ((f.files or 0) .. ' archivos') or 'no se encuentra',
        value = { folder = f.path },
        actions = { { name = 'rescan', icon = 'refresh', label = tr('Reescanear') },
                    { name = 'remove', icon = 'delete', label = tr('Quitar') } } })
    end
    if #items > 0 then items[#items].separator = true end
    local here = current_path()
    if is_local(here) then
      table.insert(items, { title = tr('Añadir la carpeta del archivo actual'), hint = dirname(here), icon = 'create_new_folder',
                            value = { add = dirname(here) } })
    end
    table.insert(items, { title = tr('Buscarla explorando el equipo…'), icon = 'folder_open',
                          hint = tr('sin teclear nada'), value = { view = 'explore' } })
    table.insert(items, { title = tr('Escribir o pegar una ruta…'), icon = 'edit', value = { input = 'folder' } })
    if #(rows or {}) > 0 then
      table.insert(items, { title = tr('Reescanear todo'), icon = 'refresh', value = { rescan = true },
        hint = state.scanning and string.format('escaneando %d %%', math.floor(state.scan_progress * 100)) or nil })
    end
    show(tr('Carpetas'), items, { footnote = tr('Enter: Reescanear / Quitar (Tab) · ⌫ atrás') })
  end, 15)
end

local RESYNC_LABEL = { auto = 'si no es exacto', always = 'siempre', never = 'nunca' }
local RESYNC_NEXT = { auto = 'always', always = 'never', never = 'auto' }

-- C4 · alta guiada de OpenSubtitles. Hace falta una Api-Key (gratis) y escribirla a mano desde el mando de un sofá es
-- absurdo: paso 1 abre la página en el navegador, paso 2 la pega del portapapeles. La clave la lee mpvd por su propia
-- conexión IPC (como la clave de emisión, ADR-061): no pasa por este script ni por el OSD, así que no acaba en un log.
local function open_in_browser(url)
  local platform = mp.get_property_native('platform') or ''
  local cmd = opts.open_command
  if cmd == '' then cmd = platform == 'windows' and 'explorer' or (platform == 'darwin' and 'open' or 'xdg-open') end
  mp.command_native_async({ name = 'subprocess', args = { cmd, url }, detach = true,
                            playback_only = false, capture_stdout = false }, function() end)
end

local function paste_secret(field, what)
  rpc.call('library.settings.paste', { field = field }, function(err, res)
    if err then osd(what .. ': ' .. fail(err, 'library.settings.paste')); return end
    if type(res) == 'table' and type(res.settings) == 'table' then state.settings = res.settings end
    osd(string.format('%s guardada (%d caracteres)', what, (res and res.length) or 0))
    state.quota = nil
    publish()
    reopen_current()
  end, 10)
end

-- Cupo que queda hoy: lo dice mpvd (de la cuenta si la hay, o de la última descarga; y dice de dónde sale el dato,
-- porque un número viejo mal presentado es peor que no decir nada).
local function ask_quota()
  state.quota = { text = 'consultando…' }
  rpc.call('library.subs.quota', nil, function(err, q)
    if err or type(q) ~= 'table' then
      state.quota = { text = 'no se pudo consultar' }
    elseif q.remaining == nil then
      state.quota = { text = (q.allowed and (tostring(q.allowed) .. ' al día') or 'se sabrá al descargar el primero') }
    else
      local txt = tostring(q.remaining) .. ' descargas'
      if q.allowed then txt = txt .. ' de ' .. tostring(q.allowed) end
      if q.source and q.source ~= '' then txt = txt .. ' · ' .. q.source end
      state.quota = { text = txt, remaining = q.remaining }
    end
    publish()
    if state.view == 'settings' then reopen_current() end
  end, 15)
end

local function osub_rows(s, out)
  if not s.has_osub_key then
    out[#out + 1] = { title = tr('Paso 1 · Abrir la página de la clave (es gratis)'), icon = 'open_in_new',
                      hint = tr('opensubtitles.com › API › Consumers'), value = { osub_help = true } }
    out[#out + 1] = { title = tr('Paso 2 · Pegar la clave del portapapeles'), icon = 'content_paste',
                      hint = tr('cópiala en el navegador y pulsa aquí'), value = { paste = 'osub_api_key' } }
    out[#out + 1] = { title = tr('…o escribirla a mano'), icon = 'keyboard', value = { input = 'osub_api_key' } }
  else
    out[#out + 1] = { title = tr('Api-Key de OpenSubtitles'), hint = tr('guardada'), icon = 'key',
                      value = { input = 'osub_api_key' } }
    local q = state.quota
    out[#out + 1] = { title = tr('Cupo de hoy'), icon = 'speed', selectable = false, muted = true,
                      hint = q and q.text or 'consultando…' }
  end
end

views.settings = function()
  if not require_mpvd('Ajustes') then return end
  rpc.call('library.settings.get', nil, function(err, s)
    if not still('settings') then return end
    if err then show(tr('Ajustes'), uosc.message_items(fail(err, 'library.settings.get'), 'error')) return end
    state.settings = s
    local function onoff(v) return v and 'sí' or 'no' end
    local items = {
      { title = tr('Siguiente episodio automático'), hint = onoff(opts.auto_next), icon = 'skip_next',
        active = opts.auto_next, value = { toggle = 'auto_next' }, separator = true },
      { title = tr('Carátulas y datos de internet (TMDB)'), hint = onoff(s.tmdb_enabled), icon = 'image',
        active = s.tmdb_enabled, value = { set = 'tmdb_enabled', to = not s.tmdb_enabled } },
      { title = tr('Clave de TMDB…'), hint = s.has_tmdb_key and 'guardada' or 'sin clave', icon = 'key',
        value = { input = 'tmdb_key' }, separator = true },
      { title = tr('Subtítulos de internet (OpenSubtitles)'), hint = onoff(s.osub_enabled), icon = 'subtitles',
        active = s.osub_enabled, value = { set = 'osub_enabled', to = not s.osub_enabled } },
    }
    osub_rows(s, items)
    for _, it in ipairs({
      { title = tr('Usuario de OpenSubtitles…'), hint = s.osub_username ~= '' and s.osub_username or 'sin cuenta',
        icon = 'person', value = { input = 'osub_username' } },
      { title = tr('Contraseña de OpenSubtitles…'), hint = s.has_osub_password and 'guardada' or '', icon = 'password',
        value = { input = 'osub_password' } },
      { title = tr('Idiomas de los subtítulos…'), hint = s.osub_languages, icon = 'translate',
        value = { input = 'osub_languages' } },
      { title = tr('Resincronizar con la voz'), hint = RESYNC_LABEL[s.osub_resync] or s.osub_resync, icon = 'sync',
        value = { set = 'osub_resync', to = RESYNC_NEXT[s.osub_resync] or 'auto' } },
    }) do items[#items + 1] = it end
    show(tr('Ajustes'), items, { footnote = tr('Las claves se guardan solo en tu equipo · ⌫ atrás') })
    if s.has_osub_key and state.quota == nil then ask_quota() end
  end, 15)
end

-- ---------------------------------------------------------------------------------------------
-- subtitles from the internet (current file)

local function add_subtitle(res, silent)
  local title = SUB_TITLE .. ' · ' .. (res.language ~= '' and res.language or '?')
    .. (res.resync == 'done' and ' (resincronizado)' or '')
  mp.command_native({ 'sub-add', res.srt, 'select', title, res.language or '' })
  state.last_subs = res.srt
  state.last_lang = res.language or ''
  state.subs_status = 'added'
  publish()
  if not silent then
    local extra = ''
    if res.resync == 'pending' then extra = ' · resincronizando con la voz…'
    elseif res.resync == 'failed' then extra = ' · sin resincronizar (' .. (res.resync_reason or '') .. ')' end
    osd(tr('💬 Subtítulos %s añadidos%s'):format(res.language or '', extra), 4)
  end
end

local function download_subs(file_id)
  local path = current_path()
  if not is_local(path) then osd(tr('Solo en archivos locales')) return end
  state.subs_status = 'downloading'
  publish()
  osd(tr('💬 Descargando subtítulos…'))
  local audio = mp.get_property_native('current-tracks/audio') or {}
  rpc.call('library.subs.download', { path = path, file_id = file_id, audio_lang = audio.lang, notify = SCRIPT },
    function(err, res)
      if err then
        state.subs_status = 'error'
        osd(tr('Subtítulos: %s'):format(fail(err, 'library.subs.download')), 5)
        return
      end
      if current_path() ~= path then return end
      add_subtitle(res)
    end, 90)
end

-- C4 · si todavía no hay clave, aquí no se busca nada: se enseñan los dos pasos del alta. Un «falta la Api-Key» y
-- nada más es un callejón sin salida justo cuando alguien quiere ver una película.
local function subs_setup_items(h)
  local items = {
    { title = tr('Para buscar subtítulos hace falta una clave de OpenSubtitles'), icon = 'info', selectable = false,
      muted = true },
    { title = tr('Es gratis y se saca en dos minutos'), icon = 'info', selectable = false, muted = true,
      separator = true },
    { title = tr('Paso 1 · Abrir la página de la clave'), icon = 'open_in_new',
      hint = tr('opensubtitles.com › API › Consumers'), value = { osub_help = true } },
    { title = tr('Paso 2 · Pegar la clave del portapapeles'), icon = 'content_paste',
      hint = tr('cópiala en el navegador y pulsa aquí'), value = { paste = 'osub_api_key' } },
    { title = tr('…o escribirla a mano'), icon = 'keyboard', value = { input = 'osub_api_key' }, separator = true },
    { title = tr('Ajustes de la biblioteca'), icon = 'settings', value = { view = 'settings' } },
  }
  if h and h.has_key and not h.enabled then
    table.insert(items, 1, { title = tr('La clave está guardada, pero los subtítulos de internet están apagados'),
                             icon = 'toggle_off', hint = tr('encenderlos'), value = { set = 'osub_enabled', to = true } })
  end
  return items
end

local search_subs_now   -- definido justo debajo: no es una vista navegable, es el cuerpo de `subs`

views.subs = function()
  local title = tr('Subtítulos de internet')
  if not require_mpvd(title) then return end
  local path = current_path()
  if not is_local(path) then show(title, uosc.message_items(tr('Abre un archivo de tu equipo'), 'info')) return end
  show(title, uosc.loading_items(tr('Buscando en OpenSubtitles…')))
  rpc.call('library.subs.help', nil, function(herr, h)
    if not still('subs') then return end
    if not herr and type(h) == 'table' and not h.active then
      state.subs_status = 'sin clave'
      state.osub_help = h
      publish()
      show(title, subs_setup_items(h))
      return
    end
    search_subs_now(path, title)
  end, 10)
end

search_subs_now = function(path, title)
  state.subs_status = 'searching'
  publish()
  rpc.call('library.subs.search', { path = path }, function(err, res)
    if not still('subs') then return end
    if err then
      state.subs_status = 'error'
      local m = fail(err, 'library.subs.search')
      show(title, { { title = m, icon = 'error', selectable = false, muted = true },
                    { title = tr('Ajustes de la biblioteca'), icon = 'settings', value = { view = 'settings' } } })
      return
    end
    state.subs_status = 'results'
    state.subs = res
    local items = {}
    for _, r in ipairs(res.results or {}) do
      local tags = {}
      if r.hash_match then tags[#tags + 1] = '✓ exacto' end
      if r.hearing_impaired then tags[#tags + 1] = 'SDH' end
      if r.machine_translated or r.ai_translated then tags[#tags + 1] = 'traducción automática' end
      tags[#tags + 1] = tostring(r.downloads or 0) .. ' descargas'
      table.insert(items, { title = r.language .. ' · ' .. (r.release ~= '' and r.release or r.file_name),
                            hint = table.concat(tags, ' · '), icon = r.hash_match and 'verified' or 'subtitles',
                            value = { sub = r.file_id } })
    end
    if #items == 0 then
      items = uosc.message_items(tr('No hay subtítulos en %s para este vídeo'):format(res.languages or ''),
                                 'subtitles_off')
    else
      table.insert(items, 1, { title = tr('Descargar el mejor'), hint = items[1].title, icon = 'download',
                               value = { sub = res.results[1].file_id }, separator = true })
    end
    show(title, items, { footnote = tr('✓ exacto = hecho para este mismo archivo · Enter descarga y activa') })
  end, 60)
end

-- ---------------------------------------------------------------------------------------------
-- text box: search, a folder path, keys

local INPUT_TITLES = {
  search = 'Buscar en la biblioteca', folder = 'Carpeta con películas o series', tmdb_key = 'Clave de TMDB (v3 o token)',
  osub_api_key = 'Api-Key de OpenSubtitles', osub_username = 'Usuario de OpenSubtitles',
  osub_password = 'Contraseña de OpenSubtitles', osub_languages = 'Idiomas (por ejemplo: es,en)',
}

local function input_menu(query, items)
  local inp = state.input
  query = query or ''
  inp.query = query
  if not items then
    items = {}
    if query ~= '' then
      local shown = inp.mode == 'osub_password' and string.rep('•', #query) or query
      local verb = inp.mode == 'folder' and 'Añadir: ' or 'Guardar: '
      table.insert(items, { title = verb .. shown, icon = 'check', value = { save = query } })
    else
      local empty = inp.mode == 'search' and 'Escribe el título de una película, serie o episodio'
        or (inp.mode:match('^osub_') or inp.mode == 'tmdb_key') and 'Escribe o pega (vacío + Enter en «Borrar» la olvida)'
        or 'Escribe o pega la ruta'
      table.insert(items, { title = empty, icon = 'edit', selectable = false, muted = true })
      if inp.mode ~= 'search' and inp.mode ~= 'folder' and inp.mode ~= 'osub_languages' then
        table.insert(items, { title = tr('Borrar el valor guardado'), icon = 'delete', value = { save = '' } })
      end
    end
  end
  return { type = INPUT, title = INPUT_TITLES[inp.mode] or 'Escribe', items = items,
    callback = { SCRIPT, INPUT_EVENT }, search_style = 'palette', search_debounce = inp.mode == 'search' and 250 or 0,
    on_search = 'callback', on_close = 'callback', search_suggestion = inp.mode == 'osub_password' and '' or query,
    footnote = inp.mode == 'search' and 'Enter reproduce · ⌫ en vacío vuelve' or 'Enter guarda · ⌫ en vacío vuelve' }
end

local function open_input(mode, text)
  state.input = { mode = mode, query = text or '' }
  publish()
  uosc.open(input_menu(text or ''))
end

local function close_input(back)
  state.input = nil
  publish()
  uosc.close(INPUT)
  if back then reopen_current(true) end
end

local function search_results(query)
  local inp = state.input
  rpc.call('library.search', { q = query, limit = 30 }, function(err, res)
    if not state.input or state.input.mode ~= 'search' or inp.query ~= query then return end
    local items = {}
    if err then
      items = uosc.message_items(fail(err, 'library.search'), 'error')
    else
      for _, s in ipairs(res.shows or {}) do
        table.insert(items, { title = s.title, hint = tr('serie'), icon = 'live_tv',
                              value = { view = 'show', key = s.key, title = s.title } })
      end
      for _, m in ipairs(res.movies or {}) do table.insert(items, play_item(m, m.label)) end
      for _, e in ipairs(res.episodes or {}) do table.insert(items, play_item(e, e.full_title)) end
      if #items == 0 then
        items = uosc.message_items(tr('Nada en la biblioteca con «%s»'):format(query), 'search_off')
      end
    end
    remember(items)
    publish()
    uosc.update(input_menu(query, items))
  end, 15)
end

local function scan_started(res)
  state.scanning = true
  state.scan_progress = 0
  publish()
  osd(tr('📚 Escaneando %s…'):format(res and res.path or tr('la biblioteca')))
end

local function add_folder(path)
  rpc.call('library.folders.add', { path = path, notify = SCRIPT }, function(err, res)
    if err then osd(tr('Carpeta: %s'):format(fail(err, 'library.folders.add')), 5) return end
    if res.added then scan_started(res) else osd(tr('Esa carpeta ya estaba: reescaneando')) end
    if still('folders') then reopen_current() end
  end, 15)
end

local function set_setting(key, value, after)
  rpc.call('library.settings.set', { [key] = value }, function(err, s)
    if err then osd(tr('Ajustes: %s'):format(fail(err, 'library.settings.set')), 5) return end
    state.settings = s
    publish()
    if after then after(s) end
  end, 15)
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local inp = state.input
  if not inp then return end
  if ev.type == 'search' then
    local q = ev.query or ''
    if inp.mode == 'search' and q ~= '' then
      inp.query = q
      uosc.update(input_menu(q, uosc.loading_items(tr('Buscando…'))))
      search_results(q)
    else
      uosc.update(input_menu(q))
    end
  elseif ev.type == 'back' then
    close_input(true)
  elseif ev.type == 'activate' and type(ev.value) == 'table' then
    local v = ev.value
    if v.play then
      close_input(false)
      uosc.close(MENU)
      mp.commandv('loadfile', v.play, 'replace')
      osd(tr('▶ %s'):format(basename(v.play)))
    elseif v.view then
      close_input(false)
      state.force_open = true
      open_view({ name = v.view, args = v })
    elseif v.save ~= nil then
      local text, mode = v.save, inp.mode
      if mode == 'folder' then
        close_input(true)
        add_folder(mp.command_native({ 'expand-path', text }) or text)
      elseif mode ~= 'search' then
        close_input(true)
        set_setting(mode, text, function(s)
          osd(text == '' and 'Borrado' or 'Guardado')
          -- a key typed while its service was off switches it on (the user clearly wants it)
          if text ~= '' and mode == 'tmdb_key' and not s.tmdb_enabled then set_setting('tmdb_enabled', true) end
          if text ~= '' and mode == 'osub_api_key' and not s.osub_enabled then set_setting('osub_enabled', true) end
          if still('settings') then reopen_current() end
        end)
      end
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- next episode: countdown at the end of an episode of the library

local function play_next(nxt)
  if not nxt or not nxt.path then return end
  mp.commandv('loadfile', nxt.path, 'replace')
  mp.set_property_bool('pause', false)   -- keep-open paused the finished episode: the next one must play
  osd(tr('▶ %s'):format(nxt.full_title or basename(nxt.path)))
end

local function cancel_countdown(by_user)
  if not cd then return end
  cd.timer:kill()
  cd = nil
  mp.remove_key_binding('mu-library-cancel')
  mp.remove_key_binding('mu-library-now')
  if by_user then osd(tr('Siguiente episodio cancelado')) end
  publish()
end

local function countdown_text()
  return string.format('⏭ Siguiente episodio en %d s: %s\nEsc cancela · Enter ya', math.max(1, math.ceil(cd.left)),
                       cd.next.full_title or basename(cd.next.path))
end

local function start_countdown(nxt)
  if cd then return end
  local n = tonumber(opts.countdown_seconds) or 5
  if n <= 0 then play_next(nxt) return end
  cd = { left = n, last = mp.get_time(), next = nxt }
  mp.add_forced_key_binding('ESC', 'mu-library-cancel', function() cancel_countdown(true) end)
  mp.add_forced_key_binding('ENTER', 'mu-library-now', function()
    local target = cd and cd.next
    cancel_countdown(false)
    play_next(target)
  end)
  mp.osd_message(countdown_text(), 1.5)
  cd.timer = mp.add_periodic_timer(0.25, function()
    if not cd then return end
    local now = mp.get_time()
    local before = math.ceil(cd.left)
    cd.left = cd.left - (now - cd.last)
    cd.last = now
    if cd.left <= 0 then
      local target = cd.next
      cancel_countdown(false)
      play_next(target)
    elseif math.ceil(cd.left) ~= before then
      mp.osd_message(countdown_text(), 1.5)
      publish()
    end
  end)
  publish()
end

local function playlist_has_next(entry_id)
  local pl = mp.get_property_native('playlist') or {}
  if (mp.get_property('loop-playlist') or 'no') ~= 'no' and #pl > 1 then return true end
  for i, e in ipairs(pl) do
    if (entry_id and e.id == entry_id) or (not entry_id and e.current) then return i < #pl end
  end
  return false
end

local function maybe_auto_next(nxt, entry_id)
  if not opts.auto_next or not nxt or nxt.source ~= 'library' then return end
  if playlist_has_next(entry_id) then return end   -- the playlist (or mu-intro) already goes on
  start_countdown(nxt)
end

local function refresh_next(path)
  if not rpc.connected() or not is_local(path) then return end
  rpc.call('library.next', { path = path }, function(err, res)
    if err then fail(err, 'library.next') return end
    if current_path() ~= path then return end
    state.next = res and res.next or nil
    state.current = res and res.current or nil
    publish()
    if mp.get_property_native('eof-reached') then maybe_auto_next(state.next) end
  end, 20)
end

mp.register_event('file-loaded', function()
  cancel_countdown(false)
  state.path = current_path()
  state.next, state.current = nil, nil
  state.subs, state.subs_status = nil, ''
  publish()
  refresh_next(state.path)
end)

-- keep-open=yes (mpv.conf): the last file stays paused at its end, no end-file → eof-reached
mp.observe_property('eof-reached', 'bool', function(_, eof)
  if eof then
    maybe_auto_next(state.next)
  elseif cd then
    cancel_countdown(false)   -- seeking back into the episode
  end
end)

-- keep-open=no: end-file eof, then idle
mp.register_event('end-file', function(ev)
  local nxt = state.next
  if ev.reason == 'eof' and nxt then
    mp.add_timeout(0.05, function()
      if mp.get_property_native('idle-active') then maybe_auto_next(nxt, ev.playlist_entry_id) end
    end)
  elseif ev.reason ~= 'eof' then
    cancel_countdown(false)
  end
  mp.add_timeout(0.5, function() refresh_home() end)   -- positions were just saved by mu-menu
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

local function forget_view(name)
  for i = #state.stack, 1, -1 do
    if state.stack[i].name == name then table.remove(state.stack, i) end
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.play or v.open then
      uosc.close(MENU)
      local p = v.play or v.open
      mp.commandv('loadfile', p, 'replace')
      osd(tr('▶ %s'):format(basename(p)))
    elseif v.view then
      if v.view == 'subs' then forget_view('subs') end
      open_view({ name = v.view, args = v })
    elseif v.osub_help then
      rpc.call('library.subs.help', nil, function(err, h)
        local url = (not err and type(h) == 'table' and h.key_url) or 'https://www.opensubtitles.com/es/consumers'
        open_in_browser(url)
        osd(tr('Abierta la página de la clave. Cópiala y vuelve al Paso 2.'))
      end, 10)
    elseif v.paste then
      paste_secret(v.paste, v.paste == 'osub_api_key' and 'Api-Key' or 'Clave')
    elseif v.input then
      open_input(v.input, v.input == 'osub_languages' and (state.settings.osub_languages or '')
        or v.input == 'osub_username' and (state.settings.osub_username or '') or '')
    elseif v.folder then
      if ev.action == 'remove' then
        rpc.call('library.folders.remove', { path = v.folder }, function(err, res)
          if err then osd(tr('Quitar: %s'):format(fail(err, 'library.folders.remove'))) return end
          osd(tr('Carpeta quitada de la biblioteca (%d archivos; no se borra nada)'):format(res.removed or 0))
          refresh_home()
          reopen_current()
        end, 15)
      else
        rpc.call('library.scan', { path = v.folder, notify = SCRIPT }, function(err)
          if err then osd(tr('Escanear: %s'):format(fail(err, 'library.scan'))) return end
          scan_started({ path = v.folder })
        end, 15)
      end
    elseif v.add then
      add_folder(v.add)
    elseif v.rescan then
      rpc.call('library.scan', { notify = SCRIPT }, function(err)
        if err then osd(tr('Escanear: %s'):format(fail(err, 'library.scan'))) return end
        scan_started()
        reopen_current()
      end, 15)
    elseif v.toggle == 'auto_next' then
      opts.auto_next = not opts.auto_next
      P:set('auto_next', opts.auto_next)
      if not opts.auto_next then cancel_countdown(false) end
      osd(tr('Siguiente episodio automático: %s'):format(opts.auto_next and tr('activado') or tr('desactivado')))
      publish()
      reopen_current()
    elseif v.set then
      if v.set == 'tmdb_enabled' and v.to and not state.settings.has_tmdb_key then
        open_input('tmdb_key', '')
      elseif v.set == 'osub_enabled' and v.to and not state.settings.has_osub_key then
        open_input('osub_api_key', '')
      else
        set_setting(v.set, v.to, function() reopen_current() end)
      end
    elseif v.sub then
      uosc.close(MENU)
      download_subs(v.sub)
    end
  elseif ev.type == 'back' then
    table.remove(state.stack)
    if #state.stack == 0 then
      if not N:leave() then uosc.close(MENU) end
    else
      reopen_current()
    end
  end
end)

local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if t == MENU or t == INPUT then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    local open = uosc.open_type()
    if open == MENU or open == INPUT then return end
    -- H63/N3 · si acabamos de pedir nuestro menú y uosc aún no lo ha confirmado, no hay nada que olvidar
    if uosc.asking(MENU) then return end
    if #state.stack > 0 or state.view ~= '' or state.input then
      state.stack, state.view, state.input = {}, '', nil
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- events from mpvd: scan progress (jobs) and subtitles resynchronised later

local function refresh_open_view()
  if uosc.open_type() == MENU and (state.view == 'root' or state.view == 'folders' or state.view == 'movies'
      or state.view == 'shows') then
    reopen_current()
  end
end

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  if ev.event == 'job' and type(ev.job) == 'table' and ev.job.name == 'library.scan' then
    local j = ev.job
    state.scanning = j.status == 'queued' or j.status == 'running'
    state.scan_progress = tonumber(j.progress) or 0
    publish()
    if j.status == 'done' then
      local r = j.result or {}
      osd(string.format('📚 Biblioteca al día (%d nuevos, %d quitados)', r.added or 0, r.removed or 0))
      refresh_home()
      refresh_open_view()
      if state.path ~= '' then refresh_next(state.path) end
    elseif j.status == 'failed' then
      osd(tr('Biblioteca: %s'):format(j.error or tr('error')), 5)
      refresh_open_view()
    end
  elseif ev.event == 'library-subs' then
    if strip_file(ev.path or '') ~= current_path() then return end
    if ev.resync == 'done' and ev.srt then
      add_subtitle({ srt = ev.srt, language = state.last_lang or '', resync = 'done' }, true)
      -- drop the unsynchronised copy added before
      for _, t in ipairs(mp.get_property_native('track-list') or {}) do
        if t.type == 'sub' and t.external and t['external-filename'] == ev.original then
          mp.commandv('sub-remove', tostring(t.id))
        end
      end
      osd(tr('💬 Subtítulos resincronizados con la voz'))
    else
      osd(tr('💬 Sin resincronizar: %s'):format(ev.resync_reason or tr('error')), 4)
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- bindings

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

local function open_subs()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'subs' })
end

N:binding('library-menu', open_root)
N:binding('library-subs', open_subs)
mp.add_key_binding(nil, 'library-next', function()
  if state.next then play_next(state.next) else osd(tr('No hay siguiente episodio')) end
end)
mp.add_key_binding(nil, 'auto-next-toggle', function()
  opts.auto_next = not opts.auto_next
  P:set('auto_next', opts.auto_next)
  osd(tr('Siguiente episodio automático: %s'):format(opts.auto_next and tr('activado') or tr('desactivado')))
  publish()
end)
mp.register_script_message('mu-library-open', open_root)
-- H64 · la puerta única (y quien quiera) puede abrir el explorador directamente
mp.register_script_message('mu-library-explore', function()
  state.stack = {}
  open_view({ name = 'explore' })
end)
mp.register_script_message('mu-library-home', function() refresh_home() end)
mp.register_script_message('mu-library-cancel', function() cancel_countdown(true) end)
P:on_change(function(reason)
  if reason == 'reset' then opts.auto_next = P:get('auto_next') publish() end
end)

-- first home rows once mpvd is connected (and again on reconnection)
local was_connected = false
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  local now = type(core) == 'table' and core.mpvd == 'connected'
  if now and not was_connected then
    refresh_home()
    if state.path ~= '' then refresh_next(state.path) end
  end
  was_connected = now
end)

publish()
msg.info('mu-library loaded')
