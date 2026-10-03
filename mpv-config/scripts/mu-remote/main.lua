-- mu-remote: phone remote control. alt+z asks mpvd for a one-time pairing token (remote.pair) and draws the QR of the
-- PWA URL as an ASS overlay (mp.create_osd_overlay + mp.assdraw, like uosc; verified in docs/REMOTE_API.md).
-- Menu 'mu-remote' (status, paired phones, forget, stop) and, H23, the downloads panel opened in this computer's
-- browser (remote.pair path=/downloads, a loopback URL). State in user-data/mu/remote. Script name: mu_remote.
local mp = require('mp')
local msg = require('mp.msg')
local assdraw = require('mp.assdraw')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local tr = require('mu.i18n').t
local nav = require('mu.nav')
local clip = require('mu.clip')
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-remote'
local EVENT = 'mu-remote-menu-event'

local opts = {
  qr_seconds = 120,     -- hide the QR automatically after this many seconds (0 = never)
  qr_size = 300,        -- QR side in PlayRes(720) pixels
  osd_seconds = 4,
  open_command = '',    -- program that opens a URL ('' = xdg-open / open / explorer); tests use `true`
}
options.read_options(opts, 'mu-remote')

local state = { visible = false, url = '', token = '', expires_at = 0, status = nil, last_error = '', qr = nil, view = '',
                downloads_url = '' }
local overlay = nil
local hide_timer = nil

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function publish()
  mp.set_property_native('user-data/mu/remote', {
    visible = state.visible, url = state.url, token = state.token, expires_at = state.expires_at,
    running = state.status and state.status.running or false, port = state.status and state.status.port or 0,
    paired = state.status and #(state.status.paired or {}) or 0, last_error = state.last_error, view = state.view,
    qr_size = state.qr and state.qr.size or 0, downloads_url = state.downloads_url,
  })
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  osd(tr('Mando: %s'):format(m))
  return m
end

-- ---------------------------------------------------------------------------------------------
-- QR overlay

local function draw_qr()
  if not state.qr or not overlay then return end
  local n = state.qr.size
  local quiet = 4
  local module = opts.qr_size / (n + 2 * quiet)
  local total = opts.qr_size
  local ow = mp.get_property_number('osd-width', 1280)
  local oh = math.max(1, mp.get_property_number('osd-height', 720))
  local res_w = math.floor(720 * (ow / oh) + 0.5)
  if res_w < 100 then res_w = 1280 end
  local x0 = math.floor((res_w - total) / 2)
  local y0 = math.floor((720 - total) / 2) - 30
  local ass = assdraw.ass_new()
  -- dim backdrop
  ass:new_event()
  ass:append('{\\pos(0,0)\\an7\\bord0\\shad0\\blur0\\1c&H000000&\\1a&H60&}')
  ass:draw_start()
  ass:rect_cw(0, 0, res_w, 720)
  ass:draw_stop()
  -- white card (quiet zone) with room for the caption
  ass:new_event()
  ass:append('{\\pos(0,0)\\an7\\bord0\\shad0\\blur0\\1c&HFFFFFF&}')
  ass:draw_start()
  ass:round_rect_cw(x0, y0, x0 + total, y0 + total, 8)
  ass:draw_stop()
  -- dark modules, one rectangle per horizontal run
  ass:new_event()
  ass:append('{\\pos(0,0)\\an7\\bord0\\shad0\\blur0\\1c&H000000&}')
  ass:draw_start()
  local ox, oy = x0 + quiet * module, y0 + quiet * module
  for r, runs in ipairs(state.qr.runs) do
    local y = oy + (r - 1) * module
    for _, run in ipairs(runs) do
      local x = ox + run[1] * module
      ass:rect_cw(x, y, x + run[2] * module, y + module)
    end
  end
  ass:draw_stop()
  -- caption
  ass:new_event()
  local cx = math.floor(res_w / 2)
  ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs26\\1c&HFFFFFF&\\3c&H000000&}', cx, y0 + total + 12))
  ass:append('Mando: escanea con el móvil o abre  ' .. state.url:gsub('\\', '\\\\'))
  ass:new_event()
  ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs20\\1c&HCCCCCC&\\3c&H000000&}', cx, y0 + total + 48))
  ass:append('El código vale una vez y caduca en 10 min · alt+z oculta')
  local fw = state.status and state.status.firewall
  if type(fw) == 'table' and fw.command then
    ass:new_event()
    ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs18\\1c&H66CCFF&\\3c&H000000&}', cx, y0 + total + 78))
    ass:append(tr('¿El móvil no conecta? El cortafuegos (%s) bloquea el puerto %s. En una terminal: %s')
      :format(fw.tool, tostring(fw.port), fw.command:gsub('\\', '\\\\')))
  end
  overlay.res_x = res_w
  overlay.res_y = 720
  overlay.z = 1000
  overlay.data = ass.text
  overlay:update()
end

local function hide()
  if hide_timer then hide_timer:kill(); hide_timer = nil end
  if overlay then overlay:remove(); overlay = nil end
  state.visible = false
  publish()
end

local function show(result)
  state.url = result.url or ''
  state.token = result.token or ''
  state.expires_at = mp.get_time() + (result.expires_in or 600)
  state.qr = result.qr
  state.status = result.status or state.status  -- includes the firewall hint shown under the code
  state.visible = true
  if not overlay then overlay = mp.create_osd_overlay('ass-events') end
  draw_qr()
  if hide_timer then hide_timer:kill() end
  if opts.qr_seconds > 0 then hide_timer = mp.add_timeout(opts.qr_seconds, hide) end
  publish()
end

local function toggle()
  if state.visible then hide(); return end
  if not rpc.connected() then osd(tr('Mando: mpvd no está conectado')); return end
  rpc.call('remote.pair', {}, function(err, result)
    if err then fail(err, 'no se pudo crear el código'); return end
    if type(result.status) == 'table' then state.status = result.status end
    show(result)
  end)
end

-- H23: the downloads panel in this computer's browser, already paired (one-time token in the URL)
local function open_downloads()
  if not rpc.connected() then osd(tr('Descargas: mpvd no está conectado')); return end
  rpc.call('remote.pair', { path = '/downloads', ['local'] = true }, function(err, result)
    if err then fail(err, 'panel de descargas'); return end
    if type(result.status) == 'table' then state.status = result.status end
    state.downloads_url = result.url or ''
    publish()
    local platform = mp.get_property_native('platform') or ''
    local cmd = opts.open_command
    if cmd == '' then cmd = platform == 'windows' and 'explorer' or (platform == 'darwin' and 'open' or 'xdg-open') end
    mp.command_native_async({ name = 'subprocess', args = { cmd, state.downloads_url }, detach = true,
                              playback_only = false, capture_stdout = false }, function() end)
    osd(tr('Panel de descargas abierto en el navegador'))
  end)
end

-- H42/A4: el mismo enlace del panel, pero copiado para llevárselo a otro aparato en vez de abierto aquí
local function copy_downloads_link()
  rpc.call('remote.pair', { path = '/downloads', ['local'] = false }, function(err, result)
    if err then fail(err, 'panel de descargas'); return end
    if type(result.status) == 'table' then state.status = result.status end
    state.downloads_url = result.url or ''
    publish()
    clip.copy_osd(state.downloads_url, osd, 'el enlace del panel de descargas')
  end)
end

mp.observe_property('osd-dimensions', 'native', function() if state.visible then draw_qr() end end)

-- ---------------------------------------------------------------------------------------------
-- menu

local COPY_ACTION = { { name = 'copy', icon = 'content_copy', label = tr('Copiar el enlace') } }

local function menu_items()
  local st = state.status or {}
  local items = {}
  items[#items + 1] = { title = state.visible and 'Ocultar el código QR' or 'Mostrar código QR para emparejar un móvil',
                        hint = 'alt+z', icon = 'qr_code_2', value = { action = 'toggle' } }
  items[#items + 1] = { title = tr('Panel de descargas en el navegador'), hint = tr('este ordenador · Tab copia el enlace'),
                        icon = 'download', value = { action = 'downloads' }, actions = COPY_ACTION }
  if st.running then
    items[#items + 1] = { title = tr('Servidor activo: %s'):format(tostring(st.url or '')),
                          hint = tr('puerto %s · Enter copia el enlace'):format(tostring(st.port)),
                          icon = 'wifi', value = { action = 'copy' } }
  else
    items[#items + 1] = { title = tr('Servidor del mando detenido'), icon = 'wifi_off', muted = true, selectable = false }
  end
  if type(st.firewall) == 'table' and st.firewall.command then
    items[#items + 1] = { title = tr('El cortafuegos (%s) puede bloquear al móvil'):format(st.firewall.tool),
                          hint = tr('copiar la orden'), icon = 'shield', value = { action = 'copy-fw' } }
  end
  local paired = st.paired or {}
  if #paired > 0 then
    local sub = {}
    for _, p in ipairs(paired) do
      sub[#sub + 1] = { title = p.name or p.id, hint = os.date('%d/%m %H:%M', math.floor(p.created or 0)),
                        icon = 'smartphone', selectable = false }
    end
    sub[#sub + 1] = { title = tr('Olvidar todos los mandos'), icon = 'delete', value = { action = 'forget' }, separator = true }
    items[#items + 1] = { title = tr('Móviles emparejados'), hint = tostring(#paired), icon = 'devices', items = sub }
  else
    items[#items + 1] = { title = tr('Ningún móvil emparejado'), icon = 'devices', muted = true, selectable = false }
  end
  items[#items + 1] = { title = st.running and 'Detener el servidor del mando' or 'Arrancar el servidor del mando',
                        icon = st.running and 'stop' or 'play_arrow', separator = true,
                        value = { action = st.running and 'stop' or 'start' } }
  items[#items + 1] = { title = tr('Sin conexión desde el móvil: revisa el cortafuegos (docs/REMOTE.md)'), icon = 'help',
                        muted = true, selectable = false }
  return items
end

local function remote_menu(items)
  return nav.decorate({ type = MENU, title = N:title({ 'Mando a distancia' }), items = items, callback = { SCRIPT, EVENT } })
end

local function refresh_menu()
  if uosc.open_type() ~= MENU then return end
  uosc.update(remote_menu(menu_items()))
end

local function open_menu()
  if not uosc.available() then osd(tr('Mando: uosc no disponible')); return end
  state.view = 'menu'
  uosc.open(remote_menu(uosc.loading_items()))
  rpc.call('remote.status', nil, function(err, st)
    if err then fail(err, 'estado del mando'); return end
    state.status = st
    publish()
    refresh_menu()
  end)
end

local function menu_action(v, action)
  if v.action == 'toggle' then
    toggle()
    uosc.close(MENU)
  elseif v.action == 'downloads' then
    if action == 'copy' then copy_downloads_link() return end
    uosc.close(MENU)
    open_downloads()
  elseif v.action == 'copy' or v.action == 'copy-fw' then
    local fw = state.status and state.status.firewall
    local text = v.action == 'copy' and tostring(state.status and state.status.url or '') or (fw and fw.command or '')
    clip.copy_osd(text, osd)
  elseif v.action == 'forget' then
    rpc.call('remote.forget', nil, function(err, r)
      if err then fail(err, 'olvidar mandos'); return end
      osd(string.format('Mando: %d móvil(es) olvidado(s)', r and r.forgotten or 0))
      rpc.call('remote.status', nil, function(_, st) state.status = st; publish(); refresh_menu() end)
    end)
  elseif v.action == 'start' or v.action == 'stop' then
    rpc.call('remote.' .. v.action, nil, function(err, st)
      if err then fail(err, 'servidor del mando'); return end
      state.status = st
      if v.action == 'stop' then hide() end
      publish()
      refresh_menu()
    end)
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = require('mp.utils').parse_json(json or '')
  if type(ev) ~= 'table' then return end
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then
    if not N:leave() then uosc.close(MENU) end
    return
  end
  if ev.type == 'activate' and type(ev.value) == 'table' then menu_action(ev.value, ev.action) end
  if ev.type == 'close' then state.view = ''; publish() end
end)

mp.add_key_binding(nil, 'remote-qr', toggle)
N:binding('remote-menu', open_menu)
mp.register_script_message('mu-remote-show', toggle)
mp.register_script_message('mu-remote-hide', hide)
mp.register_script_message('mu-remote-downloads', open_downloads)
mp.register_script_message('mu-remote-downloads-copy', copy_downloads_link)
publish()
