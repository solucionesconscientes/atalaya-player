# Guía de uso de Atalaya Player

Guía por tareas. Todas las teclas están en `docs/ATAJOS.md`; los detalles técnicos de cada función, en su documento (enlazado en cada
apartado). Sin mpvd (el daemon) lo básico sigue funcionando: reproducir, menús de uosc, teclas de mpv.

## 0. Instalar y abrir
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
tools/install.sh                 # uv sync + vendor.sh + el lanzador + entrada "Atalaya Player" en el menú de aplicaciones
tools/install.sh --extras        # además traducción offline y búsqueda semántica (≈220 MB)
atalaya video.mkv                # o bin/mpv-uos sin instalar
atalaya                          # sin archivo: pantalla de inicio con los recientes
```
La orden se instala con **dos nombres**: `atalaya` y `mpv-uos`. Son el mismo programa (el segundo es el identificador
técnico, y es también el de las carpetas de datos y el socket, así que no cambia al renombrar la aplicación).
Opciones del instalador: `--xdg` (caché y datos en `~/.cache/mpv-uos` y `~/.local/share/mpv-uos` en vez de `<proyecto>/.cache`),
`--default` (reproductor por defecto para vídeo y audio), `--dry-run`, `--uninstall` (solo borra lo que creó).

**AppImage** (sin instalar nada más que mpv): `tools/build_appimage.sh` crea `dist/Atalaya-x86_64.AppImage` (≈ 41 MB).
Cópialo donde quieras, `chmod +x Atalaya-x86_64.AppImage` y ábrelo. Necesita el mpv del sistema (0.41 o posterior); si
falta, avisa con la orden para instalarlo. Los subtítulos IA y los modelos se descargan la primera vez que se usan.

## 1. Moverse por la interfaz
- **Menú Atalaya Player**: botón derecho, tecla `MENU`, `alt+m` o el botón ▦ de la barra. Es **el único menú que hay** y
  reúne todo lo de esta guía en ocho categorías: Abrir o descargar, TV y radio, Descargas y conversión, Subtítulos,
  Imagen y sonido, Grabar, Herramientas y Preferencias. Arriba aparecen, solo cuando sirven de algo, *Continuar
  viendo* y *Resumen e índice* de lo que estás viendo.
  (Hasta ahora había un segundo menú escondido en `ctrl+m`, con 119 entradas y cuatro cosas repetidas: se retiró, y
  lo que solo estaba allí está ahora aquí.)
- **Un solo menú**: el título dice dónde estás («Atalaya Player › Subtítulos › Subtítulos del vídeo»); la primera fila, **Atrás**, vuelve un
  nivel, igual que `⌫` o `←`; `Esc` cierra. Desde la raíz de cualquier módulo (aunque lo abras con su tecla) *Atrás* te lleva al
  menú principal. Las paletas de búsqueda (`ctrl+u`, `ctrl+f`, `alt+f`) se cierran con `⌫` cuando están vacías.
- **Lista de reproducción**: si abres **varios archivos a la vez** (seleccionándolos en el gestor de archivos, o
  `atalaya a.mkv b.mkv c.mp3`), entran todos en una lista y se te **enseña al empezar**, para que veas qué has abierto
  y en qué orden; se quita sola a los 4 segundos. Con `p` vuelves a verla, y los botones ⏮⏭ de la barra aparecen solo
  cuando hay lista. Si prefieres que no salga o que se quede puesta, está en
  `--script-opts=mu-menu-playlist_on_open=0` (o un número de segundos grande).
- **Idioma**: el reproductor sale en **castellano, inglés o francés** según el idioma de tu sistema (en cualquier
  otro idioma, en inglés). Para forzarlo: *Preferencias → Idioma*, que se aplica al reiniciar.
- **Ayuda**: `?` muestra las teclas principales, y abajo la dirección del proyecto —
  **solucionesconscientes.es/atalaya**, donde está toda la información — que se abre con Enter o se copia con `Tab`
  para llevártela al móvil. También está en *Preferencias*.
- **Barra de controles**: reproducir/pausa (y anterior/siguiente si hay lista), subtítulos, audio (si hay varias pistas), velocidad,
  grabar ●, menú ▦ y pantalla completa. Lo demás está en el menú. El botón ⏭ aparece solo dentro de una intro o unos créditos.
- **Grabar** (botón ● de la barra o `alt+r`): capturas con o sin subtítulos, *Grabar desde ahora* hasta *Detener*, y
  *Recortar un tramo* con marcas de inicio y final (son las del bucle A-B, tecla `l`). Mientras graba verás un punto
  rojo y un contador; el mismo botón ● lo detiene, sin preguntar nada. Qué hace según lo que suena: un directo de TV o
  radio se graba tal cual llega; un vídeo de internet se descarga solo ese tramo con yt-dlp; un archivo local se corta
  sin recodificar.
  **Formato** (una fila, y se recuerda): *igual que el original* (MKV, el valor por defecto: nunca falla), *MP4 si los
  códecs lo permiten* —la fila te dice si los de este vídeo caben, y si no caben se graba en MKV avisando— o *solo el
  audio* (Opus 128). En MP4 un recorte empieza justo en la marca; en MKV, en el fotograma clave anterior.
  Carpeta: *Carpeta de grabaciones* (por defecto `~/Vídeos/Atalaya/Grabaciones`).
  También desde aquí: *Programar una grabación…* de TV **o radio**.
- **Pausar con un clic en el vídeo**: *Menú → Preferencias* (desactivado por defecto; activado, el clic ya no arrastra la ventana).
- **Paleta** (`alt+p`): escribe para buscar comandos, canales, recientes, acciones de mpvd y (si hay transcripción) frases del diálogo.
- **Continuar viendo** (`alt+h`): los recientes se reconocen por contenido, aunque renombres o muevas el archivo.

## 2. Ver la tele y escuchar la radio
1. `alt+t` → *España TV* / *España radio* (TDTChannels), *Mundo* (iptv-org por país y categoría), *Radio mundial* (Radio Browser).
   En *Mundo* y *Radio mundial* tu país sale primero; países y categorías van en español.
2. `alt+f` busca un canal por nombre sin acentos; `Tab` sobre un canal: favorito o copiar URL.
3. `alt+↑` / `alt+↓` cambian de canal dentro del grupo; `alt+r` graba el directo en la carpeta de grabaciones
   (`~/Vídeos/Atalaya/Grabaciones` por defecto): es el mismo botón «Grabar» del reproductor, con su punto rojo y su
   contador, y se detiene con `alt+r` otra vez o desde *Grabar*.
4. Tus listas: *TV y radio → Mis listas → Añadir* (pega la URL de una M3U). Más en docs/FUENTES_IPTV.md.
5. *Comprobar canales en segundo plano* (al final de cada lista) marca los caídos con ✕ y apunta la calidad real de cada
   canal. Lo que ves a la derecha de un canal:

   | Pista | Significa |
   |---|---|
   | `720p50 · 2,7 Mb` | mejor calidad que da la fuente (resolución, imágenes por segundo, megabits por segundo) |
   | `bitrate bajo` | es HD pero con menos de 1,6 Mb/s: se verá borroso aunque diga 720p/1080p |
   | `con anuncios` | copia FAST del canal, con cortes publicitarios insertados |
   | `+2 fuentes` | la lista trae el canal varias veces: se abre la oficial y, si falla **o no empieza a sonar en 8 s**, se prueba sola la siguiente ("Probando otra fuente de «La 1»…"). Era el problema de las radios españolas: los primeros espejos no dan error, simplemente se quedan conectando |
   | `2 comprobadas OK` | de las copias de ese canal, cuántas respondieron en la última comprobación |
   | `✕ no responde` | lo que dijo la comprobación de ese canal, con el motivo (`no responde`, `prohibido: suele ser geobloqueo`, `ya no existe`, `responde, pero no es un vídeo`…) |
   | `✓ comprobado` | se comprobó y respondió |
   | `geobloqueado` | la fuente avisa de que solo funciona desde su país |
   | `ahora: Telediario` | lo que está emitiendo el canal según la guía |
6. **Guía y grabaciones programadas.** En las listas de TDTChannels cada canal dice qué emite (`ahora: …`). `Tab` sobre un
   canal → *Guía de programación* (o `alt+G` para el canal que ves): ahora, después y el resto del día; entra en un
   programa para *Grabar este programa* (empieza 1 min antes y acaba 3 después) o *Grabar lo que queda*.
   *TV y radio → Grabaciones programadas* muestra las pendientes, la que está grabando y las hechas (cancelar, detener,
   reproducir, quitar de la lista). *TV y radio → Programar una grabación…* (también en *Grabar*, o `Tab` →
   *Programar grabación…* en cualquier canal): elige el canal y escribe cuándo: `21:30 22:15`, `21:30 90` (minutos),
   `mañana 9:00 1h30`, `ahora 30`. **Las emisoras de radio también se pueden programar** (se guardan en `.mka`).
   Se graba aunque estés viendo otra cosa o cierres el reproductor, pero el equipo tiene que estar encendido: si estaba
   apagado a esa hora, la grabación sale como *perdida*. Los archivos van a `~/Vídeos/Atalaya/Grabaciones`
   («Canal - Programa - 2026-09-30 21.30.mkv») y avisa al terminar.

7. **Despertar para grabar y apagar al terminar.** En *TV y radio → Grabaciones programadas*: **Despertar el equipo 5 min
   antes** (solo saca de la suspensión; un equipo apagado no se puede encender desde aquí) y **Al terminar la grabación:
   nada / suspender / apagar**. Las dos las guarda mpvd, así que valen con el reproductor cerrado, y se aplican a lo que
   programes a partir de entonces. Mientras graba, el equipo no se duerme. Antes de suspender o apagar se comprueban tres
   cosas —que no estés usando el reproductor, que no haya otra grabación a menos de 15 min y que no quede nada
   descargando, convirtiendo o transcribiendo— y sale un aviso de 60 s con **Cancelar**; si falla cualquiera de las tres,
   no se hace y se dice cuál. El despertador necesita una orden con `sudo` **una sola vez** (está en NEEDS_HUMAN.md): sin
   ella todo lo demás funciona igual y la grabación avisa de que no habrá despertador.
8. **Audio y subtítulos del canal.** Los canales que traen pistas propias lo dicen en la lista: `CC` (subtítulos),
   `VO` (versión original), `AD` (audiodescripción). Mientras ves uno, *TV y radio → Audio y subtítulos del canal*
   las muestra con nombres claros (*Español*, *Versión original*, *Audiodescripción*, *Español (para sordos)*). RTVE
   trae subtítulos en español, inglés, gallego, catalán y euskera. No hay traducción en directo.
9. **Buscar en esta lista.** La primera fila de *España TV*, *España radio*, cada país de *Mundo*, *Radio mundial*,
   *Favoritos* y *Recientes* busca solo entre sus canales (sin acentos, como `alt+f`).

**Lo que depende de la fuente y lo que no.** La resolución, las imágenes por segundo y el bitrate los pone cada cadena:
RTVE emite como mucho 720p a 25 fps y 3 Mb/s, casi todo va a 25 fps y algunos canales "HD" llevan muy poco bitrate; eso
no se puede mejorar desde el reproductor. Tampoco los canales caídos o geobloqueados. Lo que sí hace Atalaya Player: no guarda
posición ni pistas de los directos (siempre entran en su mejor calidad y desde "ahora"), abre cada petición HLS con
una conexión nueva (evita que canales como 101TV se congelen a los pocos segundos), se presenta como un navegador si la
lista no dice otra cosa (Canal Sur oficial rechaza a mpv) y cambia solo a otra fuente del mismo canal si la primera no
abre **o no suena** (8 s; `mu-iptv-stall_seconds`). Si un canal entrelazado se ve con "peines" (7TV), pulsa `d`. Detalles en docs/FUENTES_IPTV.md §7.

## 3. YouTube y otras webs (yt-dlp)
- **Saltar los patrocinios al VER** (no solo al descargar): en un vídeo de YouTube, Atalaya Player pregunta a SponsorBlock qué
  tramos ha marcado la gente (patrocinio, autopromoción, «suscríbete», partes sin música) y los salta solos; `alt+k` los
  salta a mano y `alt+j` lo apaga. **No se le dice a nadie qué estás viendo**: se piden por un prefijo de 4 caracteres
  del hash del id del vídeo, que vale para 1 de cada 65 536 vídeos, y el filtrado se hace en tu equipo.

- `mpv-uos 'https://www.youtube.com/watch?v=…'` reproduce con el yt-dlp vendorizado (se actualiza solo a diario).
- **Abrir o descargar** (`ctrl+o`, el menú principal → *Abrir o descargar*, o `alt+y` → *Abrir o descargar…*): **un solo
  campo para todo**. Pega lo que sea —un enlace, veinte, una lista de reproducción, un canal entero, la ruta de un
  archivo o de una carpeta, un `.txt` con enlaces— o déjalo en blanco para usar el portapapeles. Atalaya Player reconoce qué
  es, te dice **cuántos elementos** hay y hace **una sola pregunta: reproducir o descargar**. Si eliges descargar vas a
  la pantalla de siempre (casillas, formato para todos y formato por fila). Si lo que escribes no es un enlace ni una
  ruta, se ofrece buscarlo en YouTube. Lo que yt-dlp no sabe abrir (los guardados de Instagram) se te dice **antes** de
  intentarlo, con la alternativa.
- Las puertas de antes siguen en el teclado, por si las tenías en la mano: **Abrir URL** (`ctrl+u`): si el portapapeles
  tiene un enlace, la primera entrada es *Pegar: …*;
  también puedes escribir o pegar (`ctrl+v`) una URL (YouTube, Twitch, archive.org, una radio, `rtsp://`…; vale `youtu.be/…` sin
  `https://`) y pulsar Enter. Si lo escrito no es una URL, la entrada pasa a ser *Buscar «texto» en YouTube*.
  `Tab` sobre una URL: *Añadir a la lista* (se reproduce al terminar lo actual) o *Descargar*.
- **Buscar en YouTube** (`ctrl+f`): escribe y pulsa Enter; salen hasta 15 resultados con
  duración y canal (*EN DIRECTO* en los directos). Enter reproduce el elegido; `Tab`: *Añadir a la lista* o *Descargar* (abre el
  menú de descarga de ese vídeo sin tocar lo que suena). La misma búsqueda durante una hora sale al instante (caché de mpvd).
- `alt+a` alterna vídeo / solo audio sin perder la posición; `alt+q` elige cualquier formato; `alt+d` descarga (vídeo por resolución,
  audio original o convertido a MP3/Opus/M4A/FLAC/WAV con el bitrate que quieras, subtítulos, capítulos, SponsorBlock…);
  `alt+l` muestra la cola con progreso. Destino: `~/Vídeos/Atalaya` y `~/Música/Atalaya`. Más en docs/YTDLP.md.
- **Según tu equipo**: Atalaya Player mira qué códecs decodifica tu gráfica (`vainfo`) y en *Calidad* y en los formatos de
  *Descargar* marca cada vídeo como **fluido en tu equipo** (lo decodifica la gráfica) o **exigente (por procesador)**.
  Al reproducir y al descargar se prefiere el mejor códec que tu gráfica decodifica (en este portátil, H.264; con una
  gráfica moderna, AV1 o VP9), con el vídeo y el audio originales, sin recodificar. *Estado de yt-dlp* dice cuáles son.
- Por defecto: *Vídeo · hasta 1080p (recomendado)* = vídeo original hasta 1080p con sus fps + audio **Opus** original
  en **MP4** (si algo no cabe en MP4, se guarda en MKV solo); **mkv** como opción y **webm** VP9 + Opus. Solo audio:
  *Audio · original (recomendado)* = el Opus de la web tal cual; MP3 320 kb/s y M4A como opciones. Para un MP4 que
  abra cualquier aparato viejo (H.264 + AAC) o un archivo más pequeño (HEVC), usa *Convertir*.
- Tamaños orientativos por hora: 1080p60 H.264 ≈ 2,5–3 GB; 1080p H.264 ≈ 1,5–2 GB; 1080p AV1/VP9 ≈ 1–1,5 GB;
  720p ≈ 0,7–1 GB; 480p ≈ 0,3–0,5 GB; audio Opus 130–160 kb/s ≈ 60–70 MB; MP3 320 kb/s ≈ 145 MB.
- **Una sola puerta** (*Descargas y conversión → Descargar…*): pega lo que quieras —un enlace, veinte (`ctrl+v`, da igual
  el separador), una lista de reproducción, un canal o la ruta de un `.txt` con un enlace por línea— y el reproductor
  averigua qué es. Sale **una lista con casillas**: la primera fila fija el formato de todos (*Para todos: Vídeo · hasta
  1080p*), la segunda si quieres los subtítulos en un `.srt` aparte, y cada fila puede llevar **su propio** formato o su
  propio SRT con sus acciones (*Formato solo para este*, *SRT solo para este*). Se descarga solo lo marcado, y las filas
  que comparten formato van juntas en un solo lote. Si es una lista o un canal y no has cambiado nada por filas, va a una
  carpeta con el nombre de la lista y numerado (`001 - …`). Los repetidos se descartan y lo ya descargado no se repite
  (archivo `ytdl-archive.txt` en tus datos).
- **Subtítulos al descargar** (*Descargar → Opciones*): *Subtítulos* rota entre no, dentro del vídeo y archivo SRT
  aparte (junto al vídeo); *Idiomas* entre originales + es + en (por defecto), solo el original, español, inglés y todos.
  *Solo subtítulos (SRT)* baja únicamente los `.srt` (sin vídeo). «Original» es el idioma del vídeo según la web (y, en
  YouTube, sus subtítulos automáticos del idioma original).
- **Cuando la web cambia**: si la versión estable de yt-dlp no puede abrir o descargar algo (p. ej. ok.ru en septiembre de
  2026), Atalaya Player lo intenta una vez con la versión *nightly* (se descarga y verifica sola; *Estado de yt-dlp* muestra cuál
  hay). No se reintenta lo que no es cosa de versión: vídeos privados, con inicio de sesión, bloqueados en tu país,
  borrados…
- **TikTok e Instagram**: un vídeo o reel se abre y se descarga con su enlace. Un perfil o una colección de TikTok se
  pegan en *Descargar…* y salen con casillas. Lo que **no** se puede, y el menú te lo dice antes de intentarlo en vez de
  fallar: los **guardados de Instagram** (`…/saved/all-posts/`) y los **favoritos de TikTok**, porque yt-dlp no tiene
  ningún extractor para esas páginas; la alternativa es abrir cada uno y pegar su enlace. El perfil entero de Instagram
  se ofrece, pero avisando de que yt-dlp marca ese extractor como roto. Para lo privado hace falta *Usar mi sesión del
  navegador*, y la suplantación de navegador que TikTok prefiere necesita `curl_cffi` (`tools/install.sh --extras`).
- **Ajustes de descarga**: descargas a la vez (1–4), límite de velocidad (sin límite, 500 KB/s … 10 MB/s), no repetir lo ya
  descargado, carpeta por lista y *Usar mi sesión del navegador* (desactivado; Firefox, Chrome, Chromium, Brave, Edge…:
  yt-dlp lee sus cookies para lo que ya puedes ver con tu cuenta; vale también al reproducir; nunca sirve para DRM).
  Si cierras el reproductor con descargas a medias, siguen al volver a abrirlo.

## 3a. Suscripciones (canales, listas y podcasts)
*Abrir o descargar › Suscripciones* (o `alt+Y`).
- **Añadir suscripción…**: pega la dirección de un canal de YouTube, una lista o el RSS de un podcast. Atalaya Player dice qué es,
  enseña lo más reciente y te deja cambiar el nombre y cuántos episodios bajar ya (por defecto los 3 últimos; en listas, todo).
- Cada suscripción se comprueba sola cada 2 horas y descarga lo nuevo sin molestar a lo que estés haciendo.
  **Tab** sobre una: comprobar ahora, pausar / reanudar, borrar (los archivos ya descargados se quedan).
- **Reglas**: calidad, solo audio, saltar patrocinios (SponsorBlock), conservar solo los N más nuevos, borrar lo que ya
  hayas visto y qué hacer tras descargar: igualar el volumen, subtítulos IA (y traducirlos), renombrar y mover a una
  carpeta de la biblioteca.
- **Pendientes y descargados**: lo que espera turno y lo ya bajado, con cómo va «tras descargar»; Enter lo reproduce.
- **Ajustes de suscripciones**: cada cuánto comprobar, horario de descarga (p. ej. de 1:00 a 7:00), límite por franja
  (descargas o MB), pausar con conexión medida (datos del móvil), seguir comprobando con el reproductor cerrado y lo
  que se hace tras descargar por defecto.

## 3b. Convertir vídeo y audio
- `alt+C` (o *Descargas y conversión → Convertir…*): con un archivo de tu equipo abierto, elige **MP4 compatible**
  (se abre en cualquier sitio), **Más pequeño (H.265)** (ocupa más o menos la mitad y tarda más), **Web (WebM)**,
  **solo audio** (MP3, M4A, Opus, FLAC o WAV) o **GIF animado**. Luego, las opciones: resolución máxima (original, 1080p,
  720p, 480p), calidad (alta, normal, pequeña), bitrate del audio, conservar los subtítulos y *Solo un tramo*: usa las
  marcas A-B (tecla `l` o las filas *Marcar el inicio / el final aquí*). La primera fila, *Convertir ahora*, empieza.
- **Carpeta entera** (*Convertir una carpeta entera…*): escribe o pega la ruta; cada archivo es una tarea y se convierten
  de uno en uno en `Convertidos/<nombre de la carpeta>`.
- **Tarjeta gráfica**: si tu equipo codifica por VA-API (Linux, `vainfo`), MP4 se hace con la GPU (mucho menos CPU);
  si falla, se repite solo por CPU. Se desactiva en *Usar la tarjeta gráfica*.
- **Tareas** (`alt+T`): descargas y conversiones con su progreso; `Tab` cancela, repite, quita de la lista o abre la
  carpeta. Nunca se sobrescribe un archivo («(2)» si ya existe) y lo que queda a medias al cerrar sigue al volver.
- Destino por defecto: `~/Vídeos/Atalaya/Convertidos` (se cambia en *Carpeta de salida*).

## 2b. Programar una franja: grabarla, ponerla, o las dos
En *TV y radio → Grabaciones programadas* eliges la franja (canal, hora de inicio y de fin) y, en **Qué hacer en
esa franja**:

| | |
|---|---|
| **Grabarlo** | Lo de siempre: queda el archivo |
| **Ponerlo** | A esa hora se enciende y suena, y para a la hora de fin |
| **Las dos cosas** | Se ve y además queda grabado |

«Ponerlo» **abre el reproductor si no hay ninguno**, que es lo que hace falta para que funcione de verdad: con el
despertador activado (*Despertar el equipo 5 min antes*), el equipo puede estar suspendido, levantarse solo y
ponerte las noticias. Y al terminar la franja se aplica lo de siempre: nada, suspender o apagar.

No es solo para canales: se puede programar **una canción, una carpeta, una lista o una dirección**. Hereda todo
—despertador, franja y apagado—, así que vale igual para «la radio a las 7:00» que para «esta lista mientras
cenamos, y al acabar suspende».

## 2c. Ir a un minuto escribiéndolo
Icono del reloj en la barra, o `g`. Escribes y Enter. Admite lo que uno escribiría de verdad: `2:15`, `00:02:15`,
`1:02:15`, `135` (segundos sueltos) y también relativos: `+30` adelanta medio minuto y `-30` lo retrocede. Si el
minuto se sale del archivo, te lo dice en vez de dejarte pulsar.

## 2d. Verlo acortado: la charla de una hora en quince minutos
En *Resumen e índice → **Verlo acortado*** (solo con archivos de tu equipo de más de cinco minutos). Eliges cuánto
quieres que dure —5, 10, 15 o 30 minutos— y el reproductor **monta los trozos que mejor lo representan** y los
pone seguidos.

Tres cosas que conviene saber:
- **No es el resumen escrito**, que ya existe ahí al lado: esto es el vídeo, acortado y reproducible.
- **No recodifica nada ni toca el original**: es un montaje virtual, se abre al instante y no ocupa disco. Si lo
  quieres como archivo, *Guardarlo como archivo* lo manda a Tareas (eso sí recodifica).
- **Corta por frases enteras**, nunca a mitad de palabra: usa la transcripción para saber dónde empieza y acaba
  cada frase. Necesita que el vídeo tenga subtítulos IA generados (`alt+c`).

## 3c. Tramos: quedarte con trozos de lo que estás viendo
En la barra hay tres iconos nuevos, que son los que se pulsan **con la película andando** (el porqué de cada uno
está en `docs/INTERFAZ.md`):

| Icono | Qué hace | Tecla |
|---|---|---|
| ✂ **Tramos** | 1ª pulsación «desde aquí», 2ª «hasta aquí». El número de tramos elegidos sale en el icono | `ctrl+x` |
| ⟳ **Bucle** | Repite el tramo elegido; sin nada elegido, es el bucle A-B de siempre | `l` |
| ✎ **Nota** | Caja de texto; la nota se guarda con el minuto en el que estás | `n` |

**Lo que eliges se ve en la línea de tiempo**: mientras marcas, A y B; una vez cerrado, el tramo queda pintado en
azul de principio a fin. Las notas salen como rombos con su texto al pasar por encima. Todo eso convive con los
capítulos de la película, que no se pierden; al cambiar de archivo, las marcas se van.

En *Tramos* (`ctrl+l`, o *Herramientas → Tramos*) está lo que se hace con ellos:
- **Guardar los N por separado** → un archivo por tramo, llamado `<película> [inicio-fin].<formato>`.
- **Guardar los N unidos en uno** → un solo archivo con todos pegados, en una sola pasada; se llama
  `<película> [N tramos].<formato>`.
- **Formato**: la lista la da el propio programa según lo que tu ffmpeg sepa hacer, y se recuerda:
  - **Sin recodificar**: instantáneo y **sin perder un bit**, porque copia los flujos tal cual (medido: 0,07 s para
    un corte de 6 s, frente a recodificarlo). A cambio el corte empieza en el fotograma clave anterior —pueden
    entrar unos segundos de más— y **no puede unir** tramos: pegarlos obliga a recodificar.
  - **Vídeo**: MP4 (H.264, se abre en cualquier sitio), H.265 (la mitad de tamaño), **AV1** (lo más nuevo, el que
    menos ocupa, pero el que más tarda) y WebM.
  - **Solo audio**: M4A, MP3, Opus, FLAC o WAV.
- **El icono de la lista** (junto a ✂, con el número de tramos) lleva directo a todo esto; también `ctrl+l`.
- **Puedes cambiar el orden** con las flechas de cada fila. Mientras no toques nada van por tiempo; en cuanto subes
  o bajas uno, el orden es el tuyo — y es **el orden en el que se pegan al unirlos**, así que puedes montar el
  final primero si quieres.
- **Eliges cuáles exportas.** Cada tramo entra marcado; pulsando Enter sobre su fila lo dejas fuera (y lo
  devuelves). Lo que se guarda es lo marcado, así que puedes quedarte con tres de cinco, o unir solo dos de ellos.
  *Elegirlos todos* / *Dejar fuera todos* hace la selección de golpe.
- Cada tramo tiene lo suyo en los botones de la derecha de su fila: *Ir ahí*, *Repetir este* y *Quitarlo*.

Todo va a la cola de *Tareas*, como las demás conversiones, así que puedes seguir viendo mientras se hace. Dos
avisos honestos: al **unir** tramos hay que recodificar (un corte y pegado no puede copiar los flujos tal cual), y
por eso no se usa la tarjeta gráfica y los subtítulos incrustados se quedan fuera; guardándolos **por separado** sí
se aprovecha todo lo de siempre.

## 4. Subtítulos con IA, traducción, duales y guardar SRT
0. **Vídeos de internet: subtítulos que da la web.** Con un vídeo de YouTube (u otra web con subtítulos) abierto, `alt+i`
   → *Subtítulos de la web* lista los manuales y los automáticos del idioma del vídeo. Enter añade el elegido como pista
   (limpio: los automáticos de YouTube llegan «rodando» línea a línea y aquí salen en frases de dos líneas). Si no hay en
   tu idioma, la primera fila es *Traducir al español (…)*: baja el original y lo traduce entero, sin conexión, antes de
   mostrarlo (nunca frase a frase). Las traducciones automáticas de YouTube no se ofrecen: la web las bloquea (error
   429). `alt+S` guarda la pista en SRT como cualquier otra.
1. `alt+c` inicia los subtítulos IA del archivo local abierto (whisper.cpp; el modelo se elige según tu CPU, docs/BENCHMARKS.md).
   Aparecen unos segundos por delante de lo que ves; tras un salto, se transcribe primero lo nuevo. Quedan guardados para la próxima vez.
   En un portátil de 4 núcleos, en vivo se usa `base` (rápido, pero se equivoca y apenas puntúa) y para **pre-subtitular** (el
   siguiente de la lista, resincronizar, "Completar y guardar") `small-q8_0`, que transcribe mucho mejor. Si ya hay una
   transcripción terminada con un modelo mejor, `alt+c` la reutiliza.
2. `alt+i` abre el menú: idioma, modelo (descarga bajo demanda), pre-subtitular el siguiente de la lista.
3. `alt+x` resincroniza un subtítulo que va desfasado (retraso, deriva o cortes) contra la transcripción IA. Sirve para archivos
   `.srt/.ass/.vtt` y para pistas de texto que van dentro del vídeo (se extraen solas).
4. Menú → *Traducir la pista seleccionada a…* (offline, requiere `--extras`). Arriba eliges el motor:
   - **Rápido (Argos)**: todos los idiomas (≈90 MB por par, pivota por inglés). Traduce literal: "No me tomes el pelo" → "Don't take
     my hair".
   - **Máxima calidad (OPUS-MT donde llegue)**, que es la opción por defecto desde H36: español/catalán/francés ↔ inglés (y
     español↔francés por inglés) con OPUS-MT *tc-big* (Helsinki-NLP, CC-BY 4.0), 234 MB por par; los demás idiomas, con Argos.
     Acierta muchas más expresiones coloquiales ("Don't tease me"). La primera vez descarga ≈860 MB, los convierte a 234 MB y
     borra el resto; después funciona sin red. Tarda más (beam 4, en segundo plano) y usa ≈430 MB de RAM solo mientras traduce.
   - **Automático** (por defecto): OPUS-MT donde ya esté descargado y Argos para lo demás (p. ej. francés → español = Argos
     fr→en + OPUS-MT en→es).
   La pista seleccionada puede ser la IA, un archivo externo o una pista de texto interna del vídeo (se extrae con ffmpeg).
   *Subtítulos duales*: original arriba, traducción abajo.
5. **Guardar subtítulos (SRT)** (`alt+S` o menú `alt+i` → *Guardar subtítulos (SRT)*): guarda como SRT normal, junto al vídeo, la
   pista IA (con su idioma y el % transcrito), la traducción, el resincronizado o la pista seleccionada (externa o interna; ASS y
   WebVTT se convierten a SRT). `alt+S` guarda lo que estés viendo o, si no hay pista seleccionada, la IA. Aparece
   "✓ Guardado: película.es.srt (812 líneas)".
   - Nombre: `película.<idioma>.srt`; si ya existe, `película.<idioma>.ia.srt` (IA o traducción) o `.resync.srt`, y luego `(2)`, `(3)`…
   - Si la carpeta del vídeo no admite escritura o es una URL, va a `~/Vídeos/Atalaya/Subtítulos` (tu carpeta de vídeos XDG).
   - Pista IA sin terminar: elige *Guardar lo transcrito* (lo que haya) o *Completar y guardar* (sigue en segundo plano, aunque
     cierres el reproductor, y guarda al acabar).
   - Al volver a abrir el vídeo, mpv carga el SRT guardado y los subtítulos IA lo reutilizan en vez de añadir otra pista igual.
6. **Límites honestos**: la traducción solo puede ser tan buena como el texto de origen; con subtítulos IA, lo que más cuenta es la
   transcripción (usa `small-q8_0` o mejor: pre-subtitula o *Completar y guardar* antes de traducir). Los subtítulos de imagen de
   DVD/Blu-ray (PGS, VobSub, DVB) no se pueden guardar, traducir ni resincronizar: necesitan OCR, que Atalaya Player no incluye. Los tiempos
   de la traducción son los del original: cuando una frase ocupa varios cues se reparte cortando en comas y conjunciones, pero con
   idiomas de orden muy distinto alguna línea queda partida.
Más en docs/WHISPER.md y docs/TRADUCCION.md.

## 5. Buscar dentro del vídeo y capítulos automáticos
Con la transcripción hecha, la paleta (`alt+p`) busca por **idea** en la sección *Diálogo* ("cuando hablan del presupuesto") y Enter salta
allí. Menú de subtítulos IA → *Capítulos por tema* crea capítulos titulados con una frase del propio vídeo. Requiere `--extras` y el
modelo de embeddings (se descarga desde el menú). Más en docs/SEMANTICA.md.

## 6. Series: saltar intro y créditos
Abre un episodio: mpvd compara su audio (huellas Chromaprint, requiere `fpcalc`) con los episodios vecinos y detecta la intro y los
créditos. La primera vez tarda unos segundos por episodio (≈10 s en una serie de 50 min); después sale de la caché al instante.
- **Dónde busca los otros episodios**: primero en la misma carpeta; si no hay ninguno de la misma serie, en las carpetas hermanas (una
  carpeta por episodio, como `Descargas/Don Matteo 1x03 …/…mp4`). Se reconocen `S01E02`, `1x02`, `Temporada 1 Capítulo 2` y `Cap.102`;
  el título se compara sin acentos, puntuación ni etiquetas entre corchetes, y debe coincidir la temporada. Se usan los 3 vecinos más
  cercanos por número de episodio que se puedan leer (los rotos o sin audio se saltan). Con nombres sin serie (`ep01.mkv`) se usan los
  demás vídeos de la carpeta.
- **Qué verás**: el botón ⏭ aparece en la barra solo dentro de la intro o los créditos (el menú `alt+j` dice por qué aún no hay
  nada que saltar: analizando, sin otros episodios, error), junto a un recuadro **Saltar intro ▸**
  clicable. `alt+k` salta (fuera de un segmento, al final del siguiente; durante el análisis dice «Analizando… NN %»). Si no se detecta
  nada, un aviso breve lo explica (p. ej. «Saltar intro: no hay otros episodios para comparar · alt+j»).
- **Saltar créditos** lleva al siguiente elemento de la lista; si no hay lista, abre el siguiente episodio encontrado (aunque esté en
  otra carpeta). Unos créditos que acaban a menos de 15 s del final cuentan como final del episodio.
- **Salto automático** (menú `alt+j` o `script-opts`: `mu-intro-auto_skip_intro=yes`, `mu-intro-auto_skip_credits=yes`): muestra
  «Saltando intro en 3 s · Esc cancela» (`mu-intro-countdown_seconds`, 0 = sin espera; en pausa la cuenta se detiene). Esc solo actúa
  durante la cuenta atrás.
- **Marcar a mano** (menú `alt+j` → *Marcar inicio/final de la intro/créditos aquí*, o los bindings `mu_intro/intro-mark-start`,
  `intro-mark-end`, `credits-mark-start`, `credits-mark-end`): si solo marcas el final de la intro, empieza en 0:00; el inicio de los
  créditos se guarda hasta el final del archivo (marca también su final si hay escena poscréditos). mpvd busca ese mismo audio al
  principio (intro) o al final (créditos) de los demás episodios de la temporada, aunque la intro empiece en otro minuto (avances de
  distinta duración), y también en los episodios que añadas después. Las marcas manuales mandan sobre la detección automática; se
  guardan en `<datos>/intro-marks.json` y se borran desde el menú.
- **Analizar temporada** (menú): analiza en segundo plano todos los episodios de la temporada. Al abrir un episodio, además, se preparan
  en prioridad baja las huellas de toda la temporada, así los siguientes se resuelven al momento.
- **Exportar segmentos (Jellyfin)** (menú): escribe `segments.json` junto al vídeo con los segmentos de los vídeos de esa carpeta
  (`type`/`start`/`end` en segundos y `Type`/`StartTicks`/`EndTicks` al estilo media-segments de Jellyfin). Atalaya Player nunca escribe en tus
  carpetas si no se lo pides.

Límites: una **película suelta** (sin otros episodios) no tiene intro detectable: solo sirve el marcado manual. Varias **versiones del
mismo vídeo** en una carpeta (montajes, cortes para redes) se reconocen y no se marca nada («parecen versiones del mismo vídeo»). Se
descartan coincidencias sin sentido: una intro que empiece después del 40 % del vídeo o unos créditos que acaben antes del 60 %. La intro
se busca en los primeros 10 minutos y los créditos en los últimos 5. Hace falta pista de audio; solo archivos locales.

## 7. Sonido e imagen
`alt+v`: diálogo claro, modo noche (`alt+n`), reducción de ruido, sonido binaural para auriculares, protección fotosensible, perfil ligero
para equipos modestos y diagnóstico de tirones con recomendaciones. Más en docs/AUDIO_VIDEO.md.
- **Quitar el vídeo al minimizar** (en `alt+v`, **activado**): mientras la ventana está escondida nadie necesita la
  imagen, así que se deja de decodificar y, si es un vídeo de internet, también de descargar; al restaurar vuelve. Hace
  falta porque **minimizar por sí solo no para nada**: medido en este portátil con un 720p HEVC, 37 % de un núcleo con
  la ventana visible, 18 % minimizada y 8 % sin vídeo, con los mismos fotogramas descartados. Y en un vídeo de YouTube,
  deseleccionar la pista baja la descarga al 35 % (98,7 → 34,1 KiB/s en 480p; cuanto mejor la calidad, más se ahorra).
  - **Quitar o devolver el vídeo a mano** (`alt+a`): lo mismo, al instante (0,03 s, no recarga nada) y solo para ese
    archivo. **No se recuerda**: el siguiente vídeo se abre con su imagen. Para escuchar de verdad solo el audio de un
    vídeo largo de internet —bajando únicamente la pista de audio— está «Solo audio» en el menú de calidad (`alt+q`),
    que sí recarga porque se ha pedido a propósito.
- **Volumen parejo · con las etiquetas** (ReplayGain): gratis y exacto, pero solo donde hay etiquetas —casi solo
  música—; por pista o por álbum. Es el mismo ajuste que hay en *Música*, ofrecido también aquí porque es donde se
  busca viendo una película. Para la música sin etiquetas, mpvd mide la ganancia y la aplica igual.
- **Volumen parejo · siempre**: todos los vídeos suenan parecido de fuertes, sin tocar el mando (normalizador lento; tarda unos
  segundos en ajustarse al empezar).
- **Ecualizador**: *Plano*, *Más graves*, *Menos graves*, *Más agudos*, *Voz*, *Música*, *Altavoces del portátil* y
  *Auriculares*. Se recuerda como el resto de filtros.
- **Controles del escritorio (MPRIS)**: Atalaya Player aparece en los controles multimedia de KDE (bandeja, pantalla de
  bloqueo, KDE Connect), responde a las teclas multimedia del teclado y a los botones de los auriculares, con título,
  duración, posición, volumen, velocidad y repetición. Necesita el extra `desktop` (lo instala `tools/install.sh`);
  estado: `.venv/bin/python -m mpvd call mpris.status`.

## 8. Estudiar con vídeos
`alt+e` menú Estudio: `alt+w` repite en bucle la frase actual (`alt+←/→` anterior/siguiente), `alt+g` acelera solo los silencios,
`alt+b` guarda una nota con la cita y un enlace de tiempo en Markdown, `alt+u` exporta el bucle A-B (tecla `l` de mpv) o la frase como
clip mp4/GIF/mp3. Más en docs/ESTUDIO.md.

**Mis notas** (`alt+B` o *Menú → Herramientas → Mis notas*): las notas de este vídeo y las de todos, cada archivo con el
título del vídeo en `~/.local/share/mpv-uos/notas/`. Enter salta a ese minuto (abre el vídeo si es otro); Tab → *Editar*
(escribe el texto nuevo y Enter) o *Borrar*. *Exportar junto al vídeo* deja `<vídeo>.notas.md` al lado; *Exportar a una
carpeta* pide la ruta (por ejemplo tu bóveda de Obsidian) y la recuerda. Los enlaces de las notas (`mpv-uos://…`) abren el
vídeo en ese minuto desde Obsidian o el navegador una vez instalado (`tools/install.sh`); pegados en Atalaya Player (`ctrl+v`)
también funcionan.

## 9. Mando a distancia desde el móvil
`alt+z` muestra un QR; escanéalo con el móvil en la misma wifi y tendrás play/pausa, saltos, volumen, pistas, canales y búsqueda.
El móvil queda emparejado hasta que lo olvides (`alt+Z`). Si no conecta, abre el puerto en el cortafuegos: docs/REMOTE.md.

**Panel de descargas**: en *Mando a distancia* (`alt+Z`) → *Panel de descargas en el navegador* se abre en el navegador
del ordenador una página con las descargas y conversiones en vivo; en el móvil emparejado está en *Más → Panel de
descargas y conversiones*. Marca varias con las casillas para cancelarlas, reintentarlas o quitarlas; ▶ abre una terminada
en el reproductor; pega o arrastra enlaces en el recuadro, elige formato y pulsa *Descargar*. Muestra el espacio libre del
disco y avisa cuando termina cada una (notificación si la permites; en el móvil, aviso en la página y vibración, con la
página abierta). Al final de la página, *«Enviar a Atalaya Player» desde el navegador*: arrastra a la barra de marcadores
*Descargar con Atalaya Player* (la pone en cola en este ordenador sin abrir el reproductor), *Ver en Atalaya Player* o *Enviar al panel*
(sirve en cualquier equipo de la red; rellena el enlace y tú confirmas). Más en docs/REMOTE.md.

## 9b. Compartir: ver juntos
`alt+W` (o *Herramientas → Compartir: ver juntos*) → *Crear una sala para ver juntos*. La sala se abre al momento y
**el enlace se copia solo**: ya lo puedes pegar en un mensaje. Quien lo abra pone su nombre y ve lo mismo que tú, a la
vez, en su navegador (móvil u ordenador) o en su propio reproductor.

Por defecto la sala se abre **hacia internet**, porque compartir suele ser con alguien que no está en casa. Eso tarda
un poco: la dirección pública la da Cloudflare en unos segundos pero **no empieza a funcionar hasta un minuto después**
(medido). Por eso el reproductor **no te copia el enlace hasta que la dirección contesta de verdad**: primero te dice
«Abriendo la puerta a internet…» y luego «Enlace copiado». Si lo pegaras antes, quien lo abriera se encontraría un
error durante ese minuto. Para salas dentro de casa, apaga *Que se pueda entrar desde internet* y el enlace es
inmediato.
- Los invitados entran en *solo ver*. Si uno pide el control, te sale un sí/no en pantalla; en *Invitados* puedes
  darlo, quitarlo o sacar a alguien. Verás avisos como «Ana ha pausado».
- Vídeos de internet: el navegador del invitado los abre directamente si puede; si no, Atalaya Player los retransmite (con
  los subtítulos de texto activos).
- **El invitado puede verlo en SU reproductor, sea lo que sea.** En su página aparece *Abrir en mi reproductor* tanto
  si compartes un archivo tuyo (se lleva el original) como si ves la TV o un vídeo de internet (se lleva la
  retransmisión o la dirección original). Antes ese bloque solo salía con un archivo tuyo, así que con la TV el
  invitado se quedaba encerrado en el navegador.
- **Archivos de tu equipo: el invitado puede verlos tal cual.** En su página aparece *Abrir en mi reproductor*, con
  tres formas de llevárselo porque cada sistema va mejor con una: **copiar el enlace**, bajar un **`.m3u`** (doble
  clic lo abre en VLC o en mpv en Windows, macOS y Linux) y la línea **`mpv "<enlace>"`** para pegar en un terminal.
  Así lo ve **como es** —calidad original, cualquier códec— y los saltos son instantáneos (medido: primer fotograma
  en 0,36 s, salto al minuto 98 en 0,07 s), sin que tu equipo recomprima nada. Para ir juntos, la página dice en vivo
  por dónde vas, con un botón para copiar esa posición.
  En el navegador se ve el original cuando puede con él; cuando no (y «es un MP4» no basta: tus grabaciones son HEVC
  + Opus con el índice al final, que es justo lo que peor lleva), se ve la retransmisión y se te dice por qué.
- **La retransmisión empieza donde vas tú**, no en el segundo 0: quien entra en el minuto 40 ya no espera a que el
  empaquetado llegue hasta ahí (eran ~18 minutos). La página lo dice: «Empezamos donde va el anfitrión».
- **Entrar en la sala de otro desde Atalaya Player** (y no desde el navegador): es la mejor forma de ver juntos,
  porque te llega el **archivo original** del anfitrión en vez de la retransmisión, y tu reproductor sigue solo sus
  pausas, sus saltos y su velocidad. Tres formas de dárselo, todas con el mismo enlace que te han pasado:
  - pégalo en *Abrir o descargar* (`ctrl+o`): te ofrecerá *Entrar en esa sala*;
  - *Compartir → Entrar en una sala de otro…* (si lo tienes copiado, la primera fila ya es ese enlace);
  - desde un terminal o un acceso directo: `bin/mpv-uos "<enlace>"` abre el reproductor **ya dentro** de la sala.

  Mientras estés dentro, *Compartir* te dice en qué sala estás y por dónde va, y tiene *Salir de la sala*. Si te
  separas del anfitrión por unas décimas, el reproductor va un poco más rápido o más lento hasta ponerse a la par
  (no se nota); si la diferencia es grande, salta. Es la misma cuenta que hace la página, para que quien entra por
  el navegador y quien entra por el reproductor vean lo mismo en el mismo momento.
- El enlace caduca (24 h como mucho), se puede cambiar por uno nuevo y deja de valer al cerrar la sala.
- **Al crear la sala se copian LOS DOS enlaces de una vez**, con una línea diciendo para qué es cada uno: el del
  navegador (fácil, con chat y mandos) y el de VLC/mpv (calidad original, sin chat). Pégalo en un mensaje y quien
  lo reciba elige. No se copia hasta que la sala sirve de verdad, así que puede tardar unos segundos (o un minuto
  si va por internet).
- **Los invitados pueden pausar y saltar desde el primer momento.** En una sala privada a quien entra lo has
  invitado tú, así que entra pudiendo controlar: eso es lo que significa «ver juntos». Si prefieres llevar tú los
  mandos, apaga *Compartir → Los invitados pueden controlar*; a ellos se les dice por qué no pueden y tienen el
  botón de pedirlo. En una sala **pública** nunca se puede controlar.
- **Para VLC, mpv u otro reproductor, hace falta OTRO enlace**, y esto no es un capricho: el enlace de la sala
  lleva su contraseña en el fragmento (`#k=…`), y un navegador **nunca** manda el fragmento al servidor —que es
  justo lo que la mantiene fuera de los registros—, así que un reproductor que abra ese enlace no puede
  identificarse. El enlace bueno está en dos sitios: en la página del invitado, *Abrir en mi reproductor*, y en tu
  menú, **Compartir → Copiar el enlace para VLC o mpv**. Ese sí da calidad original.
- **Si saltas hacia atrás, la retransmisión se rehace donde vayas.** La retransmisión solo contiene *desde* donde
  arrancó, así que al ir a un minuto anterior no habría nada que enseñar: ahora se vuelve a preparar ahí sola. Se
  nota un par de segundos de «preparando» y sigue.
- **El código QR ya no sale solo.** Está en *Compartir → Mostrar el código QR* y en `alt+Q`, para cuando quien va a
  entrar está delante de ti con el móvil. Para todo lo demás, lo que sirve es el enlace, y ese ya lo tienes copiado.
- **Que se pueda entrar desde internet.** En *Compartir* hay un interruptor con ese nombre, **encendido** por defecto y que
  se recuerda. Encendido, al crear la sala Atalaya Player abre un *túnel rápido de Cloudflare* (sin cuenta ni configuración) y el
  enlace pasa a ser una dirección `https://…trycloudflare.com` que funciona desde cualquier sitio. El túnel vive
  exactamente lo que vive la sala: al cerrarla, la dirección deja de existir, y la próxima sala tendrá otra distinta. Con
  túnel no hace falta tocar el cortafuegos ni el router.
  La primera vez hay que instalar el programa del túnel (no viene de serie, porque es lo único que saca algo a internet):
  ```bash
  cd ~/Documentos/PROJECTES/MPV-UOS && MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh
  ```
  Si falta, la sala se abre igual en tu red y el menú te lo dice. Para entrar sigue haciendo falta el enlace con su
  token: la dirección sola no deja pasar.

**Sala pública (solo ver).** En *Compartir*, *Crear una sala pública (solo ver)*. Quien abra el enlace o escanee el QR
entra sin escribir nada y solo puede mirar: no pausa, no salta, no hay chat. Caben hasta 20 personas a la vez (se cambia
con `mu-share-max_viewers`). En el menú ves cuántos miran. Como las privadas: en tu red, o desde internet si enciendes
el túnel.

**Chat y reacciones.** En una sala privada, los invitados escriben y mandan reacciones desde la página; en tu pantalla
salen unos segundos abajo a la izquierda. Tú escribes desde *Compartir › Chat › Escribir un mensaje*, y ahí puedes
quitar el chat de la pantalla. Cada invitado puede mandar 5 mensajes y 8 reacciones cada 10 segundos.

**Emitir en directo.** En *Compartir › Emitir en directo…*:
1. *Servidor*: YouTube, YouTube (cifrado) o Twitch, u *Otro servidor* con su dirección `rtmp://…` (PeerTube, Owncast).
2. Copia tu clave de emisión (la da la web del servicio) y pulsa *Pegar la clave de emisión*. No se muestra nunca.
3. *Emitir lo que estoy viendo* (desde este punto) o *Emitir desde el principio*. El menú muestra el tiempo y la
   velocidad de emisión; *Parar la emisión* la corta.
Emite solo lo que tengas derecho a compartir (lo tuyo, contenido libre o con permiso). Si cierras el reproductor, la
emisión se para. La calidad es 720p, pensada para un portátil sin tarjeta gráfica dedicada.

## 9b-2. Enviar a la tele
`alt+E` (o *Herramientas → Enviar a la tele*) busca en tu wifi las teles y altavoces que aceptan DLNA (casi todas las
Samsung, LG, Sony, Philips, Kodi…; en algunas hay que activar «compartir contenido» o «DLNA» en sus ajustes). Elige una:
lo que estás viendo sigue en la tele desde el mismo minuto y aquí se pausa. Desde el menú: pausar, ±30 s, volumen de la
tele, *Seguir viendo aquí* (vuelve al reproductor donde iba la tele) y *Parar en la tele*.
- Archivos que la tele entiende (H.264/H.265 con AAC/MP3/AC-3) van tal cual y la tele puede saltar; los demás y los
  vídeos de YouTube se convierten al vuelo (MPEG-TS; saltar reinicia la conversión en ese punto).
- Como el mando y Compartir, necesita el puerto 8792 abierto en el cortafuegos (el menú muestra la orden exacta).
- Chromecast (Google TV) aún no: ver docs/PLATAFORMAS.md.

## 9c. Mini reproductor, modo salón y modo sencillo
- **Mini reproductor** (`alt+F`): ventana pequeña, sin bordes y siempre encima; la misma tecla la devuelve. En Wayland
  la coloca el escritorio; si KDE no la deja encima: `Alt+F3 → Más acciones → Mantener por encima`.
- **Modo salón** (*Preferencias*): pantalla completa y todo más grande para verlo desde el sofá. Con un mando de consola
  (Xbox o compatible, por USB o Bluetooth): A pausa, cruceta ←/→ 10 s, ↑/↓ volumen, X subtítulos, Start menú, B cierra
  el menú, LB/RB anterior/siguiente. Se recuerda al volver a abrir.
- **Modo sencillo** (*Preferencias*): menú principal corto y barra mínima, para quien solo quiere ver cosas;
  *Menú completo* lo quita.

## 9d. Resumen e índice
Las dos cosas viven juntas en **Resumen e índice**: en la raíz del menú (mientras hay algo abierto) y dentro del
*Panel de subtítulos*, que es de donde sale el material. También `alt+R` y `alt+I` directamente.

**¿Qué me he perdido?** Si minimizas la ventana o te vas a otra mientras sigue el vídeo, al volver aparece
«¿Te has perdido algo? alt+R». `alt+R` muestra 3–7 frases del propio diálogo que resumen ese tramo (si no te fuiste,
los últimos 5 minutos), cada una con su minuto: Enter salta ahí. Todo en tu equipo, en menos de un segundo.
Necesita palabras: subtítulos de texto del vídeo, o su transcripción con subtítulos IA (`alt+c`).
**En un vídeo de internet** no hay ninguna pista cargada, así que al pulsar se piden **los que ofrece la web**
(«Buscando los subtítulos del vídeo…»): unos 5 segundos, frente a los ~8 minutos que tardaría transcribirlo. Se piden
solo en los **idiomas propios** del vídeo —pedirle a YouTube una traducción automática devuelve un error de «demasiadas
peticiones»—, así que si el vídeo está en inglés el resumen sale en inglés y se te ofrece **traducirlo al español sin
conexión** (OPUS-MT, en el panel de subtítulos), que además queda mejor que la traducción de la web.
Con el modelo de búsqueda semántica instalado elige mejor; sin él cuenta las palabras que más se repiten. No inventa
nada: son frases que se dijeron.

### Índice del vídeo (`alt+I`)
Lo mismo, pero del vídeo entero: **secciones** (cortes donde cambia el tema, si está el modelo de búsqueda semántica;
si no, tramos de 5 min), cada una con su título y, dentro, sus frases clave. Cada línea salta a su minuto exacto. Es
instantáneo porque no lo escribe ningún modelo: los títulos y las frases son del propio diálogo, así que los minutos
son los de verdad. Hace falta un subtítulo de texto (los de YouTube valen, y son gratis); **nunca** se lanza una
transcripción para hacer un índice, porque eso serían horas en un portátil normal.

**Resumen en prosa** (al final del índice): lo escribe un modelo que corre **en tu equipo**, a partir del índice, en
español. Corto (3-4 frases, unos 40 s en un portátil de 4 núcleos) o largo (8-12 frases, alrededor de un minuto); el
tiempo se dice antes de empezar y hay progreso. Cada frase lleva su `[mm:ss]` y salta ahí al pulsarla. **Los minutos no
los pone el modelo**: salen del subtítulo, y si el modelo escribe uno que no existe, se quita antes de que lo veas.
El modelo se baja una vez (806 MB) desde el propio menú; mientras no esté, el índice funciona igual.

## 9d-2. Música
`alt+M` (o *Abrir → Música*) abre tu música. La primera vez añade tu carpeta Música y la lee en segundo plano (solo
cambia lo nuevo en las siguientes); en *Carpetas* puedes añadir otras. Navega por *Artistas › Álbumes › Pistas*,
*Álbumes*, *Géneros* o *Buscar* (sin importar acentos). En cualquier canción o álbum, `Tab` ofrece «Reproducir»,
«Reproducir a continuación» y «Añadir a la cola». *Cola* muestra lo que viene (mueve con `ctrl+↑/↓`, quita o guárdala
como lista). *Listas*: crea, ordena y exporta en M3U8, o usa las inteligentes (más escuchadas, añadidas hace poco…).
*Historial* se guarda solo en tu equipo y se borra desde el mismo menú. En *Ajustes*: «Sin cortes» entre pistas,
«Fundido» (baja y sube el volumen entre canciones; mpv no puede mezclar dos a la vez), «Volumen igualado» por pista o
por álbum (usa las etiquetas ReplayGain y, si faltan, las calcula sin tocar tus archivos) y «Salida exclusiva» (solo
PipeWire, Windows y macOS). El ecualizador (`alt+v`) tiene perfiles para auriculares de botón, cerrados y abiertos.

## 9e. Audiolibros, podcasts y letras
Abre un audiolibro (un `.m4b`, un archivo de más de una hora o la carpeta de sus capítulos) y el reproductor lo
reconoce: la próxima vez sigue donde lo dejaste, aunque sea en otro capítulo, y con la velocidad que le pusiste a ese
libro (los demás archivos siguen a velocidad normal). `alt+A` abre *Audiolibros y podcasts*: capítulos, marcadores con
una nota (Enter salta, Tab borra), velocidad, «Seguir escuchando» y el temporizador de apagado (15, 30, 45 o 60
minutos, o al terminar el capítulo): al final baja el volumen poco a poco y pausa. `alt+J` / `alt+L` saltan 30 s
atrás o adelante. Si algo se reconoce mal, el mismo menú tiene «Esto no es un audiolibro» o «Tratar como audiolibro».
Los podcasts recuerdan la posición de cada episodio y la velocidad de cada programa. No se guarda qué escuchaste ni
cuándo: solo dónde vas.

Letras: si junto a la canción hay un `.lrc` con el mismo nombre o la letra va dentro del archivo, aparece como
subtítulo «Letra» (`v` la oculta). `alt+K` muestra la letra entera; Enter salta a esa línea. Buscar letras en internet
(LRCLIB) e identificar canciones por su sonido (AcoustID) están apagados: se encienden en `alt+K` → *Ajustes de letras
y canciones*. Para identificar hace falta tu propia clave gratuita de acoustid.org/new-application (se guarda solo en
tu equipo); «Guardar en el archivo» escribe título, artista y álbum en la canción. La carátula (cover.jpg, folder.jpg…
junto a la canción) se muestra sola.

## 10. Asistentes (MCP)
Copia `.mcp.json.example` a `.mcp.json` para que Claude Code u otro cliente MCP controle el reproductor (reproducir, buscar en el
diálogo, canales, descargas, notas). Lo que interrumpe lo que ves pide confirmación en pantalla. Más en docs/MCP.md.

## 11. Preferencias: qué se recuerda y qué no
Lo que eliges se queda para la próxima vez que abras el reproductor. Se guarda en `prefs.json`, en la carpeta de datos
(`$MPV_UOS_DATA_DIR`; por defecto `~/.local/share/mpv-uos/`). Lo que pases en la línea de órdenes (`mpv-uos --volume=40 …`,
`--script-opts=…`) manda sobre lo recordado, solo en esa sesión. Con varias ventanas abiertas se guardan los cambios de todas; si dos
cambian lo mismo, gana el último.

| Se recuerda para todo lo que abras | Cómo se cambia |
|---|---|
| Volumen, silencio y velocidad | `9`/`0`, `m`, `[`/`]`, barra de uosc, mando del móvil |
| Subtítulos: tamaño, posición y visibilidad (también de los secundarios), estilo ASS, márgenes | teclas y menús de mpv/uosc |
| Imagen: contraste, brillo, gamma, saturación, tono | teclas de mpv |
| Pantalla completa, siempre encima, repetir la lista, decodificación por hardware (`auto-safe`/`no`), formato de yt-dlp | `f`, menús |
| Idiomas de audio y subtítulos: al elegir una pista con idioma, ese idioma pasa a ser el preferido | `a`, `s` |
| Subtítulos quitados: los siguientes archivos empiezan sin subtítulos hasta que actives una pista | `s` → ninguno |
| Filtros de *Sonido e imagen* (también volumen igualado y ecualizador) y perfil ligero | `alt+v`, `alt+n` |
| Velocidad inteligente (se reactiva en el siguiente archivo local) y su velocidad en silencios | `alt+g`, `alt+e` |
| Continuar viendo activado o no | *Menú → Preferencias* |

| No se recuerda, a propósito | Por qué |
|---|---|
| Pausa, posición, pista concreta, retardos de audio/subtítulos, zoom, encuadre, aspecto, rotación, desentrelazado | dependen de cada archivo: mpv los guarda por archivo al salir y *Continuar viendo* recuerda la posición |
| Repetir archivo (`L`) y aleatorio | te quedarías repitiendo o mezclando todo lo que abras después |
| Cambios automáticos: al cargar un archivo, opciones por archivo, la velocidad que pone la velocidad inteligente en los silencios, pistas creadas por la IA | no son elecciones tuyas |

**Restablecer**: *Menú (`alt+m`) → Preferencias → Restablecer preferencias…* o, en la paleta (`alt+p`), "Restablecer preferencias".
Pide confirmación, mueve `prefs.json` a `prefs.json.bak-<fecha>` (no se borra nada: para recuperarlo, renómbralo) y vuelve al momento a
los valores de `mpv.conf`. Desde la consola de mpv: `script-message-to mu_prefs reset`. Para no recordar nada: `MPV_UOS_PREFS=0` o
`--script-opts=mu-prefs-enabled=no`. Si `prefs.json` se estropea, se aparta como `prefs.json.corrupt-<fecha>` y se sigue con los valores
por defecto.

## 12. Biblioteca: películas, series y subtítulos de internet
`ctrl+b` (o *Abrir › Biblioteca*, o la pantalla de inicio) abre la **Biblioteca**.
- **Carpetas**: *Añadir la carpeta del archivo actual* o *Escribir o pegar una ruta…*. Atalaya Player las revisa en segundo plano, sin
  frenar la reproducción; la segunda vez solo mira lo nuevo. Tab sobre una carpeta: *Reescanear* o *Quitar* (no se borra nada del disco).
- **Películas** y **Series › Temporada › Episodio**: ✓ = visto, «45 %» = empezado. Se reconocen `Serie S01E02`, `Serie 1x06`,
  `Serie/Temporada 1/01 - Título.mkv`, `Película (1975)`, nombres con etiquetas (1080p, x264, WEB-DL…) y anime `[Grupo] Serie - 05`.
- **Seguir viendo** y **Siguiente episodio** salen arriba en la Biblioteca y en la pantalla de inicio.
- **Siguiente episodio automático**: al acabar un episodio, si no queda nada en la lista, aparece «Siguiente episodio en 5 s»
  (Esc cancela, Enter ya). Se apaga en *Biblioteca › Ajustes*.
- **Carátulas**: `poster.jpg`, `folder.jpg`, `cover.jpg` o `<nombre del vídeo>.jpg` junto al vídeo o en la carpeta de la serie; si no
  hay, un fotograma del vídeo. Opcional: *Carátulas y datos de internet (TMDB)* con tu propia clave (themoviedb.org → Ajustes → API).
- **Subtítulos de internet** (*Biblioteca › Buscar subtítulos en internet*, o en el menú de subtítulos): necesita tu Api-Key de
  opensubtitles.com (Ajustes; sin cuenta, 5 descargas al día; con cuenta gratuita, 20). Primero busca los hechos para ese mismo
  archivo («✓ exacto»); si no hay, por el nombre. Si no es exacto y hay Whisper, se ajusta solo a la voz (Ajustes › *Resincronizar
  con la voz*: si no es exacto / siempre / nunca). Las claves se guardan solo en tu equipo.

## Nombre y logo
El logo «C · Anillo» es el icono del menú de aplicaciones, de la PWA del mando y de los controles del escritorio.
El nombre de la app sigue siendo provisional: cambiarlo es editar `name` en `brand.json` y volver a ejecutar
`tools/install.sh` (detalles en docs/marca/README.md).

## Dónde se guardan las cosas
| Qué | Dónde (instalación por defecto) | Con `--xdg` |
|---|---|---|
| Caché (listas, transcripciones, índices, huellas) | `<proyecto>/.cache/` | `~/.cache/mpv-uos/` |
| Datos (favoritos, recientes, notas, móviles emparejados, marcas de intro) | `<proyecto>/.cache/data/` | `~/.local/share/mpv-uos/` |
| Descargas y clips | `~/Vídeos/Atalaya`, `~/Música/Atalaya` | igual |
| Grabaciones programadas | `~/Vídeos/Atalaya/Grabaciones` (lista en `<datos>/iptv-schedule.json`) | igual |
| Biblioteca (índice, ajustes, claves en archivo privado) y subtítulos bajados | `<datos>/library.sqlite3`, `library-settings.json`, `library-secrets.json` · caché `<caché>/library/` | igual (rutas XDG) |
| Conversiones | `~/Vídeos/Atalaya/Convertidos` (historial en los datos: `conversions.json`) | igual |
| Grabaciones de TV/radio | `~/Escritorio/Atalaya` | igual |
| Subtítulos guardados (SRT) | junto al vídeo; si no se puede, `~/Vídeos/Atalaya/Subtítulos` | igual |
| Modelos de traducción | Argos: `<proyecto>/vendor/models/argos` · OPUS-MT: `<proyecto>/.cache/data/models/opus-mt` | Argos igual · OPUS-MT: `~/.local/share/mpv-uos/models/opus-mt` |
| Configuración de mpv | `<proyecto>/mpv-config/` (nunca `~/.config/mpv`) | igual |

## Problemas frecuentes
- **No aparece nada de mpvd** (menús vacíos, "mpvd no está conectado"): `.venv/bin/python -m mpvd status`; log en `.cache/mpvd.log`.
- **Los subtítulos IA van lentos**: elige un modelo más pequeño en `alt+i` o activa el perfil ligero (`alt+v`); ver docs/BENCHMARKS.md.
- **Un canal no carga**: puede estar caído o geobloqueado; la entrada *Comprobar canales en segundo plano* de los menús de TV los marca en segundo plano.
- **YouTube falla**: `.venv/bin/python -m mpvd call ytdl.update.check` y luego `ytdl.update.apply` actualizan yt-dlp; necesita `node ≥ 22` o `deno`.
