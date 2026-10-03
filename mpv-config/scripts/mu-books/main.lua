-- mu-books: audiobooks and podcasts (H32, ADR-065). mpvd (books.*) decides what is a book (m4b, a folder of chapters,
-- more than an hour, genre Audiobook/Podcast, or the user's choice) and keeps, per book, the position (track + time),
-- the speed and bookmarks with a note. This script:
--   · resumes a book where it was left (also in another track of a folder book) and applies its speed as a
--     file-local option (mpv restores the normal speed after the file: mu-prefs never learns a book's speed);
--   · saves the position every `save_seconds` while playing, on pause, after a seek and when the file is unloaded;
--   · menu «Audiolibros y podcasts»: chapters (the file's own or one per track), bookmarks (add with a note, jump,
--     delete), speed, ±30 s, «Seguir escuchando» and the sleep timer;
--   · sleep timer (any playback, not only books): 15/30/45/60 min of playback or «al terminar el capítulo»; at the
--     end the volume goes down little by little (`fade_seconds`) and playback pauses (volume restored).
-- Bindings: books-menu, sleep-menu, back-30, forward-30, bookmark, sleep-cycle. Script name: mu_books. State: user-data/mu/books.
-- Messages (tests, other scripts): mu-books-sleep <min|chapter|off>, mu-books-bookmark [note], mu-books-speed <x>,
-- mu-books-skip <seconds>.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local tr = require('mu.i18n').t
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-books'
local EVENT = 'mu-books-event'
local INPUT = 'mu-books-input'
local INPUT_EVENT = 'mu-books-input-event'
local ROOT_TITLE = 'Audiolibros y podcasts'

local opts = {
  save_seconds = 10,        -- periodic save while playing
  resume_min = 5,           -- do not resume positions closer than this to the start
  skip_seconds = 30,
  fade_seconds = 20,        -- sleep timer: how long the volume takes to go down
  minute_seconds = 60,      -- length of a timer "minute" (tests use a fraction of a second)
  osd_seconds = 3,
}
options.read_options(opts, 'mu-books')

local SPEEDS = { 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0 }
local SLEEP_MINUTES = { 15, 30, 45, 60 }

local state = {
  book = nil,            -- books.open result for the current file (nil: not a book)
  prev_id = nil,         -- book of the previous file: moving between tracks of the same book never jumps
  pending_seek = nil,    -- {path, time}: seek after the next file-loaded (resume in another track)
  last_saved = nil,
  view = '', stack = {}, items = {}, force_open = false, input = nil, last_error = '',
  sleep = nil,           -- {mode='minutes'|'chapter', remaining, fading, vol0, minutes}
}

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function clock(t)
  t = math.max(0, math.floor(tonumber(t) or 0))
  local h, m, s = math.floor(t / 3600), math.floor(t / 60) % 60, t % 60
  if h > 0 then return string.format('%d:%02d:%02d', h, m, s) end
  return string.format('%d:%02d', m, s)
end

local function basename(p) return (tostring(p or ''):match('[^/\\]+$')) or tostring(p or '') end
local function is_url(p) return type(p) == 'string' and p:find('^%a[%w+.-]*://') ~= nil and not p:find('^file://') end

local function abs_path()
  local path = mp.get_property('path')
  if not path or path == '' then return nil end
  if is_url(path) then return path end
  return mp.command_native({ 'normalize-path', path }) or path
end

local function sleep_public()
  local s = state.sleep
  if not s then return nil end
  return { mode = s.mode, remaining = math.floor((s.remaining or 0) * 10 + 0.5) / 10, fading = s.fading == true,
           minutes = s.minutes }
end

local function publish()
  local b = state.book
  mp.set_property_native('user-data/mu/books', {
    book = b and { id = b.id, kind = b.kind, title = b.title, reason = b.reason, folder = b.tracks ~= nil,
                   track_index = b.track_index, tracks = b.tracks and #b.tracks or 0 } or nil,
    speed = mp.get_property_number('speed'), bookmarks = b and #(b.bookmarks or {}) or 0,
    last_saved = state.last_saved, sleep = sleep_public(), view = state.view, depth = #state.stack,
    items = state.items, input = state.input and state.input.mode or '', last_error = state.last_error,
  })
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

-- ---------------------------------------------------------------------------------------------
-- book detection, resume, speed, saving

local function audio_only()
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'video' and not t.albumart and not t.image then return false end
  end
  return true
end

local function current_track()
  local b = state.book
  if b and b.tracks then return basename(mp.get_property('path') or '') end
  return ''
end

local function book_fields(b)
  return { title = b.title or '', kind = b.kind or 'book', path = b.path or '', show = b.show or '',
            author = b.author or '' }
end

local function save(reason, finished)
  local b = state.book
  if not b or not rpc.connected() then return end
  local t = mp.get_property_number('time-pos')
  if not t then return end
  local params = book_fields(b)
  params.id, params.track, params.time = b.id, current_track(), t
  params.speed = mp.get_property_number('speed')
  params.finished = finished == true
  rpc.call('books.save', params, function(err, res)
    if err then fail(err, 'books.save') return end
    state.last_saved = { reason = reason, track = res.track, time = res.time, speed = res.speed,
                         finished = res.finished }
    publish()
  end, 10)
end

local function set_book_speed(speed)
  -- file-local: mpv puts the normal speed back when the file ends, and mu-prefs ignores options set locally
  mp.set_property_native('file-local-options/speed', speed)
end

local function resume(b)
  local pos = b.position
  if state.prev_id == b.id or not pos then return end
  if b.tracks and pos.index ~= nil and pos.index ~= b.track_index then
    local target = b.tracks[pos.index + 1]
    local list = mp.get_property_native('playlist') or {}
    for i, e in ipairs(list) do
      if basename(e.filename) == target.file then
        state.pending_seek = { file = target.file, time = pos.time }
        mp.commandv('playlist-play-index', tostring(i - 1))
        osd(string.format('📖 Sigues en «%s» · %s', target.title, clock(pos.time)))
        return
      end
    end
    return
  end
  local now = mp.get_property_number('time-pos') or 0
  if pos.time >= opts.resume_min and math.abs(now - pos.time) > 2 then
    mp.commandv('seek', tostring(pos.time), 'absolute+exact')
    osd(tr('📖 Sigues donde lo dejaste: %s'):format(clock(pos.time)))
  end
end

-- a folder book opened from one track: put the other tracks around it in the playlist, in the book's order
local function complete_playlist(b)
  if not b.tracks or #b.tracks < 2 then return end
  if (mp.get_property_number('playlist-count') or 1) > 1 then return end
  local here = b.track_index or 0
  for i, t in ipairs(b.tracks) do
    if i - 1 < here then
      mp.commandv('loadfile', t.path, 'insert-at', tostring(i - 1))
    elseif i - 1 > here then
      mp.commandv('loadfile', t.path, 'append')
    end
  end
end

local function on_book(b)
  state.book = b
  local speed = tonumber(b.speed) or mp.get_property_number('speed') or 1
  set_book_speed(speed)
  complete_playlist(b)
  local ps = state.pending_seek
  state.pending_seek = nil
  if ps and b.tracks and basename(mp.get_property('path') or '') == ps.file then
    if ps.time >= opts.resume_min then mp.commandv('seek', tostring(ps.time), 'absolute+exact') end
  else
    resume(b)
  end
  publish()
end

local checked = nil     -- path asked to mpvd (a file opened before mpvd connected is asked once it does)
local function check_file()
  state.book = nil
  publish()
  local path = abs_path()
  if not path or not audio_only() or not rpc.connected() then return end
  checked = path
  local meta = mp.get_property_native('metadata') or {}
  local want = path
  rpc.call('books.open', { path = path, meta = meta, duration = mp.get_property_number('duration') },
    function(err, res)
      if abs_path() ~= want then return end
      if err then fail(err, 'books.open') return end
      if res and res.is_book then on_book(res) else publish() end
    end, 30)
end

mp.register_event('file-loaded', check_file)
mp.register_event('start-file', function()
  state.prev_id = state.book and state.book.id or nil
  state.book, checked = nil, nil
  publish()
end)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if type(core) == 'table' and core.mpvd == 'connected' and not checked and not state.book
      and mp.get_property_native('idle-active') == false and mp.get_property_number('duration') then
    check_file()
  end
end)

mp.add_hook('on_unload', 50, function()
  local b = state.book
  if not b then return end
  -- finished: the end of the last track (the last 30 s of a long book, the last 2 % of a short one)
  local remaining = mp.get_property_number('time-remaining')
  local tail = math.min(30, (mp.get_property_number('duration') or 0) * 0.02)
  local last = not b.tracks or (b.track_index or 0) == #b.tracks - 1
  save('unload', last and ((remaining and remaining < tail) or mp.get_property_native('eof-reached') == true))
end)

mp.observe_property('pause', 'bool', function(_, paused) if paused then save('pause') end end)
mp.register_event('seek', function()
  if state.book then mp.add_timeout(1, function() save('seek') end) end
end)
mp.add_periodic_timer(opts.save_seconds, function()
  if state.book and not mp.get_property_native('pause') then save('timer') end
end)
mp.observe_property('speed', 'number', function()
  if state.book then publish() end
end)

-- ---------------------------------------------------------------------------------------------
-- sleep timer

local sleep_timer = nil
local TICK = 0.25

local function chapter_left()
  local pos = mp.get_property_number('time-pos')
  if not pos then return nil end
  local stop = mp.get_property_number('duration')
  for _, c in ipairs(mp.get_property_native('chapter-list') or {}) do
    if c.time > pos + 0.05 then stop = c.time break end
  end
  if not stop then return nil end
  return (stop - pos) / (mp.get_property_number('speed') or 1)
end

local function stop_sleep(quiet)
  local s = state.sleep
  if sleep_timer then sleep_timer:kill(); sleep_timer = nil end
  state.sleep = nil
  if s and s.fading and s.vol0 then mp.set_property_number('volume', s.vol0) end
  if not quiet then osd(tr('⏰ Temporizador desactivado')) end
  publish()
end

local function sleep_finish()
  local s = state.sleep
  mp.set_property_native('pause', true)
  if s and s.vol0 then mp.set_property_number('volume', s.vol0) end
  if sleep_timer then sleep_timer:kill(); sleep_timer = nil end
  state.sleep = nil
  osd(tr('🌙 Buenas noches: pausado'), 4)
  publish()
end

local last_tick = nil
local published_at = 0
local function sleep_tick()
  local s = state.sleep
  if not s then return end
  local now = mp.get_time()
  local dt = now - (last_tick or now)
  last_tick = now
  local playing = not mp.get_property_native('pause') and not mp.get_property_native('idle-active')
  if s.mode == 'minutes' then
    if playing then s.remaining = s.remaining - dt end
  else
    s.remaining = chapter_left() or s.remaining
  end
  if playing and s.remaining <= opts.fade_seconds then
    if not s.fading then
      s.fading = true
      s.vol0 = mp.get_property_number('volume') or 100
    end
    mp.set_property_number('volume', s.vol0 * math.max(0, s.remaining) / math.max(0.1, opts.fade_seconds))
  elseif s.fading then
    -- in chapter mode «remaining» can grow again (alt+J back 30 s, a bookmark): give the volume back instead of
    -- leaving the book playing at 10 % for the rest of the night
    s.fading = false
    if s.vol0 then mp.set_property_number('volume', s.vol0) end
  end
  -- chapter mode stops a few frames early (the tick is 0.25 s): play goes on at the start of the next chapter
  if playing and s.remaining <= (s.mode == 'chapter' and 0.3 or 0) then
    sleep_finish()
    return
  end
  if now - published_at >= 1 then
    published_at = now
    publish()
  end
end

local function start_sleep(mode, minutes)
  stop_sleep(true)
  if mode == 'off' then osd(tr('⏰ Temporizador desactivado')) return end
  state.sleep = { mode = mode, minutes = minutes, fading = false,
                  remaining = mode == 'minutes' and minutes * opts.minute_seconds or (chapter_left() or 0) }
  last_tick = mp.get_time()
  sleep_timer = mp.add_periodic_timer(TICK, sleep_tick)
  if mode == 'minutes' then
    osd(string.format('⏰ Pausa dentro de %d min', minutes))
  else
    osd(tr('⏰ Pausa al terminar el capítulo'))
  end
  publish()
end

-- the end of a track counts as the end of the chapter: the next file starts paused
mp.register_event('end-file', function(ev)
  local s = state.sleep
  if s and s.mode == 'chapter' and ev and ev.reason == 'eof' then sleep_finish() end
end)

local function sleep_arg(arg)
  arg = tostring(arg or '')
  if arg == 'off' or arg == '' then stop_sleep() return end
  if arg == 'chapter' then start_sleep('chapter') return end
  local n = tonumber(arg)
  if n and n > 0 then start_sleep('minutes', n) end
end

local function sleep_cycle()
  local s = state.sleep
  if not s then start_sleep('minutes', SLEEP_MINUTES[1]) return end
  if s.mode == 'minutes' then
    for i, m in ipairs(SLEEP_MINUTES) do
      if m == s.minutes then
        if SLEEP_MINUTES[i + 1] then start_sleep('minutes', SLEEP_MINUTES[i + 1]) else start_sleep('chapter') end
        return
      end
    end
  end
  stop_sleep()
end

-- ---------------------------------------------------------------------------------------------
-- actions

local function skip(seconds)
  if mp.get_property_native('idle-active') then return end
  mp.commandv('seek', tostring(seconds), 'relative+exact')
  osd(seconds < 0 and string.format('⏪ %d s', -seconds) or string.format('⏩ +%d s', seconds), 1)
end

local function set_speed(x)
  x = tonumber(x)
  if not x then return end
  if state.book then set_book_speed(x) else mp.set_property_number('speed', x) end
  osd(string.format('Velocidad ×%.2g', x))
  if state.book then save('speed') end
end

local reopen_current
local function add_bookmark(note, cb)
  local b = state.book
  if not b then osd(tr('Esto no es un audiolibro: los marcadores son para libros y podcasts')) return end
  local t = mp.get_property_number('time-pos') or 0
  local params = book_fields(b)
  params.id, params.track, params.time, params.note = b.id, current_track(), t, note or ''
  rpc.call('books.bookmark.add', params, function(err, list)
    if err then osd(tr('Marcador: %s'):format(fail(err, 'books.bookmark.add'))) return end
    b.bookmarks = list or {}
    osd(tr('🔖 Marcador en %s%s'):format(clock(t), (note and note ~= '') and (' · ' .. note) or ''))
    publish()
    if cb then cb() end
  end, 10)
end

local function play_track(index, time)
  local b = state.book
  if not b or not b.tracks then return end
  local target = b.tracks[index + 1]
  if not target then return end
  if index == b.track_index then
    mp.commandv('seek', tostring(time or 0), 'absolute+exact')
    return
  end
  local list = mp.get_property_native('playlist') or {}
  for i, e in ipairs(list) do
    if basename(e.filename) == target.file then
      state.pending_seek = { file = target.file, time = time or 0 }
      if (time or 0) < opts.resume_min then state.pending_seek.time = 0 end
      mp.commandv('playlist-play-index', tostring(i - 1))
      return
    end
  end
  mp.commandv('loadfile', target.path, 'replace', '-1', 'start=' .. string.format('%.1f', time or 0))
end

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 300 then break end
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

local function sleep_hint()
  local s = state.sleep
  if not s then return 'no' end
  if s.mode == 'chapter' then return 'al terminar el capítulo' end
  return string.format('quedan %d min', math.ceil((s.remaining or 0) / opts.minute_seconds))
end

local function position_hint(b)
  local t = mp.get_property_number('time-pos') or 0
  if b.tracks then return string.format('pista %d/%d · %s', (b.track_index or 0) + 1, #b.tracks, clock(t)) end
  local ch = mp.get_property_number('chapter')
  local n = mp.get_property_number('chapter-list/count') or 0
  if ch and ch >= 0 and n > 0 then return string.format('cap. %d/%d · %s', ch + 1, n, clock(t)) end
  return clock(t) .. ' / ' .. clock(mp.get_property_number('duration'))
end

views.root = function()
  local items = {}
  local b = state.book
  if b then
    table.insert(items, { title = (b.kind == 'podcast' and '🎙 ' or '📖 ') .. (b.title or ''), hint = position_hint(b),
                          selectable = false, muted = true })
    table.insert(items, { title = b.tracks and 'Capítulos (pistas)' or 'Capítulos', icon = 'list',
                          value = { view = 'chapters' } })
    table.insert(items, { title = tr('Marcadores'), icon = 'bookmarks', hint = tostring(#(b.bookmarks or {})),
                          value = { view = 'bookmarks' } })
    table.insert(items, { title = tr('Añadir marcador aquí…'), icon = 'bookmark_add', value = { bookmark = true } })
    table.insert(items, { title = tr('Velocidad de este libro'), icon = 'speed',
                          hint = string.format('×%.2g', mp.get_property_number('speed') or 1), value = { view = 'speed' } })
    table.insert(items, { title = tr('Atrás 30 s'), icon = 'replay_30', hint = 'alt+J', value = { skip = -opts.skip_seconds },
                          keep_open = true })
    table.insert(items, { title = tr('Adelante 30 s'), icon = 'forward_30', hint = 'alt+L', value = { skip = opts.skip_seconds },
                          keep_open = true, separator = true })
  end
  table.insert(items, { title = tr('Temporizador de apagado'), icon = 'bedtime', hint = sleep_hint(),
                        value = { view = 'sleep' } })
  table.insert(items, { title = tr('Seguir escuchando'), icon = 'auto_stories', value = { view = 'list' }, separator = true })
  local path = abs_path()
  if b then
    table.insert(items, { title = b.tracks and 'Esta carpeta no es un audiolibro' or 'Esto no es un audiolibro',
                          icon = 'music_note', value = { mark = false, folder = b.tracks ~= nil } })
  elseif path and audio_only() and not mp.get_property_native('idle-active') then
    table.insert(items, { title = tr('Tratar este archivo como audiolibro'), icon = 'menu_book', value = { mark = true } })
    if not is_url(path) then
      table.insert(items, { title = tr('Tratar toda la carpeta como un audiolibro'), icon = 'library_books',
                            value = { mark = true, folder = true } })
    end
  end
  show(ROOT_TITLE, items, { footnote = tr('Enter elige · ⌫ atrás') })
end

views.chapters = function()
  local b = state.book
  local title = tr('Capítulos')
  local items = {}
  if b and b.tracks then
    for i, t in ipairs(b.tracks) do
      table.insert(items, { title = t.title, hint = clock(t.duration), active = (i - 1) == b.track_index,
                            value = { track = i - 1 } })
    end
  else
    local cur = mp.get_property_number('chapter') or -1
    for i, c in ipairs(mp.get_property_native('chapter-list') or {}) do
      table.insert(items, { title = (c.title and c.title ~= '') and c.title or ('Capítulo ' .. i), hint = clock(c.time),
                            active = (i - 1) == cur, value = { seek = c.time } })
    end
  end
  if #items == 0 then items = uosc.message_items(tr('Este archivo no tiene capítulos'), 'info') end
  show(title, items, { footnote = tr('Enter salta · ⌫ atrás') })
end

views.bookmarks = function()
  local b = state.book
  local title = tr('Marcadores')
  if not b then show(title, uosc.message_items(tr('Abre un audiolibro primero'), 'info')) return end
  local items = { { title = tr('Añadir marcador aquí…'), icon = 'bookmark_add', value = { bookmark = true },
                    separator = true } }
  for _, m in ipairs(b.bookmarks or {}) do
    local where = clock(m.time)
    if b.tracks and m.track_index then where = string.format('pista %d · %s', m.track_index + 1, where) end
    table.insert(items, { title = (m.note and m.note ~= '') and m.note or 'Marcador', hint = where, icon = 'bookmark',
                          value = { jump = { track_index = m.track_index, time = m.time }, index = m.index },
                          actions = { { name = 'delete', icon = 'delete', label = tr('Borrar') } } })
  end
  if #items == 1 then
    table.insert(items, { title = tr('Todavía no hay marcadores'), icon = 'info', selectable = false, muted = true })
  end
  show(title, items, { footnote = tr('Enter salta · Tab: borrar · ⌫ atrás') })
end

views.speed = function()
  local cur = mp.get_property_number('speed') or 1
  local items = {}
  for _, x in ipairs(SPEEDS) do
    table.insert(items, { title = string.format('×%.2g', x), active = math.abs(cur - x) < 0.01, value = { speed = x } })
  end
  show(tr('Velocidad'), items, { footnote = tr('Se guarda para este libro') })
end

views.sleep = function()
  local items = {}
  local s = state.sleep
  if s then
    table.insert(items, { title = tr('Desactivar'), icon = 'alarm_off', hint = sleep_hint(), value = { sleep = 'off' },
                          separator = true })
  end
  for _, m in ipairs(SLEEP_MINUTES) do
    table.insert(items, { title = string.format('Dentro de %d minutos', m), icon = 'timer',
                          active = s and s.mode == 'minutes' and s.minutes == m or false, value = { sleep = m } })
  end
  table.insert(items, { title = tr('Al terminar el capítulo'), icon = 'last_page',
                        active = s and s.mode == 'chapter' or false, value = { sleep = 'chapter' } })
  show(tr('Temporizador de apagado'), items, { footnote = tr('Al final baja el volumen poco a poco y pausa') })
end

views.list = function()
  local title = tr('Seguir escuchando')
  if not rpc.connected() then show(title, uosc.message_items(tr('mpvd no está conectado'), 'error')) return end
  show(title, uosc.loading_items())
  rpc.call('books.list', nil, function(err, list)
    if state.view ~= 'list' then return end
    if err then show(title, uosc.message_items(fail(err, 'books.list'), 'error')) return end
    local items = {}
    for _, bk in ipairs(list or {}) do
      local hint = bk.finished and 'terminado' or clock(bk.time)
      if bk.track and bk.track ~= '' and not bk.finished then hint = bk.track .. ' · ' .. hint end
      table.insert(items, { title = bk.title, hint = hint, icon = bk.kind == 'podcast' and 'podcasts' or 'menu_book',
                            value = { open = bk.open, id = bk.id },
                            actions = { { name = 'forget', icon = 'delete', label = tr('Olvidar') } } })
    end
    if #items == 0 then
      items = uosc.message_items(tr('Aún no hay libros: abre un audiolibro o un podcast'), 'info')
    end
    show(title, items, { footnote = tr('Enter sigue escuchando · Tab: olvidar · ⌫ atrás') })
  end, 15)
end

-- ---------------------------------------------------------------------------------------------
-- text box for the bookmark note (a uosc palette whose query is the text)

local function input_menu(query)
  query = query or ''
  state.input.query = query
  local items = {}
  if query ~= '' then
    table.insert(items, { title = tr('Guardar con la nota: %s'):format(query), icon = 'check',
                          value = { save = query } })
  end
  table.insert(items, { title = tr('Guardar sin nota'), icon = 'bookmark_add', value = { save = '' } })
  return { type = INPUT, title = tr('Nota del marcador'), items = items, callback = { SCRIPT, INPUT_EVENT },
           search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
           search_suggestion = query, footnote = tr('Escribe una nota (opcional) · Enter guarda · ⌫ en vacío vuelve') }
end

local function close_input(back)
  state.input = nil
  publish()
  uosc.close(INPUT)
  if back and #state.stack > 0 then reopen_current(true) end
end

local function open_input()
  if not state.book then osd(tr('Abre un audiolibro primero')) return end
  state.input = { mode = 'bookmark', query = '', at = mp.get_property_number('time-pos') }
  publish()
  uosc.open(input_menu(''))
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if not state.input then return end
  if ev.type == 'search' then
    uosc.update(input_menu(ev.query or ''))
  elseif ev.type == 'back' then
    close_input(true)
  elseif ev.type == 'close' then
    state.input = nil
    publish()
  elseif ev.type == 'activate' and type(ev.value) == 'table' and ev.value.save ~= nil then
    add_bookmark(ev.value.save, function() close_input(true) end)
  end
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

local function mark(value, folder)
  local path = abs_path()
  if not path then return end
  rpc.call('books.mark', { path = path, value = value, folder = folder == true }, function(err)
    if err then osd(tr('Audiolibro: %s'):format(fail(err, 'books.mark'))) return end
    osd(value and '📖 Se tratará como audiolibro' or '🎵 No se tratará como audiolibro')
    check_file()
    mp.add_timeout(0.5, function() if uosc.open_type() == MENU then reopen_current() end end)
  end, 15)
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.view then
      open_view({ name = v.view, args = v })
    elseif v.skip then
      skip(v.skip)
    elseif v.bookmark then
      open_input()
    elseif v.speed then
      set_speed(v.speed)
      reopen_current()
    elseif v.sleep ~= nil then
      sleep_arg(v.sleep)
      table.remove(state.stack)
      reopen_current()
    elseif v.track then
      play_track(v.track, 0)
      uosc.close(MENU)
    elseif v.seek then
      mp.commandv('seek', tostring(v.seek), 'absolute+exact')
      uosc.close(MENU)
    elseif v.jump then
      if ev.action == 'delete' then
        rpc.call('books.bookmark.delete', { id = state.book and state.book.id or '', index = v.index },
          function(err, list)
            if err then osd(tr('Borrar: %s'):format(fail(err, 'books.bookmark.delete'))) return end
            if state.book then state.book.bookmarks = list or {} end
            osd(tr('🗑 Marcador borrado'))
            reopen_current()
          end, 10)
        return
      end
      local b = state.book
      if b and b.tracks and v.jump.track_index then
        play_track(v.jump.track_index, v.jump.time)
      else
        mp.commandv('seek', tostring(v.jump.time or 0), 'absolute+exact')
      end
      osd('🔖 ' .. clock(v.jump.time))
      uosc.close(MENU)
    elseif v.open then
      if ev.action == 'forget' then
        rpc.call('books.forget', { id = v.id }, function(err)
          if err then osd(tr('Olvidar: %s'):format(fail(err, 'books.forget'))) return end
          reopen_current()
        end, 10)
        return
      end
      uosc.close(MENU)
      mp.commandv('loadfile', v.open, 'replace')
    elseif v.mark ~= nil then
      mark(v.mark, v.folder)
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
    if #state.stack > 0 or state.view ~= '' or state.input then
      state.stack, state.view, state.input = {}, '', nil
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- bindings and messages

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

N:binding('books-menu', open_root)
-- K3 · el temporizador vale para cualquier reproducción, no solo para un audiolibro, así que tiene puerta propia:
-- escondido dentro del menú de audiolibros nadie lo encuentra viendo una película
N:binding('sleep-menu', function()
  state.stack = {}
  open_view({ name = 'sleep' })
end)
mp.add_key_binding(nil, 'back-30', function() skip(-opts.skip_seconds) end, { repeatable = true })
mp.add_key_binding(nil, 'forward-30', function() skip(opts.skip_seconds) end, { repeatable = true })
mp.add_key_binding(nil, 'bookmark', function() add_bookmark('') end)
mp.add_key_binding(nil, 'sleep-cycle', sleep_cycle)
mp.register_script_message('mu-books-sleep', sleep_arg)
mp.register_script_message('mu-books-bookmark', function(note) add_bookmark(note or '') end)
mp.register_script_message('mu-books-speed', set_speed)
mp.register_script_message('mu-books-skip', function(s) if tonumber(s) then skip(tonumber(s)) end end)

publish()
msg.info('mu-books loaded')
