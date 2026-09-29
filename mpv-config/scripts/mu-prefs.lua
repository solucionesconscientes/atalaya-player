-- mu-prefs: remembers the user's global mpv choices between sessions (volume, speed, subtitle look, picture
-- equaliser, window state, preferred audio/subtitle languages...) in <data_dir>/prefs.json, namespace "mpv", and is the
-- single writer of that file for every mu-* script (mu/prefs.lua). Script name: mu_prefs. State in user-data/mu/prefs.
--
-- At load it snapshots the "factory" values (mpv.conf + command line), then applies the stored ones except options
-- given on the command line (option-info/<x>/set-from-commandline). Changes are saved when they look like the user's:
-- ignored between start-file/end-file and the first playback-restart + 0.3 s (watch_later, per-file options, track
-- auto-selection), when the option is file-local (option-info/<x>/set-locally) and, for speed, while mu-study's smart
-- speed is inside a silence. Track choices teach languages: picking an audio/subtitle track with a language moves that
-- language (2- and 3-letter codes) to the front of alang/slang; turning subtitles off (sid=no) is remembered as
-- subs_off and applied to the next files. Deliberately NOT remembered (docs/USO.md): pause, position, track ids,
-- delays, zoom/pan/aspect, deinterlace, loop-file.
-- Messages: reset (move prefs.json to prefs.json.bak-<ts> and go back to the factory values), reset-ask (confirmation
-- dialog through mu-menu first), flush.
local mp = require('mp')
local msg = require('mp.msg')
package.path = mp.command_native({ 'expand-path', '~~/script-modules/?.lua' }) .. ';' .. package.path
local prefs = require('mu.prefs')

local IGNORE_AFTER_RESTART = 0.3
local MAX_LANGS = 12

-- whitelist: kind = how the value is read, observed and stored
local PROPS = {
  { name = 'volume', kind = 'number' },
  { name = 'mute', kind = 'bool' },
  { name = 'speed', kind = 'number' },
  { name = 'sub-scale', kind = 'number' },
  { name = 'sub-pos', kind = 'number' },
  { name = 'sub-visibility', kind = 'bool' },
  { name = 'secondary-sub-pos', kind = 'number' },
  { name = 'secondary-sub-visibility', kind = 'bool' },
  { name = 'sub-ass-override', kind = 'string' },
  { name = 'sub-use-margins', kind = 'bool' },
  { name = 'contrast', kind = 'number' },
  { name = 'brightness', kind = 'number' },
  { name = 'gamma', kind = 'number' },
  { name = 'saturation', kind = 'number' },
  { name = 'hue', kind = 'number' },
  { name = 'fullscreen', kind = 'bool' },
  { name = 'ontop', kind = 'bool' },
  { name = 'loop-playlist', kind = 'string' },
  -- only portable values: a specific API (vaapi-copy, nvdec...) may not exist on the next machine/driver
  { name = 'hwdec', kind = 'string', allow = { ['auto-safe'] = true, no = true, ['vaapi,auto-safe'] = true } },
  { name = 'ytdl-format', kind = 'string' },
}
local BY_NAME = {}
for _, p in ipairs(PROPS) do BY_NAME[p.name] = p end

local OBSERVE_TYPE = { number = 'number', bool = 'bool', string = 'string' }

local function read(p)
  if p.kind == 'string' then return mp.get_property(p.name) end
  return mp.get_property_native(p.name)
end

local function write(p, v)
  if p.kind == 'string' then return mp.set_property(p.name, v) end
  return mp.set_property_native(p.name, v)
end

local function from_cli(name)
  return mp.get_property_native('option-info/' .. name .. '/set-from-commandline') == true
end

local function set_locally(name)
  return mp.get_property_native('option-info/' .. name .. '/set-locally') == true
end

-- ---------------------------------------------------------------------------------------------
-- languages: ISO 639-1 <-> 639-2 (B and T) for the languages mpv users meet most

local ISO = {
  es = { 'spa' }, en = { 'eng' }, fr = { 'fre', 'fra' }, de = { 'ger', 'deu' }, it = { 'ita' }, pt = { 'por' },
  ca = { 'cat' }, gl = { 'glg' }, eu = { 'baq', 'eus' }, ja = { 'jpn' }, zh = { 'chi', 'zho' }, ko = { 'kor' },
  ru = { 'rus' }, nl = { 'dut', 'nld' }, pl = { 'pol' }, ar = { 'ara' }, hi = { 'hin' }, tr = { 'tur' },
  uk = { 'ukr' }, sv = { 'swe' }, da = { 'dan' }, no = { 'nor' }, nb = { 'nob' }, nn = { 'nno' }, fi = { 'fin' },
  cs = { 'cze', 'ces' }, el = { 'gre', 'ell' }, he = { 'heb' }, hu = { 'hun' }, ro = { 'rum', 'ron' },
  bg = { 'bul' }, hr = { 'hrv' }, sr = { 'srp' }, sk = { 'slo', 'slk' }, sl = { 'slv' }, th = { 'tha' },
  vi = { 'vie' }, id = { 'ind' }, ms = { 'may', 'msa' }, fa = { 'per', 'fas' }, la = { 'lat' },
  is = { 'ice', 'isl' }, et = { 'est' }, lv = { 'lav' }, lt = { 'lit' }, ga = { 'gle' }, cy = { 'wel', 'cym' },
  bn = { 'ben' }, ta = { 'tam' }, ur = { 'urd' }, sq = { 'alb', 'sqi' }, mk = { 'mac', 'mkd' },
}
local ISO3 = {}
for two, threes in pairs(ISO) do for _, t in ipairs(threes) do ISO3[t] = two end end
local NOT_A_LANGUAGE = { und = true, mis = true, mul = true, zxx = true, qaa = true }

-- 'pt-BR' -> { 'pt-br', 'pt', 'por' }; 'spa' -> { 'es', 'spa' }; unknown codes are kept as they are.
local function lang_variants(lang)
  lang = tostring(lang or ''):lower():gsub('_', '-')
  local base = lang:match('^([a-z]+)') or ''
  if base == '' or NOT_A_LANGUAGE[base] then return nil end
  local out = {}
  local function add(x) for _, y in ipairs(out) do if y == x then return end end table.insert(out, x) end
  if lang ~= base then add(lang) end
  local two = #base == 2 and base or ISO3[base]
  if two then
    add(two)
    for _, t in ipairs(ISO[two] or {}) do add(t) end
  end
  add(base)
  return out
end

local function is_string_list(v)
  if type(v) ~= 'table' then return false end
  for _, x in pairs(v) do if type(x) ~= 'string' then return false end end
  return true
end

-- ---------------------------------------------------------------------------------------------
-- factory snapshot and stored values

local factory = {}
for _, p in ipairs(PROPS) do factory[p.name] = read(p) end
factory.alang = mp.get_property_native('alang') or {}
factory.slang = mp.get_property_native('slang') or {}
factory.subs_off = false

prefs.become_writer()   -- before any namespace: the writer quarantines a corrupt file
local P = prefs.ns('mpv', factory, function(key, v)
  if key == 'alang' or key == 'slang' then return is_string_list(v) end
  local p = BY_NAME[key]
  if p and p.allow then return p.allow[v] == true end
  return true
end)

-- loads: files whose loading window already closed (lets tests and tools wait for "user changes count now")
local state = { loading = false, loads = 0, applied = {}, skipped_cli = {}, confirm_token = nil, learned = '' }

local function publish()
  local st = prefs.status()
  mp.set_property_native('user-data/mu/prefs', {
    enabled = st.enabled, path = st.path, writes = st.writes, last_write = st.last_write, last_error = st.last_error,
    corrupt = st.corrupt, backup = st.backup, pending = st.pending, loading = state.loading, loads = state.loads,
    values = P:all(),
    applied = state.applied, skipped_cli = state.skipped_cli, learned = state.learned,
  })
end
prefs.on_status(publish)

local function apply_stored()
  state.applied, state.skipped_cli = {}, {}
  for _, p in ipairs(PROPS) do
    if P:has(p.name) then
      if from_cli(p.name) then
        table.insert(state.skipped_cli, p.name)
      else
        local ok, err = write(p, P:get(p.name))
        if ok then
          table.insert(state.applied, p.name)
        else
          msg.warn(string.format('prefs: %s=%s rechazado por mpv (%s); se descarta', p.name, tostring(P:get(p.name)),
            tostring(err)))
          P:unset(p.name)
        end
      end
    end
  end
  for _, key in ipairs({ 'alang', 'slang' }) do
    if P:has(key) then
      if from_cli(key) then table.insert(state.skipped_cli, key)
      else
        mp.set_property_native(key, P:get(key))
        table.insert(state.applied, key)
      end
    end
  end
end

local function restore_factory()
  for _, p in ipairs(PROPS) do
    if factory[p.name] ~= nil and read(p) ~= factory[p.name] then write(p, factory[p.name]) end
  end
  mp.set_property_native('alang', factory.alang)
  mp.set_property_native('slang', factory.slang)
end

-- ---------------------------------------------------------------------------------------------
-- what is a user change

local restart_timer = nil
local function set_loading(on)
  if restart_timer then restart_timer:kill(); restart_timer = nil end
  if state.loading ~= on then state.loading = on; publish() end
end

mp.register_event('start-file', function() set_loading(true) end)
mp.register_event('end-file', function() set_loading(true) end)
mp.register_event('playback-restart', function()
  if not state.loading or restart_timer then return end
  restart_timer = mp.add_timeout(IGNORE_AFTER_RESTART, function()
    restart_timer = nil
    state.loads = state.loads + 1
    set_loading(false)
  end)
end)
mp.observe_property('idle-active', 'bool', function(_, idle) if idle then set_loading(false) end end)

local function study_in_silence()
  local st = mp.get_property_native('user-data/mu/study')
  return type(st) == 'table' and st.in_silence == true
end

local function automatic(name)
  if state.loading or set_locally(name) then return true end
  if name == 'speed' and study_in_silence() then return true end
  return false
end

for _, p in ipairs(PROPS) do
  local initial = true   -- the first notification is the start-up value (mpv.conf, command line or applied pref)
  mp.observe_property(p.name, OBSERVE_TYPE[p.kind], function(_, v)
    if initial then initial = false; return end
    if v == nil or automatic(p.name) then return end
    if p.allow and not p.allow[v] then return end
    if P:set(p.name, v) then publish() end
  end)
end

-- ---------------------------------------------------------------------------------------------
-- languages and "subtitles off"

-- tracks created by mu-subs (AI transcription, translation, resync) say nothing about the user's language
local function generated(t)
  local title = t.title or ''
  if title:find('^Subtítulos IA') or title:find('^Traducción %(') or title:find('^Resincronizado %(') then return true end
  local ext = t['external-filename']
  if type(ext) ~= 'string' or ext == '' then return false end
  local subs = mp.get_property_native('user-data/mu/subs')
  if type(subs) == 'table' then
    for _, k in ipairs({ 'srt', 'translate_out', 'resync_out' }) do
      if subs[k] == ext then return true end
    end
  end
  for _, dir in ipairs({ os.getenv('MPV_UOS_CACHE_DIR') or '',
                         (os.getenv('MPV_UOS_ROOT') or '') ~= '' and (os.getenv('MPV_UOS_ROOT') .. '/.cache') or '' }) do
    if dir ~= '' and ext:sub(1, #dir) == dir then return true end
  end
  return false
end

local function learn(key, lang)
  if from_cli(key) then return end
  local front = lang_variants(lang)
  if not front then return end
  local list, seen = {}, {}
  for _, x in ipairs(front) do
    if not seen[x] then seen[x] = true; table.insert(list, x) end
  end
  for _, x in ipairs(mp.get_property_native(key) or {}) do
    local lx = x:lower()
    if not seen[lx] and #list < MAX_LANGS then seen[lx] = true; table.insert(list, x) end
  end
  local current = mp.get_property_native(key) or {}
  local same = #current == #list
  for i = 1, #list do if current[i] ~= list[i] then same = false end end
  if not same then mp.set_property_native(key, list) end
  if P:set(key, list) then
    state.learned = key .. '=' .. table.concat(list, ',')
    publish()
  end
end

local function track_exists(kind, id)
  for _, t in ipairs(mp.get_property_native('track-list') or {}) do
    if t.type == kind and t.id == id then return true end
  end
  return false
end

local last = { audio = nil, sub = nil }
local function on_track(kind, prop, value)
  local prev = last[kind]
  last[kind] = value
  if value == nil or state.loading or mp.get_property_native('idle-active') then return end
  if value == false then
    -- only a deliberate "off": the track that was selected is still there (mu-subs removing its track is not)
    if kind == 'sub' and type(prev) == 'number' and track_exists(kind, prev) then
      if P:set('subs_off', true) then publish() end
    end
    return
  end
  local t = mp.get_property_native('current-tracks/' .. kind)
  if type(t) ~= 'table' or generated(t) then return end
  if kind == 'sub' and P:unset('subs_off') then publish() end
  if t.lang and t.lang ~= '' then learn(prop == 'aid' and 'alang' or 'slang', t.lang) end
end
mp.observe_property('aid', 'native', function(_, v) on_track('audio', 'aid', v) end)
mp.observe_property('sid', 'native', function(_, v) on_track('sub', 'sid', v) end)

-- subs_off is applied per file before track selection, unless this file already has an explicit choice (a
-- watch_later sid or a per-file option: the option is then a number or "no" instead of "auto")
mp.add_hook('on_load', 50, function()
  if not P:get('subs_off') or from_cli('sid') then return end
  if mp.get_property('options/sid') == 'auto' then mp.set_property('options/sid', 'no') end
end)

-- ---------------------------------------------------------------------------------------------
-- reset

P:on_change(function(reason)
  if reason ~= 'reset' then return end
  restore_factory()
  state.applied, state.skipped_cli, state.learned = {}, {}, ''
  publish()
end)

local function reset()
  if not prefs.enabled() then
    mp.osd_message('Las preferencias están desactivadas (MPV_UOS_PREFS=0 o mu-prefs-enabled=no)', 4)
    return
  end
  local bak = prefs.reset_all()
  publish()
  mp.osd_message('Preferencias restablecidas' .. ((bak and bak ~= '') and
    (' · copia en ' .. (bak:match('[^/\\]+$') or bak)) or ''), 4)
end

local confirm_seq = 0
local function reset_ask()
  confirm_seq = confirm_seq + 1
  state.confirm_token = 'prefs-reset-' .. confirm_seq
  mp.commandv('script-message-to', 'mu_menu', 'mu-confirm', state.confirm_token,
    '¿Restablecer todas las preferencias? (se guarda una copia)', '20')
end

mp.observe_property('user-data/mu/confirm', 'native', function(_, v)
  if type(v) ~= 'table' or not state.confirm_token or v.token ~= state.confirm_token then return end
  state.confirm_token = nil
  if v.answer == 'yes' then
    reset()
  elseif v.answer == 'no-ui' then
    mp.osd_message('Sin menú para confirmar: usa script-message-to mu_prefs reset', 4)
  end
end)

mp.register_script_message('reset', reset)
mp.register_script_message('reset-ask', reset_ask)
mp.register_script_message('flush', function() prefs.flush() end)

-- ---------------------------------------------------------------------------------------------

if prefs.enabled() then apply_stored() end
publish()
msg.info(string.format('mu-prefs %s · %s · %d aplicadas%s', prefs.enabled() and 'activo' or 'desactivado', prefs.path(),
  #state.applied, #state.skipped_cli > 0 and (' · de la línea de órdenes: ' .. table.concat(state.skipped_cli, ',')) or ''))
