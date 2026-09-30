# MPV-UOS

Reproductor multiplataforma sobre **mpv ≥ 0.41 + uosc ≥ 5.13** con un daemon companion en Python (**mpvd**) que hace lo pesado
(IA, red, índices, descargas) y habla con mpv por JSON IPC. Local-first; sin fork de mpv; configuración portable.

**Guía de uso: `docs/USO.md`** · Teclas: `docs/ATAJOS.md` · Visión: `docs/VISION.md` · Plan: `BACKLOG.md` · Estado: `PROGRESS.md` · Decisiones: `docs/DECISIONS.md` · Plataformas: `docs/PLATAFORMAS.md`

## Requisitos (Linux)
- mpv ≥ 0.41 con Lua (`mpv --version`), ffmpeg/ffprobe, [uv](https://docs.astral.sh/uv/).
- Para los tests: espeak-ng (voces de prueba), luacheck (opcional), shellcheck (opcional).

## Puesta en marcha
```bash
git clone <repo> MPV-UOS && cd MPV-UOS
uv sync              # crea .venv con Python 3.12 y el paquete mpvd
tools/vendor.sh      # descarga y verifica uosc 5.13.0 (+ ziggy), thumbfast y yt-dlp según vendor.lock
tools/check.sh       # lint + tests headless; debe acabar en "✅ check OK"
```

## Instalación de usuario (Linux, sin sudo)
```bash
tools/install.sh               # uv sync + vendor.sh + ~/.local/bin/mpv-uos + "MPV-UOS" en el menú de aplicaciones (icono y tipos MIME)
tools/install.sh --extras      # + traducción offline y búsqueda semántica (≈220 MB)
tools/install.sh --xdg         # caché y datos en ~/.cache/mpv-uos y ~/.local/share/mpv-uos en vez de <proyecto>/.cache
tools/install.sh --default     # además, reproductor por defecto para vídeo y audio (xdg-mime)
tools/install.sh --uninstall   # quita lanzador, .desktop e icono (solo si los creó el instalador); no toca datos ni el checkout
```
El lanzador instalado apunta a este checkout (no copia nada) y nunca toca `~/.config/mpv`. Subtítulos IA: `tools/vendor_whisper.sh`
(docs/WHISPER.md). Windows y macOS: docs/PLATAFORMAS.md.

## Abrir un archivo o URL
```bash
bin/mpv-uos /ruta/al/video.mkv
bin/mpv-uos https://www.youtube.com/watch?v=...
bin/mpv-uos --fs pelicula.mp4              # cualquier opción de mpv se pasa tal cual
```
`bin/mpv-uos` lanza el mpv del sistema con `--config-dir=<proyecto>/mpv-config` (no toca `~/.config/mpv`) y crea un socket IPC único
por instancia en `$XDG_RUNTIME_DIR/mpv-uos/mpv-<pid>.sock` (nunca se hereda de otra instancia). Tus datos (favoritos, notas, recientes, preferencias, posición de reanudación) viven en `~/.local/share/mpv-uos` (`MPV_UOS_DATA_DIR`). Botón derecho o tecla `MENU` abre el menú de uosc.

## El daemon mpvd
`mu-core.lua` arranca `mpvd` automáticamente al abrir mpv (se apaga solo a los 10 min sin sesiones). CLI:
```bash
.venv/bin/python -m mpvd status                  # sesiones, trabajos y guardián de rendimiento
.venv/bin/python -m mpvd call capabilities       # métodos JSON-RPC disponibles
.venv/bin/python -m mpvd call <método> '{...}'   # cualquier método
.venv/bin/python -m mpvd stop
```
Socket: `$XDG_RUNTIME_DIR/mpv-uos/mpvd.sock` (JSON-RPC 2.0, una línea por mensaje). Log: `.cache/mpvd.log`.

## Menú, paleta y continuar viendo
- Botón derecho, `MENU` o `alt+m`: menú **MPV-UOS** (buscar, abrir, continuar viendo, TV y radio, yt-dlp, lista, subtítulos, audio,
  capítulos, captura, menú completo, salir). Al arrancar sin archivo aparece la pantalla de inicio con los recientes.
- `alt+p`: **paleta** global. Escribe para filtrar comandos (todas las teclas con título), canales de TV y radio, vídeos recientes y
  acciones de mpvd (actualizar listas, buscar actualización de yt-dlp, estado). Sin acentos: "pelicula" encuentra "Película".
- **Continuar viendo** por contenido: la posición se guarda por hash del archivo (mpvd `watch.*`), así que un archivo renombrado o
  movido reanuda donde lo dejaste. `alt+h` lista los recientes (Tab: olvidar). Todas las teclas: `docs/ATAJOS.md`.

## TV y radio
Menú **TV y radio** en uosc (botón 📺 en la barra de controles, `alt+t`, o menú contextual): España TV y radio (TDTChannels),
Mundo por país y categoría (iptv-org), Radio mundial (Radio Browser), Favoritos, Recientes, Mis listas (M3U propias) y búsqueda
tipo paleta (`alt+f`, sin acentos). Sobre un canal, `Tab` abre las acciones: favorito y copiar URL.
- Zapping dentro del grupo actual: `alt+UP` / `alt+DOWN`. Grabar el directo (`stream-record`): `alt+r`; se guarda en
  `~/Escritorio/MPV-UOS` (opción `mu-iptv-record_dir` en `script-opts/mu-iptv.conf`).
- Las listas se descargan por mpvd con caché (ETag, 12 h, modo offline con la última copia) y las cabeceras `#EXTVLCOPT`/`#KODIPROP`
  se traducen a opciones de mpv por archivo. Añade tus propias M3U desde "Mis listas" (pega la URL con `ctrl+v`) o por CLI:
  ```bash
  .venv/bin/python -m mpvd call iptv.sources.add '{"name":"Mi lista","url":"https://.../lista.m3u"}'
  .venv/bin/python -m mpvd call iptv.search '{"q":"la 1"}'
  ```
- Copiar URL necesita `wl-copy` (paquete wl-clipboard) o `xclip`; si no hay, la URL se muestra en pantalla.

## yt-dlp: calidad, solo audio y descargas
Al abrir una URL (YouTube, archive.org, cualquier extractor de yt-dlp) mpv usa el **yt-dlp vendorizado** (`vendor/bin/yt-dlp`,
actualizado a diario por mpvd verificando las sumas oficiales) y, si hay `node ≥ 22` o `deno`, el runtime JS que YouTube exige.
Menú **yt-dlp** (botón ⬇ en la barra, `alt+y`):
- `alt+a` **Solo audio / vídeo**: recarga la misma URL con `bestaudio/best` (o el formato de vídeo) en la posición actual.
- `alt+q` **Calidad**: todos los formatos de `yt-dlp -J` agrupados (vídeo+audio, solo vídeo, solo audio) con contenedor, códecs,
  resolución, fps, HDR, bitrate y tamaño; Enter cambia en caliente, `Tab` descarga ese formato exacto.
- `alt+d` **Descargar**: presets (mejor calidad, 1080/720/480/360p, audio original, MP3 96–320 kbps o VBR, Opus, M4A, FLAC, WAV)
  y opciones (contenedor mp4/mkv/webm, subtítulos, capítulos, miniatura, metadatos, SponsorBlock marcar/quitar, playlist entera).
- `alt+l` **Descargas**: progreso (%, velocidad, ETA), cancelar, repetir, quitar; aviso en pantalla al terminar.
  Carpetas por defecto `~/Vídeos/MPV-UOS` y `~/Música/MPV-UOS` (XDG); plantilla `%(title).120B [%(id)s].%(ext)s`.
```bash
.venv/bin/python -m mpvd call ytdl.status                                   # binario, versión, runtime JS, actualización
.venv/bin/python -m mpvd call ytdl.info '{"url":"https://www.youtube.com/watch?v=aqz-KE-bpKQ"}'
.venv/bin/python -m mpvd call ytdl.download '{"url":"https://archive.org/details/Countdow1960","preset":"audio_mp3_128"}'
.venv/bin/python -m mpvd call ytdl.downloads.list
.venv/bin/python -m mpvd call ytdl.settings.set '{"video_dir":"~/Descargas/video","auto_update":false}'
```

## Subtítulos IA en vivo (whisper.cpp)
Para cualquier **archivo local**, mpvd transcribe el audio por delante de la posición de reproducción con `whisper-cli`
(vendorizado en `vendor/whisper` por `tools/vendor_whisper.sh`, ver docs/WHISPER.md) y va escribiendo un SRT que mpv añade como
pista externa y recarga solo. Menú **Subtítulos IA** (botón CC en la barra, `alt+i`); `alt+c` inicia/detiene.
- Idioma automático o fijado; modelo automático según el hardware (docs/BENCHMARKS.md) o elegido y **descargado bajo demanda**
  desde el menú (tiny → large-v3, cuantizados incluidos; VAD Silero).
- Un seek mueve el cursor de transcripción: primero se transcribe lo que vas a ver. Lo transcrito queda en caché por contenido
  (hash) y se reanuda al instante aunque cierres mpv o renombres el archivo.
- "Pre-subtitular el siguiente de la lista": el próximo elemento de la playlist se transcribe en baja prioridad mientras ves el actual.
- **Resincronizar** (`alt+x`): alinea un .srt/.ass/.vtt externo con la transcripción IA (retraso constante, deriva por fps y cortes
  de publicidad: recta robusta por tramos) y lo carga como pista nueva.
- **Traducir** (menú → "Traducir la pista seleccionada a…", también pistas internas): offline sobre CTranslate2 con dos motores,
  *Rápido (Argos)* para cualquier par (pivota por inglés) y *Calidad (OPUS-MT tc-big, 234 MB, se descarga una vez)* para
  español/catalán↔inglés. Los tiempos no cambian. La calidad depende sobre todo de la transcripción.
- **Guardar subtítulos (SRT)** (`alt+S`): pista IA, traducción, resincronizado o pista seleccionada → `<vídeo>.<idioma>.srt` junto al
  vídeo (o `~/Vídeos/MPV-UOS/Subtítulos`). Los subtítulos de imagen (PGS/VobSub) necesitarían OCR y no se pueden guardar.
- **Duales**: original arriba (`secondary-sid`) y traducción abajo, activables desde el mismo menú.
```bash
LD_LIBRARY_PATH=vendor/whisper/bin vendor/whisper/bin/whisper-cli --version    # ¿está whisper?
.venv/bin/python -m mpvd call asr.models                                        # modelos presentes / recomendados
.venv/bin/python -m mpvd call asr.start '{"path":"/ruta/video.mkv","language":"es"}'
.venv/bin/python -m mpvd call asr.status
.venv/bin/python -m mpvd call subs.resync '{"path":"/ruta/video.mkv","srt":"/ruta/video.srt","language":"es"}'
.venv/bin/python -m mpvd call subs.translate '{"srt":"/ruta/video.srt","source":"es","target":"en"}'
.venv/bin/python -m mpvd call subs.translate.models '{"index":true}'          # paquetes Argos presentes / descargables
tools/bench_asr.sh                                                              # RTF por modelo → docs/BENCHMARKS.md
```
La traducción necesita el extra opcional `uv sync --extra translate` (ctranslate2 + sentencepiece, ≈190 MB; docs/TRADUCCION.md).

## Sonido e imagen
Menú **Sonido e imagen** (`alt+v`, botón de ajustes en la barra): diálogo claro, modo noche (`alt+n`), reducción de ruido (RNNoise
o afftdn), binaural para auriculares (HRTF SOFA o crossfeed), protección fotosensible, perfil ligero y diagnóstico de tirones con
recomendaciones. Cada filtro es un grafo libavfilter validado contra el mpv instalado (docs/AUDIO_VIDEO.md); los modelos se descargan
desde el propio menú.
```bash
.venv/bin/python -m mpvd call av.models
.venv/bin/python -m mpvd call av.models.download '{"name":"rnnoise-sh"}'
```

## Saltar intro y créditos (local, sin servicios)
Al abrir un episodio, mpvd compara su audio (huellas Chromaprint, `fpcalc`) con el de los otros episodios de la temporada (misma carpeta o carpetas hermanas):
el tramo común al principio es la intro y el común al final son los créditos; los bordes se ajustan a silencios y fotogramas
negros (`silencedetect`/`blackdetect`). Al entrar en un tramo aparece el botón ⏭ en la barra y un aviso; `alt+k` salta
(créditos → siguiente episodio de la lista), `alt+j` abre el menú (segmentos, salto automático, volver a analizar). Los segmentos
no se escriben junto a los vídeos salvo que lo pidas (menú → Exportar segmentos (Jellyfin) → `segments.json`). Si no hay
otros episodios (una película suelta), se puede marcar a mano desde el menú. Requiere `fpcalc`
(paquete `libchromaprint-tools` en Debian/Ubuntu); sin él la función se desactiva sola.
```bash
bin/mpv-uos ~/Series/MiSerie/S01E02.mkv                                        # alt+k salta · alt+j menú
.venv/bin/python -m mpvd call intro.analyze '{"path":"/ruta/S01E02.mkv","wait":true}'
.venv/bin/python -m mpvd call intro.segments '{"path":"/ruta/S01E02.mkv"}'
.venv/bin/python -m mpvd call intro.export '{"path":"/ruta/S01E02.mkv"}'
```

## Búsqueda semántica en el diálogo y capítulos por tema
Con una transcripción de subtítulos IA (`alt+c`), mpvd indexa las frases con embeddings multilingües (ONNX int8, sin torch;
docs/SEMANTICA.md) y la paleta (`alt+p`) gana la sección **Diálogo**: escribe una idea en cualquier idioma ("cuando hablan del
presupuesto") y Enter salta a ese momento. En el menú de subtítulos IA, **Capítulos por tema (IA)** detecta cambios de tema
(ventanas de 45 s, mínimo 3 min por capítulo) y los aplica a `chapter-list` con una cita como título; se quitan desde el mismo menú.
Requiere `uv sync --extra semantic` (onnxruntime ≈24 MB) y el modelo (118 MB) en `vendor/models/embed`, que se descarga desde mpvd.
```bash
uv sync --extra translate --extra semantic
.venv/bin/python -m mpvd call semantic.status
.venv/bin/python -m mpvd call semantic.models.download
.venv/bin/python -m mpvd call asr.precompute '{"path":"/ruta/charla.mkv"}'      # transcripción previa
.venv/bin/python -m mpvd call semantic.index '{"path":"/ruta/charla.mkv","wait":true}'
.venv/bin/python -m mpvd call semantic.search '{"q":"when they talk about the budget","path":"/ruta/charla.mkv","k":5}'
.venv/bin/python -m mpvd call semantic.chapters '{"path":"/ruta/charla.mkv"}'  # incluye ffmetadata para ffmpeg
```

## Estudio: repetir línea, velocidad inteligente, notas y clips
Menú **Estudio** (`alt+e`, entrada en el menú raíz; docs/ESTUDIO.md): `alt+w` repite en bucle la línea de subtítulo en pantalla
(`alt+←`/`alt+→` pasan a la anterior/siguiente; `l` o `alt+w` lo quitan), `alt+g` activa la velocidad inteligente (×2,5 en los
silencios que mpvd mapea con `silencedetect`, velocidad normal cuando hay voz), `alt+b` guarda una nota con enlace de tiempo y la
cita del subtítulo en `<datos>/notas/<clave>.md` (Markdown, `mpv://seek?t=`), y `alt+u` exporta el bucle A-B (o la línea actual)
como clip: mp4 exacto, mp4/mkv sin recodificar, GIF, mp3, opus o wav a `<Vídeos|Música>/MPV-UOS/clips`, con progreso y aviso.
```bash
.venv/bin/python -m mpvd call study.formats
.venv/bin/python -m mpvd call study.clip '{"path":"/ruta/video.mkv","start":65,"end":72.5,"format":"gif"}'
.venv/bin/python -m mpvd call study.clips.list
.venv/bin/python -m mpvd call study.silences '{"path":"/ruta/charla.mkv","start":0,"length":600}'
.venv/bin/python -m mpvd call notes.list
```

## MCP: controla el reproductor desde Claude Code u otro asistente
`python -m mpvd mcp` es un servidor MCP por stdio (docs/MCP.md): tools `status`, `play`, `pause`, `resume`, `seek`, `search_dialogue`
(busca en la transcripción IA), `list_channels`, `play_channel`, `download`, `add_note`, `subtitles_ai`; resources con la transcripción
y las notas. Las acciones que interrumpen lo que ves piden confirmación en la pantalla de mpv. Configuración: copia `.mcp.json.example`
a `.mcp.json` y ajusta las rutas.

## Mando a distancia desde el móvil
`alt+z` muestra un código QR: escanéalo con el móvil (misma wifi) y la PWA servida por mpvd hace de mando (play/pausa, saltos,
volumen, velocidad, pistas, capítulos, lista, canales de TV/radio, búsqueda en el diálogo, recientes). El código vale una vez y caduca
a los 10 min; el móvil queda emparejado con una cookie firmada hasta "Olvidar" (`alt+Z`). Solo LAN, sin nube (docs/REMOTE.md).
Con `ufw` activo hay que abrir el puerto una vez: `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp`.
```bash
.venv/bin/python -m mpvd call remote.status
.venv/bin/python -m mpvd call remote.pair        # URL de emparejamiento sin pasar por mpv
```

## Estructura
- `bin/mpv-uos` lanzador · `tools/install.sh` instalación de usuario · `mpv-config/` configuración portable (mpv.conf, input.conf, scripts `mu-*`, uosc, thumbfast)
- `mpvd/` daemon Python (JSON-RPC 2.0; `mpvd/mcp.py` servidor MCP) · `tests/` pytest (unit + integración con mpv headless) · `tools/` scripts de build/check
- `vendor.lock` versiones fijadas de terceros · `vendor/` descargas y binarios (yt-dlp, deno opcional, whisper.cpp + modelos;
  ignorado por git)

## Tests a mano
```bash
uv run pytest                     # sin red
uv run pytest -m network          # solo los que necesitan internet
tools/make_test_media.sh          # regenera tests/fixtures/media (ffmpeg + espeak-ng)
```

## Créditos de modelos
- Modelos OPUS-MT (Helsinki-NLP; Tiedemann & Thottingal 2020, Tatoeba Translation Challenge), CC-BY 4.0:
  https://github.com/Helsinki-NLP/Tatoeba-Challenge
