-- mu-intro: skip intro / credits detected locally by mpvd (intro.*: Chromaprint between episodes of the same season, in
-- the same folder or in one folder per episode). On file-loaded asks mpvd for the segments (starting the analysis in the
-- background if needed). The uosc button mu-skip is always there for local videos (tooltip says why nothing can be skipped
-- yet); inside a segment it lights up and a clickable "Saltar intro ▸" box floats over the video. alt+k skips (intro → its
-- end; credits → next playlist item, else the next episode found by mpvd, else the end). Optional automatic skipping with a
-- short countdown (Esc cancels), manual marking (applied by mpvd to the whole season), season analysis and on-demand export.
-- Script name: mu_intro. State in user-data/mu/intro.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')

local SCRIPT = mp.get_script_name()

local opts = {
  enabled = true,
  auto_skip_intro = false,
  auto_skip_credits = false,
  countdown_seconds = 3,    -- automatic skips wait this long (Esc cancels); 0 = skip at once
  indicator = true,         -- floating clickable "Saltar intro ▸" while inside a segment
  notify_missing = true,    -- brief notice when nothing could be detected or the analysis failed
  osd_seconds = 3,
  poll_seconds = 0.5,
  min_remaining = 1.0,      -- do not offer a skip when the segment is about to end anyway
  credits_tail = 15,        -- credits ending this close to the end of the file lead to the next episode
  watchdog_seconds = 20,    -- re-ask mpvd while an analysis is pending (restarted daemon, lost event)
}
local on_options -- forward: script-opts changed at runtime (e.g. by a preferences system)
options.read_options(opts, 'mu-intro', function() if on_options then on_options() end end)

local state = {
  path = '',
  local_video = false,      -- a local file with a real video track: the only thing mpvd can analyse
  status = '',              -- '', analyzing, done, unknown, error
  segments = {},            -- { {type='intro', start=, ['end']=, source=}, ... }
  current = nil,            -- type of the segment time-pos is in, or nil
  skipped = {},             -- type -> true after a skip / a cancelled countdown (no automatic skip twice per file)
  hinted = {},
  siblings = 0,
  episodes = 0,
  reason = '',
  last_error = '',
  progress = 0,
  progress_msg = '',
  next = '',                -- following episode found by mpvd (used when the playlist has no next item)
  pending = {},             -- kind -> start time marked by hand, waiting for its end
  noticed = false,          -- the "nothing detected" / error notice was shown for this file
  season = nil,             -- { done=, total=, found= } while/after "Analizar temporada"
  button_sig = '',
  retries = 0,              -- intro.segments timeouts retried for this file
}

local set_button, refresh_menu, update_indicator, cancel_countdown -- forward
local cd = nil               -- running countdown { kind=, left=, last=, timer= }
local ind = { kind = nil, text = '', box = nil, hover = false, watching = false } -- floating indicator

local function osd(text)
  mp.osd_message(text, opts.osd_seconds)
  mp.set_property_native('user-data/mu/intro-osd', text) -- last notice (tests, remote)
end

local function pct() return math.floor((tonumber(state.progress) or 0) * 100 + 0.5) end

local function short(text, n)
  text = tostring(text or '')
  n = n or 70
  if #text <= n then return text end
  return text:sub(1, n) .. '…'
end

local function basename(p) return (p or ''):match('([^/\\]+)$') or p end

local function fmt_time(t)
  t = math.floor(tonumber(t) or 0)
  if t >= 3600 then return string.format('%d:%02d:%02d', t / 3600, (t % 3600) / 60, t % 60) end
  return string.format('%d:%02d', t / 60, t % 60)
end

-- Why nothing can be skipped right now (Spanish, short).
local function why()
  if not state.local_video then return 'solo en vídeos locales' end
  if state.status == 'analyzing' then return string.format('analizando… %d %%', pct()) end
  if state.status == 'error' then
    local r = state.reason ~= '' and state.reason or state.last_error
    return 'error al analizar' .. (r ~= '' and (' (' .. short(r, 60) .. ')') or '')
  end
  if state.status == '' then return 'esperando a mpvd' end
  return state.reason ~= '' and state.reason or 'sin intro ni créditos detectados'
end

local function publish()
  mp.set_property_native('user-data/mu/intro', {
    enabled = opts.enabled, path = state.path, local_video = state.local_video, status = state.status,
    segments = state.segments, current = state.current or '', siblings = state.siblings, episodes = state.episodes,
    reason = state.reason, last_error = state.last_error, progress = state.progress, next = state.next,
    auto_skip_intro = opts.auto_skip_intro, auto_skip_credits = opts.auto_skip_credits,
    countdown = cd and math.max(0, math.ceil(cd.left)) or 0, countdown_kind = cd and cd.kind or '',
    indicator = ind.kind or '',
    pending_intro = state.pending.intro or -1, pending_credits = state.pending.credits or -1,
    season = state.season or {},
  })
end

local function is_local(path)
  if not path or path == '' then return false end
  if path:match('^file://') then return true end
  return path:match('^%a[%w+.-]*://') == nil
end

local function has_video()
  local v = mp.get_property_native('current-tracks/video')
  return type(v) == 'table' and not v.image and not v.albumart
end

local function current_path() return (mp.get_property('path') or ''):gsub('^file://', '') end

local function segment_at(t)
  for _, s in ipairs(state.segments) do
    if t >= s.start and t < s['end'] - 0.05 then return s end
  end
  return nil
end

local function segment_of(kind)
  for _, s in ipairs(state.segments) do if s.type == kind then return s end end
  return nil
end

local function skip(kind)
  if cancel_countdown then cancel_countdown(false) end
  local seg = segment_of(kind or (state.current or ''))
  if not seg then
    if #state.segments == 0 then
      if state.status == 'analyzing' then
        osd(string.format('Analizando… %d %%', pct()))
      else
        osd('Saltar intro: ' .. why() .. ' · alt+j')
      end
      return false
    end
    if kind ~= nil then osd('No hay ' .. (kind == 'intro' and 'intro' or 'créditos') .. ' detectados') return false end
    -- outside any segment: jump to the end of the next one ahead (manual key press)
    local pos = mp.get_property_number('time-pos') or 0
    for _, s in ipairs(state.segments) do
      if s.start > pos and (not seg or s.start < seg.start) then seg = s end
    end
    if not seg then osd('No hay intro ni créditos por delante') return false end
  end
  local duration = mp.get_property_number('duration') or 0
  if seg.type == 'credits' and duration > 0 and seg['end'] >= duration - math.max(1.0, opts.credits_tail) then
    local pos = mp.get_property_number('playlist-pos') or 0
    local count = mp.get_property_number('playlist-count') or 0
    if pos + 1 < count then
      mp.commandv('playlist-next')
      osd('⏭ Créditos saltados: siguiente episodio')
    elseif state.next ~= '' then
      mp.commandv('loadfile', state.next, 'insert-next')
      mp.commandv('playlist-next')
      osd('⏭ Créditos saltados: ' .. basename(state.next))
    else
      mp.commandv('seek', tostring(math.max(0, seg['end'] - 0.5)), 'absolute')
      osd('⏭ Créditos saltados')
    end
  else
    mp.commandv('seek', tostring(seg['end']), 'absolute')
    osd(seg.type == 'intro' and '⏭ Intro saltada' or '⏭ Segmento saltado')
  end
  state.skipped[seg.type] = true
  return true
end

-- ---------------------------------------------------------------------------------------------
-- floating indicator: ASS box in the lower right corner, clickable while the mouse is over it

local overlay = mp.create_osd_overlay('ass-events')
overlay.z = 5

local function utf8_len(s)
  local _, n = s:gsub('[^\128-\191]', '')
  return n
end

local function ind_render()
  if not ind.kind then overlay:remove() ind.box = nil return end
  local dim = mp.get_property_native('osd-dimensions') or {}
  local w, h = tonumber(dim.w) or 0, tonumber(dim.h) or 0
  if w <= 0 or h <= 0 then w, h = 1280, 720 end
  local fs = math.max(16, math.floor(h / 28))
  local pad = math.floor(fs * 0.7)
  local bw = math.floor(utf8_len(ind.text) * fs * 0.56 + pad * 2)
  local bh = math.floor(fs * 1.25 + pad)
  local x1 = w - math.floor(w * 0.03)
  local y1 = h - math.max(math.floor(h * 0.17), 90)   -- above uosc's timeline and controls
  local x0, y0 = x1 - bw, y1 - bh
  ind.box = { x0, y0, x1, y1 }
  local fill, alpha, fg = '000000', '40', 'FFFFFF'
  if ind.hover then fill, alpha, fg = 'FFFFFF', '10', '000000' end
  overlay.res_x, overlay.res_y = w, h
  overlay.data = string.format(
    '{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}m %d %d l %d %d l %d %d l %d %d{\\p0}\n' ..
    '{\\an5\\pos(%d,%d)\\bord0\\shad0\\fs%d\\b1\\1c&H%s&}%s',
    fill, alpha, x0, y0, x1, y0, x1, y1, x0, y1, math.floor((x0 + x1) / 2), math.floor((y0 + y1) / 2), fs, fg, ind.text)
  overlay:update()
end

local function on_mouse(_, m)
  if not ind.box or type(m) ~= 'table' then return end
  local b = ind.box
  local over = m.hover ~= false and m.x ~= nil and m.x >= b[1] and m.x <= b[3] and m.y >= b[2] and m.y <= b[4]
  if over == ind.hover then return end
  ind.hover = over
  if over then
    mp.add_forced_key_binding('MBTN_LEFT', 'mu-intro-indicator-click', function() skip(ind.kind) end)
  else
    mp.remove_key_binding('mu-intro-indicator-click')
  end
  ind_render()
end

local function on_resize() if ind.kind then ind_render() end end

local function ind_set(kind, text)
  if kind == ind.kind and text == ind.text then return end
  ind.kind, ind.text = kind, text
  if kind and not ind.watching then
    ind.watching = true
    mp.observe_property('mouse-pos', 'native', on_mouse)
    mp.observe_property('osd-dimensions', 'native', on_resize)
  elseif not kind and ind.watching then
    ind.watching = false
    mp.unobserve_property(on_mouse)
    mp.unobserve_property(on_resize)
    if ind.hover then mp.remove_key_binding('mu-intro-indicator-click') end
    ind.hover = false
  end
  ind_render()
end

local function countdown_text()
  return string.format('Saltando %s en %d s · Esc cancela', cd.kind == 'intro' and 'intro' or 'créditos',
    math.max(1, math.ceil(cd.left)))
end

update_indicator = function()
  if cd then
    local text = countdown_text()
    if opts.indicator then ind_set(cd.kind, text) elseif text ~= ind.text then ind.text = text osd(text) end
    return
  end
  if opts.indicator and opts.enabled and state.current then
    ind_set(state.current, state.current == 'intro' and 'Saltar intro ▸' or 'Saltar créditos ▸')
  else
    ind_set(nil, '')
  end
end

-- ---------------------------------------------------------------------------------------------
-- automatic skip with a countdown (Esc cancels: forced binding only while counting)

cancel_countdown = function(by_user)
  if not cd then return end
  local kind = cd.kind
  cd.timer:kill()
  cd = nil
  mp.remove_key_binding('mu-intro-cancel')
  if by_user then
    state.skipped[kind] = true
    osd('Salto automático cancelado')
  end
  update_indicator()
  publish()
end

local function start_countdown(kind)
  local n = tonumber(opts.countdown_seconds) or 3
  if n <= 0 then skip(kind) return end
  cd = { kind = kind, left = n, last = mp.get_time() }
  mp.add_forced_key_binding('ESC', 'mu-intro-cancel', function() cancel_countdown(true) end)
  cd.timer = mp.add_periodic_timer(0.25, function()
    if not cd then return end
    local now = mp.get_time()
    local dt = now - cd.last
    cd.last = now
    if mp.get_property_bool('pause') then return end -- the countdown waits while paused
    local before = math.ceil(cd.left)
    cd.left = cd.left - dt
    if cd.left <= 0 then
      skip(cd.kind)
    elseif math.ceil(cd.left) ~= before then
      update_indicator()
      publish()
    end
  end)
  update_indicator()
  publish()
end

local function tick()
  if not opts.enabled or #state.segments == 0 then return end
  local pos = mp.get_property_number('time-pos')
  if pos == nil then return end
  local seg = segment_at(pos)
  local kind = seg and seg.type or nil
  if kind == state.current then return end
  if cd and cd.kind ~= kind then cancel_countdown(false) end
  state.current = kind
  if seg and not state.hinted[kind] and seg['end'] - pos > opts.min_remaining then
    state.hinted[kind] = true
    local auto = (kind == 'intro' and opts.auto_skip_intro) or (kind == 'credits' and opts.auto_skip_credits)
    if auto and not state.skipped[kind] then
      start_countdown(kind)
    elseif not opts.indicator then
      osd((kind == 'intro' and 'Intro' or 'Créditos') .. ' · alt+k para saltar')
    end
  end
  update_indicator()
  publish()
  set_button()
  refresh_menu()
end

local timer = mp.add_periodic_timer(opts.poll_seconds, tick)
timer:kill()

-- ---------------------------------------------------------------------------------------------
-- results from mpvd

local request -- forward
local watchdog = mp.add_periodic_timer(math.max(5, tonumber(opts.watchdog_seconds) or 20), function()
  if state.status == 'analyzing' then request(true) end
end)
watchdog:kill()

local function notice()
  if state.noticed or not opts.notify_missing or not state.local_video then return end
  state.noticed = true
  osd('Saltar intro: ' .. why() .. ' · alt+j')
end

local function apply(res, quiet)
  state.status = res.status or 'done'
  state.reason = res.reason or ''
  if state.status == 'error' then state.last_error = state.reason end
  state.siblings = type(res.siblings) == 'table' and #res.siblings or 0
  if tonumber(res.episodes) then state.episodes = tonumber(res.episodes) end
  if res.next ~= nil then state.next = type(res.next) == 'string' and res.next or '' end
  if state.status == 'analyzing' then
    state.progress = tonumber(res.progress) or state.progress
    state.progress_msg = res.message or state.progress_msg
  end
  local sources = type(res.sources) == 'table' and res.sources or {}
  state.segments = {}
  for _, kind in ipairs({ 'intro', 'credits' }) do
    local seg = res[kind]
    if type(seg) == 'table' and seg[1] and seg[2] then
      table.insert(state.segments, { type = kind, start = seg[1], ['end'] = seg[2], source = sources[kind] or 'auto' })
    end
  end
  if #state.segments > 0 then timer:resume() else timer:kill() end
  if state.status == 'analyzing' then watchdog:resume() else watchdog:kill() end
  if cd and not segment_of(cd.kind) then cancel_countdown(false) end
  state.current = nil
  tick()
  update_indicator()
  publish()
  set_button()
  refresh_menu()
  if not quiet and state.status ~= 'analyzing' and #state.segments == 0 then notice() end
end

request = function(quiet)
  if not rpc.connected() or not state.local_video then return end
  local path = state.path
  rpc.call('intro.segments', { path = current_path(), notify = SCRIPT }, function(err, res)
    if state.path ~= path then return end -- another file meanwhile
    if err and err.code == -32000 and state.retries < 2 and state.status ~= 'done' then
      -- lost reply / busy or restarting daemon: ask again before giving up
      state.retries = state.retries + 1
      mp.add_timeout(3, function() if state.path == path then request(quiet) end end)
      return
    end
    if err then
      state.status = 'error'
      state.last_error = err.message or tostring(err)
      state.reason = state.last_error
      watchdog:kill()
      publish()
      set_button()
      refresh_menu()
      if not quiet then msg.info('intro.segments: ' .. state.last_error) end
      notice()
      return
    end
    apply(res)
  end, 20)
end

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' then return end
  local p = current_path()
  if ev.event == 'intro' and type(ev.result) == 'table' then
    if ev.result.path == p then apply(ev.result) end
  elseif ev.event == 'intro-progress' then
    if ev.path == p and state.status == 'analyzing' then
      state.progress = tonumber(ev.progress) or state.progress
      state.progress_msg = ev.message or ''
      publish()
      set_button()
      refresh_menu()
    end
  elseif ev.event == 'intro-season' then
    state.season = { done = ev.done or 0, total = ev.total or 0, found = ev.found or 0, final = ev.final == true }
    if ev.final then
      osd(string.format('Temporada analizada: %d de %d episodios con intro o créditos', ev.found or 0, ev.total or 0))
    end
    publish()
    refresh_menu()
  elseif ev.event == 'intro-mark' then
    osd(string.format('Marca de %s aplicada a %d de %d episodios', ev.kind == 'intro' and 'la intro' or 'los créditos',
      ev.applied or 0, ev.total or 0))
  end
end)

mp.register_event('file-loaded', function()
  local path = mp.get_property('path') or ''
  if cancel_countdown then cancel_countdown(false) end
  state.path = path
  state.local_video = is_local(path) and has_video()
  state.status = ''
  state.segments = {}
  state.current = nil
  state.skipped = {}
  state.hinted = {}
  state.reason = ''
  state.last_error = ''
  state.progress = 0
  state.progress_msg = ''
  state.next = ''
  state.pending = {}
  state.noticed = false
  state.season = nil
  state.retries = 0
  timer:kill()
  watchdog:kill()
  update_indicator()
  publish()
  set_button()
  if opts.enabled then request(true) end
end)

mp.register_event('end-file', function()
  timer:kill()
  watchdog:kill()
  if cancel_countdown then cancel_countdown(false) end
  state.segments = {}
  state.current = nil
  state.path = ''
  state.local_video = false
  update_indicator()
  publish()
  set_button()
end)

mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then state.button_sig = '' set_button() end
  if core and core.mpvd == 'connected' and state.status == '' and state.local_video then request(true) end
end)

set_button = function()
  if not uosc.available() then return end
  local inside = state.current ~= nil
  local tooltip
  if inside then
    tooltip = (state.current == 'intro' and 'Saltar intro' or 'Saltar créditos') .. ' (alt+k)'
  elseif #state.segments > 0 then
    tooltip = 'Saltar intro/créditos (alt+k)'
  else
    tooltip = 'Saltar intro: ' .. why() .. ' · alt+j para marcarla a mano'
  end
  local spec = {
    icon = 'skip_next', active = inside, hide = not opts.enabled or not state.local_video, tooltip = tooltip,
    badge = inside and (state.current == 'intro' and 'intro' or 'fin') or nil,
    command = { 'script-binding', SCRIPT .. '/skip' },
  }
  local sig = utils.format_json(spec)
  if sig == state.button_sig then return end
  state.button_sig = sig
  uosc.set_button('mu-skip', spec)
end

-- ---------------------------------------------------------------------------------------------
-- manual marks: intro-mark-start / intro-mark-end / credits-mark-start / credits-mark-end

local function save_mark(kind, start_, end_)
  rpc.call('intro.mark', { path = current_path(), kind = kind, start = start_, ['end'] = end_, notify = SCRIPT },
    function(err, res)
      if err then osd('No se pudo guardar la marca: ' .. short(err.message or tostring(err))) return end
      apply(res, true)
      osd(string.format('%s guardada: %s → %s · se buscará en el resto de la temporada',
        kind == 'intro' and 'Intro' or 'Créditos', fmt_time(start_), fmt_time(end_)))
    end, 30)
end

local function mark(kind, which)
  if not state.local_video then osd('Solo se puede marcar en vídeos locales') return end
  if not rpc.connected() then osd('mpvd no está conectado') return end
  local pos = mp.get_property_number('time-pos')
  if not pos then return end
  local start_, end_
  if which == 'start' then
    state.pending[kind] = pos
    if kind == 'intro' then
      osd(string.format('Inicio de la intro: %s · ahora marca el final', fmt_time(pos)))
      publish()
      refresh_menu()
      return
    end
    -- credits: saved at once up to the end of the file; marking their end later narrows them
    start_, end_ = pos, mp.get_property_number('duration') or 0
  else
    local seg = segment_of(kind)
    start_ = state.pending[kind] or (seg and seg.start) or (kind == 'intro' and 0 or nil)
    if not start_ then osd('Marca primero el inicio de los créditos') return end
    end_ = pos
    state.pending[kind] = nil
  end
  if end_ - start_ < 1 then osd('El tramo marcado es demasiado corto (marca el final después del inicio)') return end
  publish()
  save_mark(kind, start_, end_)
end

local function unmark()
  if not rpc.connected() or not state.local_video then return end
  rpc.call('intro.unmark', { path = current_path() }, function(err, res)
    if err then osd('No se pudo borrar: ' .. short(err.message or tostring(err))) return end
    state.pending = {}
    apply(res, true)
    osd('Marcas manuales borradas')
  end, 30)
end

-- ---------------------------------------------------------------------------------------------
-- menu (one level): status, segments, skip now, marks, automatic skipping, season, re-analyse, export

local MENU = 'mu-intro'
local EVENT = 'mu-intro-menu-event'

local function yesno(b) return b and 'activado' or 'desactivado' end

local function has_manual()
  for _, s in ipairs(state.segments) do if s.source == 'manual' then return true end end
  return false
end

local function menu_items()
  local items = {}
  local pos = mp.get_property_number('time-pos') or 0
  if not state.local_video then
    table.insert(items, { title = 'Abre un episodio de una serie (vídeo local)', icon = 'info', selectable = false,
      muted = true })
  elseif state.status == 'analyzing' then
    table.insert(items, { title = string.format('Analizando… %d %%', pct()), hint = state.progress_msg ~= '' and
      short(state.progress_msg, 40) or nil, icon = 'spinner', selectable = false, muted = true })
  elseif state.status == 'error' then
    table.insert(items, { title = 'Error: ' .. short(why(), 80), icon = 'error', selectable = false, muted = true })
  elseif #state.segments == 0 then
    table.insert(items, { title = 'Sin intro ni créditos detectados', hint = state.reason ~= '' and short(state.reason, 50)
      or nil, icon = 'info', selectable = false, muted = true })
  end
  if state.season and not state.season.final then
    table.insert(items, { title = string.format('Analizando temporada… %d/%d', state.season.done or 0,
      state.season.total or 0), icon = 'spinner', selectable = false, muted = true })
  end
  for _, s in ipairs(state.segments) do
    table.insert(items, { title = (s.type == 'intro' and 'Intro' or 'Créditos') .. ' · ' .. fmt_time(s.start) .. ' → '
      .. fmt_time(s['end']), hint = string.format('%d s%s', math.floor(s['end'] - s.start + 0.5),
      s.source == 'manual' and ' · manual' or ''), icon = s.type == 'intro' and 'play_circle' or 'movie',
      active = state.current == s.type, value = { seek = s.start } })
  end
  if #state.segments > 0 then
    table.insert(items, { title = 'Saltar ahora', hint = 'alt+k', icon = 'skip_next', value = { skip = true } })
  end
  if state.local_video then
    local here = fmt_time(pos)
    items[#items].separator = true
    table.insert(items, { title = 'Marcar inicio de la intro aquí', hint = state.pending.intro and
      ('inicio ' .. fmt_time(state.pending.intro) .. ' pendiente') or here, icon = 'first_page',
      value = { mark = 'intro', which = 'start' } })
    table.insert(items, { title = 'Marcar final de la intro aquí', hint = here, icon = 'last_page',
      value = { mark = 'intro', which = 'end' } })
    table.insert(items, { title = 'Marcar inicio de los créditos aquí', hint = here, icon = 'first_page',
      value = { mark = 'credits', which = 'start' } })
    table.insert(items, { title = 'Marcar final de los créditos aquí', hint = here, icon = 'last_page',
      value = { mark = 'credits', which = 'end' } })
    if has_manual() then
      table.insert(items, { title = 'Borrar las marcas manuales de este episodio', icon = 'delete',
        value = { unmark = true } })
    end
  end
  items[#items].separator = true
  table.insert(items, { title = 'Saltar la intro automáticamente', hint = yesno(opts.auto_skip_intro), icon = 'fast_forward',
    active = opts.auto_skip_intro, value = { toggle = 'auto_skip_intro' } })
  table.insert(items, { title = 'Saltar los créditos automáticamente', hint = yesno(opts.auto_skip_credits),
    icon = 'skip_next', active = opts.auto_skip_credits, value = { toggle = 'auto_skip_credits' } })
  table.insert(items, { title = 'Detección activada', hint = yesno(opts.enabled), icon = 'radar', active = opts.enabled,
    value = { toggle = 'enabled' } })
  if state.local_video then
    items[#items].separator = true
    table.insert(items, { title = 'Analizar temporada', hint = state.episodes > 0 and ((state.episodes + 1) .. ' episodios')
      or nil, icon = 'playlist_play', value = { season = true } })
    table.insert(items, { title = 'Volver a analizar este episodio', hint = 'fpcalc + ffmpeg en segundo plano',
      icon = 'refresh', value = { reanalyze = true } })
    table.insert(items, { title = 'Exportar segmentos (Jellyfin)', hint = 'segments.json junto al vídeo', icon = 'save',
      value = { export = true } })
  end
  return items
end

local function base_menu()
  return { type = MENU, title = 'Saltar intro y créditos', items = menu_items(), callback = { SCRIPT, EVENT },
    keep_open = true, search_submenus = false,
    footnote = 'mpvd compara el audio con los otros episodios de la temporada (misma carpeta o carpetas hermanas)' }
end

refresh_menu = function()
  if uosc.open_type() == MENU then uosc.update(base_menu()) end
end

local function open_menu()
  if not uosc.available() then osd('uosc no está cargado') return end
  if uosc.open_type() == MENU then uosc.update(base_menu()) else uosc.open(base_menu()) end
end

local function reanalyze()
  if not rpc.connected() then osd('mpvd no está conectado') return end
  if not state.local_video then osd('Solo vídeos locales') return end
  state.status = 'analyzing'
  state.progress = 0
  state.noticed = false
  publish()
  set_button()
  refresh_menu()
  watchdog:resume()
  rpc.call('intro.analyze', { path = current_path(), notify = SCRIPT }, function(err)
    if err then
      state.status = 'error'
      state.last_error = err.message or tostring(err)
      state.reason = state.last_error
      watchdog:kill()
      publish()
      set_button()
      refresh_menu()
      osd('Análisis: ' .. state.last_error)
    end
  end, 30)
end

local function analyze_season()
  if not rpc.connected() then osd('mpvd no está conectado') return end
  if not state.local_video then osd('Solo vídeos locales') return end
  rpc.call('intro.season', { path = current_path(), notify = SCRIPT }, function(err, res)
    if err then osd('Temporada: ' .. short(err.message or tostring(err))) return end
    state.season = { done = 0, total = res.episodes or 0, found = 0 }
    osd(string.format('Analizando la temporada en segundo plano (%d episodios)', res.episodes or 0))
    publish()
    refresh_menu()
  end, 30)
end

local function export()
  if not rpc.connected() then osd('mpvd no está conectado') return end
  if not state.local_video then osd('Solo vídeos locales') return end
  rpc.call('intro.export', { path = current_path() }, function(err, res)
    if err then osd('Exportar: ' .. short(err.message or tostring(err))) return end
    local n = 0
    for _ in pairs(res.entries or {}) do n = n + 1 end
    osd(string.format('Exportados %d archivo(s) a %s', n, res.file or 'segments.json'))
  end, 30)
end

local function set_opt(key, value)
  if key == 'countdown_seconds' then
    opts.countdown_seconds = tonumber(value) or opts.countdown_seconds
  elseif key == 'auto_skip_intro' or key == 'auto_skip_credits' or key == 'enabled' or key == 'indicator'
      or key == 'notify_missing' then
    if type(value) == 'boolean' then opts[key] = value else opts[key] = (value == 'yes' or value == 'true') end
  else
    return
  end
  if key == 'enabled' then
    if opts.enabled then request(true) else timer:kill() cancel_countdown(false) state.current = nil end
  end
  update_indicator()
  publish()
  set_button()
  refresh_menu()
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type ~= 'activate' then return end
  local v = type(ev.value) == 'table' and ev.value or {}
  if v.toggle then
    set_opt(v.toggle, not opts[v.toggle])
  elseif v.skip then
    uosc.close(MENU)
    skip(nil)
  elseif v.seek then
    mp.commandv('seek', tostring(v.seek), 'absolute')
    refresh_menu()
  elseif v.mark then
    mark(v.mark, v.which)
    refresh_menu()
  elseif v.unmark then
    unmark()
  elseif v.season then
    analyze_season()
  elseif v.reanalyze then
    reanalyze()
  elseif v.export then
    export()
  end
end)

on_options = function()
  if cd and not ((cd.kind == 'intro' and opts.auto_skip_intro) or (cd.kind == 'credits' and opts.auto_skip_credits)) then
    cancel_countdown(false)
  end
  update_indicator()
  publish()
  set_button()
  refresh_menu()
end

mp.add_key_binding(nil, 'skip', function() skip(nil) end)
mp.add_key_binding(nil, 'intro-menu', open_menu)
mp.add_key_binding(nil, 'intro-mark-start', function() mark('intro', 'start') end)
mp.add_key_binding(nil, 'intro-mark-end', function() mark('intro', 'end') end)
mp.add_key_binding(nil, 'credits-mark-start', function() mark('credits', 'start') end)
mp.add_key_binding(nil, 'credits-mark-end', function() mark('credits', 'end') end)
mp.add_key_binding(nil, 'intro-season', analyze_season)
mp.add_key_binding(nil, 'intro-export', export)
mp.register_script_message('mu-intro-skip', function(kind) skip(kind ~= '' and kind or nil) end)
mp.register_script_message('mu-intro-refresh', function() request(false) end)
mp.register_script_message('mu-intro-set', set_opt)
mp.register_script_message('mu-intro-mark', function(kind, which) mark(kind, which) end)
mp.register_script_message('uosc-version', function() state.button_sig = '' set_button() end)

publish()
msg.info('mu-intro loaded')
