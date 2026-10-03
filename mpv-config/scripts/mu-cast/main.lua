-- mu-cast: «Enviar a la tele» (H27, ADR-063). Lists the TVs and players of the local network (DLNA, found by mpvd:
-- cast.discover) and sends them what is playing here, from the same moment; this player pauses. While the TV plays:
-- pause/resume, ±30 s, its volume, «Seguir viendo aquí» (back to this player at the TV's position) and stop.
-- Binding: cast-menu. Script name: mu_cast. State for the tests: user-data/mu/cast.
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
local MENU = 'mu-cast'
local EVENT = 'mu-cast-event'
local ROOT_TITLE = 'Enviar a la tele'

local opts = {
  discover_seconds = 3,
  poll_seconds = 2,     -- the TV's position while the menu is open
  osd_seconds = 3,
}
options.read_options(opts, 'mu-cast')

local state = { devices = nil, searching = false, status = nil, last_error = '', items = {}, volume = nil }
local poll_timer

local function publish()
  mp.set_property_native('user-data/mu/cast', {
    devices = state.devices or {}, searching = state.searching, casting = state.status and state.status.casting or false,
    status = state.status, last_error = state.last_error, items = state.items,
  })
end

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function clock(t)
  if type(t) ~= 'number' then return '—' end
  t = math.max(0, math.floor(t))
  local h, m, s = math.floor(t / 3600), math.floor(t / 60) % 60, t % 60
  if h > 0 then return string.format('%d:%02d:%02d', h, m, s) end
  return string.format('%d:%02d', m, s)
end

local STATE_TEXT = { PLAYING = 'reproduciendo', PAUSED_PLAYBACK = 'en pausa', STOPPED = 'parada',
                     TRANSITIONING = 'cargando…', NO_MEDIA_PRESENT = 'sin nada' }

local function show(items, open)
  state.items = {}
  for _, it in ipairs(items) do state.items[#state.items + 1] = { title = it.title or '', hint = it.hint or '' } end
  publish()
  local menu = N:frame({ type = MENU, title = ROOT_TITLE, items = items, callback = { SCRIPT, EVENT }, keep_open = true },
                       { { name = 'root' } })
  if open or uosc.open_type() ~= MENU then uosc.open(menu) else uosc.update(menu) end
end

local function current_source()
  local path = mp.get_property('path')
  if not path or path == '' or mp.get_property_native('idle-active') then return nil end
  if not path:find('^%a[%w+.-]*://') then path = mp.command_native({ 'normalize-path', path }) or path end
  return { path = path, title = mp.get_property('media-title') or '', start = mp.get_property_number('time-pos') or 0,
           audio_only = mp.get_property_native('current-tracks/video') == nil }
end

local render

local function casting_items(st)
  local d = st.device or {}
  local items = {
    { title = d.name or 'Tele', hint = STATE_TEXT[st.state or ''] or (st.state or ''), icon = 'cast_connected',
      selectable = false, muted = true },
    { title = st.title ~= '' and st.title or '—', hint = clock(st.position) .. ' / ' .. clock(st.duration),
      icon = 'movie', selectable = false, muted = true, separator = true },
  }
  if st.state == 'PAUSED_PLAYBACK' then
    items[#items + 1] = { title = tr('Reanudar en la tele'), icon = 'play_arrow', value = { action = 'play' } }
  else
    items[#items + 1] = { title = tr('Pausar la tele'), icon = 'pause', value = { action = 'pause' } }
  end
  items[#items + 1] = { title = tr('Atrasar 30 s'), icon = 'replay_30', value = { action = 'seek', delta = -30 } }
  items[#items + 1] = { title = tr('Adelantar 30 s'), icon = 'forward_30', value = { action = 'seek', delta = 30 } }
  local vol = st.volume or state.volume
  local vol_hint = vol and (tostring(vol) .. ' %') or nil
  items[#items + 1] = { title = tr('Subir volumen de la tele'), hint = vol_hint, icon = 'volume_up',
                        value = { action = 'volume', delta = 5 } }
  items[#items + 1] = { title = tr('Bajar volumen de la tele'), hint = vol_hint, icon = 'volume_down',
                        value = { action = 'volume', delta = -5 }, separator = true }
  items[#items + 1] = { title = tr('Seguir viendo aquí'), hint = tr('desde %s'):format(clock(st.position)), icon = 'computer',
                        value = { action = 'here' } }
  items[#items + 1] = { title = tr('Parar en la tele'), icon = 'stop', value = { action = 'stop' } }
  if st.firewall and st.firewall.command then
    items[#items + 1] = { title = tr('Si la tele no carga: abre el puerto %s')
                            :format(tostring(st.firewall.port)), icon = 'shield',
                          hint = st.firewall.command, selectable = false, muted = true }
  end
  if st.error then
    items[#items + 1] = { title = st.error, icon = 'error', selectable = false, muted = true }
  end
  return items
end

local function device_items()
  local items = {}
  local src = current_source()
  if not src then
    items[#items + 1] = { title = tr('Abre un vídeo o una canción y elige la tele'), icon = 'info', selectable = false,
                          muted = true }
  end
  if state.searching and not state.devices then
    for _, it in ipairs(uosc.loading_items(tr('Buscando teles en tu red…'))) do items[#items + 1] = it end
    return items
  end
  for _, d in ipairs(state.devices or {}) do
    local hint = table.concat({ d.manufacturer or '', d.model or '' }, ' '):gsub('^%s+', ''):gsub('%s+$', '')
    items[#items + 1] = { title = d.name, hint = hint, icon = 'tv', value = { device = d.id } }
  end
  if state.devices and #state.devices == 0 then
    items[#items + 1] = { title = tr('No hay ninguna tele en tu red'), icon = 'tv_off', selectable = false, muted = true }
    items[#items + 1] = { title = tr('Enciéndela y actívale «compartir contenido» o DLNA'), icon = 'info',
                          selectable = false, muted = true }
  end
  if #items > 0 then items[#items].separator = true end
  items[#items + 1] = { title = state.searching and 'Buscando…' or 'Buscar de nuevo', icon = 'refresh',
                        value = { action = 'discover' } }
  return items
end

render = function(open)
  if not rpc.connected() then
    show(uosc.message_items(tr('mpvd no está conectado: espera unos segundos'), 'error'), open)
    return
  end
  if state.status and state.status.casting then show(casting_items(state.status), open)
  else show(device_items(), open) end
end

local function stop_poll()
  if poll_timer then poll_timer:kill(); poll_timer = nil end
end

local function refresh_status(cb)
  rpc.call('cast.status', nil, function(err, st)
    if not err then state.status = st end
    publish()
    if cb then cb(err) end
  end, 10)
end

local function start_poll()
  stop_poll()
  poll_timer = mp.add_periodic_timer(opts.poll_seconds, function()
    if uosc.open_type() ~= MENU then stop_poll() return end
    refresh_status(function() if uosc.open_type() == MENU then render(false) end end)
  end)
end

local function discover()
  if state.searching then return end
  state.searching = true
  render(false)
  rpc.call('cast.discover', { timeout = opts.discover_seconds }, function(err, res)
    state.searching = false
    if err then
      state.last_error = err.message or tostring(err)
      osd(tr('No se pudo buscar: %s'):format(state.last_error))
    else
      state.devices = res.devices or {}
    end
    if uosc.open_type() == MENU then render(false) else publish() end
  end, opts.discover_seconds + 10)
end

local function after(err, st, what)
  if err then
    state.last_error = err.message or tostring(err)
    osd(what .. ': ' .. state.last_error)
  else
    state.status = st
  end
  if uosc.open_type() == MENU then render(false) else publish() end
end

local function send_to(device_id)
  local src = current_source()
  if not src then osd(tr('Abre primero lo que quieras ver en la tele')) return end
  show(uosc.loading_items(tr('Enviando a la tele…')), false)
  rpc.call('cast.play', { device = device_id, path = src.path, title = src.title, start = src.start,
                          audio_only = src.audio_only }, function(err, st)
    if not err then
      mp.set_property_native('pause', true)
      osd(tr('En la tele: %s'):format((st.device or {}).name or ''))
      start_poll()
    end
    after(err, st, 'No se pudo enviar')
  end, 60)
end

local function action(v)
  local st = state.status or {}
  if v.action == 'discover' then
    state.devices = nil
    discover()
  elseif v.action == 'seek' then
    rpc.call('cast.control', { action = 'seek', value = math.max(0, (st.position or 0) + v.delta) },
             function(err, res) after(err, res, 'La tele no quiso saltar') end, 30)
  elseif v.action == 'volume' then
    -- SetVolume is absolute: mpvd moves it from the volume the TV really is at (asking it first), because starting
    -- from a number invented here put a TV at 8 straight to 35
    rpc.call('cast.control', { action = 'volume_add', value = v.delta }, function(err, res)
      if not err and type(res) == 'table' and res.volume then state.volume = res.volume end
      after(err, res, 'Volumen')
    end, 15)
  elseif v.action == 'here' then
    local pos = st.position
    rpc.call('cast.stop', nil, function(err, res)
      after(err, res, 'Parar')
      if pos and pos > 0 then mp.commandv('seek', tostring(pos), 'absolute') end
      mp.set_property_native('pause', false)
      osd(tr('Seguimos aquí'))
      stop_poll()
      uosc.close(MENU)
    end, 15)
  elseif v.action == 'stop' then
    rpc.call('cast.stop', nil, function(err, res) after(err, res, 'Parar'); stop_poll() end, 15)
  else
    rpc.call('cast.control', { action = v.action }, function(err, res) after(err, res, 'La tele no respondió') end, 15)
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back or ev.type == 'back' then
    stop_poll()
    if not N:leave() then uosc.close(MENU) end
  elseif ev.type == 'activate' and type(ev.value) == 'table' then
    if ev.value.device then send_to(ev.value.device) else action(ev.value) end
  end
end)

local function open_root()
  render(true)
  if not rpc.connected() then return end
  refresh_status(function()
    render(false)
    if state.status and state.status.casting then start_poll()
    elseif not state.devices then discover() end
  end)
end

N:binding('cast-menu', open_root)
publish()
msg.info('mu-cast loaded')
