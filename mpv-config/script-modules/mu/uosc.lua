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

-- H53 · El «modo sencillo» escribía la opción `controls` de uosc, pero uosc solo la lee al arrancar
-- (`Controls:init_options()` corre en `init()` y ningún elemento escucha cambios de opciones), así que la barra se
-- quedaba idéntica. Lo que sí entiende en caliente es `hide` en cada botón gestionado, así que el modo esconde
-- aquí todos los botones propios —menos el del menú, que es la puerta a todo lo demás— y los devuelve al salir.
-- Va en el módulo y no en cada script porque así lo heredan los veinte de una vez, incluidos los que aparecen
-- más tarde (Tareas, grabación). Los elementos de uosc (play, anterior/siguiente, audio, velocidad, pantalla
-- completa) no se pueden esconder uno a uno, y son justo los que el modo sencillo quiere conservar.
local KEEP_SIMPLE = { ['mu-menu'] = true }
local simple = false
local sent = {}

-- lo que este script tiene escondido ahora mismo, para poder comprobarlo desde fuera (uosc no publica sus botones)
local function publish_bar()
  local hidden = {}
  for name in pairs(sent) do
    if simple and not KEEP_SIMPLE[name] then hidden[#hidden + 1] = name end
  end
  table.sort(hidden)
  mp.set_property_native('user-data/mu/bar/' .. mp.get_script_name(), { simple = simple, hidden = hidden })
end

function M.set_button(name, spec)
  local nuevo = sent[name] == nil
  sent[name] = spec
  local out = spec
  if simple and not KEEP_SIMPLE[name] then
    out = {}
    for k, v in pairs(spec) do out[k] = v end
    out.hide = true
  end
  M.send('set-button', name, utils.format_json(out))
  if nuevo then publish_bar() end
end

-- mu-modes publica el modo en `user-data/mu/modes`; al cambiar, se reenvían los botones ya registrados
mp.observe_property('user-data/mu/modes', 'native', function(_, modes)
  local on = type(modes) == 'table' and modes.simple == true
  if on == simple then return end
  simple = on
  for name, spec in pairs(sent) do M.set_button(name, spec) end
  publish_bar()
end)

function M.loading_items(text)
  return { { title = text or 'Cargando…', icon = 'spinner', align = 'center', selectable = false, muted = true } }
end

function M.message_items(text, icon)
  return { { title = text, icon = icon or 'info', align = 'center', selectable = false, muted = true } }
end

return M
