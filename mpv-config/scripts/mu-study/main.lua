-- mu-study: study tools. Repeat the current subtitle line (ab-loop over sub-start/sub-end + sub-delay, verified in
-- docs/ESTUDIO.md), smart speed (faster over silences mapped by mpvd study.silences), quick notes with time links
-- (mpvd notes.add) and clip/GIF export of the A-B loop or the current line (mpvd study.clip). Script name: mu_study.
-- State in user-data/mu/study. Menu type 'mu-study'.
-- Preferences (mu/prefs.lua, namespace mu-study): smart speed on/off (re-armed on the next local file) and the speed
-- used inside silences, saved only on explicit user actions.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')

local SCRIPT = mp.get_script_name()
local MENU = 'mu-study'
local EVENT = 'mu-study-menu-event'
local NOTE_MENU = 'mu-study-note'
local NOTE_EVENT = 'mu-study-note-event'

local opts = {
  silence_speed = 2.5,      -- playback speed inside silences when smart speed is on
  silence_db = -30,         -- silencedetect threshold (dB); -30/0.5 catches pauses between sentences
  silence_min = 0.5,        -- minimum silence length (s)
  silence_window = 600,     -- seconds of silence map requested ahead of time-pos
  poll_seconds = 0.2,       -- smart speed timer
  clip_format = 'mp4',      -- default clip format (study.formats)
  clip_dir = '',            -- '' = <Videos|Music>/MPV-UOS/clips
  osd_seconds = 3,
}
options.read_options(opts, 'mu-study')

local SILENCE_SPEEDS = { 1.5, 2, 2.5, 3, 4 }
local P = prefs.ns('mu-study', { smart = false, silence_speed = opts.silence_speed }, function(key, v)
  if key == 'silence_speed' then return v >= 1 and v <= 8 end
  return true
end)
P:apply_opts(opts, 'mu-study', { 'silence_speed' })

local state = {
  repeat_line = false, line = nil,                 -- {start, end, text} of the repeated cue (with sub-delay applied)
  smart = false, base_speed = 1.0, in_silence = false, silences = {}, silence_from = -1, silence_to = -1,
  silence_pending = false, silence_stats = nil,
  clips = {}, formats = nil, last_clip = nil, last_note = nil, notes = 0, view = '', last_error = '',
  smart_wanted = P:get('smart'),                  -- user's choice: re-armed on every local file
}

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function publish()
  mp.set_property_native('user-data/mu/study', {
    repeat_line = state.repeat_line, line = state.line or {}, smart = state.smart, base_speed = state.base_speed,
    in_silence = state.in_silence, silences = #state.silences, silence_from = state.silence_from, silence_to = state.silence_to,
    smart_wanted = state.smart_wanted, silence_speed = opts.silence_speed,
    clips = state.clips, last_clip = state.last_clip or {}, last_note = state.last_note or {}, notes = state.notes,
    view = state.view, last_error = state.last_error,
  })
end

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

local function current_path()
  local p = mp.get_property('path') or ''
  return (p:gsub('^file://', ''))
end

local function is_local(path)
  if not path or path == '' then return false end
  return path:match('^%a[%w+.-]*://') == nil
end

-- ---------------------------------------------------------------------------------------------
-- repeat line: ab-loop over the cue on screen (sub-start/sub-end do not include sub-delay: add it)

local function current_line()
  local s = mp.get_property_number('sub-start')
  local e = mp.get_property_number('sub-end')
  if s == nil or e == nil or e <= s then return nil end
  local delay = mp.get_property_number('sub-delay') or 0
  return { start = s + delay, ['end'] = e + delay, text = mp.get_property('sub-text') or '' }
end

local function set_ab(a, b)
  if a == nil then
    mp.set_property('ab-loop-a', 'no')
    mp.set_property('ab-loop-b', 'no')
  else
    mp.set_property_number('ab-loop-a', a)
    mp.set_property_number('ab-loop-b', b)
  end
end

local function repeat_stop(quiet)
  if state.repeat_line then set_ab(nil) end
  state.repeat_line = false
  state.line = nil
  publish()
  if not quiet then osd('Repetir línea: desactivado') end
end

local function repeat_toggle()
  if state.repeat_line then repeat_stop() return end
  local line = current_line()
  if not line then osd('No hay subtítulo en pantalla (activa una pista o usa alt+c)') return end
  state.repeat_line = true
  state.line = line
  set_ab(line.start, line['end'])
  mp.commandv('seek', tostring(line.start), 'absolute+exact')
  publish()
  osd('🔁 Repitiendo: ' .. (line.text:gsub('\n', ' ')))
end

local function repeat_step(dir)
  -- move the loop to the previous/next cue (sub-seek jumps to it; the new cue becomes current a moment later)
  if not state.repeat_line then repeat_toggle() return end
  set_ab(nil)
  mp.commandv('sub-seek', tostring(dir))
  mp.add_timeout(0.25, function()
    local line = current_line()
    if line then
      state.line = line
      set_ab(line.start, line['end'])
      publish()
      osd('🔁 ' .. (line.text:gsub('\n', ' ')))
    else
      repeat_stop(true)
    end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- smart speed: mpvd maps silences ahead of time-pos; the timer speeds up inside them and restores outside

local function in_silence_at(t)
  for _, s in ipairs(state.silences) do
    if t >= s[1] and t < s[2] then return true end
  end
  return false
end

local function request_silences(from)
  if state.silence_pending or not rpc.connected() then return end
  local path = current_path()
  if not is_local(path) then return end
  state.silence_pending = true
  local start = math.max(0, from - 5)
  rpc.call('study.silences', { path = path, start = start, length = opts.silence_window, noise_db = opts.silence_db,
    min_seconds = opts.silence_min }, function(err, res)
    state.silence_pending = false
    if err then fail(err, 'study.silences'); return end
    state.silences = res.silences or {}
    state.silence_from = start
    state.silence_to = start + (res.length or opts.silence_window)
    state.silence_stats = { spans = res.spans, quiet_ratio = res.quiet_ratio }
    publish()
  end, 180)
end

local function restore_speed()
  if state.in_silence then
    state.in_silence = false
    mp.set_property_number('speed', state.base_speed)
  end
end

local function smart_tick()
  if not state.smart then return end
  local pos = mp.get_property_number('time-pos')
  if pos == nil then return end
  if pos < state.silence_from or pos > state.silence_to - 30 then
    if pos < state.silence_from or pos >= state.silence_to then
      state.silences = {}
    end
    request_silences(pos)
  end
  local quiet = in_silence_at(pos)
  if quiet and not state.in_silence then
    state.base_speed = mp.get_property_number('speed') or 1.0
    state.in_silence = true
    publish()   -- before touching speed: mu-prefs ignores speed changes while in_silence
    mp.set_property_number('speed', opts.silence_speed)
  elseif not quiet and state.in_silence then
    restore_speed()
    publish()
  end
end

local smart_timer = mp.add_periodic_timer(opts.poll_seconds, smart_tick)
smart_timer:kill()

local function smart_set(on, quiet)
  if on == state.smart then return end
  state.smart = on
  if on then
    if not is_local(current_path()) then
      if not quiet then osd('Velocidad inteligente: solo archivos locales') end
      state.smart = false
      return
    end
    state.base_speed = mp.get_property_number('speed') or 1.0
    state.silences = {}
    state.silence_from, state.silence_to = -1, -1
    smart_timer:resume()
    request_silences(mp.get_property_number('time-pos') or 0)
    if not quiet then osd(string.format('⏩ Velocidad inteligente: ×%.1f en silencios', opts.silence_speed)) end
  else
    smart_timer:kill()
    restore_speed()
    if not quiet then osd('Velocidad inteligente: desactivada') end
  end
  publish()
end

-- explicit user action: apply and remember (turning it on only counts once it really started, or with nothing open)
local function user_smart(on)
  if on and (mp.get_property('path') or '') == '' then
    state.smart_wanted = true
    P:set('smart', true)
    osd('⏩ Velocidad inteligente: se activará al abrir un archivo local')
    publish()
    return
  end
  local was_active = state.smart
  smart_set(on)
  if not on or state.smart then
    if not on and not was_active and state.smart_wanted then osd('Velocidad inteligente: desactivada') end
    state.smart_wanted = on
    P:set('smart', on)
  end
  publish()
end

local function user_smart_toggle() user_smart(not (state.smart or state.smart_wanted)) end

local function set_silence_speed(x)
  x = tonumber(x)
  if not x or x < 1 or x > 8 then return end
  opts.silence_speed = x
  P:set('silence_speed', x)
  if state.in_silence then mp.set_property_number('speed', x) end
  publish()
  osd(string.format('Velocidad en silencios: ×%g', x))
end

-- ---------------------------------------------------------------------------------------------
-- notes (mpvd notes.add: Markdown with time links) — the uosc palette search box is the text input

local function add_note(text)
  if not rpc.connected() then osd('mpvd no está conectado') return end
  local line = current_line()
  local quote = line and line.text ~= '' and ('«' .. line.text:gsub('\n', ' ') .. '»') or ''
  local body = text and text ~= '' and text or ''
  if body ~= '' and quote ~= '' then body = body .. ' ' .. quote elseif body == '' then body = quote end
  if body == '' then osd('Nota vacía: escribe algo o activa subtítulos') return end
  rpc.call('notes.add', { text = body, path = current_path(), time_pos = mp.get_property_number('time-pos'),
    title = mp.get_property('media-title') }, function(err, res)
    if err then osd('Nota: ' .. fail(err, 'notes.add')); return end
    state.last_note = { file = res.file, time_pos = res.time_pos, text = body }
    state.notes = state.notes + 1
    publish()
    osd('📝 Nota guardada en ' .. (res.file:match('[^/\\]+$') or res.file))
  end, 15)
end

local note_query = ''
local function note_menu(query)
  note_query = query or ''
  local line = current_line()
  local items = {}
  if note_query ~= '' then
    table.insert(items, { title = 'Guardar: ' .. note_query, icon = 'save', value = { save = note_query } })
  end
  if line and line.text ~= '' then
    table.insert(items, { title = 'Guardar la cita del subtítulo', hint = line.text:gsub('\n', ' '), icon = 'format_quote',
      value = { save = '' } })
  end
  if #items == 0 then
    table.insert(items, { title = 'Escribe la nota y pulsa Enter', icon = 'edit', selectable = false, muted = true })
  end
  return { type = NOTE_MENU, title = 'Nota en ' .. fmt_time(mp.get_property_number('time-pos') or 0), items = items,
    callback = { SCRIPT, NOTE_EVENT }, search_style = 'palette', search_debounce = 100, on_search = 'callback',
    search_suggestion = note_query, footnote = 'Enter guarda con enlace de tiempo · ⌫ cierra' }
end

local function open_note_menu()
  if not uosc.available() then osd('uosc no está cargado') return end
  uosc.open(note_menu(''))
end

mp.register_script_message(NOTE_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type == 'search' then
    uosc.update(note_menu(ev.query or ''))
  elseif ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.save ~= nil then
      uosc.close(NOTE_MENU)
      add_note(v.save)
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- clips: A-B loop (or the repeated line) → study.clip; progress via mu-event

local function clip_range()
  local a = mp.get_property_number('ab-loop-a')
  local b = mp.get_property_number('ab-loop-b')
  if a ~= nil and b ~= nil and b > a then return a, b, 'bucle A-B' end
  if state.line then return state.line.start, state.line['end'], 'línea' end
  local line = current_line()
  if line then return line.start, line['end'], 'subtítulo' end
  return nil
end

local function export_clip(fmt)
  if not rpc.connected() then osd('mpvd no está conectado') return end
  local path = current_path()
  if not is_local(path) then osd('Clips: solo archivos locales') return end
  local a, b, what = clip_range()
  if not a then osd('Marca un tramo A-B (l) o activa un subtítulo') return end
  rpc.call('study.clip', { path = path, start = a, ['end'] = b, format = fmt or opts.clip_format,
    dir = opts.clip_dir ~= '' and opts.clip_dir or nil, title = mp.get_property('media-title') or '', notify = SCRIPT },
    function(err, item)
      if err then osd('Clip: ' .. fail(err, 'study.clip')); return end
      state.clips[item.id] = item
      state.last_clip = item
      publish()
      osd(string.format('🎬 Exportando %s %s–%s (%s)…', what, fmt_time(a), fmt_time(b), item.format))
    end, 20)
end

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'clip' or type(ev.clip) ~= 'table' then return end
  local c = ev.clip
  state.clips[c.id] = c
  state.last_clip = c
  publish()
  if c.status == 'done' then
    osd('✅ Clip listo: ' .. ((c.file or ''):match('[^/\\]+$') or c.file or ''))
  elseif c.status == 'failed' then
    osd('❌ Clip: ' .. (c.message or 'error'))
  end
  if uosc.open_type() == MENU then mp.commandv('script-message-to', SCRIPT, 'mu-study-menu-refresh') end
end)

-- ---------------------------------------------------------------------------------------------
-- menu

local function root_items()
  local items = {}
  local line = current_line()
  table.insert(items, { title = 'Repetir la línea actual', hint = state.repeat_line and 'repitiendo · alt+w' or 'alt+w',
    icon = 'repeat_one', active = state.repeat_line, value = { repeat_line = true },
    muted = (not state.repeat_line and line == nil) or nil })
  table.insert(items, { title = 'Línea anterior / siguiente en bucle', hint = 'alt+LEFT / alt+RIGHT', icon = 'swap_horiz',
    selectable = false, muted = true })
  table.insert(items, { title = 'Velocidad inteligente (acelera silencios)', icon = 'speed', active = state.smart,
    hint = (state.smart and (string.format('×%.1f', opts.silence_speed) .. (state.silence_stats and
      string.format(' · %d%% silencio', math.floor((state.silence_stats.quiet_ratio or 0) * 100 + 0.5)) or ''))
      or (state.smart_wanted and 'en el próximo archivo local' or 'alt+g')), value = { smart = true } })
  local speeds = {}
  for _, x in ipairs(SILENCE_SPEEDS) do
    table.insert(speeds, { title = string.format('×%g', x), active = math.abs(x - opts.silence_speed) < 1e-6,
      value = { silence_speed = x } })
  end
  table.insert(items, { title = 'Velocidad en silencios', hint = string.format('×%g', opts.silence_speed),
    icon = 'fast_forward', items = speeds, separator = true })
  table.insert(items, { title = 'Nota con enlace de tiempo…', hint = 'alt+b', icon = 'edit_note', value = { note = true },
    separator = true })
  local a, b, what = clip_range()
  local sub = {}
  for _, f in ipairs(state.formats or {}) do
    table.insert(sub, { title = f.label, hint = f.name, icon = f.kind == 'audio' and 'audiotrack' or 'movie',
      value = { clip = f.name } })
  end
  table.insert(items, { title = 'Exportar clip / GIF', icon = 'content_cut', items = #sub > 0 and sub or nil,
    hint = a and string.format('%s %s–%s', what, fmt_time(a), fmt_time(b)) or 'marca A-B con l',
    value = #sub == 0 and { clip = opts.clip_format } or nil })
  local recent = {}
  for _, c in pairs(state.clips) do table.insert(recent, c) end
  table.sort(recent, function(x, y) return (x.created_at or 0) > (y.created_at or 0) end)
  for i, c in ipairs(recent) do
    if i > 5 then break end
    local hint = c.status == 'running' and string.format('%d %%', math.floor((c.progress or 0) * 100)) or c.status
    table.insert(items, { title = (c.file or ''):match('[^/\\]+$') or c.title or c.id, hint = hint,
      icon = c.status == 'done' and 'check_circle' or (c.status == 'failed' and 'error' or 'hourglass_top'),
      muted = c.status ~= 'done' or nil, value = { open_clip = c.file, status = c.status } })
  end
  return items
end

local function base_menu()
  return { type = MENU, title = 'Estudio', items = root_items(), callback = { SCRIPT, EVENT }, keep_open = true,
    search_submenus = false, footnote = 'Repetir · velocidad inteligente · notas · clips' }
end

local function refresh_menu()
  if uosc.open_type() == MENU then uosc.update(base_menu()) end
end

local function open_menu()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.view = 'root'
  publish()
  if state.formats == nil and rpc.connected() then
    rpc.call('study.formats', nil, function(err, res)
      if not err then state.formats = res; refresh_menu() end
    end, 10)
  end
  if uosc.open_type() == MENU then uosc.update(base_menu()) else uosc.open(base_menu()) end
end

mp.register_script_message('mu-study-menu-refresh', refresh_menu)

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type ~= 'activate' then
    if ev.type == 'close' then state.view = ''; publish() end
    return
  end
  local v = type(ev.value) == 'table' and ev.value or {}
  if v.repeat_line then
    repeat_toggle()
    refresh_menu()
  elseif v.smart then
    user_smart_toggle()
    refresh_menu()
  elseif v.silence_speed then
    set_silence_speed(v.silence_speed)
    refresh_menu()
  elseif v.note then
    uosc.close(MENU)
    open_note_menu()
  elseif v.clip then
    export_clip(v.clip)
    refresh_menu()
  elseif v.open_clip then
    if v.status == 'done' then
      uosc.close(MENU)
      mp.commandv('loadfile', v.open_clip, 'append-play')
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- lifecycle

mp.register_event('file-loaded', function()
  state.repeat_line = false
  state.line = nil
  state.silences = {}
  state.silence_from, state.silence_to = -1, -1
  if state.smart then
    restore_speed()
    if is_local(current_path()) then request_silences(0) else smart_set(false, true) end
  elseif state.smart_wanted and is_local(current_path()) then
    smart_set(true, true)   -- remembered choice (a stream in between only pauses it)
  end
  publish()
end)

mp.register_event('end-file', function()
  restore_speed()
  state.repeat_line = false
  state.line = nil
  publish()
end)

mp.observe_property('ab-loop-a', 'native', function()
  -- the user cleared the loop (l): stop repeating. Notifications are asynchronous (a stale "no" can arrive right after we
  -- set a new loop), so trust only the current value read synchronously.
  local cur = mp.get_property('ab-loop-a')
  if state.repeat_line and (cur == nil or cur == 'no') then repeat_stop(true) end
end)

mp.add_key_binding(nil, 'study-menu', open_menu)
mp.add_key_binding(nil, 'repeat-line', repeat_toggle)
mp.add_key_binding(nil, 'repeat-prev', function() repeat_step(-1) end)
mp.add_key_binding(nil, 'repeat-next', function() repeat_step(1) end)
mp.add_key_binding(nil, 'smart-speed', user_smart_toggle)
mp.add_key_binding(nil, 'note', open_note_menu)
mp.add_key_binding(nil, 'clip', function() export_clip(opts.clip_format) end)
mp.register_script_message('mu-study-note', function(text) add_note(text or '') end)
mp.register_script_message('mu-study-clip', function(fmt) export_clip(fmt ~= '' and fmt or nil) end)
mp.register_script_message('mu-study-smart', function(on) user_smart(on ~= 'no' and on ~= 'false') end)
mp.register_script_message('mu-study-silence-speed', set_silence_speed)
mp.register_script_message('mu-study-repeat', function(on)
  if on == 'no' or on == 'false' then repeat_stop(true) elseif not state.repeat_line then repeat_toggle() end
end)

P:on_change(function(reason)
  if reason ~= 'reset' then return end
  opts.silence_speed = P:get('silence_speed')
  state.smart_wanted = false
  smart_set(false, true)
  publish()
  refresh_menu()
end)

publish()
msg.info('mu-study loaded')
