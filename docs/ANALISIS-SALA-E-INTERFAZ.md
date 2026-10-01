# Análisis: la sala para ver juntos y la interfaz

Fecha: 2026-10-01. Todo lo que aquí lleva un número está **medido en este equipo** (4 núcleos, sin GPU dedicada),
no estimado. Lo que no se ha podido medir se dice claramente.

## Parte A · La sala para ver juntos

### A.1 Por qué hoy tarda tanto (diagnóstico)

Tres causas, las tres verificadas en el código y en el banco de pruebas:

1. **El relay empieza en el segundo 0 del vídeo, no donde está el anfitrión.** `mpv-config/.../mu-share` pide
   el relay y `mpvd/share/www/room.js` (`correct()`) deja al invitado en pausa con «Preparando la retransmisión…»
   mientras `hostPos() > ready() - 1`. Es decir: el invitado espera a que el empaquetado *alcance* la posición
   del anfitrión. Medido: recodificando con VA-API (`h264_vaapi -low_power 1`) el relay va a **2,2× tiempo real**
   (18,02 s para 40 s de vídeo). Si entras en el minuto 40, esperas ~18 minutos. Eso es exactamente lo que viste.
2. **El servidor carga el fichero entero en RAM.** `mpvd/share/service.py::_file()` hace `data = path.read_bytes()`
   y luego corta el rango sobre ese buffer. Implementa Range correctamente (206, `Content-Range`, `Accept-Ranges`),
   pero una película de 4 GB serviría 4 GB de RAM **por petición**. Hoy no se nota porque solo se sirven segmentos
   HLS de 4 s; sería un fallo grave en cuanto se sirva el fichero original.
3. **La subida no es el problema.** Medida con el endpoint `__up` de Cloudflare, dos pasadas con 25 MB de cuerpo:
   **22,7 y 19,1 MB/s → ~167 Mb/s de subida** (y 55 MB/s de bajada). Con eso caben:

   | contenido | bitrate | invitados (20 % de margen) |
   |---|---|---|
   | grabación de pantalla propia | 0,5 Mb/s | 257 |
   | película 1080p normal (2 GB / 2 h) | 2,2 Mb/s | 60 |
   | serie 720p (1,5 GB / 45 min) | 4,4 Mb/s | 30 |
   | película 1080p buena (8 GB / 2 h) | 8,9 Mb/s | 15 |
   | remux 4K (25 GB / 2 h) | 27,8 Mb/s | 4 |

   Conclusión importante para lo que viene: **el cuello de botella es el empaquetado, no la red.**

### A.2 Las seis opciones, medidas

| # | Cómo | CPU del anfitrión | Arranque | Saltos | Códecs | Sincronía | Falta por hacer |
|---|---|---|---|---|---|---|---|
| 1 | **Relay HLS** (lo de hoy) | 2,2× tiempo real recodificando; 118× con `-c copy` | minutos si entras tarde | solo donde ya hay segmentos | cualquiera (se recodifica) | exacta, ya hecha | empezar donde está el anfitrión |
| 2 | Fichero original + Range, en el navegador | **cero** | instantáneo | instantáneo | **solo los del navegador** | exacta, ya hecha | handler que no cargue en RAM |
| 3 | Remux a fMP4/WebM + Range, navegador | **9,64 s para 115 min** (~715× tiempo real), 54 MB de RAM | instantáneo | instantáneo | los del navegador, sin recodificar | exacta, ya hecha | elegir contenedor según códec |
| 4 | **Fichero original + Range, el invitado en mpv/VLC** | **cero** | **0,36 s** | **0,07 s** | **todos** | manual, o exacta si el invitado también tiene MPV-UOS | handler + bloque «abrir en tu reproductor» |
| 5 | WebTorrent (navegador) | hash del fichero entero + semilla | segundos a minutos | sí | solo los del navegador | **ninguna** (habría que usar la nuestra igual) | tracker wss, WebRTC, ~2-3 MB de JS, STUN/TURN |
| 6 | Torrent + cualquier reproductor | hash del fichero entero | segundos | sí | todos | ninguna | cliente torrent en cada invitado |

Medidas de la opción 4, hechas con una película real (MKV de 428 MB, 1 h 55 min) servida por un handler Range
que lee por trozos de 256 kB: **primer fotograma en 0,36 s, salto al minuto 98 en 0,07 s, y el servidor se queda
en 2.052 kB de RSS** (frente a los 428 MB que ocuparía el código actual). Sin recodificar nada.

### A.3 WebTorrent frente a lo que propongo

**Qué resuelve WebTorrent:** repartir el ancho de banda de subida entre los invitados. Es su única ventaja real.

**Por qué aquí no hace falta:** la subida medida es de ~167 Mb/s, que da para 15 invitados con una película 1080p
buena y 60 con una normal. Las salas de las que hablamos son de 2 a 5 personas. WebTorrent resolvería un problema
que no tenemos.

**Qué *no* resuelve, y conviene tenerlo claro:**
- **Los códecs.** WebTorrent pinta en la misma etiqueta `<video>` del navegador, así que sigue limitado a MP4/WebM.
  Y esto no es teórico: tus propias grabaciones son **HEVC + Opus dentro de MP4, con el `moov` al final**
  (verificado con ffprobe en `Video_2026-09-07_10-21-35.mp4`). HEVC solo va fino en Safari y en Chrome con
  soporte por hardware; en Firefox no va. Opus dentro de MP4 no va en Safari. Es decir: el formato que *tú*
  generas es justo el que el navegador lleva peor, con WebTorrent o sin él.
- **La sincronía.** Un torrent no tiene concepto de «vamos juntos». Habría que seguir usando el canal SSE que ya
  tenemos, así que no ahorra nada de código.
- **El directo.** Un torrent es una lista de ficheros fija. TV, radio y una grabación en curso **no se pueden
  servir por torrent**. Seguirían necesitando el relay HLS. WebTorrent sería un segundo camino, no un sustituto.

**Qué cuesta:**
- El anfitrión tiene que calcular el SHA-1 de todas las piezas antes de empezar: leer la película entera (4 GB de
  lectura antes del primer byte útil).
- Cada invitado sube a los demás. En datos móviles eso es un gasto que no ha pedido; es el caso del QR en el móvil,
  precisamente.
- WebRTC entre dos NAT necesita STUN y, en una parte de los casos, **TURN**: otro servidor que mantener. Se cambia
  «un servidor mío» por «un servidor mío *más* un tracker *más* a veces un TURN».

**Veredicto:** no-objetivo documentado. Merecería revisarse solo si (a) la subida medida bajara de ~20 Mb/s o
(b) las salas pasaran de ~15 personas. Si algún día se hace, el camino correcto es el 6 (torrent normal + cualquier
reproductor), no el 5, porque el 5 hereda las limitaciones del navegador sin quitar ninguna.

### A.4 «Que el invitado lo reproduzca en mpv o en otro reproductor»

Es la mejor de las ideas sobre la mesa, y por un margen amplio: **cero CPU del anfitrión, calidad original, todos
los códecs, saltos instantáneos** (0,36 s y 0,07 s medidos). El invitado ya tiene un reproductor o se instala uno,
y a cambio ve la película tal cual es, no una versión recomprimida a 2,2× tiempo real.

Cómo encajarlo:

1. **Una ruta nueva** `GET /s/<sala>/file` que sirva el fichero original con Range leyendo por trozos. El prototipo
   ya está medido (2 MB de RSS). De paso arregla el `read_bytes()` de `_file()`, que es un fallo real esperando a
   que alguien comparta algo grande.
2. **Un bloque «Abrir en mi reproductor»** en la página del invitado, con tres formas de llevárselo porque cada
   sistema va mejor con una: botón **copiar el enlace**, descarga de un **`.m3u`** (doble clic lo abre en VLC o mpv
   en Windows, macOS y Linux) y la línea `mpv "<enlace>"` para pegar en un terminal.
3. **La sincronía, en tres niveles honestos:**
   - *Sin sincronía*: la página muestra en vivo por dónde va el anfitrión («va por 12:34») con un botón para copiar
     esa posición, y el invitado salta a mano. Para ver una película con una llamada al lado, sobra.
   - *MPV-UOS en los dos lados*: el enlace de la sala se puede abrir **en la aplicación**, y entonces el mpv del
     invitado sigue pausa, saltos y velocidad por el mismo SSE que ya existe. Es barato porque los dos extremos son
     código nuestro, y es la única forma de tener «ver juntos» exacto **a calidad original**. Esto es lo que
     convierte la idea en la opción buena en vez de en un apaño.
   - *Intermedio*: una pestaña mínima que solo muestre la deriva («vas 4 s por delante») y un botón para igualar.
4. **Lo que no puede hacer:** solo vale para ficheros locales. Un vídeo de YouTube, la TV o la radio no se pueden
   servir así; esos se quedan con el relay. Y el túnel gratuito de Cloudflare no tiene garantías: va bien para una
   película, pero se cae tras unas horas.

### A.5 Qué construiría (hito H44)

1. Handler Range por trozos (arregla el fallo de RAM).
2. Decidir según el origen, sin preguntar:
   - fichero local con códecs de navegador (H.264/AAC en MP4) → **servir el original**, el navegador lo reproduce.
   - fichero local con otros códecs → ofrecer **las dos cosas a la vez**: «abrir en tu reproductor» (original,
     perfecto) y «ver en el navegador» (remux `-c copy` a fMP4 o WebM, 10 s para 2 h; recodificar solo cuando los
     códecs no lo permitan).
   - TV, radio, YouTube, grabación en curso → relay, pero **empezando donde está el anfitrión** y diciéndolo
     («empezamos donde va el anfitrión») en vez de dejar al invitado esperando.
3. Bloque «Abrir en mi reproductor» con copiar y `.m3u`.
4. Unirse a la sala desde MPV-UOS para la sincronía exacta.
5. WebTorrent: no-objetivo, con estos números en `docs/DECISIONS.md`.

## Parte B · La interfaz

### B.1 Estado medido

- **Dos menús en paralelo.** `mu-menu` (tecla MENU, clic derecho, alt+m) está bien: 8 categorías
  (Abrir, TV y radio, Descargas y conversión, Subtítulos, Imagen y sonido, Grabar, Herramientas, Preferencias),
  con «Continuar viendo» arriba y modo sencillo. Pero además existe el menú nativo de uosc (ctrl+m), construido
  con los 119 comentarios `#!` de `input.conf`, que tiene **40 entradas en el primer nivel** y duplica cuatro cosas
  (Biblioteca, Música, Audiolibros y Saltar intro aparecen dos veces, una como entrada y otra como submenú).
- 75 atajos de teclado, 22 scripts `mu-*`, 266 métodos RPC en mpvd.
- Barra de botones: pausa, anterior, siguiente, subtítulos, pista de audio, solo-audio, saltar intro, velocidad,
  grabar, menú, pantalla completa. Está bien dimensionada.

### B.2 Los problemas concretos, cada uno con su causa verificada

1. **El resumen no aparece en los vídeos de YouTube.** Causa real: `mu-recap`'s `source()` devuelve `nil` para una
   URL salvo que mpv ya tenga cargada una pista de subtítulos, y `mpv.conf` no pasa `ytdl-raw-options` con
   `write-auto-subs`, así que YouTube nunca da pista. No es que la opción no esté: es que se queda sin material.
   **Medido:** `yt-dlp --write-auto-subs --sub-langs en --sub-format srt` sobre un vídeo de 15 minutos tarda
   **5,41 s** y trae 23 KB / 3.678 palabras. El resumen está a cinco segundos, no a los ocho minutos de transcribir.
   Detalle importante que salió al medir: pedir un idioma **traducido automáticamente** (`es` en un vídeo en inglés)
   devuelve **HTTP 429**. Así que hay que pedir solo los idiomas nativos del vídeo y traducir el SRT nosotros con
   OPUS-MT, que además da mejor castellano.
2. **Y está escondido.** «¿Qué me he perdido?» vive en Herramientas, que tiene 15 filas: tercer nivel. «Índice del
   vídeo» no está en `mu-menu` **en absoluto**, solo como alt+I y una entrada perdida del árbol `#!`.
3. **Seis puertas para lo mismo:** Abrir archivo, Abrir URL…, Pegar URL o ruta copiada, Buscar en YouTube,
   Descargas y conversión, Suscripciones. Tu propuesta es la correcta: **una** entrada que acepte cualquier cosa
   (un enlace, varios, una lista, un canal entero, una ruta local, el portapapeles) y luego pregunte solo
   «reproducir o descargar», manteniendo la pantalla de descarga actual, que está bien.
4. **El formato de grabación no se puede elegir.** No hay ninguna fila de formato: `KIND_HINT` solo te *informa*
   («directo», «sin recodificar», «tramo del vídeo de internet»). Se graba copiando el flujo de origen.
5. **La radio no se puede programar.** Verificado: `mu-iptv/main.lua:449` esconde la acción «Programar grabación…»
   y la línea 1042 excluye la radio del selector de canal, las dos con `ch.kind ~= 'radio'`. La programación de
   mpvd graba audio sin problema; es solo la interfaz la que lo prohíbe.
6. **Programar está escondido.** Solo se llega desde la lista de un canal con Tab. Nadie lo encuentra.
7. **El QR ya se puede quitar.** `mu-share` tiene `toggle_qr()` (línea 638), la fila «Ocultar el código QR»
   (línea 382) y se esconde solo a los 120 s. Se arregló en esta iteración; tu queja era anterior. No queda nada.
8. **Faltan botones de copiar** en todo enlace que haya que llevarse a otro aparato: sala, mando desde el móvil,
   panel de descargas. Hoy se muestran como texto o como QR.
9. **El árbol `#!` es un segundo interfaz peor.** Mantiene 119 entradas en paralelo a `mu-menu` y es donde se cuela
   la duplicación.

### B.3 El criterio de simplificación

- La raíz: **ocho filas o menos**, nombradas por lo que quieres hacer, no por el script que lo hace.
- Lo que solo tiene sentido para el archivo que estás viendo va **en la pantalla** (un botón, o el panel de su
  propia función), nunca en la raíz.
- **Una función, una puerta.**
- Nada en un menú que el programa pueda decidir solo (formato, modelo, motor): valor por defecto y «cambiar» dentro.

### B.4 Hitos propuestos

| Hito | Qué |
|---|---|
| H42 | Una sola puerta para abrir y descargar; botón de copiar en todos los enlaces compartibles |
| H43 | Grabación: fila de formato que se recuerda, programar visible en el primer nivel, radio programable |
| H44 | La sala: handler por trozos, fichero original, «abrir en tu reproductor», relay empezando donde el anfitrión |
| H45 | Resumen de YouTube (subtítulos automáticos en 5 s, traducidos con OPUS-MT) y «Resumen e índice» en la raíz |
| H46 | Un solo menú: retirar el árbol `#!`, raíz de ocho filas |
