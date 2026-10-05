-- mu-convert: «Convertir» (H20, ADR-048). Converts the open file or a whole folder with mpvd (convert.*: ffmpeg presets,
-- VA-API when the machine has it) and shows «Tareas»: downloads and conversions in one live list (tasks.list + the
-- `task` events mpvd pushes here) with cancel / retry / remove / open folder.
--   · «Convertir»: preset → options (resolution, quality, audio bitrate, range from the A-B marks, subtitles…) → start.
--   · «Convertir una carpeta entera…»: a text box for the folder, then preset and options (one task per file).
-- Output folder and the last options are remembered (mu-prefs); default <Vídeos>/MPV-UOS/Convertidos.
-- Bindings: convert-menu, tasks-menu. Script name: mu_convert. State for the tests: user-data/mu/convert.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local prefs = require('mu.prefs')
local nav = require('mu.nav')
local tr = require('mu.i18n').t
local N = nav.new()

local SCRIPT = mp.get_script_name()
local EVENT = 'mu-convert-event'
local MENU = 'mu-convert'
local INPUT = 'mu-convert-input'
local INPUT_EVENT = 'mu-convert-input-event'
local ROOT_TITLE = 'Convertir'
local TASKS_TITLE = 'Tareas'

local opts = {
  panel_hz = 4,           -- max refresh rate of the «Tareas» panel
  notify_done = true,     -- OSD when a conversion finishes
  open_command = '',      -- program that opens a folder ('' = xdg-open / open / explorer); tests use `true`
  speed = 'normal',       -- encoder speed handed to mpvd (tests: fast)
}
options.read_options(opts, 'mu-convert')

-- remembered: output folder, «usar la tarjeta gráfica» and the options of the last conversion
local P = prefs.ns('mu-convert', { dir = '', hw = true, options = {} })

local DEFAULTS = { height = 0, quality = 'normal', subtitles = true, container = 'mp4', audio_bitrate = 0,
                   gif_width = 480, gif_fps = 12 }
local QUALITY_LABEL = { high = 'alta', normal = 'normal', small = 'pequeña' }
local CYCLES = {
  height = { 0, 1080, 720, 480 },
  quality = { 'normal', 'high', 'small' },
  container = { 'mp4', 'mkv' },
  audio_bitrate = { 0, 128, 192, 256, 320, 96 },
  gif_width = { 480, 640, 320 },
  gif_fps = { 12, 15, 10 },
}
local LOSSY_AUDIO = { mp3 = true, m4a = true, opus = true }
local HW_CODEC = { mp4 = 'h264', small = 'hevc' }

local state = {
  view = '', stack = {}, items = {}, force_open = false,
  presets = nil, meta = nil, default_dir = '',
  hw = nil, hw_pending = false,
  tasks = {}, task_order = {},
  job = nil,            -- { preset, source, folder = bool, range = bool } being configured
  input = nil,          -- { mode = 'folder' | 'dir', query }
  last_started = {}, last_error = '', opened = '', last_done = nil,
}

local function copy(t)
  local out = {}
  for k, v in pairs(t or {}) do out[k] = v end
  return out
end

local function current_options()
  local o = copy(DEFAULTS)
  for k, v in pairs(P:get('options') or {}) do if DEFAULTS[k] ~= nil then o[k] = v end end
  return o
end

local function count_active()
  local n = 0
  for _, t in pairs(state.tasks) do
    if t.status == 'queued' or t.status == 'running' then n = n + 1 end
  end
  return n
end

local function compact_tasks()
  local out = {}
  for _, key in ipairs(state.task_order) do
    local t = state.tasks[key]
    if t then
      out[#out + 1] = { type = t.type, kind = t.kind, id = t.id, status = t.status, progress = t.progress, title = t.title,
                        message = t.message }
    end
  end
  return out
end

-- H58 · el indicador de que hay algo en marcha. La queja de Ser («guardar los tramos no hace nada») era falsa:
-- los archivos se creaban, pero no había NINGUNA señal de que estuviera pasando algo. Mientras haya trabajo, un
-- icono en la barra con cuántas tareas van y por dónde va la primera; al pulsarlo, la lista. Sin trabajo, no está.
local function set_tasks_button()
  if not uosc.available() then return end
  local n = count_active()
  local corriendo, pct, titulo = nil, nil, nil
  for _, t in pairs(state.tasks or {}) do
    if t.status == 'running' then
      corriendo = corriendo or t
      pct = t.progress and math.floor(t.progress * 100) or nil
      titulo = t.title
    end
  end
  local tip
  if n == 0 then tip = ''
  elseif corriendo then
    tip = string.format('%s%s · %d en marcha · ver Tareas', (titulo or 'Trabajando'):sub(1, 40),
                        pct and (' ' .. pct .. ' %') or '', n)
  else
    tip = string.format('%d en cola · ver Tareas', n)
  end
  uosc.set_button('mu-tasks', {
    icon = corriendo and 'sync' or 'hourglass_top', hide = n == 0, badge = n > 0 and tostring(n) or nil,
    active = corriendo ~= nil, tooltip = tip,
    command = { 'script-binding', SCRIPT .. '/tasks-menu' },
  })
end

local function publish()
  local job = state.job
  mp.set_property_native('user-data/mu/convert', {
    view = state.view, depth = #state.stack, items = state.items, dir = P:get('dir'), default_dir = state.default_dir,
    preset = job and job.preset or '', source = job and job.source or '', folder = job and job.folder or false,
    range = job and job.range or false, options = current_options(), use_hw = P:get('hw'),
    hw = state.hw or { available = false }, tasks = compact_tasks(), tasks_active = count_active(),
    last_started = state.last_started, last_error = state.last_error, input = state.input and state.input.mode or '',
    opened = state.opened, last_done = state.last_done or { status = '' },
  })
  set_tasks_button()
end

local function osd(text, secs) mp.osd_message(text, secs or 3) end

local function fail(err, what)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  publish()
  return m
end

local function basename(p) return (tostring(p or ''):match('[^/\\]+$')) or tostring(p or '') end
local function dirname(p) return (tostring(p or ''):match('^(.*)[/\\][^/\\]*$')) or '' end
local function strip_file(p) return (tostring(p or ''):gsub('^file://', '')) end

local function hms(s)
  s = math.floor(math.max(0, tonumber(s) or 0))
  if s >= 3600 then return string.format('%d:%02d:%02d', s / 3600, (s % 3600) / 60, s % 60) end
  return string.format('%d:%02d', s / 60, s % 60)
end

-- the file playing, when it is a file of this computer
local function local_file()
  local path = strip_file(mp.get_property('path') or '')
  if path == '' or path:match('^%a[%w+.-]*://') then return nil end
  local info = utils.file_info(path)
  if info and info.is_file then return path end
  return nil
end

local function output_dir()
  local d = P:get('dir')
  if d ~= '' then return mp.command_native({ 'expand-path', d }) or d end
  return state.default_dir
end

local function marks()
  local a, b = mp.get_property_number('ab-loop-a'), mp.get_property_number('ab-loop-b')
  if a and b and b > a then return a, b end
  return nil, nil
end

-- ---------------------------------------------------------------------------------------------
-- menus

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 200 then break end
    local names = {}
    for _, a in ipairs(it.actions or {}) do names[#names + 1] = a.name end
    out[#out + 1] = { title = it.title or '', hint = it.hint or '', icon = it.icon or '', value = it.value or '',
                      actions = names }
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
  local core = mp.get_property_native('user-data/mu/core') or {}
  show(title, {
    { title = tr('mpvd no está disponible'), hint = core.mpvd or '', icon = 'error', selectable = false, muted = true },
  })
  return false
end

local function fetch_hw()
  if state.hw or state.hw_pending or not rpc.connected() then return end
  state.hw_pending = true
  rpc.call('convert.hw', nil, function(err, caps)
    state.hw_pending = false
    state.hw = (not err and type(caps) == 'table') and caps or { available = false, reason = 'error' }
    publish()
    -- the answer may arrive before uosc reports the menu as open: the view (reset when the menu closes) decides
    if state.view == 'root' or state.view == 'options' then reopen_current() end
  end, 20)
end

local function with_presets(cb)
  if state.presets then cb() return end
  rpc.call('convert.presets', nil, function(err, res)
    if err then osd(tr('Convertir: %s'):format(fail(err, 'convert.presets'))) return end
    state.presets, state.meta = res.presets, res
    state.default_dir = res.default_dir or ''
    publish()
    cb()
  end, 10)
end

local function preset(id)
  for _, p in ipairs(state.presets or {}) do if p.id == id then return p end end
  return nil
end

local function hw_hint()
  local hw = state.hw
  if not hw then return 'comprobando…' end
  if not hw.available then return 'no disponible' end
  local codecs = {}
  for _, c in ipairs(hw.codecs or {}) do codecs[#codecs + 1] = c == 'h264' and 'H.264' or (c == 'hevc' and 'H.265' or c) end
  return (P:get('hw') and 'sí' or 'no') .. ' · VA-API ' .. table.concat(codecs, ', ')
end

local function preset_items()
  local items = {}
  for _, p in ipairs(state.presets or {}) do
    items[#items + 1] = { title = p.label, hint = p.hint, value = { preset = p.id },
      icon = p.kind == 'audio' and 'music_note' or (p.kind == 'gif' and 'gif_box' or 'movie') }
  end
  if items[1] then items[#items].separator = true end
  return items
end

views.root = function()
  if not require_mpvd(ROOT_TITLE) then return end
  fetch_hw()
  with_presets(function()
    local items = {}
    local file = local_file()
    if file then
      items[#items + 1] = { title = basename(file), icon = 'description', selectable = false, muted = true }
      for _, it in ipairs(preset_items()) do
        it.value.source = file
        items[#items + 1] = it
      end
    else
      local path = mp.get_property('path') or ''
      items[#items + 1] = { title = path ~= '' and 'Los vídeos de internet se guardan con «Descargar»'
                              or 'Abre un archivo de tu equipo para convertirlo',
                            icon = 'info', selectable = false, muted = true, separator = true }
    end
    items[#items + 1] = { title = tr('Convertir una carpeta entera…'), icon = 'folder_copy', value = { folder_input = true } }
    local n = count_active()
    items[#items + 1] = { title = TASKS_TITLE, icon = 'pending_actions', hint = n > 0 and (n .. ' activas') or nil,
                          value = { view = 'tasks' }, separator = true }
    local dir = P:get('dir')
    items[#items + 1] = { title = tr('Carpeta de salida'), icon = 'folder', hint = dir ~= '' and dir or 'predeterminada',
                          value = { choose_dir = true } }
    items[#items + 1] = { title = tr('Usar la tarjeta gráfica'), icon = 'memory', hint = hw_hint(),
                          value = { toggle_hw = true } }
    show(ROOT_TITLE, items, { footnote = tr('Enter elige · ⌫ atrás · Esc cierra') })
  end)
end

-- a whole folder: its presets
views.folder = function(args)
  with_presets(function()
    local items = { { title = args.folder, icon = 'folder', selectable = false, muted = true } }
    for _, it in ipairs(preset_items()) do
      it.value.source = args.folder
      it.value.folder = true
      items[#items + 1] = it
    end
    show(tr('Carpeta %s'):format(basename(args.folder)), items, { footnote = tr('Cada archivo es una tarea · ⌫ atrás') })
  end)
end

local function opt_label(name, v)
  if name == 'height' then return v == 0 and 'original' or (v .. 'p') end
  if name == 'quality' then return QUALITY_LABEL[v] or v end
  if name == 'container' then return string.upper(v) end
  if name == 'audio_bitrate' then return v == 0 and 'según la calidad' or (v .. ' kbps') end
  if name == 'gif_width' then return v .. ' px' end
  if name == 'gif_fps' then return v .. ' fps' end
  if name == 'subtitles' then return v and 'sí' or 'no' end
  return tostring(v)
end

local function opt_item(title, name, icon, o)
  return { title = title, hint = opt_label(name, o[name]), icon = icon, value = { opt = name } }
end

views.options = function()
  local job = state.job
  local p = preset(job and job.preset or '')
  if not p then reopen_current() return end
  local o = current_options()
  local items = {
    { title = job.folder and 'Convertir toda la carpeta' or 'Convertir ahora', icon = 'play_arrow', bold = true,
      hint = basename(job.source), value = { start = true }, separator = true },
  }
  if p.kind == 'video' then
    items[#items + 1] = opt_item('Resolución máxima', 'height', 'aspect_ratio', o)
  end
  if p.kind ~= 'gif' and p.id ~= 'flac' and p.id ~= 'wav' then
    items[#items + 1] = opt_item('Calidad', 'quality', 'high_quality', o)
  end
  if LOSSY_AUDIO[p.id] then items[#items + 1] = opt_item('Bitrate del audio', 'audio_bitrate', 'graphic_eq', o) end
  if p.id == 'small' then items[#items + 1] = opt_item('Formato del archivo', 'container', 'inventory_2', o) end
  if p.kind == 'video' then items[#items + 1] = opt_item('Conservar los subtítulos', 'subtitles', 'subtitles', o) end
  if p.kind == 'gif' then
    items[#items + 1] = opt_item('Ancho', 'gif_width', 'photo_size_select_large', o)
    items[#items + 1] = opt_item('Fotogramas por segundo', 'gif_fps', 'slow_motion_video', o)
  end
  if HW_CODEC[p.id] then
    local can = state.hw and state.hw.available and state.hw.encode and state.hw.encode[HW_CODEC[p.id]]
    items[#items + 1] = { title = tr('Tarjeta gráfica'), icon = 'memory', value = { toggle_hw = true },
      hint = can and (P:get('hw') and 'sí (VA-API)' or 'no') or 'no disponible para este formato' }
  end
  items[#items].separator = true
  if not job.folder then
    local a, b = marks()
    items[#items + 1] = { title = tr('Solo un tramo'), icon = 'content_cut', value = { opt = 'range' },
      hint = (job.range and a) and (hms(a) .. '–' .. hms(b)) or (a and 'no · hay marcas A-B' or 'todo el archivo') }
    items[#items + 1] = { title = tr('Marcar el inicio aquí'), icon = 'first_page', value = { mark = 'a' },
      hint = mp.get_property_number('ab-loop-a') and hms(mp.get_property_number('ab-loop-a')) or 'sin marcar' }
    items[#items + 1] = { title = tr('Marcar el final aquí'), icon = 'last_page', value = { mark = 'b' },
      hint = mp.get_property_number('ab-loop-b') and hms(mp.get_property_number('ab-loop-b')) or 'sin marcar' }
  end
  show(p.label, items, { footnote = tr('Enter cambia una opción · la primera fila empieza') })
end

-- ---------------------------------------------------------------------------------------------
-- tasks panel (downloads + conversions)

local STATUS_ICON = { queued = 'schedule', done = 'check_circle', failed = 'error', cancelled = 'cancel' }
local ACTION = {
  cancel = { name = 'cancel', icon = 'cancel', label = tr('Cancelar') },
  retry = { name = 'retry', icon = 'refresh', label = tr('Repetir') },
  remove = { name = 'remove', icon = 'delete', label = tr('Quitar de la lista') },
  folder = { name = 'folder', icon = 'folder_open', label = tr('Abrir la carpeta') },
}
local METHODS = {
  download = { cancel = 'ytdl.downloads.cancel', retry = 'ytdl.downloads.retry', remove = 'ytdl.downloads.remove' },
  convert = { cancel = 'convert.cancel', retry = 'convert.retry', remove = 'convert.remove' },
  -- H62 · lo demás que pasa por detrás (subtítulos, índice, traducción, intro, música…) llega como `job`, y lo
  -- único que se puede hacer con un trabajo es pararlo: no hay archivo que reintentar ni carpeta que abrir.
  job = { cancel = 'jobs.cancel' },
}

local function task_key(t) return tostring(t.type) .. ':' .. tostring(t.id) end

local function upsert(t, front)
  local key = task_key(t)
  if not state.tasks[key] then
    if front then table.insert(state.task_order, 1, key) else state.task_order[#state.task_order + 1] = key end
  end
  state.tasks[key] = t
end

local function task_item(t)
  local actions = {}
  for _, a in ipairs(t.actions or {}) do if ACTION[a] then actions[#actions + 1] = ACTION[a] end end
  local icon = STATUS_ICON[t.status]
  if t.status == 'running' then
    icon = (t.type == 'download' and 'downloading') or (t.type == 'job' and 'autorenew') or 'sync'
  end
  local hint = t.message ~= '' and t.message or t.status
  if t.status == 'failed' then hint = tr('error') end
  -- el nombre de la clase de tarea lo manda mpvd cuando lo sabe (`kind`), que es quien conoce los trabajos
  local kind = t.kind
  if kind == nil or kind == '' then kind = t.type == 'download' and 'Descarga' or 'Conversión' end
  return {
    title = (t.title or '?') .. '  ·  ' .. kind .. (t.description ~= '' and (': ' .. t.description) or ''),
    hint = hint, icon = icon or 'help', value = { task = { type = t.type, id = t.id } }, actions = actions,
    muted = t.status == 'cancelled' or nil, bold = t.status == 'running' or nil, keep_open = true,
  }
end

local function tasks_items()
  local items = {}
  for _, key in ipairs(state.task_order) do
    local t = state.tasks[key]
    if t then items[#items + 1] = task_item(t) end
  end
  if #items == 0 then return uosc.message_items(tr('Ahora mismo no se está haciendo nada por detrás'), 'pending_actions') end
  items[#items].separator = true
  items[#items + 1] = { title = tr('Limpiar terminadas'), icon = 'cleaning_services', value = { clear = true },
                        keep_open = true }
  return items
end

local TASKS_FOOT = 'Tab: cancelar · repetir · quitar · abrir carpeta · Enter: detalles · ⌫ atrás'

views.tasks = function()
  if not require_mpvd(TASKS_TITLE) then return end
  rpc.call('tasks.list', nil, function(err, res)
    if err then show(TASKS_TITLE, uosc.message_items(fail(err, 'tasks.list'), 'error')) return end
    state.tasks, state.task_order = {}, {}
    for _, t in ipairs(res.tasks or {}) do upsert(t) end
    show(TASKS_TITLE, tasks_items(), { footnote = TASKS_FOOT })
  end)
end

local panel_timer = nil
local function refresh_panel()
  if state.view ~= 'tasks' or uosc.open_type() ~= MENU or panel_timer then return end
  panel_timer = mp.add_timeout(1 / math.max(1, opts.panel_hz), function()
    panel_timer = nil
    if state.view == 'tasks' and uosc.open_type() == MENU then show(TASKS_TITLE, tasks_items(), { footnote = TASKS_FOOT }) end
  end)
end

local function open_folder(dir)
  if not dir or dir == '' then return end
  local platform = mp.get_property_native('platform') or ''
  local cmd = opts.open_command
  if cmd == '' then cmd = platform == 'windows' and 'explorer' or (platform == 'darwin' and 'open' or 'xdg-open') end
  state.opened = dir
  publish()
  mp.command_native_async({ name = 'subprocess', args = { cmd, dir }, detach = true, playback_only = false,
                            capture_stdout = false }, function() end)
end

local function task_details(t)
  local lines = { t.title or '?', t.description or '', 'Estado: ' .. (t.message ~= '' and t.message or t.status) }
  if t.error and t.error ~= '' then lines[#lines + 1] = t.error end
  for _, w in ipairs(t.warnings or {}) do lines[#lines + 1] = '⚠ ' .. w end
  for _, f in ipairs(t.outputs or {}) do lines[#lines + 1] = '→ ' .. f end
  if #(t.outputs or {}) == 0 and t.out_dir ~= '' then lines[#lines + 1] = 'Carpeta: ' .. t.out_dir end
  mp.osd_message(table.concat(lines, '\n'), 6)
end

local function task_action(ref, action)
  local t = state.tasks[task_key(ref)]
  if not t then return end
  if not action then task_details(t) return end
  if action == 'folder' then open_folder(t.out_dir) return end
  local method = (METHODS[t.type] or {})[action]
  if not method then return end
  rpc.call(method, { id = t.id }, function(err)
    if err then osd(fail(err, method)) return end
    if state.view == 'tasks' then open_view({ name = 'tasks' }, false) end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- starting

local function start_job()
  local job = state.job
  if not job then return end
  local p = preset(job.preset)
  local o = current_options()
  local spec = { quality = o.quality, speed = opts.speed, hw = P:get('hw') and 'auto' or 'cpu' }
  if p.kind == 'video' then
    spec.height, spec.subtitles = o.height, o.subtitles
    if p.id == 'small' then spec.container = o.container end
  end
  if p.kind == 'gif' then spec.gif_width, spec.gif_fps = o.gif_width, o.gif_fps end
  if LOSSY_AUDIO[p.id] and o.audio_bitrate ~= 0 then spec.audio_bitrate = o.audio_bitrate end
  if p.kind == 'audio' and not job.folder then
    -- the audio track playing (ff-index among the audio streams is its position in the track list)
    local n = 0
    for _, t in ipairs(mp.get_property_native('track-list') or {}) do
      if t.type == 'audio' then
        if t.selected then spec.audio_track = n end
        n = n + 1
      end
    end
  end
  if job.range and not job.folder then
    local a, b = marks()
    if a then spec.start, spec['end'] = a, b end
  end
  local params = { path = job.source, preset = job.preset, options = spec, notify = SCRIPT }
  if P:get('dir') ~= '' then params.out_dir = output_dir() end
  rpc.call('convert.start', params, function(err, res)
    if err then osd(tr('Convertir: %s'):format(fail(err, 'convert.start')), 5) return end
    state.last_started = {}
    for _, it in ipairs(res.items or {}) do
      state.last_started[#state.last_started + 1] = it.id
      upsert({ type = 'convert', id = it.id, title = it.title, description = it.description, status = it.status,
               progress = 0, message = it.message, out_dir = it.out_dir, outputs = {}, actions = { 'cancel' } }, true)
    end
    state.last_error = ''
    osd(res.count > 1 and string.format('⚙ Convirtiendo %d archivos → %s', res.count, res.out_dir)
      or ('⚙ Convirtiendo ' .. basename(job.source) .. ' → ' .. p.label))
    -- show the tasks, one level below where we are
    open_view({ name = 'tasks' })
  end, 20)
end

local function cycle_option(name)
  local o = current_options()
  if name == 'subtitles' then
    o.subtitles = not o.subtitles
  else
    local list = CYCLES[name]
    local idx = 0
    for i, v in ipairs(list) do if v == o[name] then idx = i end end
    o[name] = list[idx % #list + 1]
  end
  P:set('options', o)
end

-- ---------------------------------------------------------------------------------------------
-- text box (a uosc palette whose query is the text): source folder or output folder

local INPUT_TITLES = { folder = 'Carpeta que quieres convertir', dir = 'Carpeta de salida' }

local function input_menu(query)
  state.input.query = query or ''
  local mode = state.input.mode
  local items
  if query ~= '' then
    items = { { title = tr('Usar: %s'):format(query), icon = 'check', value = { save = query } } }
  elseif mode == 'dir' then
    items = { { title = tr('Escribe o pega la carpeta (vacío = predeterminada)'), icon = 'edit', value = { save = '' } } }
  else
    items = { { title = tr('Escribe o pega la ruta de la carpeta'), icon = 'edit', selectable = false, muted = true } }
  end
  return { type = INPUT, title = INPUT_TITLES[mode], items = items, callback = { SCRIPT, INPUT_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    search_suggestion = query, footnote = tr('Enter elige · ⌫ en vacío vuelve') }
end

local function open_input(mode, text)
  state.input = { mode = mode, query = text or '' }
  publish()
  uosc.open(input_menu(text or ''))
end

mp.register_script_message(INPUT_EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if not state.input then return end
  if ev.type == 'search' then
    uosc.update(input_menu(ev.query or ''))
    return
  end
  local chosen = ev.type == 'activate' and type(ev.value) == 'table' and ev.value.save or nil
  if ev.type ~= 'back' and chosen == nil then return end
  local mode = state.input.mode
  state.input = nil
  uosc.close(INPUT)
  if mode == 'dir' and chosen ~= nil then
    P:set('dir', chosen)
    osd(tr('Carpeta de salida: %s'):format(chosen ~= '' and output_dir() or tr('predeterminada')))
  elseif mode == 'folder' and chosen ~= nil then
    local path = mp.command_native({ 'expand-path', chosen }) or chosen
    local info = utils.file_info(path)
    if info and info.is_dir then
      state.force_open = true
      open_view({ name = 'folder', args = { folder = path } })
      return
    end
    osd(tr('No es una carpeta: %s'):format(path))
  end
  reopen_current(true)
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    local v = type(ev.value) == 'table' and ev.value or {}
    if v.preset then
      local a = marks()
      state.job = { preset = v.preset, source = v.source, folder = v.folder or false, range = a ~= nil and not v.folder }
      open_view({ name = 'options' })
    elseif v.opt == 'range' then
      if not marks() then
        osd(tr('Marca el inicio y el final (filas de abajo o tecla l)'))
        state.job.range = false
      else
        state.job.range = not state.job.range
      end
      reopen_current()
    elseif v.opt then
      cycle_option(v.opt)
      reopen_current()
    elseif v.mark then
      mp.set_property_number(v.mark == 'a' and 'ab-loop-a' or 'ab-loop-b', mp.get_property_number('time-pos') or 0)
      if marks() then state.job.range = true end
      reopen_current()
    elseif v.start then
      start_job()
    elseif v.toggle_hw then
      P:set('hw', not P:get('hw'))
      reopen_current()
    elseif v.folder_input then
      local file = local_file()
      open_input('folder', file and dirname(file) or '')
    elseif v.choose_dir then
      open_input('dir', P:get('dir'))
    elseif v.task then
      task_action(v.task, ev.action)
    elseif v.clear then
      rpc.call('tasks.clear', nil, function() open_view({ name = 'tasks' }, false) end)
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
-- events pushed by mpvd: `task` rows (downloads and conversions) and `convert` items

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  local t = ev.task
  if type(t) ~= 'table' or not t.id then return end
  local prev = state.tasks[task_key(t)]
  upsert(t, true)
  if t.type == 'convert' and (not prev or prev.status ~= t.status) and (t.status == 'done' or t.status == 'failed') then
    state.last_done = { id = t.id, status = t.status, file = (t.outputs or {})[1] or '', error = t.error or '' }
    if opts.notify_done then
      if t.status == 'done' then
        osd(tr('✓ Convertido: %s\n→ %s'):format(t.title or '', (t.outputs or {})[1] or t.out_dir or ''), 5)
      else
        osd(tr('✗ No se pudo convertir %s\n%s'):format(t.title or '', t.error or ''), 5)
      end
    end
  end
  publish()
  refresh_panel()
end)

-- ---------------------------------------------------------------------------------------------
-- bindings

local function open_root()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

local function open_tasks()
  if not uosc.available() then osd(tr('uosc no está cargado')) return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'tasks' })
end

N:binding('convert-menu', open_root)
N:binding('tasks-menu', open_tasks)
-- `script-message-to mu_convert mu-convert-folder <carpeta>`: presets for a folder (e.g. from a file manager)
mp.register_script_message('mu-convert-folder', function(path)
  if not uosc.available() or not path or path == '' then return end
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  state.force_open = true
  open_view({ name = 'folder', args = { folder = path } })
end)

mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.mpvd == 'connected' and state.default_dir == '' then
    with_presets(function() end)
  end
end)

publish()
msg.info('mu-convert loaded')
