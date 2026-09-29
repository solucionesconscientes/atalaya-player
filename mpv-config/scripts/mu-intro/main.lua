-- mu-intro: skip intro / credits detected locally by mpvd (intro.* : Chromaprint between episodes of the same folder).
-- On file-loaded asks mpvd for the segments (starting the analysis in the background if needed); while time-pos is inside
-- a segment shows a uosc button + OSD hint; alt+k skips (intro → seek to its end; credits → next playlist item or end).
-- Optional automatic skipping. Script name: mu_intro. State in user-data/mu/intro.
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
  osd_seconds = 4,
  poll_seconds = 0.5,
  min_remaining = 1.0,      -- do not offer a skip when the segment is about to end anyway
}
options.read_options(opts, 'mu-intro')

local state = {
  path = '',
  status = '',              -- '', analyzing, done, unknown, error
  segments = {},            -- { {type='intro', start=, ['end']=}, ... }
  current = nil,            -- type of the segment time-pos is in, or nil
  skipped = {},             -- type -> true after an automatic skip (never twice per file)
  hinted = {},
  siblings = 0,
  reason = '',
  last_error = '',
}

local function osd(text) mp.osd_message(text, opts.osd_seconds) end

local function publish()
  mp.set_property_native('user-data/mu/intro', {
    enabled = opts.enabled, path = state.path, status = state.status, segments = state.segments,
    current = state.current or '', siblings = state.siblings, reason = state.reason, last_error = state.last_error,
    auto_skip_intro = opts.auto_skip_intro, auto_skip_credits = opts.auto_skip_credits,
  })
end

local function is_local(path)
  if not path or path == '' then return false end
  if path:match('^file://') then return true end
  return path:match('^%a[%w+.-]*://') == nil
end

local set_button, refresh_menu -- forward

local function segment_at(t)
  for _, s in ipairs(state.segments) do
    if t >= s.start and t < s['end'] - 0.05 then return s end
  end
  return nil
end

local function skip(kind)
  local seg = nil
  for _, s in ipairs(state.segments) do if s.type == (kind or (state.current or '')) then seg = s end end
  if not seg then
    if kind == nil and #state.segments > 0 then
      -- outside any segment: jump to the end of the next one ahead (manual key press)
      local pos = mp.get_property_number('time-pos') or 0
      local nxt = nil
      for _, s in ipairs(state.segments) do
        if s.start > pos and (not nxt or s.start < nxt.start) then nxt = s end
      end
      if not nxt then osd('No hay intro ni créditos por delante') return false end
      seg = nxt
    else
      osd('Sin segmentos detectados' .. (state.reason ~= '' and (': ' .. state.reason) or ''))
      return false
    end
  end
  local duration = mp.get_property_number('duration') or 0
  if seg.type == 'credits' and duration > 0 and seg['end'] >= duration - 1.0 then
    local pos = mp.get_property_number('playlist-pos') or 0
    local count = mp.get_property_number('playlist-count') or 0
    if pos + 1 < count then
      mp.commandv('playlist-next')
      osd('⏭ Créditos saltados: siguiente episodio')
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

local function tick()
  if not opts.enabled or #state.segments == 0 then return end
  local pos = mp.get_property_number('time-pos')
  if pos == nil then return end
  local seg = segment_at(pos)
  local kind = seg and seg.type or nil
  if kind ~= state.current then
    state.current = kind
    publish()
    set_button()
    refresh_menu()
    if seg and not state.hinted[kind] and seg['end'] - pos > opts.min_remaining then
      state.hinted[kind] = true
      local auto = (kind == 'intro' and opts.auto_skip_intro) or (kind == 'credits' and opts.auto_skip_credits)
      if auto and not state.skipped[kind] then
        skip(kind)
      else
        osd((kind == 'intro' and 'Intro' or 'Créditos') .. ' · alt+k para saltar')
      end
    end
  end
end

local timer = mp.add_periodic_timer(opts.poll_seconds, tick)
timer:kill()

local function apply(res)
  state.status = res.status or 'done'
  state.segments = {}
  state.reason = res.reason or ''
  state.siblings = res.siblings and #res.siblings or 0
  for _, kind in ipairs({ 'intro', 'credits' }) do
    local seg = res[kind]
    if type(seg) == 'table' and seg[1] and seg[2] then
      table.insert(state.segments, { type = kind, start = seg[1], ['end'] = seg[2] })
    end
  end
  if #state.segments > 0 then timer:resume() else timer:kill() end
  state.current = nil
  publish()
  set_button()
  tick()
  if refresh_menu then refresh_menu() end
end

local function request(quiet)
  if not rpc.connected() then return end
  local path = mp.get_property('path') or ''
  if not is_local(path) then return end
  local p = path:gsub('^file://', '')
  rpc.call('intro.segments', { path = p, notify = SCRIPT }, function(err, res)
    if err then
      state.status = 'error'
      state.last_error = err.message or tostring(err)
      publish()
      if not quiet then msg.info('intro.segments: ' .. state.last_error) end
      return
    end
    if res.status == 'analyzing' then
      state.status = 'analyzing'
      state.siblings = res.siblings and #res.siblings or 0
      publish()
      return
    end
    apply(res)
  end, 60)
end

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'intro' or type(ev.result) ~= 'table' then return end
  local p = (mp.get_property('path') or ''):gsub('^file://', '')
  if ev.result.path ~= p then return end
  apply(ev.result)
end)

mp.register_event('file-loaded', function()
  local path = mp.get_property('path') or ''
  state.path = path
  state.status = ''
  state.segments = {}
  state.current = nil
  state.skipped = {}
  state.hinted = {}
  state.reason = ''
  timer:kill()
  publish()
  set_button()
  if opts.enabled then request(true) end
end)

mp.register_event('end-file', function()
  timer:kill()
  state.segments = {}
  state.current = nil
  state.path = ''
  publish()
  set_button()
end)

mp.observe_property('user-data/mu/core', 'native', function(_, core)
  if core and core.uosc then set_button() end
  if core and core.mpvd == 'connected' and state.status == '' and (mp.get_property('path') or '') ~= '' then request(true) end
end)

set_button = function()
  if not uosc.available() then return end
  local inside = state.current ~= nil
  uosc.set_button('mu-skip', {
    icon = 'skip_next', active = inside, hide = not opts.enabled or #state.segments == 0,
    tooltip = inside and ((state.current == 'intro' and 'Saltar intro' or 'Saltar créditos') .. ' (alt+k)')
      or 'Saltar intro/créditos (alt+k)',
    badge = inside and (state.current == 'intro' and 'intro' or 'fin') or nil,
    command = { 'script-binding', SCRIPT .. '/skip' },
  })
end

-- ---------------------------------------------------------------------------------------------
-- menu (one level): current segments, skip now, automatic skipping, re-analyse

local MENU = 'mu-intro'
local EVENT = 'mu-intro-menu-event'

local function fmt_time(t)
  t = math.floor(tonumber(t) or 0)
  if t >= 3600 then return string.format('%d:%02d:%02d', t / 3600, (t % 3600) / 60, t % 60) end
  return string.format('%d:%02d', t / 60, t % 60)
end

local function yesno(b) return b and 'activado' or 'desactivado' end

local function menu_items()
  local items = {}
  local playing = (mp.get_property('path') or '') ~= ''
  if not playing then
    table.insert(items, { title = 'Abre un episodio de una serie (carpeta con varios vídeos)', icon = 'info',
      selectable = false, muted = true })
  elseif state.status == 'analyzing' then
    table.insert(items, { title = 'Analizando la carpeta…', hint = state.siblings .. ' episodios', icon = 'spinner',
      selectable = false, muted = true })
  elseif state.status == 'error' then
    table.insert(items, { title = 'Error: ' .. state.last_error, icon = 'error', selectable = false, muted = true })
  elseif #state.segments == 0 then
    table.insert(items, { title = 'Sin intro ni créditos detectados', hint = state.reason ~= '' and state.reason or nil,
      icon = 'info', selectable = false, muted = true })
  end
  for _, s in ipairs(state.segments) do
    table.insert(items, { title = (s.type == 'intro' and 'Intro' or 'Créditos') .. ' · ' .. fmt_time(s.start) .. ' → '
      .. fmt_time(s['end']), hint = string.format('%d s', math.floor(s['end'] - s.start + 0.5)),
      icon = s.type == 'intro' and 'play_circle' or 'movie', active = state.current == s.type,
      value = { seek = s.start } })
  end
  if #state.segments > 0 then
    table.insert(items, { title = 'Saltar ahora', hint = 'alt+k', icon = 'skip_next', value = { skip = true },
      separator = true })
  end
  table.insert(items, { title = 'Saltar la intro automáticamente', hint = yesno(opts.auto_skip_intro), icon = 'fast_forward',
    active = opts.auto_skip_intro, value = { toggle = 'auto_skip_intro' }, separator = #state.segments == 0 })
  table.insert(items, { title = 'Saltar los créditos automáticamente', hint = yesno(opts.auto_skip_credits),
    icon = 'last_page', active = opts.auto_skip_credits, value = { toggle = 'auto_skip_credits' } })
  table.insert(items, { title = 'Detección activada', hint = yesno(opts.enabled), icon = 'radar', active = opts.enabled,
    value = { toggle = 'enabled' } })
  if playing then
    table.insert(items, { title = 'Volver a analizar este episodio', hint = 'fpcalc + ffmpeg en segundo plano',
      icon = 'refresh', value = { reanalyze = true }, separator = true })
  end
  return items
end

local function base_menu()
  return { type = MENU, title = 'Saltar intro y créditos', items = menu_items(), callback = { SCRIPT, EVENT },
    keep_open = true, search_submenus = false, footnote = 'mpvd compara el audio con los otros episodios de la carpeta' }
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
  local path = mp.get_property('path') or ''
  if not is_local(path) then osd('Solo archivos locales') return end
  state.status = 'analyzing'
  state.segments = {}
  state.current = nil
  publish()
  set_button()
  refresh_menu()
  rpc.call('intro.analyze', { path = path:gsub('^file://', ''), notify = SCRIPT }, function(err)
    if err then
      state.status = 'error'
      state.last_error = err.message or tostring(err)
      publish()
      refresh_menu()
      osd('Análisis: ' .. state.last_error)
    end
  end, 30)
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  if ev.type ~= 'activate' then return end
  local v = type(ev.value) == 'table' and ev.value or {}
  if v.toggle then
    opts[v.toggle] = not opts[v.toggle]
    if v.toggle == 'enabled' then
      if opts.enabled then request(true) elseif #state.segments > 0 then timer:kill() end
    end
    publish()
    set_button()
    refresh_menu()
  elseif v.skip then
    uosc.close(MENU)
    skip(nil)
  elseif v.seek then
    mp.commandv('seek', tostring(v.seek), 'absolute')
    refresh_menu()
  elseif v.reanalyze then
    reanalyze()
  end
end)

mp.add_key_binding(nil, 'skip', function() skip(nil) end)
mp.add_key_binding(nil, 'intro-menu', open_menu)
mp.register_script_message('mu-intro-skip', function(kind) skip(kind ~= '' and kind or nil) end)
mp.register_script_message('mu-intro-refresh', function() request(false) end)
mp.register_script_message('mu-intro-set', function(key, value)
  if key == 'auto_skip_intro' or key == 'auto_skip_credits' or key == 'enabled' then
    opts[key] = (value == 'yes' or value == 'true')
    publish()
    set_button()
    refresh_menu()
  end
end)
mp.register_script_message('uosc-version', set_button)

publish()
msg.info('mu-intro loaded')
