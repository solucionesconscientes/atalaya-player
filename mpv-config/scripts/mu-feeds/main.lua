-- mu-feeds: «Suscripciones» (H23, ADR-059). The menu over mpvd's feeds.* (channels, lists and podcasts checked in the
-- background, downloaded at low priority when the rules allow it, trimmed and passed through the post-download chain).
--   · Root: «Añadir suscripción…» (paste a URL → feeds.detect → confirm name and how many to take → feeds.add), one row
--     per subscription (Tab: check now, pause/resume, delete), «Comprobar todas ahora», «Ajustes de suscripciones».
--   · Per subscription: check now, rules (quality, audio only, SponsorBlock, keep N, delete what was watched and the
--     chain after downloading: equal volume, AI subtitles + translation, rename, move to the library), pending and
--     downloaded episodes with the state of their chain (feeds.chain.status), rename, pause/resume, delete (confirmed).
--   · Settings: check interval, download window, limit per window (downloads / MB), metered pause, keep checking with
--     the player closed, parallel downloads, grace before deleting what was watched, the default chain.
--   · mpvd pushes `mu-event` {event:'feeds', state, subscription?}: the open menu refreshes (≤ refresh_hz) and new
--     episodes found by a check are announced on the OSD.
-- Bindings: feeds-menu. Script name: mu_feeds. State for the tests: user-data/mu/feeds.
local mp = require('mp')
local msg = require('mp.msg')
local utils = require('mp.utils')
local options = require('mp.options')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local rpc = require('mu.rpc')
local uosc = require('mu.uosc')
local nav = require('mu.nav')
local N = nav.new()

local SCRIPT = mp.get_script_name()
local MENU = 'mu-feeds'
local EVENT = 'mu-feeds-event'
local INPUT = 'mu-feeds-input'
local INPUT_EVENT = 'mu-feeds-input-event'
local ROOT_TITLE = 'Suscripciones'

local opts = {
  notify = true,          -- OSD when a check finds new episodes
  refresh_hz = 4,         -- max refresh rate of the open menu on mpvd events
  detect_timeout = 90,    -- seconds for feeds.detect / feeds.add (yt-dlp lists the channel)
}
options.read_options(opts, 'mu-feeds')

local state = {
  view = '', stack = {}, items = {}, force_open = false,
  list = nil,          -- last feeds.list: {subscriptions, settings, state, presets, rename_presets}
  byid = {},           -- id → public subscription (list, get, update and events keep it fresh)
  details = {},        -- id → last feeds.get (pending_entries, file_records)
  chains = {},         -- download id → feeds.chain.status
  folders = nil,       -- library folders (for «mover a la biblioteca»)
  input = nil,         -- palette {mode, query, id}
  add = nil,           -- {url, busy, error, kind, kind_label, title, entries, initial}
  checks = {},         -- id → last_check already seen (new-episode notices)
  last_error = '', last_notice = '', last_action = '',
}

local function osd(text, secs) mp.osd_message(text, secs or 3) end

local function fail(err, what, show_osd)
  local m = err and (err.message or tostring(err)) or 'error'
  msg.warn(what .. ': ' .. m)
  state.last_error = what .. ': ' .. m
  if show_osd ~= false then osd('Suscripciones: ' .. m, 4) end
  return m
end

local function copy(t)
  local out = {}
  for k, v in pairs(t or {}) do out[k] = v end
  return out
end

local function basename(p) return (tostring(p or ''):match('[^/\\]+$')) or tostring(p or '') end

local function subs() return state.list and state.list.subscriptions or {} end
local function settings() return state.list and state.list.settings or {} end

-- ---------------------------------------------------------------------------------------------
-- published state (tests)

local function compact_subs()
  local out = {}
  for _, s in ipairs(subs()) do
    out[#out + 1] = { id = s.id, title = s.title, kind = s.kind, paused = s.paused, status = s.status,
                      pending = s.pending, files = s.files, preset = s.preset, keep = s.keep,
                      keep_watched_only = s.keep_watched_only, delete_watched = s.delete_watched,
                      sponsorblock = s.sponsorblock, chain = s.chain, last_found = s.last_found,
                      last_error = s.last_error }
  end
  return out
end

local function publish()
  local a = state.add or {}
  mp.set_property_native('user-data/mu/feeds', {
    view = state.view, depth = #state.stack, items = state.items, subscriptions = compact_subs(),
    settings = settings(), state = state.list and state.list.state or {}, input = state.input and state.input.mode or '',
    add = { url = a.url or '', title = a.title or '', kind = a.kind or '', initial = a.initial or 0,
            busy = a.busy or false, error = a.error or '' },
    last_error = state.last_error, last_notice = state.last_notice, last_action = state.last_action,
  })
end

-- ---------------------------------------------------------------------------------------------
-- labels

local KIND_ICON = { channel = 'subscriptions', playlist = 'playlist_play', rss = 'podcasts' }
local STATUS_TEXT = { checking = 'comprobando…', downloading = 'descargando', paused = 'en pausa', idle = 'al día' }
local LANGS = { '', 'es', 'en', 'fr', 'de', 'it', 'pt' }
local LANG_NAME = { es = 'español', en = 'inglés', fr = 'francés', de = 'alemán', it = 'italiano', pt = 'portugués' }
local SPONSOR = { '', 'remove', 'mark', 'none' }
local SPONSOR_TEXT = { [''] = 'como en Descargas', remove = 'quitarlos', mark = 'marcarlos como capítulos',
                       none = 'no' }
local KEEP = { 0, 3, 5, 10, 20, 50 }
local INITIAL = { 3, 0, 1, 5, 10, -1 }
local FIELD_NAME = { date = 'fecha', title = 'título', uploader = 'autor', feed = 'suscripción', id = 'id' }
local WINDOWS = {
  { '', 'Siempre' }, { '01:00-07:00', 'De 1:00 a 7:00' }, { '02:00-06:00', 'De 2:00 a 6:00' },
  { '00:00-08:00', 'De 0:00 a 8:00' }, { '23:00-07:00', 'De 23:00 a 7:00' }, { '09:00-17:00', 'De 9:00 a 17:00' },
}
local SETTING_CYCLES = {
  interval_h = { 0.5, 1, 2, 4, 6, 12, 24 },
  max_items = { 0, 1, 2, 3, 5, 10, 20 },
  max_mb = { 0, 500, 1000, 2000, 5000, 10000, 20000 },
  parallel = { 1, 2, 3 },
  watched_grace_h = { 24, 0, 6, 72, 168 },
}
local SETTING_TOGGLES = { pause_metered = true, keep_running = true, chain_downloads = true }

local function yes(v) return v and 'sí' or 'no' end

local function plural(n, one, many) return tostring(n) .. ' ' .. (n == 1 and one or many) end

local function status_text(s)
  if s.status == 'pending' then return plural(s.pending or 0, 'pendiente', 'pendientes') end
  local t = STATUS_TEXT[s.status] or tostring(s.status or '')
  if s.status == 'downloading' and (s.pending or 0) > 0 then t = t .. ' · ' .. s.pending .. ' en cola' end
  return t
end

local function ago(ts)
  ts = tonumber(ts) or 0
  if ts <= 0 then return 'nunca' end
  local d = math.max(0, os.time() - ts)
  if d < 60 then return 'ahora mismo' end
  if d < 3600 then return 'hace ' .. math.floor(d / 60) .. ' min' end
  if d < 86400 then return 'hace ' .. math.floor(d / 3600) .. ' h' end
  return 'hace ' .. plural(math.floor(d / 86400), 'día', 'días')
end

local function hours_label(h)
  h = tonumber(h) or 0
  if h <= 0 then return 'enseguida' end
  if h < 1 then return math.floor(h * 60 + 0.5) .. ' min' end
  if h >= 168 and h % 168 == 0 then return plural(h / 168, 'semana', 'semanas') end
  if h >= 24 and h % 24 == 0 then return plural(h / 24, 'día', 'días') end
  return (h == math.floor(h) and string.format('%d', h) or tostring(h)) .. ' h'
end

local function keep_label(n)
  n = tonumber(n) or 0
  if n <= 0 then return 'todo' end
  return n == 1 and 'el más nuevo' or ('los ' .. n .. ' más nuevos')
end

local function initial_label(n)
  n = tonumber(n) or 0
  if n < 0 then return 'todo lo que haya' end
  if n == 0 then return 'nada, solo lo que salga' end
  return n == 1 and 'el último' or ('los ' .. n .. ' últimos')
end

local function mb_label(n)
  n = tonumber(n) or 0
  if n <= 0 then return 'sin límite' end
  if n >= 1000 then return (n % 1000 == 0 and string.format('%d', n / 1000) or string.format('%.1f', n / 1000)) .. ' GB' end
  return n .. ' MB'
end

local function items_label(n)
  n = tonumber(n) or 0
  return n <= 0 and 'sin límite' or plural(n, 'descarga', 'descargas')
end

local function template_label(tpl)
  if not tpl or tpl == '' then return 'nombre original' end
  return (tpl:gsub('{(%w+)}', function(f) return FIELD_NAME[f] or f end))
end

local function preset_short(s)
  local t = tostring(s.preset_title or s.preset or '')
  return (t:gsub('%s*%(recomendado%)', ''):gsub(', recomendado%)', ')'))
end

local function is_audio(s) return tostring(s.preset or ''):sub(1, 5) == 'audio' end

local function window_text(st)
  local w = (state.list and state.list.state or {}).window_text
  if w and w ~= '' then return w end
  st = st or settings()
  if not st.window or st.window == '' then return 'siempre' end
  return st.window
end

local function metered_text(m)
  if m == true then return 'ahora: medida' end
  if m == false then return 'ahora: normal' end
  return 'no se sabe'
end

local function state_text()
  local st = state.list and state.list.state
  if type(st) ~= 'table' then return nil end
  if st.allowed == false and (st.reason or '') ~= '' then return 'En espera: ' .. st.reason end
  local parts = {}
  if (st.inflight or 0) > 0 then parts[#parts + 1] = 'descargando ' .. st.inflight end
  if (st.chains or 0) > 0 then parts[#parts + 1] = 'preparando ' .. st.chains .. ' (tras descargar)' end
  if #parts == 0 then return 'Nada descargándose ahora' end
  return 'Ahora: ' .. table.concat(parts, ' · ')
end

local function cycle(list, cur)
  local idx = 0
  for i, v in ipairs(list) do
    if v == cur or (type(v) == 'number' and type(cur) == 'number' and math.abs(v - cur) < 1e-6) then idx = i end
  end
  return list[idx % #list + 1]
end

-- ---------------------------------------------------------------------------------------------
-- cache

local function sort_subs()
  if not state.list then return end
  table.sort(state.list.subscriptions, function(a, b)
    return tostring(a.title or ''):lower() < tostring(b.title or ''):lower()
  end)
end

local function notice_new(s)
  local prev = state.checks[s.id]
  local now = tonumber(s.last_check) or 0
  state.checks[s.id] = now
  -- a check that just ended (not old news pushed again) and found something
  if now <= (prev or 0) or os.time() - now > 120 or (tonumber(s.last_found) or 0) <= 0 then return end
  state.last_notice = string.format('%s: %s', s.title or '', plural(s.last_found, 'nuevo', 'nuevos'))
  if opts.notify then osd('Suscripciones · ' .. state.last_notice) end
end

local function store_sub(s, announce)
  if type(s) ~= 'table' or not s.id then return end
  if announce then notice_new(s) elseif state.checks[s.id] == nil then state.checks[s.id] = tonumber(s.last_check) or 0 end
  state.byid[s.id] = s
  if not state.list then return end
  local list = state.list.subscriptions
  for i, x in ipairs(list) do
    if x.id == s.id then list[i] = s; return end
  end
  list[#list + 1] = s
  sort_subs()
end

local function drop_sub(id)
  state.byid[id], state.details[id] = nil, nil
  if not state.list then return end
  local list = state.list.subscriptions
  for i = #list, 1, -1 do if list[i].id == id then table.remove(list, i) end end
end

local function set_list(res)
  state.list = res
  state.list.subscriptions = res.subscriptions or {}
  state.byid = {}
  for _, s in ipairs(state.list.subscriptions) do
    state.byid[s.id] = s
    -- the first sight of each subscription only records where it is (no notice for old news)
    if state.checks[s.id] == nil then state.checks[s.id] = tonumber(s.last_check) or 0 end
  end
end

local function fetch_list(done)
  if not rpc.connected() then done() return end
  rpc.call('feeds.list', nil, function(err, res)
    if err then fail(err, 'feeds.list', false) else set_list(res) end
    publish()
    done()
  end, 20)
end

local function fetch_sub(id, done)
  if not rpc.connected() then done() return end
  rpc.call('feeds.get', { id = id }, function(err, res)
    if err then
      fail(err, 'feeds.get', false)
      if err.code == -32001 or tostring(err.message or ''):find('no existe') then drop_sub(id) end
    else
      state.details[id] = res
      store_sub(res)
    end
    publish()
    done()
  end, 20)
end

-- ---------------------------------------------------------------------------------------------
-- menu plumbing (same pattern as mu-convert / mu-share)

local function remember(items)
  local out = {}
  for i, it in ipairs(items or {}) do
    if i > 200 then break end
    local names = {}
    for _, a in ipairs(it.actions or {}) do names[#names + 1] = a.name end
    out[#out + 1] = { title = it.title or '', hint = it.hint or '', icon = it.icon or '', value = it.value or '',
                      actions = names, active = it.active or false }
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

local function render(spec)
  state.view = spec.name
  views[spec.name].render(spec.args or {})
end

-- push (or re-show) a view: drawn at once from the cache, then again when mpvd answers (if it is still on top)
local function open_view(spec, push)
  if push ~= false then table.insert(state.stack, spec) end
  state.view = spec.name
  publish()
  local v = views[spec.name]
  v.render(spec.args or {})
  if v.fetch then
    v.fetch(spec.args or {}, function()
      if state.stack[#state.stack] == spec then render(spec) end
    end)
  end
end

local function reopen_current(force)
  local spec = state.stack[#state.stack]
  if spec then
    state.force_open = force or false
    open_view(spec, false)
  end
end

-- redraw the view on top from the cache (after an action answered)
local function rerender()
  local spec = state.stack[#state.stack]
  if spec then render(spec) end
end

local function go_back()
  table.remove(state.stack)
  if #state.stack == 0 then
    if not N:leave() then uosc.close(MENU) end
  else
    reopen_current()
  end
end

local refresh_timer = nil
local REFRESHED = { root = true, sub = true, rules = true, files = true, settings = true }
local function schedule_refresh()
  if refresh_timer then return end
  refresh_timer = mp.add_timeout(1 / math.max(1, opts.refresh_hz), function()
    refresh_timer = nil
    local spec = state.stack[#state.stack]
    if spec and REFRESHED[spec.name] and uosc.open_type() == MENU then render(spec) end
  end)
end

local function not_connected(title)
  if rpc.connected() then return false end
  show(title, uosc.message_items('mpvd no está conectado: espera unos segundos', 'error'))
  return true
end

-- ---------------------------------------------------------------------------------------------
-- views

local ROW_ACTIONS = {
  check = { name = 'check', icon = 'refresh', label = 'Comprobar ahora' },
  pause = { name = 'pause', icon = 'pause', label = 'Pausar' },
  resume = { name = 'resume', icon = 'play_arrow', label = 'Reanudar' },
  remove = { name = 'remove', icon = 'delete', label = 'Borrar' },
}

views.root = {
  fetch = function(_, done) fetch_list(done) end,
  render = function()
    if not_connected(ROOT_TITLE) then return end
    if not state.list then show(ROOT_TITLE, uosc.loading_items('Cargando suscripciones…')) return end
    local items = {
      { title = 'Añadir suscripción…', hint = 'canal, lista o podcast', icon = 'add', value = { add = true } },
    }
    for _, s in ipairs(subs()) do
      local hint = (s.kind_label or s.kind or '') .. ' · ' .. status_text(s)
      if (s.last_error or '') ~= '' and s.status ~= 'checking' then hint = hint .. ' · error' end
      items[#items + 1] = {
        title = s.title, hint = hint, icon = s.paused and 'pause_circle' or (KIND_ICON[s.kind] or 'rss_feed'),
        value = { view = 'sub', id = s.id }, muted = s.paused or nil,
        actions = { ROW_ACTIONS.check, s.paused and ROW_ACTIONS.resume or ROW_ACTIONS.pause, ROW_ACTIONS.remove },
      }
    end
    if #subs() == 0 then
      items[#items + 1] = { title = 'Aún no sigues nada: pega la dirección de un canal, una lista o un podcast',
                            icon = 'info', muted = true, selectable = false }
    end
    items[#items].separator = true
    items[#items + 1] = { title = 'Comprobar todas ahora', icon = 'refresh', value = { check_all = true },
                          keep_open = true }
    items[#items + 1] = { title = 'Ajustes de suscripciones', icon = 'tune', hint = 'horario: ' .. window_text(),
                          value = { view = 'settings' } }
    local st = state_text()
    if st then items[#items + 1] = { title = st, icon = 'schedule', muted = true, selectable = false } end
    show(ROOT_TITLE, items, { footnote = 'Enter abre · Tab: comprobar, pausar, borrar · ⌫ atrás' })
  end,
}

local function gone(title)
  show(title or 'Suscripción', uosc.message_items('Esta suscripción ya no existe', 'delete'))
end

views.sub = {
  fetch = function(args, done) fetch_sub(args.id, done) end,
  render = function(args)
    local s = state.byid[args.id]
    if not s then gone() return end
    local info = (s.kind_label or s.kind) .. ' · ' .. status_text(s) .. ' · comprobada ' .. ago(s.last_check)
    local items = { { title = info, icon = KIND_ICON[s.kind] or 'rss_feed', muted = true, selectable = false } }
    if (s.last_error or '') ~= '' then
      items[#items + 1] = { title = 'Último error: ' .. s.last_error, icon = 'error', muted = true, selectable = false }
    end
    items[#items].separator = true
    items[#items + 1] = { title = 'Comprobar ahora', icon = 'refresh', value = { check = s.id }, keep_open = true }
    local rules = preset_short(s)
    if (s.keep or 0) > 0 then rules = rules .. ' · conservar ' .. s.keep end
    items[#items + 1] = { title = 'Reglas', icon = 'rule', hint = rules, value = { view = 'rules', id = s.id } }
    items[#items + 1] = { title = 'Pendientes y descargados', icon = 'video_library',
                          hint = (s.pending or 0) .. ' pendientes · ' .. (s.files or 0) .. ' guardados',
                          value = { view = 'files', id = s.id } }
    items[#items + 1] = { title = 'Cambiar el nombre…', icon = 'edit', value = { rename = s.id } }
    items[#items + 1] = { title = s.paused and 'Reanudar' or 'Pausar', icon = s.paused and 'play_arrow' or 'pause',
                          hint = s.paused and 'ahora en pausa' or nil,
                          value = { pause = s.id, paused = not s.paused }, keep_open = true, separator = true }
    items[#items + 1] = { title = 'Borrar la suscripción', icon = 'delete', value = { view = 'remove', id = s.id } }
    show(s.title, items, { footnote = 'Enter elige · ⌫ atrás · Esc cierra' })
  end,
}

-- chain rows shared by a subscription (id) and the default chain (global = true)
local function chain_rows(chain, ref)
  chain = chain or {}
  local function v(key) local t = copy(ref); t.chain = key; return t end
  local move = copy(ref)
  move.view = 'move'
  local items = {
    { title = 'Tras descargar', icon = 'auto_fix_high', muted = true, selectable = false },
    { title = 'Volumen igualado', icon = 'graphic_eq', hint = yes(chain.loudnorm), value = v('loudnorm') },
    { title = 'Subtítulos IA', icon = 'subtitles', hint = yes(chain.subtitles), value = v('subtitles') },
  }
  if chain.subtitles then
    items[#items + 1] = { title = 'Traducir los subtítulos', icon = 'translate',
                          hint = (chain.translate or '') ~= '' and (LANG_NAME[chain.translate] or chain.translate) or 'no',
                          value = v('translate') }
  end
  items[#items + 1] = { title = 'Renombrar', icon = 'drive_file_rename_outline', hint = template_label(chain.rename),
                        value = v('rename') }
  items[#items + 1] = { title = 'Mover a la biblioteca', icon = 'drive_file_move',
                        hint = (chain.move_to or '') ~= '' and chain.move_to or 'no', value = move }
  return items
end

views.rules = {
  fetch = function(args, done) fetch_sub(args.id, done) end,
  render = function(args)
    local s = state.byid[args.id]
    if not s then gone('Reglas') return end
    local items = {
      { title = 'Calidad', icon = 'high_quality', hint = preset_short(s), value = { view = 'quality', id = s.id } },
      { title = 'Solo audio', icon = 'headphones', hint = yes(is_audio(s)), value = { rule = 'audio', id = s.id } },
    }
    if s.kind ~= 'rss' then
      items[#items + 1] = { title = 'Saltar patrocinios (SponsorBlock)', icon = 'content_cut',
                            hint = SPONSOR_TEXT[s.sponsorblock or ''] or s.sponsorblock,
                            value = { rule = 'sponsorblock', id = s.id } }
    end
    items[#items + 1] = { title = 'Conservar', icon = 'inventory_2', hint = keep_label(s.keep),
                          value = { rule = 'keep', id = s.id } }
    if (s.keep or 0) > 0 then
      items[#items + 1] = { title = 'Al quitar, solo lo ya visto', icon = 'visibility',
                            hint = yes(s.keep_watched_only), value = { rule = 'keep_watched_only', id = s.id } }
    end
    items[#items + 1] = { title = 'Borrar lo que ya hayas visto', icon = 'auto_delete',
                          hint = s.delete_watched and ('sí · tras ' .. hours_label(settings().watched_grace_h or 24))
                            or 'no',
                          value = { rule = 'delete_watched', id = s.id }, separator = true }
    for _, it in ipairs(chain_rows(s.chain, { id = s.id })) do items[#items + 1] = it end
    items[#items].separator = true
    items[#items + 1] = { title = 'Aplicar ya «conservar» y «borrar lo visto»', icon = 'cleaning_services',
                          value = { apply = s.id }, keep_open = true }
    show('Reglas', items, { footnote = 'Enter cambia · se guarda al momento · ⌫ atrás' })
  end,
}

views.quality = {
  render = function(args)
    local s = state.byid[args.id]
    if not s then gone('Calidad') return end
    local items = {}
    for _, p in ipairs(state.list and state.list.presets or {}) do
      items[#items + 1] = { title = p.title, active = p.id == s.preset or nil,
                            icon = p.id:sub(1, 5) == 'audio' and 'music_note' or 'movie',
                            value = { set_preset = p.id, id = s.id } }
    end
    if #items == 0 then items = uosc.message_items('No hay calidades: ¿está mpvd conectado?', 'error') end
    show('Calidad', items)
  end,
}

views.move = {
  fetch = function(_, done)
    if state.folders or not rpc.connected() then done() return end
    rpc.call('library.folders.list', nil, function(err, res)
      state.folders = (not err and type(res) == 'table') and res or {}
      done()
    end, 10)
  end,
  render = function(args)
    local chain
    if args.global then chain = settings().chain or {} else
      local s = state.byid[args.id]
      if not s then gone('Mover a la biblioteca') return end
      chain = s.chain or {}
    end
    local cur = chain.move_to or ''
    local function v(path) return { move = path, id = args.id, global = args.global } end
    local items = { { title = 'No mover', icon = 'block', active = cur == '' or nil, value = v('') } }
    if not state.folders then
      items[#items + 1] = { title = 'Buscando las carpetas de la biblioteca…', icon = 'spinner', muted = true,
                            selectable = false }
    end
    for _, f in ipairs(state.folders or {}) do
      items[#items + 1] = { title = basename(f.path), hint = f.path, icon = 'folder', active = f.path == cur or nil,
                            value = v(f.path) }
    end
    if state.folders and #state.folders == 0 then
      items[#items + 1] = { title = 'Tu biblioteca aún no tiene carpetas', icon = 'info', muted = true,
                            selectable = false }
    end
    items[#items].separator = true
    items[#items + 1] = { title = 'Otra carpeta…', icon = 'edit', hint = cur ~= '' and cur or nil,
                          value = { move_input = true, id = args.id, global = args.global } }
    show('Mover a la biblioteca', items, { footnote = 'Cada suscripción va a su propia subcarpeta' })
  end,
}

local function chain_state(res)
  local post = type(res) == 'table' and res.post or nil
  if type(post) ~= 'table' or type(post.steps) ~= 'table' or #post.steps == 0 then return 'listo' end
  if post.status == 'running' or post.status == 'pending' then
    for _, st in ipairs(post.steps) do
      if st.status == 'running' then return 'ahora: ' .. (st.label or st.id) end
    end
    return 'preparando…'
  end
  local bad = {}
  for _, st in ipairs(post.steps) do if st.status == 'failed' then bad[#bad + 1] = st.label or st.id end end
  if #bad > 0 then return 'falló: ' .. table.concat(bad, ', ') end
  return 'listo · ' .. plural(#post.steps, 'paso', 'pasos')
end

views.files = {
  fetch = function(args, done)
    fetch_sub(args.id, function()
      local d = state.details[args.id]
      local recs = d and d.file_records or {}
      local ids = {}
      for i = #recs, math.max(1, #recs - 9), -1 do
        if (recs[i].download or '') ~= '' then ids[#ids + 1] = recs[i].download end
      end
      if #ids == 0 then done() return end
      local left = #ids
      for _, did in ipairs(ids) do
        rpc.call('feeds.chain.status', { id = did }, function(err, res)
          state.chains[did] = err and { error = err.message } or res
          left = left - 1
          if left == 0 then done() end
        end, 10)
      end
    end)
  end,
  render = function(args)
    local s = state.byid[args.id]
    if not s then gone('Pendientes y descargados') return end
    local d = state.details[args.id] or {}
    local items = {}
    local pend = d.pending_entries or {}
    if #pend > 0 then
      items[#items + 1] = { title = 'Pendientes', icon = 'schedule', hint = tostring(#pend), muted = true,
                            selectable = false }
      for i, e in ipairs(pend) do
        if i > 30 then break end
        items[#items + 1] = { title = e.title or e.url or '?', icon = 'hourglass_empty', muted = true,
                              selectable = false }
      end
      items[#items].separator = true
    end
    local recs = d.file_records or {}
    if #recs > 0 then
      items[#items + 1] = { title = 'Descargados', icon = 'download_done', hint = tostring(#recs), muted = true,
                            selectable = false }
      for i = #recs, math.max(1, #recs - 29), -1 do
        local r = recs[i]
        local cs = r.download and state.chains[r.download]
        items[#items + 1] = { title = r.title or basename(r.path), icon = 'play_circle',
                              hint = cs and (cs.error and 'sin datos' or chain_state(cs)) or nil,
                              value = { play = r.path } }
      end
    end
    if #items == 0 then
      items = uosc.message_items(s.status == 'checking' and 'Comprobando…' or 'Nada pendiente ni descargado todavía',
                                 'inbox')
    end
    show('Pendientes y descargados', items, { footnote = 'Enter reproduce · a la derecha, cómo va «tras descargar»' })
  end,
}

views.remove = {
  render = function(args)
    local s = state.byid[args.id]
    if not s then gone('Borrar') return end
    show('Borrar «' .. s.title .. '»', {
      { title = 'Sí, borrar la suscripción', hint = 'los archivos descargados se quedan', icon = 'delete',
        value = { remove = s.id } },
      { title = 'No, mantenerla', icon = 'close', value = { nav_back = true } },
    })
  end,
}

views.add = {
  fetch = function(_, done)
    local a = state.add
    if not a or not a.busy then done() return end
    rpc.call('feeds.detect', { url = a.url }, function(err, res)
      if state.add ~= a then return end
      a.busy = false
      if err then
        a.error = fail(err, 'feeds.detect', false)
      else
        a.kind, a.kind_label, a.url_norm = res.kind, (res.kind == 'rss' and 'podcast')
          or (res.kind == 'channel' and 'canal') or 'lista', res.url
        a.title, a.entries = res.title or a.url, res.entries or {}
        a.initial = res.kind == 'playlist' and -1 or 3
      end
      publish()
      done()
    end, opts.detect_timeout)
  end,
  render = function()
    local a = state.add
    if not a then show('Añadir suscripción', uosc.message_items('Nada que añadir', 'info')) return end
    if a.busy then show('Añadir suscripción', uosc.loading_items('Mirando qué es… (puede tardar un poco)')) return end
    if a.error then
      show('Añadir suscripción', {
        { title = 'No se puede seguir esta dirección', icon = 'error', muted = true, selectable = false },
        { title = a.error, muted = true, selectable = false, separator = true },
        { title = 'Probar otra dirección…', icon = 'edit', value = { add = true } },
      })
      return
    end
    local items = {
      { title = 'Suscribirse', icon = 'add_task', bold = true, hint = a.kind_label, value = { subscribe = true },
        separator = true },
      { title = 'Nombre', icon = 'edit', hint = a.title, value = { name_input = true } },
      { title = 'Es un ' .. a.kind_label, icon = KIND_ICON[a.kind] or 'rss_feed', muted = true, selectable = false },
      { title = 'Al suscribirte, descargar', icon = 'download', hint = initial_label(a.initial),
        value = { initial = true }, separator = true },
    }
    if #(a.entries or {}) > 0 then
      items[#items + 1] = { title = 'Lo más reciente', icon = 'new_releases', muted = true, selectable = false }
      for _, e in ipairs(a.entries) do
        items[#items + 1] = { title = e.title or e.url or '?', icon = 'movie', muted = true, selectable = false }
      end
    end
    show('Añadir suscripción', items, { footnote = 'Enter elige · ⌫ atrás' })
  end,
}

views.settings = {
  fetch = function(_, done) fetch_list(done) end,
  render = function()
    if not_connected('Ajustes de suscripciones') then return end
    if not state.list then show('Ajustes de suscripciones', uosc.loading_items()) return end
    local st = settings()
    local fs = state.list.state or {}
    local chain = st.chain or {}
    local steps = {}
    if chain.loudnorm then steps[#steps + 1] = 'volumen' end
    if chain.subtitles then steps[#steps + 1] = 'subtítulos' end
    if (chain.rename or '') ~= '' then steps[#steps + 1] = 'nombre' end
    if (chain.move_to or '') ~= '' then steps[#steps + 1] = 'mover' end
    local items = {
      { title = 'Comprobar cada', icon = 'update', hint = hours_label(st.interval_h), value = { set = 'interval_h' } },
      { title = 'Horario de descarga', icon = 'schedule', hint = window_text(st), value = { view = 'window' } },
      { title = 'Límite por franja', icon = 'filter_list', hint = items_label(st.max_items),
        value = { set = 'max_items' } },
      { title = 'Límite de datos por franja', icon = 'data_usage', hint = mb_label(st.max_mb),
        value = { set = 'max_mb' } },
      { title = 'Pausar con conexión medida', icon = 'signal_cellular_alt',
        hint = yes(st.pause_metered) .. ' · ' .. metered_text(fs.metered), value = { set = 'pause_metered' } },
      { title = 'Seguir comprobando con el reproductor cerrado', icon = 'nightlight',
        hint = yes(st.keep_running), value = { set = 'keep_running' } },
      { title = 'Descargas a la vez', icon = 'downloading', hint = tostring(st.parallel or 1),
        value = { set = 'parallel' } },
      { title = 'Esperar antes de borrar lo visto', icon = 'hourglass_bottom', hint = hours_label(st.watched_grace_h),
        value = { set = 'watched_grace_h' }, separator = true },
      { title = 'Tras descargar (para las nuevas)', icon = 'auto_fix_high',
        hint = #steps > 0 and table.concat(steps, ', ') or 'nada', value = { view = 'chain_global' } },
      { title = 'También en las descargas normales', icon = 'download', hint = yes(st.chain_downloads),
        value = { set = 'chain_downloads' } },
    }
    local sx = state_text()
    if sx then
      items[#items].separator = true
      items[#items + 1] = { title = sx, icon = 'info', muted = true, selectable = false }
    end
    show('Ajustes de suscripciones', items, { footnote = 'Enter cambia · se guarda al momento · ⌫ atrás' })
  end,
}

views.window = {
  render = function()
    local cur = settings().window or ''
    local items = {}
    for _, w in ipairs(WINDOWS) do
      items[#items + 1] = { title = w[2], icon = w[1] == '' and 'all_inclusive' or 'schedule',
                            active = cur == w[1] or nil, value = { window = w[1] } }
    end
    items[#items].separator = true
    local known = false
    for _, w in ipairs(WINDOWS) do if w[1] == cur then known = true end end
    items[#items + 1] = { title = 'Otra franja…', icon = 'edit', hint = not known and window_text() or nil,
                          active = not known or nil, value = { window_input = true } }
    show('Horario de descarga', items, { footnote = 'Fuera de la franja solo se comprueba, no se descarga' })
  end,
}

views.chain_global = {
  render = function()
    local items = chain_rows(settings().chain, { global = true })
    table.remove(items, 1)   -- the «Tras descargar» heading is this view's title
    items[#items + 1] = { title = 'Cuenta para las suscripciones sin cambios propios', icon = 'info', muted = true,
                          selectable = false }
    show('Tras descargar', items, { footnote = 'Enter cambia · se guarda al momento · ⌫ atrás' })
  end,
}

-- ---------------------------------------------------------------------------------------------
-- actions

local function update_sub(id, changes, cb)
  local params = copy(changes)
  params.id = id
  rpc.call('feeds.update', params, function(err, res)
    if err then fail(err, 'feeds.update') else
      store_sub(res)
      state.last_error = ''
    end
    publish()
    if cb then cb(err, res) end
    rerender()
  end)
end

local function set_settings(values, cb)
  rpc.call('feeds.settings.set', values, function(err, res)
    if err then fail(err, 'feeds.settings.set') elseif state.list then
      state.list.settings = res
      state.last_error = ''
    end
    publish()
    if cb then cb(err, res) end
    if not err then
      -- the window text and «why nothing downloads» come with the state
      rpc.call('feeds.state', nil, function(e2, st)
        if not e2 and state.list then state.list.state = st end
        publish()
        rerender()
      end)
    end
    rerender()
  end)
end

local function check(id)
  local s = state.byid[id]
  rpc.call('feeds.check', { id = id }, function(err)
    if err then fail(err, 'feeds.check') return end
    state.last_action = 'check:' .. id
    publish()
    osd('Comprobando «' .. (s and s.title or '') .. '»…')
  end, 20)
end

local function check_all()
  rpc.call('feeds.check', nil, function(err, res)
    if err then fail(err, 'feeds.check') return end
    local n = #(res.jobs or {})
    state.last_action = 'check-all:' .. n
    publish()
    osd(n > 0 and ('Comprobando ' .. plural(n, 'suscripción', 'suscripciones') .. '…')
      or 'No hay suscripciones activas que comprobar')
  end, 20)
end

local function pause(id, paused)
  rpc.call('feeds.pause', { id = id, paused = paused }, function(err, res)
    if err then fail(err, 'feeds.pause') return end
    store_sub(res)
    state.last_action = (paused and 'pause:' or 'resume:') .. id
    publish()
    osd((paused and 'En pausa: ' or 'Reanudada: ') .. (res.title or ''))
    rerender()
  end)
end

local function remove(id)
  local s = state.byid[id]
  rpc.call('feeds.remove', { id = id }, function(err)
    if err then fail(err, 'feeds.remove') return end
    drop_sub(id)
    state.last_action = 'remove:' .. id
    osd('Suscripción borrada: ' .. (s and s.title or '') .. ' (los archivos se quedan)')
    -- back to the list, whatever was open for it
    while #state.stack > 1 do table.remove(state.stack) end
    if #state.stack == 0 then state.stack = { { name = 'root' } } end
    reopen_current()
  end)
end

local function subscribe()
  local a = state.add
  if not a or a.busy or a.error then return end
  a.busy = true
  publish()
  show('Añadir suscripción', uosc.loading_items('Suscribiendo…'))
  rpc.call('feeds.add', { url = a.url, title = a.title, rules = { initial = a.initial } }, function(err, res)
    a.busy = false
    if err then
      a.error = fail(err, 'feeds.add')
      publish()
      rerender()
      return
    end
    state.add = nil
    store_sub(res)
    state.last_action = 'add:' .. res.id
    osd('Suscrito a «' .. res.title .. '»: comprobando…')
    -- the confirmation view is replaced by the new subscription
    if state.stack[#state.stack] and state.stack[#state.stack].name == 'add' then table.remove(state.stack) end
    open_view({ name = 'sub', args = { id = res.id } })
  end, opts.detect_timeout)
end

local function change_rule(id, rule)
  local s = state.byid[id]
  if not s then return end
  local ch = {}
  if rule == 'audio' then
    ch.preset = is_audio(s) and 'video_1080' or 'audio_original'
  elseif rule == 'sponsorblock' then
    ch.sponsorblock = cycle(SPONSOR, s.sponsorblock or '')
  elseif rule == 'keep' then
    ch.keep = cycle(KEEP, s.keep or 0)
  elseif rule == 'keep_watched_only' or rule == 'delete_watched' then
    ch[rule] = not s[rule]
  else
    return
  end
  update_sub(id, ch)
end

local function next_chain(chain, key)
  local c = copy(chain)
  if key == 'loudnorm' or key == 'subtitles' then
    c[key] = not c[key]
  elseif key == 'translate' then
    c.translate = cycle(LANGS, c.translate or '')
  elseif key == 'rename' then
    local list = { '' }
    for _, t in ipairs(state.list and state.list.rename_presets or {}) do list[#list + 1] = t end
    c.rename = cycle(list, c.rename or '')
  end
  return c
end

-- a subscription always gets its whole chain (a partial one would start from the defaults, not the general one)
local function set_chain(ref, chain)
  if ref.global then
    set_settings({ chain = chain })
  else
    update_sub(ref.id, { chain = chain })
  end
end

local function current_chain(ref)
  if ref.global then return settings().chain or {} end
  local s = state.byid[ref.id]
  return s and s.chain or {}
end

local function change_setting(key)
  local st = settings()
  local v
  if SETTING_TOGGLES[key] then v = not st[key] else
    local list = SETTING_CYCLES[key]
    if not list then return end
    v = cycle(list, tonumber(st[key]) or list[1])
  end
  set_settings({ [key] = v })
end

-- ---------------------------------------------------------------------------------------------
-- text box (a uosc palette whose query is the text)

local INPUT_TITLES = {
  url = 'Dirección del canal, la lista o el podcast', name = 'Nombre de la suscripción',
  title = 'Nombre nuevo', window = 'Franja de descarga (ejemplo: de 1:00 a 7:00)', move = 'Carpeta a la que mover',
}
local INPUT_EMPTY = {
  url = 'Pega la dirección (Ctrl+V): YouTube, una lista o el RSS de un podcast', name = 'Escribe el nombre',
  title = 'Escribe el nombre', window = 'Escribe la franja: «de 1:00 a 7:00», «23 a 6»…',
  move = 'Escribe o pega la ruta de la carpeta',
}

local function input_menu(query)
  state.input.query = query or ''
  local mode = state.input.mode
  local items
  if query ~= '' then
    items = { { title = (mode == 'url' and 'Seguir: ' or 'Usar: ') .. query, icon = 'check', value = { save = query } } }
  else
    items = { { title = INPUT_EMPTY[mode], icon = 'edit', selectable = false, muted = true } }
  end
  return { type = INPUT, title = INPUT_TITLES[mode], items = items, callback = { SCRIPT, INPUT_EVENT },
    search_style = 'palette', search_debounce = 0, on_search = 'callback', on_close = 'callback',
    search_suggestion = query, footnote = 'Enter elige · ⌫ en vacío vuelve' }
end

local function open_input(mode, text, extra)
  state.input = { mode = mode, query = text or '' }
  for k, v in pairs(extra or {}) do state.input[k] = v end
  publish()
  uosc.open(input_menu(text or ''))
end

local function http_path()
  local p = mp.get_property('path') or ''
  if p:match('^https?://') then return p end
  return ''
end

local function input_saved(inp, text)
  local mode = inp.mode
  if mode == 'url' then
    if text == '' then reopen_current(true) return end
    state.add = { url = text, busy = true }
    state.force_open = true
    local top = state.stack[#state.stack]
    if top and top.name == 'add' then table.remove(state.stack) end
    open_view({ name = 'add' })
    return
  end
  if mode == 'name' and state.add and text ~= '' then
    state.add.title = text
  elseif mode == 'title' and text ~= '' then
    reopen_current(true)
    update_sub(inp.id, { title = text })
    return
  elseif mode == 'window' then
    reopen_current(true)
    set_settings({ window = text }, function(err)
      local top = state.stack[#state.stack]
      if not err and top and top.name == 'window' then go_back() end
    end)
    return
  elseif mode == 'move' then
    reopen_current(true)
    local ref = { id = inp.id, global = inp.global }
    local c = copy(current_chain(ref))
    c.move_to = text
    set_chain(ref, c)
    local top = state.stack[#state.stack]
    if top and top.name == 'move' then go_back() end
    return
  end
  reopen_current(true)
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
  local inp = state.input
  state.input = nil
  uosc.close(INPUT)
  if chosen == nil then
    if ev.type == 'back' then reopen_current(true) else publish() end
    return
  end
  input_saved(inp, (chosen:gsub('^%s+', ''):gsub('%s+$', '')))
end)

-- ---------------------------------------------------------------------------------------------
-- events from uosc

local function row_action(id, action)
  if action == 'check' then check(id)
  elseif action == 'pause' then pause(id, true)
  elseif action == 'resume' then pause(id, false)
  elseif action == 'remove' then open_view({ name = 'remove', args = { id = id } }) end
end

local function activate(v, action)
  if v.view and v.id and action then
    row_action(v.id, action)
  elseif v.add then
    open_input('url', http_path())
  elseif v.check_all then
    check_all()
  elseif v.check then
    check(v.check)
  elseif v.pause then
    pause(v.pause, v.paused == true)
  elseif v.remove then
    remove(v.remove)
  elseif v.nav_back then
    go_back()
  elseif v.rename then
    local s = state.byid[v.rename]
    open_input('title', s and s.title or '', { id = v.rename })
  elseif v.rule then
    change_rule(v.id, v.rule)
  elseif v.set_preset then
    update_sub(v.id, { preset = v.set_preset }, function(err) if not err then go_back() end end)
  elseif v.chain then
    set_chain(v, next_chain(current_chain(v), v.chain))
  elseif v.move ~= nil then
    local c = copy(current_chain(v))
    c.move_to = v.move
    set_chain(v, c)
    go_back()
  elseif v.move_input then
    open_input('move', current_chain(v).move_to or '', { id = v.id, global = v.global })
  elseif v.apply then
    rpc.call('feeds.rules.apply', { id = v.apply }, function(err, res)
      if err then fail(err, 'feeds.rules.apply') return end
      state.last_action = 'apply:' .. v.apply
      publish()
      osd(res.removed > 0 and ('Borrados: ' .. plural(res.removed, 'archivo', 'archivos'))
        or 'Nada que borrar ahora')
    end)
  elseif v.play then
    uosc.close(MENU)
    mp.commandv('loadfile', v.play, 'replace')
  elseif v.subscribe then
    subscribe()
  elseif v.name_input then
    open_input('name', state.add and state.add.title or '')
  elseif v.initial then
    if state.add then state.add.initial = cycle(INITIAL, state.add.initial) end
    publish()
    rerender()
  elseif v.set then
    change_setting(v.set)
  elseif v.window ~= nil then
    set_settings({ window = v.window }, function(err) if not err then go_back() end end)
  elseif v.window_input then
    local cur = settings().window or ''
    open_input('window', cur ~= '' and window_text() or '')
  elseif v.view then
    open_view({ name = v.view, args = v })
  end
end

mp.register_script_message(EVENT, function(json)
  local ev = utils.parse_json(json or '') or {}
  local back, handled = nav.classify(ev)
  if handled then return end
  if back then ev.type = 'back' end
  if ev.type == 'activate' then
    activate(type(ev.value) == 'table' and ev.value or {}, ev.action)
  elseif ev.type == 'back' then
    go_back()
  end
end)

local reset_timer = nil
mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if reset_timer then reset_timer:kill(); reset_timer = nil end
  if t == MENU or t == INPUT then return end
  reset_timer = mp.add_timeout(0.5, function()   -- uosc may take a moment to publish the next menu
    reset_timer = nil
    local open = uosc.open_type()
    if open == MENU or open == INPUT then return end
    if #state.stack > 0 or state.view ~= '' or state.input then
      state.stack, state.view, state.input = {}, '', nil
      publish()
    end
  end)
end)

-- ---------------------------------------------------------------------------------------------
-- events pushed by mpvd: {event:'feeds', state, subscription?}

mp.register_script_message('mu-event', function(payload)
  local ev = utils.parse_json(payload or '')
  if type(ev) ~= 'table' or ev.event ~= 'feeds' then return end
  if state.list and type(ev.state) == 'table' then state.list.state = ev.state end
  if type(ev.subscription) == 'table' then
    store_sub(ev.subscription, true)
  elseif state.list and type(ev.state) == 'table' and ev.state.subscriptions ~= #subs() then
    -- one was removed elsewhere (another player, the MCP): read the list again
    fetch_list(function() schedule_refresh() end)
  end
  publish()
  schedule_refresh()
end)

-- ---------------------------------------------------------------------------------------------
-- bindings

local function open_root()
  if not uosc.available() then osd('uosc no está cargado') return end
  state.stack = {}
  state.force_open = uosc.open_type() ~= MENU
  open_view({ name = 'root' })
end

N:binding('feeds-menu', open_root)
-- `script-message-to mu_feeds mu-feeds-add <url>`: straight to «Añadir suscripción» with that address
mp.register_script_message('mu-feeds-add', function(url)
  if not uosc.available() or not url or url == '' then return end
  state.stack = { { name = 'root', title = ROOT_TITLE } }
  state.add = { url = url, busy = true }
  state.force_open = true
  open_view({ name = 'add' })
end)

publish()
msg.info('mu-feeds loaded')
