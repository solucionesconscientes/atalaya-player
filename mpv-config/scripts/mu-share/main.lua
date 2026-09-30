-- mu-share: «Compartir · ver juntos» (H25, ADR-054). A private room served by mpvd (share.*): the link and its QR
-- are drawn over the video like the remote's (ASS overlay), guests watch in their browser in sync with this player.
--   · Menu: create the room, show/copy the link, «Invitados» (give or take back control, expel), new link, close.
--   · A guest asking for control opens a yes/no menu here (also `script-message-to mu_share mu-share-answer <id> yes|no`).
--   · mpvd pushes `mu-event` {event:'share', kind, text, status}: notices («Ana ha pausado») go to the OSD.
-- Bindings: share-menu, share-qr. Script name: mu_share. State for the tests: user-data/mu/share.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local assdraw = require('mp.assdraw')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-share'
local EVENT = 'mu-share-event'
local ASK_MENU = 'mu-share-request'
local ASK_EVENT = 'mu-share-request-event'
local ROOT_TITLE = 'Compartir'

local opts = {
  qr_seconds = 120,      -- hide the QR automatically after this many seconds (0 = never)
  qr_size = 300,         -- QR side in PlayRes(720) pixels
  ask_seconds = 30,      -- the «X pide el control» menu closes itself after this (the request stays in Invitados)
  ttl_hours = 4,         -- life of a room
  osd_seconds = 3,
}
options.read_options(opts, 'mu-share')

local state = {
  status = nil, view = '', stack = {}, items = {}, force_open = false,
  qr = nil, url = '', qr_visible = false, last_notice = '', last_request = nil, asking = nil,
  last_error = '', copied = '',
}
local overlay, hide_timer, ask_timer = nil, nil, nil

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function compact_guests()
  local out = {}
  for _, g in ipairs(state.status and state.status.guests or {}) do
    out[#out + 1] = { id = g.id, name = g.name, perm = g.perm, pending = g.pending, connected = g.connected }
  end
  return out
end

local function publish()
  local st = state.status or {}
  mp.set_property_native('user-data/mu/share', {
    open = st.open or false, url = st.url or '', room = st.room and st.room.id or '', guests = compact_guests(),
    pending = #(st.pending or {}), view = state.view, depth = #state.stack, items = state.items,
    qr_visible = state.qr_visible, qr_size = state.qr and state.qr.size or 0, last_notice = state.last_notice,
    last_request = state.last_request or { id = '', name = '' }, asking = state.asking or '',
    media = st.media and st.media.kind or '', last_error = state.last_error, copied = state.copied,
    lan_only = st.lan_only ~= false, port = st.port or 0,
  })
end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  osd('Compartir: ' .. m)
  return m
end

-- ---------------------------------------------------------------------------------------------
-- QR overlay (same drawing as mu-remote: one rectangle per horizontal run of dark modules)

local function esc(s) return (tostring(s or ''):gsub('\\', '\\\\'):gsub('{', '\\{'):gsub('}', '\\}')) end

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
  local y0 = math.floor((720 - total) / 2) - 40
  local ass = assdraw.ass_new()
  ass:new_event()
  ass:append('{\\pos(0,0)\\an7\\bord0\\shad0\\blur0\\1c&H000000&\\1a&H60&}')
  ass:draw_start()
  ass:rect_cw(0, 0, res_w, 720)
  ass:draw_stop()
  ass:new_event()
  ass:append('{\\pos(0,0)\\an7\\bord0\\shad0\\blur0\\1c&HFFFFFF&}')
  ass:draw_start()
  ass:round_rect_cw(x0, y0, x0 + total, y0 + total, 8)
  ass:draw_stop()
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
  local cx = math.floor(res_w / 2)
  ass:new_event()
  ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs26\\1c&HFFFFFF&\\3c&H000000&}', cx, y0 + total + 12))
  ass:append('Ver juntos: escanea el código o comparte el enlace')
  ass:new_event()
  ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs19\\1c&HCCCCCC&\\3c&H000000&}', cx, y0 + total + 48))
  ass:append(esc(state.url))
  ass:new_event()
  ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs18\\1c&HCCCCCC&\\3c&H000000&}', cx, y0 + total + 76))
  ass:append('Solo para quien esté en tu misma red · el enlace da acceso a la sala: pásalo solo a quien quieras')
  local fw = state.status and state.status.firewall
  if type(fw) == 'table' and fw.command then
    ass:new_event()
    ass:append(string.format('{\\pos(%d,%d)\\an8\\bord2\\shad0\\fs17\\1c&H66CCFF&\\3c&H000000&}', cx, y0 + total + 102))
    ass:append('¿No conectan? El cortafuegos (' .. esc(fw.tool) .. ') bloquea el puerto ' .. tostring(fw.port) ..
               '. En una terminal: ' .. esc(fw.command))
  end
  overlay.res_x = res_w
  overlay.res_y = 720
  overlay.z = 1000
  overlay.data = ass.text
  overlay:update()
end

local function hide_qr()
  if hide_timer then hide_timer:kill(); hide_timer = nil end
  if overlay then overlay:remove(); overlay = nil end
  state.qr_visible = false
  publish()
end

local function show_qr(result)
  state.url = result.url or ''
  state.qr = result.qr
  if type(result.status) == 'table' then state.status = result.status end
  state.qr_visible = true
  if not overlay then overlay = mp.create_osd_overlay('ass-events') end
  draw_qr()
  if hide_timer then hide_timer:kill() end
  if opts.qr_seconds > 0 then hide_timer = mp.add_timeout(opts.qr_seconds, hide_qr) end
  publish()
end

mp.observe_property('osd-dimensions', 'native', function() if state.qr_visible then draw_qr() end end)

-- ---------------------------------------------------------------------------------------------
-- menu

local function remember(items)
  local out = {}
  for _, it in ipairs(items or {}) do
    out[#out + 1] = { title = it.title or '', hint = it.hint or '', value = it.value or '' }
  end
  state.items = out
end

local function base_menu(title, items)
  local menu = { type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, on_close = 'callback',
                 keep_open = true, search_submenus = false }
  return N:frame(menu, state.stack)
end

local function show(title, items)
  remember(items)
  publish()
  if uosc.open_type() == MENU and not state.force_open then
    uosc.update(base_menu(title, items))
  else
    uosc.open(base_menu(title, items))
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

local function reopen_current()
  if state.asking then return end  -- the yes/no menu of a request is open in place of ours
  local spec = state.stack[#state.stack]
  if spec and uosc.open_type() == MENU then open_view(spec, false) end
end

local function refresh_status(cb)
  rpc.call('share.status', nil, function(err, st)
    if err then fail(err, 'estado de la sala'); if cb then cb(false) end; return end
    state.status = st
    publish()
    if cb then cb(true) end
  end)
end

local function perm_text(g)
  if g.pending then return 'pide el control' end
  local p = g.perm == 'control' and 'controla' or 'solo ver'
  if not g.connected then p = p .. ' · desconectado' end
  return p
end

local function expires_text(room)
  local s = room and room.expires_in or 0
  local h, m = math.floor(s / 3600), math.floor((s % 3600) / 60)
  if h > 0 then return string.format('caduca en %d h %d min', h, m) end
  return string.format('caduca en %d min', m)
end

local MEDIA_TEXT = {
  hls = 'retransmisión desde este equipo', direct = 'el vídeo directo de la web', preparing = 'preparando…',
  none = 'nada todavía',
}

views.root = function()
  local st = state.status or {}
  local items = {}
  if not rpc.connected() then
    items = uosc.message_items('mpvd no está conectado: espera unos segundos', 'error')
  elseif not st.open then
    items[#items + 1] = { title = 'Crear una sala para ver juntos', icon = 'group_add', value = { action = 'create' },
                          hint = 'enlace y QR' }
    items[#items + 1] = { title = 'Tus invitados verán lo mismo que tú, a la vez, en su navegador', icon = 'info',
                          muted = true, selectable = false }
    items[#items + 1] = { title = 'De momento, solo en tu misma red (wifi de casa)', icon = 'wifi', muted = true,
                          selectable = false }
  else
    items[#items + 1] = { title = 'Mostrar el enlace y el código QR', icon = 'qr_code_2', value = { action = 'qr' } }
    items[#items + 1] = { title = 'Copiar el enlace', icon = 'content_copy', value = { action = 'copy' } }
    local guests = st.guests or {}
    local online = 0
    for _, g in ipairs(guests) do if g.connected then online = online + 1 end end
    items[#items + 1] = { title = 'Invitados', icon = 'group', value = { view = 'guests' },
                          hint = string.format('%d en la sala · %d conectados', #guests, online) }
    for _, g in ipairs(st.pending or {}) do
      items[#items + 1] = { title = g.name .. ' pide el control', icon = 'front_hand', hint = 'responder',
                            value = { view = 'guest', id = g.id } }
    end
    local media = st.media or {}
    local mt = MEDIA_TEXT[media.kind or 'none'] or ''
    if media.kind == 'hls' and media.mode == 'vaapi' then mt = mt .. ' (tarjeta gráfica)'
    elseif media.kind == 'hls' and media.mode == 'cpu' then mt = mt .. ' (convirtiendo)' end
    items[#items + 1] = { title = 'Ven: ' .. mt, icon = 'live_tv', muted = true, selectable = false }
    if type(st.firewall) == 'table' and st.firewall.command then
      items[#items + 1] = { title = 'El cortafuegos (' .. st.firewall.tool .. ') puede bloquear a los invitados',
                            hint = 'copiar la orden', icon = 'shield', value = { action = 'copy-fw' } }
    end
    items[#items + 1] = { title = 'Enlace nuevo', hint = 'el anterior deja de valer', icon = 'autorenew',
                          value = { action = 'rotate' }, separator = true }
    items[#items + 1] = { title = 'Cerrar la sala', icon = 'close', value = { action = 'close' } }
    items[#items + 1] = { title = expires_text(st.room) .. ' · solo tu red', icon = 'schedule', muted = true,
                          selectable = false }
  end
  show(ROOT_TITLE, items)
end

views.guests = function()
  local items = {}
  for _, g in ipairs(state.status and state.status.guests or {}) do
    items[#items + 1] = { title = g.name, hint = perm_text(g),
                          icon = g.perm == 'control' and 'sports_esports' or (g.connected and 'person' or 'person_off'),
                          value = { view = 'guest', id = g.id } }
  end
  if #items == 0 then
    items = uosc.message_items('Aún no ha entrado nadie: comparte el enlace', 'group')
  end
  show('Invitados', items)
end

local function find_guest(id)
  for _, g in ipairs(state.status and state.status.guests or {}) do
    if g.id == id then return g end
  end
  return nil
end

views.guest = function(args)
  local g = find_guest(args.id)
  if not g then
    show('Invitado', uosc.message_items('Ya no está en la sala', 'person_off'))
    return
  end
  local items = {}
  if g.perm == 'control' then
    items[#items + 1] = { title = 'Quitar el control', icon = 'remove_moderator', value = { perm = 'view', id = g.id } }
  else
    items[#items + 1] = { title = 'Dar el control', hint = 'podrá pausar y saltar', icon = 'sports_esports',
                          value = { perm = 'control', id = g.id } }
  end
  if g.pending then
    items[#items + 1] = { title = 'Rechazar la petición', icon = 'block', value = { deny = g.id } }
  end
  items[#items + 1] = { title = 'Sacar de la sala', icon = 'person_remove', value = { kick = g.id }, separator = true }
  items[#items + 1] = { title = perm_text(g), icon = 'info', muted = true, selectable = false }
  show(g.name, items)
end

local function after_change(err, st, what, text)
  if err then fail(err, what); return end
  state.status = st
  if text then osd(text) end
  publish()
  reopen_current()
end

local function set_perm(id, perm)
  local g = find_guest(id) or { name = 'El invitado' }
  rpc.call('share.permission', { guest = id, perm = perm }, function(err, st)
    after_change(err, st, 'permiso', perm == 'control' and (g.name .. ' puede controlar la reproducción')
                 or (g.name .. ' ya solo puede ver'))
  end)
end

local function deny(id)
  rpc.call('share.deny', { guest = id }, function(err, st) after_change(err, st, 'rechazar') end)
end

local function kick(id)
  local g = find_guest(id) or { name = 'El invitado' }
  rpc.call('share.kick', { guest = id }, function(err, st)
    after_change(err, st, 'sacar de la sala', g.name .. ' ya no está en la sala')
    if not err and state.view == 'guest' then
      table.remove(state.stack)
      reopen_current()
    end
  end)
end

local function create_room(then_menu)
  if not rpc.connected() then osd('Compartir: mpvd no está conectado'); return end
  rpc.call('share.create', { ttl_hours = opts.ttl_hours }, function(err, res)
    if err then fail(err, 'no se pudo crear la sala'); return end
    state.last_error = ''
    show_qr(res)
    if then_menu then reopen_current() else uosc.close(MENU) end
  end, 30)
end

local function toggle_qr()
  if state.qr_visible then hide_qr(); return end
  if not rpc.connected() then osd('Compartir: mpvd no está conectado'); return end
  rpc.call('share.link', nil, function(err, res)
    if err then create_room(false); return end
    show_qr(res)
  end)
end

local function copy_text(text)
  local ok = text ~= '' and mp.set_property('clipboard/text', text)
  state.copied = ok and text or ''
  publish()
  osd(ok and ('Copiado: ' .. text) or ('Compartir: ' .. text))
end

local function menu_action(v)
  if v.action == 'create' then
    uosc.close(MENU)
    create_room(false)
  elseif v.action == 'qr' then
    uosc.close(MENU)
    if not state.qr_visible then toggle_qr() end
  elseif v.action == 'copy' then
    copy_text(state.status and state.status.url or '')
  elseif v.action == 'copy-fw' then
    local fw = state.status and state.status.firewall
    copy_text(fw and fw.command or '')
  elseif v.action == 'rotate' then
    rpc.call('share.rotate', nil, function(err, res)
      if err then fail(err, 'enlace nuevo'); return end
      state.status = res.status
      if state.qr_visible then show_qr(res) end
      osd('Enlace nuevo creado: el anterior ya no vale')
      publish()
      reopen_current()
    end)
  elseif v.action == 'close' then
    rpc.call('share.close', nil, function(err, st)
      if err then fail(err, 'cerrar la sala'); return end
      state.status = st
      hide_qr()
      osd('Sala cerrada')
      publish()
      reopen_current()
    end)
  elseif v.perm then
    set_perm(v.id, v.perm)
  elseif v.deny then
    deny(v.deny)
  elseif v.kick then
    kick(v.kick)
  elseif v.view then
    open_view({ name = v.view, args = v })
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' and type(ev.value) == 'table' then
    menu_action(ev.value)
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
      state.stack, state.view = {}, ''
      publish()
    end
  end)
end)

local function open_root()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
  refresh_status(function(ok) if ok and state.view == 'root' then reopen_current() end end)
end

-- ---------------------------------------------------------------------------------------------
-- a guest asks for control

local function close_ask()
  if ask_timer then ask_timer:kill(); ask_timer = nil end
  state.asking = nil
  if uosc.open_type() == ASK_MENU then uosc.close(ASK_MENU) end
  publish()
end

local function answer(id, yes)
  close_ask()
  if yes then set_perm(id, 'control') else deny(id) end
end

local function ask(guest)
  state.last_request = { id = guest.id, name = guest.name }
  state.asking = guest.id
  publish()
  if not uosc.available() then return end
  uosc.open({
    type = ASK_MENU, title = guest.name .. ' pide el control', callback = { SCRIPT, ASK_EVENT }, on_close = 'callback',
    items = {
      { title = 'Dar el control', hint = 'podrá pausar y saltar', icon = 'check', value = { id = guest.id, yes = true } },
      { title = 'Ahora no', icon = 'close', value = { id = guest.id, yes = false } },
    },
  })
  if ask_timer then ask_timer:kill() end
  ask_timer = mp.add_timeout(opts.ask_seconds, function()
    ask_timer = nil
    if state.asking == guest.id then close_ask() end  -- no answer: the request waits in «Invitados»
  end)
end

mp.register_script_message(ASK_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type == 'activate' and type(ev.value) == 'table' then
    answer(ev.value.id, ev.value.yes == true)
  elseif ev.type == 'close' or ev.type == 'back' then
    if state.asking then close_ask() end  -- dismissed: undecided, still pending in «Invitados»
  end
end)

-- `script-message-to mu_share mu-share-answer <guest id> yes|no`
mp.register_script_message('mu-share-answer', function(id, yes)
  if not id or id == '' then return end
  answer(id, yes == 'yes' or yes == 'si' or yes == 'sí')
end)

-- ---------------------------------------------------------------------------------------------
-- events pushed by mpvd

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'share' then return end
  if type(ev.status) == 'table' then state.status = ev.status end
  if ev.kind == 'notice' and ev.text and ev.text ~= '' then
    state.last_notice = ev.text
    osd(ev.text)
  elseif ev.kind == 'request' and type(ev.guest) == 'table' then
    state.last_notice = ev.text or ''
    osd(ev.text or '')
    ask(ev.guest)
  elseif ev.kind == 'closed' then
    state.last_notice = ev.text or ''
    hide_qr()
    close_ask()
    osd(ev.text or 'Sala cerrada')
  end
  publish()
  -- the yes/no menu of a request replaces ours: refreshing ours now would close it again
  if ev.kind ~= 'request' and (state.view == 'root' or state.view == 'guests' or state.view == 'guest') then
    reopen_current()
  end
end)

N:binding('share-menu', open_root)
mp.add_key_binding(nil, 'share-qr', toggle_qr)
mp.register_script_message('mu-share-hide', hide_qr)
publish()
msg.info('mu-share loaded')
