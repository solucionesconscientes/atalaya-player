-- mu-av: "Sonido e imagen" menu for MPV-UOS. Toggles labelled libavfilter graphs (`af add @mu-x:lavfi=[...]`) validated
-- against the mpv 0.41 / FFmpeg 8 installed here (docs/AUDIO_VIDEO.md): clear dialogue, night mode, noise reduction
-- (arnndn with an RNNoise model from mpvd, afftdn fallback), binaural for headphones (sofalizer with a SOFA HRTF, crossfeed
-- fallback), photosensitivity protection (video), plus a stutter diagnosis view with a "light profile" toggle.
-- Script name: mu_av. Bindings: av-menu (alt+v), av-night (alt+n). State in user-data/mu/av.
-- Preferences (mu/prefs.lua, namespace mu-av): active filters by name and the light profile, saved only on explicit
-- user actions (menu, keys, script messages) and restored when mpv starts.
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
local EVENT = 'mu-av-event'
local MENU = 'mu-av'

local opts = {
  osd_seconds = 3,
  denoise_mix = 0.9,        -- arnndn mix (1 = only denoised signal)
  night_limit = 0.7,        -- alimiter ceiling in night mode
  rnnoise_path = '',        -- override model paths (otherwise asked to mpvd / vendor/models)
  sofa_path = '',
}
options.read_options(opts, 'mu-av')

-- equalizer presets (H24): peaking biquads; boosts end in a limiter without auto-level so nothing clips
local LIMIT = 'alimiter=limit=0.97:level=0'
local EQ_PRESETS = {
  { id = 'bass', title = 'Más graves', graph = 'equalizer=f=60:t=q:w=0.8:g=5,equalizer=f=150:t=q:w=1:g=2,' .. LIMIT },
  { id = 'less_bass', title = 'Menos graves', graph = 'equalizer=f=80:t=q:w=0.8:g=-6' },
  { id = 'treble', title = 'Más agudos', graph = 'equalizer=f=4000:t=q:w=1:g=1.5,equalizer=f=9000:t=q:w=0.7:g=4,' .. LIMIT },
  { id = 'voice', title = 'Voz', graph = 'equalizer=f=200:t=q:w=1:g=-2,equalizer=f=2500:t=q:w=1:g=4,' .. LIMIT },
  { id = 'music', title = 'Música', graph = 'equalizer=f=70:t=q:w=0.8:g=4,equalizer=f=1000:t=q:w=1:g=-2,' ..
    'equalizer=f=10000:t=q:w=0.8:g=3,' .. LIMIT },
  { id = 'laptop', title = 'Altavoces del portátil', graph = 'highpass=f=120,equalizer=f=250:t=q:w=1:g=-2,' ..
    'equalizer=f=3000:t=q:w=1:g=3,' .. LIMIT },
  { id = 'headphones', title = 'Auriculares', graph = 'equalizer=f=50:t=q:w=0.8:g=3,equalizer=f=3500:t=q:w=1.5:g=-2,' ..
    'equalizer=f=8000:t=q:w=1:g=1.5,' .. LIMIT },
  -- H32: generic corrections by headphone type (towards a neutral target; no per-model measurements are shipped)
  { id = 'hp_inear', title = 'Auriculares de botón', headphones = true,
    graph = 'lowshelf=f=90:g=4,equalizer=f=6000:t=q:w=1.5:g=-2.5,' .. LIMIT },
  { id = 'hp_closed', title = 'Auriculares cerrados', headphones = true,
    graph = 'equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3000:t=q:w=1.2:g=1.5,highshelf=f=9000:g=1,' .. LIMIT },
  { id = 'hp_open', title = 'Auriculares abiertos', headphones = true,
    graph = 'lowshelf=f=60:g=5,equalizer=f=5000:t=q:w=1.5:g=-1.5,' .. LIMIT },
}
local EQ_GRAPHS = {}
local EQ_TITLES = {}
for _, e in ipairs(EQ_PRESETS) do EQ_GRAPHS[e.id] = e.graph; EQ_TITLES[e.id] = e.title end

-- graphs verified headless (tools: mpv --af=help, --vf=help; run with --end=3 and no lavfi errors in the log)
local FILTERS = {
  dialog = { kind = 'af', title = 'Diálogo claro', icon = 'record_voice_over',
    desc = 'realza la voz: paso alto 70 Hz, normalizador dinámico, +2,5 dB en 2,8 kHz',
    graph = function() return 'highpass=f=70,dynaudnorm=f=250:g=11:p=0.85:m=8,equalizer=f=2800:t=q:w=1.2:g=2.5' end },
  night = { kind = 'af', title = 'Modo noche', icon = 'bedtime',
    desc = 'comprime la dinámica y limita los picos (explosiones más bajas, diálogos audibles)',
    graph = function()
      return string.format('acompressor=threshold=-24dB:ratio=6:attack=5:release=400:makeup=4dB,alimiter=limit=%.2f',
        opts.night_limit)
    end },
  denoise = { kind = 'af', title = 'Reducción de ruido', icon = 'noise_control_off',
    desc = 'RNNoise (arnndn) si hay modelo; si no, afftdn',
    graph = function(state)
      if state.models.rnnoise then
        return string.format('arnndn=m=%s:mix=%.2f', state.models.rnnoise, opts.denoise_mix)
      end
      return 'afftdn=nr=12:nf=-40'
    end },
  binaural = { kind = 'af', title = 'Binaural para auriculares', icon = 'headphones',
    desc = 'HRTF (sofalizer) si hay archivo SOFA; si no, crossfeed',
    graph = function(state)
      if state.models.sofa then return string.format('sofalizer=sofa=%s:type=freq', state.models.sofa) end
      return 'crossfeed=strength=0.5:range=0.5'
    end },
  -- H24: same loudness from one video to the next. A slow dynaudnorm (7.5 s look-ahead, target RMS): loudnorm was
  -- discarded (resamples to 192 kHz, several times the CPU) and ReplayGain tags are rare outside music (ADR-051)
  level = { kind = 'af', title = 'Volumen parejo · siempre', icon = 'volume_up',
    desc = 'funciona en todo (también sin etiquetas), cuesta algo de CPU',
    graph = function() return 'dynaudnorm=f=500:g=31:p=0.9:m=8:r=0.15' end },
  eq = { kind = 'af', title = 'Ecualizador', icon = 'graphic_eq',
    desc = 'perfiles de graves, agudos, voz y altavoces',
    graph = function(st) return EQ_GRAPHS[st.eq] or '' end },
  photo = { kind = 'vf', title = 'Protección fotosensible', icon = 'flash_off',
    desc = 'atenúa destellos rápidos (photosensitivity)',
    graph = function() return 'photosensitivity=frames=30:threshold=1:bypass=0' end },
}
local ORDER = { 'dialog', 'night', 'level', 'eq', 'denoise', 'binaural', 'photo' }

local P = prefs.ns('mu-av', { filters = {}, light = false, eq = '', audio_minimized = true,
                              told_minimized = false }, function(key, v)
  if key == 'eq' then return v == '' or EQ_GRAPHS[v] ~= nil end
  if key ~= 'filters' then return true end
  for _, name in pairs(v) do if type(name) ~= 'string' then return false end end
  return true
end)

-- light profile: cheap scalers, no debanding/interpolation (applied with `set`, remembered to restore)
local LIGHT = { scale = 'bilinear', dscale = 'bilinear', cscale = 'bilinear', deband = 'no', interpolation = 'no',
  ['video-sync'] = 'audio' }

local state = {
  models = { rnnoise = nil, sofa = nil },   -- resolved paths
  models_info = nil,                        -- av.models result
  downloads = {},
  light = false,
  light_saved = nil,
  view = '',
  stack = {},
  items = {},
  last_error = '',
  diag = nil,
  force_open = false,
  eq = '',                                  -- equalizer preset id ('' = flat)
}

local set_button_state
local reopen_current

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function label(name) return 'mu-' .. name end

local function active_filters()
  local out = {}
  for _, kind in ipairs({ 'af', 'vf' }) do
    for _, f in ipairs(mp.get_property_native(kind) or {}) do
      if f.label and f.label:match('^mu%-') and f.enabled ~= false then out[f.label:sub(4)] = true end
    end
  end
  return out
end

local function publish()
  local presets = {}
  for _, e in ipairs(EQ_PRESETS) do
    presets[#presets + 1] = { id = e.id, title = e.title, headphones = e.headphones or e.id == 'headphones' }
  end
  mp.set_property_native('user-data/mu/av', {
    filters = active_filters(), light = state.light, eq = state.eq, eq_presets = presets,
    minimized_audio = state.minimized_audio or false, audio_minimized = P:get('audio_minimized') == true,
    models = state.models, view = state.view, items = state.items,
    last_error = state.last_error, diag = state.diag or {},
  })
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 100 then break end
    table.insert(out, { title = it.title or '', hint = it.hint or '', icon = it.icon or '', value = it.value or '',
      active = it.active or false })
  end
  state.items = out
end

-- ---------------------------------------------------------------------------------------------
-- models (paths for arnndn / sofalizer)

local function detect_root()
  local env = os.getenv('MPV_UOS_ROOT')
  if env and env ~= '' then return env end
  local conf = mp.command_native({ 'expand-path', '~~/' }) or ''
  return conf:match('^(.*)[/\\][^/\\]+[/\\]?$') or ''
end

local function file_exists(p)
  local info = p and p ~= '' and utils.file_info(p)
  return info and info.is_file
end

local function resolve_models_local()
  local root = detect_root()
  local function pick(override, rel)
    if override ~= '' and file_exists(override) then return override end
    local p = utils.join_path(root, rel)
    if file_exists(p) then return p end
    return nil
  end
  state.models.rnnoise = pick(opts.rnnoise_path, 'vendor/models/rnnoise/sh.rnnn')
    or pick('', 'vendor/models/rnnoise/bd.rnnn')
  state.models.sofa = pick(opts.sofa_path, 'vendor/models/sofa/mit_kemar_normal_pinna.sofa')
end

local function refresh_models(cb)
  if not rpc.connected() then resolve_models_local(); publish(); if cb then cb() end return end
  rpc.call('av.models', nil, function(err, res)
    if err then fail(err, 'av.models'); resolve_models_local(); if cb then cb() end return end
    state.models_info = res
    local d = res.defaults or {}
    if opts.rnnoise_path == '' then state.models.rnnoise = d.rnnoise or state.models.rnnoise end
    if opts.sofa_path == '' then state.models.sofa = d.sofa or state.models.sofa end
    if not state.models.rnnoise or not state.models.sofa then resolve_models_local() end
    publish()
    if cb then cb() end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- filters

local function is_active(name) return active_filters()[name] == true end

local function set_filter(name, on)
  local f = FILTERS[name]
  if not f then return end
  local lbl = label(name)
  if on then
    if is_active(name) then return end
    local graph = f.graph(state)
    if graph == '' then return end
    local _, err = mp.command_native({ f.kind, 'add', '@' .. lbl .. ':lavfi=[' .. graph .. ']' })
    if err ~= nil then
      state.last_error = name .. ': no se pudo aplicar ' .. graph
      osd('No se pudo activar ' .. f.title)
    end
  else
    mp.command_native({ f.kind, 'remove', '@' .. lbl })
  end
  publish()
  set_button_state()
end

local function toggle_filter(name, quiet)
  local on = not is_active(name)
  set_filter(name, on)
  if not quiet then
    local f = FILTERS[name]
    local extra = ''
    if name == 'denoise' and on then extra = state.models.rnnoise and ' (RNNoise)' or ' (afftdn, sin modelo RNNoise)' end
    if name == 'binaural' and on then extra = state.models.sofa and ' (HRTF)' or ' (crossfeed, sin archivo SOFA)' end
    osd((on and '✓ ' or '✗ ') .. f.title .. extra)
  end
end

local function set_light(on)
  if on and not state.light then
    state.light_saved = {}
    for k, v in pairs(LIGHT) do
      state.light_saved[k] = mp.get_property(k)
      mp.set_property(k, v)
    end
    state.light = true
  elseif not on and state.light then
    for k, v in pairs(state.light_saved or {}) do
      if v ~= nil then mp.set_property(k, v) end
    end
    state.light = false
    state.light_saved = nil
  end
  publish()
end

-- remember the user's choice (explicit actions only; never from observers or automatic re-applies)
local function save_prefs()
  local active, list = active_filters(), {}
  for _, name in ipairs(ORDER) do if active[name] then table.insert(list, name) end end
  P:set('filters', list)
  P:set('light', state.light)
end

-- ---------------------------------------------------------------------------------------------
-- stutter diagnosis

local function diagnose()
  local p = function(name) return mp.get_property_native(name) end
  local d = {
    drops = p('frame-drop-count') or 0, decoder_drops = p('decoder-frame-drop-count') or 0,
    mistimed = p('mistimed-frame-count') or 0, delayed = p('vo-delayed-frame-count') or 0,
    vf_fps = p('estimated-vf-fps'), container_fps = p('container-fps'), display_fps = p('display-fps'),
    est_display_fps = p('estimated-display-fps'), hwdec = p('hwdec-current') or 'no', vo = p('current-vo') or '',
    video_sync = p('video-sync'), interpolation = p('interpolation'), scale = p('scale'),
    codec = p('video-codec') or '', size = (p('video-params') or {}).w and
      string.format('%dx%d', p('video-params').w, p('video-params').h) or '', speed = p('speed') or 1,
  }
  local tips = {}
  if d.vo == '' then table.insert(tips, 'No hay vídeo en reproducción: abre un vídeo para diagnosticarlo')
  else
    if (d.drops or 0) > 20 then
      table.insert(tips, string.format('%d fotogramas perdidos en el VO: el equipo no llega a dibujarlos', d.drops))
      if not state.light then
        table.insert(tips, 'Prueba el perfil ligero (escaladores bilineales, sin deband ni interpolación)')
      end
    end
    if (d.decoder_drops or 0) > 20 then
      table.insert(tips, string.format('%d fotogramas perdidos en el decodificador: la CPU no da abasto', d.decoder_drops))
      if d.hwdec == 'no' then table.insert(tips, 'Activa la decodificación por hardware: hwdec=auto-safe (menú → hwdec)') end
    end
    if (d.mistimed or 0) > 20 then
      table.insert(tips, string.format('%d fotogramas fuera de tiempo: reloj de audio/vídeo inestable', d.mistimed))
      if d.video_sync ~= 'display-resample' then table.insert(tips, 'Prueba video-sync=display-resample') end
    end
    if d.container_fps and d.display_fps and d.display_fps > 0 then
      local ratio = d.display_fps / d.container_fps
      local frac = math.abs(ratio - math.floor(ratio + 0.5))
      if frac > 0.02 and frac < 0.98 then
        table.insert(tips, string.format('%.3f fps en un monitor a %.2f Hz: cadencia irregular (judder); ' ..
          'interpolation=yes ayuda', d.container_fps, d.display_fps))
      end
    end
    if #tips == 0 then table.insert(tips, 'Sin síntomas: no hay pérdidas ni desincronía relevantes') end
  end
  d.tips = tips
  state.diag = d
  publish()
  return d
end

-- ---------------------------------------------------------------------------------------------
-- menus

local function base_menu(title, items, extra)
  local menu = { type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, on_close = 'callback',
    keep_open = false, search_submenus = false }
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
  state.items = {}  -- never publish the new view with the previous view's rows
  publish()
  views[spec.name](spec.args or {})
end

reopen_current = function()
  local spec = state.stack[#state.stack]
  if spec then open_view(spec, false) end
end

local function yesno(b) return b and 'activado' or 'desactivado' end

-- modos de ReplayGain, con lo que significa cada uno (el ajuste es el de mu-music)
local RG_HINT = { no = 'desactivado · gratis, pero solo donde hay etiquetas (música)',
                  track = 'por pista · gratis, sin tocar el sonido', album = 'por álbum · respeta el disco entero' }
local RG_NEXT = { no = 'track', track = 'album', album = 'no' }

views.root = function()
  local active = active_filters()
  local items = {}
  for _, name in ipairs(ORDER) do
    local f = FILTERS[name]
    local hint = active[name] and 'activado' or f.desc
    if name == 'denoise' and not state.models.rnnoise then hint = hint .. ' · sin modelo RNNoise (descargar)' end
    if name == 'binaural' and not state.models.sofa then hint = hint .. ' · sin SOFA (descargar)' end
    if name == 'eq' then
      table.insert(items, { title = f.title, hint = state.eq ~= '' and EQ_TITLES[state.eq] or 'plano', icon = f.icon,
        active = active.eq == true, value = { view = 'eq' } })
    else
      table.insert(items, { title = f.title, hint = hint, icon = f.icon, active = active[name] == true,
        value = { toggle = name } })
    end
    -- K3 · las dos formas de que todo suene igual de fuerte, juntas y diciendo lo que cuestan. La de las etiquetas
    -- (ReplayGain) es gratis pero solo donde las hay —casi solo música—; el filtro vale para todo pero cuesta CPU.
    -- El ajuste vive en mu-music, que es quien lo aplica; aquí solo se ofrece donde se busca viendo una película.
    if name == 'level' then
      local rg = (mp.get_property_native('user-data/mu/music') or {}).replaygain or 'no'
      table.insert(items, { title = 'Volumen parejo · con las etiquetas', icon = 'equalizer',
        hint = RG_HINT[rg] or RG_HINT.no, active = rg ~= 'no', value = { replaygain = rg }, separator = true })
    end
  end
  table.insert(items, { title = 'Quitar el vídeo al minimizar', hint = yesno(P:get('audio_minimized')),
    icon = 'minimize', active = P:get('audio_minimized'), value = { audio_minimized = true }, separator = true })
  table.insert(items, { title = 'Perfil ligero (menos GPU/CPU)', hint = yesno(state.light), icon = 'speed',
    active = state.light, value = { light = true }, separator = true })
  table.insert(items, { title = 'Diagnóstico de tirones', icon = 'monitor_heart', value = { view = 'diag' } })
  table.insert(items, { title = 'Modelos (RNNoise, HRTF)', icon = 'cloud_download', value = { view = 'models' } })
  table.insert(items, { title = 'Quitar todos los filtros', icon = 'filter_alt_off', value = { clear = true },
    separator = true })
  show('Filtros de imagen y sonido', items)
end

views.eq = function()
  local items = { { title = 'Plano (sin ecualizar)', icon = state.eq == '' and 'radio_button_checked' or 'radio_button_unchecked',
    active = state.eq == '', value = { eq = '' } } }
  for _, e in ipairs(EQ_PRESETS) do
    table.insert(items, { title = e.title, icon = state.eq == e.id and 'radio_button_checked' or 'radio_button_unchecked',
      active = state.eq == e.id, value = { eq = e.id } })
  end
  show('Ecualizador', items, { footnote = 'se recuerda · ⌫ atrás' })
end

local function set_eq(id, quiet)
  if id ~= '' and not EQ_GRAPHS[id] then return end
  state.eq = id
  set_filter('eq', false)
  if id ~= '' then set_filter('eq', true) end
  P:set('eq', id)
  if not quiet then osd('Ecualizador: ' .. (id ~= '' and EQ_TITLES[id] or 'plano')) end
end

views.diag = function()
  local d = diagnose()
  local items = {}
  for _, tip in ipairs(d.tips) do
    table.insert(items, { title = tip, icon = 'lightbulb', selectable = false })
  end
  local function row(title, value, icon)
    table.insert(items, { title = title, hint = tostring(value), icon = icon or 'info', selectable = false, muted = true })
  end
  if d.vo ~= '' then
    row('Perdidos VO / decodificador / fuera de tiempo',
      string.format('%d / %d / %d', d.drops, d.decoder_drops, d.mistimed), 'movie')
    local function fmt(v, f) return v and string.format(f, v) or '?' end
    row('fps contenedor → estimados → pantalla', string.format('%s → %s → %s', fmt(d.container_fps, '%.3f'),
      fmt(d.vf_fps, '%.2f'), fmt(d.display_fps, '%.2f')), 'timer')
    row('Decodificación por hardware', d.hwdec, 'memory')
    row('Salida de vídeo / sincronía', string.format('%s / %s', d.vo, tostring(d.video_sync)), 'monitor')
    row('Códec / tamaño', string.format('%s %s', d.codec, d.size), 'videocam')
  end
  table.insert(items, { title = 'Perfil ligero', hint = yesno(state.light), icon = 'speed', active = state.light,
    value = { light = true }, separator = true })
  table.insert(items, { title = 'Actualizar', icon = 'refresh', value = { view = 'diag' } })
  show('Diagnóstico de tirones', items)
end

local function models_items(res)
  local items = {}
  for _, m in ipairs((res and res.models) or {}) do
    local dl = state.downloads[m.name]
    local hint, icon, value
    if m.present then
      hint = m.note .. ' · presente'; icon = 'check'; value = { noop = true }
    elseif dl and (dl.status == 'queued' or dl.status == 'running') then
      hint = string.format('descargando %d%%', math.floor((dl.progress or 0) * 100 + 0.5))
      icon = 'downloading'; value = { noop = true }
    else
      hint = string.format('%s · descargar %d KB', m.note, m.size_kb or 0)
      icon = 'cloud_download'; value = { download = m.name }
    end
    table.insert(items, { title = m.name, hint = hint, icon = icon, value = value })
  end
  return items
end

views.models = function()
  if not rpc.connected() then
    show('Modelos', { { title = 'mpvd no está disponible', icon = 'error', selectable = false, muted = true },
      { title = 'Reintentar', icon = 'refresh', value = { view = 'models', ensure = true } } })
    return
  end
  show('Modelos', uosc.loading_items())
  rpc.call('av.models', nil, function(err, res)
    if err then show('Modelos', uosc.message_items(fail(err, 'av.models'), 'error')); return end
    state.models_info = res
    if state.view == 'models' then show('Modelos', models_items(res)) end
  end)
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.replaygain then
      mp.commandv('script-message-to', 'mu_music', 'mu-music-replaygain', RG_NEXT[v.replaygain] or 'track')
      mp.add_timeout(0.3, reopen_current)   -- mu-music publica su estado y la fila se redibuja con el nuevo modo
    elseif v.audio_minimized then
      P:set('audio_minimized', not P:get('audio_minimized'))
      osd('Quitar el vídeo al minimizar: ' .. yesno(P:get('audio_minimized')))
      reopen_current()
    elseif v.eq ~= nil then
      set_eq(v.eq, true)
      save_prefs()
      reopen_current()
    elseif v.toggle then
      toggle_filter(v.toggle, true)
      save_prefs()
      reopen_current()
    elseif v.light then
      set_light(not state.light)
      save_prefs()
      reopen_current()
    elseif v.clear then
      for _, name in ipairs(ORDER) do set_filter(name, false) end
      state.eq = ''
      P:set('eq', '')
      save_prefs()
      osd('Filtros de sonido e imagen quitados')
      reopen_current()
    elseif v.download then
      rpc.call('av.models.download', { name = v.download, notify = SCRIPT }, function(err, job)
        if err then osd('Descarga: ' .. fail(err, 'av.models.download')); return end
        state.downloads[v.download] = job
        reopen_current()
      end)
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
-- events pushed by mpvd (model downloads)

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'av-model' or type(ev.job) ~= 'table' then return end
  state.downloads[ev.model] = ev.job
  if ev.job.status == 'done' then
    osd('Modelo ' .. ev.model .. ' descargado')
    refresh_models(function()
      -- re-apply a filter that was running on its fallback so it picks up the model
      for _, name in ipairs({ 'denoise', 'binaural' }) do
        if is_active(name) then set_filter(name, false); set_filter(name, true) end
      end
    end)
  elseif ev.job.status == 'failed' then
    osd('No se pudo descargar ' .. ev.model .. ': ' .. (ev.job.message or ev.job.error or ''))
  end
  if uosc.open_type() == MENU and state.view == 'models' then reopen_current() end
end)

-- ---------------------------------------------------------------------------------------------
-- bindings

set_button_state = function()
  if not uosc.available() then return end
  local n = 0
  for _ in pairs(active_filters()) do n = n + 1 end
  uosc.set_button('mu-av', { icon = 'tune', tooltip = 'Sonido e imagen (alt+v)', active = n > 0,
    badge = n > 0 and tostring(n) or nil, command = { 'script-binding', SCRIPT .. '/av-menu' } })
end

local function open_root()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  refresh_models(function() open_view({ name = 'root' }) end)
end

N:binding('av-menu', open_root)
mp.add_key_binding(nil, 'av-night', function() toggle_filter('night'); save_prefs() end)
mp.register_script_message('mu-av-toggle', function(name) toggle_filter(name); save_prefs() end)
mp.register_script_message('mu-av-set', function(name, on) set_filter(name, on == 'yes' or on == 'true'); save_prefs() end)
mp.register_script_message('mu-av-eq', function(id) set_eq(id or ''); save_prefs() end)
mp.register_script_message('mu-av-light', function(on) set_light(on ~= 'no' and on ~= 'false'); save_prefs() end)
mp.register_script_message('mu-av-diagnose', function() diagnose() end)

mp.register_script_message('uosc-version', set_button_state)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button_state() end
  if core and core.mpvd == 'connected' then refresh_models() end
end)
mp.observe_property('af', 'native', function() publish(); set_button_state() end)
mp.observe_property('vf', 'native', function() publish(); set_button_state() end)

-- H32/H60: while the window is minimized nothing needs the picture, so the video track is deselected for the current
-- file (file-local, so the next file is untouched) and selected again when the window comes back. Encendido por
-- defecto desde H60: medido, minimizar NO deja de decodificar por sí solo (37 % → 18 % de un núcleo, y 8 % sin
-- vídeo), y en un vídeo de internet deseleccionar la pista corta además su descarga. Vale igual para una URL que
-- para un fichero: 0,03 s de ida y vuelta, sin recargar nada. ADR-096.
-- Con la ventana escondida no hay OSD que valga, así que la señal va donde sí se ve con la ventana minimizada:
-- el TÍTULO, que es lo que enseña la barra de tareas. Y al volver se dice una vez lo que ha pasado, porque si no
-- no hay forma de saber si el vídeo se apagó de verdad (lo preguntó Ser, y era una pega justa).
local MIN_TITLE = '🎧 Solo audio (minimizado)'
local minimized = { vid = nil, path = nil, title = nil }
local function on_minimized(_, m)
  if m and P:get('audio_minimized') then
    local v = mp.get_property_native('current-tracks/video')
    if type(v) ~= 'table' or v.image or minimized.vid then return end
    minimized.vid, minimized.path = mp.get_property('vid'), mp.get_property('path')
    mp.set_property('file-local-options/vid', 'no')
    minimized.title = mp.get_property('options/title')
    mp.set_property('title', MIN_TITLE .. ' — ' .. (minimized.title or ''))
    state.minimized_audio = true
    publish()
  elseif not m and minimized.vid then
    if mp.get_property('path') == minimized.path then mp.set_property('file-local-options/vid', minimized.vid) end
    if minimized.title then mp.set_property('title', minimized.title) end
    if not P:get('told_minimized') then
      P:set('told_minimized', true)
      mp.osd_message('🎧 Mientras la ventana estaba minimizada se ha apagado el vídeo: no se decodifica, y si viene '
                     .. 'de internet tampoco se descarga. Se cambia en Imagen y sonido.', 7)
    end
    minimized.vid, minimized.path, minimized.title = nil, nil, nil
    state.minimized_audio = false
    publish()
  end
end
mp.observe_property('window-minimized', 'bool', on_minimized)
mp.register_event('end-file', function()
  if not minimized.vid then return end
  if minimized.title then mp.set_property('title', minimized.title) end
  minimized.vid, minimized.path, minimized.title, state.minimized_audio = nil, nil, nil, false
end)

resolve_models_local()
state.eq = P:get('eq') or ''
-- restore the user's filters and light profile (before the first file: `af/vf add` is accepted while idle)
for _, name in ipairs(P:get('filters')) do
  if FILTERS[name] then set_filter(name, true) end
end
if P:get('light') then set_light(true) end
P:on_change(function(reason)
  if reason ~= 'reset' then return end
  for _, name in ipairs(ORDER) do set_filter(name, false) end
  state.eq = ''
  set_light(false)
  if uosc.open_type() == MENU then reopen_current() end
end)
publish()
msg.info('mu-av loaded')
