-- mu-modes: ways of using the player (H27, ADR-058). Script name: mu_modes. State: user-data/mu/modes.
--   * Mini reproductor: a small borderless window kept on top (size only on Wayland: the compositor places it;
--     "on top" depends on the window manager — KDE: Alt+F3 › Más acciones › Mantener por encima if it is ignored).
--   * Modo salón: to watch from the sofa — fullscreen, big menus (uosc scale), big subtitles and messages; a gamepad
--     can drive it through mpvd (gamepad.* over the Linux joystick API) while it is on.
--   * Modo sencillo: a short main menu (mu-menu reads `simple` here) and a reduced control bar. The bar shrinks in
--     two ways, because uosc needs both: the `controls` option for the next start, and `hide` on every mu-* button
--     for right now (mu.uosc does that for every script; H53, ADR-099).
-- Each mode remembers what it changed and puts it back when it is turned off. Salón and sencillo are remembered
-- (mu-prefs namespace mu-modes); the mini player is not (it depends on the window of the moment).
local mp = require('mp')
local msg = require('mp.msg')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local tr = require('mu.i18n').t
local prefs = require('mu.prefs')

local SCRIPT = mp.get_script_name()

local P = prefs.ns('mu-modes', { salon = false, simple = false })

local MINI_FRACTION = 0.3       -- the mini window is ~30 % of the screen width
local SALON = { ['sub-scale'] = 1.5, ['osd-font-size'] = 50 }
local SALON_UOSC = { scale = '1.8', scale_fullscreen = '1.8', font_scale = '1.2' }
-- uosc's own element names (script-opts/uosc.conf `controls=`): play/pause, subtitles, audio, menu, fullscreen
local SIMPLE_CONTROLS = 'play-pause,space,<video,audio>subtitles,<has_many_audio>audio,space,button:mu-menu,fullscreen'

local state = { mini = false, salon = false, simple = false, gamepad = '' }
local saved = { mini = nil, salon = nil, simple = nil }

local function osd(text) mp.osd_message(text, 3) end

local function publish()
  mp.set_property_native('user-data/mu/modes', { mini = state.mini, salon = state.salon, simple = state.simple,
    gamepad = state.gamepad })
end

local function uosc_opt(key, value)
  mp.commandv('change-list', 'script-opts', 'append', 'uosc-' .. key .. '=' .. value)
end

local function uosc_current(key, default)
  local opts = mp.get_property_native('script-opts') or {}
  return opts['uosc-' .. key] or default
end

-- -- mini player -------------------------------------------------------------------------------

local function set_mini(on)
  if on == state.mini then return end
  if on then
    saved.mini = { fullscreen = mp.get_property_native('fullscreen'), ontop = mp.get_property_native('ontop'),
      border = mp.get_property_native('border'), ['window-scale'] = mp.get_property_native('window-scale') }
    local dw = mp.get_property_number('display-width') or 1920
    local vw = mp.get_property_number('dwidth') or 0
    mp.set_property_native('fullscreen', false)
    if vw > 0 then mp.set_property_number('window-scale', math.max(0.1, dw * MINI_FRACTION / vw)) end
    mp.set_property_native('border', false)
    mp.set_property_native('ontop', true)
    state.mini = true
    osd(tr('Mini reproductor (vuelve con la misma tecla)'))
  else
    for k, v in pairs(saved.mini or {}) do mp.set_property_native(k, v) end
    saved.mini = nil
    state.mini = false
    osd(tr('Ventana normal'))
  end
  publish()
end

-- -- salón ---------------------------------------------------------------------------------------

local function set_salon(on, quiet)
  if on == state.salon then return end
  if on then
    saved.salon = { fullscreen = mp.get_property_native('fullscreen') }
    for k in pairs(SALON) do saved.salon[k] = mp.get_property_native(k) end
    saved.salon_uosc = {}
    for k in pairs(SALON_UOSC) do saved.salon_uosc[k] = uosc_current(k, '') end
    for k, v in pairs(SALON) do mp.set_property_native(k, v) end
    for k, v in pairs(SALON_UOSC) do uosc_opt(k, v) end
    if state.mini then set_mini(false) end
    mp.set_property_native('fullscreen', true)
    state.salon = true
    if rpc.connected() then
      rpc.call('gamepad.start', { notify = SCRIPT }, function(err, res)
        state.gamepad = (not err and res and res.device) or ''
        publish()
      end)
    end
    if not quiet then osd(tr('Modo salón: letra grande y pantalla completa · un mando (gamepad) también sirve')) end
  else
    for k, v in pairs(saved.salon or {}) do mp.set_property_native(k, v) end
    for k, v in pairs(saved.salon_uosc or {}) do
      if v == '' then mp.commandv('change-list', 'script-opts', 'remove', 'uosc-' .. k) else uosc_opt(k, v) end
    end
    saved.salon, saved.salon_uosc = nil, nil
    state.salon, state.gamepad = false, ''
    if rpc.connected() then rpc.call('gamepad.stop', {}, function() end) end
    if not quiet then osd(tr('Modo salón desactivado')) end
  end
  P:set('salon', state.salon)
  publish()
end

-- -- sencillo -------------------------------------------------------------------------------------

local function set_simple(on, quiet)
  if on == state.simple then return end
  if on then
    saved.simple = uosc_current('controls', '')
    uosc_opt('controls', SIMPLE_CONTROLS)
    state.simple = true
    if not quiet then osd(tr('Modo sencillo: menú corto y barra mínima')) end
  else
    if (saved.simple or '') == '' then mp.commandv('change-list', 'script-opts', 'remove', 'uosc-controls')
    else uosc_opt('controls', saved.simple) end
    saved.simple = nil
    state.simple = false
    if not quiet then osd(tr('Modo sencillo desactivado: menú completo')) end
  end
  P:set('simple', state.simple)
  publish()
end

-- -- gamepad (salón): mpvd reads the joystick and sends the buttons here ----------------------------

local GAMEPAD_ACTIONS = {
  play_pause = function() mp.commandv('cycle', 'pause') end,
  back = function() mp.commandv('seek', -10) end,
  forward = function() mp.commandv('seek', 10) end,
  volume_up = function() mp.commandv('add', 'volume', 5) end,
  volume_down = function() mp.commandv('add', 'volume', -5) end,
  menu = function() mp.commandv('script-binding', 'mu_menu/root') end,
  close = function() mp.commandv('script-message-to', 'uosc', 'close-menu') end,
  next = function() mp.commandv('playlist-next') end,
  prev = function() mp.commandv('playlist-prev') end,
  subtitles = function() mp.commandv('cycle', 'sub') end,
}

mp.register_script_message('mu-event', function(payload)
  local ev = require('mp.utils').parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'gamepad' then return end
  if ev.status == 'lost' then state.gamepad = ''; publish(); osd(tr('Mando desconectado')) return end
  local fn = GAMEPAD_ACTIONS[ev.action or '']
  if fn and state.salon then fn() end
end)

-- -- bindings and restore ---------------------------------------------------------------------------

mp.add_key_binding(nil, 'mini-toggle', function() set_mini(not state.mini) end)
mp.add_key_binding(nil, 'salon-toggle', function() set_salon(not state.salon) end)
mp.add_key_binding(nil, 'simple-toggle', function() set_simple(not state.simple) end)
mp.register_script_message('mu-modes-set', function(name, on)
  local v = on == 'yes' or on == 'true'
  if name == 'mini' then set_mini(v) elseif name == 'salon' then set_salon(v) elseif name == 'simple' then set_simple(v) end
end)

P:on_change(function(reason)
  if reason ~= 'reset' then return end
  set_salon(false, true)
  set_simple(false, true)
end)

-- remembered modes come back at start (salón waits for the first file: fullscreen with nothing loaded is odd)
if P:get('simple') then set_simple(true, true) end
if P:get('salon') then
  local once
  once = function()
    mp.unregister_event(once)
    set_salon(true, true)
  end
  mp.register_event('file-loaded', once)
end
mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if state.salon and core and core.mpvd == 'connected' and state.gamepad == '' then
    rpc.call('gamepad.start', { notify = SCRIPT }, function(err, res)
      state.gamepad = (not err and res and res.device) or ''
      publish()
    end)
  end
end)
publish()
msg.info('mu-modes loaded')
