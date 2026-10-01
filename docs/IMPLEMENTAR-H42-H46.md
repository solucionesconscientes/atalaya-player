# Implementar H42–H46 · instrucciones para la sesión siguiente

Este documento es el traspaso completo. Léelo entero antes de tocar nada, junto con `CLAUDE.md` (reglas del
proyecto), `docs/ANALISIS-SALA-E-INTERFAZ.md` (el análisis del que salen estos hitos, con todas las medidas) y
`BACKLOG.md` (los criterios de aceptación, H42–H46).

## 0 · De dónde viene esto

Ser probó el reproductor de verdad, dijo que la interfaz es poco intuitiva, que en los vídeos de YouTube no aparece
el resumen y que el vídeo de la sala tarda mucho desde el móvil. De ahí salieron dos análisis (ya escritos y
aprobados: «todo ok, quiero implementarlo») y cinco hitos. **Nada de H42–H46 está empezado.** Lo anterior, H35–H40,
está hecho; H41 (el nombre de la aplicación) lo decide Ser y no se toca.

Todo lo que lleva un número en este documento está medido en este portátil (4 núcleos, sin GPU dedicada). No
vuelvas a medirlo: úsalo.

## 1 · Reglas que no se negocian

- Todo dentro de `/home/pc/Documentos/PROJECTES/MPV-UOS/`. Nada de sudo ni de paquetes del sistema.
- **No toques `~/.config/mpv`.** mpv se ejecuta siempre con `--config-dir=<proyecto>/mpv-config`.
- Rama actual `nocturno/2026-09-28`. Commits pequeños y en español. Nunca push, rebase ni merge a main.
- `tools/check.sh` en verde antes de cada commit, y **con el portátil descargado**: los tests de Whisper y los de
  navegación por teclas fallan por tiempo si hay carga. No lances subagentes ni benchmarks mientras corre.
- Mata solo los PID que tú hayas arrancado. **Prohibido `pkill -f`, `killall` y `ps | grep | xargs kill`**: cazan
  al propio runner, a Claude y a los programas de Ser.
- Código y comentarios en inglés; documentación, menús y commits en español.
- Al cerrar cada hito: check.sh verde → commit → ADR en `docs/DECISIONS.md` si hubo decisión → actualizar
  `BACKLOG.md` y `PROGRESS.md` → registrar en Notion (`/registrar`).

## 2 · Tres trampas que ya me costaron tiempo

1. **`str.replace` de Python no avisa si no encuentra nada.** Si editas Lua o Python con un script, haz siempre
   `assert old in s` antes de reemplazar. Perdí una iteración con dos ramas de despacho que nunca se insertaron.
2. **LuaJIT no tiene `//` y no ve hacia adelante.** Si una función se usa antes de definirse, declárala arriba
   (`local watch_stall`) y asígnala después (`watch_stall = function(...)`). `check.sh` ya pasa `luajit -bl`.
3. **Monkeypatchear propiedades de solo lectura no funciona.** En los tests, parchea lo de dentro (`binary`), no la
   propiedad calculada (`available`). Y lee las variables de entorno **en cada llamada**, no al importar el módulo,
   o los tests no podrán cambiarlas.

## 3 · El orden y por qué

**H42 → H43 → H45 → H44 → H46.** H42 es lo que más se nota al usar el programa. H43 son tres arreglos pequeños y
visibles. H45 son cinco segundos de trabajo del programa que arreglan la queja más concreta de Ser. H44 es el más
grande y además arregla un fallo real de memoria. H46 se deja al final porque retira cosas, y conviene retirarlas
cuando lo nuevo ya esté en su sitio.

---

## H42 · Una sola puerta para abrir y descargar

**Qué está mal hoy:** hay seis puertas para lo mismo. En `mu-menu` (`mpv-config/scripts/mu-menu/main.lua`,
`views.open`, línea ~356) están «Biblioteca», «Música», «Abrir archivo», «Abrir URL (YouTube y otras webs)…»,
«Buscar en YouTube», «Pegar URL o ruta copiada», «Recientes» y «Lista de reproducción»; y además `CATEGORIES`
(línea ~300) tiene «Descargas y conversión» como categoría aparte.

**Qué hacer:**

1. Una entrada **«Abrir o descargar»** que acepte cualquier cosa en un solo campo de texto: un enlace, varios
   enlaces pegados (separados por espacios o saltos de línea), una lista de reproducción, un canal entero, una ruta
   local, o lo que haya en el portapapeles si el campo se deja vacío. El reconocimiento ya existe casi todo en
   `mu-ytdl`: mira `views.add` (línea 1124), `views.picklist` (1175) y `views.pickfmt` (1227), y los bindings
   `open-url` (1687) y `yt-search` (1688), que hoy abren paletas distintas.
2. Después de reconocer lo que es, **una sola pregunta: reproducir o descargar**. Al descargar, lleva a la pantalla
   actual de descargas, que está bien: parámetros para todos y la posibilidad de cambiar vídeos uno a uno
   (eso se hizo en H37 agrupando por `(preset, srt)` en `pick_download()`).
3. Retira del primer nivel las otras puertas. Siguen existiendo como atajos de teclado (`o`, `ctrl+u`, `ctrl+f`,
   `ctrl+v`) y dentro de la entrada nueva. «Suscripciones» pasa a vivir donde corresponda, no en la raíz.
4. **Botón de copiar en todo enlace que haya que llevarse a otro aparato**: sala (`mu-share`), mando desde el móvil
   (`mu-remote`), panel de descargas. Con aviso en el OSD de que se ha copiado.

**Ojo con esto:** el ayudante para copiar **ya existe tres veces**, duplicado, en `mu-iptv/main.lua:278`
(`copy_to_clipboard`, con `clipboard/text` nativo de mpv 0.41 y respaldo a `wl-copy`/`xclip`/`xsel`/`pbcopy`),
`mu-remote/main.lua:236` y `mu-share/main.lua:648`. Extrae **uno** a `mpv-config/script-modules/mu/clip.lua` (es
donde viven los módulos compartidos: `brand`, `nav`, `prefs`, `rpc`, `uosc`) y que los tres lo usen. No escribas un
cuarto.

**Criterio de aceptación:** un test que pegue en el campo único (a) un enlace de vídeo, (b) dos enlaces, (c) una
lista y (d) una ruta local, y compruebe que en los cuatro casos aparece la pregunta reproducir/descargar con el
número correcto de elementos. Y un test de que copiar un enlace escribe en `clipboard/text`.

---

## H43 · Grabar: formato, programación visible y radio

**B1 · La fila de formato.** Hoy no se puede elegir nada: `KIND_HINT` (`mu-record/main.lua:108`) solo
*informa* («directo», «sin recodificar», «tramo del vídeo de internet»). Antes de diseñarla, entiende cómo se graba:
`mp.set_property('stream-record', file)` (línea 273), es decir **mpv escribe el flujo tal cual**, sin recodificar, en
el contenedor que implique la extensión. Así que la fila no puede ofrecer «calidad»; lo que puede ofrecer es:

- **Igual que el original (MKV, sin recodificar)** — el valor por defecto, el que nunca falla.
- **MP4 si los códecs lo permiten** — y si no, avisa y deja MKV. Comprobado midiendo: un VP8 **no** entra en MP4
  (`Could not write header (incorrect codec parameters?)`), y sí entra en WebM copiando (9,64 s para 115 min).
- **Solo audio (Opus 128)** — ya existe como paso posterior: `rpc.call('record.audio', {file=…, remove=true})`
  (línea 294). Reutilízalo, no escribas otro.

La elección se recuerda (usa `mu/prefs.lua`, como el resto).

**B2 · Programar visible.** Hoy solo se llega con Tab dentro de la lista de un canal
(`mu-iptv/main.lua:310` y `:995`). Pon «Programar una grabación» en el primer nivel de «TV y radio» **y** en el menú
de «Grabar».

**B3 · La radio se puede programar.** Dos condiciones lo impiden: `mu-iptv/main.lua:449` esconde la acción Tab y
`:1042` excluye la radio del selector de canal, las dos con `ch.kind ~= 'radio'`. Quítalas. **mpvd ya graba audio
bien**, es solo la interfaz; comprueba de paso que `iptv.schedule.add` no asume vídeo en ningún sitio.

**B4** Al volver a pulsar el botón de grabar ya termina sin preguntar (se hizo en H35, `set_button()` con
`stopping`). Solo hay que comprobar que sigue así con la fila de formato nueva.

---

## H45 · Que el resumen funcione en los vídeos de internet

**La causa, ya verificada:** `mu-recap/main.lua`, función `source()` (línea ~120-135): necesita una pista de
subtítulos cargada, y para una URL acaba en `if path and not is_url(path)` → `nil`. Y `mpv-config/mpv.conf` **no**
pasa `ytdl-raw-options`, así que mpv nunca pide los subtítulos automáticos de YouTube. El resumen no está roto: se
queda sin material.

**Qué hacer:**

1. Para una URL, pedir los subtítulos automáticos **antes** de plantearse transcribir. Medido con `yt-dlp
   --skip-download --write-auto-subs --sub-langs en --sub-format srt`: **5,41 s**, 23 KB, 3.678 palabras en un vídeo
   de 15 minutos, frente a los ~8 minutos de Whisper.
2. **Pide solo los idiomas nativos del vídeo.** Pedirle a YouTube una traducción automática (`--sub-langs es` en un
   vídeo en inglés) devuelve **HTTP 429**. El castellano se consigue traduciendo el SRT con OPUS-MT, que además da
   mejor resultado; el motor ya está (`mpvd/subs/opus.py`, `default_engine = "opus-big"`).
3. «Resumen e índice» **en la raíz del menú** y como fila dentro del panel de subtítulos. Hoy «¿Qué me he perdido?»
   está en el tercer nivel (dentro de «Herramientas», que tiene 15 filas) y «Índice del vídeo» no está en `mu-menu`
   en absoluto: solo existe como `alt+I` y una entrada del árbol `#!`.

Decide si los subtítulos se piden solos al abrir la URL o al pulsar «Resumen». Mi recomendación: **al pulsar**, para
no hacer una petición de red por cada vídeo que Ser abra, y con el aviso de «buscando subtítulos…» que ya sabe
pintar `mu-recap`.

---

## H44 · La sala: fichero original y el reproductor del invitado

Lee `docs/ANALISIS-SALA-E-INTERFAZ.md` §A entero antes de empezar. Resumen de las medidas que justifican el diseño:

- Subida de esta casa: **~167 Mb/s** (22,7 y 19,1 MB/s). Caben 15 invitados con una película 1080p buena, 60 con una
  normal. **La red no es el problema**, así que no hay nada que repartir y WebTorrent queda descartado (C7).
- El problema es el empaquetado: el relay arranca en el segundo 0 y recodifica a **2,2× tiempo real** con VA-API, y
  `mpvd/share/www/room.js` (`correct()`, `ready()`) deja al invitado en pausa con «Preparando la retransmisión…»
  mientras `hostPos() > ready() - 1`. Entrar en el minuto 40 = **~18 minutos** de espera.
- Servir el fichero original y abrirlo en mpv: **primer fotograma en 0,36 s, salto al minuto 98 en 0,07 s**, servidor
  en **2 MB de RSS**, sin recodificar nada. Medido con un MKV de 428 MB y 1 h 55 min.
- Remux sin recodificar para el navegador: **9,64 s para 115 min** (~715× tiempo real), 54 MB de RAM.

**C1 · El handler (hazlo primero, es un fallo real).** `mpvd/share/service.py::_file()` hace
`data = path.read_bytes()` y corta el rango sobre ese buffer: una película de 4 GB serviría 4 GB de RAM **por
petición**. Hoy no se nota porque solo sirve segmentos HLS de 4 s. Reescríbelo para leer por trozos (256 kB va bien).
El Range ya está bien implementado (206, `Content-Range`, `Accept-Ranges`, 416 cuando toca): conserva ese
comportamiento, solo cambia de dónde salen los bytes. **Hay un prototipo medido** en el scratchpad de la sesión
anterior; si ya no está, son 30 líneas.

**C2 · La ruta.** `GET /s/<sala>/file`, dentro de `handle()` (junto a `media/` y `subs/`), con la misma cookie de
sala que el resto. No publiques la ruta real del fichero: `_media_public()` ya quita `path` a propósito, mantén eso.

**C3 · «Abrir en mi reproductor»** en la página del invitado, con tres formas porque cada sistema va mejor con una:
botón **copiar el enlace**, descarga de un **`.m3u`** (doble clic lo abre en VLC o mpv en los tres sistemas) y la
línea `mpv "<enlace>"` para pegar en un terminal. Añade la posición del anfitrión en vivo («va por 12:34») con un
botón para copiarla: es la sincronía de los pobres y para ver una película con una llamada al lado sobra.

**C4 · Elegir el camino sin preguntar:**
- fichero local con códecs de navegador (H.264/AAC en MP4) → **el original**, y el navegador lo reproduce;
- fichero local con otros códecs → **las dos cosas a la vez**: «abrir en tu reproductor» (original, perfecto) y
  «ver en el navegador» (remux copiando a fMP4 o WebM; recodificar **solo** si los códecs no lo permiten);
- TV, radio, YouTube o grabación en curso → relay, como hoy.

Dato útil para no equivocarte con el navegador: las grabaciones de Ser son **HEVC + Opus en MP4 con el `moov` al
final** (verificado con ffprobe en `Video_2026-09-07_10-21-35.mp4`), que es justo lo que el navegador lleva peor.
No des por bueno que «es un MP4, se verá».

**C5 · El relay empieza donde está el anfitrión**, no en el segundo 0, y la página lo dice («empezamos donde va el
anfitrión») en vez de dejar al invitado esperando. Esto es lo que arregla la queja de Ser.

**C6 · Unirse a la sala desde MPV-UOS**, no solo desde el navegador: el mpv del invitado sigue pausa, saltos y
velocidad por el SSE que ya existe. Es barato porque los dos extremos son código nuestro, y es **la única forma de
«ver juntos» exacto a calidad original**. Hoy `mu-share` solo sabe ser anfitrión (`views.guest` es la ficha de un
invitado visto desde el anfitrión, no el modo invitado).

**C7 · ADR** con WebTorrent como no-objetivo y estos números. Y ojo con la regla 5 de Notion: la decisión nueva
**matiza** el «qué reproduce el invitado» de **ADR-054**, que por lo demás sigue en pie (token en el fragmento,
cookie firmada, SSE, permisos). Cuando escribas el ADR nuevo, marca en Notion la entrada vieja como no vigente y
usa `Sustituye a`; hasta ahora la dejé vigente a propósito, porque el código seguía haciendo lo de antes.

---

## H46 · Un solo menú

- Retira el árbol `#!` de `mpv-config/input.conf`: **119 entradas, 40 en el primer nivel**, que montan un segundo
  menú (`ctrl+m`, `script-binding uosc/menu`) peor que el nuestro. `input.conf` se queda solo para las teclas.
- Quita las cuatro duplicaciones: Biblioteca, Música, Audiolibros y Saltar intro aparecen **dos veces**, una como
  entrada suelta y otra como submenú.
- Raíz de **ocho filas o menos**, nombradas por lo que quieres hacer. `mu-menu` ya está así (`CATEGORIES`, línea
  ~300: Abrir, TV y radio, Descargas y conversión, Subtítulos, Imagen y sonido, Grabar, Herramientas, Preferencias)
  y es el menú de `MENU`, clic derecho y `alt+m`: el trabajo es retirar el otro, no rehacer este.
- `docs/ATAJOS.md` se genera o se comprueba contra `input.conf`: actualízalo en el mismo commit.

**Criterio:** ningún test puede depender del árbol `#!`. Si alguno lo hace, arréglalo en este hito.

## 4 · El criterio de simplificación, para no reintroducir el problema

- Una función, **una puerta**.
- La raíz: ocho filas como máximo, nombradas por lo que quieres hacer, no por el script que lo hace.
- Lo que solo tiene sentido para el archivo que estás viendo va **en la pantalla** (un botón o el panel de su propia
  función), nunca en la raíz.
- Nada en un menú que el programa pueda decidir solo (formato, modelo, motor): valor por defecto y «cambiar» dentro.

## 5 · Lo que no se toca

- **H41, el nombre.** Lo decide Ser. Vive en `brand.json` y en ningún otro sitio.
- Las cinco cosas que necesitan a Ser están en `NEEDS_HUMAN.md` y en las tareas de Notion: regla de sudoers para
  `rtcwake`, usuario y contraseña de OpenSubtitles, clave de Subdl, y los puertos 8790 y 8791 de ufw.
- `uosc` no se edita: solo su API pública. Si hiciera falta, parche documentado en `patches/`.
