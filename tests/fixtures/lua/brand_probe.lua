-- test helper for tests/test_brand.py: loads mu.brand from the project and prints what it read
local mp = require('mp')
local root = ''
for kv in (mp.get_property('script-opts') or ''):gmatch('[^,]+') do
  local v = kv:match('^brand_probe%-root=(.*)$')
  if v then root = v end
end
package.path = root .. '/mpv-config/script-modules/?.lua;' .. package.path
-- mu.brand finds brand.json through $MPV_UOS_ROOT (set by the test)
local brand = dofile(root .. '/mpv-config/script-modules/mu/brand.lua')
mp.msg.info(string.format('BRAND %s %s %s', brand.name, brand.id, brand.ass('amber')))
mp.commandv('quit')
