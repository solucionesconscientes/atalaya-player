# API pública de uosc 5.13.0 (referencia verificada para los scripts mu-*)

Todo lo que sigue está contrastado con el código vendorizado en `mpv-config/scripts/uosc/` (5.13.0) y con una
prueba headless real por IPC (mpv 0.41.0, `--vo=null --ao=null --idle=yes`). Cuando la wiki y el código difieren,
manda el código. Referencias `archivo:línea` relativas a `mpv-config/scripts/uosc/`.

## 1. Mensajes que uosc escucha (`script-message-to uosc <msg> ...`)

Registrados en `main.lua:1086-1153` y `lib/buttons.lua:57`. **No existen** en 5.13.0: `get-locale`, `set-shared-props`,
`set-menu`, `add-menu-item`. La única forma de meter entradas en el menú principal es `input.conf` (sección 6).

| Mensaje | Argumentos | Qué hace |
|---|---|---|
| `open-menu` | `<json> [submenu_id]` | Abre (o sustituye) el menú. El 2.º argumento **no** es el script receptor: es el `id` del submenú que se abre preseleccionado (`main.lua:1090`). El receptor de eventos va dentro del JSON (`callback`). |
| `update-menu` | `<json>` | Actualiza el menú abierto conservando selección/scroll/búsqueda. **Exige `type`** y que coincida con el del menú abierto; si no, se ignora en silencio (`main.lua:1103`). |
| `close-menu` | `[type]` | Cierra el menú; con `type`, solo si coincide (`main.lua:1118`). Dispara el evento `close`. |
| `show-submenu` / `show-submenu-blurred` | `<menu_id>` | Abre el menú de `input.conf` en el submenú `"A > B"` (blurred = sin preseleccionar item). |
| `select-menu-item` | `<type> <index> [menu_id]` | Selecciona y hace scroll a un item (ignorado si el usuario navega con ratón). |
| `menu-action` | `search-cancel [menu_id]` | Cancela la búsqueda (en `palette` solo vacía la consulta). **`search-query-update` está roto: llama a un método inexistente, lanza un error Lua fatal y mpv descarga uosc** (`main.lua:1126`, verificado). No usarlo. |
| `set-button` | `<name> <json>` | Define/actualiza un botón `button:<name>` de la barra (sección 5). |
| `set` | `<prop> <value>` | Guarda una propiedad externa y dispara `external_prop_<prop>`; la usan los botones `toggle:/cycle:` con `prop@script` y los badges `#prop@script` (`main.lua:1141`). |
| `overwrite-binding` | `<name> [command]` | Sustituye el comando de un binding de uosc (p. ej. `stream-quality`); sin comando, lo restaura (`main.lua:1152`). |
| `disable-elements` | `<client_id> <ids>` | Desactiva elementos por cliente; `""` para reactivar. Ids: `window_border, buffering_indicator, pause_indicator, top_bar, timeline, controls, volume, idle_indicator, audio_indicator`. |
| `flash-elements` / `toggle-elements` | `<ids,separados,por,coma>` | Muestra momentáneamente / fija visibilidad. Ids: `timeline, controls, volume, top_bar, speed, window_border` y el especial `progress`. |
| `set-min-visibility` | `<0..1> [ids]` | Visibilidad mínima (por defecto `timeline,controls,volume,top_bar`). |
| `thumbfast-info` | `<json>` | Interno de thumbfast. |

uosc emite al arrancar `script-message uosc-version 5.13.0` (`main.lua:4`): sirve para detectar que está cargado.
Mientras un menú está abierto, `user-data/uosc/menu/type` vale su `type` (o `"undefined"`); al cerrarse, la propiedad
desaparece (`Menu.lua:142,168`). Es la forma de saber si hay menú abierto y cuál.

```lua
local utils = require('mp.utils')
local function uosc(msg, ...) mp.commandv('script-message-to', 'uosc', msg, ...) end
local function menu_open_type() return mp.get_property('user-data/uosc/menu/type') end  -- nil si cerrado
```

## 2. Esquema del menú (`open-menu` / `update-menu`)

Tipos en `Menu.lua:3-11`; normalización en `Menu:update` (`Menu.lua:172-288`). Todo campo no listado se copia tal
cual y se ignora. `items` es obligatorio (si no es array, error y no se abre: `main.lua:1092`).

### 2.1 Campos del menú raíz

| Campo | Tipo (defecto) | Significado |
|---|---|---|
| `type` | string | Identificador para `update-menu`/`close-menu`/`select-menu-item` y `user-data/uosc/menu/type`. Ponlo siempre. |
| `title` | string | Título; solo se pinta en la raíz (`Menu.lua:1396`). En `palette` es el placeholder del buscador. |
| `items` | array | Items o submenús (2.2). |
| `callback` | `[script, message]` | Modo callback (3): cada evento se envía como `script-message-to <script> <message> <json>` (`lib/menus.lua:17-20`). Sin él, uosc ejecuta `value` por sí mismo. |
| `keep_open` | bool (false) | No cerrar al activar; lo heredan los items que no lo definan (`Menu.lua:218`). En modo callback el cierre siempre lo decides tú. |
| `footnote` | string | Texto bajo el menú al pasar el ratón por el icono `?` (`Menu.lua:1662`). Por menú/submenú. |
| `selected_index` | int (primer `active` o 1) | Solo se aplica a la raíz (`Menu.lua:230`). |
| `search_style` | `on_demand` (defecto) / `palette` / `disabled` | `palette` = buscador siempre visible; `disabled` = sin búsqueda (`Menu.lua:250`). |
| `search_debounce` | ms o `'submit'` | Defecto: 300 ms si hay `on_search`, 0 si la búsqueda es interna (`Menu.lua:199-205`). `'submit'`: solo busca al pulsar Enter sin item seleccionado (al teclear se desmarca la selección; `Menu.lua:1017,1262`). |
| `search_submenus` | bool (false) | La búsqueda interna aplana los submenús ("Padre / Hijo"); se hereda hacia abajo (`Menu.lua:823-827`). |
| `search_suggestion` | string | Consulta inicial (`Menu.lua:271`). |
| `search_submit` | bool | Con `palette`+`submit`, lanza la búsqueda inicial al abrir (`Menu.lua:152`). |
| `on_search` / `on_paste` / `on_move` / `on_close` | `'callback'` o string o array | `'callback'` → evento al `callback`. Comando string → `mp.command(cmd .. ' ' .. params)`; array → `mp.command_native(cmd + params)` (`Menu.lua:1349-1362`). Params añadidos: search `query, menu_id`; paste `value, menu_id`; move `from, to, menu_id`; close ninguno. |
| `item_actions` / `item_actions_place` | array de Action / `inside`·`outside` | Botones comunes a todos los items (2.3). |
| `bind_keys` | array de teclas | Teclas extra que uosc captura y reenvía como evento `key` (p. ej. `{'f1','ctrl+d'}`; `Menu.lua:1201`). |
| `id` | string | Solo tiene sentido en submenús; la raíz siempre es `{root}` (`Menu.lua:190-194`). |

### 2.2 Campos de cada item (`Menu.lua:7`)

| Campo | Tipo | Significado |
|---|---|---|
| `title` | string | Texto. UTF-8 sin problemas (probado con `ñ` y CJK). |
| `hint` | string | Texto secundario a la derecha (más pequeño); también se busca (`Menu.lua:916`). |
| `icon` | string | Nombre de Material Icons Rounded, tal cual (`live_tv`, `star`, `radio`, `content_copy`, `check_box`…); se pinta como ligadura de la fuente `MaterialIconsRound-Regular` (`lib/ass.lua:41`). Lista: https://fonts.google.com/icons?icon.set=Material+Icons&icon.style=Rounded. Especial: `spinner`. |
| `value` | string, array o cualquier JSON | Sin callback: string → `mp.command`, array → `mp.commandv` (`lib/menus.lua:8-15`). Con callback: llega intacto en el evento (puede ser un objeto). |
| `items` | array | Convierte el item en submenú (2.4). Un submenú no tiene `value`. |
| `active` | bool | Resaltado; el primer activo fija `selected_index` inicial. |
| `selectable` | bool (true) | `false` = cabecera/estado, no se selecciona ni entra en búsquedas. |
| `keep_open` | bool | Anula el del menú para ese item. |
| `bold`, `italic`, `muted` | bool | Estilo (`muted` = 50 % opacidad). |
| `separator` | bool | Línea separadora **debajo** del item. |
| `align` | `left`/`center`/`right` | Alineación del título. |
| `actions` / `actions_place` | array / `inside`·`outside` | Botones propios del item; sustituyen (no suman) a `item_actions` (`Menu.lua:646`). |

Sin un `callback`, los modificadores hacen esto (`lib/menus.lua:21-31`, verificado): Enter/click ejecuta y cierra;
`alt+Enter` ejecuta sin cerrar; `ctrl`/`shift+Enter` no hacen nada; pulsar un botón de acción no hace nada.

### 2.3 Action (`Menu.lua:3`)

`{name: string, icon: string, label?: string, filter_hidden?: boolean}`. Se muestran al seleccionar el item; se
recorren con `Tab`/`Shift+Tab` y se disparan con Enter o clic. **Solo se sabe cuál se pulsó en modo callback**
(`event.action == name`). `label` se muestra como pie mientras la acción está seleccionada.

### 2.4 Submenús

Un item con `items` es un submenú; admite `title`, `hint`, `icon` (se fuerza `chevron_right`), `bold`, `italic`,
`muted`, `separator`, `align`, `footnote`, `keep_open`, `search_*`, `on_*` e `id`. Su `id` por defecto es la cadena de
títulos `"Padre > Hijo"` (`Menu.lua:192`); es lo que llega en `event.menu_id` y lo que acepta `open-menu` como 2.º arg.
Navegación: `→`/Enter entra, `←`/Backspace vuelve; en la raíz, Backspace envía `{type:'back'}`.
`←` en la raíz (sin búsqueda) no hace nada en uosc y llega al callback como `{type:'key', id:'left'}`; `script-binding
uosc/menu-back` vuelve al submenú padre o, en la raíz, envía `back` (`Menu.lua:618`, `main.lua:1005`). `selected_index` del JSON
solo cuenta en la raíz; los submenús empiezan en su primer elemento. Tras `open-menu`, uosc enlaza sus teclas unos ms después de
publicar `user-data/uosc/menu/type` (los tests esperan a `input-bindings`). Uso en MPV-UOS: ADR-041 (`mu/nav.lua`).

## 3. Modo callback: eventos que recibe tu script

Con `callback = {script, mensaje}` uosc envía **todos** los eventos ahí y no ejecuta ni cierra nada. JSON exacto
capturado en la prueba (claves sin orden fijo):

```jsonc
{"type":"activate","index":1,"value":"uno","menu_id":"{root}","is_pointer":false,
 "alt":false,"ctrl":false,"shift":false}                       // Enter/clic en un item
{"type":"activate", ..., "modifiers":"alt","alt":true}          // alt+Enter; modifiers solo si hay alguno
{"type":"activate","index":1,"action":"fav", ...}               // botón de acción "fav" del item 1
{"type":"activate", ..., "keep_open":true}                      // solo si el item/menú tiene keep_open
{"type":"key","id":"f1","key":"f1","menu_id":"{root}","selected_item":{"index":2,"value":"dos"}}
{"type":"move","from_index":2,"to_index":1,"menu_id":"{root}"}  // ctrl+↑/↓/PgUp/PgDn/Home/End, requiere on_move
{"type":"search","query":"xñ","menu_id":"{root}"}               // requiere on_search='callback'
{"type":"paste","value":"<portapapeles>","menu_id":"{root}","selected_item":{...}}  // ctrl+v, requiere on_paste
{"type":"back"}                                                 // Backspace en la raíz
{"type":"close"}                                                // siempre, tras cerrarse (después de on_close)
```

Detalles: `menu_id` es `{root}` o el id del submenú; en `activate` `index` es 1-based dentro de ese submenú.
En `key`, `modifiers`/`alt`/`ctrl`/`shift` solo aparecen cuando son verdaderos (`lib/std.lua:308`); `id` es
`"ctrl+shift+f1"` con modificadores ordenados alfabéticamente. Las teclas que uosc ya usa (flechas, Enter, Esc, Tab,
`/`, `ctrl+f`, `ctrl+v`, `ctrl+c`, Backspace, Del…) no llegan como `key`; `shift+del` durante una búsqueda llega como
`del`. `on_search='callback'` desactiva la búsqueda interna: tú devuelves los resultados con `update-menu`.

## 4. Patrón recomendado en un script mu-*

```lua
local utils = require('mp.utils')
local SCRIPT = mp.get_script_name()
local function uosc(msg, ...) mp.commandv('script-message-to', 'uosc', msg, ...) end

local function build_menu(query)
  return {
    type = 'mu-tv', title = 'TV y radio', callback = {SCRIPT, 'mu-tv-event'},
    search_style = 'on_demand', search_debounce = 250, on_search = 'callback', on_close = 'callback',
    footnote = 'Enter reproduce · Tab elige acción · / busca',
    item_actions = {
      {name = 'fav', icon = 'star', label = 'Favorito'},
      {name = 'copy', icon = 'content_copy', label = 'Copiar URL'},
    },
    items = {
      {title = 'España', hint = '128', icon = 'flag', items = {
        {title = 'La 1', hint = 'TVE', icon = 'live_tv', value = {url = 'https://…/la1.m3u8', id = 'la1'}},
      }},
      {title = 'Radio', icon = 'radio', items = { {title = 'RNE', value = {url = 'https://…', id = 'rne'}} }},
      {title = 'Favoritos', icon = 'star', items = {}},
    },
  }
end

mp.register_script_message('mu-tv-event', function(json)
  local ev = utils.parse_json(json)
  if ev.type == 'activate' then
    if ev.action == 'fav' then toggle_favorite(ev.value.id); uosc('update-menu', utils.format_json(build_menu()))
    elseif ev.action == 'copy' then copy_to_clipboard(ev.value.url) -- función propia (xclip/wl-copy vía subprocess)
    else mp.commandv('loadfile', ev.value.url); if not ev.keep_open then uosc('close-menu', 'mu-tv') end end
  elseif ev.type == 'search' then
    -- pide a mpvd los canales que casan con ev.query y responde con update-menu (mismo type)
    request_search(ev.query, function(items) uosc('update-menu', utils.format_json({
      type = 'mu-tv', title = 'TV y radio', callback = {SCRIPT, 'mu-tv-event'}, on_search = 'callback',
      search_style = 'on_demand', search_debounce = 250, items = items})) end)
  elseif ev.type == 'close' then cancel_pending_search() end
end)

mp.add_key_binding(nil, 'tv-menu', function() uosc('open-menu', utils.format_json(build_menu())) end)
```

Notas: `update-menu` con un `search` activo y `on_search` conserva la consulta escrita (`Menu.lua:278-281`); la
raíz que envías reemplaza a la anterior, así que reenvía siempre el menú completo (con `callback`, `on_*`, `type`).
Menú "cargando": `items = {{icon = 'spinner', align = 'center', selectable = false, muted = true}}` (uosc lo usa en
`lib/menus.lua:962`). Paleta de comandos: `search_style = 'palette'`, `title` como placeholder, `search_submenus = true`.

## 5. Botones en la barra de controles

Sintaxis de `controls=` en `script-opts/uosc.conf` (parser en `elements/Controls.lua:62-217`):
`[<disposiciones>]elemento[:param…][#badge[>límite]][?tooltip]`, separados por comas.

- `command:{icon}:{comando}` — botón fijo; `cycle:{icon}:{prop}[@owner]:{v1}[=icono][!]/{v2}…`; `toggle:{icon}:{prop}[@owner]`;
  `gap[:escala]`, `space`, `speed[:escala]`, atajos (`menu`, `subtitles`, `play-pause`, `fullscreen`…).
- `button:{name}` — botón gestionado por un script vía `set-button` (`Controls.lua:180`). Hasta que llegue el primer
  `set-button`, muestra el icono `help_center` con tooltip "Uninitialized button" (`lib/buttons.lua:14`).
- Disposiciones: `<idle>`, `<video,audio>` (OR), `<has_audio+!audio>` (AND/negación), `<stream>`, `<has_playlist>` o
  cualquier propiedad mpv booleana (p. ej. `<user-data/mu/tv-mode>`).

`set-button <name> <json>` (`lib/buttons.lua:58-74`; `icon` obligatorio o se ignora):
`{icon: string, active?: bool, badge?: string, tooltip?: string, command?: string | string[], hide?: bool}`.
`command` string → `mp.command`; array → `mp.command_native` (`lib/utils.lua:430`). Sin `command`, el botón no es
clicable. `hide=true` lo quita de la barra y recoloca el resto (`ManagedButton.lua:25-34`). Cada `set-button`
reemplaza todo el estado (no hay merge). Verificado sin errores en la prueba.

```lua
-- uosc.conf:  controls=menu,gap,button:mu-tv,<video,audio>subtitles,…,space,…
uosc('set-button', 'mu-tv', utils.format_json({
  icon = 'live_tv', tooltip = 'TV y radio', badge = tostring(favorites_count > 0 and favorites_count or ''),
  active = mp.get_property('user-data/mu/tv-mode') == 'yes',
  command = {'script-binding', SCRIPT .. '/tv-menu'},   -- abre el menú de la sección 4
}))
```

Botones `toggle:{icon}:{prop}@{script}`: al pulsar, uosc envía `script-message-to <script> set <prop> <valor>`
(`CycleButton.lua:45`); tu script debe responder `script-message-to uosc set <prop> <valor>` para que cambie el estado.
Badge desde script: `#prop@script` y `set prop valor` (`Controls.lua:262-290`).

## 6. Menú principal desde input.conf

uosc lee el fichero `input-conf` (no la propiedad `input-bindings`): solo cuentan las líneas de `input.conf`
(`lib/menus.lua:575-628`), una sola vez (caché en `menu_items`; requiere reiniciar mpv). Sintaxis: `tecla comando #! Título > Sub`;
`#` como tecla para no asignar tecla; `#! ---` separador; línea con `#!` y sin comando = cabecera no seleccionable;
`ignore` crea solo las carpetas. Para añadir nuestras entradas: `#  script-binding mu_tv/tv-menu  #! TV y radio`
en `mpv-config/input.conf` (nombre de script = fichero sin `.lua`, `-` → `_`). El menú resultante es de modo
"sin callback" con `search_submenus = true` (`lib/menus.lua:47`); `show-submenu "TV y radio"` abre directo un submenú.

## 7. Límites y rendimiento observados (mpv 0.41, portátil 4 núcleos, headless)

- 10 000 items (JSON de 746 KB, con `hint` y `ñ`) por IPC: abre en ≈0,4–0,6 s; búsqueda interna con 4 teclas ≈0,5 s;
  `update-menu` completo ≈0,4 s. 1 000 items: indistinguible del coste base. No hay paginación: el render solo pinta
  los visibles (`Menu.lua:1398-1399`), pero cada `open/update` mide el ancho de **todos** los títulos y hints
  (`Menu.lua:315-327`) y la búsqueda interna es fuzzy sobre todo (`Menu.lua:843`). Para el menú de TV: submenús por
  país/categoría (≤1–2 k items cada uno) y `on_search='callback'` resuelto en mpvd; evita `update-menu` a >2–3 Hz.
- No hay límite de tamaño de argumento en `script-message-to` desde Lua; por IPC el JSON va doblemente codificado.
- Un `value` que sea objeto solo sirve en modo callback; sin callback debe ser string o array de comando.
- `update-menu` sin `type` o con otro `type`: ignorado en silencio. `close-menu` sin `type` cierra cualquier menú
  (incluido el de otro script).
- Cada Lua error dentro de uosc mata el script completo hasta reiniciar mpv (comprobado con `search-query-update`).
- `select-menu-item` no hace nada si el usuario ha movido el ratón (`mouse_nav`).
- Iconos: solo nombres de Material Icons Rounded incluidos en la fuente vendorizada (`fonts/uosc_icons.otf`); un
  nombre inexistente se pinta como texto. `hide` en `set-button` sí existe aunque el README de la release no lo cite.
- Discrepancias wiki vs código 5.13.0: la wiki dice que `search_debounce='submit'` se envía con `ctrl+Enter` (es Enter
  a secas con la selección desmarcada); que `search_debounce` vale 0 por defecto (es 300 con `on_search`); que
  `Item.active` es `integer` (se usa como booleano); y documenta `menu-action search-query-update` (rompe uosc).

## Verificado en

uosc 5.13.0 (`vendor.lock`, `main.lua:2`), mpv 0.41.0, 2026-09-28. Archivos: `main.lua:305-330, 434-435, 799-810,
1086-1153`; `lib/menus.lua:1-49, 54-200, 567-703, 959-1110`; `lib/buttons.lua` (completo); `lib/std.lua:288-309`;
`lib/utils.lua:430-440`; `lib/ass.lua:34-44`; `elements/Menu.lua:3-30, 34-60, 103-170, 172-330, 570-700, 789-860,
942-1030, 1129-1362, 1364-1400, 1479-1690`; `elements/Controls.lua:33-217, 262-290`; `elements/ManagedButton.lua`,
`elements/CycleButton.lua`, `elements/Button.lua:1-40`; `script-opts/uosc.conf:28-115`; `tmp/dl/uosc-README.md:366-503`;
wiki Home y Menu-API (github.com/tomasklaen/uosc/wiki, consultadas el mismo día). Prueba headless: mpv vía
`bin/mpv-uos --vo=null --ao=null --idle=yes` con cliente IPC Python (socket y log en `tmp/`, ya borrables).
