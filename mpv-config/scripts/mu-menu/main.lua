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
}
options.read_options(opts, 'mu-menu')

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

mp.register_event('file-loaded', function()
  local path = mp.get_property('path') or ''
  state.path, state.resumed, state.position = path, false, mp.get_property_number('time-pos') or 0
  state.duration = mp.get_property_number('duration') or 0
  state.title = mp.get_property('media-title') or path
  state.tracked = trackable(path)
  publish()
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

local function base_menu(title, items, extra)
  local menu = { type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, on_close = 'callback',
                 keep_open = false, search_submenus = true }
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

local function static_root_items()
  local playing = (mp.get_property('path') or '') ~= ''
  local items = {
    { title = 'Buscar: comandos, canales, recientes…', hint = 'alt+p', icon = 'search', value = { view = 'palette' } },
    { title = 'Abrir archivo', hint = 'o', icon = 'folder_open', value = { cmd = { 'script-binding', 'uosc/open-file' } } },
    { title = 'TV y radio', hint = 'alt+t', icon = 'live_tv', value = { cmd = { 'script-binding', 'mu_iptv/tv-menu' } } },
    { title = 'yt-dlp: calidad y descargas', hint = 'alt+y', icon = 'download',
      value = { cmd = { 'script-binding', 'mu_ytdl/ytdl-menu' } } },
    { title = 'Subtítulos IA (whisper)', hint = 'alt+i', icon = 'closed_caption',
      value = { cmd = { 'script-binding', 'mu_subs/subs-menu' } } },
    { title = 'Sonido e imagen (filtros, diagnóstico)', hint = 'alt+v', icon = 'tune',
      value = { cmd = { 'script-binding', 'mu_av/av-menu' } } },
    { title = 'Saltar intro y créditos', hint = 'alt+j', icon = 'skip_next',
      value = { cmd = { 'script-binding', 'mu_intro/intro-menu' } } },
    { title = 'Estudio (repetir línea, velocidad inteligente, notas, clips)', hint = 'alt+e', icon = 'school',
      value = { cmd = { 'script-binding', 'mu_study/study-menu' } } },
    { title = 'Mando a distancia (QR para el móvil)', hint = 'alt+z', icon = 'qr_code_2',
      value = { cmd = { 'script-binding', 'mu_remote/remote-qr' } } },
    { title = 'Lista de reproducción', hint = 'p', icon = 'list_alt', value = { cmd = { 'script-binding', 'uosc/playlist' } },
      separator = true },
  }
  local function binding(title, key, icon, name)
    return { title = title, hint = key, icon = icon, value = { cmd = { 'script-binding', name } } }
  end
  if playing then
    table.insert(items, binding('Subtítulos', 's', 'subtitles', 'uosc/subtitles'))
    table.insert(items, binding('Audio', 'a', 'graphic_eq', 'uosc/audio'))
    table.insert(items, binding('Capítulos', 'c', 'bookmark', 'uosc/chapters'))
    table.insert(items, { title = 'Captura de pantalla', hint = 'ctrl+s', icon = 'photo_camera',
                          value = { cmd = { 'async', 'screenshot' } } })
  end
  table.insert(items, { title = 'Menú completo (todas las teclas)', hint = 'uosc', icon = 'menu',
                        value = { cmd = { 'script-binding', 'uosc/menu' } }, separator = true })
  table.insert(items, { title = 'Salir', hint = 'q', icon = 'logout', value = { cmd = { 'quit' } } })
  return items
end

local function with_recents(limit, unfinished, cb)
  if not rpc.connected() then cb(nil, {}) return end
  rpc.call('watch.recents', { limit = limit, unfinished_only = unfinished }, function(err, rows)
    if err then fail(err, 'watch.recents'); cb(err, {}) return end
    cb(nil, rows or {})
  end, 10)
end

views.root = function()
  local items = static_root_items()
  show('MPV-UOS', items, { footnote = 'Enter abre · / busca · ⌫ atrás' })
  with_recents(opts.recents_in_root, true, function(_, rows)
    if state.view ~= 'root' then return end
    if #rows > 0 then
      local sub = {}
      for _, r in ipairs(rows) do table.insert(sub, recent_item(r)) end
      table.insert(sub, { title = 'Todos los recientes…', icon = 'history', value = { view = 'recents' } })
      table.insert(items, 2, { title = 'Continuar viendo', hint = tostring(#rows), items = sub })
    else
      table.insert(items, 2, { title = 'Recientes', icon = 'history', value = { view = 'recents' } })
    end
    show('MPV-UOS', items, { footnote = 'Enter abre · / busca · ⌫ atrás' })
  end)
end

views.recents = function()
  show('Recientes', uosc.loading_items())
  with_recents(50, false, function(err, rows)
    if err then show('Recientes', uosc.message_items(fail(err, 'watch.recents'), 'error')) return end
    local items = {}
    for _, r in ipairs(rows) do table.insert(items, recent_item(r)) end
    if #items == 0 then items = uosc.message_items('Todavía no has visto nada con MPV-UOS', 'history')
    else table.insert(items, { title = 'Borrar historial', icon = 'delete_sweep', value = { clear = true }, separator = true,
                               actions = {} }) end
    show('Recientes', items, { footnote = 'Enter continúa · Tab olvida · ⌫ atrás' })
  end)
end

views.start = function()
  local items = {
    { title = 'Abrir archivo', hint = 'o', icon = 'folder_open', value = { cmd = { 'script-binding', 'uosc/open-file' } } },
    { title = 'TV y radio', hint = 'alt+t', icon = 'live_tv', value = { cmd = { 'script-binding', 'mu_iptv/tv-menu' } } },
    { title = 'Buscar…', hint = 'alt+p', icon = 'search', value = { view = 'palette' } },
    { title = 'Menú completo', hint = 'alt+m', icon = 'apps', value = { view = 'root' }, separator = true },
  }
  show('MPV-UOS · Inicio', items)
  with_recents(12, false, function(_, rows)
    if state.view ~= 'start' then return end
    if #rows > 0 then
      local header = { { title = 'Continuar viendo', icon = 'history', selectable = false, muted = true, align = 'center' } }
      local recents = {}
      for _, r in ipairs(rows) do table.insert(recents, recent_item(r)) end
      local all = {}
      for _, it in ipairs(recents) do table.insert(all, it) end
      for i, it in ipairs(items) do
        if i == 1 then it.separator = true end
        table.insert(all, it)
      end
      for i = #header, 1, -1 do table.insert(all, 1, header[i]) end
      show('MPV-UOS · Inicio', all)
    end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- command palette

local CURATED = {
  { title = 'Pausa / reproducir', cmd = 'cycle pause', key = 'espacio' },
  { title = 'Pantalla completa', cmd = 'cycle fullscreen', key = 'f' },
  { title = 'Silenciar', cmd = 'cycle mute', key = 'm' },
  { title = 'Subir volumen', cmd = 'add volume 5', key = '0' },
  { title = 'Bajar volumen', cmd = 'add volume -5', key = '9' },
  { title = 'Velocidad +10 %', cmd = 'multiply speed 1.1', key = ']' },
  { title = 'Velocidad −10 %', cmd = 'multiply speed 1/1.1', key = '[' },
  { title = 'Velocidad normal', cmd = 'set speed 1.0', key = 'BS' },
  { title = 'Capítulo siguiente', cmd = 'add chapter 1', key = '!' },
  { title = 'Capítulo anterior', cmd = 'add chapter -1', key = '@' },
  { title = 'Siguiente de la lista', cmd = 'playlist-next', key = '>' },
  { title = 'Anterior de la lista', cmd = 'playlist-prev', key = '<' },
  { title = 'Bucle A-B', cmd = 'ab-loop', key = 'l' },
  { title = 'Repetir archivo', cmd = 'cycle-values loop-file inf no', key = 'L' },
  { title = 'Subtítulos: alternar visibilidad', cmd = 'cycle sub-visibility', key = 'v' },
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
}

local ACTIONS = {
  { title = 'Actualizar listas de TV y radio', icon = 'refresh', action = 'iptv.refresh' },
  { title = 'Buscar actualización de yt-dlp', icon = 'system_update_alt', action = 'ytdl.update.check' },
  { title = 'Ver descargas', icon = 'downloading', cmd = { 'script-binding', 'mu_ytdl/ytdl-downloads' } },
  { title = 'Estado de mpvd', icon = 'monitor_heart', action = 'status' },
  { title = 'Relanzar mpvd', icon = 'restart_alt', cmd = { 'script-message-to', 'mu_core', 'mu-ensure' } },
}

local commands_cache = nil
local function commands()
  if commands_cache then return commands_cache end
  local list, seen = {}, {}
  for _, b in ipairs(mp.get_property_native('input-bindings') or {}) do
    local comment = b.comment or ''
    local title = comment:match('^!%s*(.+)$')
    if title and b.cmd and b.cmd ~= 'ignore' and not b.is_weak and not seen[b.cmd] then
      seen[b.cmd] = true
      title = title:gsub('%s*>%s*', ' › ')
      table.insert(list, { title = title, cmd = b.cmd, key = b.key or '' })
    end
  end
  for _, c in ipairs(CURATED) do
    if not seen[c.cmd] then
      seen[c.cmd] = true
      table.insert(list, { title = c.title, cmd = c.cmd, key = c.key or '' })
    end
  end
  for _, c in ipairs(list) do c.fold = fold(c.title .. ' ' .. c.cmd) end
  commands_cache = list
  return list
end
mp.observe_property('input-bindings', 'native', function() commands_cache = nil end)

local function score(folded, words)
  local total = 0
  for _, w in ipairs(words) do
    local pos = folded:find(w, 1, true)
    if not pos then return nil end
    total = total + (pos == 1 and 3 or (folded:sub(pos - 1, pos - 1) == ' ' and 2 or 1))
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
    if seq ~= palette_seq or state.view ~= 'palette' then return end
    local out = {}
    local dl = {}
    for _, hit in ipairs(dialogue) do
      table.insert(dl, { title = hit.text, hint = fmt_time(hit.start), icon = 'forum', value = { seek = hit.start } })
    end
    section(dialogue_mode == 'semantic' and 'Diálogo (semántico)' or 'Diálogo', dl, out)
    section('Comandos', command_items(query, query == '' and 6 or limit), out)
    local ch = {}
    for _, c in ipairs(channels) do
      table.insert(ch, { title = c.name, hint = c.group or c.source, icon = c.kind == 'radio' and 'radio' or 'live_tv',
                         value = { channel = c.id } })
    end
    section('Canales', ch, out)
    local rc = {}
    for _, r in ipairs(recents) do table.insert(rc, recent_item(r)) end
    section('Recientes', rc, out)
    section('mpvd', action_items(query, query == '' and 2 or limit), out)
    if #out == 0 then out = uosc.message_items('Sin resultados para «' .. query .. '»', 'search_off') end
    state.palette_results = #out
    remember(out)
    publish()
    uosc.update(palette_menu(out, query))
  end
  if rpc.connected() then
    pending = 2
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

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.cmd then
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
    if #state.stack == 0 then uosc.close(MENU) else reopen_current() end
  end
end)

local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if t == MENU or t == PALETTE then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    local open = uosc.open_type()
    if open == MENU or open == PALETTE then return end
    if #state.stack > 0 or state.view ~= '' then
      state.stack = {}
      state.view = ''
      state.palette_query = ''
      publish()
    end
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

mp.add_key_binding(nil, 'root', open_root)
mp.add_key_binding(nil, 'palette', open_palette)
mp.add_key_binding(nil, 'recents', function() state.stack = { { name = 'root' } }; open_view({ name = 'recents' }) end)
mp.add_key_binding(nil, 'resume-toggle', function()
  opts.resume = not opts.resume
  osd(opts.resume and 'Continuar viendo: activado' or 'Continuar viendo: desactivado')
end)

local function set_button()
  uosc.set_button('mu-menu', { icon = 'apps', tooltip = 'MPV-UOS (alt+m)', command = { 'script-binding', SCRIPT .. '/root' } })
end
mp.register_script_message('uosc-version', set_button)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button() end
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

mp.register_script_message('mu-confirm', function(token, text, seconds)
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
      { title = 'Sí, adelante', icon = 'check', value = { token = token, answer = 'yes' } },
      { title = 'No', icon = 'close', value = { token = token, answer = 'no' } },
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
