-- mu-subs: live AI subtitles for MPV-UOS. Asks mpvd (asr.*) to transcribe the current local file ahead of the playback
-- position with whisper.cpp, adds the incremental SRT as an external subtitle track (`sub-add`) and reloads it
-- (`sub-reload`) on every push event; the look-ahead cursor follows seeks (`asr.seek`). Menu "Subtítulos IA" (uosc):
-- start/stop, language, model (download on demand), automatic start, pre-subtitling of the next playlist item, status.
-- Script name: mu_subs. Bindings: subs-menu (alt+i), subs-toggle (alt+c). Commands verified against mpv 0.41
-- (`mpv --input-cmdlist`: sub-add url [flags] [title] [lang] · sub-reload [id] · sub-remove [id]).
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-subs-event'
local MENU = 'mu-subs'
local TRACK_TITLE = 'Subtítulos IA'

local opts = {
  language = 'auto',        -- ISO 639-1 code or auto
  model = 'auto',           -- whisper model name or auto (mpvd picks by hardware tier)
  auto_start = false,       -- start transcribing every local file as soon as it loads
  precompute_next = true,   -- transcribe the next playlist item at low priority while this one plays
  seek_interval = 5,        -- seconds between look-ahead cursor updates while playing
  reload_min_interval = 1,  -- seconds between two sub-reload of the same track
  notify_done = true,       -- OSD when a transcription finishes or fails
  osd_seconds = 3,
  chunk_seconds = 0,        -- 0 = mpvd default (20 s, ADR-024)
}
options.read_options(opts, 'mu-subs')

local LANGUAGES = {
  { 'auto', 'Detectar automáticamente' }, { 'es', 'Español' }, { 'en', 'Inglés' }, { 'ca', 'Catalán' }, { 'fr', 'Francés' },
  { 'de', 'Alemán' }, { 'it', 'Italiano' }, { 'pt', 'Portugués' }, { 'gl', 'Gallego' }, { 'eu', 'Euskera' },
  { 'nl', 'Neerlandés' },
  { 'pl', 'Polaco' }, { 'ru', 'Ruso' }, { 'uk', 'Ucraniano' }, { 'tr', 'Turco' }, { 'ar', 'Árabe' }, { 'hi', 'Hindi' },
  { 'zh', 'Chino' }, { 'ja', 'Japonés' }, { 'ko', 'Coreano' },
}

local state = {
  language = opts.language,
  model = opts.model,
  auto_start = opts.auto_start,
  precompute_next = opts.precompute_next,
  task = nil,             -- last asr task dict pushed by mpvd (the live one for the current file)
  path = '',              -- file the task belongs to
  srt = '',               -- external subtitle file added to mpv
  sid = nil,              -- track id of that file (may change after sub-reload; re-resolved from track-list)
  starting = false,
  precompute = nil,       -- {path=, id=, status=} for the next playlist item
  models = nil,           -- last asr.models result
  downloads = {},         -- model name -> job dict (from asr-model events)
  view = '',
  stack = {},
  items = {},
  last_event = nil,
  last_error = '',
  last_seq = -1,
  last_reload = 0,
  reload_pending = false,
  force_open = false,
  notified = {},
}

local set_button_state -- defined with the bindings
local reopen_current   -- defined with the menus

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function task_running()
  return state.task ~= nil and (state.task.status == 'queued' or state.task.status == 'running')
end

local function publish()
  local t = state.task
  mp.set_property_native('user-data/mu/subs', {
    language = state.language, model = state.model, auto_start = state.auto_start,
    precompute_next = state.precompute_next, active = task_running(), starting = state.starting,
    path = state.path, srt = state.srt, sid = state.sid or 0,
    task_id = t and t.id or '', status = t and t.status or '', progress = t and t.progress or 0,
    ahead = t and t.ahead or 0, cues = t and t.cues or 0, detected = t and t.detected or '',
    task_model = t and t.model or '', rtf = t and t.rtf or 0, seq = t and t.seq or -1, error = t and t.error or '',
    precompute_id = state.precompute and state.precompute.id or '',
    precompute_status = state.precompute and state.precompute.status or '',
    view = state.view, depth = #state.stack, items = state.items, last_event = state.last_event or '',
    last_error = state.last_error,
  })
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

-- uosc only exposes the open menu's type in user-data, so keep a compact copy of what we showed (tests/diagnostics).
local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 200 then break end
    local names = {}
    for _, a in ipairs(it.actions or {}) do table.insert(names, a.name) end
    table.insert(out, { title = it.title or '', hint = it.hint or '', icon = it.icon or '', value = it.value or '',
      active = it.active or false, actions = names })
  end
  state.items = out
end

-- ---------------------------------------------------------------------------------------------
-- current file helpers

local function is_local(path)
  if not path or path == '' then return false end
  if path:match('^file://') then return true end
  return path:match('^%a[%w+.-]*://') == nil
end

local function current_path()
  local p = mp.get_property('path') or ''
  if p:match('^file://') then p = p:gsub('^file://', '') end
  return p
end

-- ffmpeg's `-map 0:a:N` counts audio streams in demuxer order: position of the selected audio track among audio tracks
local function current_audio_index()
  local tracks = mp.get_property_native('track-list') or {}
  local n = 0
  for _, t in ipairs(tracks) do
    if t.type == 'audio' and not t.external then
      if t.selected then return n end
      n = n + 1
    end
  end
  return nil
end

local function find_track(srt)
  if not srt or srt == '' then return nil end
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'sub' and t.external and t['external-filename'] == srt then return t end
  end
  return nil
end

local function task_label(t)
  if not t then return '' end
  local parts = { t.model or '' }
  local lang = (t.detected ~= nil and t.detected ~= '') and t.detected or (t.language or '')
  if lang ~= '' then table.insert(parts, lang) end
  return table.concat(parts, ' · ')
end

-- ---------------------------------------------------------------------------------------------
-- subtitle track: add once, reload on updates (throttled)

local function add_track()
  if state.srt == '' then return end
  local t = find_track(state.srt)
  if t then
    state.sid = t.id
    mp.set_property_native('sid', t.id)
  else
    local lang = state.task and ((state.task.detected ~= '' and state.task.detected) or nil) or nil
    if state.task and state.task.language ~= 'auto' then lang = state.task.language end
    local args = { 'sub-add', state.srt, 'select', TRACK_TITLE .. ' (' .. task_label(state.task) .. ')' }
    if lang then table.insert(args, lang) end
    mp.command_native(args)
    t = find_track(state.srt)
    state.sid = t and t.id or nil
  end
  publish()
end

local reload_timer = nil
local function reload_track()
  if state.srt == '' then return end
  local now = mp.get_time()
  local wait = opts.reload_min_interval - (now - state.last_reload)
  if wait > 0 then
    if not reload_timer then
      reload_timer = mp.add_timeout(wait, function() reload_timer = nil; reload_track() end)
    end
    return
  end
  local t = find_track(state.srt)
  if not t then add_track(); return end
  state.last_reload = now
  -- sub-reload re-adds the file: the track id may change and the selection is kept by mpv
  mp.commandv('sub-reload', tostring(t.id))
  local nt = find_track(state.srt)
  state.sid = nt and nt.id or state.sid
  publish()
end

-- ---------------------------------------------------------------------------------------------
-- look-ahead cursor

local function send_seek()
  if not state.task or not task_running() then return end
  local pos = mp.get_property_number('time-pos')
  if pos == nil then return end
  rpc.call('asr.seek', { id = state.task.id, time_pos = pos }, function(err)
    if err then msg.debug('asr.seek: ' .. (err.message or '')) end
  end)
end

local seek_timer = mp.add_periodic_timer(math.max(1, opts.seek_interval), function()
  if task_running() and not mp.get_property_bool('pause', false) then send_seek() end
end)
seek_timer:kill()

local function update_timers()
  if task_running() then seek_timer:resume() else seek_timer:kill() end
end

-- ---------------------------------------------------------------------------------------------
-- start / stop / precompute

local precompute_next -- forward

local function apply_task(t, from_event)
  state.task = t
  if t.srt and t.srt ~= '' then state.srt = t.srt end
  if from_event then state.last_event = { id = t.id, status = t.status, progress = t.progress, seq = t.seq } end
  update_timers()
  publish()
end

local function start(quiet)
  if not rpc.connected() then
    if not quiet then osd('mpvd no está disponible; reintentando…') end
    mp.commandv('script-message-to', 'mu_core', 'mu-ensure')
    return false
  end
  local path = current_path()
  if path == '' then
    if not quiet then osd('No hay ningún archivo abierto') end
    return false
  end
  if not is_local(mp.get_property('path')) then
    if not quiet then osd('Los subtítulos IA solo funcionan con archivos locales por ahora') end
    return false
  end
  if state.starting then return true end
  state.starting = true
  state.path = path
  state.notified = {}
  publish()
  local params = {
    path = path, language = state.language, model = state.model ~= 'auto' and state.model or nil,
    time_pos = mp.get_property_number('time-pos') or 0, audio_track = current_audio_index(), notify = SCRIPT,
  }
  if opts.chunk_seconds > 0 then params.chunk_seconds = opts.chunk_seconds end
  rpc.call('asr.start', params, function(err, t)
    state.starting = false
    if err then
      publish()
      osd('Subtítulos IA: ' .. fail(err, 'asr.start'))
      set_button_state()
      return
    end
    if current_path() ~= path then publish(); return end -- the user moved on meanwhile
    state.last_seq = -1
    apply_task(t, false)
    add_track()
    if t.status == 'done' then
      if not quiet then osd('Subtítulos IA listos (caché): ' .. task_label(t)) end
    elseif not quiet then
      osd('Subtítulos IA: transcribiendo con ' .. task_label(t) .. '…')
    end
    set_button_state()
    if state.precompute_next then precompute_next() end
  end, 60)
  return true
end

local function stop()
  if not state.task then return end
  local id = state.task.id
  if task_running() then
    rpc.call('asr.stop', { id = id }, function(err, t)
      if err then fail(err, 'asr.stop'); return end
      apply_task(t, false)
      set_button_state()
    end)
  end
  state.task = state.task and { id = id, status = 'cancelled', progress = state.task.progress, cues = state.task.cues,
    model = state.task.model, language = state.task.language, detected = state.task.detected, seq = state.task.seq } or nil
  update_timers()
  publish()
  set_button_state()
end

local function toggle()
  if task_running() then
    stop()
    osd('Subtítulos IA detenidos (lo transcrito queda en caché)')
  else
    start(false)
  end
end

precompute_next = function()
  if not rpc.connected() then return end
  local pos = mp.get_property_number('playlist-pos') or -1
  local count = mp.get_property_number('playlist-count') or 0
  if pos < 0 or pos + 1 >= count then return end
  local nxt = mp.get_property_native('playlist/' .. (pos + 1) .. '/filename') or ''
  if not is_local(nxt) then return end
  nxt = nxt:gsub('^file://', '')
  if state.precompute and state.precompute.path == nxt and state.precompute.status ~= 'failed' then return end
  local params = { path = nxt, language = state.language, model = state.model ~= 'auto' and state.model or nil, notify = SCRIPT }
  if opts.chunk_seconds > 0 then params.chunk_seconds = opts.chunk_seconds end
  rpc.call('asr.precompute', params, function(err, t)
    if err then msg.warn('asr.precompute: ' .. (err.message or '')); return end
    state.precompute = { path = nxt, id = t.id, status = t.status }
    publish()
  end, 60)
end

-- ---------------------------------------------------------------------------------------------
-- events pushed by mpvd

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  if ev.event == 'asr' and type(ev.task) == 'table' then
    local t = ev.task
    if state.precompute and t.id == state.precompute.id then
      state.precompute.status = t.status
      publish()
      -- the pre-subtitled file became the current one: adopt it as the live task
      if t.path ~= state.path or state.task ~= nil then return end
    end
    if state.task and t.id ~= state.task.id then return end
    if not state.task and t.path ~= state.path then return end
    apply_task(t, true)
    if t.seq ~= state.last_seq then
      state.last_seq = t.seq
      reload_track()
    end
    if t.status == 'done' or t.status == 'failed' then
      if opts.notify_done and not state.notified[t.id] then
        state.notified[t.id] = true
        if t.status == 'done' then
          osd(string.format('✓ Subtítulos IA completos (%d cues, %s)', t.cues or 0, task_label(t)))
        else
          osd('✗ Subtítulos IA: ' .. (t.error or 'error'))
        end
      end
      reload_track()
    end
    set_button_state()
  elseif ev.event == 'asr-model' and type(ev.job) == 'table' then
    state.downloads[ev.model] = ev.job
    state.last_event = { model = ev.model, status = ev.job.status, progress = ev.job.progress }
    if ev.job.status == 'done' then
      state.models = nil
      if opts.notify_done then osd('Modelo ' .. ev.model .. ' descargado') end
    elseif ev.job.status == 'failed' then
      osd('No se pudo descargar el modelo ' .. ev.model .. ': ' .. (ev.job.message or ev.job.error or ''))
    end
    publish()
    if uosc.open_type() == MENU and state.view == 'models' and reopen_current then reopen_current() end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- mpv events

mp.register_event('file-loaded', function()
  local path = current_path()
  if path ~= state.path then
    -- a task pre-computed for this file is adopted by asr.start (same key/model/language → same task, from cache)
    state.task = nil
    state.srt = ''
    state.sid = nil
    state.path = ''
    state.last_seq = -1
    update_timers()
    publish()
    set_button_state()
  end
  if state.auto_start and is_local(mp.get_property('path')) then
    if rpc.connected() then start(true) else
      -- wait for mpvd, then start once
      local tries = 0
      local t
      t = mp.add_periodic_timer(1, function()
        tries = tries + 1
        if rpc.connected() then t:kill(); if current_path() == path then start(true) end
        elseif tries > 30 then t:kill() end
      end)
    end
  end
end)

mp.register_event('seek', function()
  if task_running() then send_seek() end
end)

mp.register_event('end-file', function()
  if state.task and task_running() and state.precompute_next then
    -- keep the daemon working on it at low priority instead of dropping the job with the session
    rpc.call('asr.precompute', { path = state.path, language = state.task.language, model = state.task.model,
      chunk_seconds = state.task.chunk_seconds, notify = SCRIPT })
  end
  state.task = nil
  state.srt = ''
  state.sid = nil
  state.path = ''
  update_timers()
  publish()
  set_button_state()
end)

-- ---------------------------------------------------------------------------------------------
-- menus

local function base_menu(title, items, extra)
  local menu = {
    type = MENU, title = title, items = items, callback = { SCRIPT, EVENT },
    on_close = 'callback', keep_open = false, search_submenus = false,
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

reopen_current = function()
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

local function language_name(code)
  for _, l in ipairs(LANGUAGES) do if l[1] == code then return l[2] end end
  return code
end

local function yesno(b) return b and 'sí' or 'no' end

views.root = function()
  local items = {}
  local t = state.task
  local path = mp.get_property('path') or ''
  if task_running() or state.starting then
    local pct = t and math.floor((t.progress or 0) * 100 + 0.5) or 0
    table.insert(items, { title = 'Detener subtítulos IA', icon = 'stop',
      hint = state.starting and 'iniciando…' or string.format('%d%% · %d cues · %s', pct, t.cues or 0, task_label(t)),
      value = { toggle = true } })
  elseif path == '' then
    table.insert(items, { title = 'Abre un archivo local para subtitularlo', icon = 'info', selectable = false, muted = true })
  elseif not is_local(path) then
    table.insert(items, { title = 'Solo archivos locales por ahora (ADR-023)', icon = 'info', selectable = false, muted = true })
  elseif t and t.status == 'done' then
    table.insert(items, { title = 'Subtítulos IA listos', icon = 'check_circle',
      hint = string.format('%d cues · %s', t.cues or 0, task_label(t)), value = { toggle = true } })
  else
    table.insert(items, { title = 'Iniciar subtítulos IA', icon = 'closed_caption', hint = 'alt+c', value = { toggle = true } })
  end
  table.insert(items, { title = 'Idioma', hint = language_name(state.language), icon = 'translate',
    value = { view = 'language' } })
  table.insert(items, { title = 'Modelo', hint = state.model, icon = 'memory', value = { view = 'models' } })
  table.insert(items, { title = 'Activar automáticamente al abrir un archivo', hint = yesno(state.auto_start),
    icon = 'autorenew', value = { opt = 'auto_start' }, separator = true })
  table.insert(items, { title = 'Pre-subtitular el siguiente de la lista', hint = yesno(state.precompute_next),
    icon = 'queue_play_next', value = { opt = 'precompute_next' } })
  if state.precompute then
    table.insert(items, { title = 'Siguiente: ' .. (state.precompute.path:match('[^/\\]+$') or ''),
      hint = state.precompute.status, icon = 'skip_next', selectable = false, muted = true })
  end
  table.insert(items, { title = 'Estado del motor', icon = 'monitor_heart', value = { view = 'status' }, separator = true })
  show('Subtítulos IA', items)
end

views.language = function()
  local items = {}
  for _, l in ipairs(LANGUAGES) do
    table.insert(items, { title = l[2], hint = l[1] ~= 'auto' and l[1] or nil,
      icon = l[1] == 'auto' and 'auto_awesome' or 'language', active = state.language == l[1], value = { language = l[1] } })
  end
  show('Idioma del audio', items)
end

local function models_items(res)
  local items = {}
  local rec = res.recommended or {}
  table.insert(items, { title = 'Automático según el hardware',
    hint = (rec.live or '?') .. ' (tier ' .. (res.tier or '?') .. ')', icon = 'auto_awesome', active = state.model == 'auto',
    value = { model = 'auto' } })
  for _, m in ipairs(res.models or {}) do
    if not m.vad then
      local dl = state.downloads[m.name]
      local hint, icon, value
      if m.present then
        hint = m.note; icon = 'check'; value = { model = m.name }
      elseif dl and (dl.status == 'queued' or dl.status == 'running') then
        hint = string.format('descargando %d%% %s', math.floor((dl.progress or 0) * 100 + 0.5), dl.message or '')
        icon = 'downloading'
        value = { noop = true }
      else
        hint = string.format('%s · descargar %d MB', m.note, m.size_mb or 0)
        icon = 'cloud_download'
        value = { download = m.name }
      end
      if m.name == rec.live then hint = hint .. ' · recomendado en vivo' end
      if m.name == rec.precompute and rec.precompute ~= rec.live then
        hint = hint .. ' · recomendado para pre-calcular'
      end
      local item = { title = m.name, hint = hint, icon = icon, active = state.model == m.name, value = value }
      if m.present then item.actions = { { name = 'remove', icon = 'delete', label = 'Borrar modelo' } } end
      table.insert(items, item)
    end
  end
  local vad = nil
  for _, m in ipairs(res.models or {}) do if m.vad then vad = m end end
  if vad then
    table.insert(items, { title = 'Detector de voz (VAD Silero)', hint = vad.present and 'presente' or 'descargar 1 MB',
      icon = vad.present and 'graphic_eq' or 'cloud_download', value = vad.present and { noop = true } or { download = vad.name },
      separator = true })
  end
  return items
end

views.models = function()
  if not require_mpvd('Modelo de whisper') then return end
  if state.models then show('Modelo de whisper', models_items(state.models)); return end
  show('Modelo de whisper', uosc.loading_items('Consultando modelos…'))
  rpc.call('asr.models', nil, function(err, res)
    if err then show('Modelo de whisper', uosc.message_items(fail(err, 'asr.models'), 'error')); return end
    state.models = res
    if state.view == 'models' then show('Modelo de whisper', models_items(res)) end
  end)
end

views.status = function()
  if not require_mpvd('Estado del motor') then return end
  show('Estado del motor', uosc.loading_items())
  rpc.call('asr.status', nil, function(err, st)
    if err then show('Estado del motor', uosc.message_items(fail(err, 'asr.status'), 'error')); return end
    local e = st.engine or {}
    local items = {
      { title = 'whisper-cli', hint = e.available and (e.version or 'ok') or 'no encontrado (tools/vendor_whisper.sh)',
        icon = e.available and 'check_circle' or 'error', selectable = false },
      { title = 'Hilos', hint = tostring(e.threads or '?'), icon = 'memory', selectable = false },
      { title = 'VAD Silero', hint = yesno(e.vad), icon = 'graphic_eq', selectable = false },
      { title = 'Hardware', hint = 'tier ' .. tostring(st.tier or '?'), icon = 'computer', selectable = false },
      { title = 'Recomendado en vivo / pre-cálculo', icon = 'auto_awesome', selectable = false,
        hint = ((st.recommended or {}).live or '?') .. ' / ' .. ((st.recommended or {}).precompute or '?') },
      { title = 'Modelos presentes', hint = table.concat(st.models_present or {}, ', '), icon = 'storage', selectable = false },
      { title = 'RTF medio del motor', hint = e.rtf and string.format('%.2f', e.rtf) or '—', icon = 'speed',
        selectable = false, separator = true },
    }
    for _, t in ipairs(st.tasks or {}) do
      table.insert(items, { title = (t.path or ''):match('[^/\\]+$') or t.path or '', icon = 'subtitles',
        hint = string.format('%s · %d%% · %d cues · %s%s', t.status, math.floor((t.progress or 0) * 100 + 0.5),
          t.cues or 0, task_label(t), t.rtf and string.format(' · RTF %.2f', t.rtf) or ''), selectable = false })
    end
    if state.view == 'status' then show('Estado del motor', items) end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- events from uosc

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.toggle then
      toggle()
      uosc.close(MENU)
    elseif v.language then
      state.language = v.language
      publish()
      table.remove(state.stack)
      reopen_current()
    elseif v.model then
      if ev.action == 'remove' and v.model ~= 'auto' then
        rpc.call('asr.models.remove', { name = v.model }, function() state.models = nil; reopen_current() end)
      else
        state.model = v.model
        publish()
        table.remove(state.stack)
        reopen_current()
      end
    elseif v.download then
      rpc.call('asr.models.download', { name = v.download, notify = SCRIPT }, function(err, job)
        if err then osd('Descarga: ' .. fail(err, 'asr.models.download')); return end
        state.downloads[v.download] = job
        osd('Descargando el modelo ' .. v.download .. '…')
        reopen_current()
      end)
    elseif v.opt then
      state[v.opt] = not state[v.opt]
      publish()
      if v.opt == 'precompute_next' and state.precompute_next and task_running() then precompute_next() end
      reopen_current()
    elseif v.view then
      if v.ensure then mp.commandv('script-message-to', 'mu_core', 'mu-ensure') end
      if v.view == 'root' then state.stack = {} end
      open_view({ name = v.view, args = v })
    end
  elseif ev.type == 'back' then
    table.remove(state.stack)
    if #state.stack == 0 then uosc.close(MENU) else reopen_current() end
  end
end)

local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if t == MENU then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    if uosc.open_type() == MENU then return end
    if #state.stack > 0 or state.view ~= '' then
      state.stack = {}
      state.view = ''
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- bindings and controls button

set_button_state = function()
  if not uosc.available() then return end
  local t = state.task
  local running = task_running()
  local badge = nil
  if running and t then badge = string.format('%d%%', math.floor((t.progress or 0) * 100 + 0.5)) end
  uosc.set_button('mu-subs', {
    icon = 'closed_caption', tooltip = 'Subtítulos IA (alt+i)', active = running or (t ~= nil and t.status == 'done'),
    badge = badge, command = { 'script-binding', SCRIPT .. '/subs-menu' },
  })
end

local function open_root()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'root' })
end

mp.add_key_binding(nil, 'subs-menu', open_root)
mp.add_key_binding(nil, 'subs-toggle', toggle)
mp.register_script_message('mu-subs-start', function() start(false) end)
mp.register_script_message('mu-subs-stop', stop)
mp.register_script_message('mu-subs-set', function(key, value)
  if key == 'language' or key == 'model' then state[key] = value
  elseif key == 'auto_start' or key == 'precompute_next' then state[key] = (value == 'yes' or value == 'true')
  end
  publish()
end)

mp.register_script_message('uosc-version', set_button_state)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button_state() end
end)

publish()
msg.info('mu-subs loaded')
