-- mu-share: «Compartir · ver juntos» (H25, ADR-054). A private room served by mpvd (share.*): the link and its QR
-- are drawn over the video like the remote's (ASS overlay), guests watch in their browser in sync with this player.
--   · Menu: create the room, show/copy the link, «Invitados» (give or take back control, expel), new link, close.
--   · A guest asking for control opens a yes/no menu here (also `script-message-to mu_share mu-share-answer <id> yes|no`).
--   · mpvd pushes `mu-event` {event:'share', kind, text, status}: notices («Ana ha pausado») go to the OSD.
--   · «Sala pública (solo ver)»: anyone with the link, anonymous, up to `max_viewers`; no control, no chat.
--   · Private rooms: chat and reactions drawn briefly at the bottom left (ASS overlay, escaped, ≤10 Hz); the host
--     writes with a text box (uosc palette) from «Chat».
--   · «Emitir en directo…» (live.*, ADR-061): server preset or URL, the stream key pasted from the clipboard (read
--     by mpvd, never shown nor published here), start from here / from the beginning, status, stop.
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
local prefs = require('mu.prefs')
local clip = require('mu.clip')
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-share'
local EVENT = 'mu-share-event'
local ASK_MENU = 'mu-share-request'
local ASK_EVENT = 'mu-share-request-event'
local INPUT = 'mu-share-input'
local INPUT_EVENT = 'mu-share-input-event'
local ROOT_TITLE = 'Compartir'

local opts = {
  qr_seconds = 120,      -- hide the QR automatically after this many seconds (0 = never)
  qr_size = 300,         -- QR side in PlayRes(720) pixels
  ask_seconds = 30,      -- the «X pide el control» menu closes itself after this (the request stays in Invitados)
  ttl_hours = 4,         -- life of a room
  osd_seconds = 3,
  max_viewers = 20,      -- public room: viewers at once (mpvd caps it at 100)
  chat_osd = true,       -- show chat messages and reactions over the video
  chat_seconds = 8,      -- how long each chat line stays on screen
  chat_lines = 5,        -- lines on screen at most
  join = '',             -- H44/C6: invitation the launcher found in the command line; entered as soon as mpvd answers
}
options.read_options(opts, 'mu-share')

-- «Que se pueda entrar desde internet» (H25, cambiado en H51): ENCENDIDO por defecto. Una sala es para ver algo
-- con alguien que no está aquí; si solo vale dentro de casa, no sirve para lo que se pide. El túnel sigue viviendo
-- exactamente lo que vive la sala, y se puede apagar desde el menú.
local P = prefs.ns('mu-share', { internet = true })

local state = {
  status = nil, view = '', stack = {}, items = {}, force_open = false,
  qr = nil, url = '', qr_visible = false, last_notice = '', last_request = nil, asking = nil,
  last_error = '', copied = '', live = nil, last_chat = nil, chat_visible = 0, input = nil,
}
local overlay, hide_timer, ask_timer = nil, nil, nil
local chat_overlay, chat_timer, chat_render_pending = nil, nil, false
local chat_rows = {}
local live_timer = nil
local live_hint  -- defined with the «Emitir en directo» views

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function compact_guests()
  local out = {}
  for _, g in ipairs(state.status and state.status.guests or {}) do
    out[#out + 1] = { id = g.id, name = g.name, perm = g.perm, pending = g.pending, connected = g.connected }
  end
  return out
end

-- what the tests may see of «Emitir en directo» (the key never reaches this script)
local function live_public()
  local lv = state.live or {}
  local run = type(lv.run) == 'table' and lv.run or {}
  return { configured = lv.configured or false, has_key = lv.has_key or false, key_length = lv.key_length or 0,
           server_host = lv.server_host or '', active = lv.active or false, status = run.status or '',
           mode = run.mode or '', error = run.error or '' }
end

-- H44/C6 · la sala en la que hemos entrado como invitados, en corto (lo mismo que enseña el menú)
local function guest_public(g)
  if type(g) ~= 'table' then return { room = '', host = '', connected = false, kind = '' } end
  local room = g.room or {}
  return { room = room.id or '', host = room.host or '', connected = g.connected == true, kind = g.kind or '' }
end

local function publish()
  local st = state.status or {}
  mp.set_property_native('user-data/mu/share', {
    open = st.open or false, url = st.url or '', room = st.room and st.room.id or '', guests = compact_guests(),
    pending = #(st.pending or {}), view = state.view, depth = #state.stack, items = state.items,
    qr_visible = state.qr_visible, qr_size = state.qr and state.qr.size or 0, last_notice = state.last_notice,
    last_request = state.last_request or { id = '', name = '' }, asking = state.asking or '',
    media = st.media and st.media.kind or '', last_error = state.last_error, copied = state.copied,
    lan_only = st.lan_only ~= false, port = st.port or 0, mode = st.mode or '', viewers = st.viewers or 0,
    max_viewers = st.max_viewers or 0, last_chat = state.last_chat or { who = '', text = '', kind = '' },
    chat_visible = state.chat_visible, chat_osd = opts.chat_osd, input = state.input and state.input.mode or '',
    live = live_public(), guest_of = guest_public(st.guest_of),
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
  local public = state.status and state.status.mode == 'public'
  ass:append(public and 'Sala pública (solo ver): escanea el código o comparte el enlace'
             or 'Ver juntos: escanea el código o comparte el enlace')
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

-- segundos → «12:34» o «1:02:03», para los avisos de progreso
local function reloj(seconds)
  local n = math.max(0, math.floor(tonumber(seconds) or 0))
  -- LuaJIT (5.1) no tiene división entera `//`: math.floor
  if n >= 3600 then return string.format('%d:%02d:%02d', math.floor(n / 3600), math.floor(n % 3600 / 60), n % 60) end
  return string.format('%d:%02d', math.floor(n / 60), n % 60)
end

local function hide_qr()
  if hide_timer then hide_timer:kill(); hide_timer = nil end
  if overlay then overlay:remove(); overlay = nil end
  state.qr_visible = false
  publish()
end

-- ``refresco``: el estado de la sala cambia a menudo (entra un invitado, llega un chat) y el QR se redibuja con los datos
-- nuevos. En ese caso NO se rearma el temporizador de ocultado: si se rearmaba, con una sala viva el QR no se iba nunca.
local function show_qr(result, refresco)
  state.url = result.url or ''
  state.qr = result.qr
  if type(result.status) == 'table' then state.status = result.status end
  state.qr_visible = true
  if not overlay then overlay = mp.create_osd_overlay('ass-events') end
  draw_qr()
  if not refresco then
    if hide_timer then hide_timer:kill() end
    if opts.qr_seconds > 0 then hide_timer = mp.add_timeout(opts.qr_seconds, hide_qr) end
  end
  publish()
end

-- ---------------------------------------------------------------------------------------------
-- chat overlay: the last lines at the bottom left, each for chat_seconds; redrawn at most every 0.1 s

local function osd_res_w()
  local ow = mp.get_property_number('osd-width', 1280)
  local oh = math.max(1, mp.get_property_number('osd-height', 720))
  local w = math.floor(720 * (ow / oh) + 0.5)
  return w < 100 and 1280 or w
end

local function clip_utf8(s, max)
  if #s <= max then return s end
  s = s:sub(1, max):gsub('[\192-\255][\128-\191]*$', '')  -- never cut a character in half
  return s .. '…'
end

local chat_render

local function chat_schedule()
  if chat_render_pending then return end
  chat_render_pending = true
  mp.add_timeout(0.1, function() chat_render_pending = false; chat_render() end)
end

chat_render = function()
  if chat_timer then chat_timer:kill(); chat_timer = nil end
  local now = mp.get_time()
  local keep = {}
  for _, r in ipairs(chat_rows) do if r.expires > now then keep[#keep + 1] = r end end
  while #keep > opts.chat_lines do table.remove(keep, 1) end
  chat_rows = keep
  state.chat_visible = #chat_rows
  if #chat_rows == 0 then
    if chat_overlay then chat_overlay:remove(); chat_overlay = nil end
    publish()
    return
  end
  if not chat_overlay then chat_overlay = mp.create_osd_overlay('ass-events') end
  local ass = assdraw.ass_new()
  local y = 720 - 96
  local soonest = math.huge
  for i = #chat_rows, 1, -1 do
    local r = chat_rows[i]
    local color = r.host and '&H40C8FF&' or '&HFFD08A&'
    ass:new_event()
    ass:append(string.format('{\\pos(24,%d)\\an1\\bord2\\shad0\\fs22\\3c&H000000&\\1c%s\\b1}', y, color))
    ass:append(esc(r.who))
    ass:append('{\\b0\\1c&HFFFFFF&' .. (r.reaction and '\\i1' or '') .. '}' .. (r.reaction and ' ' or ': '))
    ass:append(esc(r.body))
    y = y - 30
    if r.expires < soonest then soonest = r.expires end
  end
  chat_overlay.res_x = osd_res_w()
  chat_overlay.res_y = 720
  chat_overlay.z = 900
  chat_overlay.data = ass.text
  chat_overlay:update()
  chat_timer = mp.add_timeout(math.max(0.1, soonest - now + 0.05), chat_render)
  publish()
end

local function chat_push(row)
  if type(row) ~= 'table' then return end
  local reaction = row.kind == 'reaction'
  local body = reaction and (row.words or '') or clip_utf8(tostring(row.text or ''), 120)
  state.last_chat = { who = row.who or '', text = row.text or '', kind = row.kind or '', reaction = row.reaction or '' }
  if opts.chat_osd then
    chat_rows[#chat_rows + 1] = { who = row.who or '', body = body, host = row.host == true, reaction = reaction,
                                  expires = mp.get_time() + opts.chat_seconds }
    chat_schedule()
  end
  publish()
end

local function chat_clear()
  chat_rows = {}
  chat_render()
end

mp.observe_property('osd-dimensions', 'native', function()
  if state.qr_visible then draw_qr() end
  if #chat_rows > 0 then chat_schedule() end
end)

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

-- H44/C6 · «dónde va» la sala en la que hemos entrado, para la fila de estado
local function guest_hint(g)
  if not g then return '' end
  if g.error and g.error ~= '' then return g.error end
  if not g.connected then return 'reconectando…' end
  local where = g.pos and reloj(g.pos) or nil
  return (g.paused and 'en pausa' or 'viendo') .. (where and (' · ' .. where) or '')
end

views.root = function()
  local st = state.status or {}
  local items = {}
  local inside = st.guest_of
  if not rpc.connected() then
    items = uosc.message_items('mpvd no está conectado: espera unos segundos', 'error')
  elseif inside then
    -- estamos de invitados en la sala de otro: eso es lo único que importa aquí
    local host = (inside.room or {}).host
    items[#items + 1] = { title = 'Estás en la sala' .. (host and (' de ' .. host) or ''), icon = 'groups',
                          hint = guest_hint(inside), muted = true, selectable = false }
    if inside.title and inside.title ~= '' then
      items[#items + 1] = { title = inside.title, icon = 'movie', muted = true, selectable = false,
                            hint = inside.kind == 'file' and 'el archivo original del anfitrión' or nil }
    end
    items[#items + 1] = { title = 'Salir de la sala', icon = 'logout', value = { action = 'leave' } }
    items[#items + 1] = { title = 'Mientras estés dentro, el reproductor sigue al anfitrión', icon = 'info',
                          muted = true, selectable = false, hint = 'pausa, saltos y velocidad' }
  elseif not st.open then
    items[#items + 1] = { title = 'Crear una sala para ver juntos', icon = 'group_add', value = { action = 'create' },
                          hint = 'enlace y QR' }
    items[#items + 1] = { title = 'Crear una sala pública (solo ver)', icon = 'public',
                          value = { action = 'create', mode = 'public' },
                          hint = string.format('sin nombres ni chat · hasta %d', opts.max_viewers) }
    items[#items + 1] = { title = 'Tus invitados verán lo mismo que tú, a la vez, en su navegador', icon = 'info',
                          muted = true, selectable = false }
    local internet = P:get('internet') == true
    local can = st.tunnel_available ~= false
    items[#items + 1] = { title = 'Que se pueda entrar desde internet', icon = internet and 'public' or 'wifi',
                          value = { action = 'toggle_internet' }, active = internet,
                          hint = (not can and 'falta cloudflared')
                            or (internet and 'túnel de Cloudflare mientras la sala esté abierta')
                            or 'ahora solo desde tu wifi' }
    if internet and not can then
      items[#items + 1] = { title = 'Instálalo con: MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh', icon = 'info',
                            muted = true, selectable = false }
    end
  else
    local public = st.mode == 'public'
    -- H51 · lo primero es el enlace, que es lo que se manda a quien no está aquí; el QR baja a segundo plano
    local tunel = st.tunnel_state or 'off'
    local copiar_hint = 'listo para pegar'
    if tunel == 'starting' or tunel == 'warming' then
      copiar_hint = 'todavía no sirve fuera de tu red · se copia solo en cuanto conteste'
    elseif tunel == 'ready' then
      copiar_hint = 'se puede entrar desde internet'
    elseif tunel == 'failed' then
      copiar_hint = 'solo dentro de tu red'
    end
    items[#items + 1] = { title = 'Copiar el enlace', icon = 'content_copy', hint = copiar_hint,
                          value = { action = 'copy' } }
    if tunel == 'starting' or tunel == 'warming' then
      items[#items + 1] = { title = 'Abriendo la puerta a internet…', icon = 'hourglass_top', muted = true,
                            selectable = false,
                            hint = tunel == 'starting' and 'arrancando' or 'esperando a que la dirección conteste' }
    elseif tunel == 'failed' and (st.tunnel_error or '') ~= '' then
      items[#items + 1] = { title = 'Sin puerta a internet: ' .. st.tunnel_error, icon = 'info', muted = true,
                            selectable = false, hint = 'la sala funciona en tu red' }
    end
    -- H54 · el enlace de la sala NO puede abrirse en VLC ni en mpv: su token va en el fragmento (#k=) y un
    -- navegador no manda nunca el fragmento al servidor. Este es el que sí vale, y da calidad original.
    items[#items + 1] = { title = 'Copiar el enlace para VLC o mpv', icon = 'play_circle',
                          hint = 'calidad original · el de la sala solo vale en el navegador',
                          value = { action = 'copy-player' } }
    items[#items + 1] = { title = state.qr_visible and 'Ocultar el código QR' or 'Mostrar el código QR',
                          icon = state.qr_visible and 'qr_code_scanner' or 'qr_code_2', active = state.qr_visible,
                          hint = state.qr_visible and 'también con alt+Q' or 'para quien esté delante',
                          value = { action = 'qr' } }
    if public then
      items[#items + 1] = { title = 'Sala pública (solo ver)', icon = 'public', muted = true, selectable = false,
                            hint = string.format('%d viendo · máximo %d', st.viewers or 0, st.max_viewers or 0) }
    else
      local guests = st.guests or {}
      local online = 0
      for _, g in ipairs(guests) do if g.connected then online = online + 1 end end
      items[#items + 1] = { title = 'Invitados', icon = 'group', value = { view = 'guests' },
                            hint = string.format('%d en la sala · %d conectados', #guests, online) }
      for _, g in ipairs(st.pending or {}) do
        items[#items + 1] = { title = g.name .. ' pide el control', icon = 'front_hand', hint = 'responder',
                              value = { view = 'guest', id = g.id } }
      end
      local n = #(st.chat or {})
      items[#items + 1] = { title = 'Chat', icon = 'chat', value = { view = 'chat' },
                            hint = n > 0 and string.format('%d mensajes', n) or 'escribir a los invitados' }
    end
    local media = st.media or {}
    local mt = MEDIA_TEXT[media.kind or 'none'] or ''
    if media.kind == 'hls' and media.mode == 'vaapi' then mt = mt .. ' (tarjeta gráfica)'
    elseif media.kind == 'hls' and media.mode == 'cpu' then mt = mt .. ' (convirtiendo)' end
    -- H1: un archivo tuyo hay que empaquetarlo para el navegador del invitado, y eso tarda. Decirlo aquí, porque si no
    -- el invitado ve «preparando» y tú no tienes forma de saber si va, cuánto le queda o si se ha roto.
    local prep = nil
    for _, pre in ipairs({ '', 'relay_' }) do
      local estado, listos = media[pre .. 'status'], media[pre .. 'ready']
      if estado == 'failed' then
        prep = { 'La retransmisión ha fallado', media[pre .. 'error'] or '', 'error' }
      elseif estado and estado ~= 'done' and not media[pre .. 'complete'] then
        local total = mp.get_property_number('duration')
        prep = { 'Preparando la retransmisión…',
                 (listos and listos > 0)
                   and (reloj(listos) .. ' listos' .. (total and (' de ' .. reloj(total)) or ''))
                   or 'empezando',
                 'hourglass_top' }
      end
    end
    items[#items + 1] = { title = 'Ven: ' .. mt, icon = 'live_tv', muted = true, selectable = false }
    if prep then
      items[#items + 1] = { title = prep[1], hint = prep[2], icon = prep[3], muted = true, selectable = false }
    end
    if type(st.firewall) == 'table' and st.firewall.command then
      items[#items + 1] = { title = 'El cortafuegos (' .. st.firewall.tool .. ') puede bloquear a los invitados',
                            hint = 'copiar la orden', icon = 'shield', value = { action = 'copy-fw' } }
    end
    items[#items + 1] = { title = 'Enlace nuevo', hint = 'el anterior deja de valer', icon = 'autorenew',
                          value = { action = 'rotate' }, separator = true }
    items[#items + 1] = { title = 'Cerrar la sala', icon = 'close', value = { action = 'close' } }
    local donde = (st.tunnel_state == 'ready') and 'desde internet' or 'solo tu red'
    items[#items + 1] = { title = expires_text(st.room) .. ' · ' .. donde, icon = 'schedule', muted = true,
                          selectable = false }
  end
  if rpc.connected() and not inside then
    items[#items + 1] = { title = 'Entrar en una sala de otro…', icon = 'login', value = { view = 'join' },
                          hint = 'te han pasado un enlace', separator = true }
    items[#items + 1] = { title = 'Emitir en directo…', icon = 'sensors', value = { view = 'live' },
                          hint = live_hint() }
  end
  show(ROOT_TITLE, items)
end

views.join = function()
  local pegado = clip.first_line()
  local es_sala = pegado:match('^https?://[^%s]+/s/[%w_-]+#k=.') ~= nil
  local items = {}
  if es_sala then
    items[#items + 1] = { title = 'Entrar con el enlace copiado', icon = 'content_paste_go',
                          hint = (#pegado > 60 and (pegado:sub(1, 57) .. '…') or pegado),
                          value = { action = 'join-clipboard' } }
  end
  items[#items + 1] = { title = 'Escribir o pegar el enlace…', icon = 'edit', value = { action = 'join-write' } }
  if not es_sala and pegado ~= '' then
    items[#items + 1] = { title = 'Lo que tienes copiado no es un enlace de sala', icon = 'info', muted = true,
                          selectable = false, hint = 'tiene que acabar en #k=…' }
  end
  items[#items + 1] = { title = 'Verás lo mismo que el anfitrión, a la vez y con su archivo original', icon = 'info',
                        muted = true, selectable = false, separator = true }
  show('Entrar en una sala', items)
end

views.chat = function()
  local items = {
    { title = 'Escribir un mensaje', icon = 'edit', value = { action = 'chat-write' } },
    { title = 'Mostrar el chat en pantalla', icon = opts.chat_osd and 'toggle_on' or 'toggle_off',
      hint = opts.chat_osd and 'sí' or 'no', value = { action = 'chat-osd' } },
  }
  local rows = state.status and state.status.chat or {}
  if #rows == 0 then
    items[#items + 1] = { title = 'Aún no hay mensajes', icon = 'chat_bubble_outline', muted = true,
                          selectable = false, separator = true }
  end
  for i = #rows, 1, -1 do
    local r = rows[i]
    items[#items + 1] = { title = r.who .. ': ' .. clip_utf8(tostring(r.text or ''), 80), muted = true,
                          selectable = false, icon = r.host and 'star' or 'person', separator = i == #rows }
  end
  show('Chat', items)
end

-- -- «Emitir en directo» ---------------------------------------------------------------------------

local function live_status_text(run)
  if type(run) ~= 'table' then return '' end
  if run.status == 'connecting' then return 'Conectando con el servidor…' end
  if run.status == 'live' then
    local s = math.floor(run.seconds or 0)
    local where = run.mode == 'vaapi' and 'tarjeta gráfica' or 'procesador'
    return string.format('En directo · %d:%02d:%02d · %d kb/s · %s', math.floor(s / 3600), math.floor(s % 3600 / 60),
                         s % 60, math.floor(run.kbps or 0), where)
  end
  if run.status == 'failed' then return 'Se cortó: ' .. (run.error or '') end
  if run.status == 'done' then return 'Terminó: se acabó el vídeo' end
  if run.status == 'stopped' then return 'Parada' end
  return ''
end

live_hint = function()
  local lv = state.live
  if type(lv) ~= 'table' then return '' end
  if lv.active then return 'en directo' end
  if not lv.configured then return 'sin configurar' end
  return lv.service ~= '' and lv.service or (lv.server_host or '')
end

local function refresh_live(cb)
  rpc.call('live.status', nil, function(err, st)
    if not err then state.live = st; publish() end
    if cb then cb(not err) end
  end)
end

local function live_tick()
  if state.view ~= 'live' or uosc.open_type() ~= MENU then
    if live_timer then live_timer:kill(); live_timer = nil end
    return
  end
  refresh_live(function(ok) if ok and state.view == 'live' then reopen_current() end end)
end

views.live = function()
  local lv = state.live or {}
  local run = type(lv.run) == 'table' and lv.run or nil
  local items = {
    { title = 'Emite solo lo que tengas derecho a compartir', icon = 'gavel', muted = true, selectable = false,
      hint = 'tuyo, libre o con permiso' },
    { title = 'Películas, series, fútbol o canales de TV: normalmente no', icon = 'block', muted = true,
      selectable = false },
  }
  if lv.ffmpeg == false then
    items[#items + 1] = { title = 'ffmpeg no está instalado', icon = 'error', muted = true, selectable = false }
  end
  if lv.active then
    items[#items + 1] = { title = live_status_text(run), icon = 'sensors', muted = true, selectable = false,
                          separator = true }
    items[#items + 1] = { title = 'Parar la emisión', icon = 'stop_circle', value = { live = 'stop' } }
  elseif lv.configured then
    items[#items + 1] = { title = 'Emitir lo que estoy viendo', hint = 'desde este punto', icon = 'sensors',
                          value = { live = 'start' }, separator = true }
    items[#items + 1] = { title = 'Emitir desde el principio', icon = 'replay',
                          value = { live = 'start', from_start = true } }
    if run and run.status == 'failed' then
      items[#items + 1] = { title = live_status_text(run), icon = 'error', muted = true, selectable = false }
    end
  else
    items[#items + 1] = { title = 'Para emitir, elige el servidor y pega tu clave de emisión', icon = 'info',
                          muted = true, selectable = false, separator = true }
  end
  items[#items + 1] = { title = 'Servidor', icon = 'dns', value = { view = 'live_server' }, separator = true,
                        hint = lv.has_server and (lv.server_host or '') or 'sin elegir' }
  items[#items + 1] = { title = 'Pegar la clave de emisión', icon = 'key', value = { live = 'key' },
                        hint = lv.has_key and string.format('guardada (%d caracteres)', lv.key_length or 0)
                          or 'cópiala antes · no se muestra' }
  if lv.has_key then
    items[#items + 1] = { title = 'Olvidar la clave', icon = 'key_off', value = { live = 'forget' } }
  end
  show('Emitir en directo', items)
  if lv.active and not live_timer then live_timer = mp.add_periodic_timer(2, live_tick) end
end

views.live_server = function()
  local lv = state.live or {}
  local items = {}
  for _, p in ipairs(lv.presets or {}) do
    items[#items + 1] = { title = p.name, hint = p.server, icon = 'dns', value = { live = 'preset', preset = p.id } }
  end
  items[#items + 1] = { title = 'Otro servidor (PeerTube, Owncast…)', hint = 'escribir la dirección rtmp://',
                        icon = 'edit', value = { action = 'server-write' } }
  show('Servidor', items)
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

-- H42/A4: por mu.clip, y con el respaldo de wl-copy/xclip que antes solo tenía mu-iptv
local function copy_text(text, what)
  clip.copy(text, function(ok)
    state.copied = ok and text or ''
    publish()
    osd(clip.notice(ok, text, what))
  end)
end

local function create_room(then_menu, mode)
  if not rpc.connected() then osd('Compartir: mpvd no está conectado'); return end
  local params = { ttl_hours = opts.ttl_hours, internet = P:get('internet') == true }
  if mode == 'public' then params.mode = 'public'; params.max_viewers = opts.max_viewers end
  rpc.call('share.create', params, function(err, res)
    if err then fail(err, 'no se pudo crear la sala'); return end
    state.last_error = ''
    state.status = res.status or state.status
    publish()
    -- H51 · ya no sale el QR por su cuenta. Lo normal es compartir con quien NO está delante, así que lo que hace
    -- falta es el enlace, y en la mano: se copia solo. El QR sigue estando en el menú y en alt+Q.
    local st = res.status or {}
    if st.tunnel_state == 'starting' or st.tunnel_state == 'warming' then
      -- y NO se copia todavía: cloudflared da la dirección mucho antes de que enrute (medido, unos 60 s de más),
      -- así que copiarla ahora sería darte un enlace muerto. Se copia sola en cuanto conteste.
      osd('Sala abierta · abriendo la puerta a internet, suele tardar un minuto. Te aviso y te copio el enlace', 6)
    else
      copy_text(res.url, 'el enlace de la sala')
    end
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

-- text box (a uosc palette whose query is the text): a chat message or the address of another server
local INPUT_TITLES = { chat = 'Mensaje para los invitados', server = 'Dirección del servidor (rtmp:// o rtmps://)',
                       join = 'Enlace de la sala' }

local function input_menu(query)
  state.input.query = query or ''
  local mode = state.input.mode
  local items
  if query ~= '' then
    local verb = (mode == 'chat' and 'Enviar: ') or (mode == 'join' and 'Entrar: ') or 'Usar: '
    items = { { title = verb .. query, icon = 'check', value = { save = query } } }
  else
    local empty = 'Escribe o pega la dirección (por ejemplo rtmp://mi-servidor/live)'
    if mode == 'chat' then empty = 'Escribe el mensaje y pulsa Enter'
    elseif mode == 'join' then empty = 'Pega aquí el enlace que te han pasado (termina en #k=…)' end
    items = { { title = empty, icon = 'edit', selectable = false, muted = true } }
  end
  return { type = INPUT, title = INPUT_TITLES[mode], items = items, callback = { SCRIPT, INPUT_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    search_suggestion = query, footnote = 'Enter elige · ⌫ en vacío vuelve' }
end

local function open_input(mode)
  state.input = { mode = mode, query = '' }
  publish()
  uosc.open(input_menu(''))
end

local function reopen_forced()
  local spec = state.stack[#state.stack]
  if not spec then return end
  state.force_open = true
  open_view(spec, false)
end

-- ---------------------------------------------------------------------------------------------
-- H44/C6 · entrar en la sala de OTRO. Lo pesado (entrar, el canal de eventos y seguir al anfitrión) lo hace mpvd:
-- aquí solo se pide el enlace, se enseña en qué estado está y se sale.

local function join_room(url)
  url = tostring(url or ''):gsub('^%s+', ''):gsub('%s+$', '')
  if url == '' then osd('No hay ningún enlace'); reopen_forced(); return end
  osd('Entrando en la sala…')
  rpc.call('share.join', { url = url }, function(err, res)
    if err then fail(err, 'entrar en la sala'); reopen_forced(); return end
    osd('Ya estás en la sala' .. ((res and res.room and res.room.host) and (' de ' .. res.room.host) or ''))
    refresh_status(reopen_forced)
  end, 30)
end

local function join_from_clipboard()
  local url = clip.first_line()
  if url == '' then osd('El portapapeles está vacío'); reopen_current(); return end
  join_room(url)
end

local function leave_room()
  rpc.call('share.leave', {}, function(err)
    if err then fail(err, 'salir de la sala'); reopen_current(); return end
    osd('Has salido de la sala')
    refresh_status(reopen_forced)
  end)
end

local function input_done(mode, text)
  if mode == 'chat' then
    rpc.call('share.chat', { text = text }, function(err)
      if err then fail(err, 'chat') end
      refresh_status(reopen_forced)
    end)
  elseif mode == 'join' then
    join_room(text)
  elseif mode == 'server' then
    rpc.call('live.configure', { server = text }, function(err, st)
      if err then fail(err, 'servidor'); reopen_forced(); return end
      state.live = st
      osd('Servidor guardado: ' .. (st.server_host or ''))
      publish()
      if (state.stack[#state.stack] or {}).name == 'live_server' then table.remove(state.stack) end
      reopen_forced()
    end)
  end
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if not state.input then return end
  if ev.type == 'search' then
    uosc.update(input_menu(ev.query or ''))
    return
  end
  local chosen = ev.type == 'activate' and type(ev.value) == 'table' and ev.value.save or nil
  if ev.type ~= 'back' and ev.type ~= 'close' and chosen == nil then return end
  local mode = state.input.mode
  state.input = nil
  publish()
  if ev.type == 'close' then return end  -- Esc: nothing sent, nothing reopened
  if uosc.open_type() == INPUT then uosc.close(INPUT) end
  if chosen ~= nil and chosen ~= '' then
    input_done(mode, chosen)
  else
    reopen_forced()
  end
end)

local function live_call(method, params, what, done_text, cb)
  rpc.call(method, params, function(err, st)
    if err then fail(err, what); reopen_current(); return end
    state.live = st
    if done_text then osd(done_text) end
    publish()
    if cb then cb(st) end
    reopen_current()
  end, 60)
end

local function live_action(v)
  if v.live == 'start' then
    osd('Preparando la emisión…')
    live_call('live.start', { from_start = v.from_start == true }, 'emitir en directo', nil, function()
      if not live_timer then live_timer = mp.add_periodic_timer(2, live_tick) end
    end)
  elseif v.live == 'stop' then
    live_call('live.stop', nil, 'parar la emisión', 'Emisión parada')
  elseif v.live == 'key' then
    -- mpvd reads the key from the clipboard itself: it never passes through this script nor the OSD
    live_call('live.configure', { key_from = 'clipboard' }, 'clave de emisión', nil, function(st)
      osd(string.format('Clave guardada (%d caracteres)', st.key_length or 0))
    end)
  elseif v.live == 'forget' then
    live_call('live.configure', { key = '' }, 'olvidar la clave', 'Clave olvidada')
  elseif v.live == 'preset' then
    live_call('live.configure', { preset = v.preset }, 'servidor', nil, function(st)
      osd('Servidor: ' .. (st.server_host or ''))
      if state.view == 'live_server' then table.remove(state.stack); state.view = 'live' end
    end)
  end
end

local function menu_action(v)
  if v.live then
    live_action(v)
  elseif v.action == 'create' then
    uosc.close(MENU)
    create_room(false, v.mode)
  elseif v.action == 'toggle_internet' then
    local on = P:get('internet') ~= true
    P:set('internet', on)
    osd(on and 'Compartir: la próxima sala también se podrá abrir desde internet'
          or 'Compartir: las salas solo se abrirán en tu red')
    reopen_current()
  elseif v.action == 'join-write' then
    open_input('join')
  elseif v.action == 'join-clipboard' then
    uosc.close(MENU)
    join_from_clipboard()
  elseif v.action == 'leave' then
    leave_room()
  elseif v.action == 'chat-write' then
    open_input('chat')
  elseif v.action == 'server-write' then
    open_input('server')
  elseif v.action == 'chat-osd' then
    opts.chat_osd = not opts.chat_osd
    if not opts.chat_osd then chat_clear() end
    publish()
    reopen_current()
  elseif v.action == 'qr' then
    uosc.close(MENU)
    toggle_qr()        -- alterna: si está puesto, lo quita (antes solo lo mostraba y no había forma de sacarlo)
  elseif v.action == 'copy' then
    copy_text(state.status and state.status.url or '')
  elseif v.action == 'copy-player' then
    rpc.call('share.player_link', nil, function(err, res)
      if err then fail(err, 'enlace para otro reproductor'); return end
      copy_text(res.url, 'el enlace para VLC o mpv')
    end, 20)
  elseif v.action == 'copy-fw' then
    local fw = state.status and state.status.firewall
    copy_text(fw and fw.command or '')
  elseif v.action == 'rotate' then
    rpc.call('share.rotate', nil, function(err, res)
      if err then fail(err, 'enlace nuevo'); return end
      state.status = res.status
      if state.qr_visible then show_qr(res, true) end
      osd('Enlace nuevo creado: el anterior ya no vale')
      publish()
      reopen_current()
    end)
  elseif v.action == 'close' then
    rpc.call('share.close', nil, function(err, st)
      if err then fail(err, 'cerrar la sala'); return end
      state.status = st
      hide_qr()
      chat_clear()
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
  if t == MENU or t == INPUT then return end
  reset_timer = mp.add_timeout(0.2, function()
    reset_timer = nil
    local ot = uosc.open_type()
    if ot == MENU or ot == INPUT then return end
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
  if rpc.connected() then
    refresh_live(function(ok) if ok and state.view == 'root' then reopen_current() end end)
  end
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
  if type(ev) ~= 'table' then return end
  if ev.event == 'live' then
    if type(ev.status) == 'table' then state.live = ev.status end
    if ev.text and ev.text ~= '' then state.last_notice = ev.text; osd(ev.text) end
    publish()
    if state.view == 'live' or state.view == 'root' then reopen_current() end
    return
  end
  if ev.event == 'share-guest' then
    if type(ev.status) == 'table' then state.status = ev.status end
    if ev.text and ev.text ~= '' then state.last_notice = ev.text; osd(ev.text) end
    publish()
    if state.view == 'root' or state.view == 'join' then reopen_current() end
    return
  end
  if ev.event ~= 'share' then return end
  if type(ev.status) == 'table' then state.status = ev.status end
  if ev.kind == 'chat' then
    chat_push(ev.chat)
    if state.view == 'chat' then reopen_current() end
    return
  end
  if ev.kind == 'link' then
    if ev.text and ev.text ~= '' then state.last_notice = ev.text; osd(ev.text, 5) end
    publish()
    if state.view == 'root' then reopen_current() end
    return
  end
  if ev.kind == 'link-ready' then
    -- H51 · AHORA sí: la dirección de internet ya contesta de verdad, así que el enlace vale y se copia
    copy_text(ev.url or (state.status or {}).url or '', 'el enlace de la sala')
    state.last_notice = 'Enlace copiado: ya se puede entrar desde internet'
    publish()
    if state.view == 'root' then reopen_current() end
    return
  end
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
    chat_clear()
    osd(ev.text or 'Sala cerrada')
  end
  publish()
  -- the yes/no menu of a request replaces ours: refreshing ours now would close it again
  if ev.kind ~= 'request' and (state.view == 'root' or state.view == 'guests' or state.view == 'guest') then
    reopen_current()
  end
end)

-- H44/C6 · `bin/mpv-uos https://…/s/<sala>#k=<token>` abre el reproductor YA DENTRO de la sala: el lanzador saca el
-- enlace de la lista de cosas que reproducir y lo deja aquí. Hay que esperar a que mpvd conteste (el reproductor
-- arranca antes que el demonio), y hacerlo una sola vez.
if opts.join ~= '' then
  local pending = opts.join
  local function when_ready(_, core)
    if pending == '' or type(core) ~= 'table' or core.mpvd ~= 'connected' then return end
    local url = pending
    pending = ''
    mp.unobserve_property(when_ready)   -- por la función, no por un identificador (manual de mpv)
    join_room(url)
  end
  mp.observe_property('user-data/mu/core', 'native', when_ready)
end

N:binding('share-menu', open_root)
mp.add_key_binding(nil, 'share-qr', toggle_qr)
mp.register_script_message('mu-share-hide', hide_qr)
-- `script-message-to mu_share mu-share-join <enlace>`: la puerta única de mu-ytdl manda aquí lo que resulta ser
-- una invitación, y así el enlace se pega en el mismo sitio que todo lo demás (H42).
mp.register_script_message('mu-share-join', function(url) join_room(url or '') end)
publish()
msg.info('mu-share loaded')
