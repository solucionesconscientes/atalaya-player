-- mu-subs: live AI subtitles for MPV-UOS. Asks mpvd (asr.*) to transcribe the current local file ahead of the playback
-- position with whisper.cpp, adds the incremental SRT as an external subtitle track (`sub-add`) and reloads it
-- (a fresh copy replaces it) on every push event; the look-ahead cursor follows seeks (`asr.seek`). Menu "Subtítulos IA" (uosc):
-- start/stop, language, model (download on demand), automatic start, pre-subtitling of the next playlist item, status.
-- Also: translation (Argos / OPUS-MT, subs.translate), resync (subs.resync), extraction of embedded text tracks
-- (subs.extract) and "Guardar subtítulos (SRT)" (subs.save: AI track, translation, resync or the selected track).
-- Script name: mu_subs. Bindings: subs-menu (alt+i), subs-toggle (alt+c), subs-resync (alt+x), subs-save (alt+S).
-- Commands verified against mpv 0.41 (`mpv --input-cmdlist`: sub-add url [flags] [title] [lang] · sub-reload [id] ·
-- sub-remove [id]; track-list/N/ff-index and track-list/N/codec exist in 0.41).
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local N = nav.new()
local prefs = require('mu.prefs')

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
  chunk_seconds = 0,        -- 0 = mpvd default (28.5 s: one 30 s whisper window, ADR-024)
  chapter_min_seconds = 180, -- minimum length of an AI chapter (semantic.chapters)
  chapter_window = 45,      -- seconds per topic window (semantic.chapters)
  translate_engine = 'auto', -- auto (OPUS-MT when downloaded for the pair, else Argos) | argos | opus-big
  save_dir = '',            -- folder for "Guardar subtítulos"; empty = next to the video (fallback ~/Vídeos/MPV-UOS/…)
}
options.read_options(opts, 'mu-subs')
-- remembered choices (menu alt+i): language, model, automatic start, pre-subtitling, translation engine/target, duals
local P = prefs.ns('mu-subs', { language = opts.language, model = opts.model, auto_start = opts.auto_start,
  precompute_next = opts.precompute_next, translate_engine = opts.translate_engine, translate_target = '', dual = false })
P:apply_opts(opts, 'mu-subs', { 'language', 'model', 'auto_start', 'precompute_next', 'translate_engine' })

local LANGUAGES = {
  { 'auto', 'Detectar automáticamente' }, { 'es', 'Español' }, { 'en', 'Inglés' }, { 'ca', 'Catalán' }, { 'fr', 'Francés' },
  { 'de', 'Alemán' }, { 'it', 'Italiano' }, { 'pt', 'Portugués' }, { 'gl', 'Gallego' }, { 'eu', 'Euskera' },
  { 'nl', 'Neerlandés' },
  { 'pl', 'Polaco' }, { 'ru', 'Ruso' }, { 'uk', 'Ucraniano' }, { 'tr', 'Turco' }, { 'ar', 'Árabe' }, { 'hi', 'Hindi' },
  { 'zh', 'Chino' }, { 'ja', 'Japonés' }, { 'ko', 'Coreano' },
}

-- Bitmap subtitle codecs (mpv/FFmpeg names): they cannot become SRT without OCR.
local IMAGE_CODECS = { hdmv_pgs_subtitle = true, dvd_subtitle = true, dvb_subtitle = true, xsub = true }
local IMAGE_MSG = 'subtítulo de imagen: necesita OCR'

-- ISO 639-2 codes that containers use (mpv track-list lang) → the ISO 639-1 codes of the menus and mpvd
local ISO3 = { spa = 'es', eng = 'en', cat = 'ca', fra = 'fr', fre = 'fr', deu = 'de', ger = 'de', ita = 'it',
  por = 'pt', glg = 'gl', eus = 'eu', baq = 'eu', nld = 'nl', dut = 'nl', pol = 'pl', rus = 'ru', ukr = 'uk',
  tur = 'tr', ara = 'ar', hin = 'hi', zho = 'zh', chi = 'zh', jpn = 'ja', kor = 'ko' }

local function norm_lang(code)
  code = (code or ''):lower():gsub('[-_].*$', '')
  return ISO3[code] or code
end

local function language_name_for(code)
  for _, l in ipairs(LANGUAGES) do if l[1] == code then return l[2] end end
  return code
end

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
  wait_said = nil,        -- último «listos en …» dicho por OSD, para no repetirlo en cada trozo (C3)
  resync = nil,           -- {srt=, task=, status=, stats=, out=} last resync request
  translate = nil,        -- {srt=, source=, target=, status=, out=, job=, progress=} last translation request
  packages = nil,         -- subs.translate.models result (present pairs)
  dual = false,           -- original on top (secondary-sid) + translation below (sid)
  translate_engine = opts.translate_engine,
  save = nil,             -- {kind=, status=, out=, name=, lines=, error=, coverage=, fallback=} last subs.save
  extract = nil,          -- {job=, srt=, status=, cb=} pending subs.extract of an embedded track
  adopted = '',           -- saved SRT (auto-loaded by mpv) shown instead of re-adding the cached AI track
}

local set_button_state -- defined with the bindings

P:on_change(function(reason)
  if reason ~= 'reset' then return end
  state.language, state.model = P:get('language'), P:get('model')
  state.auto_start, state.precompute_next = P:get('auto_start'), P:get('precompute_next')
  state.translate_engine = P:get('translate_engine')
end)
local reopen_current   -- defined with the menus

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function task_running()
  return state.task ~= nil and (state.task.status == 'queued' or state.task.status == 'running')
end

local wait_text   -- se define más abajo (necesita task_running/duracion_larga); publish() ya lo usa
local function publish()
  local t = state.task
  mp.set_property_native('user-data/mu/subs', {
    language = state.language, model = state.model, auto_start = state.auto_start,
    precompute_next = state.precompute_next, active = task_running(), starting = state.starting,
    path = state.path, srt = state.srt, sid = state.sid or 0,
    task_id = t and t.id or '', status = t and t.status or '', progress = t and t.progress or 0,
    ahead = t and t.ahead or 0, cues = t and t.cues or 0, detected = t and t.detected or '',
    task_model = t and t.model or '', rtf = t and t.rtf or 0, seq = t and t.seq or -1, error = t and t.error or '',
    rtf_recent = t and t.rtf_recent or 0, remaining = t and t.remaining or 0, wait = wait_text(t) or '',
    precompute_id = state.precompute and state.precompute.id or '',
    precompute_status = state.precompute and state.precompute.status or '',
    view = state.view, depth = #state.stack, items = state.items, last_event = state.last_event or '',
    last_error = state.last_error,
    resync_status = state.resync and state.resync.status or '', resync_out = state.resync and state.resync.out or '',
    resync_offset = state.resync and state.resync.stats and state.resync.stats.offset_median or 0,
    translate_status = state.translate and state.translate.status or '',
    translate_out = state.translate and state.translate.out or '',
    translate_target = state.translate and state.translate.target or '',
    translate_progress = state.translate and state.translate.progress or 0, dual = state.dual,
    translate_engine = state.translate_engine,
    translate_engines = state.translate and state.translate.engines or '',
    ai_chapters = state.ai_chapters or 0, chapters_status = state.chapters_status or '',
    save_status = state.save and state.save.status or '', save_kind = state.save and state.save.kind or '',
    save_out = state.save and state.save.out or '', save_name = state.save and state.save.name or '',
    save_lines = state.save and state.save.lines or 0, save_error = state.save and state.save.error or '',
    save_coverage = state.save and state.save.coverage or 0, save_fallback = state.save and state.save.fallback or false,
    extract_status = state.extract and state.extract.status or '', extract_srt = state.extract and state.extract.srt or '',
    adopted = state.adopted,
    web_status = state.web and state.web.status or '', web_srt = state.web and state.web.srt or '',
    web_lang = state.web and state.web.lang or '', web_kind = state.web and state.web.kind or '',
    web_cues = state.web and state.web.cues or 0,
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

-- C3 · el aviso de espera. Se calcula con el ritmo que mide la propia tarea (`rtf_recent`, los últimos trozos) sobre
-- lo que queda por transcribir, que es lo que publica mpvd en `remaining`. No se promete nada por modelo: si la
-- máquina se carga, el ritmo sube y el número sube con él en el siguiente aviso.
local function duracion_larga(seconds)
  local n = math.max(0, math.floor(tonumber(seconds) or 0))
  if n < 60 then return n .. ' s' end
  if n < 3600 then return math.floor(n / 60 + 0.5) .. ' min' end
  local h = math.floor(n / 3600)
  local m = math.floor((n % 3600) / 60 + 0.5)
  return m > 0 and string.format('%d h %d min', h, m) or string.format('%d h', h)
end

-- Devuelve el texto del aviso, o nil si todavía no hay ninguna medida (nunca inventar un número).
wait_text = function(t)
  if not t or t.complete or t.status == 'done' then return nil end
  local queda = tonumber(t.remaining)
  local ritmo = tonumber(t.rtf_recent) or tonumber(t.rtf)
  if not queda or queda <= 0 or not ritmo or ritmo <= 0 then return nil end
  local txt = 'listos en ' .. duracion_larga(queda)
  if ritmo < 1 then
    -- va más rápido que el vídeo: en cuanto termine el primer tramo se puede empezar a ver sin que te alcance
    return txt .. ' · va más rápido que el vídeo, no te alcanzará'
  end
  local alt = t.alt
  if type(alt) == 'table' and alt.model and tonumber(alt.remaining) then
    txt = txt .. string.format(' · con %s, %s', alt.model, duracion_larga(alt.remaining))
  end
  return txt .. string.format(' (va %.1f veces más lento que el vídeo)', ritmo)
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

-- A finished AI track that was saved next to the video comes back through sub-auto on the next play: select that
-- track instead of adding the cached copy (same subtitles twice).
local function saved_ai_track()
  local t = state.task
  if not t or not (t.complete or t.status == 'done') then return nil end
  for _, sv in ipairs(t.saved or {}) do
    if sv.complete and sv.path then
      local tr = find_track(sv.path)
      if tr then return tr end
    end
  end
  return nil
end

local function add_track()
  if state.srt == '' then return end
  local saved = not find_track(state.srt) and saved_ai_track() or nil
  if saved then
    state.sid = saved.id
    state.adopted = saved['external-filename']
    mp.set_property_native('sid', saved.id)
    publish()
    return
  end
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
  -- sub-reload re-adds the file AND selects it (asynchronously). Only reload while the AI track is what the
  -- viewer is looking at; otherwise remember and reload when it gets selected again (see the `sid` observer).
  local sid = mp.get_property_native('sid')
  local sec = mp.get_property_native('secondary-sid')
  if sid ~= t.id and sec ~= t.id then
    state.reload_pending = true
    return
  end
  state.last_reload = now
  state.reload_pending = false
  -- Not sub-reload: it re-adds AND re-selects the track asynchronously, which could undo a track the viewer picks a
  -- moment later. A fresh copy is added unselected, takes the old one's place (primary or secondary) and the old one
  -- goes; all synchronous.
  local args = { 'sub-add', state.srt, 'auto', t.title or TRACK_TITLE }
  if t.lang then table.insert(args, t.lang) end
  mp.command_native(args)
  local nt = nil
  for _, tr in ipairs(mp.get_property_native('track-list') or {}) do
    if tr.type == 'sub' and tr['external-filename'] == state.srt and tr.id ~= t.id then nt = tr end
  end
  if nt then
    if sid == t.id then mp.set_property_native('sid', nt.id) end
    if sec == t.id then mp.set_property_native('secondary-sid', nt.id) end
    mp.commandv('sub-remove', tostring(t.id))
    state.sid = nt.id
  end
  publish()
end

mp.observe_property('sid', 'native', function(_, sid)
  if state.reload_pending and state.srt ~= '' and type(sid) == 'number' then
    local t = find_track(state.srt)
    if t and t.id == sid then reload_track() end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- Cursor de look-ahead: EN DESUSO desde H36. Mientras se transcribía «en vivo» persiguiendo la reproducción, tenía
-- sentido mover el cursor a donde estabas; ahora el archivo se prepara entero y en orden desde el principio, así que
-- perseguir la posición solo rompería ese orden (y dejaría huecos detrás). Se conserva `asr.seek` en mpvd por si algún
-- día se ofrece «transcribe desde aquí» a mano.
local SEGUIR_POSICION = false

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
  if SEGUIR_POSICION and task_running() then seek_timer:resume() else seek_timer:kill() end
end

-- ---------------------------------------------------------------------------------------------
-- start / stop / precompute

local precompute_next -- forward

-- C3 · se avisa por OSD la primera vez que hay una estimación y cuando cambia de verdad (±40 % o media hora), no en
-- cada trozo: si no, el aviso se convierte en ruido.
local function notify_wait(t)
  local queda = tonumber(t and t.remaining)
  if not queda or queda <= 0 or t.complete or t.status ~= 'running' then return end
  local antes = state.wait_said
  local salto = antes and math.abs(queda - antes) or math.huge
  if antes and salto < math.max(0.4 * antes, 20) then return end
  local txt = wait_text(t)
  if not txt then return end
  state.wait_said = queda
  osd('Subtítulos IA: ' .. txt)
end

local function apply_task(t, from_event)
  state.task = t
  if t.srt and t.srt ~= '' then state.srt = t.srt end
  if from_event then
    state.last_event = { id = t.id, status = t.status, progress = t.progress, seq = t.seq }
    notify_wait(t)
  end
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
  state.wait_said = nil
  publish()
  -- H36: ya no se transcribe «en vivo» persiguiendo la reproducción con un modelo flojo. Se prepara el archivo entero
  -- con el modelo bueno desde el principio (`prepare`), que es más rápido que el vídeo, así que basta esperar unos
  -- minutos y luego ya no te alcanza. time_pos = 0 para que vaya en orden desde el inicio.
  local params = {
    path = path, language = state.language, model = state.model ~= 'auto' and state.model or nil,
    purpose = 'prepare', time_pos = 0, audio_track = current_audio_index(), notify = SCRIPT,
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
-- resync of an external subtitle file against the Whisper transcription (subs.resync)

local selected_sub_file, with_extracted -- defined with the translation helpers below

local function selected_external_sub()
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'sub' and t.selected and t.external and t['external-filename'] and t['external-filename'] ~= state.srt
      and not (t.title or ''):find(TRACK_TITLE, 1, true) and not (t['external-filename'] or ''):find('%.resync%.srt$') then
      return t
    end
  end
  return nil
end

local function resync_apply(res)
  state.resync.status = 'done'
  state.resync.out = res.srt
  state.resync.stats = res.stats
  publish()
  local st = res.stats or {}
  if not st.ok then
    osd(string.format('Resincronización: pocas coincidencias (%d de %d cues); no se aplica', st.matched or 0, st.cues or 0))
    return
  end
  local existing = find_track(res.srt)
  if existing then
    mp.commandv('sub-reload', tostring(existing.id))
    mp.set_property_native('sid', existing.id)
  else
    mp.command_native({ 'sub-add', res.srt, 'select', 'Resincronizado (' .. (res.source or ''):match('[^/\\]+$') .. ')' })
  end
  local drift = st.drift_ppm or 0
  osd(string.format('✓ Subtítulo resincronizado: %+.2f s%s (%d/%d cues)', st.offset_median or 0,
    math.abs(drift) > 500 and string.format(', deriva %+.1f ms/min', drift * 60 / 1000) or '', st.matched or 0, st.cues or 0))
end

local function resync_request(srt, quiet)
  local params = { path = current_path(), srt = srt, language = state.language,
    model = state.model ~= 'auto' and state.model or nil, notify = SCRIPT }
  rpc.call('subs.resync', params, function(err, res)
    if err then
      state.resync.status = 'failed'
      publish()
      osd('Resincronizar: ' .. fail(err, 'subs.resync'))
      return
    end
    if res.status == 'pending' then
      state.resync.status = 'pending'
      state.resync.task = res.task and res.task.id or nil
      publish()
      if not quiet then
        osd('Transcribiendo el audio para resincronizar… (' .. math.floor((res.task.progress or 0) * 100) .. '%)')
      end
      return
    end
    resync_apply(res)
  end, 120)
end

local function resync_selected()
  if not rpc.connected() then osd('mpvd no está disponible') return end
  if not is_local(mp.get_property('path')) then osd('Solo archivos locales por ahora') return end
  local t = selected_external_sub()
  if t then
    state.resync = { srt = t['external-filename'], status = 'requested', lang = t.lang }
    publish()
    resync_request(t['external-filename'], false)
    return
  end
  local _, embedded = selected_sub_file()
  if not embedded then
    osd('Selecciona primero una pista de subtítulos (archivo .srt/.ass/.vtt o pista interna de texto)')
    return
  end
  with_extracted(embedded, 'Resincronizar', function(srt)
    state.resync = { srt = srt, status = 'requested', lang = embedded.lang }
    publish()
    resync_request(srt, false)
  end)
end

-- ---------------------------------------------------------------------------------------------
-- translation (subs.translate) and dual subtitles (secondary-sid = original on top, sid = translation below)

selected_sub_file = function()
  -- the primary selected subtitle track: (external track) or (nil, embedded track)
  local sid = mp.get_property_native('sid')
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'sub' and t.selected and (type(sid) ~= 'number' or t.id == sid) then
      if t.external and t['external-filename'] then return t end
      return nil, t
    end
  end
  return nil
end

local function source_language(track)
  if track['external-filename'] == state.srt then return 'auto' end   -- the AI track: mpvd knows its language
  if track.lang and track.lang ~= '' then return track.lang end        -- mpvd maps spa → es, en-US → en
  return state.language   -- 'auto' → mpvd will ask for it (OSD)
end

-- Embedded text track → SRT in mpvd's cache (subs.extract); cb(srt, result) runs now or when the job ends.
with_extracted = function(track, what, cb)
  if IMAGE_CODECS[track.codec or ''] then
    osd(what .. ': ' .. IMAGE_MSG .. ' (' .. track.codec .. ')')
    return
  end
  if not is_local(mp.get_property('path')) then osd(what .. ': las pistas internas solo se leen en archivos locales') return end
  rpc.call('subs.extract', { path = current_path(), ff_index = track['ff-index'], codec = track.codec, notify = SCRIPT },
    function(err, res)
      if err then osd(what .. ': ' .. fail(err, 'subs.extract')) return end
      if res.status == 'done' then
        state.extract = { srt = res.srt, status = 'done' }
        publish()
        cb(res.srt, res)
        return
      end
      state.extract = { job = res.job and res.job.id or nil, srt = res.srt, status = 'running', cb = cb, what = what }
      publish()
      osd('Extrayendo la pista de subtítulos (' .. (track.codec or '?') .. ')…')
    end, 60)
end

local function translated_track()
  return state.translate and state.translate.out and state.translate.out ~= '' and find_track(state.translate.out) or nil
end

local function apply_dual(on)
  local tr = translated_track()
  local orig = state.translate and find_track(state.translate.srt) or nil
  if not orig and state.translate and state.translate.orig_id then   -- an embedded track that was extracted
    for _, t in ipairs(mp.get_property_native('track-list') or {}) do
      if t.type == 'sub' and t.id == state.translate.orig_id then orig = t end
    end
  end
  if on and tr and orig then
    mp.set_property_native('sid', tr.id)
    mp.set_property_native('secondary-sid', orig.id)
    state.dual = true
  else
    mp.set_property_native('secondary-sid', 'no')
    state.dual = false
  end
  publish()
end

local translate_request -- forward

local function translate_apply(res)
  state.translate.status = 'done'
  state.translate.out = res.srt
  state.translate.progress = 1
  state.translate.engines = table.concat(res.engines or {}, '+')
  local existing = find_track(res.srt)
  local title = 'Traducción (' .. (res.target or state.translate.target or '?') .. ')'
  if existing then
    mp.commandv('sub-reload', tostring(existing.id))
    mp.set_property_native('sid', existing.id)
  else
    mp.command_native({ 'sub-add', res.srt, 'select', title, res.target or '' })
  end
  publish()  -- after the track exists: whoever sees "done" finds it in track-list
  if state.dual or P:get('dual') then apply_dual(true) end
  if opts.notify_done then
    osd('✓ ' .. title .. (res.cached and ' (caché)' or '') .. ': ' .. tostring(res.cues or 0) .. ' cues')
  end
  set_button_state()
end

local function download_packages(missing, target)
  local big = false
  for _, pair in ipairs(missing) do
    local engine = pair[3] or 'argos'
    if engine == 'opus-big' then big = true end
    rpc.call('subs.translate.download', { source = pair[1], target = pair[2], engine = engine, notify = SCRIPT },
      function(err)
        if err then osd('Modelo ' .. pair[1] .. '→' .. pair[2] .. ': ' .. fail(err, 'subs.translate.download')) end
      end)
  end
  state.translate.status = 'downloading'
  state.translate.retry_target = target
  state.translate.pending = #missing
  publish()
  if big then
    osd('Descargando OPUS-MT (≈860 MB, se convierte a 234 MB una sola vez)… se traducirá al terminar')
  else
    osd('Descargando el paquete de traducción (' .. #missing .. ')… se traducirá al terminar')
  end
end

translate_request = function(track, target)
  local srt = track['external-filename']
  state.translate = { srt = srt, target = target, status = 'requested', progress = 0, track = track,
    orig_id = track.orig_id }
  publish()
  local params = { srt = srt, target = target, source = source_language(track), notify = SCRIPT,
    engine = state.translate_engine }
  if is_local(mp.get_property('path')) then params.path = current_path() end
  rpc.call('subs.translate', params, function(err, res)
    if err then
      if err.data and type(err.data.missing) == 'table' and #err.data.missing > 0 then
        download_packages(err.data.missing, target)
        return
      end
      state.translate.status = 'failed'
      publish()
      osd('Traducir: ' .. fail(err, 'subs.translate'))
      return
    end
    if res.status == 'done' then translate_apply(res); return end
    state.translate.status = 'running'
    state.translate.job = res.job and res.job.id or nil
    publish()
    osd('Traduciendo ' .. tostring(res.cues or 0) .. ' cues al ' .. language_name_for(target) .. '…')
  end, 60)
end

-- ---------------------------------------------------------------------------------------------
-- subtitles the website offers for an internet video (H29): manual tracks and the automatic captions in the video's
-- own language, fetched by mpvd as clean SRT files (subs.web.*); translated offline like any other track

local function web_video()
  local path = mp.get_property('path') or ''
  if path == '' or is_local(path) then return nil end
  local y = mp.get_property_native('user-data/mu/ytdl') or {}
  if y.active and y.url == path then return path end
  return nil
end

local function web_add(url, lang, kind, target)
  if not rpc.connected() then osd('mpvd no está disponible') return end
  state.web = { status = 'fetching', lang = lang, kind = kind }
  publish()
  osd('Bajando los subtítulos de la web…')
  rpc.call('subs.web.fetch', { url = url, lang = lang, kind = kind }, function(err, res)
    if err then
      state.web.status = 'failed'
      publish()
      osd('Subtítulos de la web: ' .. fail(err, 'subs.web.fetch'))
      return
    end
    local t = find_track(res.srt)
    if t then mp.set_property_number('sid', t.id)
    else mp.command_native({ 'sub-add', res.srt, 'select', res.title, lang }) end
    state.web = { status = 'done', lang = lang, kind = kind, srt = res.srt, cues = res.cues or 0 }
    publish()
    if target then
      local sid = mp.get_property_number('sid')
      translate_request({ ['external-filename'] = res.srt, lang = lang, id = sid, orig_id = sid }, target)
    else
      osd('✓ ' .. res.title .. ' (' .. tostring(res.cues or 0) .. ' líneas)')
    end
  end, 90)
end

local function translate_selected(target)
  if not rpc.connected() then osd('mpvd no está disponible') return end
  local track, embedded = selected_sub_file()
  if track then translate_request(track, target) return end
  if not embedded then osd('Selecciona primero una pista de subtítulos (o inicia los subtítulos IA)') return end
  with_extracted(embedded, 'Traducir', function(srt)
    translate_request({ ['external-filename'] = srt, lang = embedded.lang, orig_id = embedded.id }, target)
  end)
end

-- ---------------------------------------------------------------------------------------------
-- save as SRT next to the video (subs.save): AI track, translation, resync or the selected track

local open_save_menu -- defined with the menus

local function base_name(p) return (p or ''):match('[^/\\]+$') or '' end

local function track_kind(t)
  local f = t.external and t['external-filename'] or nil
  if f and (f == state.srt or f == state.adopted) then return 'ai' end
  if f and state.translate and f == state.translate.out then return 'translation' end
  if f and state.resync and f == state.resync.out then return 'resync' end
  return 'track'
end

local function ai_lang()
  local t = state.task
  if not t then return state.language ~= 'auto' and state.language or '' end
  if t.detected and t.detected ~= '' then return t.detected end
  return t.language ~= 'auto' and t.language or ''
end

local function pct(x) return math.floor((x or 0) * 100 + 0.5) end

local function save_done(res)
  state.save = { kind = res.kind, status = 'done', out = res.path, name = res.name, lines = res.lines or 0,
    coverage = res.coverage or 1, fallback = res.fallback or false }
  publish()
  local extra = ''
  if res.partial then extra = string.format(' · parcial: %d%%', pct(res.coverage)) end
  if res.already then extra = extra .. ' · ya estaba guardado' end
  if res.fallback then extra = extra .. '\nen ' .. (res.dir or '') end
  osd(string.format('✓ Guardado: %s (%d líneas)%s', res.name or base_name(res.path), res.lines or 0, extra))
end

local function save_failed(message)
  state.save = state.save or {}
  state.save.status = 'failed'
  state.save.error = message
  publish()
  osd('✗ No se pudo guardar: ' .. message)
end

local function save_request(kind, extra)
  extra = extra or {}
  if not rpc.connected() then osd('mpvd no está disponible') return end
  local raw = mp.get_property('path') or ''
  if raw == '' then osd('No hay ningún archivo abierto') return end
  local params = { path = is_local(raw) and current_path() or raw, kind = kind, notify = SCRIPT,
    title = mp.get_property('media-title') }
  if opts.save_dir ~= '' then params.dest_dir = mp.command_native({ 'expand-path', opts.save_dir }) end
  if kind == 'ai' then
    if state.srt == '' and not state.task then osd('No hay pista IA: inicia los subtítulos IA (alt+c)') return end
    params.srt = state.srt ~= '' and state.srt or nil
    params.lang = ai_lang()
    params.allow_partial = extra.partial or nil
    params.complete = extra.complete or nil
  elseif kind == 'translation' then
    if not (state.translate and state.translate.out and state.translate.out ~= '') then
      osd('No hay ninguna traducción que guardar')
      return
    end
    params.srt = state.translate.out
    params.lang = state.translate.target
  elseif kind == 'resync' then
    if not (state.resync and state.resync.out and state.resync.out ~= '') then
      osd('No hay ningún subtítulo resincronizado que guardar')
      return
    end
    params.srt = state.resync.out
    params.lang = norm_lang(state.resync.lang)
  else
    local t = extra.track
    if not t then
      local ext, emb = selected_sub_file()
      t = ext or emb
    end
    if not t then osd('Selecciona primero una pista de subtítulos') return end
    if IMAGE_CODECS[t.codec or ''] then
      state.save = { kind = 'track', status = 'failed', error = IMAGE_MSG }
      publish()
      osd('✗ No se pudo guardar: ' .. IMAGE_MSG .. ' (' .. t.codec .. ')')
      return
    end
    if t.external and t['external-filename'] then
      params.srt = t['external-filename']
    else
      params.ff_index = t['ff-index']
      params.codec = t.codec
    end
    params.lang = norm_lang(t.lang)
  end
  state.save = { kind = kind, status = 'requested' }
  publish()
  rpc.call('subs.save', params, function(err, res)
    if err then save_failed(fail(err, 'subs.save')) return end
    if res.status == 'done' then
      save_done(res)
    elseif res.status == 'partial' then
      state.save = { kind = 'ai', status = 'partial', coverage = res.coverage or 0, lines = res.lines or 0 }
      publish()
      osd(string.format('La pista IA va por el %d%%: elige «Completar y guardar» o «Guardar lo transcrito»',
        pct(res.coverage)))
      if extra.menu ~= false and open_save_menu then open_save_menu() end
    elseif res.status == 'waiting' then
      state.save = { kind = 'ai', status = 'waiting', coverage = res.coverage or 0 }
      publish()
      osd(string.format('Completando la transcripción (%d%%)… se guardará al terminar', pct(res.coverage)))
    else -- queued: an embedded track is being extracted first
      state.save = { kind = kind, status = 'queued' }
      publish()
      osd('Extrayendo la pista… se guardará al terminar')
    end
  end, 60)
end

-- binding: the selected track (whatever it is: AI, translation, resync, external or embedded), else the AI track
local function save_default()
  local ext, emb = selected_sub_file()
  local t = ext or emb
  if t then
    save_request(track_kind(t), { track = t })
  elseif state.srt ~= '' or state.task then
    save_request('ai')
  else
    osd('No hay subtítulos que guardar: selecciona una pista o inicia los subtítulos IA')
  end
end

-- ---------------------------------------------------------------------------------------------
-- events pushed by mpvd

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  if ev.event == 'asr' and type(ev.task) == 'table' then
    local t = ev.task
    if state.resync and state.resync.status == 'pending' and state.resync.task == t.id and t.status == 'done' then
      resync_request(state.resync.srt, true)
    end
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
  elseif ev.event == 'subs-translate' and type(ev.job) == 'table' then
    if not state.translate or (state.translate.job and ev.job.id ~= state.translate.job) then return end
    state.translate.job = ev.job.id
    state.translate.progress = ev.job.progress or 0
    if ev.job.status == 'done' and type(ev.result) == 'table' then
      translate_apply(ev.result)
    elseif ev.job.status == 'failed' then
      state.translate.status = 'failed'
      publish()
      osd('✗ Traducción fallida: ' .. (ev.job.error or ev.job.message or ''))
    else
      state.translate.status = 'running'
      publish()
      set_button_state()
    end
  elseif ev.event == 'subs-save' then
    if type(ev.result) == 'table' then
      save_done(ev.result)
    elseif ev.error then
      save_failed(ev.error)
    elseif ev.status == 'waiting' then
      state.save = state.save or { kind = 'ai' }
      state.save.status = 'waiting'
      state.save.coverage = ev.coverage or 0
      publish()
    end
  elseif ev.event == 'subs-extract' and type(ev.job) == 'table' then
    local ex = state.extract
    if ex and ex.job and ev.job.id == ex.job then
      if ev.job.status == 'done' then
        ex.status = 'done'
        publish()
        local cb = ex.cb
        ex.cb = nil
        if cb then cb((type(ev.result) == 'table' and ev.result.srt) or ex.srt, ev.result) end
      elseif ev.job.status == 'failed' then
        ex.status = 'failed'
        publish()
        osd('✗ ' .. (ex.what or 'Extraer') .. ': ' .. (ev.job.error or 'no se pudo extraer la pista'))
      end
    end
  elseif ev.event == 'subs-translate-model' and type(ev.job) == 'table' then
    state.packages = nil
    if state.translate and state.translate.status == 'downloading' then
      state.translate.dl_progress = ev.job.progress or 0
      publish()
    end
    if ev.job.status == 'done' then
      osd((ev.engine == 'opus-big' and 'Modelo OPUS-MT ' or 'Paquete de traducción ') .. (ev.pair or '') .. ' listo')
      local tr = state.translate
      if tr and tr.status == 'downloading' and tr.retry_target then
        tr.pending = (tr.pending or 1) - 1
        local track = tr.track or find_track(tr.srt)
        if track and tr.pending <= 0 then translate_request(track, tr.retry_target) end
      end
    elseif ev.job.status == 'failed' then
      if state.translate then state.translate.status = 'failed' end
      publish()
      osd('✗ No se pudo descargar ' .. (ev.pair or '') .. ': ' .. (ev.job.error or ''))
    end
    if uosc.open_type() == MENU and state.view == 'translate' and reopen_current then reopen_current() end
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
    state.resync = nil
    state.translate = nil
    state.dual = false
    state.save = nil
    state.extract = nil
    state.web = nil          -- the web subtitles belonged to the video we just left
    state.wait_said = nil
    state.adopted = ''
    state.ai_chapters = 0
    state.chapters_status = ''
    state.orig_chapters = nil
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
  state.web = nil
  state.wait_said = nil
  state.adopted = ''
  state.ai_chapters = 0
  state.chapters_status = ''
  state.orig_chapters = nil
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

-- topic-change chapters from mpvd (semantic.chapters over the transcript) → chapter-list; originals restored on removal
local function apply_chapters(on)
  if not on then
    if state.ai_chapters and state.ai_chapters > 0 then
      mp.set_property_native('chapter-list', state.orig_chapters or {})
      osd('Capítulos IA quitados')
    end
    state.ai_chapters = 0
    state.chapters_status = ''
    state.orig_chapters = nil
    publish()
    return
  end
  local path = current_path()
  if path == '' or not is_local(path) then osd('Capítulos IA: solo archivos locales') return end
  if not rpc.connected() then osd('mpvd no está conectado') return end
  state.chapters_status = 'calculando'
  publish()
  rpc.call('semantic.chapters', { path = path, min_seconds = opts.chapter_min_seconds, window = opts.chapter_window },
    function(err, res)
    if err then
      state.chapters_status = 'error'
      local m = fail(err, 'semantic.chapters')
      if m:find('transcripci') then m = 'primero genera los subtítulos IA (alt+c)' end
      osd('Capítulos IA: ' .. m)
      publish()
      return
    end
    local chaps = (type(res) == 'table' and res.chapters) or {}
    if #chaps == 0 then
      state.chapters_status = 'sin cambios de tema'
      osd('Capítulos IA: no se detectan cambios de tema')
      publish()
      return
    end
    if not state.orig_chapters then state.orig_chapters = mp.get_property_native('chapter-list') or {} end
    local list = {}
    for _, c in ipairs(chaps) do table.insert(list, { title = c.title, time = c.start }) end
    mp.set_property_native('chapter-list', list)
    state.ai_chapters = #list
    state.chapters_status = 'listo'
    osd(string.format('Capítulos IA: %d capítulos por tema', #list))
    publish()
  end, 120)
end

views.root = function()
  local items = {}
  local t = state.task
  local path = mp.get_property('path') or ''
  if task_running() or state.starting then
    local done_pct = t and pct(t.progress) or 0
    table.insert(items, { title = 'Detener subtítulos IA', icon = 'stop',
      hint = state.starting and 'iniciando…' or string.format('%d%% · %d cues · %s', done_pct, t.cues or 0, task_label(t)),
      value = { toggle = true } })
    local espera = wait_text(t)
    table.insert(items, { title = espera or 'Calculando cuánto va a tardar…', icon = 'hourglass_top',
      selectable = false, muted = true })
  elseif path == '' then
    table.insert(items, { title = 'Abre un archivo local para subtitularlo', icon = 'info', selectable = false, muted = true })
  elseif not is_local(path) then
    if web_video() then
      local w = state.web
      table.insert(items, { title = 'Subtítulos de la web', icon = 'language',
        hint = (w and w.status == 'done') and (language_name_for(w.lang) .. (w.kind == 'auto' and ' · automáticos' or ''))
          or 'los que da la web, y traducidos sin conexión', value = { view = 'web' } })
    end
    table.insert(items, { title = 'Subtítulos IA: solo archivos locales (ADR-023)', icon = 'info', selectable = false,
      muted = true })
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
  local sel, sel_emb = selected_sub_file()
  local tr = state.translate
  local tr_hint = 'selecciona una pista'
  if tr and tr.status == 'running' then tr_hint = string.format('traduciendo %d%%', math.floor((tr.progress or 0) * 100 + 0.5))
  elseif tr and tr.status == 'downloading' then tr_hint = string.format('descargando modelo %d%%', pct(tr.dl_progress))
  elseif tr and tr.status == 'done' then tr_hint = 'lista: ' .. language_name_for(tr.target)
  elseif sel then tr_hint = (sel.title or sel['external-filename']:match('[^/\\]+$') or '')
  elseif sel_emb then tr_hint = 'pista interna (' .. (sel_emb.codec or '?') .. ')' end
  table.insert(items, { title = 'Traducir la pista seleccionada a…', icon = 'translate', hint = tr_hint,
    value = { view = 'translate' }, separator = true, muted = sel == nil and sel_emb == nil and not tr })
  if translated_track() then
    table.insert(items, { title = 'Duales: original arriba + traducción abajo', icon = 'vertical_split',
      hint = yesno(state.dual), value = { dual = true } })
  end
  local ext = selected_external_sub()
  local rs = state.resync
  table.insert(items, { title = 'Resincronizar la pista externa con la IA', icon = 'sync_alt',
    hint = ext and ((rs and rs.srt == ext['external-filename'] and rs.status ~= 'done') and rs.status
      or (ext['external-filename']:match('[^/\\]+$'))) or 'selecciona un .srt/.ass externo',
    value = { resync = true }, muted = ext == nil })
  table.insert(items, { title = 'Buscar subtítulos en internet (OpenSubtitles)', icon = 'travel_explore',
    hint = 'por hash del archivo', value = { library_subs = true } })
  local sv = state.save
  local sv_hint = 'alt+S'
  if sv and sv.status == 'done' then sv_hint = sv.name or 'guardado'
  elseif sv and sv.status == 'waiting' then sv_hint = string.format('completando %d%%…', pct(sv.coverage))
  elseif sv and sv.status == 'queued' then sv_hint = 'extrayendo…' end
  table.insert(items, { title = 'Guardar subtítulos (SRT)', icon = 'save', hint = sv_hint, value = { view = 'save' } })
  local nch = state.ai_chapters or 0
  table.insert(items, { title = 'Capítulos por tema (IA)', icon = 'bookmarks', active = nch > 0,
    hint = nch > 0 and (nch .. ' capítulos · quitar') or (state.chapters_status ~= '' and state.chapters_status
      or 'según la transcripción'), value = { chapters = true } })
  table.insert(items, { title = 'Estado del motor', icon = 'monitor_heart', value = { view = 'status' } })
  show('Subtítulos IA', items)
end

views.web = function()
  local url = web_video()
  if not url then
    show('Subtítulos de la web', { { title = 'Abre un vídeo de internet (YouTube y otras webs)', icon = 'info',
      selectable = false, muted = true } })
    return
  end
  if not require_mpvd('Subtítulos de la web') then return end
  show('Subtítulos de la web', { { title = 'Preguntando a la web…', icon = 'hourglass_empty', selectable = false,
    muted = true } })
  local target = P:get('translate_target') or 'es'
  if target == '' then target = 'es' end
  rpc.call('subs.web.list', { url = url, prefer = target }, function(err, res)
    if state.view ~= 'web' then return end
    if err then
      show('Subtítulos de la web', { { title = 'No se pudo consultar: ' .. fail(err, 'subs.web.list'), icon = 'error',
        selectable = false, muted = true } })
      return
    end
    local items, have_target, orig = {}, false, nil
    local original = norm_lang(res.language or '')
    for _, t in ipairs(res.tracks or {}) do
      local base = norm_lang(t.lang)
      if base == target then have_target = true end
      if base == original and (not orig or (orig.kind == 'auto' and t.kind == 'manual')) then orig = t end
      table.insert(items, { title = t.label, icon = t.kind == 'auto' and 'auto_awesome' or 'subtitles',
        hint = t.kind == 'auto' and 'automáticos de la web' or 'de la web',
        active = state.web and state.web.srt ~= nil and state.web.lang == t.lang and state.web.kind == t.kind,
        value = { web = { lang = t.lang, kind = t.kind } } })
    end
    if #items == 0 then
      table.insert(items, { title = 'Esta web no ofrece subtítulos para este vídeo', icon = 'info', selectable = false,
        muted = true })
    end
    orig = orig or (res.tracks or {})[1]
    if orig and not have_target then
      table.insert(items, 1, { title = 'Traducir al ' .. language_name_for(target):lower() .. ' (' .. orig.label .. ')',
        icon = 'translate', hint = 'sin conexión, el archivo entero antes de mostrarlo',
        value = { web = { lang = orig.lang, kind = orig.kind }, web_translate = target } })
      items[2].separator = true
    end
    show('Subtítulos de la web', items, { footnote = 'Enter añade la pista · luego alt+S la guarda en SRT · ⌫ atrás' })
  end, 90)
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
    hint = (rec.prepare or '?') .. ' (tier ' .. (res.tier or '?') .. ')', icon = 'auto_awesome', active = state.model == 'auto',
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
      if m.name == rec.prepare then hint = hint .. ' · recomendado para este equipo' end
      if m.name == rec.precompute and rec.precompute ~= rec.prepare then
        hint = hint .. ' · recomendado en segundo plano'
      end
      if m.name == rec.best and m.name ~= rec.prepare then hint = hint .. ' · máxima calidad (lento)' end
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

local function translate_items(res)
  local present = {}
  for _, pk in ipairs((res and res.packages) or {}) do
    if pk.present then present[pk.source .. '_' .. pk.target] = true end
  end
  local opus_pairs, opus_present, opus = {}, {}, {}
  for _, e in ipairs((res and res.engines) or {}) do
    if e.id == 'opus-big' then
      opus = e
      for _, pr in ipairs(e.pairs or {}) do
        opus_pairs[pr.source .. '_' .. pr.target] = true
        if pr.present then opus_present[pr.source .. '_' .. pr.target] = true end
      end
    end
  end
  local eng = state.translate_engine
  local listo = {}
  for k in pairs(opus_present) do table.insert(listo, (k:gsub('_', '→'))) end
  table.sort(listo)
  local items = {
    { title = 'Automático', hint = 'OPUS-MT donde ya esté descargado; si no, Argos', icon = 'auto_awesome',
      active = eng == 'auto', value = { engine = 'auto' } },
    { title = 'Rápido (Argos)', hint = 'todos los idiomas · ~90 MB por par', icon = 'bolt', active = eng == 'argos',
      value = { engine = 'argos' } },
    { title = 'Calidad (OPUS-MT, ' .. tostring(opus.size_mb or 234) .. ' MB, se descarga una vez)',
      hint = #listo > 0 and ('listo: ' .. table.concat(listo, ', '))
        or ('español/catalán ↔ inglés · descarga de ' .. tostring(opus.download_mb or 863) .. ' MB'),
      icon = 'workspace_premium', active = eng == 'opus-big', value = { engine = 'opus-big' }, separator = true },
  }
  local ext, emb = selected_sub_file()
  local track = ext or emb
  local src = track and norm_lang(source_language(track)) or state.language
  for _, l in ipairs(LANGUAGES) do
    if l[1] ~= 'auto' and l[1] ~= src then
      local key = src .. '_' .. l[1]
      local direct = present[key]
      local pivot = src ~= 'auto' and present[src .. '_en'] and present['en_' .. l[1]]
      local use_opus = eng ~= 'argos' and opus_pairs[key] and (eng == 'opus-big' or opus_present[key])
      local hint
      if src == 'auto' then hint = 'idioma de origen según la pista'
      elseif use_opus then hint = opus_present[key] and 'OPUS-MT listo' or 'se descargará OPUS-MT (234 MB)'
      elseif direct then hint = 'paquete listo'
      elseif pivot then hint = 'vía inglés'
      else hint = 'se descargará el paquete (~90 MB)' end
      local ready = direct or pivot or (use_opus and opus_present[key])
      table.insert(items, { title = l[2], hint = hint, icon = ready and 'check' or 'cloud_download',
        active = (state.translate ~= nil and state.translate.target == l[1])
          or (state.translate == nil and P:get('translate_target') == l[1]) or nil, value = { translate = l[1] } })
    end
  end
  if not (res and res.engine and res.engine.available) then
    table.insert(items, 4, { title = 'Falta el runtime de traducción: uv sync --extra translate', icon = 'error',
      selectable = false, muted = true })
  end
  return items
end

views.translate = function()
  if not require_mpvd('Traducir a') then return end
  if state.packages then show('Traducir a', translate_items(state.packages)); return end
  show('Traducir a', uosc.loading_items('Consultando paquetes…'))
  rpc.call('subs.translate.models', nil, function(err, res)
    if err then show('Traducir a', uosc.message_items(fail(err, 'subs.translate.models'), 'error')); return end
    state.packages = res
    if state.view == 'translate' then show('Traducir a', translate_items(res)) end
  end)
end

views.save = function()
  local items = {}
  local t = state.task
  if t or state.srt ~= '' then
    local lang = ai_lang()
    local label = 'Pista IA' .. (lang ~= '' and (' (' .. lang .. ')') or '')
    if t and (t.complete or t.status == 'done') then
      table.insert(items, { title = label, hint = string.format('completa · %d líneas', t.cues or 0),
        icon = 'closed_caption', value = { save = 'ai' } })
    else
      table.insert(items, { title = label .. ': guardar lo transcrito', icon = 'closed_caption',
        hint = string.format('%d%% hecho', pct(t and t.progress)), value = { save = 'ai', partial = true } })
      table.insert(items, { title = 'Completar y guardar', hint = 'sigue transcribiendo y guarda al terminar',
        icon = 'hourglass_top', value = { save = 'ai', complete = true } })
    end
  else
    table.insert(items, { title = 'Pista IA: no iniciada', hint = 'alt+c', icon = 'closed_caption',
      selectable = false, muted = true })
  end
  local tr = state.translate
  if tr and tr.out and tr.out ~= '' then
    table.insert(items, { title = 'Traducción (' .. (tr.target or '?') .. ')', hint = tr.engines or '', icon = 'translate',
      value = { save = 'translation' } })
  end
  local rs = state.resync
  if rs and rs.out and rs.out ~= '' then
    table.insert(items, { title = 'Resincronizado', hint = base_name(rs.srt), icon = 'sync_alt', value = { save = 'resync' } })
  end
  local ext, emb = selected_sub_file()
  local sel = ext or emb
  if sel and track_kind(sel) == 'track' then
    local codec = sel.codec or '?'
    local img = IMAGE_CODECS[codec] == true
    local desc = sel.title or base_name(sel['external-filename'])
    if desc == '' then desc = 'pista ' .. tostring(sel.id) end
    table.insert(items, { title = 'Pista seleccionada (' .. codec .. ')', icon = img and 'image' or 'subtitles',
      hint = img and IMAGE_MSG or (desc .. ((sel.lang and sel.lang ~= '') and (' · ' .. sel.lang) or '')),
      muted = img or nil, value = { save = 'track' } })
  end
  local sv = state.save
  if sv and sv.status == 'done' and sv.name then
    table.insert(items, { title = 'Último guardado: ' .. sv.name, hint = string.format('%d líneas', sv.lines or 0),
      icon = 'check_circle', selectable = false, muted = true })
  end
  show('Guardar subtítulos (SRT)', items, {
    footnote = 'Junto al vídeo como <nombre>.<idioma>.srt (si existe: .ia / .resync / (2)). Si la carpeta no admite '
      .. 'escritura o es una URL: ~/Vídeos/MPV-UOS/Subtítulos. alt+S guarda la pista seleccionada.' })
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
      { title = 'Recomendado aquí / en segundo plano', icon = 'auto_awesome', selectable = false,
        hint = ((st.recommended or {}).prepare or '?') .. ' / ' .. ((st.recommended or {}).precompute or '?') },
      { title = 'Modelos presentes', hint = table.concat(st.models_present or {}, ', '), icon = 'storage', selectable = false },
      { title = 'RTF medio del motor', hint = e.rtf and string.format('%.2f', e.rtf) or '—', icon = 'speed',
        selectable = false, separator = true },
    }
    for _, t in ipairs(st.tasks or {}) do
      table.insert(items, { title = (t.path or ''):match('[^/\\]+$') or t.path or '', icon = 'subtitles',
        hint = string.format('%s · %d%% · %d cues · %s%s%s', t.status, math.floor((t.progress or 0) * 100 + 0.5),
          t.cues or 0, task_label(t), t.rtf and string.format(' · RTF %.2f', t.rtf) or '',
          wait_text(t) and (' · ' .. wait_text(t)) or ''), selectable = false })
    end
    if state.view == 'status' then show('Estado del motor', items) end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- events from uosc

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.toggle then
      toggle()
      uosc.close(MENU)
    elseif v.resync then
      resync_selected()
      uosc.close(MENU)
    elseif v.web then
      local url = web_video()
      if url then web_add(url, v.web.lang, v.web.kind, v.web_translate) end
      uosc.close(MENU)
    elseif v.library_subs then
      uosc.close(MENU)
      mp.commandv('script-binding', 'mu_library/library-subs')
    elseif v.translate then
      P:set('translate_target', v.translate)
      translate_selected(v.translate)
      uosc.close(MENU)
    elseif v.engine then
      state.translate_engine = v.engine
      P:set('translate_engine', v.engine)
      publish()
      reopen_current()
    elseif v.save then
      uosc.close(MENU)
      save_request(v.save, { partial = v.partial, complete = v.complete, menu = false })
    elseif v.dual then
      apply_dual(not state.dual)
      P:set('dual', state.dual)
      reopen_current()
    elseif v.chapters then
      apply_chapters((state.ai_chapters or 0) == 0)
      reopen_current()
    elseif v.language then
      state.language = v.language
      P:set('language', v.language)
      publish()
      table.remove(state.stack)
      reopen_current()
    elseif v.model then
      if ev.action == 'remove' and v.model ~= 'auto' then
        rpc.call('asr.models.remove', { name = v.model }, function() state.models = nil; reopen_current() end)
      else
        state.model = v.model
        P:set('model', v.model)
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
      if v.opt == 'auto_start' or v.opt == 'precompute_next' then P:set(v.opt, state[v.opt]) end
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
  if state.translate and state.translate.status == 'running' then
    badge = 'T' .. math.floor((state.translate.progress or 0) * 100 + 0.5) .. '%'
  end
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

open_save_menu = function()
  if not uosc.available() then return end
  state.stack = { { name = 'root', title = 'Subtítulos IA' } }
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'save' })
end

N:binding('subs-menu', open_root)
mp.add_key_binding(nil, 'subs-toggle', toggle)
mp.add_key_binding(nil, 'subs-resync', resync_selected)
mp.add_key_binding(nil, 'subs-save', save_default)
mp.register_script_message('mu-subs-start', function() start(false) end)
mp.register_script_message('mu-subs-stop', stop)
mp.register_script_message('mu-subs-resync', resync_selected)
mp.register_script_message('mu-subs-translate', function(target, engine)
  if engine and engine ~= '' then state.translate_engine = engine end
  translate_selected(target or 'en')
end)
-- mu-subs-save [auto|ai|translation|resync|track] [partial|complete]
mp.register_script_message('mu-subs-save', function(kind, mode)
  if not kind or kind == '' or kind == 'auto' then save_default() return end
  save_request(kind, { partial = mode == 'partial', complete = mode == 'complete' })
end)
mp.register_script_message('mu-subs-dual', function(v) apply_dual(v ~= 'no' and v ~= 'false') end)
mp.register_script_message('mu-subs-chapters', function(on) apply_chapters(on ~= 'no' and on ~= 'false') end)
mp.register_script_message('mu-subs-set', function(key, value)
  if key == 'language' or key == 'model' or key == 'translate_engine' then state[key] = value
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
