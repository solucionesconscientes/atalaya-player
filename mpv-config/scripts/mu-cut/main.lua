-- mu-cut (H52): choose pieces of what you are watching, see them on the timeline, loop them and save them.
--
-- Two buttons in the control bar, because this is the kind of thing you press with the film running and going
-- through a menu breaks the moment (docs/INTERFAZ.md):
--
--   mu-cut   ✂  press once «from here», press again «to here»; the badge counts what you have chosen
--   mu-loop  ⟳  repeats what you have chosen (and, with nothing chosen, is mpv's plain A-B loop)
--
-- While you are choosing, the in/out are mpv's own `ab-loop-a`/`ab-loop-b`, so uosc already draws A and B on the
-- timeline with no help from us. Once a piece is closed it becomes two marks through mu-marks, which is the only
-- script that writes `chapter-list`: uosc paints from the first to the second (`chapter_range_patterns=tramo:…`).
-- Saving is mpvd's job (`convert.cut`), which already knows about presets, queue, progress and names.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local prefs = require('mu.prefs')
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-cut'
local EVENT = 'mu-cut-event'
local ROOT_TITLE = 'Tramos'

local opts = { osd_seconds = 3 }
options.read_options(opts, 'mu-cut')

-- what you save them as; remembered, like the recording format (H43)
local P = prefs.ns('mu-cut', { preset = 'mp4' })

local FORMATS = {
  { id = 'mp4', title = 'Vídeo · MP4', hint = 'se abre en cualquier sitio' },
  { id = 'small', title = 'Vídeo · más pequeño (H.265)', hint = 'ocupa la mitad, tarda más' },
  { id = 'web', title = 'Vídeo · WebM', hint = 'para páginas web' },
  { id = 'm4a', title = 'Solo audio · M4A', hint = 'móviles y Apple' },
  { id = 'mp3', title = 'Solo audio · MP3', hint = 'el más compatible' },
  { id = 'opus', title = 'Solo audio · Opus', hint = 'el más pequeño' },
  { id = 'flac', title = 'Solo audio · FLAC', hint = 'sin pérdida' },
}

local state = { segments = {}, pending = nil, view = '', stack = {}, items = {}, last_error = '', saving = false }

local function osd(text, secs) mp.osd_message(text, secs or opts.osd_seconds) end

local function clock(s)
  s = math.max(0, math.floor(tonumber(s) or 0))
  local h, m, sec = math.floor(s / 3600), math.floor((s % 3600) / 60), s % 60
  if h > 0 then return string.format('%d:%02d:%02d', h, m, sec) end
  return string.format('%d:%02d', m, sec)
end

local function format_label()
  for _, f in ipairs(FORMATS) do if f.id == P:get('preset') then return f.title end end
  return 'Vídeo · MP4'
end

local function total_seconds()
  local t = 0
  for _, s in ipairs(state.segments) do t = t + (s.b - s.a) end
  return t
end

-- H54 · los elegidos, que son los que se guardan. Al marcar un tramo entra elegido; se quitan uno a uno con Enter
-- sobre su fila. Antes era todo o nada: no había forma de exportar unos sí y otros no.
local function chosen()
  local out = {}
  for i, s in ipairs(state.segments) do
    if s.on ~= false then out[#out + 1] = { i = i, a = s.a, b = s.b } end
  end
  return out
end

local function chosen_seconds()
  local t = 0
  for _, s in ipairs(chosen()) do t = t + (s.b - s.a) end
  return t
end

local function publish()
  local segs = {}
  for i, s in ipairs(state.segments) do segs[i] = { a = s.a, b = s.b, on = s.on ~= false } end
  mp.set_property_native('user-data/mu/cut', {
    segments = segs, count = #segs, chosen = #chosen(), pending = state.pending or -1, total = total_seconds(),
    chosen_total = chosen_seconds(),
    preset = P:get('preset'), view = state.view, items = state.items, last_error = state.last_error,
    saving = state.saving,
  })
end

-- the timeline: a mark where each piece starts and another where it ends, so uosc paints the range between them
local function send_marks()
  local marks = {}
  for i, s in ipairs(state.segments) do
    marks[#marks + 1] = { time = s.a, title = string.format('tramo %d · %s-%s', i, clock(s.a), clock(s.b)) }
    marks[#marks + 1] = { time = s.b, title = string.format('fin del tramo %d', i) }
  end
  mp.commandv('script-message-to', 'mu_marks', 'mu-marks-set', 'cut',
              utils.format_json({ mode = 'add', marks = marks }))
end

local function set_buttons()
  if not uosc.available() then return end
  local n = #state.segments
  local pend = state.pending ~= nil
  uosc.set_button('mu-cut', {
    icon = pend and 'content_cut' or 'cut',
    active = pend, badge = n > 0 and tostring(n) or nil,
    tooltip = pend and ('Hasta aquí (empezó en ' .. clock(state.pending) .. ') · alt+x')
      or (n > 0 and string.format('Tramos: %d (%s) · alt+x marca otro', n, clock(total_seconds())))
      or 'Elegir un tramo desde aquí (alt+x)',
    command = { 'script-binding', SCRIPT .. '/cut-mark' },
  })
  local a = mp.get_property_number('ab-loop-a')
  local b = mp.get_property_number('ab-loop-b')
  local looping = a ~= nil and b ~= nil
  uosc.set_button('mu-loop', {
    icon = looping and 'repeat_on' or 'repeat',
    active = looping,
    tooltip = looping and 'Dejar de repetir (l)' or (n > 0 and 'Repetir el último tramo (l)' or 'Repetir un trozo (l)'),
    command = { 'script-binding', SCRIPT .. '/cut-loop' },
  })
end

local function refresh()
  send_marks()
  set_buttons()
  publish()
end

-- -------------------------------------------------------------------------------------------------
-- choosing

local function duration() return mp.get_property_number('duration') or 0 end
local function now() return mp.get_property_number('time-pos') or 0 end

local function clear_ab()
  mp.set_property('ab-loop-a', 'no')
  mp.set_property('ab-loop-b', 'no')
end

local function mark()
  if duration() <= 0 then osd('Esto no tiene duración: no se puede trocear'); return end
  local t = now()
  if state.pending == nil then
    state.pending = t
    mp.set_property_number('ab-loop-a', t)   -- uosc dibuja la A sola
    mp.set_property('ab-loop-b', 'no')
    osd('Tramo desde ' .. clock(t) .. ' · pulsa otra vez donde quieras acabarlo')
    refresh()
    return
  end
  local a, b = state.pending, t
  if b < a then a, b = b, a end
  state.pending = nil
  clear_ab()
  if b - a < 0.2 then
    osd('Tramo descartado: no llega a durar nada')
    refresh()
    return
  end
  state.segments[#state.segments + 1] = { a = a, b = b, on = true }
  table.sort(state.segments, function(x, y) return x.a < y.a end)
  osd(string.format('Tramo %d: %s → %s (%s)', #state.segments, clock(a), clock(b), clock(b - a)))
  refresh()
end

local function loop_toggle()
  local a = mp.get_property_number('ab-loop-a')
  local b = mp.get_property_number('ab-loop-b')
  if a ~= nil and b ~= nil then
    clear_ab()
    osd('Ya no se repite')
  elseif #state.segments > 0 and state.pending == nil then
    local s = state.segments[#state.segments]
    mp.set_property_number('ab-loop-a', s.a)
    mp.set_property_number('ab-loop-b', s.b)
    mp.commandv('seek', s.a, 'absolute+exact')
    osd(string.format('Repitiendo %s → %s', clock(s.a), clock(s.b)))
  else
    mp.commandv('ab-loop')   -- sin nada elegido, el bucle de siempre de mpv
  end
  set_buttons()
  publish()
end

local function drop(i)
  table.remove(state.segments, i)
  refresh()
end

local function clear_all()
  state.segments = {}
  state.pending = nil
  clear_ab()
  refresh()
end

-- -------------------------------------------------------------------------------------------------
-- menu

local function remember(items)
  local out = {}
  for _, it in ipairs(items or {}) do
    out[#out + 1] = { title = it.title or '', hint = it.hint or '', value = it.value or '' }
  end
  state.items = out
end

local function show(title, items)
  remember(items)
  publish()
  local menu = { type = MENU, title = title, items = items, callback = { SCRIPT, EVENT }, on_close = 'callback',
                 keep_open = true, search_submenus = false }
  if uosc.open_type() == MENU then uosc.update(N:frame(menu, state.stack))
  else uosc.open(N:frame(menu, state.stack)) end
end

local views = {}

local function open_view(spec, push)
  if push ~= false then table.insert(state.stack, spec) end
  state.view = spec.name
  publish()
  views[spec.name](spec.args or {})
end

local function reopen() local s = state.stack[#state.stack]; if s then open_view(s, false) end end

local function current_path()
  local p = mp.get_property('path') or ''
  if p == '' or p:match('^%a[%w+.-]*://') then return nil end
  return mp.command_native({ 'expand-path', p })
end

local function save(joined)
  local path = current_path()
  if not path then osd('Solo se pueden guardar tramos de un archivo de tu equipo'); return end
  local elegidos = chosen()
  if #elegidos == 0 then osd('No has elegido ningún tramo'); return end
  if not rpc.connected() then osd('mpvd no está conectado'); return end
  local segs = {}
  for _, s in ipairs(elegidos) do segs[#segs + 1] = { start = s.a, ['end'] = s.b } end
  state.saving = true
  publish()
  osd(joined and 'Uniendo los tramos…' or 'Guardando los tramos…')
  rpc.call('convert.cut', { path = path, segments = segs, preset = P:get('preset'), joined = joined },
    function(err, res)
      state.saving = false
      if err then
        state.last_error = err.message or 'error'
        osd('No se pudo guardar: ' .. state.last_error)
        publish()
        return
      end
      local n = (type(res) == 'table' and res.count) or 0
      osd(joined and string.format('Un archivo con %d tramos, en Tareas', #segs)
            or string.format('%d archivo(s) en camino, en Tareas', n))
      publish()
      reopen()
    end, 30)
end

views.root = function()
  local items = {}
  local n = #state.segments
  if duration() <= 0 then
    show(ROOT_TITLE, uosc.message_items('Esto no tiene duración: no se puede trocear', 'info'))
    return
  end
  if state.pending ~= nil then
    items[#items + 1] = { title = 'Cerrar el tramo aquí', icon = 'content_cut',
                          hint = 'empezó en ' .. clock(state.pending), value = { action = 'mark' } }
    items[#items + 1] = { title = 'Olvidar este tramo a medias', icon = 'close', value = { action = 'cancel' } }
  else
    items[#items + 1] = { title = 'Empezar un tramo aquí', icon = 'content_cut', hint = clock(now()) .. ' · alt+x',
                          value = { action = 'mark' } }
  end
  if n > 0 then
    local el = #chosen()
    if el == 0 then
      items[#items + 1] = { title = 'No has elegido ningún tramo', icon = 'info', muted = true,
                            selectable = false, separator = true,
                            hint = 'marca abajo los que quieras guardar' }
    else
      items[#items + 1] = { title = el == 1 and 'Guardar el tramo elegido'
                              or string.format('Guardar los %d elegidos por separado', el),
                            icon = 'content_copy',
                            hint = string.format('%s · %s', format_label(),
                                                 el == 1 and clock(chosen_seconds()) or (el .. ' archivos')),
                            value = { action = 'save' }, separator = true }
      if el > 1 then
        items[#items + 1] = { title = string.format('Guardar los %d elegidos unidos en uno', el), icon = 'merge',
                              hint = string.format('%s · %s', format_label(), clock(chosen_seconds())),
                              value = { action = 'save-joined' } }
      end
    end
    items[#items + 1] = { title = 'Formato', icon = 'tune', hint = format_label(), value = { view = 'format' } }
    -- H54 · una fila por tramo, con casilla: Enter lo elige o lo deja fuera, y los botones de la derecha hacen
    -- lo demás. Antes la fila era un submenú y no había forma de exportar unos sí y otros no.
    for i, s in ipairs(state.segments) do
      local on = s.on ~= false
      items[#items + 1] = {
        title = string.format('%d · %s → %s', i, clock(s.a), clock(s.b)),
        hint = clock(s.b - s.a) .. (on and '' or ' · fuera'),
        icon = on and 'check_box' or 'check_box_outline_blank', active = on, muted = not on,
        separator = i == 1, value = { action = 'toggle', index = i },
        actions = { { name = 'go', icon = 'play_arrow', label = 'Ir ahí' },
                    { name = 'loop', icon = 'repeat', label = 'Repetir este' },
                    { name = 'drop', icon = 'delete', label = 'Quitarlo' } },
      }
    end
    items[#items + 1] = { title = el == n and 'Dejar fuera todos' or 'Elegirlos todos',
                          icon = el == n and 'check_box_outline_blank' or 'check_box',
                          value = { action = 'toggle-all' }, separator = true }
    items[#items + 1] = { title = 'Vaciar la lista', icon = 'delete_sweep', value = { action = 'clear' } }
  else
    items[#items + 1] = { title = 'Marca un principio y un final y el tramo aparece en la línea de tiempo',
                          icon = 'info', muted = true, selectable = false, separator = true }
  end
  show(ROOT_TITLE, items)
end

views.format = function()
  local items = {}
  local cur = P:get('preset')
  for _, f in ipairs(FORMATS) do
    items[#items + 1] = { title = f.title, hint = f.hint, icon = f.id == cur and 'radio_button_checked'
                          or 'radio_button_unchecked', active = f.id == cur, value = { preset = f.id } }
  end
  show('Formato', items)
end

local function act(v)
  if v.action == 'mark' then
    mark()
    reopen()
  elseif v.action == 'cancel' then
    state.pending = nil
    clear_ab()
    refresh()
    reopen()
  elseif v.action == 'save' then
    save(false)
  elseif v.action == 'save-joined' then
    save(true)
  elseif v.action == 'toggle' then
    local seg = state.segments[v.index]
    if seg then seg.on = (seg.on == false) end
    reopen()          -- sin publish aquí: reopen ya publica, y hacerlo antes deja ver filas viejas con datos nuevos
  elseif v.action == 'toggle-all' then
    local todos = #chosen() == #state.segments
    for _, seg in ipairs(state.segments) do seg.on = not todos end
    reopen()
  elseif v.action == 'go' then
    local s = state.segments[v.index]
    if s then mp.commandv('seek', s.a, 'absolute+exact'); uosc.close(MENU) end
  elseif v.action == 'loop' then
    local s = state.segments[v.index]
    if s then
      mp.set_property_number('ab-loop-a', s.a)
      mp.set_property_number('ab-loop-b', s.b)
      mp.commandv('seek', s.a, 'absolute+exact')
      set_buttons()
      uosc.close(MENU)
    end
  elseif v.action == 'drop' then
    drop(v.index)
    reopen()
  elseif v.action == 'clear' then
    clear_all()
    reopen()
  elseif v.preset then
    P:set('preset', v.preset)
    table.remove(state.stack)
    state.view = 'root'
    reopen()
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local kind = nav.classify(ev)
  if kind == 'back' then
    if #state.stack > 1 then
      table.remove(state.stack)
      open_view(state.stack[#state.stack], false)
    elseif not N:to_parent() then
      uosc.close(MENU)
    end
    return
  end
  if kind == 'close' then state.view = ''; state.stack = {}; publish(); return end
  if ev.type ~= 'activate' or type(ev.value) ~= 'table' then return end
  -- los botones de la derecha de una fila llegan como `action`; el Enter de la fila, como su `value`
  if ev.action and ev.value.index then
    act({ action = ev.action, index = ev.value.index })
    return
  end
  if ev.value.view then open_view({ name = ev.value.view }) else act(ev.value) end
end)

local function open_root()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  open_view({ name = 'root' })
end

-- a new file has nothing to do with the pieces of the previous one
mp.register_event('start-file', function()
  state.segments = {}
  state.pending = nil
  state.last_error = ''
  set_buttons()
  publish()
end)

mp.observe_property('ab-loop-a', 'native', function() set_buttons() end)
mp.observe_property('ab-loop-b', 'native', function() set_buttons() end)
mp.observe_property('duration', 'native', function() set_buttons() end)

N:binding('cut-menu', open_root)
mp.add_key_binding(nil, 'cut-mark', mark)
mp.add_key_binding(nil, 'cut-loop', loop_toggle)

set_buttons()
publish()
msg.info('mu-cut loaded')
