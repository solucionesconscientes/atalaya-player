-- mu-menu: the "MPV-UOS" root menu, the global command palette, "continue watching" by content hash (mpvd watch.*)
-- and the start screen shown when mpv starts idle. Script name: mu_menu.
-- Bindings: root, palette, resume-toggle (see input.conf). Menu types: mu-menu (root/start/recents), mu-palette.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local brand = require('mu.brand')
local prefs = require('mu.prefs')
local nav = require('mu.nav')
local clip = require('mu.clip')

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-menu-event'
local MENU = 'mu-menu'
local PALETTE = 'mu-palette'

local opts = {
  start_screen = true,     -- open the start menu when mpv starts without a file
  start_delay = 0.6,       -- seconds after start (lets uosc initialise)
  resume = true,           -- continue watching by content hash
  resume_min = 20,         -- seconds: do not resume below this position
  save_interval = 15,      -- seconds between position updates while playing
  recents_in_root = 5,     -- "continue watching" entries shown inline in the root menu
  palette_limit = 8,       -- results per section in the palette
  osd_seconds = 3,
  click_pause = false,     -- a left click on the video toggles pause (preference, off by default)
  playlist_on_open = 4,    -- H47: abrir varios archivos a la vez enseña la lista; segundos que se queda (0 = no)
}
options.read_options(opts, 'mu-menu')
-- remembered "continue watching" switch (mu/prefs.lua; --script-opts=mu-menu-resume=… still wins)
local P = prefs.ns('mu-menu', { resume = opts.resume, click_pause = opts.click_pause })
P:apply_opts(opts, 'mu-menu', { 'resume', 'click_pause' })
local N = nav.new()

local state = {
  view = '', stack = {}, items = {},
  path = '', title = '', duration = 0, position = 0, kind = '', tracked = false, resumed = false,
  palette_query = '', palette_results = 0, last_error = '', start_shown = false, force_open = false,
}

local function publish()
  mp.set_property_native('user-data/mu/menu', {
    view = state.view, depth = #state.stack, items = state.items, path = state.path, tracked = state.tracked,
    resumed = state.resumed, position = state.position, palette_query = state.palette_query,
    palette_results = state.palette_results, last_error = state.last_error, start_shown = state.start_shown,
    resume = opts.resume, click_pause = opts.click_pause,
  })
end

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

local function fmt_time(s)
  s = math.floor(tonumber(s) or 0)
  if s >= 3600 then return string.format('%d:%02d:%02d', s / 3600, (s % 3600) / 60, s % 60) end
  return string.format('%d:%02d', s / 60, s % 60)
end

-- accent-insensitive lower-case text for palette matching (Latin-1 range covers Spanish)
local ACCENTS = {
  ['á'] = 'a', ['é'] = 'e', ['í'] = 'i', ['ó'] = 'o', ['ú'] = 'u', ['ü'] = 'u', ['ñ'] = 'n',
  ['Á'] = 'a', ['É'] = 'e', ['Í'] = 'i', ['Ó'] = 'o', ['Ú'] = 'u', ['Ü'] = 'u', ['Ñ'] = 'n',
}
local function fold(text)
  local out = tostring(text or ''):gsub('[%z\1-\127\194-\244][\128-\191]*', function(c) return ACCENTS[c] or c end)
  return out:lower()
end

-- ---------------------------------------------------------------------------------------------
-- continue watching

local function strip_file(p) return (tostring(p or ''):gsub('^file://', '')) end

-- mpv reports file:// URLs as plain paths; compare both sides without the scheme
local function is_live_channel(path)
  local iptv = mp.get_property_native('user-data/mu/iptv')
  if type(iptv) ~= 'table' or not iptv.current_url or iptv.current_url == '' then return false end
  return strip_file(iptv.current_url) == strip_file(path)
end

local function trackable(path)
  if not path or path == '' then return false end
  if path:match('^edl://') or path:match('^av://') or path:match('^lavf://') or path:match('^dvd') or path:match('^bd') then
    return false
  end
  if is_live_channel(path) then return false end
  return true
end

local function save_position(final, reason)
  if not state.tracked or state.path == '' then return end
  local pos = state.position
  local params = { path = state.path, title = state.title, duration = state.duration, position = pos }
  if final then
    if reason == 'eof' then params.finished = true end
  end
  rpc.call('watch.update', params, function(err) if err then fail(err, 'watch.update') end end, 10)
end

local save_timer = nil
local function start_timer()
  if save_timer then save_timer:kill() end
  save_timer = mp.add_periodic_timer(opts.save_interval, function() save_position(false) end)
end

mp.observe_property('time-pos', 'number', function(_, v)
  if v then state.position = v end
end)
mp.observe_property('duration', 'number', function(_, v)
  if v then state.duration = v end
end)
mp.observe_property('media-title', 'string', function(_, v)
  if v then state.title = v end
end)

local function register_play()
  rpc.call('watch.update', { path = state.path, title = state.title, duration = state.duration,
                             position = state.position, new_play = true },
           function(err) if err then fail(err, 'watch.update') end end, 10)
end

-- Ask for the saved position BEFORE recording this play (recording first would overwrite it with 0).
local function try_resume()
  local path = state.path
  if not opts.resume then register_play() return end
  rpc.call('watch.get', { path = path }, function(err, entry)
    if err then fail(err, 'watch.get') return end
    if (mp.get_property('path') or '') ~= path then return end
    local now = mp.get_property_number('time-pos') or 0
    local ok = entry and entry.resume and now <= 5 and entry.position >= opts.resume_min
      and not (state.duration > 0 and entry.position > state.duration - 5)
    if ok then
      mp.commandv('seek', tostring(entry.position), 'absolute', 'exact')
      state.position = entry.position
      state.resumed = true
      publish()
      osd('▶ Continuando en ' .. fmt_time(entry.position)
        .. (state.duration > 0 and (' / ' .. fmt_time(state.duration)) or ''))
    end
    register_play()
  end, 10)
end

-- H47 · abrir varios archivos de golpe (seleccionándolos en el gestor de archivos, o `mpv-uos a.mkv b.mkv c.mp3`)
-- ya construía la lista —eso lo hace mpv— pero no se veía: lo único que lo insinuaba eran los botones ⏮⏭ de la
-- barra, que uosc solo pinta cuando hay lista. Ahora se enseña al abrir el primero y se quita sola, para no dejar
-- un menú encima de la película: `playlist_on_open` son los segundos que se queda (0 la desactiva, un número
-- grande la deja hasta que la cierres).
local playlist_shown = false
local playlist_timer = nil

local function show_playlist_once()
  local secs = tonumber(opts.playlist_on_open) or 0
  if secs <= 0 or playlist_shown then return end
  if (mp.get_property_number('playlist-count') or 0) < 2 then return end
  if (mp.get_property_number('playlist-pos') or 0) ~= 0 then return end   -- solo al empezar la lista, no en cada pista
  if uosc.open_type() ~= nil then return end                              -- hay un menú puesto: no se lo pisamos
  playlist_shown = true
  mp.commandv('script-binding', 'uosc/playlist')
  if playlist_timer then playlist_timer:kill() end
  playlist_timer = mp.add_timeout(secs, function()
    playlist_timer = nil
    -- si sigue siendo la lista de uosc y nadie ha tocado nada, se cierra; si el espectador se ha movido a otro
    -- menú o la ha cerrado él, no se toca nada
    if uosc.open_type() == 'playlist' then mp.commandv('script-message-to', 'uosc', 'close-menu', 'playlist') end
  end)
end

mp.register_event('file-loaded', function()
  local path = mp.get_property('path') or ''
  state.path, state.resumed, state.position = path, false, mp.get_property_number('time-pos') or 0
  state.duration = mp.get_property_number('duration') or 0
  state.title = mp.get_property('media-title') or path
  state.tracked = trackable(path)
  publish()
  show_playlist_once()
  if not state.tracked then return end
  try_resume()
  start_timer()
end)

mp.register_event('end-file', function(ev)
  if save_timer then save_timer:kill(); save_timer = nil end
  if state.tracked then save_position(true, ev.reason) end
  state.tracked = false
  state.path = ''
  publish()
end)

mp.observe_property('pause', 'bool', function(_, paused)
  if paused ~= nil and state.tracked then save_position(false) end
end)
mp.register_event('seek', function() if state.tracked then save_position(false) end end)

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 300 then break end
    table.insert(out, { title = it.title or '', hint = it.hint or '', icon = it.icon or '', value = it.value or '',
                        submenu = it.items ~= nil and #it.items or 0 })
  end
  state.items = out
end

local function levels()
  local out = {}
  for _, spec in ipairs(state.stack) do out[#out + 1] = spec.title end
  return out
end

local function base_menu(title, items, extra)
  local top = state.stack[#state.stack]
  if top then top.title = title end
  local menu = { type = MENU, title = N:title(levels()), items = items, callback = { SCRIPT, EVENT }, on_close = 'callback',
                 keep_open = false, search_submenus = true }
  for k, v in pairs(extra or {}) do menu[k] = v end
  -- the top of the main menu (root, start screen) has nowhere to go back to
  if #state.stack > 1 then nav.decorate(menu) end
  return menu
end

-- Is ``view`` still the one on screen? Answers from mpvd arrive late and must not reopen a menu the viewer closed
-- (the state reset below runs 0.2 s after uosc says there is no menu, so the view name alone would still match).
local function still(view)
  return state.view == view
end

-- uosc dice que no hay ningún menú: hasta que se abra uno a propósito (open_view), una respuesta de mpvd que llegue
-- con retraso no debe volver a pintar lo que el espectador acaba de cerrar. Lo pone el observador del final.
local closing = false

local function show(title, items, extra)
  if closing then return end
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
  closing = false
  if push ~= false then table.insert(state.stack, spec) end
  state.view = spec.name
  publish()
  views[spec.name](spec.args or {})
end

local function reopen_current()
  local spec = state.stack[#state.stack]
  if spec then open_view(spec, false) end
end

local function recent_item(r)
  local hint
  if r.finished then hint = 'visto'
  elseif r.duration and r.duration > 0 then hint = fmt_time(r.position) .. ' / ' .. fmt_time(r.duration)
  elseif r.position and r.position > 0 then hint = fmt_time(r.position) end
  return {
    title = r.title ~= '' and r.title or r.path, hint = hint,
    icon = r.kind == 'url' and 'public' or 'movie', value = { open = r.path, position = r.position, resume = r.resume },
    muted = r.finished or nil, bold = r.resume or nil,
    actions = { { name = 'forget', icon = 'delete', label = 'Olvidar' } },
  }
end

local function with_recents(limit, unfinished, cb)
  if not rpc.connected() then cb(nil, {}) return end
  rpc.call('watch.recents', { limit = limit, unfinished_only = unfinished }, function(err, rows)
    if err then fail(err, 'watch.recents'); cb(err, {}) return end
    cb(nil, rows or {})
  end, 10)
end

-- item builders: a command, a key binding, another view of this menu, another script's menu one level down
local function cmd(title, hint, icon, command, extra)
  local it = { title = title, hint = hint, icon = icon, value = { cmd = command } }
  for k, v in pairs(extra or {}) do it[k] = v end
  return it
end
local function bind(title, hint, icon, name, extra) return cmd(title, hint, icon, { 'script-binding', name }, extra) end
local function sub(title, hint, icon, view, extra)
  local it = { title = title, hint = hint, icon = icon, value = { view = view } }
  for k, v in pairs(extra or {}) do it[k] = v end
  return it
end
local function child(title, hint, icon, script, entry, extra)
  local it = { title = title, hint = hint, icon = icon, value = { child = { script = script, entry = entry } } }
  for k, v in pairs(extra or {}) do it[k] = v end
  return it
end
local function toggle(title, on, icon, pref, extra)
  local it = { title = title, hint = on and 'sí' or 'no', icon = icon, active = on, value = { pref = pref } }
  for k, v in pairs(extra or {}) do it[k] = v end
  return it
end

-- The main menu: eight categories (H15). TV y radio and Descargas open their module directly; the others are views
-- here that lead into the modules one level down (their "Atrás" comes back to the category).
local CATEGORIES = {
  { title = 'Abrir o descargar', icon = 'folder_open', hint = 'enlace, archivo, lista, biblioteca', view = 'open' },
  { title = 'TV y radio', icon = 'live_tv', hint = 'alt+t', child = { 'mu_iptv', 'tv-menu' } },
  { title = 'Descargas y conversión', icon = 'download', hint = 'alt+y', child = { 'mu_ytdl', 'ytdl-menu' } },
  { title = 'Subtítulos', icon = 'subtitles', view = 'subs' },
  { title = 'Imagen y sonido', icon = 'tune', view = 'av' },
  { title = 'Grabar', icon = 'fiber_manual_record', hint = 'capturas, directos, tramos', child = { 'mu_record', 'record-menu' } },
  { title = 'Herramientas', icon = 'handyman', hint = 'intro, estudio, mando…', view = 'tools' },
  { title = 'Preferencias', icon = 'settings', view = 'prefs' },
}

-- Modo sencillo (mu-modes, H27): only what a first-time user needs, plus a way back to the full menu.
local SIMPLE = { ['Abrir o descargar'] = true, ['TV y radio'] = true, ['Subtítulos'] = true,
                 ['Preferencias'] = true }
local modes = {}

-- C8 · una línea que diga siempre qué se está haciendo por detrás. El texto lo compone mpvd (pending.status) y lo
-- reparte mu-core en `user-data/mu/core`; aquí solo se pinta, y lleva al panel de subtítulos, que es donde se para.
local work_text = ''

local function work_row()
  if work_text == '' then return nil end
  return { title = 'Trabajando por detrás', hint = work_text, icon = 'hourglass_top',
           value = { child = { script = 'mu_subs', entry = 'subs-menu' } } }
end

-- H45/D3 · «Resumen e índice» se encuentra desde la raíz, pero como fila de lo que se está viendo, no como una
-- novena categoría: lo que solo tiene sentido para este archivo no ocupa un sitio fijo (criterio de §B.3).
local function recap_row()
  if mp.get_property_native('idle-active') then return nil end
  if (mp.get_property('path') or '') == '' then return nil end
  return { title = 'Resumen e índice', hint = 'qué me he perdido · secciones con su minuto', icon = 'history_edu',
           value = { child = { script = 'mu_recap', entry = 'recap-menu' } } }
end

local function root_items()
  local items = {}
  if modes.simple then
    for _, c in ipairs(CATEGORIES) do
      if SIMPLE[c.title] then
        if c.view then table.insert(items, sub(c.title, c.hint, c.icon, c.view))
        else table.insert(items, child(c.title, c.hint, c.icon, c.child[1], c.child[2])) end
      end
    end
    local w = work_row()
    if w then table.insert(items, w) end
    items[#items].separator = true
    table.insert(items, cmd('Menú completo', 'quita el modo sencillo', 'unfold_more',
                            { 'script-message-to', 'mu_modes', 'mu-modes-set', 'simple', 'no' }))
    table.insert(items, bind('Salir', 'q', 'logout', 'mu_core/quit-ask'))
    return items
  end
  for _, c in ipairs(CATEGORIES) do
    if c.view then table.insert(items, sub(c.title, c.hint, c.icon, c.view))
    else table.insert(items, child(c.title, c.hint, c.icon, c.child[1], c.child[2])) end
  end
  local r = recap_row()
  if r then
    r.separator = true
    table.insert(items, r)
  end
  local w = work_row()
  if w then table.insert(items, w) end
  items[#items].separator = true
  table.insert(items, { title = 'Buscar comandos, canales y recientes…', hint = 'alt+p', icon = 'search',
                        value = { view = 'palette' } })
  table.insert(items, bind('Ayuda: teclas principales', '?', 'help_outline', SCRIPT .. '/help'))
  table.insert(items, bind('Salir', 'q', 'logout', 'mu_core/quit-ask'))
  return items
end

views.root = function()
  local items = root_items()
  show(brand.name, items, { footnote = 'Enter abre · ⌫ o ← atrás · Esc cierra · ? ayuda' })
  with_recents(opts.recents_in_root, true, function(_, rows)
    if not still('root') or #rows == 0 then return end
    local list = {}
    for _, r in ipairs(rows) do table.insert(list, recent_item(r)) end
    table.insert(list, { title = 'Todos los recientes…', icon = 'history', value = { view = 'recents' } })
    table.insert(items, 1, { title = 'Continuar viendo', hint = tostring(#rows), icon = 'history', items = list,
                             separator = true })
    show(brand.name, items, { footnote = 'Enter abre · ⌫ o ← atrás · Esc cierra · ? ayuda' })
  end)
end

-- H42/A1-A3 · una sola puerta para lo que se pega o se escribe. Las cinco de antes (abrir archivo, abrir URL,
-- pegar, buscar en YouTube, suscripciones) ya no son filas de este menú: viven dentro de la caja y en el teclado
-- (`o`, `ctrl+u`, `ctrl+f`, `ctrl+v`). Lo que queda aquí no son puertas, son sitios donde mirar.
views.open = function()
  show('Abrir o descargar', {
    child('Abrir o descargar…', 'un enlace, varios, una lista, un archivo', 'add_link', 'mu_ytdl', 'ytdl-gate',
          { separator = true }),
    child('Biblioteca', 'ctrl+b', 'video_library', 'mu_library', 'library-menu'),
    child('Música', 'alt+M', 'library_music', 'mu_music', 'music-menu'),
    child('Suscripciones', 'canales, listas y podcasts', 'subscriptions', 'mu_feeds', 'feeds-menu'),
    sub('Recientes', 'alt+h', 'history', 'recents', { separator = true }),
    bind('Lista de reproducción', 'p', 'playlist_play', 'uosc/playlist'),
  })
end

views.subs = function()
  show('Subtítulos', {
    bind('Elegir pista de subtítulos', 's', 'subtitles', 'uosc/subtitles'),
    bind('Cargar un archivo de subtítulos', 'alt+s', 'upload_file', 'uosc/load-subtitles'),
    cmd('Mostrar u ocultar', 'v', 'visibility', { 'cycle', 'sub-visibility' }, { separator = true }),
    child('Panel de subtítulos', 'alt+i', 'closed_caption', 'mu_subs', 'subs-menu'),
    child('Resumen e índice del vídeo', 'alt+R · alt+I', 'history_edu', 'mu_recap', 'recap-menu'),
    bind('Guardar subtítulos (SRT)', 'alt+S', 'save', 'mu_subs/subs-save'),
    child('Buscar subtítulos en internet', 'OpenSubtitles', 'travel_explore', 'mu_library', 'library-subs'),
  })
end

views.av = function()
  show('Imagen y sonido', {
    bind('Pista de audio', 'a', 'graphic_eq', 'uosc/audio'),
    bind('Salida de sonido', nil, 'speaker', 'uosc/audio-device'),
    cmd('Silenciar', 'm', 'volume_off', { 'cycle', 'mute' }, { separator = true }),
    bind('Pista de vídeo', nil, 'movie', 'uosc/video'),
    bind('Calidad del directo o del vídeo', 'ctrl+q', 'high_quality', 'uosc/stream-quality'),
    cmd('Relación de aspecto', 'A', 'aspect_ratio', { 'cycle-values', 'video-aspect-override', '16:9', '4:3', '2.35:1', '-1' }),
    cmd('Quitar rayas (desentrelazar)', 'd', 'blur_linear', { 'cycle', 'deinterlace' }, { separator = true }),
    child('Filtros: diálogo claro, modo noche, ruido…', 'alt+v', 'tune', 'mu_av', 'av-menu'),
    bind('Modo noche', 'alt+n', 'bedtime', 'mu_av/av-night'),
    child('Audiolibros y podcasts', 'alt+A', 'menu_book', 'mu_books', 'books-menu', { separator = true }),
    child('Letra de la canción', 'alt+K', 'lyrics', 'mu_lyrics', 'lyrics-menu'),
  })
end

views.tools = function()
  show('Herramientas', {
    sub('Buscar comandos, canales y recientes…', 'alt+p', 'search', 'palette'),
    child('Saltar intro y créditos', 'alt+j', 'skip_next', 'mu_intro', 'intro-menu'),
    child('Estudio: repetir, velocidad, notas', 'alt+e', 'school', 'mu_study', 'study-menu'),
    child('Mis notas', 'alt+B', 'sticky_note_2', 'mu_notes', 'notes-menu'),
    child('Mando desde el móvil', 'alt+Z', 'qr_code_2', 'mu_remote', 'remote-menu'),
    cmd('Panel de descargas en el navegador', nil, 'open_in_browser',
        { 'script-message-to', 'mu_remote', 'mu-remote-downloads' }),
    child('Compartir: ver juntos', 'alt+W', 'group', 'mu_share', 'share-menu'),
    child('Enviar a la tele', 'alt+E', 'cast', 'mu_cast', 'cast-menu', { separator = true }),
    bind('Capítulos', 'c', 'bookmark', 'uosc/chapters'),
    cmd('Repetir este archivo', 'L', 'repeat_one', { 'cycle-values', 'loop-file', 'inf', 'no' }),
    cmd('Repetir la lista', nil, 'repeat_on', { 'cycle-values', 'loop-playlist', 'inf', 'no' }),
    bind('Orden aleatorio de la lista', nil, 'shuffle', 'uosc/shuffle'),
    bind('Mostrar en la carpeta', 'alt+o', 'folder', 'uosc/show-in-directory', { separator = true }),
    { title = 'Estado del reproductor', icon = 'monitor_heart', value = { action = 'status' } },
    bind('Todas las teclas', nil, 'keyboard', 'uosc/keybinds'),
  })
end

views.prefs = function()
  show('Preferencias', {
    toggle('Continuar viendo donde lo dejé', opts.resume, 'history', 'resume'),
    toggle('Pausar con un clic en el vídeo', opts.click_pause, 'touch_app', 'click_pause', { separator = true }),
    cmd('Mini reproductor', modes.mini and 'sí' or 'alt+F', 'picture_in_picture_alt',
        { 'script-binding', 'mu_modes/mini-toggle' }, { active = modes.mini }),
    cmd('Modo salón: letra grande y mando', modes.salon and 'sí' or 'no', 'weekend',
        { 'script-binding', 'mu_modes/salon-toggle' }, { active = modes.salon }),
    cmd('Modo sencillo: menú corto', modes.simple and 'sí' or 'no', 'filter_list',
        { 'script-binding', 'mu_modes/simple-toggle' }, { active = modes.simple, separator = true }),
    { title = 'Restablecer preferencias…', hint = 'se guarda una copia', icon = 'restart_alt',
      value = { cmd = { 'script-message-to', 'mu_prefs', 'reset-ask' } } },
    bind('Abrir la carpeta de configuración', 'ctrl+alt+o', 'folder_open', 'uosc/open-config-directory'),
    { title = 'Ayuda y novedades en ' .. brand.site:gsub('^https?://', ''), icon = 'language',
      value = { site = true }, actions = { { name = 'copy', icon = 'content_copy', label = 'Copiar la dirección' } } },
  })
end

-- On-screen help (`?`): the keys that matter, in plain words; Enter on the last row lists every key.
local HELP = {
  { 'Espacio', 'reproducir / pausa' },
  { '← →', 'atrás / adelante 5 segundos' },
  { '↑ ↓', 'atrás / adelante 1 minuto' },
  { '9 0 · rueda', 'volumen' },
  { 'f · doble clic', 'pantalla completa' },
  { 'm', 'silenciar' },
  { 's · a', 'subtítulos · audio' },
  { 'alt+m · clic derecho', 'menú' },
  { 'alt+p', 'buscar comandos, canales y recientes' },
  { 'ctrl+u · ctrl+f', 'abrir URL · buscar en YouTube' },
  { 'ctrl+b', 'biblioteca: películas y series' },
  { 'alt+t', 'TV y radio' },
  { 'alt+R', '¿qué me he perdido?' },
  { '⌫ · ←', 'volver atrás en un menú' },
  { 'Esc', 'cerrar el menú' },
  { 'q', 'salir' },
}

views.help = function()
  local items = {}
  for _, h in ipairs(HELP) do
    table.insert(items, { title = h[2], hint = h[1], selectable = false })
  end
  items[#items].separator = true
  table.insert(items, bind('Ver todas las teclas', nil, 'keyboard', 'uosc/keybinds'))
  -- H41 · la dirección del proyecto, donde está toda la información. Vive en brand.json como el resto de la
  -- identidad, y desde aquí se abre en el navegador o se copia (Tab) para llevársela a otro aparato.
  table.insert(items, { title = brand.name .. ' en internet', hint = brand.site:gsub('^https?://', ''),
                        icon = 'language', value = { site = true },
                        actions = { { name = 'copy', icon = 'content_copy', label = 'Copiar la dirección' } } })
  show('Ayuda', items, { footnote = 'Esc cierra · Tab copia la dirección' })
end

views.recents = function()
  show('Recientes', uosc.loading_items())
  with_recents(50, false, function(err, rows)
    if not still('recents') then return end   -- closed with Esc while mpvd answered: do not reopen the menu
    if err then show('Recientes', uosc.message_items(fail(err, 'watch.recents'), 'error')) return end
    local items = {}
    for _, r in ipairs(rows) do table.insert(items, recent_item(r)) end
    if #items == 0 then items = uosc.message_items('Todavía no has visto nada con ' .. brand.name, 'history')
    else table.insert(items, { title = 'Borrar historial', icon = 'delete_sweep', value = { clear = true }, separator = true,
                               actions = {} }) end
    show('Recientes', items, { footnote = 'Enter continúa · Tab olvida · ⌫ atrás' })
  end)
end

-- «seguir viendo» / «siguiente episodio» of the library, published ready-made by mu-library (H22)
local function library_rows()
  local rows = mp.get_property_native('user-data/mu/library/home')
  return type(rows) == 'table' and rows or {}
end

views.start = function(args)
  local items = {
    child('Abrir o descargar…', 'un enlace, varios, una lista, un archivo', 'add_link', 'mu_ytdl', 'ytdl-gate'),
    child('Biblioteca', 'ctrl+b', 'video_library', 'mu_library', 'library-menu'),
    child('TV y radio', 'alt+t', 'live_tv', 'mu_iptv', 'tv-menu'),
    sub('Buscar comandos, canales y recientes…', 'alt+p', 'search', 'palette'),
    sub('Menú principal', 'alt+m', 'apps', 'root', { separator = true }),
  }
  show(brand.name .. ' · Inicio', items)
  -- fresh rows from mu-library arrive through the observer below (not when this redraw comes from it)
  if not (args and args.from_library) then mp.commandv('script-message-to', 'mu_library', 'mu-library-home') end
  with_recents(12, false, function(_, rows)
    if not still('start') then return end
    local top, seen = {}, {}
    for _, it in ipairs(library_rows()) do
      if type(it.value) == 'table' and it.value.open then
        seen[strip_file(it.value.open)] = true
        table.insert(top, it)
      end
    end
    for _, r in ipairs(rows) do
      if not seen[strip_file(r.path)] then table.insert(top, recent_item(r)) end
    end
    if #top == 0 then return end
    local all = { { title = 'Continuar viendo', icon = 'history', selectable = false, muted = true, align = 'center' } }
    for _, it in ipairs(top) do table.insert(all, it) end
    for i, it in ipairs(items) do
      if i == 1 then it.separator = true end
      table.insert(all, it)
    end
    show(brand.name .. ' · Inicio', all)
  end)
end

-- mu-modes: the main menu and Preferencias follow the modes while they are open
mp.observe_property('user-data/mu/modes', 'native', function(_, m)
  m = type(m) == 'table' and m or {}
  local changed = (m.simple or false) ~= (modes.simple or false) or (m.salon or false) ~= (modes.salon or false)
    or (m.mini or false) ~= (modes.mini or false)
  modes = m
  if changed and (state.view == 'root' or state.view == 'prefs') and uosc.open_type() == MENU then
    open_view({ name = state.view }, false)
  end
end)

-- the start screen follows the library rows (they change when a scan ends or an episode is finished)
local library_home_json = ''
mp.observe_property('user-data/mu/library/home', 'native', function(_, rows)
  local json = utils.format_json(rows or {}) or ''
  if json == library_home_json then return end
  library_home_json = json
  if state.view == 'start' and #state.stack == 1 and uosc.open_type() == MENU then
    open_view({ name = 'start', args = { from_library = true } }, false)
  end
end)

-- ---------------------------------------------------------------------------------------------
-- command palette

local CURATED = {
  { title = 'Pausa / reproducir', cmd = 'cycle pause', key = 'espacio', kw = 'play pause pausar reproducir parar' },
  { title = 'Pantalla completa', cmd = 'cycle fullscreen', key = 'f', kw = 'fullscreen maximizar' },
  { title = 'Silenciar', cmd = 'cycle mute', key = 'm', kw = 'mute sonido silencio' },
  { title = 'Subir volumen', cmd = 'add volume 2', key = '0', kw = 'volume up mas alto' },
  { title = 'Bajar volumen', cmd = 'add volume -2', key = '9', kw = 'volume down mas bajo' },
  { title = 'Velocidad +10 %', cmd = 'multiply speed 1.1', key = ']', kw = 'speed rapido acelerar' },
  { title = 'Velocidad −10 %', cmd = 'multiply speed 1/1.1', key = '[', kw = 'speed lento frenar' },
  { title = 'Velocidad normal', cmd = 'set speed 1.0', key = 'BS' },
  { title = 'Capítulo siguiente', cmd = 'add chapter 1', key = '!' },
  { title = 'Capítulo anterior', cmd = 'add chapter -1', key = '@' },
  { title = 'Siguiente de la lista', cmd = 'playlist-next', key = '>', kw = 'next siguiente' },
  { title = 'Anterior de la lista', cmd = 'playlist-prev', key = '<', kw = 'previous anterior' },
  { title = 'Bucle A-B', cmd = 'ab-loop', key = 'l' },
  { title = 'Repetir archivo', cmd = 'cycle-values loop-file inf no', key = 'L' },
  { title = 'Subtítulos: mostrar / ocultar', cmd = 'cycle sub-visibility', key = 'v', kw = 'subtitles subs' },
  { title = 'Subtítulos: retrasar +100 ms', cmd = 'add sub-delay 0.1', key = 'x' },
  { title = 'Subtítulos: adelantar 100 ms', cmd = 'add sub-delay -0.1', key = 'z' },
  { title = 'Audio: retrasar +100 ms', cmd = 'add audio-delay 0.1', key = 'ctrl++' },
  { title = 'Audio: adelantar 100 ms', cmd = 'add audio-delay -0.1', key = 'ctrl+-' },
  { title = 'Estadísticas', cmd = 'script-binding stats/display-stats-toggle', key = 'I' },
  { title = 'Consola de mpv', cmd = 'script-binding console/enable', key = '`' },
  { title = 'Mostrar progreso', cmd = 'show-progress', key = 'o' },
  { title = 'Rotar vídeo 90°', cmd = 'cycle-values video-rotate 90 180 270 0' },
  { title = 'Desentrelazar', cmd = 'cycle deinterlace', key = 'd' },
  { title = 'Guardar posición y salir', cmd = 'quit-watch-later', key = 'Q' },
  { title = 'Restablecer preferencias', cmd = 'script-message-to mu_prefs reset-ask' },
  -- Los títulos de las teclas. Hasta H46 los daba el comentario `#!` de input.conf, que de paso construía
  -- un segundo menú; ahora viven aquí, en un solo sitio y en español. La tecla no se escribe: se lee.
  { title = 'Abrir URL o ruta copiada (portapapeles)', cmd = 'loadfile "${clipboard/text}" replace' },
  { title = 'Abrir URL…', cmd = 'script-binding mu_ytdl/open-url' },
  { title = 'Abrir archivo', cmd = 'script-binding uosc/open-file' },
  { title = 'Abrir carpeta de configuración', cmd = 'script-binding uosc/open-config-directory' },
  { title = 'Abrir o descargar (enlace, archivo, lista, carpeta)', cmd = 'script-binding mu_ytdl/ytdl-gate' },
  { title = 'Audio › Pistas de audio', cmd = 'script-binding uosc/audio' },
  { title = 'Audiolibros › Adelante 30 s', cmd = 'script-binding mu_books/forward-30' },
  { title = 'Audiolibros › Atrás 30 s', cmd = 'script-binding mu_books/back-30' },
  { title = 'Audiolibros y podcasts (capítulos, marcadores, temporizador)',
    cmd = 'script-binding mu_books/books-menu' },
  { title = 'Biblioteca (películas, series, seguir viendo, carpetas)', cmd = 'script-binding mu_library/library-menu' },
  { title = 'Buscar en YouTube', cmd = 'script-binding mu_ytdl/yt-search' },
  { title = 'Captura de pantalla', cmd = 'async screenshot' },
  { title = 'Capítulos', cmd = 'script-binding uosc/chapters' },
  { title = 'Compartir › Mostrar / ocultar el QR de la sala', cmd = 'script-binding mu_share/share-qr' },
  { title = 'Compartir › Ver juntos (sala, invitados, cerrar)', cmd = 'script-binding mu_share/share-menu' },
  { title = 'Convertir › Convertir vídeo o audio (archivo o carpeta)', cmd = 'script-binding mu_convert/convert-menu' },
  { title = 'Convertir › Tareas (descargas y conversiones)', cmd = 'script-binding mu_convert/tasks-menu' },
  { title = 'Ediciones', cmd = 'script-binding uosc/editions' },
  { title = 'Enviar a la tele (DLNA: elegir tele, pausar, seguir aquí)', cmd = 'script-binding mu_cast/cast-menu' },
  { title = 'Estudio › Exportar clip del bucle A-B / línea', cmd = 'script-binding mu_study/clip' },
  { title = 'Estudio › Menú (repetir, velocidad inteligente, notas, clips)', cmd = 'script-binding mu_study/study-menu' },
  { title = 'Estudio › Nota con enlace de tiempo', cmd = 'script-binding mu_study/note' },
  { title = 'Estudio › Repetir la línea anterior', cmd = 'script-binding mu_study/repeat-prev' },
  { title = 'Estudio › Repetir la línea de subtítulo actual', cmd = 'script-binding mu_study/repeat-line' },
  { title = 'Estudio › Repetir la línea siguiente', cmd = 'script-binding mu_study/repeat-next' },
  { title = 'Estudio › Velocidad inteligente (acelera silencios)', cmd = 'script-binding mu_study/smart-speed' },
  { title = 'Grabar › Grabar desde ahora / detener (directo, vídeo o archivo)', cmd = 'script-binding mu_record/record-toggle' },
  { title = 'Letra de la canción (y ¿qué canción es?)', cmd = 'script-binding mu_lyrics/lyrics-menu' },
  { title = 'Lista de reproducción', cmd = 'script-binding uosc/playlist' },
  { title = 'MPV-UOS › Ayuda (teclas principales)', cmd = 'script-binding mu_menu/help' },
  { title = 'MPV-UOS › Buscar comandos, canales y recientes', cmd = 'script-binding mu_menu/palette' },
  { title = 'MPV-UOS › Menú principal', cmd = 'script-binding mu_menu/root' },
  { title = 'MPV-UOS › Recientes / continuar viendo', cmd = 'script-binding mu_menu/recents' },
  { title = 'Mando a distancia › Menú (estado, móviles, olvidar)', cmd = 'script-binding mu_remote/remote-menu' },
  { title = 'Mando a distancia › Mostrar / ocultar QR para el móvil', cmd = 'script-binding mu_remote/remote-qr' },
  { title = 'Mis notas (saltar al minuto, editar, borrar, exportar)', cmd = 'script-binding mu_notes/notes-menu' },
  { title = 'Modos › Mini reproductor (ventana pequeña encima)', cmd = 'script-binding mu_modes/mini-toggle' },
  { title = 'Mostrar en la carpeta', cmd = 'script-binding uosc/show-in-directory' },
  { title = 'Música (artistas, álbumes, géneros, listas, cola)', cmd = 'script-binding mu_music/music-menu' },
  { title = 'Repetir › Repetir este archivo', cmd = 'cycle-values loop-file "inf" "no"' },
  { title = 'Resumen e índice › ¿Qué me he perdido? (lo que se dijo)', cmd = 'script-binding mu_recap/recap' },
  { title = 'Resumen e índice › Índice del vídeo (secciones, cada una a su minuto)', cmd = 'script-binding mu_recap/outline' },
  { title = 'Salir', cmd = 'script-binding mu_core/quit-ask' },
  { title = 'Saltar intro / créditos', cmd = 'script-binding mu_intro/skip' },
  { title = 'Saltar intro / créditos › Menú (segmentos, automático)', cmd = 'script-binding mu_intro/intro-menu' },
  { title = 'Sonido e imagen › Menú (filtros, diagnóstico)', cmd = 'script-binding mu_av/av-menu' },
  { title = 'Sonido e imagen › Modo noche', cmd = 'script-binding mu_av/av-night' },
  { title = 'Subtítulos › Cargar archivo de subtítulos', cmd = 'script-binding uosc/load-subtitles' },
  { title = 'Subtítulos › Crear con IA: iniciar / detener', cmd = 'script-binding mu_subs/subs-toggle' },
  { title = 'Subtítulos › Cuadrar la pista con la voz', cmd = 'script-binding mu_subs/subs-resync' },
  { title = 'Subtítulos › Guardar subtítulos (SRT)', cmd = 'script-binding mu_subs/subs-save' },
  { title = 'Subtítulos › Mostrar / ocultar los secundarios', cmd = 'cycle secondary-sub-visibility' },
  { title = 'Subtítulos › Panel (pistas, internet, crear con IA)', cmd = 'script-binding mu_subs/subs-menu' },
  { title = 'Subtítulos › Pistas de subtítulos', cmd = 'script-binding uosc/subtitles' },
  { title = 'Suscripciones › Canales, listas y podcasts (añadir, reglas)',
    cmd = 'script-binding mu_feeds/feeds-menu' },
  { title = 'TV y radio › Buscar canal o emisora', cmd = 'script-binding mu_iptv/tv-search' },
  { title = 'TV y radio › Canal anterior', cmd = 'script-binding mu_iptv/zap-prev' },
  { title = 'TV y radio › Canal siguiente', cmd = 'script-binding mu_iptv/zap-next' },
  { title = 'TV y radio › Guía del canal que estás viendo', cmd = 'script-binding mu_iptv/tv-guide' },
  { title = 'TV y radio › Menú de canales', cmd = 'script-binding mu_iptv/tv-menu' },
  { title = 'Ver › Calidad del stream', cmd = 'script-binding uosc/stream-quality' },
  { title = 'Ver › Relación de aspecto', cmd = 'cycle-values video-aspect-override "16:9" "4:3" "2.35:1" "-1"' },
  { title = 'Vídeos de internet › Calidad', cmd = 'script-binding mu_ytdl/ytdl-quality' },
  { title = 'Vídeos de internet › Descargar', cmd = 'script-binding mu_ytdl/ytdl-download' },
  { title = 'Vídeos de internet › Descargas', cmd = 'script-binding mu_ytdl/ytdl-downloads' },
  { title = 'Vídeos de internet › Menú (calidad, descargas)', cmd = 'script-binding mu_ytdl/ytdl-menu' },
  { title = 'Vídeos de internet › Solo audio / vídeo', cmd = 'script-binding mu_ytdl/ytdl-toggle-audio' },
}

local ACTIONS = {
  { title = 'Actualizar listas de TV y radio', icon = 'refresh', action = 'iptv.refresh' },
  { title = 'Buscar actualización de yt-dlp', icon = 'system_update_alt', action = 'ytdl.update.check' },
  { title = 'Ver descargas', icon = 'downloading', cmd = { 'script-binding', 'mu_ytdl/ytdl-downloads' } },
  { title = 'Panel de descargas en el navegador', icon = 'open_in_browser',
    cmd = { 'script-message-to', 'mu_remote', 'mu-remote-downloads' } },
  { title = 'Suscripciones', icon = 'subscriptions', cmd = { 'script-binding', 'mu_feeds/feeds-menu' } },
  { title = 'Estado de mpvd', icon = 'monitor_heart', action = 'status' },
  { title = 'Relanzar mpvd', icon = 'restart_alt', cmd = { 'script-message-to', 'mu_core', 'mu-ensure' } },

}

local commands_cache = nil

-- H46/E1 · la tecla de cada comando se lee del reproductor, no se escribe en la tabla: así no se queda vieja
-- cuando se cambia input.conf. Antes el TÍTULO también salía de ahí (del comentario `#!`), y ese comentario
-- montaba de paso un segundo menú; al retirarlo los títulos pasan a CURATED, que es donde se leen y se cuidan.
local function keys_by_cmd()
  local out = {}
  for _, b in ipairs(mp.get_property_native('input-bindings') or {}) do
    if b.cmd and b.cmd ~= 'ignore' and not b.is_weak and b.key and not out[b.cmd] then out[b.cmd] = b.key end
  end
  return out
end

local function commands()
  if commands_cache then return commands_cache end
  local list, seen = {}, {}
  local keys = keys_by_cmd()
  for _, c in ipairs(CURATED) do
    if not seen[c.cmd] then
      seen[c.cmd] = true
      table.insert(list, { title = c.title, cmd = c.cmd, key = keys[c.cmd] or c.key or '', kw = c.kw })
    elseif c.kw then
      for _, l in ipairs(list) do if l.cmd == c.cmd then l.kw = c.kw end end
    end
  end
  for _, c in ipairs(list) do
    c.fold = fold(c.title .. ' ' .. (c.kw or ''))  -- what the user reads (and says) matters
    c.fold_cmd = fold(c.cmd)                        -- the raw command only as a weak fallback
  end
  commands_cache = list
  return list
end
mp.observe_property('input-bindings', 'native', function() commands_cache = nil end)

-- Word-aware matching: every query word must appear; start of text > start of a word > inside a word. Words of one
-- or two characters ("la", "1") only count at the start of a word, so "la 1" finds the channel La 1 and not
-- "adelantar 100 ms".
local function score(folded, words)
  if not folded then return nil end
  local total = 0
  for _, w in ipairs(words) do
    local best, init = nil, 1
    while true do
      local pos = folded:find(w, init, true)
      if not pos then break end
      local prev = pos > 1 and folded:sub(pos - 1, pos - 1) or ' '
      local at_word = pos == 1 or prev:match('[%s%p]') ~= nil
      local val = pos == 1 and 3 or (at_word and 2 or (#w > 2 and 1 or nil))
      if val and (not best or val > best) then best = val end
      init = pos + 1
    end
    if not best then return nil end
    total = total + best
  end
  return total
end

local function palette_menu(items, query)
  return {
    type = PALETTE, title = 'Escribe un comando, canal, vídeo reciente o algo del diálogo', items = items,
    callback = { SCRIPT, EVENT },
    search_style = 'palette', search_debounce = 150, on_search = 'callback', on_close = 'callback',
    search_suggestion = query, footnote = 'Enter ejecuta / reproduce · ⌫ cierra',
  }
end

local function section(title, items, out)
  if #items == 0 then return end
  table.insert(out, { title = title, selectable = false, muted = true, align = 'center', separator = #out > 0 })
  for _, it in ipairs(items) do table.insert(out, it) end
end

local function command_items(query, limit)
  local words = {}
  for w in fold(query):gmatch('%S+') do table.insert(words, w) end
  local scored = {}
  for _, c in ipairs(commands()) do
    local s = #words == 0 and 1 or score(c.fold, words)
    if not s and #words > 0 then
      local weak = score(c.fold_cmd, words)
      s = weak and weak / 4 or nil
    end
    if s then table.insert(scored, { s = s, c = c }) end
  end
  table.sort(scored, function(a, b) if a.s ~= b.s then return a.s > b.s end return a.c.title < b.c.title end)
  local items = {}
  for i, e in ipairs(scored) do
    if i > limit then break end
    table.insert(items, { title = e.c.title, hint = e.c.key ~= '' and e.c.key or nil, icon = 'keyboard_command_key',
                          value = { cmd = e.c.cmd } })
  end
  return items
end

local function action_items(query, limit)
  local words = {}
  for w in fold(query):gmatch('%S+') do table.insert(words, w) end
  local items = {}
  for _, a in ipairs(ACTIONS) do
    if #words == 0 or score(fold(a.title), words) then
      table.insert(items, { title = a.title, icon = a.icon, value = { action = a.action, cmd = a.cmd }, hint = 'mpvd' })
      if #items >= limit then break end
    end
  end
  return items
end

local palette_seq = 0
local dialogue_mode = 'text'
local function run_palette(query)
  palette_seq = palette_seq + 1
  local seq = palette_seq
  state.palette_query = query
  local limit = opts.palette_limit
  local pending
  local channels, recents, dialogue = {}, {}, {}
  local function finish()
    if seq ~= palette_seq or not still('palette') then return end
    local out = {}
    local dl = {}
    for _, hit in ipairs(dialogue) do
      table.insert(dl, { title = hit.text, hint = fmt_time(hit.start), icon = 'forum', value = { seek = hit.start } })
    end
    section(dialogue_mode == 'semantic' and 'Diálogo (semántico)' or 'Diálogo', dl, out)
    local ch = {}
    local fq = fold(query)
    local channel_first = false
    for _, c in ipairs(channels) do
      table.insert(ch, { title = c.name, hint = c.group or c.source, icon = c.kind == 'radio' and 'radio' or 'live_tv',
                         value = { channel = c.id } })
      if fq ~= '' and fold(c.name):sub(1, #fq) == fq then channel_first = true end
    end
    -- "la 1" is a channel search: its name starts with the query, so channels go before commands
    if channel_first then section('Canales', ch, out) end
    section('Comandos', command_items(query, query == '' and 6 or limit), out)
    if not channel_first then section('Canales', ch, out) end
    local rc = {}
    for _, r in ipairs(recents) do table.insert(rc, recent_item(r)) end
    section('Recientes', rc, out)
    if query ~= '' then
      section('YouTube', { { title = 'Buscar «' .. query .. '» en YouTube', icon = 'travel_explore',
        value = { cmd = { 'script-message-to', 'mu_ytdl', 'mu-ytdl-search', query } } } }, out)
    end
    section('mpvd', action_items(query, query == '' and 2 or limit), out)
    if #out == 0 then out = uosc.message_items('Sin resultados para «' .. query .. '»', 'search_off') end
    state.palette_results = #out
    remember(out)
    publish()
    uosc.update(palette_menu(out, query))
  end
  if rpc.connected() then
    pending = 2
    finish()  -- local commands right away; channels, recents and dialogue are added when mpvd answers
    local path = mp.get_property('path') or ''
    local is_local = path ~= '' and (path:match('^file://') ~= nil or path:match('^%a[%w+.-]*://') == nil)
    if query ~= '' and is_local then
      pending = pending + 1
      rpc.call('semantic.search', { q = query, path = path:gsub('^file://', ''), k = limit }, function(err, res)
        if not err and type(res) == 'table' then
          dialogue = res.hits or {}
          dialogue_mode = res.mode or 'text'
        end
        pending = pending - 1
        if pending == 0 then finish() end
      end, 20)
    end
    if query ~= '' then
      rpc.call('iptv.search', { q = query, limit = limit, compact = true }, function(err, rows)
        if not err then channels = rows or {} end
        pending = pending - 1
        if pending == 0 then finish() end
      end, 15)
    else
      pending = pending - 1
    end
    rpc.call(query == '' and 'watch.recents' or 'watch.search', query == '' and { limit = 5, unfinished_only = true }
             or { q = query, limit = limit }, function(err, rows)
      if not err then recents = rows or {} end
      pending = pending - 1
      if pending == 0 then finish() end
    end, 15)
  else
    finish()
  end
end

views.palette = function()
  state.palette_query = ''
  local menu = palette_menu(uosc.loading_items('Buscando…'), '')
  uosc.open(menu)
  run_palette('')
end

-- ---------------------------------------------------------------------------------------------
-- actions

local function run_action(name)
  if name == 'iptv.refresh' then
    osd('Actualizando listas de TV y radio…')
    rpc.call('iptv.refresh', nil, function(err, res)
      if err then osd('Listas: ' .. fail(err, 'iptv.refresh')) return end
      local n = 0
      for _, s in ipairs(type(res) == 'table' and res or {}) do n = n + (tonumber(s.channels) or 0) end
      osd('Listas actualizadas: ' .. n .. ' canales')
    end, 180)
  elseif name == 'ytdl.update.check' then
    rpc.call('ytdl.update.check', { force = true }, function(err, st)
      if err then osd('yt-dlp: ' .. fail(err, 'ytdl.update.check')) return end
      if st.error and st.error ~= '' then osd('yt-dlp: ' .. st.error)
      elseif st.update_available then osd('Disponible yt-dlp ' .. st.latest .. ' (instalado ' .. (st.installed or '?') .. ')')
      else osd('yt-dlp al día (' .. (st.installed or '?') .. ')') end
    end, 60)
  elseif name == 'status' then
    rpc.call('capabilities', nil, function(err, caps)
      if err then osd('mpvd: ' .. fail(err, 'capabilities')) return end
      local services = {}
      for k in pairs(caps.services or {}) do table.insert(services, k) end
      table.sort(services)
      mp.osd_message(string.format('mpvd %s · %d sesiones · %d métodos · servicios: %s · %s',
        caps.mpvd or '?', caps.sessions or 0, #(caps.methods or {}), table.concat(services, ', '),
        (caps.hardware or {}).tier or ''), 6)
    end)
  end
end

local function open_recent(v)
  mp.commandv('loadfile', v.open, 'replace')
end

local set_pref  -- defined with the bindings

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.child then
      -- another script's menu, one level below this one: it gets our crumbs and comes back here on "Atrás"
      local crumbs = { nav.HOME }
      for _, spec in ipairs(state.stack) do crumbs[#crumbs + 1] = spec.title end
      nav.open_child(v.child.script, v.child.entry, crumbs, state.view)
    elseif v.site then
      -- H41 · Tab la copia (para abrirla en el móvil), Enter la abre en el navegador de este equipo
      if ev.action == 'copy' then
        clip.copy_osd(brand.site, osd, 'la dirección de ' .. brand.name)
      else
        local platform = mp.get_property_native('platform') or ''
        local open = platform == 'windows' and 'explorer' or (platform == 'darwin' and 'open' or 'xdg-open')
        mp.command_native_async({ name = 'subprocess', args = { open, brand.site }, detach = true,
                                  playback_only = false }, function() end)
        osd('Abriendo ' .. brand.site)
        uosc.close(MENU)
      end
    elseif v.pref then
      set_pref(v.pref, not opts[v.pref])
      reopen_current()
    elseif v.cmd then
      uosc.close(MENU)
      uosc.close(PALETTE)
      if type(v.cmd) == 'table' then mp.command_native(v.cmd) else mp.command(v.cmd) end
    elseif v.action then
      uosc.close(MENU)
      uosc.close(PALETTE)
      run_action(v.action)
    elseif v.seek then
      uosc.close(PALETTE)
      mp.commandv('seek', tostring(v.seek), 'absolute')
      osd('⏱ ' .. fmt_time(v.seek))
    elseif v.channel then
      uosc.close(PALETTE)
      mp.commandv('script-message-to', 'mu_iptv', 'mu-iptv-play', v.channel)
    elseif v.open then
      if ev.action == 'forget' then
        rpc.call('watch.remove', { path = v.open }, function() reopen_current() end)
      else
        uosc.close(MENU)
        uosc.close(PALETTE)
        open_recent(v)
      end
    elseif v.clear then
      rpc.call('watch.clear', nil, function() reopen_current() end)
    elseif v.view then
      if v.view == 'palette' then
        uosc.close(MENU)
        state.stack = {}
        state.force_open = true
      end
      open_view({ name = v.view, args = v })
    end
  elseif ev.type == 'search' then
    if state.view == 'palette' then run_palette(ev.query or '') end
  elseif ev.type == 'back' then
    table.remove(state.stack)
    -- uosc only closes the menu whose type matches: the palette is PALETTE, so closing MENU did nothing at all
    if #state.stack == 0 then uosc.close(uosc.open_type()) else reopen_current() end
  end
end)

-- a module opened from here went back past its root: show the view it was opened from (one level above it)
mp.register_script_message('mu-nav-return', function(view)
  if not uosc.available() then return end
  state.stack = {}
  if view == 'start' then
    table.insert(state.stack, { name = 'start' })
  else
    table.insert(state.stack, { name = 'root', title = nav.HOME })
    if view and view ~= '' and view ~= 'root' and view ~= 'palette' and views[view] then
      table.insert(state.stack, { name = view })
    end
  end
  state.force_open = true
  open_view(table.remove(state.stack))
end)

local reset_timer = nil
local function reset_state()
  if #state.stack > 0 or state.view ~= '' then
    state.stack = {}
    state.view = ''
    state.palette_query = ''
    publish()
  end
end

mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if t == MENU or t == PALETTE then closing = false return end
  -- Sin menú: se bloquea en el acto cualquier reapertura (una respuesta de mpvd que llegue unos milisegundos
  -- después no debe volver a pintar lo que se acaba de cerrar), pero la PILA espera los 0,2 s de siempre.
  -- Cuando uosc sustituye un menú por otro —volver de un módulo al nuestro— destruye el viejo ANTES de crear el
  -- nuevo, y con la máquina cargada ese `nil` intermedio sí se observa: borrar la pila ahí dejaba el menú abierto
  -- sin camino de vuelta y el siguiente ⌫ lo cerraba todo en vez de subir un nivel.
  if t == nil or t == '' then closing = true end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    local open = uosc.open_type()
    if open == MENU or open == PALETTE then return end
    reset_state()
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- bindings, button, start screen

local function open_root()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  state.force_open = true
  open_view({ name = 'root' })
end

local function open_palette()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'palette' })
end

-- opens a view of the main menu with the root below it (so "Atrás" leads to the main menu)
local function open_under_root(view)
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = { { name = 'root', title = nav.HOME } }
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = view })
end

-- click on the video → pause (only while the preference is on: bound, the left button would stop dragging the window)
local function apply_click_pause()
  if opts.click_pause then
    mp.add_key_binding('MBTN_LEFT', 'click-pause', function()
      mp.command('cycle pause')
    end)
  else
    mp.remove_key_binding('click-pause')
  end
end

local PREF_LABELS = { resume = 'Continuar viendo', click_pause = 'Pausar con un clic en el vídeo' }
set_pref = function(name, value)
  opts[name] = value and true or false
  P:set(name, opts[name])
  if name == 'click_pause' then apply_click_pause() end
  publish()
  osd((PREF_LABELS[name] or name) .. ': ' .. (opts[name] and 'activado' or 'desactivado'))
end

mp.add_key_binding(nil, 'root', open_root)
mp.add_key_binding(nil, 'palette', open_palette)
mp.add_key_binding(nil, 'recents', function() open_under_root('recents') end)
mp.add_key_binding(nil, 'help', function() open_under_root('help') end)
-- the ● button and «Grabar» belong to mu-record (H18); kept for old bindings
mp.add_key_binding(nil, 'record', function() mp.commandv('script-binding', 'mu_record/record-menu') end)
mp.add_key_binding(nil, 'resume-toggle', function() set_pref('resume', not opts.resume) end)
mp.add_key_binding(nil, 'click-pause-toggle', function() set_pref('click_pause', not opts.click_pause) end)
N:entry('root', open_root)
P:on_change(function(reason)
  if reason == 'reset' then
    opts.resume, opts.click_pause = P:get('resume'), P:get('click_pause')
    apply_click_pause()
    publish()
  end
end)
apply_click_pause()

local function set_button()
  uosc.set_button('mu-menu', { icon = 'apps', tooltip = 'Menú (alt+m)', command = { 'script-binding', SCRIPT .. '/root' } })
end
mp.register_script_message('uosc-version', set_button)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button() end
  local w = (core and core.work) or ''
  if w ~= work_text then
    work_text = w
    -- si el menú principal está abierto, que la línea aparezca o desaparezca sin tener que volver a entrar
    if state.view == 'root' and uosc.open_type() == MENU then open_view({ name = 'root' }, false) end
  end
end)

local function maybe_start_screen()
  if not opts.start_screen or state.start_shown then return end
  if not mp.get_property_native('idle-active') then return end
  if (mp.get_property_number('playlist-count') or 0) > 0 then return end
  if not uosc.available() then return end
  state.start_shown = true
  state.stack = {}
  open_view({ name = 'start' })
end

mp.add_timeout(opts.start_delay, maybe_start_screen)

-- After a failed load mpv stays idle on an empty window (bin/mpv-uos always runs with --idle=yes): bring the
-- home screen back once the error message had time to show, unless another menu is open.
local last_end_reason = ''
mp.register_event('end-file', function(ev) last_end_reason = ev.reason or '' end)
mp.observe_property('idle-active', 'bool', function(_, idle)
  if not idle or not opts.start_screen or not state.start_shown or last_end_reason ~= 'error' then return end
  mp.add_timeout(1.5, function()
    if not mp.get_property_native('idle-active') or uosc.open_type() then return end
    state.stack = {}
    open_view({ name = 'start' })
  end)
end)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc and not state.start_shown then mp.add_timeout(0.2, maybe_start_screen) end
end)

publish()
-- ---------------------------------------------------------------------------------------------
-- confirmation dialog for mpvd / MCP actions: `mu-confirm <token> <text>` → user-data/mu/confirm = {token, answer}

local CONFIRM_MENU = 'mu-confirm'
local confirm_state = { token = nil, timer = nil }

local function confirm_answer(token, answer)
  if confirm_state.timer then confirm_state.timer:kill(); confirm_state.timer = nil end
  confirm_state.token = nil
  mp.set_property_native('user-data/mu/confirm', { token = token, answer = answer, at = mp.get_time() })
  if uosc.open_type() == CONFIRM_MENU then uosc.close(CONFIRM_MENU) end
end

-- ``si``/``no``: etiquetas propias (opcionales). Una pregunta como «¿sigo con los subtítulos o lo dejo?» no se contesta
-- con «Sí, adelante»; que cada quien ponga las palabras de su decisión.
mp.register_script_message('mu-confirm', function(token, text, seconds, si, no)
  token = token or ''
  confirm_state.token = token
  mp.set_property_native('user-data/mu/confirm_request', { token = token, text = text or '', at = mp.get_time() })
  if not uosc.available() then
    -- no UI to ask: refuse (the caller treats anything but "yes" as no)
    confirm_answer(token, 'no-ui')
    return
  end
  uosc.open({
    type = CONFIRM_MENU, title = text or '¿Confirmar?', callback = { SCRIPT, 'mu-menu-confirm-event' }, on_close = 'callback',
    items = {
      { title = (si and si ~= '') and si or 'Sí, adelante', icon = 'check',
        value = { token = token, answer = 'yes' } },
      { title = (no and no ~= '') and no or 'No', icon = 'close', value = { token = token, answer = 'no' } },
    },
  })
  local wait = tonumber(seconds) or 15
  confirm_state.timer = mp.add_timeout(wait, function()
    if confirm_state.token == token then confirm_answer(token, 'timeout') end
  end)
end)

mp.register_script_message('mu-confirm-close', function(token)
  if confirm_state.token == token then confirm_answer(token, 'timeout') end
end)

mp.register_script_message('mu-menu-confirm-event', function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type == 'activate' and type(ev.value) == 'table' then
    confirm_answer(ev.value.token, ev.value.answer)
  elseif ev.type == 'close' or ev.type == 'back' then
    if confirm_state.token then confirm_answer(confirm_state.token, 'no') end
  end
end)

msg.info('mu-menu loaded')
