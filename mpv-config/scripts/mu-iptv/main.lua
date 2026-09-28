-- mu-iptv: "TV y radio" menus in uosc backed by mpvd (iptv.* / radio.* services), zapping, channel OSD,
-- ICY titles for radio and live recording (stream-record).
-- Script name: mu_iptv. Bindings: tv-menu, tv-search, zap-next, zap-prev, record-toggle (see input.conf).
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-iptv-event'
local MENU = 'mu-iptv'
local SEARCH_MENU = 'mu-iptv-search'

local opts = {
  record_dir = '~~desktop/MPV-UOS',  -- mpv path placeholders allowed
  osd_seconds = 3,
  world_limit = 1500,                -- channels per country (iptv-org)
  radio_limit = 300,                 -- stations per country (Radio Browser)
  radio_countries = 80,              -- countries listed for world radio (by station count)
  search_limit = 60,
}
options.read_options(opts, 'mu-iptv')

local state = {
  current = nil,      -- channel dict from iptv.play
  current_url = '',   -- URL actually loaded for it (other scripts compare it with `path`)
  recording = '',     -- path of the file being recorded
  icy_title = '',
  view = '',          -- id of the view shown (for tests/diagnostics)
  stack = {},         -- navigation stack of view specs {name=..., args=...}
  search_results = 0,
  last_error = '',    -- last mpvd/uosc error shown (diagnostics)
  force_open = false, -- next show() replaces the menu instead of updating it (leaving the palette)
}

local function publish()
  mp.set_property_native('user-data/mu/iptv', {
    current = state.current or '', current_url = state.current_url, recording = state.recording, icy_title = state.icy_title,
    view = state.view, search_results = state.search_results, last_error = state.last_error,
    depth = #state.stack,
  })
end

local function osd(text)
  mp.osd_message(text, opts.osd_seconds)
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

-- ---------------------------------------------------------------------------------------------
-- playback

local function apply_play_info(info)
  state.current = info.channel
  state.current_url = info.url or ''
  publish()
  local ok = mp.command_native({ 'loadfile', info.url, 'replace', -1, info.options or {} })
  if ok == nil then msg.warn('loadfile failed for ' .. info.url) end
  osd((info.channel.kind == 'radio' and '📻 ' or '📺 ') .. info.channel.name)
end

local function play(channel_id, cb)
  rpc.call('iptv.play', { id = channel_id }, function(err, info)
    if err then osd('No se pudo reproducir: ' .. fail(err, 'iptv.play')) return end
    apply_play_info(info)
    if cb then cb() end
  end)
end

local function zap(delta)
  if not state.current or state.current == '' then
    osd('Sin canal activo: abre TV y radio (alt+t)')
    return
  end
  rpc.call('iptv.zap', { id = state.current.id, delta = delta }, function(err, info)
    if err then osd('Zapping: ' .. fail(err, 'iptv.zap')) return end
    apply_play_info(info)
  end)
end

mp.register_event('file-loaded', function()
  if state.current and state.current ~= '' then
    local title = state.current.name
    if state.current.group then title = title .. '  ·  ' .. state.current.group end
    osd((state.current.kind == 'radio' and '📻 ' or '📺 ') .. title)
  end
end)

mp.register_event('end-file', function(ev)
  if ev.reason == 'error' and state.current and state.current ~= '' then
    osd('⚠ ' .. state.current.name .. ': no se pudo abrir (' .. tostring(ev.file_error or ev.error or '') .. ')')
  end
end)

-- ICY / stream titles (radio and some TV streams)
mp.observe_property('metadata/by-key/icy-title', 'string', function(_, value)
  state.icy_title = value or ''
  publish()
  if value and value ~= '' and state.current and state.current ~= '' then
    osd('♪ ' .. value)
  end
end)

-- ---------------------------------------------------------------------------------------------
-- recording

local function sanitize(name)
  return (name:gsub('[/\\:*?"<>|]', '_'):gsub('%s+', ' '):sub(1, 60))
end

local function ensure_dir(dir)
  local info = utils.file_info(dir)
  if info and info.is_dir then return true end
  local platform = mp.get_property_native('platform')
  local args = platform == 'windows' and { 'cmd', '/c', 'mkdir', dir } or { 'mkdir', '-p', dir }
  local res = mp.command_native({ name = 'subprocess', args = args, playback_only = false })
  return res and res.status == 0
end

local function record_toggle()
  local active = mp.get_property('stream-record') or ''
  if active ~= '' then
    mp.set_property('stream-record', '')
    state.recording = ''
    publish()
    osd('⏹ Grabación guardada: ' .. active)
    return
  end
  if (mp.get_property('path') or '') == '' then
    osd('Nada que grabar')
    return
  end
  local dir = mp.command_native({ 'expand-path', opts.record_dir })
  if not ensure_dir(dir) then
    osd('No se pudo crear la carpeta ' .. dir)
    return
  end
  local name = state.current and state.current ~= '' and state.current.name or (mp.get_property('media-title') or 'grabacion')
  local is_radio = state.current and state.current ~= '' and state.current.kind == 'radio'
    or (mp.get_property_native('vid') == false and mp.get_property_native('aid') ~= false)
  local path = utils.join_path(dir, sanitize(name) .. '-' .. os.date('%Y%m%d-%H%M%S') .. (is_radio and '.mka' or '.mkv'))
  mp.set_property('stream-record', path)
  state.recording = path
  publish()
  osd('⏺ Grabando en ' .. path)
end

-- ---------------------------------------------------------------------------------------------
-- clipboard

local function copy_to_clipboard(text)
  local platform = mp.get_property_native('platform')
  local candidates
  if platform == 'windows' then
    candidates = { { 'cmd', '/c', 'clip' } }
  elseif platform == 'darwin' then
    candidates = { { 'pbcopy' } }
  else
    candidates = { { 'wl-copy' }, { 'xclip', '-selection', 'clipboard' }, { 'xsel', '--clipboard', '--input' } }
  end
  local function try(i)
    local args = candidates[i]
    if not args then osd('Sin portapapeles; URL: ' .. text) return end
    mp.command_native_async({ name = 'subprocess', args = args, stdin_data = text, playback_only = false,
                              capture_stdout = true, capture_stderr = true }, function(ok, res)
      if ok and res and res.status == 0 then osd('URL copiada') else try(i + 1) end
    end)
  end
  try(1)
end

-- ---------------------------------------------------------------------------------------------
-- menu building

local ACTIONS = {
  { name = 'fav', icon = 'star', label = 'Favorito (añadir/quitar)' },
  { name = 'copy', icon = 'content_copy', label = 'Copiar URL' },
}

local function channel_item(ch)
  local hints = {}
  if ch.favorite then table.insert(hints, '★') end
  if ch.quality then table.insert(hints, ch.quality) end
  if ch.geo_blocked then table.insert(hints, 'geo') end
  if ch.health == false then table.insert(hints, '✕') end
  return {
    title = ch.name,
    hint = #hints > 0 and table.concat(hints, ' ') or nil,
    icon = ch.kind == 'radio' and 'radio' or 'live_tv',
    value = { play = ch.id },
    muted = ch.health == false or nil,
    bold = ch.favorite or nil,
  }
end

local function grouped_items(channels, key)
  local order, groups = {}, {}
  for _, ch in ipairs(channels) do
    local g = ch[key] or 'Otros'
    if not groups[g] then groups[g] = {}; table.insert(order, g) end
    table.insert(groups[g], channel_item(ch))
  end
  if #order == 1 then return groups[order[1]] end
  table.sort(order, function(a, b) return a:lower() < b:lower() end)
  local items = {}
  for _, g in ipairs(order) do
    table.insert(items, { title = g, hint = tostring(#groups[g]), items = groups[g] })
  end
  return items
end

local function base_menu(title, items, extra)
  local menu = {
    type = MENU, title = title, items = items, callback = { SCRIPT, EVENT },
    on_close = 'callback', keep_open = false, item_actions = ACTIONS, search_submenus = true,
    footnote = 'Enter reproduce · Tab acciones (★ favorito, copiar URL) · / busca · ⌫ atrás',
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return menu
end

local function show(title, items, extra)
  if uosc.open_type() == MENU and not state.force_open then
    uosc.update(base_menu(title, items, extra))
  else
    uosc.open(base_menu(title, items, extra))
  end
  state.force_open = false
end

local function show_loading(title)
  show(title, uosc.loading_items())
end

local views = {}

local function open_view(spec, push)
  if push ~= false then table.insert(state.stack, spec) end
  state.view = spec.name .. (spec.args and spec.args.id and (':' .. spec.args.id) or '')
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

views.root = function()
  local items = {
    { title = 'Buscar canal o emisora…', icon = 'search', value = { view = 'search' } },
    { title = 'España · TV', icon = 'live_tv', value = { view = 'source', id = 'tdt_tv' } },
    { title = 'España · Radio', icon = 'radio', value = { view = 'source', id = 'tdt_radio' } },
    { title = 'Mundo · TV por país', icon = 'public', value = { view = 'world' } },
    { title = 'Radio mundial', icon = 'language', value = { view = 'radio' } },
    { title = 'Favoritos', icon = 'star', value = { view = 'favorites' } },
    { title = 'Recientes', icon = 'history', value = { view = 'recents' } },
    { title = 'Mis listas', icon = 'playlist_add', value = { view = 'lists' }, separator = true },
  }
  if state.current and state.current ~= '' then
    local rec = (mp.get_property('stream-record') or '') ~= ''
    table.insert(items, {
      title = rec and 'Detener grabación' or 'Grabar el directo',
      icon = rec and 'stop_circle' or 'fiber_manual_record',
      hint = rec and '⏺' or nil, value = { view = 'record' },
    })
  end
  table.insert(items, { title = 'Actualizar listas', icon = 'refresh', value = { view = 'refresh' } })
  show('TV y radio', items)
end

local SOURCE_TITLES = { tdt_tv = 'España · TV', tdt_radio = 'España · Radio' }

views.source = function(args)
  local title = args.name or SOURCE_TITLES[args.id] or args.id
  if not require_mpvd(title) then return end
  show_loading(title)
  rpc.call('iptv.channels', { source = args.id, compact = true, limit = 5000 }, function(err, res)
    if err then show(title, uosc.message_items(fail(err, 'iptv.channels'), 'error')) return end
    if #res.items == 0 then
      show(title, uosc.message_items('Lista vacía o no descargada (Actualizar listas)', 'info'))
      return
    end
    local items = grouped_items(res.items, 'group')
    table.insert(items, {
      title = 'Comprobar canales en segundo plano', hint = tostring(#res.items), icon = 'network_check',
      value = { health = args.id }, keep_open = true, actions = {}, separator = true,
    })
    show(title, items)
  end, 60)
end

local function health_check(source_id)
  rpc.call('iptv.health.check', { source = source_id, limit = 500 }, function(err, job)
    if err then osd('Comprobación: ' .. fail(err, 'iptv.health.check')) return end
    osd('Comprobando canales en segundo plano (trabajo ' .. tostring(job.id or '?') .. '); los caídos saldrán con ✕')
  end, 60)
end

-- user M3U lists -------------------------------------------------------------------------------

local function list_name_from_url(url)
  local last = url:match('([^/?#]+)[?#]?[^/]*$') or ''
  last = last:gsub('%.m3u8?$', ''):gsub('[%-_%.]+', ' ')
  if last == '' or last:match('^%s*$') then last = url:match('^%a+://([^/:]+)') or 'Lista' end
  return (last:gsub('^%l', string.upper))
end

local function add_list(url)
  url = (url or ''):gsub('^%s+', ''):gsub('%s+$', '')
  if not url:match('^https?://') and not url:match('^file://') then
    osd('No parece una URL de lista M3U: ' .. url)
    return
  end
  show_loading('Descargando lista…')
  rpc.call('iptv.sources.add', { url = url, name = list_name_from_url(url) }, function(err, st)
    if err then show('Mis listas', uosc.message_items(fail(err, 'iptv.sources.add'), 'error')) return end
    if st.error then
      osd('Lista añadida, pero no se pudo cargar: ' .. st.error)
    else
      osd('Lista añadida: ' .. st.name .. ' (' .. tostring(st.channels) .. ' canales)')
    end
    state.stack = { { name = 'root' }, { name = 'lists' } }
    state.force_open = true  -- the palette is replaced by a normal list menu
    open_view({ name = 'source', args = { view = 'source', id = st.id, name = st.name } })
  end, 120)
end

views.lists = function()
  if not require_mpvd('Mis listas') then return end
  show_loading('Mis listas')
  rpc.call('iptv.sources', nil, function(err, rows)
    if err then show('Mis listas', uosc.message_items(fail(err, 'iptv.sources'), 'error')) return end
    local items = {
      { title = 'Añadir lista M3U…', hint = 'URL', icon = 'add', value = { view = 'add_list' }, actions = {} },
    }
    local n = 0
    for _, st in ipairs(rows) do
      if not st.builtin then
        n = n + 1
        table.insert(items, {
          title = st.name, icon = st.kind == 'radio' and 'radio' or 'playlist_play',
          hint = st.error and 'error' or (tostring(st.channels) .. ' canales'), muted = st.error ~= nil,
          value = { view = 'source', id = st.id, name = st.name },
          actions = { { name = 'remove', icon = 'delete', label = 'Quitar lista' } },
        })
      end
    end
    if n == 0 then
      table.insert(items, { title = 'Sin listas propias: añade una URL M3U/M3U8', icon = 'info',
                            align = 'center', selectable = false, muted = true })
    end
    show('Mis listas', items)
  end)
end

views.add_list = function()
  uosc.open({
    type = MENU, title = 'Pega (ctrl+v) o escribe la URL de la lista M3U y pulsa Enter',
    items = uosc.message_items('La lista se descarga y queda en «Mis listas»', 'playlist_add'),
    callback = { SCRIPT, EVENT }, search_style = 'palette', search_debounce = 200,
    on_search = 'callback', on_paste = 'callback', on_close = 'callback',
    footnote = 'Enter añade · ⌫ atrás',
  })
end

local function add_list_prompt(query)
  local items
  if (query or '') == '' then
    items = uosc.message_items('La lista se descarga y queda en «Mis listas»', 'playlist_add')
  else
    items = { { title = 'Añadir ' .. query, icon = 'add', value = { add_url = query }, actions = {} } }
  end
  uosc.update({
    type = MENU, title = 'Pega (ctrl+v) o escribe la URL de la lista M3U y pulsa Enter', items = items,
    callback = { SCRIPT, EVENT }, search_style = 'palette', search_debounce = 200,
    on_search = 'callback', on_paste = 'callback', on_close = 'callback', search_suggestion = query,
    footnote = 'Enter añade · ⌫ atrás',
  })
end

local function remove_list(source_id)
  rpc.call('iptv.sources.remove', { id = source_id }, function(err)
    if err then osd('Quitar lista: ' .. fail(err, 'iptv.sources.remove')) return end
    osd('Lista quitada')
    if uosc.open_type() == MENU then reopen_current() end
  end)
end

views.world = function()
  if not require_mpvd('Mundo · TV') then return end
  show_loading('Mundo · TV')
  rpc.call('iptv.countries', { source = 'iptv_org' }, function(err, rows)
    if err then show('Mundo · TV', uosc.message_items(fail(err, 'iptv.countries'), 'error')) return end
    local items = {}
    for _, c in ipairs(rows) do
      table.insert(items, { title = (c.flag and (c.flag .. ' ') or '') .. c.name, hint = tostring(c.count),
                            value = { view = 'country', id = c.code, name = c.name } })
    end
    if #items == 0 then items = uosc.message_items('Sin canales (Actualizar listas)', 'info') end
    show('Mundo · TV', items)
  end, 120)
end

views.country = function(args)
  show_loading(args.name or args.id)
  rpc.call('iptv.channels', { source = 'iptv_org', country = args.id, compact = true, limit = opts.world_limit },
    function(err, res)
      if err then show(args.name or args.id, uosc.message_items(fail(err, 'iptv.channels'), 'error')) return end
      show(args.name or args.id, grouped_items(res.items, 'category'))
    end, 60)
end

views.radio = function()
  if not require_mpvd('Radio mundial') then return end
  show_loading('Radio mundial')
  rpc.call('radio.countries', nil, function(err, rows)
    if err then show('Radio mundial', uosc.message_items(fail(err, 'radio.countries'), 'error')) return end
    local items = { { title = 'Más votadas del mundo', icon = 'trending_up', value = { view = 'radio_top' } } }
    for i, c in ipairs(rows) do
      if i > opts.radio_countries then break end
      table.insert(items, { title = c.name, hint = tostring(c.count),
                            value = { view = 'radio_country', id = c.code, name = c.name } })
    end
    show('Radio mundial', items)
  end, 60)
end

local function radio_list(title, params)
  show_loading(title)
  rpc.call('radio.stations', params, function(err, rows)
    if err then show(title, uosc.message_items(fail(err, 'radio.stations'), 'error')) return end
    local items = {}
    for _, st in ipairs(rows) do
      local it = channel_item(st)
      it.hint = st.category or it.hint
      table.insert(items, it)
    end
    if #items == 0 then items = uosc.message_items('Sin emisoras', 'info') end
    show(title, items)
  end, 60)
end

views.radio_country = function(args)
  radio_list(args.name or args.id, { country = args.id, limit = opts.radio_limit, compact = true })
end

views.radio_top = function()
  radio_list('Radio · más votadas', { top = 100, compact = true })
end

local function simple_list(title, method, params, empty_text)
  if not require_mpvd(title) then return end
  show_loading(title)
  rpc.call(method, params, function(err, rows)
    if err then show(title, uosc.message_items(fail(err, method), 'error')) return end
    local items = {}
    for _, ch in ipairs(rows) do table.insert(items, channel_item(ch)) end
    if #items == 0 then items = uosc.message_items(empty_text, 'info') end
    show(title, items)
  end)
end

views.favorites = function()
  simple_list('Favoritos', 'iptv.favorites.list', { compact = true }, 'Sin favoritos: pulsa Tab sobre un canal y elige ★')
end

views.recents = function()
  simple_list('Recientes', 'iptv.recents.list', { limit = 30, compact = true }, 'Todavía no has visto nada')
end

views.refresh = function()
  if not require_mpvd('TV y radio') then return end
  show_loading('Actualizando listas…')
  rpc.call('iptv.refresh', { force = true }, function(err, states)
    if err then show('TV y radio', uosc.message_items(fail(err, 'iptv.refresh'), 'error')) return end
    local items = {}
    for _, st in ipairs(states) do
      table.insert(items, { title = st.name, hint = st.error and 'error' or (tostring(st.channels) .. ' canales'),
                            icon = st.error and 'error' or 'check_circle', selectable = false, muted = st.error ~= nil })
    end
    table.insert(items, { title = 'Volver', icon = 'arrow_back', value = { view = 'root' }, separator = false })
    show('Listas actualizadas', items)
  end, 300)
end

views.record = function()
  record_toggle()
  table.remove(state.stack)  -- not a real view
  uosc.close(MENU)
end

-- Search palette (its own type so the search event is unambiguous)
local search_seq = 0

local function search_menu(items, query)
  return {
    type = SEARCH_MENU, title = 'Buscar canal o emisora', items = items, callback = { SCRIPT, EVENT },
    search_style = 'palette', search_debounce = 300, on_search = 'callback', on_close = 'callback',
    item_actions = ACTIONS, search_suggestion = query,
    footnote = 'Busca en España, iptv-org y Radio Browser · Tab acciones',
  }
end

views.search = function()
  if not rpc.connected() then require_mpvd('Buscar') return end
  uosc.open(search_menu(uosc.message_items('Escribe para buscar (sin acentos vale)', 'search')))
end

local function run_search(query)
  search_seq = search_seq + 1
  local seq = search_seq
  if query == '' then
    uosc.update(search_menu(uosc.message_items('Escribe para buscar', 'search'), query))
    return
  end
  local results, done = {}, 0
  local function finish()
    done = done + 1
    if done < 2 or seq ~= search_seq then return end
    state.search_results = #results
    publish()
    local items = {}
    for _, ch in ipairs(results) do
      local it = channel_item(ch)
      it.hint = (ch.source == 'radio_browser' and 'radio mundial')
        or (ch.source == 'iptv_org' and ('iptv-org · ' .. (ch.country or ''):upper()))
        or (ch.group or ch.source)
      table.insert(items, it)
    end
    if #items == 0 then items = uosc.message_items('Sin resultados para «' .. query .. '»', 'search_off') end
    uosc.update(search_menu(items, query))
  end
  rpc.call('iptv.search', { q = query, limit = opts.search_limit, compact = true }, function(err, rows)
    if not err then for _, r in ipairs(rows) do table.insert(results, r) end end
    finish()
  end)
  rpc.call('radio.stations', { q = query, limit = 20, compact = true }, function(err, rows)
    if not err then for _, r in ipairs(rows) do table.insert(results, r) end end
    finish()
  end, 20)
end

-- ---------------------------------------------------------------------------------------------
-- uosc events

local function toggle_favorite(channel_id)
  rpc.call('iptv.favorites.toggle', { id = channel_id }, function(err, res)
    if err then osd('Favoritos: ' .. fail(err, 'favorites.toggle')) return end
    osd(res.favorite and '★ Añadido a favoritos' or '☆ Quitado de favoritos')
    if uosc.open_type() == MENU then reopen_current() end
  end)
end

local function copy_channel(channel_id)
  rpc.call('iptv.channel', { id = channel_id }, function(err, ch)
    if err then osd('Copiar: ' .. fail(err, 'iptv.channel')) return end
    copy_to_clipboard(ch.url)
  end)
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.health then
      health_check(v.health)
    elseif v.add_url then
      add_list(v.add_url)
    elseif v.view and ev.action == 'remove' and v.view == 'source' then
      remove_list(v.id)
    elseif v.play then
      if ev.action == 'fav' then toggle_favorite(v.play)
      elseif ev.action == 'copy' then copy_channel(v.play)
      else
        play(v.play)
        if not ev.keep_open then
          uosc.close(MENU)
          uosc.close(SEARCH_MENU)
        end
      end
    elseif v.view then
      if v.ensure then mp.commandv('script-message-to', 'mu_core', 'mu-ensure') end
      if v.view == 'root' then state.stack = {} end
      open_view({ name = v.view, args = v })
    end
  elseif ev.type == 'search' then
    if state.view == 'add_list' then add_list_prompt(ev.query or '') else run_search(ev.query or '') end
  elseif ev.type == 'paste' then
    if state.view == 'add_list' then add_list(ev.value or '') end
  elseif ev.type == 'back' then
    table.remove(state.stack)
    if #state.stack == 0 then uosc.close(MENU) else reopen_current() end
  elseif ev.type == 'close' then
    -- Not used for state: uosc dispatches `close` from its own thread while replacing a menu (old destroyed,
    -- new not yet created), so it is racy. The observer of user-data/uosc/menu/type below owns the reset.
    return
  end
end)

-- Navigation state follows the menu uosc really has open. Transient nil values (menu being replaced) are
-- filtered with a short delay before resetting.
local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if t == MENU or t == SEARCH_MENU then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    local open = uosc.open_type()
    if open == MENU or open == SEARCH_MENU then return end
    if #state.stack > 0 or state.view ~= '' then
      msg.info('menu closed (type=' .. tostring(open) .. '): navigation reset from view ' .. state.view)
      state.stack = {}
      state.view = ''
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- bindings and controls button

local function open_root()
  if not uosc.available() then
    osd('uosc no está cargado')
    return
  end
  state.stack = {}
  open_view({ name = 'root' })
end

mp.add_key_binding(nil, 'tv-menu', open_root)
mp.register_script_message('mu-iptv-play', function(channel_id) if channel_id and channel_id ~= '' then play(channel_id) end end)
mp.add_key_binding(nil, 'tv-search', function()
  state.stack = {}
  open_view({ name = 'search' })
end)
mp.add_key_binding(nil, 'zap-next', function() zap(1) end)
mp.add_key_binding(nil, 'zap-prev', function() zap(-1) end)
mp.add_key_binding(nil, 'record-toggle', record_toggle)

local function set_button()
  uosc.set_button('mu-tv', {
    icon = 'live_tv', tooltip = 'TV y radio (alt+t)', active = state.current ~= nil and state.current ~= '',
    command = { 'script-binding', SCRIPT .. '/tv-menu' },
  })
end
mp.register_script_message('uosc-version', set_button)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button() end
end)

publish()
msg.info('mu-iptv loaded')
