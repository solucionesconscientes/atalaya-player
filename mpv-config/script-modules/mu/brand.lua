-- The app's identity for the mu-* scripts (H33): name, id and colours from <project>/brand.json, the single place to
-- change when Ser decides the name. Falls back to the built-in values when the file cannot be read.
--
--   local brand = require('mu.brand')
--   brand.name · brand.id · brand.folder · brand.site · brand.colors.amber · brand.ass('amber') (ASS &HBBGGRR&)
local mp = require('mp')
local utils = require('mp.utils')

local M = {
  name = 'MPV-UOS',
  id = 'mpv-uos',
  folder = 'MPV-UOS',
  site = 'https://solucionesconscientes.es/atalaya',
  colors = { ink = '#0D1320', slate = '#24324D', signal = '#3D7BFF', amber = '#FFB020', mist = '#E9EEF6' },
}

local function root()
  local env = os.getenv('MPV_UOS_ROOT')
  if env and env ~= '' then return env end
  local conf = mp.command_native({ 'expand-path', '~~/' }) or ''
  return conf:match('^(.*)[/\\][^/\\]+[/\\]?$') or ''
end

local f = io.open(utils.join_path(root(), 'brand.json'), 'r')
if f then
  local data = utils.parse_json(f:read('*a') or '')
  f:close()
  if type(data) == 'table' then
    for _, k in ipairs({ 'name', 'id', 'folder', 'site' }) do
      if type(data[k]) == 'string' and data[k] ~= '' then M[k] = data[k] end
    end
    if type(data.colors) == 'table' then
      for k, v in pairs(data.colors) do if type(v) == 'string' then M.colors[k] = v end end
    end
  end
end

-- '#RRGGBB' → 'BBGGRR' for ASS override tags (\1c&H…&)
function M.ass(color)
  local hex = (M.colors[color] or color or ''):gsub('#', '')
  if #hex ~= 6 then return 'FFFFFF' end
  return (hex:sub(5, 6) .. hex:sub(3, 4) .. hex:sub(1, 2)):upper()
end

return M
