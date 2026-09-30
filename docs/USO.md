# Guía de uso de MPV-UOS

Guía por tareas. Todas las teclas están en `docs/ATAJOS.md`; los detalles técnicos de cada función, en su documento (enlazado en cada
apartado). Sin mpvd (el daemon) lo básico sigue funcionando: reproducir, menús de uosc, teclas de mpv.

## 0. Instalar y abrir
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
tools/install.sh                 # uv sync + vendor.sh + ~/.local/bin/mpv-uos + entrada "MPV-UOS" en el menú de aplicaciones
tools/install.sh --extras        # además traducción offline y búsqueda semántica (≈220 MB)
mpv-uos video.mkv                # o bin/mpv-uos sin instalar
mpv-uos                          # sin archivo: pantalla de inicio con los recientes
```
Opciones del instalador: `--xdg` (caché y datos en `~/.cache/mpv-uos` y `~/.local/share/mpv-uos` en vez de `<proyecto>/.cache`),
`--default` (reproductor por defecto para vídeo y audio), `--dry-run`, `--uninstall` (solo borra lo que creó).

## 1. Moverse por la interfaz
- **Menú MPV-UOS**: botón derecho, tecla `MENU`, `alt+m` o el botón ▦ de la barra. Reúne todo lo de esta guía en ocho
  categorías: Abrir, TV y radio, Descargas y conversión, Subtítulos, Imagen y sonido, Grabar, Herramientas y Preferencias
  (arriba, *Continuar viendo* si hay algo a medias).
- **Un solo menú**: el título dice dónde estás («MPV-UOS › Subtítulos › Subtítulos IA»); la primera fila, **Atrás**, vuelve un
  nivel, igual que `⌫` o `←`; `Esc` cierra. Desde la raíz de cualquier módulo (aunque lo abras con su tecla) *Atrás* te lleva al
  menú principal. Las paletas de búsqueda (`ctrl+u`, `ctrl+f`, `alt+f`) se cierran con `⌫` cuando están vacías.
- **Ayuda**: `?` muestra las teclas principales.
- **Barra de controles**: reproducir/pausa (y anterior/siguiente si hay lista), subtítulos, audio (si hay varias pistas), velocidad,
  grabar ●, menú ▦ y pantalla completa. Lo demás está en el menú. El botón ⏭ aparece solo dentro de una intro o unos créditos.
- **Grabar** (botón ● de la barra o `alt+r`): capturas con o sin subtítulos, *Grabar desde ahora* (vídeo o solo audio)
  hasta *Detener*, y *Recortar un tramo* con marcas de inicio y final (son las del bucle A-B, tecla `l`). Mientras graba
  verás un punto rojo y un contador. Qué hace según lo que suena: un directo de TV o radio se graba tal cual llega; un
  vídeo de internet se descarga solo ese tramo con yt-dlp (en H.264/AAC); un archivo local se corta sin recodificar (en
  mp4 empieza justo en la marca; en mkv, en el fotograma clave anterior). Solo audio guarda la pista original sin
  recodificar. Carpeta: *Carpeta de grabaciones* (por defecto `~/Vídeos/MPV-UOS/Grabaciones`).
- **Pausar con un clic en el vídeo**: *Menú → Preferencias* (desactivado por defecto; activado, el clic ya no arrastra la ventana).
- **Paleta** (`alt+p`): escribe para buscar comandos, canales, recientes, acciones de mpvd y (si hay transcripción) frases del diálogo.
- **Continuar viendo** (`alt+h`): los recientes se reconocen por contenido, aunque renombres o muevas el archivo.

## 2. Ver la tele y escuchar la radio
1. `alt+t` → *España TV* / *España radio* (TDTChannels), *Mundo* (iptv-org por país y categoría), *Radio mundial* (Radio Browser).
   En *Mundo* y *Radio mundial* tu país sale primero; países y categorías van en español.
2. `alt+f` busca un canal por nombre sin acentos; `Tab` sobre un canal: favorito o copiar URL.
3. `alt+↑` / `alt+↓` cambian de canal dentro del grupo; `alt+r` graba el directo en `~/Escritorio/MPV-UOS`.
4. Tus listas: *TV y radio → Mis listas → Añadir* (pega la URL de una M3U). Más en docs/FUENTES_IPTV.md.
5. *Comprobar canales en segundo plano* (al final de cada lista) marca los caídos con ✕ y apunta la calidad real de cada
   canal. Lo que ves a la derecha de un canal:

   | Pista | Significa |
   |---|---|
   | `720p50 · 2,7 Mb` | mejor calidad que da la fuente (resolución, imágenes por segundo, megabits por segundo) |
   | `bitrate bajo` | es HD pero con menos de 1,6 Mb/s: se verá borroso aunque diga 720p/1080p |
   | `con anuncios` | copia FAST del canal, con cortes publicitarios insertados |
   | `+2 fuentes` | la lista trae el canal varias veces: se abre la oficial y, si falla, se prueba sola la siguiente ("Probando otra fuente de «La 1»…") |
   | `geobloqueado` | la fuente avisa de que solo funciona desde su país |
   | `ahora: Telediario` | lo que está emitiendo el canal según la guía |
6. **Guía y grabaciones programadas.** En las listas de TDTChannels cada canal dice qué emite (`ahora: …`). `Tab` sobre un
   canal → *Guía de programación* (o `alt+G` para el canal que ves): ahora, después y el resto del día; entra en un
   programa para *Grabar este programa* (empieza 1 min antes y acaba 3 después) o *Grabar lo que queda*.
   *TV y radio → Grabaciones programadas* muestra las pendientes, la que está grabando y las hechas (cancelar, detener,
   reproducir, quitar de la lista). *Programar grabación…* (o `Tab` → *Programar grabación…* en cualquier canal): elige
   el canal y escribe cuándo: `21:30 22:15`, `21:30 90` (minutos), `mañana 9:00 1h30`, `ahora 30`.
   Se graba aunque estés viendo otra cosa o cierres el reproductor, pero el equipo tiene que estar encendido: si estaba
   apagado a esa hora, la grabación sale como *perdida*. Los archivos van a `~/Vídeos/MPV-UOS/Grabaciones`
   («Canal - Programa - 2026-09-30 21.30.mkv») y avisa al terminar.

7. **Audio y subtítulos del canal.** Los canales que traen pistas propias lo dicen en la lista: `CC` (subtítulos),
   `VO` (versión original), `AD` (audiodescripción). Mientras ves uno, *TV y radio → Audio y subtítulos del canal*
   las muestra con nombres claros (*Español*, *Versión original*, *Audiodescripción*, *Español (para sordos)*). RTVE
   trae subtítulos en español, inglés, gallego, catalán y euskera. No hay traducción en directo.
8. **Buscar en esta lista.** La primera fila de *España TV*, *España radio*, cada país de *Mundo*, *Radio mundial*,
   *Favoritos* y *Recientes* busca solo entre sus canales (sin acentos, como `alt+f`).

**Lo que depende de la fuente y lo que no.** La resolución, las imágenes por segundo y el bitrate los pone cada cadena:
RTVE emite como mucho 720p a 25 fps y 3 Mb/s, casi todo va a 25 fps y algunos canales "HD" llevan muy poco bitrate; eso
no se puede mejorar desde el reproductor. Tampoco los canales caídos o geobloqueados. Lo que sí hace MPV-UOS: no guarda
posición ni pistas de los directos (siempre entran en su mejor calidad y desde "ahora"), abre cada petición HLS con
una conexión nueva (evita que canales como 101TV se congelen a los pocos segundos), se presenta como un navegador si la
lista no dice otra cosa (Canal Sur oficial rechaza a mpv) y cambia solo a otra fuente del mismo canal si la primera no
abre. Si un canal entrelazado se ve con "peines" (7TV), pulsa `d`. Detalles en docs/FUENTES_IPTV.md §7.

## 3. YouTube y otras webs (yt-dlp)
- `mpv-uos 'https://www.youtube.com/watch?v=…'` reproduce con el yt-dlp vendorizado (se actualiza solo a diario).
- **Abrir URL** (`ctrl+u`, o `alt+y` → *Abrir URL…*): si el portapapeles tiene un enlace, la primera entrada es *Pegar: …*;
  también puedes escribir o pegar (`ctrl+v`) una URL (YouTube, Twitch, archive.org, una radio, `rtsp://`…; vale `youtu.be/…` sin
  `https://`) y pulsar Enter. Si lo escrito no es una URL, la entrada pasa a ser *Buscar «texto» en YouTube*.
  `Tab` sobre una URL: *Añadir a la lista* (se reproduce al terminar lo actual) o *Descargar*.
- **Buscar en YouTube** (`ctrl+f`, o `alt+y` → *Buscar en YouTube…*): escribe y pulsa Enter; salen hasta 15 resultados con
  duración y canal (*EN DIRECTO* en los directos). Enter reproduce el elegido; `Tab`: *Añadir a la lista* o *Descargar* (abre el
  menú de descarga de ese vídeo sin tocar lo que suena). La misma búsqueda durante una hora sale al instante (caché de mpvd).
- `alt+a` alterna vídeo / solo audio sin perder la posición; `alt+q` elige cualquier formato; `alt+d` descarga (vídeo por resolución,
  audio original o convertido a MP3/Opus/M4A/FLAC/WAV con el bitrate que quieras, subtítulos, capítulos, SponsorBlock…);
  `alt+l` muestra la cola con progreso. Destino: `~/Vídeos/MPV-UOS` y `~/Música/MPV-UOS`. Más en docs/YTDLP.md.
- **Según tu equipo**: MPV-UOS mira qué códecs decodifica tu gráfica (`vainfo`) y en *Calidad* y en los formatos de
  *Descargar* marca cada vídeo como **fluido en tu equipo** (lo decodifica la gráfica) o **exigente (por procesador)**.
  Al reproducir y al descargar se prefiere el mejor códec que tu gráfica decodifica (en este portátil, H.264; con una
  gráfica moderna, AV1 o VP9), con el vídeo y el audio originales, sin recodificar. *Estado de yt-dlp* dice cuáles son.
- Por defecto: *Vídeo · hasta 1080p (recomendado)* = vídeo original hasta 1080p con sus fps + audio **Opus** original
  en **MP4** (si algo no cabe en MP4, se guarda en MKV solo); **mkv** como opción y **webm** VP9 + Opus. Solo audio:
  *Audio · original (recomendado)* = el Opus de la web tal cual; MP3 320 kb/s y M4A como opciones. Para un MP4 que
  abra cualquier aparato viejo (H.264 + AAC) o un archivo más pequeño (HEVC), usa *Convertir*.
- Tamaños orientativos por hora: 1080p60 H.264 ≈ 2,5–3 GB; 1080p H.264 ≈ 1,5–2 GB; 1080p AV1/VP9 ≈ 1–1,5 GB;
  720p ≈ 0,7–1 GB; 480p ≈ 0,3–0,5 GB; audio Opus 130–160 kb/s ≈ 60–70 MB; MP3 320 kb/s ≈ 145 MB.
- **Varias a la vez** (*Descargas y conversión → Descargar varias URL…*): pega uno o muchos enlaces (`ctrl+v`; da igual el
  separador) o escribe la ruta de un `.txt` con un enlace por línea; los repetidos se descartan. **Listas y canales**
  (*Descargar de una lista o canal…*): pega su URL, *Ver la lista y elegir* muestra todos los vídeos marcados; desmarca los
  que no quieras y descarga: van a una carpeta con el nombre de la lista, numerados (`001 - …`). Lo ya descargado de una
  lista, un canal o un lote no se repite (archivo `ytdl-archive.txt` en tus datos).
- **Subtítulos al descargar** (*Descargar → Opciones*): *Subtítulos* rota entre no, dentro del vídeo y archivo SRT
  aparte (junto al vídeo); *Idiomas* entre originales + es + en (por defecto), solo el original, español, inglés y todos.
  *Solo subtítulos (SRT)* baja únicamente los `.srt` (sin vídeo). «Original» es el idioma del vídeo según la web (y, en
  YouTube, sus subtítulos automáticos del idioma original).
- **Cuando la web cambia**: si la versión estable de yt-dlp no puede abrir o descargar algo (p. ej. ok.ru en septiembre de
  2026), MPV-UOS lo intenta una vez con la versión *nightly* (se descarga y verifica sola; *Estado de yt-dlp* muestra cuál
  hay). No se reintenta lo que no es cosa de versión: vídeos privados, con inicio de sesión, bloqueados en tu país,
  borrados…
- **TikTok e Instagram**: un vídeo o reel se abre y se descarga con su enlace; un perfil de TikTok se descarga con
  *Descargar de una lista o canal…* (casillas). Los perfiles de Instagram no funcionan hoy en yt-dlp (su extractor está
  roto). La suplantación de navegador que TikTok prefiere necesita `curl_cffi` (`tools/install.sh --extras`).
- **Ajustes de descarga**: descargas a la vez (1–4), límite de velocidad (sin límite, 500 KB/s … 10 MB/s), no repetir lo ya
  descargado, carpeta por lista y *Usar mi sesión del navegador* (desactivado; Firefox, Chrome, Chromium, Brave, Edge…:
  yt-dlp lee sus cookies para lo que ya puedes ver con tu cuenta; vale también al reproducir; nunca sirve para DRM).
  Si cierras el reproductor con descargas a medias, siguen al volver a abrirlo.

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
- Destino por defecto: `~/Vídeos/MPV-UOS/Convertidos` (se cambia en *Carpeta de salida*).

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
   - **Calidad (OPUS-MT, 234 MB, se descarga una vez)**: español/catalán ↔ inglés con OPUS-MT *tc-big* (Helsinki-NLP, CC-BY 4.0).
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
   - Si la carpeta del vídeo no admite escritura o es una URL, va a `~/Vídeos/MPV-UOS/Subtítulos` (tu carpeta de vídeos XDG).
   - Pista IA sin terminar: elige *Guardar lo transcrito* (lo que haya) o *Completar y guardar* (sigue en segundo plano, aunque
     cierres el reproductor, y guarda al acabar).
   - Al volver a abrir el vídeo, mpv carga el SRT guardado y los subtítulos IA lo reutilizan en vez de añadir otra pista igual.
6. **Límites honestos**: la traducción solo puede ser tan buena como el texto de origen; con subtítulos IA, lo que más cuenta es la
   transcripción (usa `small-q8_0` o mejor: pre-subtitula o *Completar y guardar* antes de traducir). Los subtítulos de imagen de
   DVD/Blu-ray (PGS, VobSub, DVB) no se pueden guardar, traducir ni resincronizar: necesitan OCR, que MPV-UOS no incluye. Los tiempos
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
  (`type`/`start`/`end` en segundos y `Type`/`StartTicks`/`EndTicks` al estilo media-segments de Jellyfin). MPV-UOS nunca escribe en tus
  carpetas si no se lo pides.

Límites: una **película suelta** (sin otros episodios) no tiene intro detectable: solo sirve el marcado manual. Varias **versiones del
mismo vídeo** en una carpeta (montajes, cortes para redes) se reconocen y no se marca nada («parecen versiones del mismo vídeo»). Se
descartan coincidencias sin sentido: una intro que empiece después del 40 % del vídeo o unos créditos que acaben antes del 60 %. La intro
se busca en los primeros 10 minutos y los créditos en los últimos 5. Hace falta pista de audio; solo archivos locales.

## 7. Sonido e imagen
`alt+v`: diálogo claro, modo noche (`alt+n`), reducción de ruido, sonido binaural para auriculares, protección fotosensible, perfil ligero
para equipos modestos y diagnóstico de tirones con recomendaciones. Más en docs/AUDIO_VIDEO.md.
- **Solo audio** (`alt+a`): en un vídeo de internet recarga solo el audio (y se recuerda); en un archivo de tu equipo es
  instantáneo y solo para ese archivo: el vídeo deja de decodificarse. *Solo audio al minimizar la ventana* (en `alt+v`,
  desactivado) hace lo mismo mientras la ventana está minimizada y devuelve la imagen al volver. Medido en este portátil
  con un 1080p60 H.264 (10 s): 6,7 s de CPU decodificando por procesador, 5,8 s con VA-API en modo copia y 0,5 s en solo
  audio (unas 13 veces menos).
- **Volumen igualado**: todos los vídeos suenan parecido de fuertes, sin tocar el mando (normalizador lento; tarda unos
  segundos en ajustarse al empezar).
- **Ecualizador**: *Plano*, *Más graves*, *Menos graves*, *Más agudos*, *Voz*, *Música*, *Altavoces del portátil* y
  *Auriculares*. Se recuerda como el resto de filtros.
- **Controles del escritorio (MPRIS)**: MPV-UOS aparece en los controles multimedia de KDE (bandeja, pantalla de
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
vídeo en ese minuto desde Obsidian o el navegador una vez instalado (`tools/install.sh`); pegados en MPV-UOS (`ctrl+v`)
también funcionan.

## 9. Mando a distancia desde el móvil
`alt+z` muestra un QR; escanéalo con el móvil en la misma wifi y tendrás play/pausa, saltos, volumen, pistas, canales y búsqueda.
El móvil queda emparejado hasta que lo olvides (`alt+Z`). Si no conecta, abre el puerto en el cortafuegos: docs/REMOTE.md.

## 9b. Compartir: ver juntos
`alt+W` (o *Herramientas → Compartir: ver juntos*) → *Crear una sala para ver juntos*: sale un enlace y un QR. Quien lo
abra en tu misma red (wifi de casa) pone su nombre y ve lo mismo que tú, a la vez, en su navegador (móvil u ordenador).
- Los invitados entran en *solo ver*. Si uno pide el control, te sale un sí/no en pantalla; en *Invitados* puedes
  darlo, quitarlo o sacar a alguien. Verás avisos como «Ana ha pausado».
- Vídeos de internet: el navegador del invitado los abre directamente si puede; archivos de tu equipo: MPV-UOS los
  retransmite (con los subtítulos de texto activos).
- El enlace caduca (24 h como mucho), se puede cambiar por uno nuevo y deja de valer al cerrar la sala.
- Abrir la sala a internet (túnel) está pendiente de una decisión de Ser (ver NEEDS_HUMAN.md).

## 9c. Mini reproductor, modo salón y modo sencillo
- **Mini reproductor** (`alt+F`): ventana pequeña, sin bordes y siempre encima; la misma tecla la devuelve. En Wayland
  la coloca el escritorio; si KDE no la deja encima: `Alt+F3 → Más acciones → Mantener por encima`.
- **Modo salón** (*Preferencias*): pantalla completa y todo más grande para verlo desde el sofá. Con un mando de consola
  (Xbox o compatible, por USB o Bluetooth): A pausa, cruceta ←/→ 10 s, ↑/↓ volumen, X subtítulos, Start menú, B cierra
  el menú, LB/RB anterior/siguiente. Se recuerda al volver a abrir.
- **Modo sencillo** (*Preferencias*): menú principal corto y barra mínima, para quien solo quiere ver cosas;
  *Menú completo* lo quita.

## 9d. ¿Qué me he perdido?
Si minimizas la ventana o te vas a otra mientras sigue el vídeo, al volver aparece «¿Te has perdido algo? alt+R».
`alt+R` (o *Herramientas → ¿Qué me he perdido?*) muestra 3–7 frases del propio diálogo que resumen ese tramo (si no te
fuiste, los últimos 5 minutos), cada una con su minuto: Enter salta ahí. Todo en tu equipo, en menos de un segundo.
Necesita palabras: subtítulos de texto del vídeo (incluidos los de la web o de OpenSubtitles) o su transcripción con
subtítulos IA (`alt+c`). Con el modelo de búsqueda semántica instalado elige mejor; sin él cuenta las palabras que más
se repiten. No inventa nada: son frases que se dijeron.

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
- **Carpetas**: *Añadir la carpeta del archivo actual* o *Escribir o pegar una ruta…*. MPV-UOS las revisa en segundo plano, sin
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
| Descargas y clips | `~/Vídeos/MPV-UOS`, `~/Música/MPV-UOS` | igual |
| Grabaciones programadas | `~/Vídeos/MPV-UOS/Grabaciones` (lista en `<datos>/iptv-schedule.json`) | igual |
| Biblioteca (índice, ajustes, claves en archivo privado) y subtítulos bajados | `<datos>/library.sqlite3`, `library-settings.json`, `library-secrets.json` · caché `<caché>/library/` | igual (rutas XDG) |
| Conversiones | `~/Vídeos/MPV-UOS/Convertidos` (historial en los datos: `conversions.json`) | igual |
| Grabaciones de TV/radio | `~/Escritorio/MPV-UOS` | igual |
| Subtítulos guardados (SRT) | junto al vídeo; si no se puede, `~/Vídeos/MPV-UOS/Subtítulos` | igual |
| Modelos de traducción | Argos: `<proyecto>/vendor/models/argos` · OPUS-MT: `<proyecto>/.cache/data/models/opus-mt` | Argos igual · OPUS-MT: `~/.local/share/mpv-uos/models/opus-mt` |
| Configuración de mpv | `<proyecto>/mpv-config/` (nunca `~/.config/mpv`) | igual |

## Problemas frecuentes
- **No aparece nada de mpvd** (menús vacíos, "mpvd no está conectado"): `.venv/bin/python -m mpvd status`; log en `.cache/mpvd.log`.
- **Los subtítulos IA van lentos**: elige un modelo más pequeño en `alt+i` o activa el perfil ligero (`alt+v`); ver docs/BENCHMARKS.md.
- **Un canal no carga**: puede estar caído o geobloqueado; la entrada *Comprobar canales en segundo plano* de los menús de TV los marca en segundo plano.
- **YouTube falla**: `.venv/bin/python -m mpvd call ytdl.update.check` y luego `ytdl.update.apply` actualizan yt-dlp; necesita `node ≥ 22` o `deno`.
