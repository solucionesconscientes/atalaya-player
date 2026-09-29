# MPV-UOS

Reproductor multiplataforma sobre **mpv ≥ 0.41 + uosc ≥ 5.13** con un daemon companion en Python (**mpvd**) que hace lo pesado
(IA, red, índices, descargas) y habla con mpv por JSON IPC. Local-first; sin fork de mpv; configuración portable.

Visión: `docs/VISION.md` · Plan: `BACKLOG.md` · Estado: `PROGRESS.md` · Decisiones: `docs/DECISIONS.md` · Plataformas: `docs/PLATAFORMAS.md`

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

## Abrir un archivo o URL
```bash
bin/mpv-uos /ruta/al/video.mkv
bin/mpv-uos https://www.youtube.com/watch?v=...
bin/mpv-uos --fs pelicula.mp4              # cualquier opción de mpv se pasa tal cual
```
`bin/mpv-uos` lanza el mpv del sistema con `--config-dir=<proyecto>/mpv-config` (no toca `~/.config/mpv`) y crea un socket IPC único
por instancia en `$XDG_RUNTIME_DIR/mpv-uos/mpv-<pid>.sock` (variable `MPV_UOS_SOCKET`). Botón derecho o tecla `MENU` abre el menú de uosc.

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
- **Traducir** (menú → "Traducir la pista seleccionada a…"): traducción offline con paquetes Argos sobre CTranslate2 (es↔en fijados;
  el resto del índice se descarga bajo demanda y pivota por inglés). Los tiempos de los cues no cambian; resultado en caché.
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

## MCP: controla el reproductor desde Claude Code u otro asistente
`python -m mpvd mcp` es un servidor MCP por stdio (docs/MCP.md): tools `status`, `play`, `pause`, `resume`, `seek`, `search_dialogue`
(busca en la transcripción IA), `list_channels`, `play_channel`, `download`, `add_note`, `subtitles_ai`; resources con la transcripción
y las notas. Las acciones que interrumpen lo que ves piden confirmación en la pantalla de mpv. Configuración: copia `.mcp.json.example`
a `.mcp.json` y ajusta las rutas.

## Estructura
- `bin/mpv-uos` lanzador · `mpv-config/` configuración portable (mpv.conf, input.conf, scripts `mu-*`, uosc, thumbfast)
- `mpvd/` daemon Python (JSON-RPC 2.0; `mpvd/mcp.py` servidor MCP) · `tests/` pytest (unit + integración con mpv headless) · `tools/` scripts de build/check
- `vendor.lock` versiones fijadas de terceros · `vendor/` descargas y binarios (yt-dlp, deno opcional, whisper.cpp + modelos;
  ignorado por git)

## Tests a mano
```bash
uv run pytest                     # sin red
uv run pytest -m network          # solo los que necesitan internet
tools/make_test_media.sh          # regenera tests/fixtures/media (ffmpeg + espeak-ng)
```
