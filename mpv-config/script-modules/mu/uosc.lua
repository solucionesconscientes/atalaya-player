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

-- H63/N3 · entre «le pido a uosc que abra mi menú» y «uosc publica que está abierto» hay un hueco: normalmente 1 ms,
-- pero con el equipo ocupado se han medido 27 ms. Cada módulo tiene un vigilante que, 0,2 s después de que el menú
-- abierto deje de ser el suyo, da por cerrada su navegación y la olvida — y ese vigilante se arma cuando aparece el
-- menú del módulo PADRE, así que puede dispararse justo dentro de ese hueco: el menú sale bien en pantalla y el
-- módulo ya se ha olvidado de dónde estaba (⌫ salta de nivel, lo que cargaba no aparece, los paneles se paran).
-- Así que se apunta qué menú se ha pedido y cuándo, y el vigilante pregunta antes de olvidar nada.
local asked = { type = nil, at = -1 }

-- H63/N2 · y lo mismo por el otro lado: entre «le pido a uosc que cierre mi menú» y que lo cierre pasan
-- milisegundos (se han medido 21 con el equipo cargado). Quien pregunte en ese hueco lee en `menu/type` un menú
-- que ya está muerto, y decide mal: el módulo mandaba `update-menu` en vez de `open-menu` y uosc atendía después
-- el cierre, dejando al módulo con su navegación en pie y sin menú en pantalla. Medido: 1 de cada 10 pasadas.
local closing = { type = nil, at = -1 }

function M.open(menu, submenu_id)
  if menu.type then asked.type, asked.at = menu.type, mp.get_time() end
  closing.at = -1   -- the open goes behind the close: uosc handles them in that order, so it ends up open
  if submenu_id then
    M.send('open-menu', utils.format_json(menu), submenu_id)
  else
    M.send('open-menu', utils.format_json(menu))
  end
end

-- ¿Hemos pedido ese menú y uosc todavía no lo ha confirmado? El segundo límite es un seguro: si uosc no contesta
-- (no está cargado, se ha ido), el vigilante vuelve a hacer su trabajo un segundo después.
function M.asking(menu_type, window)
  return asked.type == menu_type and asked.at >= 0 and (mp.get_time() - asked.at) < (window or 1.0)
end

mp.observe_property('user-data/uosc/menu/type', 'native', function(_, t)
  if t ~= nil and t == asked.type then asked.at = -1 end     -- confirmado: ya no esperamos
  -- el cierre ya ha ocurrido cuando no hay menú o cuando el que hay es otro
  if t == nil or (closing.type ~= nil and t ~= closing.type) then closing.at = -1 end
end)

function M.update(menu)
  M.send('update-menu', utils.format_json(menu))
end

function M.close(menu_type)
  if menu_type == nil or menu_type == asked.type then asked.at = -1 end   -- lo cerramos nosotros: ya no esperamos
  closing.type, closing.at = menu_type, mp.get_time()
  if menu_type then M.send('close-menu', menu_type) else M.send('close-menu') end
end

-- Type of the open menu, or nil when no menu is open. Read natively: the string form of a user-data
-- sub-property is its JSON encoding (with quotes), which would never compare equal to the plain type.
function M.open_type()
  local t = mp.get_property_native('user-data/uosc/menu/type')
  if type(t) ~= 'string' or t == '' then return nil end
  -- si hemos pedido cerrar ESE menú y uosc aún no lo ha hecho, lo que se lee ya está muerto (el segundo límite es
  -- el mismo seguro que en `asking`: si uosc no contesta, se vuelve a creer lo que publica)
  if closing.at >= 0 and (mp.get_time() - closing.at) < 1.0
     and (closing.type == nil or closing.type == t) then return nil end
  return t
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
