-- mu-music: «Música» (H32, ADR-064). Menu over mpvd's music.* (folders scanned in the background, the user's Music
-- folder by default): Artistas › Álbumes › Pistas, Álbumes, Géneros, Buscar, Listas (M3U8 in the user's data, smart
-- lists), Cola (the mpv playlist after the current track: move with ctrl+↑/↓ or Tab actions, remove, save as a list),
-- Historial (local, never sent anywhere), Carpetas and Ajustes.
-- Playback: «Reproducir a continuación» inserts right after the current entry (`loadfile … insert-at <pos+1>`, mpv
-- ≥ 0.38), «Añadir a la cola» appends. Settings (mu-prefs, namespace mu-music): «Sin cortes» → gapless-audio=yes +
-- prefetch-playlist=yes (off: mpv's default weak); «Fundido» → a volume-gain fade-out at the end of a track and fade-in
-- at the start of the next (mpv plays one stream at a time: a real crossfade is impossible, ADR-064); «Volumen
-- igualado» → mpv's `replaygain` (track/album) for tagged files and, for files without tags, the gain measured by mpvd
-- applied as the file-local `replaygain-fallback` (preamp and clipping handled here, since mpv skips them for the
-- fallback); «Salida exclusiva» → audio-exclusive; headphone equalizer profiles come from mu-av (user-data/mu/av).
-- Script name: mu_music. Binding: music-menu. State: user-data/mu/music.
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
local EVENT = 'mu-music-event'
local MENU = 'mu-music'
local INPUT = 'mu-music-input'
local INPUT_EVENT = 'mu-music-input-event'
local ROOT_TITLE = 'Música'

local opts = {
  gapless = false,          -- true: gapless-audio=yes + prefetch-playlist=yes; false: mpv's default (weak)
  fade = 0,                 -- seconds of fade-out at the end of a track (fade-in is half of it); 0 = off
  replaygain = 'no',        -- no | track | album
  preamp = 0,               -- dB added to the gains computed by mpvd (tags use mpv's replaygain-preamp)
  exclusive = false,        -- audio-exclusive
  history_seconds = 240,    -- a listen counts after half the track or this many seconds
  osd_seconds = 3,
}
options.read_options(opts, 'mu-music')
local RG_MODES = { no = true, track = true, album = true }
local P = prefs.ns('mu-music', { gapless = opts.gapless, fade = opts.fade, replaygain = opts.replaygain,
                                 exclusive = opts.exclusive }, function(key, v)
  if key == 'replaygain' then return RG_MODES[v] == true end
  if key == 'fade' then return v >= 0 and v <= 12 end
  return true
end)
P:apply_opts(opts, 'mu-music', { 'gapless', 'fade', 'replaygain', 'exclusive' })

local FADE_STEPS = { 0, 2, 4, 6 }
local AUDIO_EXTS = {}
for e in ('mp3 flac ogg oga opus m4a m4b aac wav wv ape aif aiff alac wma mka mpc tta dsf dff spx'):gmatch('%S+') do
  AUDIO_EXTS[e] = true
end
local RG_LABEL = { no = 'no', track = 'por pista', album = 'por álbum' }
local RG_NEXT = { no = 'track', track = 'album', album = 'no' }

local state = {
  view = '', stack = {}, items = {}, last_error = '', force_open = false, input = nil,
  counts = {}, scanning = false, scan_progress = 0, rg = {}, settings = {},
  gains = {},               -- path → music.gain result
  gain = { source = 'none', db = nil, path = '' },
  pick = nil,               -- paths waiting for «Añadir a una lista»
  move = nil,               -- { kind = 'queue'|'list', first = menu row of the first entry, name = list, base = index }
  listen = { path = '', acc = 0, done = false, last = nil },
  fading = '',              -- '' | 'in' | 'out'
}

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function publish()
  mp.set_property_native('user-data/mu/music', {
    view = state.view, depth = #state.stack, items = state.items, last_error = state.last_error,
    input = state.input and state.input.mode or '', counts = state.counts, scanning = state.scanning,
    scan_progress = state.scan_progress, replaygain = opts.replaygain, gapless = opts.gapless, fade = opts.fade,
    exclusive = opts.exclusive, gain = state.gain, fading = state.fading, pick = state.pick and #state.pick or 0,
    listened = state.listen.done, rg = state.rg,
  })
end

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
local function plural(n, one, many) return tostring(n) .. ' ' .. (n == 1 and one or many) end

-- audio file (or a file whose only picture is its cover)
local function is_music()
  if not is_local(current_path()) then return false end
  if not mp.get_property_native('current-tracks/audio') then return false end
  local v = mp.get_property_native('current-tracks/video')
  return v == nil or v.albumart == true or v.image == true
end

-- ---------------------------------------------------------------------------------------------
-- playlist helpers

local function playlist() return mp.get_property_native('playlist') or {} end

local function playing_pos()
  local pos = mp.get_property_number('playlist-pos', -1)
  if mp.get_property_native('idle-active') then return -1 end
  return pos
end

-- replace the playlist with `paths` and start at `start` (1-based): the chosen track loads first, the rest is put
-- around it, so playback starts at once
local function play_paths(paths, start)
  if #paths == 0 then return end
  start = math.max(1, math.min(#paths, start or 1))
  mp.commandv('loadfile', paths[start], 'replace')
  for i = start + 1, #paths do mp.commandv('loadfile', paths[i], 'append') end
  for i = 1, start - 1 do mp.commandv('loadfile', paths[i], 'insert-at', tostring(i - 1)) end
  mp.set_property_bool('pause', false)
end

local function play_next(paths)
  local pos = playing_pos()
  if pos < 0 then play_paths(paths, 1) return end
  for i, p in ipairs(paths) do mp.commandv('loadfile', p, 'insert-at', tostring(pos + i)) end
end

local function enqueue(paths)
  if playing_pos() < 0 then play_paths(paths, 1) return end
  for _, p in ipairs(paths) do mp.commandv('loadfile', p, 'append') end
end

-- Paths of the tracks that can actually be played, plus, for every row, its position in THAT list. Missing files
-- (a disconnected disk) are skipped, so the row number and the playlist position are not the same thing: using the row
-- number made Enter start on another track.
local function playable(tracks)
  local paths, at = {}, {}
  for i, t in ipairs(tracks or {}) do
    if t.path and t.exists ~= false then
      paths[#paths + 1] = t.path
      at[i] = #paths
    else
      at[i] = #paths + 1     -- a missing file: start on the next one that is there
    end
  end
  return paths, at
end

local function paths_of(tracks)
  local paths = playable(tracks)
  return paths
end

-- value of a row → its track paths (asynchronously, from mpvd)
local function resolve(v, cb)
  if v.track then cb({ v.track }) return end
  if v.paths then cb(v.paths) return end
  local method, params, pick
  if v.album then method, params, pick = 'music.album', { key = v.album }, function(r) return r.tracks end
  elseif v.artist then method, params, pick = 'music.tracks', { artist = v.artist }, function(r) return r end
  elseif v.genre then method, params, pick = 'music.tracks', { genre = v.genre }, function(r) return r end
  elseif v.list then method, params, pick = 'music.playlists.get', { name = v.list }, function(r) return r.tracks end
  elseif v.smart then
    method, params, pick = 'music.smart', { kind = v.smart, value = v.smart_value }, function(r) return r.tracks end
  else cb({}) return end
  rpc.call(method, params, function(err, res)
    if err then osd(tr('Música: %s'):format(fail(err, method))) cb({}) return end
    cb(paths_of(pick(res)))
  end, 20)
end

local function do_play(v, how)
  resolve(v, function(paths)
    if #paths == 0 then osd(tr('No hay pistas que reproducir')) return end
    if how == 'next' then
      play_next(paths)
      osd(tr('⏭ A continuación: %s'):format(#paths == 1 and basename(paths[1])
                                             or plural(#paths, tr('pista'), tr('pistas'))))
    elseif how == 'queue' then
      enqueue(paths)
      osd(tr('➕ En la cola: %s'):format(#paths == 1 and basename(paths[1])
                                        or plural(#paths, tr('pista'), tr('pistas'))))
    else
      uosc.close(MENU)
      play_paths(paths, v.start or 1)
      osd(tr('▶ %s'):format(basename(paths[v.start or 1])))
    end
    publish()
  end)
end

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 400 then break end
    local acts = {}
    for _, a in ipairs(it.actions or {}) do acts[#acts + 1] = a.name end
    table.insert(out, { title = it.title or '', hint = it.hint or '', value = it.value or '', active = it.active or false,
                        actions = acts })
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
  if state.view ~= spec.name then state.move = nil end    -- a redraw of the same view keeps its move mapping
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

local PLAY_ACTIONS = {
  { name = 'play', icon = 'play_arrow', label = tr('Reproducir') },
  { name = 'next', icon = 'playlist_play', label = tr('Reproducir a continuación') },
  { name = 'queue', icon = 'playlist_add', label = tr('Añadir a la cola') },
  { name = 'list', icon = 'library_add', label = tr('Añadir a una lista…') },
}
local TRACK_ACTIONS = { PLAY_ACTIONS[2], PLAY_ACTIONS[3], PLAY_ACTIONS[4] }
local MOVE_ACTIONS = {
  { name = 'up', icon = 'arrow_upward', label = tr('Subir') },
  { name = 'down', icon = 'arrow_downward', label = tr('Bajar') },
  { name = 'remove', icon = 'delete', label = tr('Quitar') },
}

local function track_row(t, value, title)
  local here = current_path()
  local hint = {}
  if t.artist and t.artist ~= '' and title then hint[#hint + 1] = t.artist end
  if (t.duration or 0) > 0 then hint[#hint + 1] = hms(t.duration) end
  return { title = title or t.label or t.title, hint = table.concat(hint, ' · '), icon = 'music_note',
           value = value or { track = t.path }, active = t.path == here or nil, muted = (t.exists == false) or nil,
           actions = TRACK_ACTIONS }
end

local function group_rows(value, what)
  return {
    { title = tr('Reproducir %s'):format(what), icon = 'play_arrow', value = { play = value } },
    { title = tr('Reproducir a continuación'), icon = 'playlist_play', value = { next = value } },
    { title = tr('Añadir a la cola'), icon = 'playlist_add', value = { queue = value } },
    { title = tr('Añadir a una lista…'), icon = 'library_add', value = { pick = value }, separator = true },
  }
end

views.root = function()
  if not require_mpvd(ROOT_TITLE) then return end
  show(ROOT_TITLE, uosc.loading_items())
  rpc.call('music.status', nil, function(err, st)
    if not still('root') then return end
    if err then show(ROOT_TITLE, uosc.message_items(fail(err, 'music.status'), 'error')) return end
    state.counts = { tracks = st.tracks, albums = st.albums, artists = st.artists, genres = st.genres,
                     folders = st.folders, playlists = st.playlists, history = st.history }
    state.scanning, state.rg, state.settings = st.scanning, st.replaygain or {}, st.settings or {}
    publish()
    local items = {}
    if (st.folders or 0) == 0 then
      table.insert(items, { title = tr('Añade la carpeta con tu música'), icon = 'info', selectable = false, muted = true })
    elseif (st.tracks or 0) == 0 and st.scanning then
      table.insert(items, { title = tr('Buscando tu música…'), icon = 'spinner', selectable = false, muted = true })
    end
    local pos, pl = playing_pos(), playlist()
    local queued = pos >= 0 and math.max(0, #pl - pos - 1) or 0
    table.insert(items, { title = tr('Artistas'), hint = tostring(st.artists or 0), icon = 'person',
                          value = { view = 'artists' } })
    table.insert(items, { title = tr('Álbumes'), hint = tostring(st.albums or 0), icon = 'album', value = { view = 'albums' } })
    table.insert(items, { title = tr('Géneros'), hint = tostring(st.genres or 0), icon = 'category',
                          value = { view = 'genres' } })
    table.insert(items, { title = tr('Buscar…'), icon = 'search', value = { input = 'search' }, separator = true })
    table.insert(items, { title = tr('Listas'), hint = tostring(st.playlists or 0), icon = 'queue_music',
                          value = { view = 'lists' } })
    table.insert(items, { title = tr('Cola'), hint = queued > 0 and plural(queued, 'pista', 'pistas') or 'vacía',
                          icon = 'playlist_play', value = { view = 'queue' } })
    table.insert(items, { title = tr('Historial'), hint = tostring(st.history or 0), icon = 'history',
                          value = { view = 'history' }, separator = true })
    local scan = st.scanning and string.format('buscando %d %%', math.floor((st.progress or 0) * 100))
    table.insert(items, { title = tr('Carpetas'), hint = scan or tostring(st.folders or 0), icon = 'folder',
                          value = { view = 'folders' } })
    table.insert(items, { title = tr('Ajustes'), icon = 'settings', value = { view = 'settings' } })
    show(ROOT_TITLE, items, { footnote = tr('Enter abre · Tab: acciones · ⌫ atrás · Esc cierra') })
  end, 15)
end

views.artists = function()
  if not require_mpvd('Artistas') then return end
  show(tr('Artistas'), uosc.loading_items())
  rpc.call('music.artists', nil, function(err, rows)
    if not still('artists') then return end
    if err then show(tr('Artistas'), uosc.message_items(fail(err, 'music.artists'), 'error')) return end
    local items = {}
    for _, a in ipairs(rows or {}) do
      table.insert(items, { title = a.name, hint = plural(a.albums or 0, 'álbum', 'álbumes') .. ' · ' ..
                              plural(a.tracks or 0, 'pista', 'pistas'), icon = 'person',
                            value = { view = 'artist', key = a.key, title = a.name, artist = a.key },
                            actions = PLAY_ACTIONS })
    end
    if #items == 0 then items = uosc.message_items(tr('No hay música en tus carpetas'), 'music_off') end
    show(tr('Artistas'), items, { footnote = tr('Enter abre · Tab: reproducir, a continuación, a la cola'),
                              search_style = 'on_demand' })
  end, 15)
end

local function album_row(a, with_artist)
  local hint = {}
  if with_artist and a.artist and a.artist ~= '' then hint[#hint + 1] = a.artist end
  if a.year then hint[#hint + 1] = tostring(a.year) end
  hint[#hint + 1] = plural(a.tracks or 0, 'pista', 'pistas')
  return { title = a.title, hint = table.concat(hint, ' · '), icon = 'album',
           value = { view = 'album', key = a.key, title = a.title, album = a.key }, actions = PLAY_ACTIONS }
end

views.artist = function(args)
  local title = args.title or 'Artista'
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  rpc.call('music.artist', { key = args.key }, function(err, res)
    if not still('artist') then return end
    if err then show(title, uosc.message_items(fail(err, 'music.artist'), 'error')) return end
    local items = group_rows({ artist = args.key }, 'todo')
    for _, a in ipairs(res.albums or {}) do table.insert(items, album_row(a, false)) end
    show(title, items, { footnote = tr('Enter abre · Tab: acciones · ⌫ atrás') })
  end, 15)
end

views.albums = function(args)
  local title = args.title or 'Álbumes'
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  local name = state.view
  rpc.call('music.albums', args.genre and { genre = args.genre } or nil, function(err, rows)
    if not still(name) then return end
    if err then show(title, uosc.message_items(fail(err, 'music.albums'), 'error')) return end
    local items = args.genre and group_rows({ genre = args.genre }, 'todo') or {}
    for _, a in ipairs(rows or {}) do table.insert(items, album_row(a, true)) end
    if #(rows or {}) == 0 then
      for _, it in ipairs(uosc.message_items(tr('No hay álbumes'), 'album')) do table.insert(items, it) end
    end
    show(title, items, { footnote = tr('Enter abre · Tab: acciones · ⌫ atrás'), search_style = 'on_demand' })
  end, 15)
end

views.genre = function(args) views.albums(args) end

views.album = function(args)
  local title = args.title or 'Álbum'
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  rpc.call('music.album', { key = args.key }, function(err, res)
    if not still('album') then return end
    if err then show(title, uosc.message_items(fail(err, 'music.album'), 'error')) return end
    local items = group_rows({ album = args.key }, 'el álbum')
    local a = res.album or {}
    items[1].hint = table.concat({ a.artist or '', a.year and tostring(a.year) or '', hms(a.duration) }, ' · ')
    local paths, at = playable(res.tracks)
    for i, t in ipairs(res.tracks or {}) do
      table.insert(items, track_row(t, { track = t.path, paths = paths, start = at[i] }))
    end
    show(title, items, { footnote = tr('Enter reproduce desde esa pista · Tab: a continuación, a la cola, a una lista') })
  end, 15)
end

views.genres = function()
  if not require_mpvd('Géneros') then return end
  show(tr('Géneros'), uosc.loading_items())
  rpc.call('music.genres', nil, function(err, rows)
    if not still('genres') then return end
    if err then show(tr('Géneros'), uosc.message_items(fail(err, 'music.genres'), 'error')) return end
    local items = {}
    for _, g in ipairs(rows or {}) do
      table.insert(items, { title = g.name, hint = plural(g.albums or 0, 'álbum', 'álbumes'), icon = 'category',
                            value = { view = 'genre', genre = g.key, title = g.name }, actions = PLAY_ACTIONS })
    end
    if #items == 0 then items = uosc.message_items(tr('Tus pistas no tienen género'), 'category') end
    show(tr('Géneros'), items, { footnote = tr('Enter abre · Tab: acciones · ⌫ atrás'), search_style = 'on_demand' })
  end, 15)
end

local function track_list_view(name, title, method, params, pick, extra_rows, value_of)
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  rpc.call(method, params, function(err, res)
    if not still(name) then return end
    if err then show(title, uosc.message_items(fail(err, method), 'error')) return end
    local tracks = pick(res)
    local items = extra_rows and extra_rows(res) or {}
    local paths, at = playable(tracks)
    local first = #items + 2          -- menu row of the first track (the «Atrás» row is 1)
    for i, t in ipairs(tracks) do
      local row = track_row(t, value_of and value_of(t, i, paths, at) or
                               { track = t.path, paths = paths, start = at[i] }, t.title)
      if name == 'list' then row.actions = { MOVE_ACTIONS[1], MOVE_ACTIONS[2], MOVE_ACTIONS[3], TRACK_ACTIONS[1],
                                             TRACK_ACTIONS[2] } end
      table.insert(items, row)
    end
    if #tracks == 0 then
      for _, it in ipairs(uosc.message_items(tr('Nada por aquí todavía'), 'music_off')) do table.insert(items, it) end
    end
    if name == 'list' then state.move = { kind = 'list', first = first, name = params.name } end
    show(title, items, { footnote = name == 'list' and 'Enter reproduce · Ctrl+↑/↓ o Tab: mover, quitar · ⌫ atrás'
                           or 'Enter reproduce · Tab: a continuación, a la cola, a una lista · ⌫ atrás',
                         on_move = name == 'list' and 'callback' or nil })
  end, 20)
end

views.lists = function()
  if not require_mpvd('Listas') then return end
  show(tr('Listas'), uosc.loading_items())
  rpc.call('music.playlists.list', nil, function(err, lists)
    if not still('lists') then return end
    if err then show(tr('Listas'), uosc.message_items(fail(err, 'music.playlists.list'), 'error')) return end
    rpc.call('music.smart', nil, function(err2, smart)
      if not still('lists') then return end
      local items = {}
      for _, l in ipairs(lists or {}) do
        table.insert(items, { title = l.name, hint = plural(l.count or 0, 'pista', 'pistas') .. ' · ' .. hms(l.duration),
                              icon = 'queue_music', value = { view = 'list', name = l.name, title = l.name, list = l.name },
                              actions = PLAY_ACTIONS })
      end
      if #items > 0 then items[#items].separator = true end
      table.insert(items, { title = tr('Nueva lista…'), icon = 'playlist_add', value = { input = 'new_list' } })
      table.insert(items, { title = tr('Guardar la cola como lista…'), icon = 'save', value = { input = 'save_queue' } })
      table.insert(items, { title = tr('Importar una lista (M3U)…'), icon = 'file_open', value = { input = 'import' },
                            separator = true })
      if not err2 then
        for _, s in ipairs(smart or {}) do
          table.insert(items, { title = s.title, hint = tostring(s.count or 0), icon = 'auto_awesome',
                                value = { view = 'smart', kind = s.kind, smart = s.kind, smart_value = s.value,
                                          title = s.title }, actions = PLAY_ACTIONS })
        end
        if #(smart or {}) > 0 then items[#items].separator = true end
      end
      table.insert(items, { title = tr('Por género…'), icon = 'category', value = { view = 'genres' } })
      show(tr('Listas'), items, { footnote = tr('Enter abre · Tab: acciones · ⌫ atrás') })
    end, 15)
  end, 15)
end

views.list = function(args)
  track_list_view('list', args.title or args.name, 'music.playlists.get', { name = args.name },
    function(res) return res.tracks or {} end,
    function(res)
      local rows = group_rows({ list = args.name }, 'la lista')
      rows[1].hint = hms(res.duration)
      table.insert(rows, { title = tr('Ordenar…'), icon = 'sort', value = { view = 'sort', name = args.name } })
      table.insert(rows, { title = tr('Renombrar…'), icon = 'edit', value = { input = 'rename_list', name = args.name } })
      table.insert(rows, { title = tr('Exportar (M3U8)…'), icon = 'ios_share', value = { input = 'export', name = args.name } })
      table.insert(rows, { title = tr('Borrar la lista'), icon = 'delete', value = { delete_list = args.name },
                           separator = true })
      return rows
    end,
    function(t, i, paths, at) return { track = t.path, paths = paths, start = at[i], index = t.index } end)
end

views.sort = function(args)
  show(tr('Ordenar'), {
    { title = tr('Por artista'), icon = 'person', value = { sort = 'artist', name = args.name } },
    { title = tr('Por álbum'), icon = 'album', value = { sort = 'album', name = args.name } },
    { title = tr('Por título'), icon = 'sort_by_alpha', value = { sort = 'title', name = args.name } },
    { title = tr('Por año'), icon = 'event', value = { sort = 'year', name = args.name } },
    { title = tr('Al azar'), icon = 'shuffle', value = { sort = 'shuffle', name = args.name } },
  }, { footnote = tr('Se guarda en la lista · ⌫ atrás') })
end

views.smart = function(args)
  track_list_view('smart', args.title or 'Lista', 'music.smart', { kind = args.kind, value = args.smart_value },
    function(res) return res.tracks or {} end,
    function() return group_rows({ smart = args.kind, smart_value = args.smart_value }, 'todo') end)
end

views.queue = function()
  local pl = playlist()
  local pos = playing_pos()
  local items = {}
  local paths = {}
  for i, e in ipairs(pl) do paths[i] = e.filename end
  local header = pos >= 0 and pl[pos + 1] ~= nil
  state.move = { kind = 'queue', first = header and 3 or 2, base = pos + 1 }
  local function build(desc)
    items = {}
    if pos >= 0 and pl[pos + 1] then
      local d = desc[pos + 1] or {}
      table.insert(items, { title = d.title or basename(pl[pos + 1].filename), hint = tr('sonando ahora'), icon = 'graphic_eq',
                            active = true, selectable = false, separator = true })
    end
    for i = pos + 2, #pl do
      local d = desc[i] or {}
      local hint = {}
      if d.artist and d.artist ~= '' then hint[#hint + 1] = d.artist end
      if (d.duration or 0) > 0 then hint[#hint + 1] = hms(d.duration) end
      table.insert(items, { title = d.title or basename(pl[i].filename), hint = table.concat(hint, ' · '),
                            icon = 'music_note', value = { qplay = i - 1 }, actions = MOVE_ACTIONS })
    end
    local n = #pl - pos - 1
    if pos < 0 then n = 0 end
    if n <= 0 then
      table.insert(items, { title = tr('No hay nada después de esta pista'), icon = 'info', selectable = false, muted = true })
    end
    if #items > 0 then items[#items].separator = true end
    if #pl > 0 then
      table.insert(items, { title = tr('Guardar la cola como lista…'), icon = 'save', value = { input = 'save_queue' } })
    end
    if n > 0 then table.insert(items, { title = tr('Vaciar la cola'), icon = 'clear_all', value = { clear_queue = true } }) end
    show(tr('Cola'), items, { footnote = tr('Enter salta a la pista · Ctrl+↑/↓ o Tab: mover, quitar · ⌫ atrás'),
                          on_move = 'callback' })
  end
  if #paths > 0 and rpc.connected() then
    rpc.call('music.describe', { paths = paths }, function(err, res)
      if not still('queue') then return end
      if err then fail(err, 'music.describe') build({}) return end
      build(res or {})
    end, 15)
    if uosc.open_type() ~= MENU then show(tr('Cola'), uosc.loading_items()) end
  else
    build({})
  end
end

views.history = function()
  if not require_mpvd('Historial') then return end
  show(tr('Historial'), uosc.loading_items())
  rpc.call('music.history', { limit = 200 }, function(err, rows)
    if not still('history') then return end
    if err then show(tr('Historial'), uosc.message_items(fail(err, 'music.history'), 'error')) return end
    local items = {}
    for _, h in ipairs(rows or {}) do
      table.insert(items, { title = h.title ~= '' and h.title or basename(h.path),
                            hint = table.concat({ h.artist or '', os.date('%d/%m %H:%M', math.floor(h.at or 0)) }, ' · '),
                            icon = 'history', value = { track = h.path }, muted = (h.exists == false) or nil,
                            actions = TRACK_ACTIONS })
    end
    if #items == 0 then
      items = uosc.message_items(tr('Aún no has escuchado nada (solo se guarda en tu equipo)'), 'history')
    else
      items[#items].separator = true
      table.insert(items, { title = tr('Borrar el historial'), icon = 'delete', value = { clear_history = true } })
    end
    show(tr('Historial'), items, { footnote = tr('Solo en tu equipo, sin enviar nada · ⌫ atrás'), search_style = 'on_demand' })
  end, 15)
end

views.folders = function()
  if not require_mpvd('Carpetas') then return end
  show(tr('Carpetas'), uosc.loading_items())
  rpc.call('music.folders.list', nil, function(err, rows)
    if not still('folders') then return end
    if err then show(tr('Carpetas'), uosc.message_items(fail(err, 'music.folders.list'), 'error')) return end
    local items = {}
    for _, f in ipairs(rows or {}) do
      table.insert(items, { title = f.path, icon = f.exists and 'folder' or 'folder_off',
        hint = f.exists and plural(f.files or 0, 'pista', 'pistas') or 'no se encuentra', value = { folder = f.path },
        actions = { { name = 'rescan', icon = 'refresh', label = tr('Volver a buscar') },
                    { name = 'remove', icon = 'delete', label = tr('Quitar') } } })
    end
    if #items > 0 then items[#items].separator = true end
    local here = current_path()
    if is_local(here) and is_music() then
      table.insert(items, { title = tr('Añadir la carpeta de lo que suena'), hint = dirname(here), icon = 'create_new_folder',
                            value = { add = dirname(here) } })
    end
    table.insert(items, { title = tr('Escribir o pegar una ruta…'), icon = 'edit', value = { input = 'folder' } })
    if #(rows or {}) > 0 then
      table.insert(items, { title = tr('Volver a buscar en todas'), icon = 'refresh', value = { rescan = true },
        hint = state.scanning and string.format('buscando %d %%', math.floor(state.scan_progress * 100)) or nil })
    end
    show(tr('Carpetas'), items, { footnote = tr('Tab: volver a buscar / quitar (no se borra nada) · ⌫ atrás') })
  end, 15)
end

views.settings = function()
  if not require_mpvd('Ajustes') then return end
  rpc.call('music.status', { default_folder = false }, function(err, st)
    if not still('settings') then return end
    if err then show(tr('Ajustes'), uosc.message_items(fail(err, 'music.status'), 'error')) return end
    state.rg, state.settings = st.replaygain or {}, st.settings or {}
    local rg = state.rg
    local av = mp.get_property_native('user-data/mu/av') or {}
    local eq_title = 'plano'
    for _, e in ipairs(av.eq_presets or {}) do if e.id == av.eq then eq_title = e.title end end
    local measuring = (rg.pending or 0) > 0 and string.format('faltan %d', rg.pending) or 'al día'
    show(tr('Ajustes'), {
      { title = tr('Sin cortes entre pistas'), hint = opts.gapless and 'siempre' or 'si el formato coincide',
        icon = 'join_inner', active = opts.gapless, value = { toggle = 'gapless' } },
      { title = tr('Fundido entre pistas'), hint = opts.fade > 0 and (opts.fade .. ' s') or 'no', icon = 'blur_linear',
        active = opts.fade > 0, value = { cycle = 'fade' } },
      { title = tr('Volumen igualado'), hint = RG_LABEL[opts.replaygain], icon = 'equalizer',
        active = opts.replaygain ~= 'no', value = { cycle = 'replaygain' } },
      { title = tr('Calcular el volumen de las pistas sin etiquetas'), icon = 'calculate',
        hint = (state.settings.auto_replaygain and 'sí' or 'no') .. ' · ' .. measuring,
        active = state.settings.auto_replaygain, value = { toggle = 'auto_replaygain' } },
      { title = tr('Salida exclusiva'), hint = opts.exclusive and 'sí' or 'no', icon = 'speaker', active = opts.exclusive,
        value = { toggle = 'exclusive' }, separator = true },
      { title = tr('Ecualizador'), hint = eq_title, icon = 'graphic_eq', value = { view = 'eq' } },
    }, { footnote = tr('Se recuerda · Salida exclusiva: solo algunos sistemas (PipeWire, WASAPI, CoreAudio)') })
  end, 15)
end

views.eq = function()
  local av = mp.get_property_native('user-data/mu/av') or {}
  local items = { { title = tr('Plano (sin ecualizar)'), icon = (av.eq or '') == '' and 'radio_button_checked'
                      or 'radio_button_unchecked', active = (av.eq or '') == '', value = { eq = '' }, separator = true } }
  local rest = {}
  for _, e in ipairs(av.eq_presets or {}) do
    local row = { title = e.title, icon = av.eq == e.id and 'radio_button_checked' or 'radio_button_unchecked',
                  active = av.eq == e.id, value = { eq = e.id } }
    if e.headphones then table.insert(items, row) else table.insert(rest, row) end
  end
  if #items > 1 then items[#items].separator = true end
  for _, r in ipairs(rest) do table.insert(items, r) end
  if #(av.eq_presets or {}) == 0 then
    table.insert(items, { title = tr('Los perfiles vienen de «Sonido e imagen» (mu-av)'), icon = 'info', selectable = false,
                          muted = true })
  end
  show(tr('Ecualizador'), items, { footnote = tr('Perfiles para auriculares arriba · se recuerda · ⌫ atrás') })
end

views.pick = function()
  if not require_mpvd('Añadir a una lista') then return end
  rpc.call('music.playlists.list', nil, function(err, lists)
    if not still('pick') then return end
    if err then show(tr('Añadir a una lista'), uosc.message_items(fail(err, 'music.playlists.list'), 'error')) return end
    local items = { { title = tr('Nueva lista…'), icon = 'playlist_add', value = { input = 'new_list' }, separator = true } }
    for _, l in ipairs(lists or {}) do
      table.insert(items, { title = l.name, hint = plural(l.count or 0, 'pista', 'pistas'), icon = 'queue_music',
                            value = { add_to = l.name } })
    end
    show(tr('Añadir a una lista'), items, { footnote = (state.pick and plural(#state.pick, 'pista', 'pistas') or '') ..
                                          ' · Enter añade · ⌫ atrás' })
  end, 15)
end

-- ---------------------------------------------------------------------------------------------
-- text box (palette): search, a folder, list names, import/export paths

local INPUT_TITLES = {
  search = 'Buscar música', folder = 'Carpeta con música', new_list = 'Nombre de la nueva lista',
  rename_list = 'Nuevo nombre de la lista', save_queue = 'Nombre de la lista', import = 'Ruta del archivo .m3u / .m3u8',
  export = 'Carpeta o archivo de destino',
}

local function input_menu(query, items)
  local inp = state.input
  query = query or ''
  inp.query = query
  if not items then
    items = {}
    if query ~= '' then
      local verb = (inp.mode == 'folder' or inp.mode == 'import') and 'Añadir: ' or inp.mode == 'export' and 'Exportar a: '
        or 'Guardar: '
      table.insert(items, { title = verb .. query, icon = 'check', value = { save = query } })
    else
      local empty = inp.mode == 'search' and 'Artista, álbum, canción, género o año'
        or (inp.mode == 'folder' or inp.mode == 'import' or inp.mode == 'export') and 'Escribe o pega la ruta'
        or 'Escribe el nombre'
      table.insert(items, { title = empty, icon = 'edit', selectable = false, muted = true })
    end
  end
  return { type = INPUT, title = INPUT_TITLES[inp.mode] or 'Escribe', items = items,
    callback = { SCRIPT, INPUT_EVENT }, search_style = 'palette', search_debounce = inp.mode == 'search' and 250 or 0,
    on_search = 'callback', on_close = 'callback', search_suggestion = query,
    footnote = inp.mode == 'search' and 'Enter reproduce o abre · ⌫ en vacío vuelve' or 'Enter guarda · ⌫ en vacío vuelve' }
end

local function open_input(mode, text, extra)
  state.input = { mode = mode, query = text or '' }
  for k, v in pairs(extra or {}) do state.input[k] = v end
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
  rpc.call('music.search', { q = query, limit = 30 }, function(err, res)
    if not state.input or state.input.mode ~= 'search' or inp.query ~= query then return end
    local items = {}
    if err then
      items = uosc.message_items(fail(err, 'music.search'), 'error')
    else
      for _, a in ipairs(res.artists or {}) do
        table.insert(items, { title = a.name, hint = tr('artista'), icon = 'person',
                              value = { view = 'artist', key = a.key, title = a.name, artist = a.key } })
      end
      for _, a in ipairs(res.albums or {}) do table.insert(items, album_row(a, true)) end
      for _, t in ipairs(res.tracks or {}) do table.insert(items, track_row(t, nil, t.title)) end
      if #items == 0 then items = uosc.message_items(tr('Nada con «%s»'):format(query), 'search_off') end
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
  osd(tr('🎵 Buscando música en %s…'):format(res and res.path or tr('tus carpetas')))
end

local function add_folder(path)
  rpc.call('music.folders.add', { path = path, notify = SCRIPT }, function(err, res)
    if err then osd(tr('Carpeta: %s'):format(fail(err, 'music.folders.add')), 5) return end
    if res.added then scan_started(res) else osd(tr('Esa carpeta ya estaba')) end
    if still('folders') then reopen_current() end
  end, 15)
end

local function queue_paths()
  local out = {}
  for _, e in ipairs(playlist()) do out[#out + 1] = strip_file(e.filename) end
  return out
end

local function list_changed(name, text)
  osd(text)
  if still('list') or still('lists') then reopen_current() end
  publish()
  return name
end

local function save_input(text, inp)
  local mode = inp.mode
  if mode == 'folder' then
    close_input(true)
    add_folder(mp.command_native({ 'expand-path', text }) or text)
  elseif mode == 'new_list' or mode == 'save_queue' then
    local paths = mode == 'save_queue' and queue_paths() or (state.pick or {})
    close_input(true)
    rpc.call('music.playlists.create', { name = text, paths = #paths > 0 and paths or nil }, function(err, res)
      if err then osd(tr('Lista: %s'):format(fail(err, 'music.playlists.create')), 5) return end
      state.pick = nil
      if still('pick') then
        table.remove(state.stack)
        reopen_current()
      end
      list_changed(res.name, '✓ Lista «' .. res.name .. '» (' .. plural(#(res.tracks or {}), 'pista', 'pistas') .. ')')
    end, 15)
  elseif mode == 'rename_list' then
    close_input(false)
    rpc.call('music.playlists.rename', { name = inp.name, new_name = text }, function(err, res)
      if err then osd(tr('Renombrar: %s'):format(fail(err, 'music.playlists.rename')), 5) reopen_current(true) return end
      local top = state.stack[#state.stack]
      if top and top.name == 'list' then
        top.args = { name = res.name, title = res.name, list = res.name }
      end
      state.force_open = true
      list_changed(res.name, '✓ Ahora se llama «' .. res.name .. '»')
      reopen_current(true)
    end, 15)
  elseif mode == 'import' then
    close_input(true)
    rpc.call('music.playlists.import', { path = mp.command_native({ 'expand-path', text }) or text }, function(err, res)
      if err then osd(tr('Importar: %s'):format(fail(err, 'music.playlists.import')), 5) return end
      list_changed(res.name, '✓ Importada «' .. res.name .. '» (' .. plural(#(res.tracks or {}), 'pista', 'pistas') .. ')')
    end, 15)
  elseif mode == 'export' then
    close_input(true)
    rpc.call('music.playlists.export', { name = inp.name, path = mp.command_native({ 'expand-path', text }) or text },
      function(err, res)
        if err then osd(tr('Exportar: %s'):format(fail(err, 'music.playlists.export')), 5) return end
        osd(tr('✓ Exportada a %s'):format(res.path), 5)
      end, 15)
  end
end

local handle_activate

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
    if ev.value.save ~= nil then
      if ev.value.save ~= '' and inp.mode ~= 'search' then save_input(ev.value.save, inp) end
    else
      -- a search result: the same actions as in the menus
      local v = ev.value
      if v.view then
        close_input(false)
        state.force_open = true
        open_view({ name = v.view, args = v })
      else
        if (ev.action or 'play') == 'play' and not v.next and not v.queue then close_input(false) end
        handle_activate(ev)
      end
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

local function forget_view(name)
  for i = #state.stack, 1, -1 do
    if state.stack[i].name == name then table.remove(state.stack, i) end
  end
end

local function set_pref(key, value)
  opts[key] = value
  P:set(key, value)
end

local apply_audio_settings
local apply_gain

-- queue: playlist indices (0-based, as the rows carry them in v.qplay)
local function queue_move_at(a, b)
  local m = state.move
  if not m then return end
  local n = #playlist()
  if a < m.base or b < m.base or a >= n or b >= n or a == b then reopen_current() return end
  -- playlist-move puts the entry in the place of the target: moving down needs the target after it
  mp.commandv('playlist-move', tostring(a), tostring(b > a and b + 1 or b))
  mp.add_timeout(0.05, function() reopen_current() end)
end

local function list_move_at(a, b)
  local m = state.move
  if not m then return end
  if a < 0 or b < 0 or a == b then reopen_current() return end
  rpc.call('music.playlists.move', { name = m.name, src = a, dst = b }, function(err)
    if err then osd(tr('Mover: %s'):format(fail(err, 'music.playlists.move'))) end
    reopen_current()
  end, 15)
end

-- drag and drop through uosc's own «move» event, which does send menu rows (and is refused while searching)
local function queue_move(from_row, to_row)
  local m = state.move
  if not m then return end
  queue_move_at(m.base + (from_row - m.first), m.base + (to_row - m.first))
end

local function list_move(from_row, to_row)
  local m = state.move
  if not m then return end
  list_move_at(from_row - m.first, to_row - m.first)
end

handle_activate = function(ev)
  local v = type(ev.value) == 'table' and ev.value or {}
  local action = ev.action
  if v.folder then
    if action == 'remove' then
      rpc.call('music.folders.remove', { path = v.folder }, function(err, res)
        if err then osd(tr('Quitar: %s'):format(fail(err, 'music.folders.remove'))) return end
        osd(tr('Carpeta quitada (%s; no se borra ningún archivo)')
          :format(plural(res.removed or 0, tr('pista'), tr('pistas'))))
        reopen_current()
      end, 15)
    else
      rpc.call('music.scan', { path = v.folder, notify = SCRIPT }, function(err)
        if err then osd(tr('Buscar: %s'):format(fail(err, 'music.scan'))) return end
        scan_started({ path = v.folder })
      end, 15)
    end
    return
  end
  if action == 'up' or action == 'down' then
    -- the row number uosc sends is its position in the menu it is DRAWING, which the search box filters (uosc refuses
    -- to drag while searching for the same reason). The row itself carries its real index: use that.
    local step = action == 'up' and -1 or 1
    if state.move and state.move.kind == 'queue' and v.qplay then
      queue_move_at(v.qplay, v.qplay + step)
    elseif state.move and state.move.kind == 'list' and v.index then
      list_move_at(v.index, v.index + step)
    end
    return
  elseif action == 'remove' then
    if state.move and state.move.kind == 'queue' and v.qplay then
      mp.commandv('playlist-remove', tostring(v.qplay))
      mp.add_timeout(0.05, function() reopen_current() end)
    elseif state.move and state.move.kind == 'list' and v.index then
      rpc.call('music.playlists.remove', { name = state.move.name, index = v.index }, function(err)
        if err then osd(tr('Quitar: %s'):format(fail(err, 'music.playlists.remove'))) end
        reopen_current()
      end, 15)
    end
    return
  elseif action == 'next' or action == 'queue' then
    local target = { track = v.track, album = v.album, artist = v.artist, genre = v.genre, list = v.list,
                     smart = v.smart, smart_value = v.smart_value }
    do_play(target, action)
    return
  elseif action == 'list' then
    resolve({ track = v.track, album = v.album, artist = v.artist, genre = v.genre, list = v.list, smart = v.smart,
              smart_value = v.smart_value }, function(paths)
      state.pick = paths
      publish()
      open_view({ name = 'pick' })
    end)
    return
  elseif action == 'play' then
    do_play({ album = v.album, artist = v.artist, genre = v.genre, list = v.list, smart = v.smart,
              smart_value = v.smart_value, track = v.track })
    return
  end
  if v.play then do_play(v.play)
  elseif v.next then do_play(v.next, 'next')
  elseif v.queue then do_play(v.queue, 'queue')
  elseif v.pick then
    resolve(v.pick, function(paths)
      state.pick = paths
      publish()
      open_view({ name = 'pick' })
    end)
  elseif v.track then
    if v.paths then do_play({ paths = v.paths, start = v.start }) else do_play({ track = v.track }) end
  elseif v.qplay then
    mp.commandv('playlist-play-index', tostring(v.qplay))
    uosc.close(MENU)
  elseif v.view then
    if v.view == 'eq' or v.view == 'settings' then forget_view(v.view) end
    open_view({ name = v.view, args = v })
  elseif v.input then
    local text = v.input == 'rename_list' and v.name or ''
    if v.input == 'new_list' and not still('pick') then state.pick = nil end
    open_input(v.input, text, { name = v.name })
  elseif v.add then add_folder(v.add)
  elseif v.rescan then
    rpc.call('music.scan', { notify = SCRIPT }, function(err)
      if err then osd(tr('Buscar: %s'):format(fail(err, 'music.scan'))) return end
      scan_started()
      reopen_current()
    end, 15)
  elseif v.add_to then
    local paths = state.pick or {}
    rpc.call('music.playlists.add', { name = v.add_to, paths = paths }, function(err, res)
      if err then osd(tr('Lista: %s'):format(fail(err, 'music.playlists.add'))) return end
      state.pick = nil
      osd(tr('✓ %s a «%s»'):format(plural(#paths, tr('pista añadida'), tr('pistas añadidas')), res.name))
      table.remove(state.stack)
      if #state.stack == 0 then uosc.close(MENU) else reopen_current() end
      publish()
    end, 15)
  elseif v.sort then
    rpc.call('music.playlists.sort', { name = v.name, by = v.sort }, function(err)
      if err then osd(tr('Ordenar: %s'):format(fail(err, 'music.playlists.sort'))) return end
      table.remove(state.stack)
      reopen_current()
    end, 15)
  elseif v.delete_list then
    rpc.call('music.playlists.delete', { name = v.delete_list }, function(err)
      if err then osd(tr('Borrar: %s'):format(fail(err, 'music.playlists.delete'))) return end
      osd(tr('Lista borrada (queda una copia en la papelera de listas)'))
      table.remove(state.stack)
      reopen_current()
    end, 15)
  elseif v.clear_queue then
    local pos = playing_pos()
    for i = #playlist() - 1, pos + 1, -1 do mp.commandv('playlist-remove', tostring(i)) end
    mp.add_timeout(0.05, function() reopen_current() end)
  elseif v.clear_history then
    rpc.call('music.history.clear', nil, function(err)
      if err then osd(tr('Historial: %s'):format(fail(err, 'music.history.clear'))) return end
      osd(tr('Historial borrado'))
      reopen_current()
    end, 15)
  elseif v.toggle == 'gapless' or v.toggle == 'exclusive' then
    set_pref(v.toggle, not opts[v.toggle])
    apply_audio_settings()
    reopen_current()
  elseif v.toggle == 'auto_replaygain' then
    rpc.call('music.settings.set', { auto_replaygain = not state.settings.auto_replaygain }, function(err, s)
      if err then osd(tr('Ajustes: %s'):format(fail(err, 'music.settings.set'))) return end
      state.settings = s
      reopen_current()
    end, 15)
  elseif v.cycle == 'fade' then
    local nxt = FADE_STEPS[1]
    for i, s in ipairs(FADE_STEPS) do
      if s == opts.fade then nxt = FADE_STEPS[i % #FADE_STEPS + 1] end
    end
    set_pref('fade', nxt)
    osd(tr('Fundido entre pistas: %s'):format(nxt > 0 and (nxt .. ' s') or tr('no')))
    reopen_current()
  elseif v.cycle == 'replaygain' then
    set_pref('replaygain', RG_NEXT[opts.replaygain] or 'no')
    apply_audio_settings()
    osd(tr('Volumen igualado: %s'):format(RG_LABEL[opts.replaygain]))
    reopen_current()
  elseif v.eq ~= nil then
    mp.commandv('script-message-to', 'mu_av', 'mu-av-eq', v.eq)
    mp.add_timeout(0.15, function() if still('eq') then reopen_current() end end)
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    handle_activate(ev)
  elseif ev.type == 'move' then
    if state.move and state.move.kind == 'queue' then queue_move(ev.from_index, ev.to_index)
    elseif state.move and state.move.kind == 'list' then list_move(ev.from_index, ev.to_index) end
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
      state.stack, state.view, state.input, state.move = {}, '', nil, nil
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- audio settings: gapless, replaygain (tags and computed), exclusive output

-- dB to apply through replaygain-fallback for `g` (a music.gain result) in the current mode, or nil
local function fallback_db(g)
  if not g or opts.replaygain == 'no' then return nil end
  local db, peak = g.gain, g.peak
  if opts.replaygain == 'album' and g.album_gain ~= nil then db, peak = g.album_gain, g.album_peak end
  if db == nil then return nil end
  db = db + (tonumber(opts.preamp) or 0)
  if not mp.get_property_bool('replaygain-clip') and peak and peak > 0 then
    db = math.min(db, -20 * math.log10(peak))    -- mpv only protects tagged files from clipping
  end
  return math.floor(db * 100 + 0.5) / 100
end

apply_gain = function()
  local here = current_path()
  local pista = mp.get_property_native('current-tracks/audio') or {}
  if pista['replaygain-track-gain'] ~= nil then
    state.gain = { source = opts.replaygain ~= 'no' and 'tags' or 'none', db = nil, path = here }
  else
    local db = fallback_db(state.gains[here])
    if db ~= nil then
      mp.set_property_number('file-local-options/replaygain-fallback', db)
      state.gain = { source = 'computed', db = db, path = here }
    else
      if opts.replaygain == 'no' and state.gain.source == 'computed' then
        mp.set_property_number('file-local-options/replaygain-fallback',
                               mp.get_property_number('options/replaygain-fallback', 0))
      end
      state.gain = { source = 'none', db = nil, path = here }
    end
  end
  publish()
end

local function fetch_gain(path, cb)
  if not rpc.connected() or not is_local(path) then if cb then cb() end return end
  rpc.call('music.gain', { path = path }, function(err, g)
    if err then fail(err, 'music.gain') elseif g then state.gains[path] = g end
    if cb then cb() end
  end, 60)
end

-- the next entry's gain is fetched ahead, so it is set before its audio starts (on_load, no waiting)
local function prefetch_next()
  if opts.replaygain == 'no' then return end
  local pos = playing_pos()
  local e = playlist()[pos + 2]
  if e and is_local(e.filename) and not state.gains[strip_file(e.filename)] then
    local p = strip_file(e.filename)
    if AUDIO_EXTS[(p:match('%.([%w]+)$') or ''):lower()] then fetch_gain(p) end
  end
end

apply_audio_settings = function()
  mp.set_property('gapless-audio', opts.gapless and 'yes' or 'weak')
  if opts.gapless then mp.set_property('prefetch-playlist', 'yes') end
  mp.set_property('replaygain', RG_MODES[opts.replaygain] and opts.replaygain or 'no')
  if mp.get_property_bool('audio-exclusive') ~= opts.exclusive then
    mp.set_property_bool('audio-exclusive', opts.exclusive)
  end
  -- the computed gain of the current file follows the mode
  local here = current_path()
  if opts.replaygain ~= 'no' and is_music() and not state.gains[here] then
    fetch_gain(here, function() if current_path() == here then apply_gain() end end)
  elseif is_music() or state.gain.source ~= 'none' then
    apply_gain()
  end
end

mp.add_hook('on_load', 50, function()
  if opts.replaygain == 'no' then return end
  local p = strip_file(mp.get_property('stream-open-filename') or mp.get_property('path') or '')
  local db = fallback_db(state.gains[p])
  if db ~= nil then mp.set_property_number('file-local-options/replaygain-fallback', db) end
end)

-- ---------------------------------------------------------------------------------------------
-- fade out / fade in through volume-gain (on top of the user's volume, restored afterwards)

local fade = { base = nil, timer = nil, watch = nil, mode = '' }

local function fade_restore()
  if fade.timer then fade.timer:kill() fade.timer = nil end
  if fade.base ~= nil then mp.set_property_number('volume-gain', fade.base) end
  fade.base, fade.mode = nil, ''
  if state.fading ~= '' then state.fading = '' publish() end
end

local function fade_set(frac)
  if fade.base == nil then fade.base = mp.get_property_number('volume-gain', 0) end
  frac = math.max(0.01, math.min(1, frac))
  mp.set_property_number('volume-gain', math.max(-60, fade.base + 20 * math.log10(frac)))
end

local function fade_tick()
  if opts.fade <= 0 or not is_music() then fade_restore() return end
  local left = mp.get_property_number('time-remaining')
  local pos = mp.get_property_number('time-pos')
  local speed = mp.get_property_number('speed', 1)
  local fin = opts.fade / 2
  if left and left / speed <= opts.fade and (mp.get_property_number('duration', 0) >= opts.fade * 2) then
    fade.mode = 'out'
    if state.fading ~= 'out' then state.fading = 'out' publish() end
    fade_set(left / speed / opts.fade)
  elseif pos and pos / speed < fin and fade.mode == 'in' then
    fade_set(pos / speed / fin)
  elseif fade.mode ~= '' then
    fade_restore()
  end
end

local function fade_arm(starting)
  if fade.watch then fade.watch:kill() fade.watch = nil end
  if opts.fade <= 0 or not is_music() then fade_restore() return end
  if starting and mp.get_property_number('duration', 0) >= opts.fade * 2 then
    fade.mode = 'in'
    state.fading = 'in'
    fade_set(0.01)
    publish()
  end
  -- 20 Hz only while fading, 4 Hz otherwise (OSD/overlay budget of the project)
  fade.watch = mp.add_periodic_timer(0.05, function()
    if mp.get_property_bool('pause') then return end
    local left = mp.get_property_number('time-remaining') or math.huge
    if fade.mode == '' and left > opts.fade + 0.5 then
      if fade.watch.timeout ~= 0.25 then fade.watch:kill() fade.watch.timeout = 0.25 fade.watch:resume() end
      return
    end
    if fade.watch.timeout ~= 0.05 then fade.watch:kill() fade.watch.timeout = 0.05 fade.watch:resume() end
    fade_tick()
  end)
end

-- ---------------------------------------------------------------------------------------------
-- local history: a track counts once half of it (or history_seconds) has really been played

local listen_timer = nil

local function listen_reset(path)
  state.listen = { path = path or '', acc = 0, done = false, last = mp.get_time() }
end

local function listen_tick()
  local l = state.listen
  local now = mp.get_time()
  local dt = now - (l.last or now)
  l.last = now
  if l.done or l.path == '' or mp.get_property_bool('pause') or mp.get_property_bool('seeking') then return end
  l.acc = l.acc + math.min(dt, 2)
  local dur = mp.get_property_number('duration', 0)
  local need = math.min(dur > 0 and dur * 0.5 or opts.history_seconds, opts.history_seconds)
  if l.acc >= need and dur > 0 then
    l.done = true
    local meta = mp.get_property_native('metadata') or {}
    rpc.call('music.played', { path = l.path, seconds = l.acc, title = meta.title or meta.TITLE or '',
                               artist = meta.artist or meta.ARTIST or '', album = meta.album or meta.ALBUM or '' },
      function(err) if err then fail(err, 'music.played') end end, 15)
    publish()
  end
end

-- ---------------------------------------------------------------------------------------------
-- file events

mp.register_event('file-loaded', function()
  local here = current_path()
  if listen_timer then listen_timer:kill() listen_timer = nil end
  if not is_music() then
    if fade.watch then fade.watch:kill() fade.watch = nil end
    fade_restore()
    listen_reset('')
    state.gain = { source = 'none', db = nil, path = here }
    publish()
    return
  end
  listen_reset(here)
  if rpc.connected() then listen_timer = mp.add_periodic_timer(1, listen_tick) end
  fade_arm(true)
  if opts.replaygain ~= 'no' then
    if state.gains[here] then apply_gain() prefetch_next()
    else fetch_gain(here, function() if current_path() == here then apply_gain() end prefetch_next() end) end
  else
    apply_gain()
  end
end)

mp.register_event('end-file', function()
  if listen_timer then listen_timer:kill() listen_timer = nil end
  if fade.mode == 'in' then fade_restore() end
end)

-- Redrawing «Cola» costs one music.describe with the whole queue, so «Vaciar la cola» (one playlist-remove per track)
-- used to fire hundreds of them in a row. Coalesce the bursts, and follow the playing track too (its position moves
-- without the count changing, and the row numbers of «mover» are read from it).
local queue_timer = nil
local function queue_changed()
  if queue_timer then queue_timer:kill() end
  queue_timer = mp.add_timeout(0.2, function()
    queue_timer = nil
    if still('queue') and uosc.open_type() == MENU then reopen_current() end
  end)
end

mp.observe_property('playlist-count', 'number', queue_changed)
mp.observe_property('playlist-pos', 'number', queue_changed)

-- ---------------------------------------------------------------------------------------------
-- events from mpvd: scan / ReplayGain jobs

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'job' or type(ev.job) ~= 'table' then return end
  local j = ev.job
  if j.name == 'music.scan' then
    state.scanning = j.status == 'queued' or j.status == 'running'
    state.scan_progress = tonumber(j.progress) or 0
    publish()
    if j.status == 'done' then
      local r = j.result or {}
      osd(string.format('🎵 Música al día (%d nuevas, %d quitadas)', r.added or 0, r.removed or 0))
      if uosc.open_type() == MENU and (still('root') or still('folders') or still('artists') or still('albums')) then
        reopen_current()
      end
    elseif j.status == 'failed' then
      osd(tr('Música: %s'):format(j.error or tr('error')), 5)
    end
  elseif j.name == 'music.replaygain' and j.status == 'done' then
    state.gains = {}                -- computed album gains may have changed
    if is_music() and opts.replaygain ~= 'no' then
      local here = current_path()
      fetch_gain(here, function() if current_path() == here then apply_gain() end end)
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- bindings and script messages

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

local function open_sub(view)
  return function()
    if not uosc.available() then osd(tr('uosc no está cargado')) return end
    state.stack = { { name = 'root', title = ROOT_TITLE } }
    state.force_open = uosc.open_type() ~= MENU
    open_view({ name = view })
  end
end

N:binding('music-menu', open_root)
N:binding('music-queue', open_sub('queue'))
N:binding('music-lists', open_sub('lists'))
mp.add_key_binding(nil, 'music-search', function()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  state.view = 'root'
  open_input('search', '')
end)
mp.add_key_binding(nil, 'music-add-to-list', function()
  local here = current_path()
  if not is_local(here) then osd(tr('Solo archivos de tu equipo')) return end
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  state.pick = { here }
  state.force_open = true
  open_view({ name = 'pick' })
end)
-- K3 · «Volumen parejo» también se ofrece en Imagen y sonido, que es donde se busca viendo una película; el ajuste
-- sigue siendo este, para no tener dos verdades
mp.register_script_message('mu-music-replaygain', function(mode)
  if not RG_MODES[mode] then return end
  set_pref('replaygain', mode)
  apply_audio_settings()
  apply_gain()
  osd(tr('Volumen igualado: %s'):format(RG_LABEL[mode]))
end)
mp.register_script_message('mu-music-open', open_root)
mp.register_script_message('mu-music-play-next', function(path) if path then play_next({ path }) end end)
mp.register_script_message('mu-music-queue', function(path) if path then enqueue({ path }) end end)

P:on_change(function(reason)
  if reason ~= 'reset' then return end
  for _, k in ipairs({ 'gapless', 'fade', 'replaygain', 'exclusive' }) do opts[k] = P:get(k) end
  apply_audio_settings()
  publish()
end)

-- mpv options only when they differ from mpv's own defaults (nothing is touched for users who never open Música)
if opts.gapless or opts.replaygain ~= 'no' or opts.exclusive then apply_audio_settings() end
publish()
msg.info('mu-music loaded')
