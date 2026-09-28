# ytdl_hook en mpv 0.41.0 — verificado contra la fuente real

Fecha: 2026-09-29 · Máquina: dell (`mpv v0.41.0`, FFmpeg 8.0.1, yt-dlp 2026.08.19, node en `/usr/bin/node`).

Fuentes usadas (en este orden):
1. **`man mpv`** del sistema (`/usr/share/man/man1/mpv.1.gz`) y `mpv --list-options`.
2. **El script real embebido en `/usr/bin/mpv`**: extraído con Python buscando la cadena
   `local utils = require 'mp.utils'…` hasta el byte nulo (offset 2283872, 1187 líneas). Es
   byte a byte idéntico (md5 `56f21bc0…`) a la copia `tmp/mpvsrc/ytdl_hook.lua`. No hay copia
   en disco (`find / -name 'ytdl_hook*'` solo devuelve la de `tmp/`). No se usó ninguna fuente de
   GitHub ni de memoria.
3. Implementación embebida de `mp.options.read_options` (también extraída del binario) y `lua.rst`.
4. Pruebas headless reales con `bin/mpv-uos` (logs en `tmp/ytdl-probe*.log`, drivers en
   `tmp/ytdl_probe_ipc.py` y `tmp/ytdl_probe3b.py`).

---

## 1. Opciones de mpv (man mpv 0.41)

`mpv --list-options | grep -i ytdl` devuelve:

```
 --ytdl                           Flag (default: yes)
 --ytdl-format                    String (default: )
 --ytdl-raw-options               Key/value list (default: )
    --ytdl-raw-options-add / -append / -clr / -del / -set / -remove
```
(y además `--vid  Choices: no auto (or an integer) (0 to 8190) (default: auto)`,
`--aid` ídem, `--audio-display  Choices: no embedded-first external-first (default: embedded-first)`,
`--start  Relative time or percent position (default: none)`, `--script-opts  Key/value list (default: )`
con sufijos `-add/-append/-clr/-del/-set/-remove` y `--script-opt  alias for --script-opts-append (CLI/config files only)`).

### `--ytdl=<yes|no>`
> Enable the youtube-dl hook-script. It will look at the input URL, and will play the video located on the website. […] (default: yes). If the script can't do anything with an URL, it will do nothing. This accepts a set of options, which can be passed to it with the `--script-opts` option (using `ytdl_hook-` as prefix)

### `--ytdl-format=<|ytdl|best|worst|mp4|webm|...>`
> Format selection string that is directly passed to youtube-dl. […] (Default: empty)
> An empty value or `ytdl` does not pass a `--format` option to youtube-dl at all, and thus uses its default format selection behavior.

Ojo: `mpv-config/mpv.conf` del proyecto ya fija `ytdl-format=bestvideo[height<=?1080]+bestaudio/best` (línea 28); en todas las pruebas aparece como `--format` en el comando.

### `--ytdl-raw-options=<key>=<value>[,<key>=<value>[,...]]`
> Pass arbitrary options to youtube-dl. Parameter and argument should be passed as a key-value pair. Options without argument must include `=`. There is no sanity checking […] This is a key/value list option. See List Options for details.
> Example · `--ytdl-raw-options=username=user,password=pass` · `--ytdl-raw-options=force-ipv6=` · `--ytdl-raw-options=proxy=[http://127.0.0.1:3128]` · `--ytdl-raw-options-append=proxy=http://127.0.0.1:3128`

Para `js-runtimes=node`: `--ytdl-raw-options=js-runtimes=node` (probado; el script lo convierte en `--js-runtimes node`).

### `--script-opt` / `--script-opts`
> `--script-opt=<key=value>`, `--script-opts=key1=value1,key2=value2,...` — Set options for scripts. A script can query an option by key. […] Each use of the `--script-opt` option will add another option to the internal list, while `--script-opts` takes a list of options at once, and overwrites the internal list with it. The latter is a key/value list option.

Key/value list options (sección "List Options"): sufijos `-set` (lista con `,`), `-append` (un ítem, "escapes for the key, no escapes for the value"), `-add`, `-clr`, `-del`, `-remove`.
> Keys are unique within the list. If an already present key is set, the existing key is removed before the new value is appended.
> If you want to pass a value without interpreting it for escapes or `,`, it is recommended to use the `-append` variant. When using libmpv, prefer using MPV_FORMAT_NODE_MAP; when using a scripting backend or the JSON IPC, use an appropriate structured data type.

### `--vid=<ID|auto|no>`
> Select video channel. `auto` selects the default, `no` disables video. […] If video is disabled, mpv will try to download the audio only if media is streamed with youtube-dl, because it saves bandwidth. This is done by setting the ytdl_format to "bestaudio/best" in the ytdl_hook.lua script.

(Confirmado en el código: `if mp.get_property("options/vid") == "no" and #format == 0 then format = "bestaudio/best"` — solo si `ytdl-format` está vacío.)

### `--audio-display=<no|embedded-first|external-first>`
> Determines whether to display cover art when playing audio files and with what priority. […] `no` Disable display of video entirely when playing audio files. `embedded-first` […] (default). `external-first` […] This option has no influence on files with normal video tracks.

### `--start=<relative time>` / `--end=<relative time>`
Ejemplos del manual: `--start=+56`, `--start=00:56`, `--start=-56`, `--start=01:10:00`, `--start=50%`, `--start=30 --end=40`, `--start=-3:20 --length=10`, `--start='#2' --end='#4'`.

### `loadfile <url> [<flags> [<index> [<options>]]]`
> The fourth argument is a list of options and values which should be set while the file is playing. It is of the form `opt1=value1,opt2=value2,...` When using the client API, this can be a MPV_FORMAT_NODE_MAP (or a Lua table), however the values themselves must be strings currently. These options are set during playback, and restored to the previous value at end of playback (see Per-File Options).
> Warning: Since mpv 0.38.0, an insertion index argument is added as the third argument. […] the third argument now needs to be set to -1 if the fourth argument needs to be used.

Sintaxis válida en 0.41 (input.conf / IPC en texto):
```
loadfile "https://…" replace -1 ytdl-format=bestaudio/best,start=30
```
Por JSON IPC / Lua: `{"command":["loadfile", url, "replace", -1, {"ytdl-format":"bestaudio/best","start":"30"}]}`
(valores como cadenas). `ytdl-format` **sí** es una opción válida aquí: aparece en `--list-options`
(`--ytdl-format  String (default: )`) y el mecanismo es el genérico de `file-local-options/<name>`:
> Similar to `options/<name>`, but when setting an option through this property, the option is reset to its old value once the current file has stopped playing.

El propio ytdl_hook lee `mp.get_property("options/ytdl-format")` dentro del hook `on_load`, es decir, ya
con las opciones por archivo aplicadas, así que `loadfile … ytdl-format=…` afecta a esa carga.

### Propiedades `user-data/mpv/ytdl/*` (man mpv, sección `user-data`)
> `user-data/mpv/ytdl` Data shared by the builtin ytdl hook script.
> `user-data/mpv/ytdl/path` Path to the ytdl executable, if found, or an empty string otherwise. The property is not set until the script attempts to find the ytdl executable, i.e. until an URL is being loaded by the script.
> `user-data/mpv/ytdl/json-subprocess-result` Result of executing ytdl to retrieve the JSON data of the URL being loaded. The format is the same as subprocess's result, capturing stdout and stderr.

### `subprocess` (búsqueda en PATH)
> The first array entry is either an absolute path to the executable, or a filename with no path components, in which case the executable is searched in the directories in the PATH environment variable. On Unix, this is equivalent to posix_spawnp and execvp behavior.

---

## 2. El script real: `ytdl_hook.lua` embebido en mpv 0.41.0

### (a) Opciones `script-opts` (`ytdl_hook-<opt>`) y valores por defecto — literal del script
```lua
local o = {
    exclude = "",
    include = "^%w+%.youtube%.com/|^youtube%.com/|^youtu%.be/|^%w+%.twitch%.tv/|^twitch%.tv/",
    try_ytdl_first = false,
    use_manifests = false,
    all_formats = false,
    force_all_formats = true,
    thumbnails = "none",
    ytdl_path = "",
}
local ytdl = {
    path = "",
    paths_to_search = {"yt-dlp", "yt-dlp_x86", "youtube-dl"},
    searched = false,
    blacklisted = {}
}
```
Son exactamente ocho: `exclude`, `include`, `try_ytdl_first`, `use_manifests`, `all_formats`,
`force_all_formats`, `thumbnails` (`all|best|none`), `ytdl_path`. Los booleanos se escriben `yes`/`no`
en `--script-opts` (conversión de `mp.options`). Documentación del manual para cada una en la sección
`--ytdl` (citada arriba en parte); `ytdl_path` dice literalmente:
> `ytdl_path=youtube-dl` Configure paths to youtube-dl's executable or a compatible fork's. The paths should be separated by `:` on Unix and `;` on Windows. mpv looks in order for the configured paths in PATH and in mpv's config directory. The defaults are "yt-dlp", "yt-dlp_x86" and "youtube-dl". On Windows the suffix extension is not necessary, but only ".exe" is acceptable.

**Cómo resuelve `ytdl_path` (código, `run_ytdl_hook`)**:
```lua
local separator = platform_is_windows() and ";" or ":"
if o.ytdl_path:match("[^" .. separator .. "]") then
    ytdl.paths_to_search = {}
    for path in o.ytdl_path:gmatch("[^" .. separator .. "]+") do
        table.insert(ytdl.paths_to_search, path)
    end
end
for _, path in pairs(ytdl.paths_to_search) do
    local exesuf = platform_is_windows() and not path:lower():match("%.exe$") and ".exe" or ""
    local ytdl_cmd = mp.find_config_file(path .. exesuf)
    if ytdl_cmd then            -- 1º: dentro de los directorios de config de mpv
        ytdl.path = ytdl_cmd; command[1] = ytdl.path; result = exec(command); break
    else                        -- 2º: tal cual → subprocess (ruta absoluta o búsqueda en PATH)
        command[1] = path; result = exec(command)
        if result.error_string == "init" then  -- no encontrado: prueba la siguiente
        else ytdl.path = path; break end
    end
end
ytdl.searched = true
mp.set_property("user-data/mpv/ytdl/path", ytdl.path or "")
```
Respuestas concretas:
- **Varias rutas**: sí, separadas por `:` en Unix/macOS y por `;` en Windows (NO `;` en Linux). Se prueban en
  orden; la primera que arranca gana. Probado: `"/ruta/que/no/existe:/…/vendor/dl/yt-dlp-2026.08.19"` →
  falla la primera (`Subprocess failed: init`) y usa la segunda.
- **Búsqueda**: para cada entrada, primero `mp.find_config_file(path)` (relativa al `--config-dir`, p. ej.
  `mpv-config/yt-dlp`), y si no, se pasa tal cual al comando `subprocess`: ruta absoluta o nombre simple
  buscado en `PATH` (execvp). Una ruta relativa con directorio que no exista en config-dir se pasa tal cual
  al exec (relativa al cwd de mpv) — evitarlo; usar rutas absolutas.
- **zipimport con shebang `#!/usr/bin/env python3`**: funciona. `vendor/dl/yt-dlp-2026.08.19` es
  "a /usr/bin/env python3 script executable (Zip archive)"; mpv lo ejecuta directamente vía execvp y el
  kernel resuelve el shebang. Solo requiere bit de ejecución y `python3` en PATH.
- **Windows**: separador `;`; si la ruta no acaba en `.exe` se le añade `.exe` para la búsqueda en
  config-dir; después se ejecuta tal cual (sin sufijo). Un zip con shebang NO sirve en Windows (no hay
  shebang); habría que apuntar a `yt-dlp.exe` o a un `.cmd`/lanzador. `platform_is_windows()` consulta la
  propiedad `platform`.
- **Caché de la búsqueda**: `ytdl.searched = true` tras la primera carga; en cargas siguientes se reutiliza
  `ytdl.path` sin volver a buscar, **salvo** que cambien los `script-opts` (ver 2e).
- `user-data/mpv/ytdl/path` conserva el último valor encontrado incluso si la búsqueda siguiente falla
  (`ytdl.path` solo se sobreescribe al encontrar algo). Comprobado en la prueba 3: tras fallar con
  `/ruta/que/no/existe`, la propiedad seguía mostrando la ruta antigua.

**`exclude` / `include`**: patrones Lua separados por `|`, comparados contra la URL en minúsculas sin el
`https?://`. `include` fuerza "ytdl primero" para esas URL aunque `try_ytdl_first=no`.

**`try_ytdl_first`**: el script registra dos hooks, `on_load` (prioridad 10) y `on_load_fail` (10).
```lua
local function on_load_hook(load_fail)
    local url = mp.get_property("stream-open-filename", "")
    local force = url:find("^ytdl://") ~= nil
    local early = force or o.try_ytdl_first or is_whitelisted(url)
    if early == load_fail then return end
    if not force and (not url:find("^https?://") or is_blacklisted(url)) then return end
    run_ytdl_hook(url)
end
```
Es decir: por defecto ytdl solo corre si mpv/ffmpeg falla al abrir la URL (`on_load_fail`), excepto para
URLs `ytdl://…`, las de `include` (YouTube/Twitch) o si `try_ytdl_first=yes`. Solo trata `http(s)://`.

**`use_manifests`**: usa `manifest_url` (HLS/DASH maestro) si `valid_manifest(json)`; permite cambiar
calidad en caliente vía el demuxer; desactivado por rendimiento.

**`all_formats` / `force_all_formats`**: ver 2d.

**`thumbnails`**: `all` añade cada miniatura con `mp.commandv("video-add", url, "auto")`; `best` elige
por `preference` o altura; `none` nada.

### (b) Cómo lee `ytdl-format` y `ytdl-raw-options`
```lua
local format = mp.get_property("options/ytdl-format")
local raw_options = mp.get_property_native("options/ytdl-raw-options")
local command = { ytdl.path, "--no-warnings", "-J", "--flat-playlist", "--sub-format", "ass/srt/best" }
if mp.get_property("options/vid") == "no" and #format == 0 then format = "bestaudio/best" end
if format ~= "" and format ~= "ytdl" then table.insert(command, "--format"); table.insert(command, format) end
for param, arg in pairs(raw_options) do
    table.insert(command, "--" .. param)
    if arg ~= "" or param == "proxy" then table.insert(command, arg) end
    if (param == "sub-lang" or param == "sub-langs" or param == "srt-lang") and (arg ~= "") then allsubs = false
    elseif param == "proxy" and arg ~= "" then proxy = arg
    elseif param == "yes-playlist" then use_playlist = true end
end
if allsubs == true then table.insert(command, "--sub-langs"); table.insert(command, "all") end
table.insert(command, "--write-srt")
if not use_playlist then table.insert(command, "--no-playlist") end
table.insert(command, "--"); table.insert(command, url)
```
- Se leen **en cada carga** (dentro del hook), de `options/…` → valen las opciones por archivo de `loadfile`.
- `ytdl-raw-options` es un mapa `clave=valor`; cada par se convierte en `--clave valor`; con valor vacío
  solo `--clave` (flags). `pairs()` no garantiza orden.
- `js-runtimes=node` → `--js-runtimes node` (verificado en el comando real más abajo).
- Claves especiales: `sub-langs`/`sub-lang`/`srt-lang` (desactiva `--sub-langs all`), `proxy` (también se
  pasa a ffmpeg como `http_proxy`), `yes-playlist` (quita `--no-playlist` y ajusta `playlist-start`).
- Siempre añade `--no-warnings -J --flat-playlist --sub-format ass/srt/best … --write-srt`.
- Además, tras el JSON: fija `file-local-options/force-media-title`, `file-local-options/hls-bitrate`,
  `file-local-options/start`/`end` (solo si el usuario no los puso; usa `start_time`/`section_start` del
  JSON), `video-aspect-override`, cabeceras HTTP y cookies en `file-local-options/stream-lavf-o`, capítulos
  (`chapter-list` en `on_preloaded`), subtítulos (`sub-add` con `edl://!no_clip;!delay_open,media_type=sub`).

### (c) Propiedades que expone (nombres exactos, verificados por IPC)
- `user-data/mpv/ytdl/path` — cadena; se escribe tras la primera búsqueda (`mp.set_property(...)`).
- `user-data/mpv/ytdl/json-subprocess-result` — mapa con **exactamente** las claves del resultado de
  `subprocess`: `status`, `stdout` (JSON crudo de `yt-dlp -J`, ~15 KB en la prueba), `stderr`,
  `error_string`, `killed_by_us`. Se publica con `mp.set_property_native` **antes** de parsear (así que
  también está en fallos) y se borra en el hook `on_after_end_file` (`mp.del_property`).
  No hay ninguna otra propiedad (`ytdl_hook/...`, `ytdl_json`, etc.): grep del script solo da esas dos.
- No expone el JSON parseado: hay que hacer `utils.parse_json(result.stdout)` en el consumidor.

### (d) `all_formats=yes`: formatos como pistas
Con `all_formats=yes` (y `force_all_formats=yes` por defecto) el script construye un `edl://` con un
`!new_stream` por formato y cabeceras `!delay_open,media_type=video|audio,codec=…` +
`!track_meta,title=…,byterate=…[,flags=default]`. Título de pista (código `formats_to_edl`):
`track.format or track.format_note`, y si el formato lleva vídeo y audio juntos se añade `" muxed-<format_id>"`.
Los formatos que yt-dlp habría elegido llevan `flags=default`. En la prueba real (archive.org, 4 formatos
muxed) `track-list` tuvo **8 pistas**:
```
id=1 video "h.264 muxed-4"       codec=null 643x480  selected default
id=1 audio "h.264 muxed-4"       codec=null          selected default
id=2 video "Ogg Video muxed-3"   codec=null 400x304
id=2 audio "Ogg Video muxed-3"   codec=null
id=3 video "512Kb MPEG4 muxed-2" codec=null 321x240
id=3 audio "512Kb MPEG4 muxed-2" codec=null
id=4 video "MP3 muxed-1"         codec=null 0x0
id=4 audio "MP3 muxed-1"         codec=mp3
```
(`codec=null` porque son delay-open: se abren al seleccionarlas). Cambio en caliente:
`set_property vid 2` → `error: success`, y a los 4 s `video-params` pasó a 400x304 sin cortar la
reproducción (`time-pos` seguía avanzando). Para "muxed" hay que cambiar `vid` y `aid` al mismo N para no
descargar dos ficheros. `hls-bitrate` seguía en `max` (byterate=0 en este sitio). El manual advierte:
> Although this mechanism makes it possible to switch streams at runtime, it's not suitable for this purpose for various technical reasons. (It's slow, which can't be really fixed.)
Con `all_formats` las etiquetas (`artist`, `album`…) no se aplican ("tags don't work with all_formats=yes").

### (e) CLAVE: ¿lee las opciones en caliente?
**Sí.** El script llama una sola vez, al arrancar:
```lua
options.read_options(o, nil, function()
    ytdl.blacklisted = {} -- reparse o.exclude next time
    ytdl.searched = false
end)
```
y `read_options` con tercer argumento (`on_update`) instala un observador permanente
(implementación embebida de `mp.options`):
```lua
if on_update then
    local last_opts = opt_table_copy(options)
    mp.observe_property("options/script-opts", "native", function(_, val)
        local new_opts = opt_table_copy(conf_and_default_opts)
        parse_opts(val, new_opts)
        … options[k] = opt_copy(v); changelist[k] = true …
        if next(changelist) ~= nil then on_update(changelist) end
    end)
end
```
`lua.rst`: "The `on_update` parameter enables run-time updates of all matching option values via the
`script-opts` option/property. […] There is no initial `on_update()` call. This never re-reads the config
file. `script-opts` is always applied on the original config file, ignoring previous `script-opts` values".
Consecuencias:
- Cualquier cambio de `script-opts` que toque una clave `ytdl_hook-*` actualiza la tabla `o` **en el
  momento** y el callback fuerza a repetir la búsqueda del ejecutable en la siguiente carga
  (`ytdl.searched = false`). No importa el orden de carga de scripts ni que sea antes del primer `loadfile`.
- Si se **elimina** la clave de `script-opts`, vuelve al valor del `.conf`/por defecto.
- Solo cambia lo que difiere: reponer el mismo valor no dispara el callback.

**Cómo fijarla desde otro script Lua (probado por IPC, equivalente en Lua):**
- `mp.set_property('script-opts/ytdl_hook-ytdl_path', ruta)` → **NO funciona**: `error accessing property`
  (`script-opts` no admite sub-rutas; solo `user-data` las tiene).
- `mp.commandv('change-list', 'script-opts', 'append', 'ytdl_hook-ytdl_path=' .. ruta)` → funciona
  (probado: cambió la clave y la siguiente carga usó la nueva ruta). `append` no interpreta escapes en el
  valor, así que las rutas con `,` van seguras.
- `mp.set_property_native('script-opts', tabla)` con el mapa completo → funciona (probado), pero
  sobreescribe todo el mapa: hay que leer el mapa actual, añadir la clave y volver a escribir.
- También vale en `mpv.conf`: `script-opts-append=ytdl_hook-ytdl_path=/ruta` o
  `script-opt=ytdl_hook-ytdl_path=/ruta` (alias de `-append`, solo CLI/config).

---

## 3. Pruebas reales (headless, desde la raíz del proyecto)

Nota: `vendor/dl/yt-dlp` (sin versión) no existe; existe `vendor/dl/yt-dlp-2026.08.19`, idéntico en md5
(`b0d7ba4a…`) a `/usr/local/bin/yt-dlp`. Se usó el vendorizado (ruta fuera de PATH, que es el caso que interesa).

### Prueba 1 — carga simple con zipimport y `js-runtimes=node`
```
timeout 90 bin/mpv-uos --vo=null --ao=null --msg-level=ytdl_hook=debug \
  --script-opts=ytdl_hook-ytdl_path=/home/pc/Documentos/PROJECTES/MPV-UOS/vendor/dl/yt-dlp-2026.08.19 \
  --ytdl-raw-options=js-runtimes=node --end=2 --input-ipc-server=tmp/ytdl-probe.sock --idle=no \
  --log-file=tmp/ytdl-probe1.log 'https://archive.org/details/Countdow1960'
```
Log de ytdl_hook (literal):
```
[ytdl_hook] reading options for ytdl_hook
[ytdl_hook] script-opts/ytdl_hook.conf not found.
[ytdl_hook] Found youtube-dl at: /home/pc/Documentos/PROJECTES/MPV-UOS/vendor/dl/yt-dlp-2026.08.19
[ytdl_hook] Starting subprocess: [/home/pc/Documentos/PROJECTES/MPV-UOS/vendor/dl/yt-dlp-2026.08.19, --no-warnings, -J, --flat-playlist, --sub-format, ass/srt/best, --format, bestvideo[height<=?1080]+bestaudio/best, --js-runtimes, node, --sub-langs, all, --write-srt, --no-playlist, --, https://archive.org/details/Countdow1960]
[ytdl_hook] youtube-dl succeeded!
[ytdl_hook] ytdl parsing took 0.004432 seconds
[ytdl_hook] format selection: youtube-dl (separate)
[ytdl_hook] streamurl: edl://!new_stream;!no_clip;!no_chapters;%58%https://archive.org/download/Countdow1960/Countdow1960.mp4;!global_tags,date=%8%20020716,ytdl_description=%17%Countdown leader.,title=%12%Countdow1960
● Video  --vid=1  (h264 640x480 29.97 fps) [default]
● Audio  --aid=1  (aac 2ch 44100 Hz 123 kbps) [default]
AV: 00:00:02 / 00:00:14 (14%) … (Paused)
```
- "Found youtube-dl at:" sale porque `mp.find_config_file` acepta rutas absolutas existentes.
- El zipimport con shebang funcionó (exit 0, JSON válido). `--js-runtimes node` se pasó tal cual.
- Reprodujo hasta `--end=2` y luego se quedó **en pausa** (`keep-open=yes` en `mpv-config/mpv.conf`);
  por eso `timeout 90` mató mpv (exit 124, "Exiting... (Quit)"). Para tests automáticos añadir
  `--keep-open=no`. Los errores `[ffmpeg/video] h264: Device does not support VK_KHR_video_decode_queue`
  son de `hwdec=auto-safe` probando Vulkan y cayendo a software; no afectan.

### Prueba 2 — `all_formats=yes` + inspección por IPC (`--idle=yes`)
Driver: `tmp/ytdl_probe_ipc.py`. Resultado: `file-loaded`; `format selection: all_formats (separate)`;
`track-list` con las 8 pistas listadas en 2d; `user-data` contiene `mpv.ytdl.path` y
`mpv.ytdl.json-subprocess-result` (`status=0`, `stdout` 15034 bytes, `stderr` vacío,
`error_string=""`, `killed_by_us=false`) más las claves propias del proyecto (`user-data/mu/...`).
`media-title` = "[Countdown Leader: Technicolor Corporation]" (de `force-media-title`).
`set_property vid 2` cambió a 400x304 en caliente.

### Prueba 3 — cambio de `ytdl_path` en caliente
Driver: `tmp/ytdl_probe3b.py` (log `tmp/ytdl-probe3b.log`). Secuencia y resultado:
1. `--script-opts=ytdl_hook-ytdl_path=<buena>` → `loadfile` → `file-loaded`.
2. `set_property script-opts/ytdl_hook-ytdl_path /ruta/que/no/existe` → **`error accessing property`**
   (sub-ruta no soportada; el mapa no cambió).
3. `change-list script-opts append ytdl_hook-ytdl_path=/ruta/que/no/existe` → `success`;
   `loadfile` → `end-file reason=error file_error="unrecognized file format"`. Log:
   `No youtube-dl found with path /ruta/que/no/existe in config directories` →
   `Starting subprocess: [/ruta/que/no/existe, …]` → `Subprocess failed: init` →
   `youtube-dl failed: not found or not enough permissions`.
4. `set_property script-opts {"ytdl_hook-ytdl_path": <buena>}` → `loadfile` → `file-loaded` de nuevo.
5. `ytdl_path="/ruta/que/no/existe:<buena>"` → prueba la mala, falla `init`, prueba la buena, `file-loaded`.

**Conclusión: el script relee `script-opts` en caliente (observador) y repite la búsqueda del ejecutable en
cada carga posterior a un cambio.** No hace falta reiniciar mpv ni fijar la opción antes de que cargue
ytdl_hook.

---

## 4. Recomendaciones para MPV-UOS (derivadas de lo anterior)
- Fijar la ruta desde `mu-core` al arrancar con
  `mp.commandv("change-list", "script-opts", "append", "ytdl_hook-ytdl_path=" .. ruta_absoluta)`;
  admite lista `a:b` (Unix) / `a;b` (Windows) para fallback (vendorizado primero, luego `yt-dlp` de PATH).
- No usar `script-opts/<clave>`; no confiar en `user-data/mpv/ytdl/path` como prueba de éxito (queda
  el valor antiguo tras un fallo); usar `json-subprocess-result.status` o el evento `end-file`.
- Leer el JSON de yt-dlp desde `user-data/mpv/ytdl/json-subprocess-result.stdout` en `file-loaded`
  (se borra en `on_after_end_file`).
- Para "solo audio": `--vid=no` con `ytdl-format` vacío hace `bestaudio/best`; con el `ytdl-format` del
  proyecto (no vacío) hay que pasar `loadfile url replace -1 ytdl-format=bestaudio/best,vid=no`.
- `all_formats=yes` funciona y permite cambiar `vid`/`aid` en caliente, pero es lento y el manual lo
  desaconseja; para cambiar calidad es más fiable recargar con otro `ytdl-format`.
- En tests headless añadir `--keep-open=no` (el `mpv.conf` del proyecto tiene `keep-open=yes`).
- Windows: sin shebang; necesita `yt-dlp.exe` (documentar en docs/PLATAFORMAS.md).
