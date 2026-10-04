-- mu-record: the ● «Grabar» button (H18, ADR-044). One menu for everything that saves what is playing:
--   · captures with or without subtitles (mpv screenshot);
--   · «grabar desde ahora» until «detener»: live TV/radio with mpv's stream-record (audio only is extracted
--     afterwards by mpvd), internet videos as a yt-dlp --download-sections range, local files as a lossless cut
--     (mpvd study.clip, stream copy). H43: the container is a remembered choice («Formato»), not a guess;
--   · «recortar un tramo»: marks A and B (mpv's ab-loop points, so `l` works too) and saves it the same way.
-- While recording: a red dot and a counter on screen and on the button. Folder: remembered (mu-prefs), default
-- <Vídeos>/MPV-UOS/Grabaciones. Script name: mu_record. State: user-data/mu/record.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')
local brand = require('mu.brand')
local nav = require('mu.nav')
local tr = require('mu.i18n').t
local N = nav.new()

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-record-event'
local MENU = 'mu-record'
local INPUT = 'mu-record-input'
local INPUT_EVENT = 'mu-record-input-event'
local ROOT_TITLE = 'Grabar'

-- H43/B1 · el formato se recuerda. mpv escribe el flujo TAL CUAL (`stream-record`), así que lo que se puede elegir
-- no es «calidad» sino el envase: el del original (MKV, el que nunca falla), MP4 cuando los códecs caben en MP4, o
-- solo el audio, que mpvd extrae después a Opus 128 (`record.audio`).
local P = prefs.ns('mu-record', { dir = '', format = 'copy' })
local FORMAT_IDS = { copy = true, mp4 = true, audio = true }

local function fmt_id()
  local id = P:get('format')
  return FORMAT_IDS[id] and id or 'copy'
end

local state = {
  view = '', stack = {}, items = {}, force_open = false,
  rec = nil,           -- { mode = 'live'|'range', audio = bool, start = media s, started = wall s, file = path, title }
  jobs = {},           -- id → { kind = 'clip'|'download'|'extract', status, file, message }
  last = nil,          -- last finished job
  default_dir = '', last_error = '', input = nil, indicator = '',
}

local function publish()
  local rec = state.rec
  mp.set_property_native('user-data/mu/record', {
    view = state.view, depth = #state.stack, items = state.items, dir = P:get('dir'), default_dir = state.default_dir,
    recording = rec ~= nil, mode = rec and rec.mode or '', audio = rec and rec.audio or false,
    kind = rec and rec.kind or '',
    file = rec and rec.file or '', start = rec and rec.start or -1, last = state.last or { status = '' },
    stream_record = mp.get_property('stream-record') or '', last_error = state.last_error, format = fmt_id(),
    input = state.input and state.input.mode or '', indicator = state.indicator,
  })
end

local function osd(text, secs) mp.osd_message(text, secs or 3) end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

local function hms(s)
  s = math.floor(math.max(0, tonumber(s) or 0))
  if s >= 3600 then return string.format('%d:%02d:%02d', s / 3600, (s % 3600) / 60, s % 60) end
  return string.format('%d:%02d', s / 60, s % 60)
end

local function basename(p) return (tostring(p or ''):match('[^/\\]+$')) or tostring(p or '') end
local function strip_file(p) return (tostring(p or ''):gsub('^file://', '')) end
local function sanitize(name)
  return (tostring(name or ''):gsub('[/\\:*?"<>|%c]', ' '):gsub('%s+', ' '):gsub('^%s+', ''):gsub('%s+$', ''):sub(1, 80))
end

-- ---------------------------------------------------------------------------------------------
-- what is playing

local function current_path() return strip_file(mp.get_property('path') or '') end

local function is_url(p) return p:match('^%a[%w+.-]*://') ~= nil end

-- live: a TV/radio channel of mu-iptv, or a network stream without a known duration or that cannot seek (an MPEG-TS
-- over HTTP gets a duration estimated from its bitrate)
local function is_live()
  local path = mp.get_property('path') or ''
  if path == '' then return false end
  local tv = mp.get_property_native('user-data/mu/iptv') or {}
  if type(tv.current_url) == 'string' and tv.current_url ~= '' and strip_file(tv.current_url) == strip_file(path) then
    return true
  end
  -- opened through yt-dlp: its JSON says whether it is live (a server without byte ranges is not seekable, yet it is
  -- a normal video that yt-dlp can cut)
  local yt = mp.get_property_native('user-data/mu/ytdl') or {}
  if yt.active and strip_file(yt.url or '') == strip_file(path) then
    local res = mp.get_property_native('user-data/mpv/ytdl/json-subprocess-result')
    local out = type(res) == 'table' and type(res.stdout) == 'string' and res.stdout or ''
    return out:find('"is_live":%s*true') ~= nil
  end
  local dur = mp.get_property_number('duration')
  return is_url(path) and (dur == nil or dur <= 0 or mp.get_property_native('seekable') == false)
end

-- 'live' | 'url' | 'local' | nil
local function source_kind()
  local path = current_path()
  if path == '' then return nil end
  if is_live() then return 'live' end
  if is_url(path) then return 'url' end
  return 'local'
end

local KIND_HINT = { live = 'directo', url = 'tramo del vídeo de internet', ['local'] = 'sin recodificar' }

local FORMATS = {
  { id = 'copy', title = tr('Igual que el original (MKV)'), hint = tr('sin recodificar · el que nunca falla'),
    icon = 'content_copy' },
  { id = 'mp4', title = tr('MP4 si los códecs lo permiten'), icon = 'movie' },
  { id = 'audio', title = tr('Solo el audio (Opus 128)'), icon = 'mic' },
}
local FORMAT_TITLES = {}
for _, f in ipairs(FORMATS) do FORMAT_TITLES[f.id] = f.title end
local FORMAT_SHORT = { copy = 'MKV', mp4 = 'MP4', audio = 'solo audio' }

local function record_dir()
  local d = P:get('dir')
  if d == '' then d = state.default_dir end
  if d == '' then d = '~~desktop/' .. brand.folder end   -- H33: the name lives in brand.json, nowhere else
  return mp.command_native({ 'expand-path', d }) or d
end

local function ensure_dir(dir)
  local info = utils.file_info(dir)
  if info and info.is_dir then return true end
  local args = mp.get_property('platform') == 'windows' and { 'cmd', '/c', 'mkdir', dir } or { 'mkdir', '-p', dir }
  local res = mp.command_native({ name = 'subprocess', args = args, playback_only = false })
  return res and res.status == 0
end

local function media_title()
  local tv = mp.get_property_native('user-data/mu/iptv') or {}
  if type(tv.current) == 'table' and tv.current.name then return tv.current.name end
  return mp.get_property('media-title') or basename(current_path())
end

-- ---------------------------------------------------------------------------------------------
-- indicator: red dot + counter (1 Hz) on screen and on the button

local overlay = mp.create_osd_overlay('ass-events')
local tick = nil
local set_button -- forward

local function elapsed()
  local rec = state.rec
  if not rec then return 0 end
  if rec.mode == 'live' then return mp.get_time() - rec.started end
  return (mp.get_property_number('time-pos') or rec.start) - rec.start
end

local function draw()
  if not state.rec then
    overlay:remove()
    state.indicator = ''
    return
  end
  state.indicator = string.format('%s %s', state.rec.audio and 'AUDIO' or 'REC', hms(elapsed()))
  overlay.res_x, overlay.res_y = 1280, 720
  -- amber = the brand's «live / recording» colour (H33)
  overlay.data = '{\\an7\\pos(24,20)\\fs30\\bord2\\3c&H000000&\\1c&H' .. brand.ass('amber') .. '&}●{\\1c&HFFFFFF&} '
    .. state.indicator
  overlay:update()
  set_button()
  publish()
end

local function start_tick()
  if tick then tick:kill() end
  tick = mp.add_periodic_timer(1, draw)
  draw()
end

local function stop_tick()
  if tick then tick:kill(); tick = nil end
  overlay:remove()
  state.indicator = ''
  set_button()
end

set_button = function()
  if not uosc.available() then return end
  local rec = state.rec
  local live_other = not rec and (mp.get_property('stream-record') or '') ~= ''  -- alt+r of mu-iptv
  -- Grabando, el clic PARA; parado, abre el menú (que es donde se elige vídeo o solo audio). Antes abría el menú en
  -- los dos casos, así que para detener había que contestar otra pregunta con la grabación en marcha.
  local stopping = rec ~= nil or live_other
  uosc.set_button('mu-record', {
    icon = stopping and 'stop_circle' or 'fiber_manual_record',
    active = stopping, badge = rec and hms(elapsed()) or (live_other and 'REC' or nil),
    tooltip = stopping and 'Detener la grabación' or 'Grabar',
    command = { 'script-binding', SCRIPT .. (stopping and '/record-toggle' or '/record-menu') },
  })
end

-- ---------------------------------------------------------------------------------------------
-- saving

local function track_job(kind, item, what)
  local id = item.id
  state.jobs[id] = { kind = kind, status = item.status or 'queued', file = item.file or '', message = '' }
  publish()
  osd(tr('💾 Guardando %s…'):format(what))
end

-- mp4 plays a lossless cut from A exactly (edit list over the lead-in from the previous keyframe); only for codecs
-- mp4 carries, else mkv (starts at that keyframe)
local MP4_VIDEO = { h264 = true, hevc = true, av1 = true, mpeg4 = true }
local MP4_AUDIO = { aac = true, mp3 = true, opus = true, ac3 = true, eac3 = true, alac = true, flac = true }
local function mp4_friendly()
  local v, a = nil, nil
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.selected and t.type == 'video' and not t.albumart then v = t.codec end
    if t.selected and t.type == 'audio' then a = t.codec end
  end
  return v ~= nil and MP4_VIDEO[v] == true and (a == nil or MP4_AUDIO[a] == true)
end

-- range [a, b] of what is playing: local → lossless cut (study.clip), internet → yt-dlp section download
local function save_range(a, b, audio, gif, kind, path, title_at_start, mp4_at_start)
  kind = kind or source_kind()   -- a recording keeps the kind seen when it started (seekable flickers while seeking)
  -- and the file it started on: «grabar desde ahora» can end because mpv moved to the next episode, and by then
  -- current_path() is the new file (or empty), so the cut would land on the wrong video or be lost
  path = path or current_path()
  if not kind or b <= a then
    fail({ message = string.format('%s %.2f–%.2f', tostring(kind), a, b) }, 'tramo vacío')
    osd(tr('Tramo vacío: marca un inicio y un final'))
    return
  end
  if not rpc.connected() then fail(nil, 'mpvd no está conectado'); osd(tr('mpvd no está conectado')) return end
  local dir = record_dir()
  local title = sanitize(title_at_start or media_title())
  local what = string.format('%s–%s', hms(a), hms(b))
  if kind == 'local' then
    local mp4 = mp4_at_start
    -- H43/B1: antes se elegía MP4 solo si los códecs cabían, sin decirlo; ahora lo manda la fila «Formato», y con
    -- «igual que el original» siempre sale MKV (empieza en el fotograma clave anterior a la marca, no en la marca).
    if mp4 == nil then mp4 = fmt_id() == 'mp4' and mp4_friendly() end
    local fmt = gif and 'gif' or (audio and 'audio-copy' or (mp4 and 'mp4-copy' or 'mkv-copy'))
    -- max_seconds = 0: no cap. The 600 s of «clips de estudio» made every recording over ten minutes fail
    rpc.call('study.clip', { path = path, start = a, ['end'] = b, format = fmt, dir = dir, title = title,
                             notify = SCRIPT, audio_track = nil, max_seconds = 0 }, function(err, item)
      if err then osd(tr('Grabar: %s'):format(fail(err, 'study.clip')), 5) return end
      track_job('clip', item, what)
    end, 20)
  elseif kind == 'url' then
    if gif then osd(tr('GIF: solo con archivos locales')) return end
    local h = mp.get_property_number('height')
    local container = (mp4_at_start or fmt_id() == 'mp4') and 'mp4' or 'mkv'
    local spec = audio and { kind = 'audio_original' } or { kind = 'video', height = h, container = container }
    spec.url = path
    spec.sections = string.format('*%.2f-%.2f', a, b)
    spec.title = title .. ' [' .. what .. ']'
    spec.out_dir = dir
    spec.notify = SCRIPT
    rpc.call('ytdl.download', spec, function(err, item)
      if err then osd(tr('Grabar: %s'):format(fail(err, 'ytdl.download')), 5) return end
      track_job('download', item, what)
    end, 30)
  else
    -- live: the part already in mpv's cache (seekable range) is dumped with dump-cache
    ensure_dir(dir)
    local file = utils.join_path(dir, title .. ' ' .. os.date('%Y-%m-%d %H.%M.%S') .. (audio and '.mka' or '.mkv'))
    mp.command_native_async({ 'dump-cache', tostring(a), tostring(b), file }, function(ok, _, err)
      if not ok then osd(tr('No está en la caché: %s'):format(fail({ message = tostring(err) }, 'dump-cache')), 5) return end
      state.last = { file = file, status = 'done' }
      publish()
      osd(tr('✅ Guardado: %s'):format(basename(file)), 5)
    end)
  end
end

-- `format`: 'copy' | 'mp4' | 'audio'. Sin él, el recordado en la fila «Formato».
local function start_recording(format)
  local fmt = FORMAT_IDS[format or ''] and format or fmt_id()
  local audio = fmt == 'audio'
  local kind = source_kind()
  if not kind then osd(tr('Nada que grabar')) return end
  if state.rec then osd(tr('Ya se está grabando')) return end
  if kind == 'live' then
    local dir = record_dir()
    if not ensure_dir(dir) then osd(tr('No se pudo crear la carpeta %s'):format(dir)) return end
    local radio = mp.get_property_native('vid') == false
    -- Medido: un VP8 NO entra en MP4 («Could not write header (incorrect codec parameters?)») y el fichero se queda
    -- vacío, así que si los códecs no caben se avisa y se graba en MKV, que es lo que pedía el formato por defecto.
    local mp4 = fmt == 'mp4' and not radio and mp4_friendly()
    if fmt == 'mp4' and not mp4 then osd(tr('Estos códecs no caben en un MP4: se graba en MKV'), 5) end
    local ext = (audio or radio) and '.mka' or (mp4 and '.mp4' or '.mkv')
    local file = utils.join_path(dir, sanitize(media_title()) .. ' ' .. os.date('%Y-%m-%d %H.%M.%S') .. ext)
    mp.set_property('stream-record', file)
    state.rec = { mode = 'live', audio = audio, fmt = fmt, started = mp.get_time(),
                  start = mp.get_property_number('time-pos') or 0, file = file }
  else
    state.rec = { mode = 'range', audio = audio, fmt = fmt, start = mp.get_property_number('time-pos') or 0,
                  started = mp.get_time(), kind = kind, path = current_path(), title = media_title(),
                  mp4 = fmt == 'mp4' and mp4_friendly() }
  end
  publish()
  start_tick()
  osd(string.format('● Grabando %s desde %s', audio and 'el audio' or KIND_HINT[kind] or '', hms(state.rec.start)))
end

local function stop_recording(reason)
  local rec = state.rec
  if not rec then return end
  state.rec = nil
  stop_tick()
  if rec.mode == 'live' then
    mp.set_property('stream-record', '')
    publish()
    if rec.audio and mp.get_property_native('vid') ~= false and rpc.connected() then
      rpc.call('record.audio', { file = rec.file, notify = SCRIPT, remove = true }, function(err, item)
        if err then osd(tr('Solo audio: %s'):format(fail(err, 'record.audio')), 5) return end
        track_job('extract', item, 'el audio')
      end, 20)
    else
      state.last = { file = rec.file, status = 'done' }
      publish()
      osd(tr('⏹ Grabación guardada: %s'):format(basename(rec.file)), 5)
    end
    return
  end
  local b = reason == 'end' and (rec.last_pos or rec.start) or (mp.get_property_number('time-pos') or rec.start)
  publish()
  save_range(math.min(rec.start, b), math.max(rec.start, b), rec.audio, false, rec.kind, rec.path, rec.title, rec.mp4)
end

-- progress / completion pushed by mpvd (study clips, yt-dlp downloads, audio extraction) with notify = mu_record
mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  local item = ev.clip or ev.item or ev.download or ev.job
  if type(item) ~= 'table' or not item.id or not state.jobs[item.id] then return end
  local job = state.jobs[item.id]
  job.status = item.status or job.status
  job.file = item.file or (item.outputs and item.outputs[1]) or job.file
  job.message = item.message or item.error or ''
  if job.status == 'done' or job.status == 'finished' then
    state.last = { file = job.file, status = 'done' }
    state.jobs[item.id] = nil
    osd(tr('✅ Guardado: %s'):format(basename(job.file)), 5)
  elseif job.status == 'failed' or job.status == 'error' or job.status == 'cancelled' then
    state.last = { file = '', status = 'failed', message = job.message }
    state.jobs[item.id] = nil
    osd(tr('❌ Grabar: %s'):format(job.message ~= '' and job.message or job.status), 5)
  end
  publish()
end)

mp.observe_property('time-pos', 'number', function(_, v)
  if v and state.rec then state.rec.last_pos = v end
end)

mp.register_event('end-file', function()
  if state.rec then stop_recording('end') end
end)

mp.observe_property('stream-record', 'string', function(_, value)
  -- someone else stopped (or replaced) the recording: mu-iptv writes this property too. Without this the red dot and
  -- the counter kept running for ever over a file mpv had already closed.
  local rec = state.rec
  if rec and rec.mode == 'live' and (value or '') ~= rec.file then
    state.rec = nil
    stop_tick()
    state.last = { file = rec.file, status = 'done' }
    if (value or '') == '' then osd(tr('⏹ Grabación guardada: %s'):format(basename(rec.file)), 5) end
  end
  set_button()
  publish()
end)

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 100 then break end
    table.insert(out, { title = it.title or '', hint = it.hint or '', value = it.value or '' })
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

local function mark_hint(prop)
  local v = mp.get_property_number(prop)
  return v and hms(v) or 'sin marcar'
end

views.root = function()
  local kind = source_kind()
  local items = {
    { title = tr('Captura de pantalla'), hint = tr('con subtítulos · ctrl+s'), icon = 'photo_camera',
      value = { cmd = { 'async', 'screenshot', 'subtitles' } } },
    { title = tr('Captura sin subtítulos'), icon = 'photo_camera', value = { cmd = { 'async', 'screenshot', 'video' } },
      separator = true },
  }
  if state.rec then
    table.insert(items, { title = tr('Detener y guardar'), icon = 'stop_circle', bold = true, active = true,
      hint = (state.rec.audio and 'audio · ' or '') .. hms(elapsed()), value = { stop = true } })
  elseif kind then
    -- H43/B1 · una sola fila para grabar: el envase ya no es otra puerta, es la fila «Formato» de debajo
    table.insert(items, { title = tr('Grabar desde ahora'), icon = 'fiber_manual_record',
                          hint = (KIND_HINT[kind] or '') .. ' · ' .. (FORMAT_SHORT[fmt_id()] or ''),
                          value = { start = true } })
  else
    table.insert(items, { title = tr('Abre un vídeo, un canal o una radio para grabar'), icon = 'info', selectable = false,
                          muted = true })
  end
  if kind then
    table.insert(items, { title = tr('Formato'), icon = 'tune', hint = FORMAT_TITLES[fmt_id()],
                          value = { view = 'format' } })
    table.insert(items, { title = tr('Recortar un tramo…'), icon = 'content_cut', value = { view = 'cut' },
      hint = (mp.get_property_number('ab-loop-a') and mp.get_property_number('ab-loop-b'))
        and (mark_hint('ab-loop-a') .. '–' .. mark_hint('ab-loop-b')) or nil })
  end
  -- H43/B2 · programar estaba escondido: solo se llegaba con Tab dentro de la lista de un canal
  table.insert(items, { title = tr('Programar una grabación…'), hint = tr('TV o radio: canal, inicio y fin'),
                        icon = 'add_alarm', value = { child = { script = 'mu_iptv', entry = 'tv-schedule-new' } } })
  items[#items].separator = true
  local dir = P:get('dir')
  table.insert(items, { title = tr('Carpeta de grabaciones'), icon = 'folder', hint = dir ~= '' and dir or 'predeterminada',
                        value = { choose_dir = true } })
  if state.last and state.last.file and state.last.file ~= '' then
    table.insert(items, { title = tr('Última: %s'):format(basename(state.last.file)), icon = 'check_circle',
                          muted = true,
                          value = { show = state.last.file } })
  end
  local busy = 0
  for _ in pairs(state.jobs) do busy = busy + 1 end
  if busy > 0 then
    table.insert(items, { title = string.format('Guardando %d…', busy), icon = 'hourglass_top', selectable = false,
                          muted = true })
  end
  show(ROOT_TITLE, items, { footnote = tr('Enter elige · ⌫ atrás · Esc cierra') })
end

-- H43/B1 · el envase, con lo que de verdad se puede elegir y lo que pasa si no cabe
views.format = function()
  local cur = fmt_id()
  local friendly = mp4_friendly()
  local items = {}
  for _, f in ipairs(FORMATS) do
    local hint = f.hint
    if f.id == 'mp4' then
      hint = friendly and 'estos códecs caben · empieza exactamente en la marca'
        or 'estos códecs NO caben: se grabaría en MKV'
    end
    items[#items + 1] = { title = f.title, hint = hint, icon = f.icon, active = f.id == cur,
                          value = { format = f.id } }
  end
  show(tr('Formato de la grabación'), items, { footnote = tr('Se recuerda para la próxima vez') })
end

views.cut = function()
  local kind = source_kind()
  local a, b = mp.get_property_number('ab-loop-a'), mp.get_property_number('ab-loop-b')
  local ready = a ~= nil and b ~= nil and b > a
  local items = {
    { title = tr('Marcar el inicio aquí'), icon = 'first_page', hint = mark_hint('ab-loop-a'), value = { mark = 'a' } },
    { title = tr('Marcar el final aquí'), icon = 'last_page', hint = mark_hint('ab-loop-b'), value = { mark = 'b' },
      separator = true },
    { title = tr('Guardar el tramo'), icon = 'save',
      hint = ready and (hms(b - a) .. ' · ' .. (FORMAT_SHORT[fmt_id()] or '')) or 'marca inicio y final',
      value = { save = true }, muted = not ready },
    { title = tr('Formato'), icon = 'tune', hint = FORMAT_TITLES[fmt_id()], value = { view = 'format' } },
  }
  if kind == 'local' then
    table.insert(items, { title = tr('Guardar el tramo como GIF'), icon = 'gif_box',
                          value = { save = 'gif' }, muted = not ready })
  end
  items[#items].separator = true
  table.insert(items, { title = tr('Quitar las marcas'), icon = 'backspace', value = { clear = true } })
  show(tr('Recortar un tramo'), items, { footnote = tr('Las marcas son las del bucle A-B (tecla l)') })
end

-- ---------------------------------------------------------------------------------------------
-- folder text box (a uosc palette whose query is the text)

local function input_menu(query)
  state.input.query = query or ''
  local items = query ~= '' and { { title = tr('Usar: %s'):format(query), icon = 'check',
                                    value = { save = query } } }
    or { { title = tr('Escribe o pega la carpeta (vacío = predeterminada)'), icon = 'edit', value = { save = '' } } }
  return { type = INPUT, title = tr('Carpeta de grabaciones'), items = items, callback = { SCRIPT, INPUT_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    search_suggestion = query, footnote = tr('Enter guarda · ⌫ en vacío vuelve') }
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if not state.input then return end
  if ev.type == 'search' then
    uosc.update(input_menu(ev.query or ''))
  elseif ev.type == 'back' or (ev.type == 'activate' and type(ev.value) == 'table' and ev.value.save ~= nil) then
    if ev.type == 'activate' then
      P:set('dir', ev.value.save)
      osd(tr('Carpeta de grabaciones: %s'):format(record_dir()))
    end
    state.input = nil
    uosc.close(INPUT)
    reopen_current(true)
  end
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.cmd then
      uosc.close(MENU)
      mp.command_native(v.cmd)
    elseif v.start then
      uosc.close(MENU)
      start_recording(type(v.start) == 'string' and v.start or nil)
    elseif v.stop then
      uosc.close(MENU)
      stop_recording('user')
    elseif v.mark then
      mp.set_property_number(v.mark == 'a' and 'ab-loop-a' or 'ab-loop-b', mp.get_property_number('time-pos') or 0)
      reopen_current()
    elseif v.clear then
      mp.set_property('ab-loop-a', 'no')
      mp.set_property('ab-loop-b', 'no')
      reopen_current()
    elseif v.save then
      local a, b = mp.get_property_number('ab-loop-a'), mp.get_property_number('ab-loop-b')
      if not (a and b and b > a) then osd(tr('Marca primero el inicio y el final')) return end
      uosc.close(MENU)
      local gif = v.save == 'gif'
      save_range(a, b, not gif and fmt_id() == 'audio', gif)
    elseif v.choose_dir then
      local d = P:get('dir')
      state.input = { mode = 'dir', query = d }
      publish()
      uosc.open(input_menu(d))
    elseif v.format then
      P:set('format', v.format)
      table.remove(state.stack)
      -- la fila se elige desde el menú de grabar, así que detrás hay al menos la raíz; si no hubiera nada (el menú
      -- se cerró y llega el evento con retraso) se vuelve a ella en vez de quedarse sin pintar nada
      if #state.stack == 0 then table.insert(state.stack, { name = 'root' }) end
      reopen_current()
    elseif v.child then
      local crumbs = {}
      for _, c in ipairs(N.parent.crumbs or {}) do crumbs[#crumbs + 1] = c end
      for _, spec in ipairs(state.stack) do crumbs[#crumbs + 1] = spec.title end
      nav.open_child(v.child.script, v.child.entry, crumbs, state.view)
    elseif v.show then
      mp.commandv('script-binding', 'uosc/show-in-directory')
    elseif v.view then
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
  if t == MENU or t == INPUT then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    local open = uosc.open_type()
    if open == MENU or open == INPUT then return end
    -- H63/N3 · si acabamos de pedir nuestro menú y uosc aún no lo ha confirmado, no hay nada que olvidar
    if uosc.asking(MENU) then return end
    if #state.stack > 0 or state.view ~= '' or state.input then
      state.stack, state.view, state.input = {}, '', nil
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- bindings

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

N:binding('record-menu', open_root)
mp.add_key_binding(nil, 'record-toggle', function()
  if state.rec then
    stop_recording('user')
  elseif (mp.get_property('stream-record') or '') ~= '' then
    mp.set_property('stream-record', '')   -- la empezó otro sitio: parar es cerrar el fichero, no abrir otra
  else
    start_recording(false)
  end
end)
mp.add_key_binding(nil, 'record-audio-toggle', function()
  if state.rec then stop_recording('user') else start_recording('audio') end
end)
mp.register_script_message('mu-record-start', function(what) start_recording(what) end)
mp.register_script_message('mu-record-stop', function() stop_recording('user') end)
mp.register_script_message('mu-record-range', function(a, b, what)
  save_range(tonumber(a) or 0, tonumber(b) or 0, what == 'audio', what == 'gif')
end)

local function fetch_defaults()
  if not rpc.connected() then return end
  rpc.call('record.defaults', nil, function(err, res)
    if not err and type(res) == 'table' then state.default_dir = res.dir or ''; publish() end
  end, 10)
end
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button() end
  if core and core.mpvd == 'connected' and state.default_dir == '' then fetch_defaults() end
end)
mp.register_script_message('uosc-version', set_button)

publish()
msg.info('mu-record loaded')
