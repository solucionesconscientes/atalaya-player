-- mu-notes: «Mis notas» (H17, ADR-043). Menu of the notes kept by mpvd (notes.*: one Markdown file per video in
-- <datos>/notas): the notes of this video and of every other one, jump to the minute (Enter), edit or delete (Tab),
-- export next to the video or to a folder (an Obsidian vault) and open `mpv-uos://open?path=…&t=…` links pasted or
-- loaded in mpv. New notes are still taken with alt+b (mu-study). Script name: mu_notes. State: user-data/mu/notes.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')
local nav = require('mu.nav')
local tr = require('mu.i18n').t
local N = nav.new()

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-notes-event'
local MENU = 'mu-notes'
local INPUT = 'mu-notes-input'          -- palette used as a text box (edit a note, choose a folder)
local INPUT_EVENT = 'mu-notes-input-event'
local ROOT_TITLE = 'Mis notas'

local P = prefs.ns('mu-notes', { export_dir = '' })

local state = { view = '', stack = {}, items = {}, key = '', file = nil, last_export = '', last_error = '',
                force_open = false, input = nil }

local function publish()
  mp.set_property_native('user-data/mu/notes', {
    view = state.view, depth = #state.stack, items = state.items, key = state.key, last_export = state.last_export,
    last_error = state.last_error, input = state.input and state.input.mode or '',
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
  s = math.floor(tonumber(s) or 0)
  if s >= 3600 then return string.format('%d:%02d:%02d', s / 3600, (s % 3600) / 60, s % 60) end
  return string.format('%d:%02d', s / 60, s % 60)
end

local function basename(p) return (tostring(p or ''):match('[^/\\]+$')) or tostring(p or '') end
local function dirname(p) return (tostring(p or ''):match('^(.*)[/\\][^/\\]*$')) or '' end
local function strip_file(p) return (tostring(p or ''):gsub('^file://', '')) end
local function current_path() return strip_file(mp.get_property('path') or '') end

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 300 then break end
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
  -- H63 · una respuesta tardía NO puede devolverte a una vista de la que acabas de salir: medido en el log, al
  -- pulsar ⌫ en la biblioteca se llegaba al menú principal y 40 ms después la biblioteca se reabría encima. Con la
  -- pila vacía ya no estamos en ninguna vista, así que no se abre nada; y para actualizar vale `mine`, que cuenta
  -- el menú pedido y aún no confirmado (uosc atiende el open y el update en ese orden).
  if uosc.mine(MENU) and not state.force_open then
    uosc.update(base_menu(title, items, extra))
  elseif #state.stack > 0 then
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

local function require_mpvd(title)
  if rpc.connected() then return true end
  show(title, uosc.message_items(tr('mpvd no está conectado'), 'error'))
  return false
end

views.root = function()
  if not require_mpvd(ROOT_TITLE) then return end
  show(ROOT_TITLE, uosc.loading_items())
  rpc.call('notes.list', nil, function(err, files)
    if state.view ~= 'root' then return end
    if err then show(ROOT_TITLE, uosc.message_items(fail(err, 'notes.list'), 'error')) return end
    local items = {}
    local here = current_path()
    if here ~= '' then
      table.insert(items, { title = tr('Notas de este vídeo'), icon = 'movie', value = { view = 'file', path = here } })
    end
    table.insert(items, { title = tr('Nueva nota en este minuto…'), hint = 'alt+b', icon = 'edit_note',
                          value = { cmd = { 'script-binding', 'mu_study/note' } }, separator = true })
    for _, f in ipairs(files or {}) do
      table.insert(items, { title = f.title ~= '' and f.title or basename(f.path), icon = 'description',
        hint = f.notes == 1 and '1 nota' or (tostring(f.notes) .. ' notas'), value = { view = 'file', key = f.key } })
    end
    if #(files or {}) == 0 then
      table.insert(items, { title = tr('Todavía no hay notas: alt+b guarda una en el minuto actual'), icon = 'info',
                            selectable = false, muted = true })
    else
      items[#items].separator = true
      table.insert(items, { title = tr('Abrir la carpeta de notas'), icon = 'folder_open',
                            value = { open_dir = dirname(files[1].file) } })
    end
    show(ROOT_TITLE, items, { footnote = tr('Enter abre · ⌫ atrás') })
  end, 15)
end

views.file = function(args)
  local title = args.title or 'Notas'
  if not require_mpvd(title) then return end
  show(title, uosc.loading_items())
  local params = args.key and { key = args.key } or { path = args.path }
  rpc.call('notes.get', params, function(err, f)
    if state.view ~= 'file' then return end
    if err then show(title, uosc.message_items(fail(err, 'notes.get'), 'error')) return end
    state.key = f.key or ''
    state.file = f
    args.key = f.key
    if f.title and f.title ~= '' then args.title = f.title; title = f.title end
    local items = {}
    for _, n in ipairs(f.items or {}) do
      table.insert(items, { title = n.text, hint = n.time and hms(n.time) or n.stamp,
        icon = n.time and 'schedule' or 'notes',
        value = { note = n.index, time = n.time },
        actions = { { name = 'edit', icon = 'edit', label = tr('Editar') },
                    { name = 'delete', icon = 'delete', label = tr('Borrar') } } })
    end
    if #items == 0 then
      items = { { title = tr('Sin notas: alt+b guarda una en el minuto actual'), icon = 'info',
                  selectable = false, muted = true } }
    else
      items[#items].separator = true
      local playing_it = f.path ~= '' and strip_file(f.path) == current_path()
      if not playing_it and f.path ~= '' then
        table.insert(items, { title = tr('Abrir el vídeo'), icon = 'play_arrow', hint = basename(f.path),
                              value = { play = f.path } })
      end
      local next_to = f.path:match('^%a[%w+.-]*://') and 'solo archivos locales'
        or (basename(f.path):gsub('%.[^.]*$', '') .. '.notas.md')
      table.insert(items, { title = tr('Exportar junto al vídeo'), icon = 'save_alt', value = { export = 'video' },
                            hint = next_to })
      local dir = P:get('export_dir')
      table.insert(items, { title = tr('Exportar a una carpeta (Obsidian)…'), icon = 'drive_folder_upload',
                            hint = dir ~= '' and dir or nil, value = { export = 'folder' } })
    end
    show(title, items, { footnote = tr('Enter salta al minuto · Tab: editar / borrar · ⌫ atrás') })
  end, 15)
end

-- ---------------------------------------------------------------------------------------------
-- H52 · las notas, en la línea de tiempo
--
-- Una nota lleva su segundo desde siempre, pero solo se veía dentro de un menú. Ahora cada una es una marca que
-- mu-marks pone en `chapter-list`, que es lo que uosc dibuja: un rombo con su texto al pasar por encima. mu-marks
-- es el único que escribe esa propiedad (dos scripts escribiéndola se pisan sin enterarse).

local marks_count = 0
local refresh_marks   -- se define abajo; declarada aquí porque LuaJIT no mira hacia adelante

local function set_note_button()
  if not uosc.available() then return end
  uosc.set_button('mu-note', {
    icon = 'edit_note', badge = marks_count > 0 and tostring(marks_count) or nil,
    tooltip = marks_count > 0 and string.format('Notas: %d · anotar este minuto (alt+n)', marks_count)
      or 'Anotar este minuto (alt+n)',
    command = { 'script-binding', SCRIPT .. '/notes-add' },
  })
end

refresh_marks = function()
  local path = current_path()
  if path == '' or not rpc.connected() then
    marks_count = 0
    mp.commandv('script-message-to', 'mu_marks', 'mu-marks-clear', 'notes')
    set_note_button()
    return
  end
  rpc.call('notes.get', { path = path }, function(err, f)
    local marks = {}
    if not err and type(f) == 'table' then
      for _, n in ipairs(f.items or {}) do
        if type(n.time) == 'number' then
          marks[#marks + 1] = { time = n.time, title = tr('nota · %s'):format(tostring(n.text or '')) }
        end
      end
    end
    marks_count = #marks
    mp.commandv('script-message-to', 'mu_marks', 'mu-marks-set', 'notes',
                utils.format_json({ mode = 'add', marks = marks }))
    set_note_button()
  end, 15)
end

-- ---------------------------------------------------------------------------------------------
-- text box (edit a note / choose the export folder): a uosc palette whose query is the text

local function input_menu(query)
  local inp = state.input
  query = query or ''
  inp.query = query
  local items = {}
  local verbo = (inp.mode == 'edit' and 'Guardar: ') or (inp.mode == 'add' and 'Anotar: ') or 'Exportar a: '
  local vacio = (inp.mode == 'edit' and 'Escribe el nuevo texto')
    or (inp.mode == 'add' and 'Escribe la nota: se guarda con el minuto en el que estás')
    or 'Escribe o pega la carpeta'
  if query ~= '' then
    table.insert(items, { title = verbo .. query, icon = 'check', value = { save = query } })
  else
    table.insert(items, { title = vacio, icon = 'edit', selectable = false, muted = true })
  end
  local titulo = (inp.mode == 'edit' and 'Editar la nota')
    or (inp.mode == 'add' and ('Nota en ' .. hms(mp.get_property_number('time-pos') or 0)))
    or 'Carpeta de destino'
  return { type = INPUT, title = titulo, items = items,
    callback = { SCRIPT, INPUT_EVENT }, search_style = 'palette', search_debounce = 0, on_search = 'callback',
    on_close = 'callback', search_suggestion = query, footnote = tr('Enter guarda · ⌫ en vacío vuelve') }
end

local function open_input(mode, text, extra)
  state.input = { mode = mode, query = text or '' }
  for k, v in pairs(extra or {}) do state.input[k] = v end
  publish()
  uosc.open(input_menu(text or ''))
end

local function close_input(back)
  state.input = nil
  publish()
  uosc.close(INPUT)
  if back then reopen_current(true) end
end

local function export(folder)
  if state.key == '' then return end
  rpc.call('notes.export', { key = state.key, folder = folder }, function(err, res)
    if err then osd(tr('Exportar: %s'):format(fail(err, 'notes.export')), 5) return end
    state.last_export = res.file
    publish()
    osd(tr('📝 Notas exportadas: %s'):format(res.file), 5)
  end, 15)
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local inp = state.input
  if not inp then return end
  if ev.type == 'search' then
    uosc.update(input_menu(ev.query or ''))
  elseif ev.type == 'back' then
    close_input(true)
  elseif ev.type == 'activate' and type(ev.value) == 'table' and ev.value.save then
    local text = ev.value.save
    if inp.mode == 'add' then
      -- H52 · una nota en el segundo en el que estás, que es como se piden: «esto de aquí»
      rpc.call('notes.add', { text = text, path = current_path(), time_pos = inp.at,
        title = mp.get_property('media-title') }, function(err)
        if err then osd(tr('Nota: %s'):format(fail(err, 'notes.add')))
        else osd(tr('📝 Nota en %s'):format(hms(inp.at or 0))) end
        state.input = nil
        publish()
        uosc.close(INPUT)
        refresh_marks()
      end, 15)
    elseif inp.mode == 'edit' then
      rpc.call('notes.edit', { key = state.key, index = inp.index, text = text }, function(err)
        if err then osd(tr('Editar: %s'):format(fail(err, 'notes.edit'))) end
        close_input(true)
        refresh_marks()
      end, 15)
    else
      local folder = mp.command_native({ 'expand-path', text }) or text
      P:set('export_dir', text)
      close_input(true)
      export(folder)
    end
  end
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

local function load_at(path, t)
  if t then
    mp.commandv('loadfile', path, 'replace', '-1', 'start=' .. string.format('%.1f', t))
  else
    mp.commandv('loadfile', path, 'replace')
  end
end

local function jump(path, t)
  path = strip_file(path or '')
  if path ~= '' and path ~= current_path() then
    uosc.close(MENU)
    load_at(path, t)
    osd('▶ ' .. basename(path) .. (t and (' · ' .. hms(t)) or ''))
    return
  end
  if t then
    mp.commandv('seek', tostring(t), 'absolute')
    osd('⏱ ' .. hms(t))
  end
end

local function open_folder(dir)
  if dir == '' then return end
  local opener = ({ windows = 'explorer', darwin = 'open' })[mp.get_property('platform') or ''] or 'xdg-open'
  mp.command_native_async({ name = 'subprocess', args = { opener, dir }, playback_only = false, detach = true },
                          function() end)
  osd('📂 ' .. dir)
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.note ~= nil then
      if ev.action == 'edit' then
        local n = state.file and state.file.items and state.file.items[v.note + 1]
        open_input('edit', n and n.text or '', { index = v.note })
      elseif ev.action == 'delete' then
        rpc.call('notes.delete', { key = state.key, index = v.note }, function(err, f)
          if err then osd(tr('Borrar: %s'):format(fail(err, 'notes.delete'))) return end
          osd(tr('🗑 Nota borrada'))
          if f and (f.notes or 0) == 0 then
            table.remove(state.stack)
            if #state.stack == 0 then uosc.close(MENU) return end
          end
          reopen_current()
        end, 15)
      else
        jump(state.file and state.file.path, v.time)
      end
    elseif v.play then
      jump(v.play, nil)
    elseif v.export == 'video' then
      export(nil)
    elseif v.export == 'folder' then
      local dir = P:get('export_dir')
      open_input('folder', dir ~= '' and dir or '~/Documentos/Notas')
    elseif v.open_dir then
      open_folder(v.open_dir)
    elseif v.cmd then
      uosc.close(MENU)
      mp.command_native(v.cmd)
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
    -- H63/N2 · ni si estamos esperando a mpvd: al pulsar una fila, uosc cierra el menú y nosotros lo reabrimos
    -- cuando llega la respuesta. Esperar un plazo era una carrera que se pierde con el equipo cargado; lo que
    -- hay que mirar es si queda algo en vuelo.
    if rpc.pending() > 0 then return end
    if #state.stack > 0 or state.view ~= '' or state.input then
      state.stack, state.view, state.input = {}, '', nil
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- mpv-uos:// links loaded inside mpv (pasted with ctrl+v, a playlist…): open the target at that minute.
-- bin/mpv-uos resolves the links given on the command line (the desktop entry) before mpv starts.

local function urldecode(s)
  return (s:gsub('+', ' '):gsub('%%(%x%x)', function(h) return string.char(tonumber(h, 16)) end))
end

local function parse_link(url)
  if not url:match('^mpv%-uos://open%?') then return nil end
  local q = url:match('%?(.*)$') or ''
  local out = {}
  for k, v in q:gmatch('([^&=]+)=([^&]*)') do out[k] = urldecode(v) end
  if not out.path or out.path == '' then return nil end
  return out.path, tonumber(out.t)
end

mp.add_hook('on_load', 5, function()
  local url = mp.get_property('stream-open-filename') or ''
  local path, t = parse_link(url)
  if not path then return end
  msg.info('mpv-uos link → ' .. path .. (t and (' @ ' .. t) or ''))
  load_at(path, t)
end)

-- ---------------------------------------------------------------------------------------------
-- bindings

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

local function open_here()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'file', args = { path = current_path() } })
end

-- H52 · anotar el minuto en el que estás, de una tecla o del botón de la barra
local function add_here()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  if not rpc.connected() then osd(tr('mpvd no está conectado')) return end
  if current_path() == '' then osd(tr('No hay nada en reproducción que anotar')) return end
  open_input('add', '', { at = mp.get_property_number('time-pos') or 0 })
end

mp.add_key_binding(nil, 'notes-add', add_here)
mp.register_script_message('mu-notes-refresh', function() refresh_marks() end)
mp.register_event('file-loaded', function() refresh_marks() end)

N:binding('notes-menu', open_root)
N:binding('notes-here', open_here)
mp.register_script_message('mu-notes-open', open_root)

publish()
set_note_button()   -- que el botón exista desde el principio: uosc muestra un hueco vacío si no
msg.info('mu-notes loaded')
