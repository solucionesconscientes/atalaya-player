-- mu-lyrics: song lyrics and «¿Qué canción es?» (H32, ADR-065).
--   · When an audio file loads, mpvd (lyrics.get) looks for its lyrics: a .lrc next to it, the lyrics tag inside the
--     file or, only if the user turned it on, LRCLIB. Synced lyrics come as an SRT in the cache and are added as a
--     subtitle track titled «Letra» (hidden/shown with v, moved with the subtitle delay). A .lrc that mpv already
--     loaded by itself (sub-auto) is not added twice.
--   · Cover art needs nothing here: mpv already shows cover.jpg/folder.jpg/front.png… next to the song when the file
--     has no picture inside (cover-art-auto=exact + cover-art-whitelist, both mpv defaults).
--   · Menu «Letra»: the lines with their times (Enter jumps), show/hide, the LRCLIB switch and song identification
--     (Chromaprint + AcoustID with the user's own key; off by default) with «Guardar en el archivo».
-- Binding: lyrics-menu. Script name: mu_lyrics. State: user-data/mu/lyrics. Preference (mu-prefs): auto.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')
local nav = require('mu.nav')
local tr = require('mu.i18n').t
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-lyrics'
local EVENT = 'mu-lyrics-event'
local INPUT = 'mu-lyrics-input'
local INPUT_EVENT = 'mu-lyrics-input-event'
local ROOT_TITLE = 'Letra'
local TRACK_TITLE = 'Letra'

local P = prefs.ns('mu-lyrics', { auto = true })

local state = { status = 'idle', lyrics = nil, sid = nil, settings = nil, candidates = nil, view = '', stack = {},
                items = {}, force_open = false, last_error = '', tagged = nil, input = false }

local function osd(text, secs) mp.osd_message(text, secs or 3) end

local function clock(t)
  t = math.max(0, math.floor(tonumber(t) or 0))
  return string.format('%d:%02d', math.floor(t / 60), t % 60)
end

local function publish()
  local l = state.lyrics
  mp.set_property_native('user-data/mu/lyrics', {
    status = state.status, found = l and l.found or false, source = l and l.source or '', synced = l and l.synced or false,
    lines = l and #(l.lines or {}) or 0, srt = l and l.srt or '', sid = state.sid, view = state.view,
    depth = #state.stack, items = state.items, settings = state.settings, last_error = state.last_error,
    candidates = state.candidates and #state.candidates or 0, tagged = state.tagged, auto = P:get('auto'),
  })
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

local function is_url(p) return type(p) == 'string' and p:find('^%a[%w+.-]*://') ~= nil and not p:find('^file://') end

local function abs_path()
  local path = mp.get_property('path')
  if not path or path == '' then return nil end
  if is_url(path) then return path end
  return mp.command_native({ 'normalize-path', path }) or path
end

local function audio_only()
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'video' and not t.albumart and not t.image then return false end
  end
  return true
end

-- ---------------------------------------------------------------------------------------------
-- loading the lyrics

local function find_track(file)
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'sub' and t.external and t['external-filename'] == file then return t end
  end
  return nil
end

local function sub_selected()
  local s = mp.get_property('sid')
  return s ~= nil and s ~= 'no' and s ~= 'auto'
end

local function attach(res)
  state.sid = nil
  if not res.synced or not res.srt then return end
  local t = res.sidecar and find_track(res.sidecar) or find_track(res.srt)
  if t then state.sid = t.id return end      -- mpv already loaded the .lrc itself (sub-auto)
  mp.commandv('sub-add', res.srt, sub_selected() and 'auto' or 'select', TRACK_TITLE)
  t = find_track(res.srt)
  state.sid = t and t.id or nil
end

local reopen_current
local function fetch(online)
  local path = abs_path()
  if not path or not rpc.connected() then return end
  local meta = mp.get_property_native('metadata') or {}
  local function tag(k) return meta[k] or meta[k:upper()] or meta[k:sub(1, 1):upper() .. k:sub(2)] or '' end
  local params = { path = path, artist = tag('artist'), title = tag('title'), album = tag('album'),
                   duration = mp.get_property_number('duration') }
  if online ~= nil then params.online = online end
  if is_url(path) then params.path = '' end
  if params.path == '' and (params.artist == '' or params.title == '') then return end
  state.status = 'loading'
  publish()
  rpc.call('lyrics.get', params, function(err, res)
    if abs_path() ~= path then return end
    if err then state.status = 'error'; fail(err, 'lyrics.get') return end
    state.lyrics = res
    state.status = res.found and 'found' or 'none'
    attach(res)
    publish()
    if uosc.open_type() == MENU and state.view == 'root' then reopen_current() end
  end, 30)
end

local checked = nil
local function on_file()
  state.lyrics, state.sid, state.candidates, state.tagged, state.status = nil, nil, nil, nil, 'idle'
  publish()
  if not P:get('auto') or not audio_only() or not rpc.connected() then return end
  checked = abs_path()
  fetch(nil)
end

mp.register_event('file-loaded', on_file)
mp.register_event('start-file', function() checked = nil end)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if type(core) == 'table' and core.mpvd == 'connected' and not checked
      and mp.get_property_native('idle-active') == false and mp.get_property_number('duration') then
    on_file()
  end
end)

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 400 then break end
    table.insert(out, { title = it.title or '', hint = it.hint or '', value = it.value or '', active = it.active })
  end
  state.items = out
end

local function base_menu(title, items, extra)
  local menu = { type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, keep_open = true,
                 search_submenus = false }
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

reopen_current = function(force)
  local spec = state.stack[#state.stack]
  if spec then
    state.force_open = force or false
    open_view(spec, false)
  end
end

local function toggle(title, on, icon, value, extra)
  local it = { title = title, hint = on and 'sí' or 'no', icon = icon, active = on, value = value }
  for k, v in pairs(extra or {}) do it[k] = v end
  return it
end

local function settings_items(items)
  local s = state.settings or {}
  table.insert(items, toggle('Letras al abrir una canción', P:get('auto'), 'lyrics', { auto = true }))
  table.insert(items, toggle('Buscar letras en internet (LRCLIB)', s.lyrics_online == true, 'travel_explore',
                             { setting = 'lyrics_online' }))
  table.insert(items, toggle('Identificar canciones (AcoustID)', s.songid_enabled == true, 'fingerprint',
                             { setting = 'songid_enabled' }))
  table.insert(items, { title = tr('Tu clave de AcoustID…'), icon = 'key', hint = s.has_acoustid_key and 'guardada' or 'falta',
                        value = { key = true } })
end

local function lyrics_items()
  local items = {}
  local l = state.lyrics
  if state.status == 'loading' then return uosc.loading_items(tr('Buscando la letra…')) end
  if not l or not l.found then
    local why = mp.get_property_native('idle-active') and 'Abre una canción primero'
      or (audio_only() and 'Esta canción no tiene letra (ni .lrc al lado ni en sus etiquetas)'
        or 'La letra es para canciones y audios')
    return uosc.message_items(why, 'info')
  end
  local pos = mp.get_property_number('time-pos') or 0
  local cur = nil
  if l.synced then
    for i, line in ipairs(l.lines or {}) do if line.time <= pos + 0.05 then cur = i end end
    for i, line in ipairs(l.lines or {}) do
      table.insert(items, { title = line.text, hint = clock(line.time), active = i == cur, value = { seek = line.time } })
    end
  else
    for text in (tostring(l.plain or '') .. '\n'):gmatch('([^\n]*)\n') do
      if text ~= '' then table.insert(items, { title = text, selectable = false, muted = true }) end
    end
  end
  return items, cur
end

views.root = function()
  local items, cur = lyrics_items()
  local l = state.lyrics
  local head = {}
  if l and l.found and l.synced and state.sid then
    local on = tostring(mp.get_property('sid')) == tostring(state.sid) and mp.get_property_native('sub-visibility')
    table.insert(head, toggle('Mostrar la letra en pantalla', on, 'subtitles', { show = true }))
  end
  local tail = {}
  local path = abs_path()
  if path and not is_url(path) and audio_only() then
    table.insert(tail, { title = tr('¿Qué canción es?'), icon = 'fingerprint', value = { view = 'identify' },
                         hint = (state.settings and state.settings.songid_active) and 'AcoustID' or 'desactivado' })
  end
  table.insert(tail, { title = tr('Ajustes de letras y canciones'), icon = 'settings', value = { view = 'settings' } })
  local all = {}
  for _, it in ipairs(head) do table.insert(all, it) end
  if #head > 0 then all[#all].separator = true end
  for _, it in ipairs(items) do table.insert(all, it) end
  if #all > 0 then all[#all].separator = true end
  for _, it in ipairs(tail) do table.insert(all, it) end
  local source = l and l.found and ({ lrc = 'archivo .lrc', embedded = 'etiqueta del archivo',
                                      lrclib = 'LRCLIB' })[l.source] or nil
  show(ROOT_TITLE, all, { footnote = source and ('Letra de: ' .. source .. ' · Enter salta a la línea') or nil,
                          selected_index = cur and (cur + #head) or nil })
  if not state.settings and rpc.connected() then
    rpc.call('lyrics.settings.get', nil, function(err, s)
      if not err then state.settings = s; publish(); if state.view == 'root' then reopen_current() end end
    end, 10)
  end
end

views.settings = function()
  local title = tr('Ajustes de letras y canciones')
  if not rpc.connected() then show(title, uosc.message_items(tr('mpvd no está conectado'), 'error')) return end
  rpc.call('lyrics.settings.get', nil, function(err, s)
    if state.view ~= 'settings' then return end
    if err then show(title, uosc.message_items(fail(err, 'lyrics.settings.get'), 'error')) return end
    state.settings = s
    local items = {}
    settings_items(items)
    show(title, items, { footnote = tr('Todo lo de internet está apagado hasta que lo enciendas') })
  end, 10)
end

views.identify = function()
  local title = tr('¿Qué canción es?')
  local path = abs_path()
  if not path or is_url(path) then show(title, uosc.message_items(tr('Solo con archivos del equipo'), 'info')) return end
  if not rpc.connected() then show(title, uosc.message_items(tr('mpvd no está conectado'), 'error')) return end
  local s = state.settings or {}
  if not s.songid_active then
    local items = uosc.message_items(s.songid_enabled and 'Falta tu clave de AcoustID (gratis en acoustid.org)'
      or 'Está desactivado: usa la huella del audio y consulta AcoustID por internet', 'info')
    settings_items(items)
    show(title, items)
    return
  end
  show(title, uosc.loading_items(tr('Escuchando la canción…')))
  rpc.call('songid.identify', { path = path }, function(err, res)
    if state.view ~= 'identify' then return end
    if err then show(title, uosc.message_items(fail(err, 'songid.identify'), 'error')) return end
    state.candidates = res.candidates or {}
    local items = {}
    for i, c in ipairs(state.candidates) do
      local sub = c.artist .. ((c.album and c.album ~= '') and (' · ' .. c.album) or '')
      table.insert(items, { title = c.title .. ' — ' .. sub, hint = string.format('%d %%', math.floor(c.score * 100 + 0.5)),
                            icon = 'music_note', value = { candidate = i },
                            actions = { { name = 'tag', icon = 'save', label = tr('Guardar en el archivo') } } })
    end
    if #items == 0 then items = uosc.message_items(tr('No se ha encontrado esta canción'), 'info') end
    publish()
    show(title, items, { footnote = tr('Tab: guardar título, artista y álbum en el archivo') })
  end, 90)
end

-- ---------------------------------------------------------------------------------------------
-- text box for the AcoustID key

local function input_menu(query)
  local items = {}
  if query ~= '' then
    table.insert(items, { title = tr('Guardar la clave'), icon = 'check', value = { save = query } })
  else
    table.insert(items, { title = tr('Borrar la clave guardada'), icon = 'delete', value = { save = '' } })
  end
  return { type = INPUT, title = tr('Clave de aplicación de AcoustID'), items = items, callback = { SCRIPT, INPUT_EVENT },
           search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
           footnote = tr('Pídela gratis en acoustid.org/new-application · se guarda solo en este equipo') }
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if not state.input then return end
  if ev.type == 'search' then
    uosc.update(input_menu(ev.query or ''))
  elseif ev.type == 'back' or ev.type == 'close' then
    state.input = false
    uosc.close(INPUT)
    if ev.type == 'back' then reopen_current(true) end
  elseif ev.type == 'activate' and type(ev.value) == 'table' and ev.value.save ~= nil then
    rpc.call('lyrics.settings.set', { acoustid_key = ev.value.save }, function(err, s)
      if err then osd(tr('Clave: %s'):format(fail(err, 'lyrics.settings.set'))) return end
      state.settings = s
      state.input = false
      uosc.close(INPUT)
      osd(ev.value.save ~= '' and '🔑 Clave guardada' or '🔑 Clave borrada')
      reopen_current(true)
    end, 10)
  end
end)

-- ---------------------------------------------------------------------------------------------
-- events

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.view then
      open_view({ name = v.view, args = v })
    elseif v.seek then
      mp.commandv('seek', tostring(v.seek), 'absolute+exact')
      reopen_current()
    elseif v.show then
      local on = tostring(mp.get_property('sid')) == tostring(state.sid) and mp.get_property_native('sub-visibility')
      if on then
        mp.set_property('sid', 'no')
      else
        mp.set_property('sid', tostring(state.sid))
        mp.set_property_native('sub-visibility', true)
      end
      reopen_current()
    elseif v.auto then
      P:set('auto', not P:get('auto'))
      if P:get('auto') and not state.lyrics then on_file() end
      reopen_current()
    elseif v.setting then
      local s = state.settings or {}
      rpc.call('lyrics.settings.set', { [v.setting] = not s[v.setting] }, function(err, ns)
        if err then osd(fail(err, 'lyrics.settings.set')) return end
        state.settings = ns
        if v.setting == 'lyrics_online' and ns.lyrics_online and not (state.lyrics and state.lyrics.found) then
          fetch(true)
        end
        reopen_current()
      end, 10)
    elseif v.key then
      state.input = true
      uosc.open(input_menu(''))
    elseif v.candidate then
      local c = state.candidates and state.candidates[v.candidate]
      if not c then return end
      if ev.action == 'tag' then
        local path = abs_path()
        rpc.call('songid.tag', { path = path, title = c.title, artist = c.artist, album = c.album }, function(err, res)
          if err then osd(tr('Guardar: %s'):format(fail(err, 'songid.tag')), 5) return end
          state.tagged = res.tags
          publish()
          osd(tr('🏷 Guardado en el archivo: %s — %s'):format(c.title, c.artist), 4)
        end, 120)
      else
        osd('🎵 ' .. c.title .. ' — ' .. c.artist .. ((c.album ~= '') and (' · ' .. c.album) or ''), 5)
      end
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
    if #state.stack > 0 or state.view ~= '' then
      state.stack, state.view, state.input = {}, '', false
      publish()
    end
  end)
end)

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

N:binding('lyrics-menu', open_root)
mp.register_script_message('mu-lyrics-refresh', function() on_file() end)

publish()
msg.info('mu-lyrics loaded')
