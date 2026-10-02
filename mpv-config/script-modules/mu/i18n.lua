-- mu.i18n: the app's language for the mu-* scripts (H49, ADR-087).
--
--   local t = require('mu.i18n').t
--   t('Abrir o descargar')                  -- the Spanish string IS the key
--   t('%d archivos en la lista'):format(n)  -- whole sentences, never pieces glued together
--
-- The Spanish string is the key on purpose: the code stays readable, Spanish needs no catalogue, and a string that
-- is missing from a catalogue falls back to it — the worst case is "it shows in Spanish", never a raw key or a gap.
-- Who decides the language: bin/mpv-uos, before any script loads (`--script-opts=mu-core-lang=…`), so the start
-- screen never appears in one language and switches to another. See docs/IDIOMAS.md.
local mp = require('mp')
local utils = require('mp.utils')

local M = { lang = 'es' }
local catalogue = {}

local SUPPORTED = { es = true, en = true, fr = true }

-- A locale as the environment writes it ("fr_CA.UTF-8", "es", "en_GB") → the language we serve.
-- Ser's rule: Spanish or French → that one; English or anything else → English.
function M.from_locale(value)
  local code = tostring(value or ''):match('^%s*(%a%a)') or ''
  code = code:lower()
  if code == 'es' or code == 'fr' then return code end
  return 'en'
end

local function root()
  local env = os.getenv('MPV_UOS_ROOT')
  if env and env ~= '' then return env end
  local conf = mp.command_native({ 'expand-path', '~~/' }) or ''
  return conf:match('^(.*)[/\\][^/\\]+[/\\]?$') or ''
end

local function load_catalogue(lang)
  if lang == 'es' then return {} end            -- Spanish is the identity: there is no es.json
  local f = io.open(utils.join_path(utils.join_path(root(), 'locales'), lang .. '.json'), 'r')
  if not f then return {} end
  local data = utils.parse_json(f:read('*a') or '')
  f:close()
  return type(data) == 'table' and data or {}
end

-- What the launcher decided, read straight from mpv's script-opts. It has to be read here and not handed over by
-- mu-core: **every mpv script runs in its own Lua state**, so this module is a different instance in each one and
-- nothing mu-core sets would reach the rest.
local function from_script_opts()
  local all = mp.get_property_native('options/script-opts') or {}
  local value = all['mu-core-lang']
  return (type(value) == 'string' and value ~= '') and value or nil
end

-- `lang`: 'es' | 'en' | 'fr', or a locale/'' to work it out from the launcher and then from the environment.
function M.set(lang)
  local code = SUPPORTED[tostring(lang or '')] and lang or nil
  if not code then
    local env = (lang ~= nil and lang ~= '') and lang or from_script_opts()
      or os.getenv('LC_ALL') or os.getenv('LC_MESSAGES') or os.getenv('LANG') or ''
    code = SUPPORTED[tostring(env)] and env or M.from_locale(env)
  end
  M.lang = code
  catalogue = load_catalogue(code)
  return code
end

-- The translation, or the Spanish string when there is none.
function M.t(text)
  if type(text) ~= 'string' then return text end
  local hit = catalogue[text]
  if type(hit) == 'string' and hit ~= '' then return hit end
  return text
end

M.set(nil)
return M
