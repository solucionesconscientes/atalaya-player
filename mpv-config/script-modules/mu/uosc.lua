-- Thin helpers over uosc's public script messages (verified against 5.13.0, see docs/UOSC_API.md).
local mp = require('mp')
local utils = require('mp.utils')

local M = {}

function M.available()
  local core = mp.get_property_native('user-data/mu/core')
  return core ~= nil and core.uosc == true
end

function M.send(message, ...)
  mp.commandv('script-message-to', 'uosc', message, ...)
end

function M.open(menu, submenu_id)
  if submenu_id then
    M.send('open-menu', utils.format_json(menu), submenu_id)
  else
    M.send('open-menu', utils.format_json(menu))
  end
end

function M.update(menu)
  M.send('update-menu', utils.format_json(menu))
end

function M.close(menu_type)
  if menu_type then M.send('close-menu', menu_type) else M.send('close-menu') end
end

-- Type of the open menu, or nil when no menu is open. Read natively: the string form of a user-data
-- sub-property is its JSON encoding (with quotes), which would never compare equal to the plain type.
function M.open_type()
  local t = mp.get_property_native('user-data/uosc/menu/type')
  if type(t) == 'string' and t ~= '' then return t end
  return nil
end

function M.set_button(name, spec)
  M.send('set-button', name, utils.format_json(spec))
end

function M.loading_items(text)
  return { { title = text or 'Cargando…', icon = 'spinner', align = 'center', selectable = false, muted = true } }
end

function M.message_items(text, icon)
  return { { title = text, icon = icon or 'info', align = 'center', selectable = false, muted = true } }
end

return M
