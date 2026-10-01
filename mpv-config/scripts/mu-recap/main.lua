-- mu-recap: «¿Qué me he perdido?» (H27, ADR-062). A few lines that sum up what was said while you were away, picked
-- by mpvd (recap.summarize) from the subtitles of the video or its AI transcription; Enter jumps to that moment.
--   · Away = the window lost focus or was minimised while playing (also `script-message mu-recap-away` /
--     `mu-recap-back`, used by the tests: headless mpv has no window). Coming back after `min_away` seconds shows a
--     hint with the key; the recap then covers exactly that stretch.
--   · Otherwise the key sums up the last `minutes` minutes.
-- Binding: recap. Script name: mu_recap. State for the tests: user-data/mu/recap.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')

local N = nav.new()
local SCRIPT = mp.get_script_name()
local MENU = 'mu-recap'
local EVENT = 'mu-recap-event'
local ROOT_TITLE = '¿Qué me he perdido?'

local opts = {
  minutes = 5,          -- what the key sums up when you were not away
  min_away = 60,        -- seconds of video missed before the hint appears
  hint_seconds = 6,
  osd_seconds = 3,
}
options.read_options(opts, 'mu-recap')

-- text subtitle codecs ffmpeg can turn into SRT (bitmap ones like PGS/DVB cannot)
local TEXT_CODECS = { subrip = true, ass = true, ssa = true, webvtt = true, mov_text = true, text = true }

local state = { away_from = nil, missed = nil, status = 'idle', result = nil, last_error = '', items = {} }

local function publish()
  mp.set_property_native('user-data/mu/recap', {
    status = state.status, missed = state.missed, away = state.away_from ~= nil, result = state.result,
    last_error = state.last_error, items = state.items,
  })
end

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function clock(t)
  t = math.max(0, math.floor(t or 0))
  local h, m, s = math.floor(t / 3600), math.floor(t / 60) % 60, t % 60
  if h > 0 then return string.format('%d:%02d:%02d', h, m, s) end
  return string.format('%d:%02d', m, s)
end

-- -- away / back ------------------------------------------------------------------------------------

local function now() return mp.get_property_number('time-pos') end

local function went_away()
  if state.away_from or mp.get_property_native('pause') or mp.get_property_native('idle-active') then return end
  state.away_from = now()
  publish()
end

local function came_back()
  local from, to = state.away_from, now()
  state.away_from = nil
  if from and to and to - from >= opts.min_away then
    state.missed = { from = from, to = to }
    osd(string.format('¿Te has perdido algo? alt+R resume lo que se dijo (%s)', clock(to - from)), opts.hint_seconds)
  end
  publish()
end

local focused, minimized = true, false
local function window_changed()
  if focused == false or minimized == true then went_away() else came_back() end
end
mp.observe_property('focused', 'native', function(_, v)
  if v == nil then return end   -- no window (headless): nothing to follow
  focused = v
  window_changed()
end)
mp.observe_property('window-minimized', 'native', function(_, v)
  minimized = v == true
  window_changed()
end)
-- a pause while away means nothing was missed from then on
mp.observe_property('pause', 'bool', function(_, paused)
  if paused and state.away_from then came_back() end
end)
mp.register_script_message('mu-recap-away', went_away)
mp.register_script_message('mu-recap-back', came_back)
mp.register_event('start-file', function()
  state.away_from, state.missed, state.result = nil, nil, nil
  publish()
end)

-- -- where the words come from ----------------------------------------------------------------------

local function abs_path()
  local path = mp.get_property('path')
  if not path or path == '' then return nil end
  return mp.command_native({ 'normalize-path', path }) or path
end

local function is_url(p) return type(p) == 'string' and p:find('^%a[%w+.-]*://') ~= nil end

local function source_params()
  local path = abs_path()
  local function from_track(t)
    if t.external then
      local f = t['external-filename']
      if f and not is_url(f) then return { sub_path = f } end
    elseif TEXT_CODECS[t.codec or ''] and t['ff-index'] and path and not is_url(path) then
      return { path = path, ff_index = t['ff-index'] }
    end
    return nil
  end
  local current = mp.get_property_native('current-tracks/sub')
  local p = current and from_track(current)
  if p then return p end
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == 'sub' then
      p = from_track(t)
      if p then return p end
    end
  end
  if path and not is_url(path) then return { path = path } end   -- AI transcription of the file, if mpvd has it
  return nil
end

-- -- menu ----------------------------------------------------------------------------------------------

local function show(items, title, open)
  state.items = {}
  for _, it in ipairs(items) do state.items[#state.items + 1] = { title = it.title or '', hint = it.hint or '' } end
  publish()
  local menu = N:frame({ type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, keep_open = true },
                       { { name = 'root' } })
  if open or uosc.open_type() ~= MENU then uosc.open(menu) else uosc.update(menu) end
end

local function stretch()
  local pos = now() or 0
  local m = state.missed
  if m and math.abs(pos - m.to) <= 30 then return m.from, m.to, true end
  return math.max(0, pos - opts.minutes * 60), pos, false
end

local function recap()
  if mp.get_property_native('idle-active') then osd('Abre un vídeo primero') return end
  local from, to, was_away = stretch()
  local title = string.format('%s · %s–%s', ROOT_TITLE, clock(from), clock(to))
  if to - from < 5 then osd('Aún no ha pasado nada que resumir') return end
  local src = source_params()
  if not src then
    state.status, state.last_error = 'error', 'no-source'
    publish()
    show(uosc.message_items('Este vídeo no tiene subtítulos de texto: activa unos o los subtítulos IA (alt+c)', 'info'),
         title, true)
    return
  end
  if not rpc.connected() then
    show(uosc.message_items('mpvd no está conectado: espera unos segundos', 'error'), title, true)
    return
  end
  state.status, state.last_error = 'working', ''
  show(uosc.loading_items('Leyendo lo que se dijo…'), title, true)
  src.start, src['end'] = from, to
  rpc.call('recap.summarize', src, function(err, res)
    if uosc.open_type() ~= MENU then state.status = 'idle'; publish(); return end
    if err then
      state.status, state.last_error = 'error', err.message or tostring(err)
      show(uosc.message_items(state.last_error, 'error'), title)
      return
    end
    state.status, state.result = 'done', { method = res.method, source = res.source, count = #(res.sentences or {}),
                                           from = from, to = to, away = was_away }
    local items = {}
    for _, s in ipairs(res.sentences or {}) do
      items[#items + 1] = { title = s.text, hint = clock(s.start), value = { seek = s.start } }
    end
    if #items == 0 then
      items = uosc.message_items('En ese tramo no se dijo nada', 'info')
    else
      items[#items].separator = true
      items[#items + 1] = { title = 'Volver a verlo desde ' .. clock(from), icon = 'replay', value = { seek = from } }
      items[#items + 1] = { title = 'Índice del vídeo entero', icon = 'list', hint = 'secciones y frases clave',
                            value = { outline = true } }
    end
    show(items, title)
  end, 30)
end

-- H38/G2-G5 · el índice del vídeo: secciones con su título y, dentro, las frases clave con su minuto. Lo compone mpvd
-- a partir del subtítulo que ya hay (`recap.outline`), sin escribir nada con un modelo y sin lanzar ninguna
-- transcripción: si no hay subtítulo, se dice, porque transcribir una película para hacer un índice son horas.
local function outline()
  if mp.get_property_native('idle-active') then osd('Abre un vídeo primero') return end
  local title = 'Índice del vídeo'
  local src = source_params()
  if not src then
    state.status, state.last_error = 'error', 'no-source'
    publish()
    show(uosc.message_items('Para el índice hace falta un subtítulo de texto: pon uno o créalo con IA (alt+c)', 'info'),
         title, true)
    return
  end
  if not rpc.connected() then
    show(uosc.message_items('mpvd no está conectado: espera unos segundos', 'error'), title, true)
    return
  end
  src.duration = mp.get_property_number('duration')
  state.status, state.last_error = 'working', ''
  show(uosc.loading_items('Leyendo todo lo que se dice…'), title, true)
  rpc.call('recap.outline', src, function(err, res)
    if uosc.open_type() ~= MENU then state.status = 'idle'; publish(); return end
    if err then
      state.status, state.last_error = 'error', err.message or tostring(err)
      show(uosc.message_items(state.last_error, 'error'), title)
      return
    end
    local secs = res.sections or {}
    state.status = 'done'
    state.result = { method = res.method, source = res.source, count = #secs, outline = true }
    local items = {}
    for i, sec in ipairs(secs) do
      local sub = { { title = 'Ir a ' .. clock(sec.start), icon = 'play_arrow', value = { seek = sec.start } } }
      for _, pt in ipairs(sec.points or {}) do
        sub[#sub + 1] = { title = pt.text, hint = clock(pt.start), value = { seek = pt.start } }
      end
      items[#items + 1] = { title = string.format('%d. %s', i, sec.title or ''), hint = clock(sec.start),
                            icon = 'bookmark', items = sub }
    end
    if #items == 0 then
      items = uosc.message_items('No hay suficiente diálogo para hacer un índice', 'info')
    end
    show(items, title)
  end, 60)
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back or ev.type == 'back' then
    if not N:leave() then uosc.close(MENU) end
  elseif ev.type == 'activate' and type(ev.value) == 'table' and ev.value.outline then
    outline()
  elseif ev.type == 'activate' and type(ev.value) == 'table' and ev.value.seek then
    mp.commandv('seek', tostring(ev.value.seek), 'absolute+exact')
    state.missed = nil
    publish()
    uosc.close(MENU)
  end
end)

N:binding('recap', recap)
N:binding('outline', outline)
publish()
msg.info('mu-recap loaded')
