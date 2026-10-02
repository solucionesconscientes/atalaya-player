-- mu-iptv: "TV y radio" menus in uosc backed by mpvd (iptv.* / radio.* services), zapping, channel OSD,
-- ICY titles for radio and live recording (stream-record); TV guide («ahora» in the lists, a guide per channel) and
-- scheduled recordings made by mpvd (iptv.epg.*, iptv.schedule.*; H21, ADR-049). The channel's own audio and subtitle
-- tracks with readable names («Audio y subtítulos del canal», CC / VO / AD in the lists) and «Buscar en esta lista» in
-- every list (iptv.tracks, iptv.search with a scope; H30).
-- Script name: mu_iptv. Bindings: tv-menu, tv-search, tv-guide, tv-schedule, tv-tracks, zap-next, zap-prev,
-- record-toggle.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local clip = require('mu.clip')
local prefs = require('mu.prefs')
local N = nav.new()

-- H57 · qué se hace en una franja programada: grabarlo (lo de siempre), ponerlo o las dos. Se recuerda.
local P = prefs.ns('mu-iptv', { sched_mode = 'record' })

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-iptv-event'
local MENU = 'mu-iptv'
local SEARCH_MENU = 'mu-iptv-search'

local opts = {
  osd_seconds = 3,
  world_limit = 1500,                -- channels per country (iptv-org)
  radio_limit = 300,                 -- stations per country (Radio Browser)
  radio_countries = 80,              -- countries listed for world radio (by station count)
  search_limit = 60,
  epg_hours = 30,                    -- guide of a channel: from now on, this many hours
  epg_margin_before = 60,            -- «Grabar este programa»: seconds before its start...
  epg_margin_after = 180,            -- ...and after its end (programmes rarely run on time)
  now_hint_chars = 34,               -- «ahora: …» in the channel lists, cut to this length
  schedule_dir = '',                 -- folder of scheduled recordings ('' = mpvd's <Vídeos>/MPV-UOS/Grabaciones)
  stall_seconds = 8,                 -- H39/E1: segundos sin sonar antes de pasar al siguiente espejo (0 = nunca)
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
  alternatives = {},  -- other copies of the current channel still untried ({id, name, url, options})
  entry_id = nil,     -- playlist entry of the URL we loaded (its end-file error triggers the next alternative)
  fallbacks = 0,      -- alternatives tried for the current channel
  epg_hints = 0,      -- channels with a known «ahora» (cache below)
  sched_defaults = nil,  -- H40/F1: {wake, after, power} que aplica mpvd a las grabaciones nuevas
  guide = nil,        -- guide shown: {id, programmes, epg_id}
  schedule_event = nil, -- last scheduled-recording event from mpvd {id, status, text}
  playing_id = nil,   -- id of the copy of the current channel really playing (an alternative after a fallback)
  tracks = nil,       -- the channel's audio/subtitle tracks named by mpvd: {type, id, label, role, selected, …}
  badges = {},        -- CC / VO / AD of the channel playing
  search_scope = '',  -- «Buscar en esta lista»: which list the palette searches (tests/diagnostics)
}

-- «What is on now» per channel id: {title, expires} (a programme is valid until it ends, at most 10 min) or
-- {expires} when the guide has nothing for it. Times are epoch seconds (os.time, the same clock as mpvd).
local epg_cache = {}

local function publish()
  mp.set_property_native('user-data/mu/iptv', {
    current = state.current or '', current_url = state.current_url, recording = state.recording, icy_title = state.icy_title,
    view = state.view, search_results = state.search_results, last_error = state.last_error,
    depth = #state.stack, fallbacks = state.fallbacks, alternatives_left = #state.alternatives,
    epg_hints = state.epg_hints, guide = state.guide or '', schedule_event = state.schedule_event or '',
    tracks = state.tracks or {}, badges = state.badges, search_scope = state.search_scope,
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

-- Live streams have no position to resume: forget any watch_later entry of the URL before loading it (the
-- per-file option save-position-on-quit=no from mpvd keeps a new one from being written).
local watch_stall   -- H39/E1: definido más abajo; load_url lo arranca en cada carga de canal
local function load_url(url, file_options)
  mp.commandv('delete-watch-later-config', url)
  if url:find('^file://') then  -- mpv keys local files by their plain path, not by the file:// URL
    local path = url:gsub('^file://', ''):gsub('%%(%x%x)', function(h) return string.char(tonumber(h, 16)) end)
    mp.commandv('delete-watch-later-config', path)
  end
  state.current_url = url
  if type(state.current) == 'table' then state.current.url = url end  -- mu-core names failures by this URL
  local res = mp.command_native({ 'loadfile', url, 'replace', -1, file_options or {} })
  if res == nil then msg.warn('loadfile failed for ' .. url) end
  -- H56 · `pause` es global en mpv y sobrevive al cambio de archivo: si venías de pausar algo o de la pantalla de
  -- inicio, el canal entraba pausado y había que darle al play. Eligiendo un canal lo que quieres es verlo.
  mp.set_property_bool('pause', false)
  state.entry_id = type(res) == 'table' and res.playlist_entry_id or nil
  if watch_stall then watch_stall(url) end
  publish()
end

local function apply_play_info(info)
  state.current = info.channel
  state.playing_id = info.channel and info.channel.id or nil
  state.tracks, state.badges = nil, {}
  state.alternatives = info.alternatives or {}
  state.fallbacks = 0
  load_url(info.url, info.options)
  osd((info.channel.kind == 'radio' and '📻 ' or '📺 ') .. info.channel.name)
end

-- The list repeats some channels (mirrors, FAST copies): when the preferred URL fails to open, try the next one.
local function try_next_alternative(why)
  local alt = table.remove(state.alternatives, 1)
  if not alt or type(state.current) ~= 'table' then publish() return false end
  state.fallbacks = state.fallbacks + 1
  local text = 'Probando otra fuente de «' .. (state.current.name or alt.name or '') .. '»…'
  msg.info((why or '') .. ' ' .. text .. ' (' .. alt.url .. ')')
  state.playing_id = alt.id
  load_url(alt.url, alt.options)
  osd(text)
  -- mu-core explains the failed load on the OSD at the same moment; keep ours on top
  mp.add_timeout(0.3, function() if state.current_url == alt.url then osd(text) end end)
  return true
end

mp.register_event('end-file', function(ev)
  if ev.reason ~= 'error' or not state.entry_id or ev.playlist_entry_id ~= state.entry_id then return end
  state.entry_id = nil
  try_next_alternative('error de carga:')
end)

-- H39/E1 · Lo que le pasó a Ser con las radios españolas: la lista trae «Cadena SER ×6» y los primeros espejos no dan
-- error, simplemente no suenan (se quedan conectando). El error de carga nunca llega, así que nadie pasaba al
-- siguiente. Esto vigila que el reloj avance de verdad; si no avanza, es como si hubiera fallado.
local stall_timer = nil

local function cancel_stall()
  if stall_timer then stall_timer:kill(); stall_timer = nil end
end

watch_stall = function(url)
  cancel_stall()
  if opts.stall_seconds <= 0 or #state.alternatives == 0 then return end
  local started = mp.get_property_number('time-pos')
  stall_timer = mp.add_timeout(opts.stall_seconds, function()
    stall_timer = nil
    if state.current_url ~= url or #state.alternatives == 0 then return end
    if mp.get_property_bool('pause', false) then return end          -- en pausa no se espera que avance
    local now = mp.get_property_number('time-pos')
    local moving = now ~= nil and (started == nil or now > started + 0.3)
    if moving and not mp.get_property_bool('core-idle', false) then return end
    state.entry_id = nil
    try_next_alternative('no empieza a sonar en ' .. opts.stall_seconds .. ' s:')
  end)
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

-- ---------------------------------------------------------------------------------------------
-- the channel's own audio and subtitle tracks (H30): mpvd names them (mpvd/iptv/tracks.py) and stores what the
-- channel carries for the CC / VO / AD hints of the lists. mpv track titles are read-only, so the names live here.

-- Is the file playing the channel mu-iptv loaded (and not something opened afterwards)?
local function channel_playing()
  return type(state.current) == 'table' and state.current_url ~= '' and mp.get_property('path') == state.current_url
end

local TRACK_FIELDS = { 'id', 'type', 'lang', 'title', 'codec', 'default', 'forced', 'selected', 'external', 'image',
                       'visual-impaired', 'hearing-impaired', 'program-id' }

local function track_list()
  local out = {}
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'audio' or t.type == 'sub' or (t.type == 'video' and t.selected) then
      local row = {}
      for _, k in ipairs(TRACK_FIELDS) do row[k] = t[k] end
      table.insert(out, row)
    end
  end
  return out
end

-- Without mpvd: the language code or title mpv shows (the menu still switches tracks).
local function raw_tracks()
  local out = {}
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if (t.type == 'audio' or t.type == 'sub') and not t.image then
      table.insert(out, { type = t.type, id = t.id, selected = t.selected,
                          label = t.title or t.lang or ((t.type == 'audio' and 'Audio ' or 'Subtítulos ') .. t.id) })
    end
  end
  return out
end

-- Ask mpvd for the names (and let it store the channel's badges); `cb(tracks)` gets the named list.
local function named_tracks(cb)
  local list = track_list()
  if not rpc.connected() then cb(raw_tracks()) return end
  local id = channel_playing() and state.playing_id or nil
  rpc.call('iptv.tracks', { tracks = list, id = id }, function(err, res)
    if err then fail(err, 'iptv.tracks') cb(raw_tracks()) return end
    if id and id == state.playing_id then
      state.tracks, state.badges = res.tracks or {}, res.badges or {}
      publish()
    end
    cb(res.tracks or {})
  end, 20)
end

mp.register_event('file-loaded', function()
  if state.current and state.current ~= '' then
    local title = state.current.name
    local group = state.current.group_label or state.current.group
    if group then title = title .. '  ·  ' .. group end
    osd((state.current.kind == 'radio' and '📻 ' or '📺 ') .. title)
  end
  if channel_playing() and state.current.kind ~= 'radio' then named_tracks(function() end) end
end)

-- A channel that fails to open is explained by mu-core (reason in Spanish + zapping hint).

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

-- H18: one «Grabar» for the whole player. mu-record owns stream-record, the folder, the red dot and the counter; the
-- TV menu only asks it to start or stop (two owners of stream-record left the counter running over a closed file).
local function record_toggle()
  local active = mp.get_property('stream-record') or ''
  if active ~= '' then
    mp.commandv('script-message-to', 'mu_record', 'mu-record-stop')
    state.recording = ''
    publish()
    return
  end
  if (mp.get_property('path') or '') == '' then
    osd('Nada que grabar')
    return
  end
  mp.commandv('script-message-to', 'mu_record', 'mu-record-start')
end

-- ---------------------------------------------------------------------------------------------
-- clipboard

-- H42/A4: el portapapeles vive en mu.clip (antes había tres copias de este código)
local function copy_to_clipboard(text) clip.copy_osd(text, osd, 'la URL del canal') end

-- ---------------------------------------------------------------------------------------------
-- menu building

local ACTIONS = {
  { name = 'fav', icon = 'star', label = 'Favorito (añadir/quitar)' },
  { name = 'guide', icon = 'event_note', label = 'Guía de programación' },
  { name = 'schedule', icon = 'schedule', label = 'Programar grabación…' },
  { name = 'copy', icon = 'content_copy', label = 'Copiar URL' },
}

-- Cut a UTF-8 string to `n` characters (with an ellipsis).
local function cut(text, n)
  text = tostring(text or '')
  local count = 0
  for pos in text:gmatch('()[%z\1-\127\194-\244][\128-\191]*') do
    count = count + 1
    if count > n then return (text:sub(1, pos - 1):gsub('%s+$', '')) .. '…' end
  end
  return text
end

local function now_title(id)
  local e = epg_cache[id]
  if e and e.title and os.time() < e.expires then return e.title end
  return nil
end

-- Hint of a channel: what we know about it, most useful first
-- ("ahora: Telediario · ★ · 720p50 · CC VO AD · con anuncios").
local function channel_hint(ch)
  local hints = {}
  local on_now = now_title(ch.id)
  if on_now then table.insert(hints, 'ahora: ' .. cut(on_now, opts.now_hint_chars)) end
  if ch.favorite then table.insert(hints, '★') end
  local quality = ch.quality_label or ch.quality
  if quality then table.insert(hints, quality) end
  if ch.low_bitrate then table.insert(hints, 'bitrate bajo') end
  -- the channel's own subtitles / original version / audio description (learnt by mpvd)
  if type(ch.badges) == 'table' and #ch.badges > 0 then table.insert(hints, table.concat(ch.badges, ' ')) end
  if ch.ads then table.insert(hints, 'con anuncios') end
  if ch.geo_blocked then table.insert(hints, 'geobloqueado') end
  local alts = tonumber(ch.alternatives) or 0
  if alts > 0 then table.insert(hints, '+' .. alts .. (alts == 1 and ' fuente' or ' fuentes')) end
  -- H39/E2: lo que dijo la comprobación, con palabras. Un «✕» a secas no decía si merecía la pena intentarlo, y con
  -- varias copias lo que importa es cuántas respondieron.
  local vivas = tonumber(ch.health_alive)
  if alts > 0 and vivas then
    table.insert(hints, vivas > 0 and (vivas .. ' comprobada' .. (vivas == 1 and '' or 's') .. ' OK')
      or 'ninguna respondió')
  elseif ch.health == false then
    table.insert(hints, '✕ ' .. (ch.health_detail or 'no se pudo abrir'))
  elseif ch.health == true then
    table.insert(hints, '✓ comprobado')
  end
  return #hints > 0 and table.concat(hints, ' · ') or nil
end

local function channel_item(ch)
  return {
    title = ch.name,
    hint = channel_hint(ch),
    icon = ch.kind == 'radio' and 'radio' or 'live_tv',
    value = { play = ch.id, name = ch.name },
    muted = (ch.health == false and (tonumber(ch.health_alive) or 0) == 0) or nil,
    bold = ch.favorite or nil,
  }
end

-- «Buscar en esta lista»: first row of every list of channels. `scope` says which list ({kind, id, name}); the
-- palette asks mpvd's search (every word, accents ignored) limited to it.
local function search_row(scope)
  return { title = 'Buscar en esta lista…', icon = 'search', hint = 'sin acentos vale', actions = {},
           value = { view = 'scoped_search', scope = scope } }
end

local FOLD = {
  ['á'] = 'a', ['é'] = 'e', ['í'] = 'i', ['ó'] = 'o', ['ú'] = 'u', ['ü'] = 'u', ['ñ'] = 'n~', ['à'] = 'a', ['è'] = 'e',
  ['ò'] = 'o', ['ç'] = 'c', ['Á'] = 'a', ['É'] = 'e', ['Í'] = 'i', ['Ó'] = 'o', ['Ú'] = 'u', ['Ñ'] = 'n~', ['À'] = 'a',
}
local function sort_key(text)
  return (tostring(text):gsub('[%z\1-\127\194-\244][\128-\191]*', function(c) return FOLD[c] or c end):lower())
end

-- Submenus by `key` (group/category), titled with the Spanish label mpvd sends (`<key>_label`).
local function grouped_items(channels, key)
  local order, groups = {}, {}
  for _, ch in ipairs(channels) do
    local g = ch[key .. '_label'] or ch[key] or 'Otros'
    if not groups[g] then groups[g] = {}; table.insert(order, g) end
    table.insert(groups[g], channel_item(ch))
  end
  if #order == 1 then return groups[order[1]] end
  table.sort(order, function(a, b) return sort_key(a) < sort_key(b) end)
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
    footnote = 'Enter reproduce · Tab acciones (★ favorito, guía, grabar, copiar URL) · / busca · ⌫ atrás',
  }
  for k, v in pairs(extra or {}) do menu[k] = v end
  return N:frame(menu, state.stack)
end

-- Titles and hints of the menu shown (two levels, capped): uosc does not expose its items (tests/diagnostics).
local function menu_rows(items, depth)
  local rows = {}
  for i, it in ipairs(items) do
    if i > 60 then break end
    local row = { title = it.title or '', hint = it.hint or '', separator = it.separator or false, value = it.value }
    if it.items and depth < 2 then row.items = menu_rows(it.items, depth + 1) end
    table.insert(rows, row)
  end
  return rows
end

local function publish_menu(title, items)
  mp.set_property_native('user-data/mu/iptv-menu', { title = title, items = menu_rows(items, 1) })
end

local function show(title, items, extra)
  publish_menu(title, items)
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

-- «ahora» for the channels of a list: one batch request to mpvd for what the cache lacks, then `cb()` re-renders.
-- While mpvd is still downloading the guide (first use, every ~12 h) it asks again a few times.
local function want_now(channels, cb, tries)
  local ids, t = {}, os.time()
  for _, ch in ipairs(channels or {}) do
    if ch.id and ch.kind ~= 'radio' and ch.source ~= 'radio_browser' then
      local e = epg_cache[ch.id]
      if not e or t >= e.expires then table.insert(ids, ch.id) end
    end
    if #ids >= 2000 then break end
  end
  if #ids == 0 or not rpc.connected() then return end
  rpc.call('iptv.epg.now', { ids = ids }, function(err, res)
    if err then fail(err, 'iptv.epg.now') return end
    local t2 = os.time()
    local got = type(res.channels) == 'table' and res.channels or {}
    local changed = false
    for _, id in ipairs(ids) do
      local e = got[id]
      if type(e) == 'table' and type(e.now) == 'table' then
        epg_cache[id] = { title = e.now.title, expires = math.min(tonumber(e.now.stop) or t2, t2 + 600) }
        changed = true
      else
        epg_cache[id] = { expires = t2 + (res.loading and 0 or 600) }
      end
    end
    local n = 0
    for _, e in pairs(epg_cache) do if e.title and t2 < e.expires then n = n + 1 end end
    state.epg_hints = n
    publish()
    if changed then cb() end
    tries = (tries or 0) + 1
    if res.loading and tries < 20 then
      mp.add_timeout(3, function() want_now(channels, cb, tries) end)
    end
  end, 30)
end

-- A list of channels: shown at once, shown again when their «ahora» arrives (if the user is still there).
local function show_channels(title, channels, build, extra)
  local view = state.view
  local function render()
    if state.view == view and uosc.open_type() == MENU then show(title, build(), extra) end
  end
  show(title, build(), extra)
  want_now(channels, render)
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
  if state.current and state.current ~= '' and state.current.kind ~= 'radio' then
    table.insert(items, { title = 'Guía de «' .. state.current.name .. '»', icon = 'event_note',
                          hint = now_title(state.current.id) and ('ahora: ' .. cut(now_title(state.current.id), 30)) or nil,
                          value = { view = 'guide', id = state.current.id, name = state.current.name } })
  end
  if channel_playing() and state.current.kind ~= 'radio' then
    table.insert(items, { title = 'Audio y subtítulos del canal', icon = 'subtitles',
                          hint = #state.badges > 0 and table.concat(state.badges, ' ') or nil,
                          value = { view = 'tracks' } })
  end
  -- H43/B2 · programar una grabación nueva, en el primer nivel: antes solo se llegaba con Tab dentro de la lista
  -- de un canal, y nadie lo encontraba.
  table.insert(items, { title = 'Programar una grabación…', hint = 'TV o radio: canal, inicio y fin',
                        icon = 'add_alarm', value = { view = 'sched_new' } })
  table.insert(items, { title = 'Grabaciones programadas', icon = 'schedule', value = { view = 'schedule' } })
  table.insert(items, { title = 'Actualizar listas', icon = 'refresh', value = { view = 'refresh' } })
  show('TV y radio', items)
end

local SOURCE_TITLES = { tdt_tv = 'España · TV', tdt_radio = 'España · Radio' }

views.source = function(args)
  local title = args.name or SOURCE_TITLES[args.id] or args.id
  if not require_mpvd(title) then return end
  show_loading(title)
  rpc.call('iptv.channels', { source = args.id, compact = true, merge = true, limit = 5000 }, function(err, res)
    if err then show(title, uosc.message_items(fail(err, 'iptv.channels'), 'error')) return end
    if #res.items == 0 then
      show(title, uosc.message_items('Lista vacía o no descargada (Actualizar listas)', 'info'))
      return
    end
    show_channels(title, res.items, function()
      local items = grouped_items(res.items, 'group')
      table.insert(items, 1, search_row({ kind = 'source', id = args.id, name = title }))
      table.insert(items, {
        title = 'Comprobar canales en segundo plano', hint = tostring(#res.items), icon = 'network_check',
        value = { health = args.id }, keep_open = true, actions = {}, separator = true,
      })
      return items
    end)
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
      -- the user's country comes first (mpvd marks it `home`), set apart from the A-Z list
      table.insert(items, { title = ((c.flag or '') ~= '' and (c.flag .. ' ') or '') .. c.name, hint = tostring(c.count),
                            value = { view = 'country', id = c.code, name = c.name }, separator = c.home or nil })
    end
    if #items == 0 then items = uosc.message_items('Sin canales (Actualizar listas)', 'info') end
    show('Mundo · TV', items)
  end, 120)
end

views.country = function(args)
  show_loading(args.name or args.id)
  rpc.call('iptv.channels', { source = 'iptv_org', country = args.id, compact = true, merge = true,
                              limit = opts.world_limit },
    function(err, res)
      if err then show(args.name or args.id, uosc.message_items(fail(err, 'iptv.channels'), 'error')) return end
      show_channels(args.name or args.id, res.items, function()
        local items = grouped_items(res.items, 'category')
        if #res.items > 0 then
          table.insert(items, 1, search_row({ kind = 'country', id = args.id, name = args.name or args.id }))
        end
        return items
      end)
    end, 60)
end

views.radio = function()
  if not require_mpvd('Radio mundial') then return end
  show_loading('Radio mundial')
  rpc.call('radio.countries', nil, function(err, rows)
    if err then show('Radio mundial', uosc.message_items(fail(err, 'radio.countries'), 'error')) return end
    local items = { search_row({ kind = 'radio', name = 'Radio mundial' }),
                    { title = 'Más votadas del mundo', icon = 'trending_up', value = { view = 'radio_top' } } }
    for i, c in ipairs(rows) do
      if i > opts.radio_countries then break end
      table.insert(items, { title = ((c.flag or '') ~= '' and (c.flag .. ' ') or '') .. c.name, hint = tostring(c.count),
                            value = { view = 'radio_country', id = c.code, name = c.name }, separator = c.home or nil })
    end
    show('Radio mundial', items)
  end, 60)
end

local function radio_list(title, params, scope)
  show_loading(title)
  rpc.call('radio.stations', params, function(err, rows)
    if err then show(title, uosc.message_items(fail(err, 'radio.stations'), 'error')) return end
    local items = {}
    if #rows > 0 then table.insert(items, search_row(scope)) end
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
  local title = args.name or args.id
  radio_list(title, { country = args.id, limit = opts.radio_limit, compact = true },
             { kind = 'radio_country', id = args.id, name = title })
end

views.radio_top = function()
  radio_list('Radio · más votadas', { top = 100, compact = true }, { kind = 'radio_top', name = 'Más votadas' })
end

local function simple_list(title, method, params, empty_text, scope)
  if not require_mpvd(title) then return end
  show_loading(title)
  rpc.call(method, params, function(err, rows)
    if err then show(title, uosc.message_items(fail(err, method), 'error')) return end
    show_channels(title, rows, function()
      local items = {}
      if #rows > 0 then table.insert(items, search_row(scope)) end
      for _, ch in ipairs(rows) do table.insert(items, channel_item(ch)) end
      if #items == 0 then items = uosc.message_items(empty_text, 'info') end
      return items
    end)
  end)
end

views.favorites = function()
  simple_list('Favoritos', 'iptv.favorites.list', { compact = true }, 'Sin favoritos: pulsa Tab sobre un canal y elige ★',
              { kind = 'favorites', name = 'Favoritos' })
end

views.recents = function()
  simple_list('Recientes', 'iptv.recents.list', { limit = 30, compact = true }, 'Todavía no has visto nada',
              { kind = 'recents', name = 'Recientes' })
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

-- «Audio y subtítulos del canal»: the channel's tracks with readable names; Enter switches aid/sid.
local ROLE_HINTS = { vo = 'VO', ad = 'AD', sdh = 'para sordos' }

local function track_row(t)
  return {
    title = t.label, icon = t.selected and 'radio_button_checked' or 'radio_button_unchecked', active = t.selected or nil,
    hint = ROLE_HINTS[t.role], keep_open = true, actions = {},
    value = { track = { type = t.type, id = t.id, label = t.label } },
  }
end

views.tracks = function()
  local name = type(state.current) == 'table' and state.current.name or ''
  local title = 'Audio y subtítulos' .. (name ~= '' and (' · ' .. name) or '')
  if not channel_playing() then
    show('Audio y subtítulos', uosc.message_items('Pon un canal de TV para elegir su audio y sus subtítulos', 'info'))
    return
  end
  local view = state.view
  if not state.tracks then show_loading(title) end
  named_tracks(function(list)
    if state.view ~= view then return end
    local audio, subs = {}, {}
    local sub_on = false
    for _, t in ipairs(list) do
      if t.type == 'audio' then table.insert(audio, track_row(t)) end
      if t.type == 'sub' then
        table.insert(subs, track_row(t))
        if t.selected then sub_on = true end
      end
    end
    local items = {}
    table.insert(items, { title = 'Audio', icon = 'graphic_eq', selectable = false, muted = true })
    if #audio == 0 then
      table.insert(items, { title = 'Sin audio', selectable = false, muted = true })
    end
    for _, it in ipairs(audio) do table.insert(items, it) end
    items[#items].separator = true
    table.insert(items, { title = 'Subtítulos', icon = 'subtitles', selectable = false, muted = true })
    table.insert(items, { title = 'Sin subtítulos', icon = sub_on and 'radio_button_unchecked' or 'radio_button_checked',
                          active = (not sub_on) or nil, keep_open = true, actions = {},
                          value = { track = { type = 'sub', id = 'no', label = 'Sin subtítulos' } } })
    for _, it in ipairs(subs) do table.insert(items, it) end
    if #subs == 0 then
      table.insert(items, { title = 'Este canal no trae subtítulos', icon = 'info', selectable = false, muted = true })
    end
    show(title, items, { footnote = 'Pistas del propio canal (sin traducción en directo) · Enter elige · ⌫ atrás' })
  end)
end

local function select_track(t)
  local prop = t.type == 'audio' and 'aid' or 'sid'
  if t.id == 'no' then mp.set_property(prop, 'no') else mp.set_property_native(prop, t.id) end
  osd((t.type == 'audio' and 'Audio: ' or 'Subtítulos: ') .. (t.label or tostring(t.id)))
  -- the selection flags of track-list change a moment later
  mp.add_timeout(0.15, function() if state.view == 'tracks' and uosc.open_type() == MENU then reopen_current() end end)
end

-- ---------------------------------------------------------------------------------------------
-- TV guide and scheduled recordings (mpvd: iptv.epg.*, iptv.schedule.*)

local DAYS = { 'domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado' }

local function day_title(ts)
  local d, today = os.date('*t', ts), os.date('*t')
  local tomorrow = os.date('*t', os.time() + 86400)
  local name = DAYS[d.wday] .. ' ' .. d.day
  if d.year == today.year and d.yday == today.yday then return 'Hoy, ' .. name end
  if d.year == tomorrow.year and d.yday == tomorrow.yday then return 'Mañana, ' .. name end
  return name:gsub('^%l', string.upper)
end

local function minutes(seconds)
  local m = math.floor((seconds + 30) / 60)
  if m < 60 then return m .. ' min' end
  return math.floor(m / 60) .. ' h' .. (m % 60 > 0 and string.format(' %02d', m % 60) or '')
end

-- H57 · qué se hace en la franja: grabarlo, verlo/oírlo, o las dos cosas. Se recuerda, como el formato de grabar.
local MODES = {
  { id = 'record', title = 'Grabarlo', hint = 'queda el archivo', icon = 'fiber_manual_record' },
  { id = 'play', title = 'Ponerlo', hint = 'se enciende y suena a esa hora', icon = 'play_circle' },
  { id = 'both', title = 'Las dos cosas', hint = 'se ve y además queda grabado', icon = 'library_add' },
}
local function mode_label(id)
  for _, m in ipairs(MODES) do if m.id == id then return m.title end end
  return 'Grabarlo'
end

local function schedule_params(channel_id, start, stop, title, programme)
  local p = { channel = channel_id, start = start, stop = stop, title = title, mode = P:get('sched_mode') or 'record' }
  if programme then
    p.programme = programme
    p.margin_before, p.margin_after = opts.epg_margin_before, opts.epg_margin_after
  end
  if opts.schedule_dir ~= '' then p.dir = mp.command_native({ 'expand-path', opts.schedule_dir }) end
  return p
end

local function schedule_add(params, after)
  rpc.call('iptv.schedule.add', params, function(err, rec)
    if err then osd('No se pudo programar: ' .. fail(err, 'iptv.schedule.add')) return end
    local verbo = (rec.mode == 'play' and '▶ Programado') or (rec.mode == 'both' and '⏺▶ Programado')
      or '⏺ Grabación programada'
    osd(verbo .. ': ' .. (rec.title or '') .. ' · ' .. (rec.label or ''))
    if after then after(rec) end
  end)
end

views.guide = function(args, tries)
  local title = 'Guía · ' .. (args.name or '')
  if not require_mpvd(title) then return end
  show_loading(title)
  local view = state.view
  tries = (tries or 0) + 1
  rpc.call('iptv.epg.channel', { id = args.id, hours = opts.epg_hours }, function(err, res)
    if state.view ~= view then return end
    if err then show(title, uosc.message_items(fail(err, 'iptv.epg.channel'), 'error')) return end
    local name = res.channel and res.channel.name or args.name or ''
    title = 'Guía · ' .. name
    local progs = res.programmes or {}
    state.guide = { id = args.id, programmes = #progs, epg_id = res.epg_id or '' }
    publish()
    if #progs == 0 then
      local text = (res.loading and 'Descargando la guía…')
        or (not res.has_guide and 'Esta lista no trae guía de programación')
        or (not res.epg_id and 'La guía no incluye este canal') or 'Sin programas en las próximas horas'
      show(title, {
        { title = text, icon = res.loading and 'spinner' or 'info', selectable = false, muted = true, align = 'center' },
        { title = 'Ver el canal', icon = 'live_tv', value = { play = args.id } },
      })
      -- first download of the guide: look again in a moment, but not for ever (the server may be down)
      if res.loading and tries < 20 then
        mp.add_timeout(2, function()
          if state.view == view then views.guide(args, tries) end
        end)
      end
      return
    end
    local items, day, now = {}, nil, os.time()
    for i, p in ipairs(progs) do
      local d = os.date('%Y%m%d', p.start)
      if d ~= day and i > 1 then
        table.insert(items, { title = day_title(p.start), icon = 'calendar_today', selectable = false, muted = true })
      end
      day = d
      local hint
      if p.now then
        hint = 'ahora · quedan ' .. minutes(p.stop - now)
      elseif i == 2 and progs[1].now then
        hint = 'después'
      else
        hint = minutes(p.stop - p.start)
      end
      if p.scheduled then hint = '⏺ ' .. hint end
      local rec = { rec_prog = { channel = args.id, start = p.start, stop = p.stop, title = p.title } }
      local sub = {}
      if p.desc and p.desc ~= '' then
        table.insert(sub, { title = cut(p.desc, 160), selectable = false, muted = true, icon = 'notes' })
      end
      if p.scheduled then
        table.insert(sub, { title = 'Ya está programada', icon = 'check', selectable = false, muted = true })
        table.insert(sub, { title = 'Grabaciones programadas', icon = 'schedule', value = { view = 'schedule' } })
      elseif p.now then
        table.insert(sub, { title = 'Grabar lo que queda', icon = 'fiber_manual_record', value = rec })
      else
        table.insert(sub, { title = 'Grabar este programa', icon = 'fiber_manual_record', value = rec })
      end
      table.insert(sub, { title = p.now and 'Ver ahora' or 'Ver el canal ahora', icon = 'live_tv',
                          value = { play = args.id } })
      -- `id` propio: dos programas con el mismo título (una reposición, «Cine») chocarían (ver ADR-079)
      table.insert(items, { title = os.date('%H:%M', p.start) .. '  ' .. p.title, hint = hint, items = sub,
                            id = 'prog:' .. tostring(p.start),
                            bold = p.now or nil, icon = p.now and 'play_arrow' or nil })
    end
    show(title, items, { footnote = 'Enter abre el programa (grabar, ver) · ⌫ atrás' })
  end, 30)
end

local STATUS_ICONS = { scheduled = 'schedule', recording = 'fiber_manual_record', done = 'check_circle',
                       failed = 'error', missed = 'event_busy', cancelled = 'block' }

-- H40/F1 · lo que se aplica a las grabaciones nuevas: despertar el equipo antes y qué hacer al terminar. Lo guarda
-- mpvd (sobrevive a cerrar el reproductor, que es justo cuando hace falta), y si el equipo no puede poner el
-- despertador se dice qué falta en vez de ofrecer algo que no va a pasar.
local AFTER_LABEL = { nothing = 'nada', suspend = 'suspender el equipo', shutdown = 'apagar el equipo' }
local AFTER_NEXT = { nothing = 'suspend', suspend = 'shutdown', shutdown = 'nothing' }

local function power_rows(d, out)
  local p = (d and d.power) or {}
  local wake_hint
  if d and d.wake then wake_hint = 'sí' else wake_hint = 'no' end
  if p.can_wake == false then wake_hint = wake_hint .. ' · ' .. (p.reason or 'este equipo no puede') end
  out[#out + 1] = { title = 'Despertar el equipo 5 min antes', hint = wake_hint, icon = 'alarm',
                    active = (d and d.wake) or false, value = { sched_pref = 'wake' } }
  local after = (d and d.after) or 'nothing'
  local hint = AFTER_LABEL[after] or after
  if after == 'suspend' and p.can_suspend == false then hint = hint .. ' · este equipo no deja suspender' end
  if after == 'shutdown' and p.can_shutdown == false then hint = hint .. ' · este equipo no deja apagar' end
  out[#out + 1] = { title = 'Al terminar la grabación', hint = hint, icon = 'bedtime',
                    active = after ~= 'nothing', value = { sched_pref = 'after' } }
  if p.install_hint and p.install_hint ~= '' and d and d.wake then
    out[#out + 1] = { title = 'Para el despertador hace falta una orden con sudo, una sola vez',
                      hint = 'está en NEEDS_HUMAN.md', icon = 'info', selectable = false, muted = true }
  end
end

views.schedule = function(args)
  local title = 'Grabaciones programadas'
  if not require_mpvd(title) then return end
  -- Un refresco (una grabación que empieza o termina mientras se mira la lista) no debe dejarla en «Cargando…»:
  -- parpadea, y además encoger a dos filas un menú que uosc tiene abierto le hace perder el alto de sus submenús
  -- (`Menu:set_scroll_to` acaba con `scroll_height` nil y uosc revienta). Se refresca con la lista puesta.
  if not (args and args.refresh) then show_loading(title) end
  local view = state.view
  rpc.call('iptv.schedule.defaults', nil, function(derr, defaults)
    if not derr and type(defaults) == 'table' then state.sched_defaults = defaults end
  end, 10)
  rpc.call('iptv.schedule.list', nil, function(err, res)
    if state.view ~= view then return end
    if err then show(title, uosc.message_items(fail(err, 'iptv.schedule.list'), 'error')) return end
    -- H57 · lo mismo sirve para grabar y para que SUENE: el modo es una fila y se recuerda
    local modo = P:get('sched_mode') or 'record'
    local items = {
      { title = 'Programar una franja…', hint = 'canal, inicio y fin', icon = 'add', value = { view = 'sched_new' } },
      { title = 'Qué hacer en esa franja', icon = 'tune', hint = mode_label(modo), value = { view = 'sched_mode' } },
    }
    local list = res.items or {}
    for i, r in ipairs(list) do
      local sub = {}
      if r.message and r.message ~= '' then
        table.insert(sub, { title = cut(r.message, 120), icon = 'info', selectable = false, muted = true })
      end
      if r.status == 'scheduled' then
        table.insert(sub, { title = 'Cancelar grabación', icon = 'block', value = { sched_cancel = r.id } })
      elseif r.status == 'recording' then
        table.insert(sub, { title = 'Detener grabación', icon = 'stop_circle', value = { sched_cancel = r.id } })
      else
        if r.file and r.file ~= '' then
          table.insert(sub, { title = 'Reproducir', icon = 'play_arrow', value = { open_file = r.file } })
        end
        table.insert(sub, { title = 'Quitar de la lista', hint = 'el archivo se queda', icon = 'delete',
                            value = { sched_remove = r.id } })
      end
      local ch = r.channel and r.channel.name or ''
      local what = (r.title and r.title ~= ch) and (ch .. ' · ' .. r.title) or ch
      table.insert(items, {
        title = what, hint = (r.label or '') .. ' · ' .. (r.status_label or r.status), icon = STATUS_ICONS[r.status],
        items = sub, id = 'sched:' .. tostring(r.id),
        separator = (i == #list) or nil, muted = (r.status == 'cancelled' or r.status == 'missed') or nil,
        bold = r.status == 'recording' or nil,
      })
    end
    if #list == 0 then
      table.insert(items, { title = 'No hay grabaciones: prográmalas desde la guía de un canal (Tab › Guía)',
                            icon = 'info', selectable = false, muted = true })
    end
    power_rows(state.sched_defaults, items)
    table.insert(items, { title = 'Carpeta: ' .. (opts.schedule_dir ~= '' and opts.schedule_dir or res.dir or ''),
                          icon = 'folder', selectable = false, muted = true })
    show(title, items, { footnote = 'Se graba aunque veas otra cosa o cierres el reproductor (con el equipo encendido)' })
  end)
end

-- «Programar grabación…»: pick the channel (the one playing, favourites, recents; any other: Tab › Programar
-- grabación… in its list), then type the times.
views.sched_mode = function()
  local cur = P:get('sched_mode') or 'record'
  local items = {}
  for _, m in ipairs(MODES) do
    items[#items + 1] = { title = m.title, hint = m.hint, icon = m.id == cur and 'radio_button_checked'
                          or 'radio_button_unchecked', active = m.id == cur, value = { sched_mode = m.id } }
  end
  items[#items + 1] = { title = 'Ponerlo enciende el reproductor a esa hora, aunque esté cerrado', icon = 'info',
                        muted = true, selectable = false, separator = true,
                        hint = 'con el despertador, también si el equipo está suspendido' }
  show('Qué hacer en esa franja', items)
end

views.sched_new = function()
  local title = 'Programar grabación'
  if not require_mpvd(title) then return end
  show_loading(title)
  local view = state.view
  local items, seen, pending = {}, {}, 2
  local function add(ch, hint)
    -- H43/B3 · la radio también: mpvd la graba en .mka sin problema, era este `kind ~= 'radio'` el que la excluía
    if ch and ch.id and not seen[ch.id] then
      seen[ch.id] = true
      table.insert(items, { title = ch.name, hint = hint, icon = ch.kind == 'radio' and 'radio' or 'live_tv',
                            value = { view = 'sched_time', id = ch.id, name = ch.name } })
    end
  end
  if state.current and state.current ~= '' then add(state.current, 'viendo ahora') end
  local favs, recents = {}, {}
  local function done()
    pending = pending - 1
    if pending > 0 or state.view ~= view then return end
    for _, ch in ipairs(favs) do add(ch, '★') end
    for _, ch in ipairs(recents) do add(ch, 'reciente') end
    if #items == 0 then
      items = uosc.message_items('Elige el canal en su lista: Tab › Programar grabación…', 'info')
    end
    show(title, items, { footnote = 'Otro canal: en su lista, Tab › Programar grabación…' })
  end
  rpc.call('iptv.favorites.list', { compact = true }, function(err, rows) if not err then favs = rows end done() end)
  rpc.call('iptv.recents.list', { limit = 15, compact = true }, function(err, rows)
    if not err then recents = rows end
    done()
  end)
end

local function sched_time_menu(args, items, query)
  return {
    type = MENU, title = 'Grabar «' .. (args.name or '') .. '»: inicio y fin o minutos (21:30 22:15 · 21:30 90 · ahora 30)',
    items = items, callback = { SCRIPT, EVENT }, search_style = 'palette', search_debounce = 250,
    on_search = 'callback', on_close = 'callback', search_suggestion = query,
    footnote = 'También «mañana 9:00 1h30» · Enter programa · ⌫ atrás',
  }
end

views.sched_time = function(args)
  local items = uosc.message_items('Escribe la hora de inicio y la de fin (o los minutos)', 'schedule')
  publish_menu('sched_time', items)
  uosc.open(sched_time_menu(args, items))
end

local sched_seq = 0
local function sched_time_prompt(args, query)
  sched_seq = sched_seq + 1
  local seq = sched_seq
  if (query or '') == '' then
    uosc.update(sched_time_menu(args, uosc.message_items('Escribe la hora de inicio y la de fin (o los minutos)',
                                                         'schedule'), query))
    return
  end
  rpc.call('iptv.schedule.parse', { text = query }, function(err, res)
    if seq ~= sched_seq then return end
    local items
    if err then
      items = uosc.message_items(fail(err, 'iptv.schedule.parse'), 'error')
    elseif res.error then
      items = uosc.message_items(res.error, 'help')
    else
      items = { { title = 'Grabar «' .. (args.name or '') .. '» ' .. res.label, icon = 'fiber_manual_record',
                  value = { sched_add = { channel = args.id, start = res.start, stop = res.stop, title = args.name } } } }
    end
    publish_menu('sched_time', items)
    uosc.update(sched_time_menu(args, items, query))
  end)
end

local function sched_cancel(id, remove)
  local method = remove and 'iptv.schedule.remove' or 'iptv.schedule.cancel'
  rpc.call(method, { id = id }, function(err)
    if err then osd('Grabaciones: ' .. fail(err, method)) return end
    osd(remove and 'Quitada de la lista' or 'Grabación cancelada')
    if uosc.open_type() == MENU then reopen_current() end
  end, 40)
end

-- mpvd tells every player when a scheduled recording starts and ends (mpvd/iptv/schedule.py).
mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  if ev.event == 'schedule' and type(ev.item) == 'table' then
    local r = ev.item
    local name = r.channel and r.channel.name or ''
    local what = (r.title and r.title ~= name) and ('«' .. r.title .. '» (' .. name .. ')') or name
    local text
    if r.status == 'recording' then text = '⏺ Empieza la grabación programada: ' .. what
    elseif r.status == 'done' then text = '✔ Grabación terminada: ' .. what
    elseif r.status == 'failed' then
      text = '✕ Falló la grabación de ' .. what .. ((r.message or '') ~= '' and (': ' .. r.message) or '')
    elseif r.status == 'missed' then text = 'Grabación perdida: ' .. what
    end
    state.schedule_event = { id = r.id, status = r.status, text = text or '', file = r.file or '' }
    publish()
    if text then mp.osd_message(text, math.max(opts.osd_seconds, 5)) end
    if state.view == 'schedule' and uosc.open_type() == MENU then
      open_view({ name = 'schedule', args = { refresh = true } }, false)
    end
  elseif ev.event == 'epg' then
    -- a guide was just (re)loaded: forget the «nothing on» answers so the next list asks again
    for id, e in pairs(epg_cache) do if not e.title then epg_cache[id] = nil end end
  end
end)

-- Search palette (its own type so the search event is unambiguous)
local search_seq = 0

-- `scope` (optional): «Buscar en esta lista» of one list ({kind, id, name}, see search_row).
local function search_menu(items, query, scope)
  return {
    type = SEARCH_MENU, title = scope and ('Buscar en «' .. (scope.name or '') .. '»') or 'Buscar canal o emisora',
    items = items, callback = { SCRIPT, EVENT },
    search_style = 'palette', search_debounce = 300, on_search = 'callback', on_close = 'callback',
    item_actions = ACTIONS, search_suggestion = query,
    footnote = scope and 'Solo los canales de esta lista · Tab acciones · ⌫ vuelve a la lista'
      or 'Busca en España, iptv-org y Radio Browser · Tab acciones',
  }
end

views.search = function()
  if not rpc.connected() then require_mpvd('Buscar') return end
  state.search_scope = ''
  publish()
  uosc.open(search_menu(uosc.message_items('Escribe para buscar (sin acentos vale)', 'search')))
end

views.scoped_search = function(args)
  local scope = args.scope or {}
  if not rpc.connected() then require_mpvd('Buscar') return end
  state.search_scope = (scope.kind or '') .. (scope.id and (':' .. scope.id) or '')
  state.search_results = 0
  publish()
  uosc.open(search_menu(uosc.message_items('Escribe para buscar en esta lista (sin acentos vale)', 'search'), nil,
                        scope))
end

-- iptv.search parameters of each kind of list (mpvd/iptv/service.py: filters of the catalogue or a scope).
local function scope_params(scope)
  local k = scope.kind
  if k == 'source' then return { source = scope.id, merge = true } end
  if k == 'country' then return { source = 'iptv_org', country = scope.id, merge = true } end
  if k == 'favorites' or k == 'recents' then return { scope = k } end
  if k == 'radio_country' then return { scope = 'radio', country = scope.id, pool = opts.radio_limit } end
  if k == 'radio_top' then return { scope = 'radio_top', pool = 100 } end
  return { scope = 'radio' }
end

local function render_results(seq, query, results, scope)
  state.search_results = #results
  publish()
  local function render(first)
    if not first and (seq ~= search_seq or uosc.open_type() ~= SEARCH_MENU) then return end
    local items = {}
    for _, ch in ipairs(results) do
      local it = channel_item(ch)
      local where = (ch.source == 'radio_browser' and (scope and (ch.category or '') or 'radio mundial'))
        or (ch.source == 'iptv_org' and (scope and (ch.category_label or ch.category or '')
                                         or ('iptv-org · ' .. (ch.country or ''):upper())))
        or (ch.group_label or ch.group or ch.source)
      if where ~= '' then it.hint = it.hint and (where .. ' · ' .. it.hint) or where end
      table.insert(items, it)
    end
    if #items == 0 then items = uosc.message_items('Sin resultados para «' .. query .. '»', 'search_off') end
    publish_menu('Buscar: ' .. query, items)
    uosc.update(search_menu(items, query, scope))
  end
  render(true)
  want_now(results, function() render(false) end)
end

local function run_search(query, scope)
  search_seq = search_seq + 1
  local seq = search_seq
  if query == '' then
    uosc.update(search_menu(uosc.message_items('Escribe para buscar', 'search'), query, scope))
    return
  end
  if scope then
    local params = scope_params(scope)
    params.q, params.limit, params.compact = query, opts.search_limit, true
    rpc.call('iptv.search', params, function(err, rows)
      if seq ~= search_seq then return end
      if err then
        uosc.update(search_menu(uosc.message_items(fail(err, 'iptv.search'), 'error'), query, scope))
        return
      end
      render_results(seq, query, rows, scope)
    end, 30)
    return
  end
  local results, done = {}, 0
  local function finish()
    done = done + 1
    if done < 2 or seq ~= search_seq then return end
    render_results(seq, query, results)
  end
  rpc.call('iptv.search', { q = query, limit = opts.search_limit, compact = true, merge = true }, function(err, rows)
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
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.health then
      health_check(v.health)
    elseif v.add_url then
      add_list(v.add_url)
    elseif v.view and ev.action == 'remove' and v.view == 'source' then
      remove_list(v.id)
    elseif v.rec_prog then
      local p = v.rec_prog
      schedule_add(schedule_params(p.channel, p.start, p.stop, p.title,
                                   { title = p.title, start = p.start, stop = p.stop }),
                   function() if uosc.open_type() == MENU then reopen_current() end end)
    elseif v.sched_add then
      local p = v.sched_add
      schedule_add(schedule_params(p.channel, p.start, p.stop, p.title), function()
        state.stack = { { name = 'root' } }
        state.force_open = true  -- the palette is replaced by the list
        open_view({ name = 'schedule' })
      end)
    elseif v.sched_mode then
      P:set('sched_mode', v.sched_mode)
      table.remove(state.stack)
      state.view = 'schedule'
      reopen_current()
    elseif v.sched_pref then
      -- H40/F1: lo guarda mpvd, así que vale también con el reproductor cerrado
      local d = state.sched_defaults or { wake = false, after = 'nothing' }
      local params = {}
      if v.sched_pref == 'wake' then params.wake = not d.wake
      else params.after = AFTER_NEXT[d.after or 'nothing'] or 'nothing' end
      rpc.call('iptv.schedule.defaults', params, function(err, res)
        if err then osd('No se pudo guardar: ' .. fail(err, 'iptv.schedule.defaults')) return end
        state.sched_defaults = res
        publish()
        reopen_current()
      end, 10)
    elseif v.sched_cancel then
      sched_cancel(v.sched_cancel, false)
    elseif v.sched_remove then
      sched_cancel(v.sched_remove, true)
    elseif v.open_file then
      mp.commandv('loadfile', v.open_file, 'replace')
      uosc.close(MENU)
    elseif v.track then
      select_track(v.track)
    elseif v.play then
      if ev.action == 'fav' then toggle_favorite(v.play)
      elseif ev.action == 'copy' then copy_channel(v.play)
      elseif ev.action == 'guide' then
        open_view({ name = 'guide', args = { id = v.play, name = v.name } })
      elseif ev.action == 'schedule' then
        open_view({ name = 'sched_time', args = { id = v.play, name = v.name } })
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
    local top = state.stack[#state.stack]
    if state.view == 'add_list' then add_list_prompt(ev.query or '')
    elseif top and top.name == 'sched_time' then sched_time_prompt(top.args or {}, ev.query or '')
    elseif top and top.name == 'scoped_search' then run_search(ev.query or '', (top.args or {}).scope or {})
    else run_search(ev.query or '') end
  elseif ev.type == 'paste' then
    if state.view == 'add_list' then add_list(ev.value or '') end
  elseif ev.type == 'back' then
    local from = table.remove(state.stack)
    if from and from.name == 'sched_time' then state.force_open = true end  -- leaving the palette
    if #state.stack == 0 then
      -- the search palette closes (⌫ there is mostly "delete text"); menus return to their opener
      if (from and from.name == 'search') or not N:leave() then uosc.close(MENU); uosc.close(SEARCH_MENU) end
    else
      reopen_current()
    end
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

N:binding('tv-menu', open_root)
mp.register_script_message('mu-iptv-play', function(channel_id) if channel_id and channel_id ~= '' then play(channel_id) end end)
N:binding('tv-search', function()
  state.stack = {}
  open_view({ name = 'search' })
end)
-- Guide of the channel being watched (or the channel menu when none is), and the scheduled recordings.
N:binding('tv-guide', function()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  if state.current and state.current ~= '' and state.current.kind ~= 'radio' then
    open_view({ name = 'guide', args = { id = state.current.id, name = state.current.name } })
  else
    osd('Elige un canal: Tab › Guía de programación')
    open_view({ name = 'root' })
  end
end)
-- Audio and subtitles of the channel being watched (its own tracks with readable names).
N:binding('tv-tracks', function()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'tracks' })
end)
N:binding('tv-schedule', function()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'schedule' })
end)
-- H43/B2 · entrada para «Programar una grabación…» desde el menú de Grabar (mu-record la abre como hija)
N:binding('tv-schedule-new', function()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'sched_new' })
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
