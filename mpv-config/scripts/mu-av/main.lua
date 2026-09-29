-- mu-av: "Sonido e imagen" menu for MPV-UOS. Toggles labelled libavfilter graphs (`af add @mu-x:lavfi=[...]`) validated
-- against the mpv 0.41 / FFmpeg 8 installed here (docs/AUDIO_VIDEO.md): clear dialogue, night mode, noise reduction
-- (arnndn with an RNNoise model from mpvd, afftdn fallback), binaural for headphones (sofalizer with a SOFA HRTF, crossfeed
-- fallback), photosensitivity protection (video), plus a stutter diagnosis view with a "light profile" toggle.
-- Script name: mu_av. Bindings: av-menu (alt+v), av-night (alt+n). State in user-data/mu/av.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')

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
  photo = { kind = 'vf', title = 'Protección fotosensible', icon = 'flash_off',
    desc = 'atenúa destellos rápidos (photosensitivity)',
    graph = function() return 'photosensitivity=frames=30:threshold=1:bypass=0' end },
}
local ORDER = { 'dialog', 'night', 'denoise', 'binaural', 'photo' }

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
  mp.set_property_native('user-data/mu/av', {
    filters = active_filters(), light = state.light, models = state.models, view = state.view, items = state.items,
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

local function yesno(b) return b and 'activado' or 'desactivado' end

views.root = function()
  local active = active_filters()
  local items = {}
  for _, name in ipairs(ORDER) do
    local f = FILTERS[name]
    local hint = active[name] and 'activado' or f.desc
    if name == 'denoise' and not state.models.rnnoise then hint = hint .. ' · sin modelo RNNoise (descargar)' end
    if name == 'binaural' and not state.models.sofa then hint = hint .. ' · sin SOFA (descargar)' end
    table.insert(items, { title = f.title, hint = hint, icon = f.icon, active = active[name] == true,
      value = { toggle = name } })
  end
  table.insert(items, { title = 'Perfil ligero (menos GPU/CPU)', hint = yesno(state.light), icon = 'speed',
    active = state.light, value = { light = true }, separator = true })
  table.insert(items, { title = 'Diagnóstico de tirones', icon = 'monitor_heart', value = { view = 'diag' } })
  table.insert(items, { title = 'Modelos (RNNoise, HRTF)', icon = 'cloud_download', value = { view = 'models' } })
  table.insert(items, { title = 'Quitar todos los filtros', icon = 'filter_alt_off', value = { clear = true },
    separator = true })
  show('Sonido e imagen', items)
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
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.toggle then
      toggle_filter(v.toggle, true)
      reopen_current()
    elseif v.light then
      set_light(not state.light)
      reopen_current()
    elseif v.clear then
      for _, name in ipairs(ORDER) do set_filter(name, false) end
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

mp.add_key_binding(nil, 'av-menu', open_root)
mp.add_key_binding(nil, 'av-night', function() toggle_filter('night') end)
mp.register_script_message('mu-av-toggle', function(name) toggle_filter(name) end)
mp.register_script_message('mu-av-set', function(name, on) set_filter(name, on == 'yes' or on == 'true') end)
mp.register_script_message('mu-av-light', function(on) set_light(on ~= 'no' and on ~= 'false') end)
mp.register_script_message('mu-av-diagnose', function() diagnose() end)

mp.register_script_message('uosc-version', set_button_state)
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button_state() end
  if core and core.mpvd == 'connected' then refresh_models() end
end)
mp.observe_property('af', 'native', function() publish(); set_button_state() end)
mp.observe_property('vf', 'native', function() publish(); set_button_state() end)

resolve_models_local()
publish()
msg.info('mu-av loaded')
