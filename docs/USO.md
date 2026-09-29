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
- **Menú MPV-UOS**: botón derecho, tecla `MENU` o `alt+m`. Reúne todo lo de esta guía.
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
- Contenedor de las descargas de vídeo: **mp4** prioriza H.264 + AAC (se abre en cualquier sitio; en YouTube el máximo es 1080p),
  **webm** VP9 + Opus y **mkv** la mejor calidad sin restricciones (AV1, 4K…). Se cambia en *Descargar → Opciones → Contenedor*.

## 4. Subtítulos con IA, traducción, duales y guardar SRT
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
- **Qué verás**: el botón ⏭ está siempre en la barra con vídeos locales; si aún no hay nada que saltar, su tooltip dice por qué
  (analizando, sin otros episodios, error). Dentro de la intro o los créditos se ilumina y aparece un recuadro **Saltar intro ▸**
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

## 8. Estudiar con vídeos
`alt+e` menú Estudio: `alt+w` repite en bucle la frase actual (`alt+←/→` anterior/siguiente), `alt+g` acelera solo los silencios,
`alt+b` guarda una nota con la cita y un enlace de tiempo en Markdown, `alt+u` exporta el bucle A-B (tecla `l` de mpv) o la frase como
clip mp4/GIF/mp3. Más en docs/ESTUDIO.md.

## 9. Mando a distancia desde el móvil
`alt+z` muestra un QR; escanéalo con el móvil en la misma wifi y tendrás play/pausa, saltos, volumen, pistas, canales y búsqueda.
El móvil queda emparejado hasta que lo olvides (`alt+Z`). Si no conecta, abre el puerto en el cortafuegos: docs/REMOTE.md.

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
| Filtros de *Sonido e imagen* y perfil ligero | `alt+v`, `alt+n` |
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

## Dónde se guardan las cosas
| Qué | Dónde (instalación por defecto) | Con `--xdg` |
|---|---|---|
| Caché (listas, transcripciones, índices, huellas) | `<proyecto>/.cache/` | `~/.cache/mpv-uos/` |
| Datos (favoritos, recientes, notas, móviles emparejados, marcas de intro) | `<proyecto>/.cache/data/` | `~/.local/share/mpv-uos/` |
| Descargas y clips | `~/Vídeos/MPV-UOS`, `~/Música/MPV-UOS` | igual |
| Grabaciones de TV/radio | `~/Escritorio/MPV-UOS` | igual |
| Subtítulos guardados (SRT) | junto al vídeo; si no se puede, `~/Vídeos/MPV-UOS/Subtítulos` | igual |
| Modelos de traducción | Argos: `<proyecto>/vendor/models/argos` · OPUS-MT: `<proyecto>/.cache/data/models/opus-mt` | Argos igual · OPUS-MT: `~/.local/share/mpv-uos/models/opus-mt` |
| Configuración de mpv | `<proyecto>/mpv-config/` (nunca `~/.config/mpv`) | igual |

## Problemas frecuentes
- **No aparece nada de mpvd** (menús vacíos, "mpvd no está conectado"): `.venv/bin/python -m mpvd status`; log en `.cache/mpvd.log`.
- **Los subtítulos IA van lentos**: elige un modelo más pequeño en `alt+i` o activa el perfil ligero (`alt+v`); ver docs/BENCHMARKS.md.
- **Un canal no carga**: puede estar caído o geobloqueado; la entrada *Comprobar canales en segundo plano* de los menús de TV los marca en segundo plano.
- **YouTube falla**: `.venv/bin/python -m mpvd call ytdl.update.check` y luego `ytdl.update.apply` actualizan yt-dlp; necesita `node ≥ 22` o `deno`.
